"""知识编译器测试（RAG v3 · P0）"""

from __future__ import annotations

import json

import pytest

from backend.app.agent.knowledge_compiler import (
    compile_knowledge, load_index, write_compiled,
)

_VALID_CAUSES = {"审题", "词汇", "语法", "定位", "推断", "表达"}


def test_compile_real_knowledge_base():
    index = load_index()
    assert len(index.entries) >= 120, "知识条目应不少于 120 条"
    ids = list(index.entries)
    assert len(ids) == len(set(ids)), "条目 ID 必须全库唯一"
    for entry in index.entries.values():
        assert entry.domain, f"条目缺领域: {entry.entry_id}"
        assert entry.tags, f"条目缺标签: {entry.entry_id}"
        assert entry.source, f"条目缺来源: {entry.entry_id}"
        assert entry.credibility in {"A", "B", "C", "D"}


def test_routing_covers_question_types():
    index = load_index()
    assert {"阅读理解", "完形填空", "语法填空", "书面表达"} <= set(index.routing)
    cloze = index.routing["完形填空"]
    assert "vocab.collocation" in cloze["points"]
    assert set(cloze["error_causes"]) <= _VALID_CAUSES
    assert cloze["error_causes"], "题型路由必须给出优先错因"


def test_lookup_exact_id_alias_and_tag():
    index = load_index()
    assert index.lookup("reading.inference").entry_id.startswith("CP-")
    assert index.lookup("宾语从句") is not None
    assert index.lookup("不存在的考点xyz") is None
    assert index.lookup("") is None


def test_lookup_strict_no_substring_match():
    """回归（评审 P2）：包含匹配必须被拒绝，否则深查会登记错误教学依据。"""
    index = load_index()
    assert index.lookup("宾语从句专题") is None, "子串不得命中「从句关系」"
    assert index.lookup("不存在的阅读标签") is None, "子串不得命中「学业质量：读」"
    assert index.lookup("完形填空真题汇编") is None
    # 精确标签/ID 不受影响
    assert index.lookup("宾语从句") is not None
    assert index.lookup("阅读理解") is not None


def test_actions_indexed_by_point_and_question_type():
    index = load_index()
    assert index.actions.get("完形填空"), "题型必须有训练方法行动"
    tense_actions = index.actions.get("grammar.tense") or []
    assert any("句法还原" in action for action in tense_actions)


def test_write_compiled_artifacts(tmp_path):
    out = write_compiled(output_dir=tmp_path)
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    routing = json.loads((out / "routing.json").read_text(encoding="utf-8"))
    source_map = json.loads((out / "source_map.json").read_text(encoding="utf-8"))
    assert manifest["entry_count"] == len(load_index().entries)
    assert set(routing) == set(load_index().routing)
    index = load_index()
    point_entry = index.aliases["reading.inference"]
    assert point_entry in source_map
    assert source_map[point_entry]["credibility"] in {"A", "B", "C", "D"}
    # 两次编译确定性一致（哈希稳定）
    again = compile_knowledge()
    assert again.manifest["file_hashes"] == manifest["file_hashes"]


def test_compile_is_deterministic():
    a = compile_knowledge()
    b = compile_knowledge()
    assert a.manifest == b.manifest
    assert set(a.aliases) == set(b.aliases)
