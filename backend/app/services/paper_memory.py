"""试卷记忆服务（RAG v3 · 记忆板块）

一场考试的 AI 理解摘要：按语篇研读 What/Why/How 框架，把"这份卷子考什么、
考点与课标要求的对应、易错预设、复习钩子"写成 markdown 记忆，教师确认后
作为分析依据注入分析包（见 analysis_packet）。

- 生成：读 confirmed 试卷结构 + 知识点标注 → LLM 生成草稿（draft）；
- 确认：教师可直接确认或编辑后确认（confirmed），旧 confirmed 自动 superseded；
- 分类白名单：知识点标注逐一对照知识库（考点 ID + 语法类目），未命中的记入
  knowledge_gaps（提示教师补充分类表），不阻断录入。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from ..agent.knowledge_compiler import load_index

logger = logging.getLogger(__name__)

_MAX_QUESTIONS_IN_PROMPT = 40
_MEMORY_CHAR_LIMIT = 1200
_SNIPPET_LIMIT = 800

_GENERATION_PROMPT = """你是初中英语教学的试卷分析助手。请依据以下试卷结构信息，\
按语篇研读 What/Why/How 框架生成这份试卷的"试卷记忆"，供后续 AI 考试分析与\
复习计划使用。

试卷信息：{exam_line}
试卷状态：{paper_status}
题目结构：
{question_lines}
{gap_note}
输出要求：
1. 只输出 Markdown 正文（≤600 字），依次包含四个小节：
   ## 卷面结构（题型与分值分布）
   ## 考点与课标要求（知识点 → 义务教育英语课标三级要求的对应）
   ## 易错预设（按考点与学生常见错误，预设本题卷最可能的失分点）
   ## 复习钩子（后续复习计划可直接引用的 1~3 条建议方向）
2. 只使用上面给出的题目信息，不得编造未提供的题目、数据或地市规则；
3. 未入分类表的知识点如实标注"待归类"，不要强行归类；
4. 语言精炼，句子可直接被后续分析引用。"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _run(awaitable):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable)
    raise RuntimeError("paper_memory_sync_called_inside_event_loop")


def _build_provider():
    """默认文本 provider（可注入替身用于测试）。"""
    from ..agent.config import get_agent_config
    from ..agent.factory import _create_text_provider

    return _create_text_provider(get_agent_config())


def classify_knowledge_points(names: list[str]) -> dict[str, list[str]]:
    """对照知识库（考点 ID + 语法类目 + 受控别名）把知识点分为已知/未知。"""
    index = load_index()
    known: list[str] = []
    unknown: list[str] = []
    for name in names:
        text = str(name).strip()
        if not text:
            continue
        (known if index.lookup(text) is not None else unknown).append(text)
    return {"known": known, "unknown": unknown}


def get_latest_memory(db, exam_id: int, *, status: str | None = None):
    """按版本号新→旧返回第一条（可按状态过滤）；无则 None。"""
    from ..models.agent_entities import ExamPaperMemory
    query = select(ExamPaperMemory).where(ExamPaperMemory.exam_id == exam_id)
    if status:
        query = query.where(ExamPaperMemory.status == status)
    return db.scalar(query.order_by(ExamPaperMemory.version.desc()).limit(1))


def memory_payload(memory) -> dict[str, Any]:
    return {
        "id": memory.id, "exam_id": memory.exam_id,
        "paper_version_id": memory.paper_version_id,
        "version": memory.version, "status": memory.status,
        "content_md": memory.content_md, "source": memory.source,
        "generation_model": memory.generation_model,
        "knowledge_gaps": memory.knowledge_gaps_json or [],
        "confirmed_at": memory.confirmed_at.isoformat() if memory.confirmed_at else None,
    }


_memory_payload = memory_payload  # 兼容旧调用名


def _paper_facts(db, exam, *, term_id=None):
    from ..models.agent_entities import ExamPaperVersion, ExamQuestion
    paper = db.scalar(select(ExamPaperVersion).where(
        ExamPaperVersion.exam_id == exam.id,
        ExamPaperVersion.status == "confirmed",
    ).order_by(ExamPaperVersion.version.desc()).limit(1))
    status = "confirmed"
    if paper is None:
        paper = db.scalar(select(ExamPaperVersion).where(
            ExamPaperVersion.exam_id == exam.id
        ).order_by(ExamPaperVersion.version.desc()).limit(1))
        status = "draft" if paper else None
    questions = []
    if paper is not None:
        questions = list(db.scalars(select(ExamQuestion).where(
            ExamQuestion.paper_version_id == paper.id
        ).order_by(ExamQuestion.question_no).limit(_MAX_QUESTIONS_IN_PROMPT)))
    return paper, status, questions


def generate_paper_memory(db, exam_id: int, *, term_id=None,
                          provider=None) -> dict[str, Any]:
    """生成试卷记忆草稿。provider 可注入替身（测试）；缺 Key 时抛 RuntimeError。"""
    from ..services.exams import get_exam

    exam = get_exam(db, exam_id, term_id=term_id)
    paper, paper_status, questions = _paper_facts(db, exam)
    if paper is None or not questions:
        return {"ok": False, "error": "paper_structure_missing"}

    node_names: list[str] = []
    for question in questions:
        for node in question.knowledge_nodes_json or []:
            if node and node not in node_names:
                node_names.append(node)
    gaps = classify_knowledge_points(node_names)["unknown"]

    question_lines = "\n".join(
        f"- 第{q.question_no}题 {q.question_type or '未知题型'} "
        f"[{q.max_score:g}分] 知识点：{'、'.join(q.knowledge_nodes_json or []) or '未标注'}"
        + (f"｜内容摘录：{q.content_text[:60]}" if q.content_text else "")
        for q in questions)
    gap_note = ("注意：以下知识点尚未入分类表，请在记忆中标注为「待归类」："
                + "、".join(gaps)) if gaps else "所有知识点均已在分类表中。"
    exam_line = (f"{exam.name}，满分 {exam.full_score:g}"
                 + (f"，考试日期 {exam.exam_date}" if exam.exam_date else ""))
    prompt = _GENERATION_PROMPT.format(
        exam_line=exam_line, paper_status=paper_status,
        question_lines=question_lines, gap_note=gap_note)

    model_name = None
    if provider is None:
        provider = _build_provider()
        model_name = getattr(provider, "default_model", None)
    response = _run(provider.complete(
        [{"role": "user", "content": prompt}],
        temperature=0.2, max_tokens=1200))
    content = (response.content or "").strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[1] if "\n" in content else content
        content = content.rsplit("```", 1)[0].strip()
    if not content:
        return {"ok": False, "error": "empty_memory"}

    from ..models.agent_entities import ExamPaperMemory
    # 新草稿尚未获得教师确认，不能提前撤销当前已确认版本。这里只淘汰旧草稿；
    # 已确认版本会在新草稿确认时由 confirm_paper_memory 原子替换。
    previous = db.scalars(select(ExamPaperMemory).where(
        ExamPaperMemory.exam_id == exam_id,
        ExamPaperMemory.status == "draft")).all()
    for old in previous:
        old.status = "superseded"
    version = (db.scalar(select(ExamPaperMemory.version).where(
        ExamPaperMemory.exam_id == exam_id).order_by(
        ExamPaperMemory.version.desc()).limit(1)) or 0) + 1
    memory = ExamPaperMemory(
        exam_id=exam_id, paper_version_id=paper.id, version=version,
        status="draft", content_md=content[:_MEMORY_CHAR_LIMIT * 4],
        source="ai", generation_model=model_name or getattr(
            provider, "_default_model", None),
        knowledge_gaps_json=gaps)
    db.add(memory)
    db.commit()
    return {"ok": True, **_memory_payload(memory)}


def confirm_paper_memory(db, exam_id: int, memory_id: int, *,
                         content: str | None = None,
                         confirmed_by: str = "teacher") -> dict[str, Any]:
    """确认记忆（可带编辑内容）；同卷其他 confirmed 置为 superseded。"""
    from ..models.agent_entities import ExamPaperMemory
    memory = db.get(ExamPaperMemory, memory_id)
    if memory is None or memory.exam_id != exam_id:
        return {"ok": False, "error": "memory_not_found"}
    if memory.status == "superseded":
        return {"ok": False, "error": "memory_superseded"}
    if content is not None and content.strip():
        memory.content_md = content.strip()
        memory.source = "manual"
    others = db.scalars(select(ExamPaperMemory).where(
        ExamPaperMemory.exam_id == exam_id,
        ExamPaperMemory.status == "confirmed",
        ExamPaperMemory.id != memory.id)).all()
    for old in others:
        old.status = "superseded"
    memory.status = "confirmed"
    memory.confirmed_at = _now()
    db.commit()
    logger.info("试卷记忆已确认 exam=%s v%s by=%s", exam_id,
                memory.version, confirmed_by)
    return {"ok": True, **_memory_payload(memory)}


def get_confirmed_content(db, exam_id: int) -> tuple[str, int] | None:
    """返回 (已确认记忆内容, 版本)；无则 None。分析包注入用。"""
    memory = get_latest_memory(db, exam_id, status="confirmed")
    if memory is None:
        return None
    return memory.content_md, memory.version
