"""教学知识库编译器（RAG v3 · P0）

把 `knowledge/**/*.md` 的人工条目编译为运行时可查的确定性产物：

- entries   全部条目（ID/标题/领域/级别/标签/可信度/来源/正文）
- routing   题型 → 优先考点 → 优先错因（来自 exam-zhejiang-2025/question-types.md）
- actions   考点/错因/题型 → 短行动建议（来自 review-tasks.md 与 question-types.md）
- aliases   规范 ID 与受控别名（考点 ID、条目标签）
- manifest  源文件哈希（检测源条目变化）

设计要点（docs/KNOWLEDGE_BASE_RAG.md v3）：
- 编译是确定性的、纯内存即可完成（122 条目量级）；`write_compiled()` 可把产物
  落盘到 knowledge/compiled/ 供审计，运行期始终以内存最新编译为准（不存在
  过期产物问题）；
- 只做精确匹配与受控别名，不做开放模糊检索；
- 条目是唯一人工事实源，本模块不修改任何条目。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

KNOWLEDGE_DIR = Path(__file__).resolve().parent / "knowledge"

_ENTRY_HEADER_RE = re.compile(r"^##\s+\[([^\]]+)\]\s*(.+?)\s*$", re.M)
_META_RE = re.compile(r"^-\s+(领域|级别|标签|可信度|来源):\s*(.+?)\s*$", re.M)


@dataclass(frozen=True)
class KnowledgeEntry:
    entry_id: str
    title: str
    domain: str
    level: str
    tags: tuple[str, ...]
    credibility: str
    source: str
    body: str
    file: str

    def snippet(self, limit: int = 600) -> str:
        text = self.body.strip()
        return text if len(text) <= limit else text[:limit] + "…"


@dataclass
class KnowledgeIndex:
    entries: dict[str, KnowledgeEntry]
    routing: dict[str, dict[str, list[str]]]
    actions: dict[str, list[str]]
    aliases: dict[str, str]
    manifest: dict[str, Any]

    def lookup(self, query: str) -> KnowledgeEntry | None:
        """规范 ID 精确匹配 > 受控别名/标签精确匹配 > 明确失败。

        不做包含/模糊匹配（v3：受控别名，不做开放模糊检索）——
        分类白名单等场景依赖"未命中即未知"的严格语义。
        """
        query = (query or "").strip()
        if not query:
            return None
        if query in self.entries:
            return self.entries[query]
        alias_id = self.aliases.get(query)
        if alias_id and alias_id in self.entries:
            return self.entries[alias_id]
        lowered = query.lower()
        for entry in self.entries.values():
            for tag in entry.tags:
                if tag.lower() == lowered:
                    return entry
        return None


_POINT_ID_RE = re.compile(r"^[a-z][a-z_]*\.[a-z_]+$")
_VALID_ERROR_CAUSES = ("审题", "词汇", "语法", "定位", "推断", "表达")


def _split_list(value: str) -> list[str]:
    value = value.replace("`", "")
    parts = re.split(r"[、，,;；/\n]", value)
    items = []
    for part in parts:
        text = part.strip().rstrip("。.．")
        if text:
            items.append(text)
    return items


_KNOWN_LABELS = (
    "主要能力", "优先考点", "优先错因", "诊断要点", "训练方法", "适用考点",
    "掌握证据", "目标", "步骤", "固定流程", "提交前清单", "方法", "复盘表",
)


def _normalize_action(text: str) -> str:
    """行动文本压成单行（省 token，中文文本去换行安全）。"""
    return re.sub(r"\s*\n\s*", "", text).strip()


def _labeled_body(body: str, label: str) -> str:
    """取"标签：值"的内容，允许跨行，直到下一个已知标签或空行。"""
    pattern = re.compile(
        label + r"[:：]\s*([\s\S]*?)(?=\n\s*(?:"
        + "|".join(_KNOWN_LABELS)
        + r")[:：]|\n\s*\n|\Z)"
    )
    match = pattern.search(body)
    return match.group(1).strip() if match else ""


def parse_entries(knowledge_dir: Path | str = KNOWLEDGE_DIR) -> tuple[list[KnowledgeEntry], dict[str, str]]:
    """解析全部条目；返回 (条目列表, 文件哈希表)。"""
    root = Path(knowledge_dir)
    entries: list[KnowledgeEntry] = []
    hashes: dict[str, str] = {}
    for path in sorted(root.rglob("*.md")):
        rel = path.relative_to(root).as_posix()
        if rel == "README.md":
            continue
        raw = path.read_text(encoding="utf-8")
        hashes[rel] = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        matches = list(_ENTRY_HEADER_RE.finditer(raw))
        for idx, match in enumerate(matches):
            entry_id, title = match.group(1).strip(), match.group(2).strip()
            start = match.end()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(raw)
            block = raw[start:end]
            meta = {key: value.strip() for key, value in _META_RE.findall(block)}
            body_start = 0
            for line in block.splitlines():
                if line.startswith("- ") and _META_RE.match(line):
                    body_start += len(line) + 1
                else:
                    break
            body = block[body_start:].strip()
            entries.append(KnowledgeEntry(
                entry_id=entry_id,
                title=title,
                domain=meta.get("领域", ""),
                level=meta.get("级别", "通用"),
                tags=tuple(_split_list(meta.get("标签", ""))),
                credibility=meta.get("可信度", "C"),
                source=meta.get("来源", ""),
                body=body,
                file=rel,
            ))
    return entries, hashes




def compile_knowledge(knowledge_dir: Path | str = KNOWLEDGE_DIR) -> KnowledgeIndex:
    """编译知识库为运行期索引（确定性、可重复）。"""
    entries_list, hashes = parse_entries(knowledge_dir)
    entries: dict[str, KnowledgeEntry] = {}
    for entry in entries_list:
        if entry.entry_id in entries:
            logger.warning("知识条目 ID 重复（后者忽略）: %s", entry.entry_id)
            continue
        entries[entry.entry_id] = entry

    # routing：题型（question-types.md 的 [QT-*]）→ 优先考点/错因
    routing: dict[str, dict[str, list[str]]] = {}
    for entry in entries.values():
        if not entry.entry_id.startswith("[QT-") and not entry.entry_id.startswith("QT-"):
            continue
        points = [p for p in _split_list(_labeled_body(entry.body, "优先考点"))
                  if _POINT_ID_RE.match(p)]
        causes = [c for c in _split_list(_labeled_body(entry.body, "优先错因"))
                  if c in _VALID_ERROR_CAUSES]
        for name in entry.tags[:1] or [entry.title]:
            routing[name] = {"points": points, "error_causes": causes,
                             "entry_id": entry.entry_id}

    # actions：考点标签 / 题型 → 短行动（review-tasks 适用考点 + 题型训练方法）
    actions: dict[str, list[str]] = {}
    for entry in entries.values():
        if entry.entry_id.startswith("[RV-") or entry.entry_id.startswith("RV-"):
            action_text = _normalize_action(entry.title)
            applicable = [t for t in _split_list(_labeled_body(entry.body, "适用考点"))
                          if _POINT_ID_RE.match(t) or t in _VALID_ERROR_CAUSES]
            targets = applicable or entry.tags
            for tag in targets:
                actions.setdefault(tag, []).append(action_text)
        elif entry.entry_id.startswith("[QT-") or entry.entry_id.startswith("QT-"):
            action_text = _normalize_action(_labeled_body(entry.body, "训练方法"))
            if not action_text:
                continue
            for name in entry.tags:
                actions.setdefault(name, []).append(action_text)

    # aliases：考点 ID（[CP-*] 标签首项即规范 ID）与全部条目标签 → entry_id
    aliases: dict[str, str] = {}
    for entry in entries.values():
        for tag in entry.tags:
            aliases.setdefault(tag, entry.entry_id)

    manifest = {
        "schema_version": "1.0.0",
        "entry_count": len(entries),
        "file_hashes": hashes,
    }
    return KnowledgeIndex(entries=entries, routing=routing, actions=actions,
                          aliases=aliases, manifest=manifest)


@lru_cache(maxsize=1)
def load_index(knowledge_dir: str | None = None) -> KnowledgeIndex:
    """进程内缓存的知识索引（确定性编译，无过期产物问题）。"""
    index = compile_knowledge(knowledge_dir or KNOWLEDGE_DIR)
    logger.info("教学知识库已编译: %s 条目, %s 题型路由",
                len(index.entries), len(index.routing))
    return index


def write_compiled(knowledge_dir: Path | str = KNOWLEDGE_DIR,
                   output_dir: Path | str | None = None) -> Path:
    """把当前编译产物落盘到 compiled/（供审计与跨语言消费，非运行期依赖）。"""
    index = compile_knowledge(knowledge_dir)
    out = Path(output_dir) if output_dir else Path(knowledge_dir) / "compiled"
    out.mkdir(parents=True, exist_ok=True)
    payloads = {
        "manifest.json": index.manifest,
        "routing.json": index.routing,
        "actions.json": index.actions,
        "aliases.json": index.aliases,
        "source_map.json": {
            entry_id: {
                "title": entry.title, "domain": entry.domain, "level": entry.level,
                "credibility": entry.credibility, "source": entry.source,
                "file": entry.file, "tags": list(entry.tags),
            } for entry_id, entry in index.entries.items()
        },
    }
    for name, payload in payloads.items():
        (out / name).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> None:  # pragma: no cover
    import argparse
    parser = argparse.ArgumentParser(description="TeachMate 知识库编译器")
    parser.add_argument("--knowledge-dir", default=str(KNOWLEDGE_DIR))
    parser.add_argument("--write", action="store_true", help="落盘 compiled/ 产物")
    args = parser.parse_args(argv)
    index = compile_knowledge(args.knowledge_dir)
    print(json.dumps({
        "entries": len(index.entries),
        "routing": len(index.routing),
        "actions": len(index.actions),
        "aliases": len(index.aliases),
    }, ensure_ascii=False))
    if args.write:
        out = write_compiled(args.knowledge_dir)
        print(f"compiled -> {out}")


if __name__ == "__main__":  # pragma: no cover
    main()
