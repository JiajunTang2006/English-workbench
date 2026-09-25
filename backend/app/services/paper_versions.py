"""统一的试卷版本选择器。

同一场考试可能存在多个试卷版本（draft / confirmed / superseded）。审核界面与
正式诊断必须选择同一个版本，否则会出现「审核界面能看到草稿结构、正式诊断 404」
的分叉：教师以为某个分数已进入正式事实，模型却读不到。

规则（方案 §1.1）：
- 正式诊断默认只使用最新 confirmed 版本；没有 confirmed 版本时明确返回
  ``missing``，不得把草稿当作正式事实回退。
- 审核界面可显式请求 draft（``allow_draft=True``），调用方必须同时把版本状态
  展示给教师。
- 已确认版本永远优先于更新的草稿版本，保证正式结论不被未确认结构改写。
"""

from __future__ import annotations

from sqlalchemy import select

from ..models.agent_entities import ExamPaperVersion


PAPER_STATUS_MISSING = "missing"


def list_paper_versions(session, exam_id: int) -> list[ExamPaperVersion]:
    """按版本号倒序列出该考试的所有试卷版本。"""
    return list(session.scalars(
        select(ExamPaperVersion)
        .where(ExamPaperVersion.exam_id == exam_id)
        .order_by(ExamPaperVersion.version.desc())
    ))


def select_paper_version(
    session, exam_id: int, *, allow_draft: bool = False,
) -> tuple[ExamPaperVersion | None, str]:
    """返回 ``(version | None, status)``。

    status 取值：``confirmed`` / ``draft`` / ``superseded`` / ``missing``。
    ``allow_draft=False``（默认）时，没有 confirmed 版本就返回
    ``(None, "missing")``，由调用方给出「尚未确认试卷结构」的明确状态。
    """
    versions = list_paper_versions(session, exam_id)
    if not versions:
        return None, PAPER_STATUS_MISSING
    confirmed = next((item for item in versions if item.status == "confirmed"), None)
    if confirmed is not None:
        return confirmed, "confirmed"
    if allow_draft:
        latest = versions[0]
        return latest, latest.status
    return None, PAPER_STATUS_MISSING
