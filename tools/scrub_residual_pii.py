"""对演示库做一次穷尽式残留脱敏。

与生成脚本的区别：生成脚本只处理它「知道」的结构化字段；本工具用
原始备份库还原出完整的花名册（92 人），然后对**所有表、所有文本列**做
字面量与 JSON 转义量（\\uXXXX）双重替换，堵住「名字藏在 JSON 字符串里」
这类漏网。

用法：
    .venv/bin/python tools/scrub_residual_pii.py            # 执行
    .venv/bin/python tools/scrub_residual_pii.py --dry-run  # 只报告
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
from pathlib import Path

DATA_DIR = Path(os.path.expanduser("~/Library/Application Support/冯老师初中英语工作台"))
DB = DATA_DIR / "workbench.db"
# 用于还原原始花名册的备份库（生成前状态）
REF_DB = DATA_DIR / "backups" / "pre_migration_20260925_130125_772841" / "workbench.db"

# 与姓名无关、但同样需要抹掉的机构/称谓
EXTRA_TERMS = {
    "冯老师": "示范老师",
    "建兰中学": "示范中学",
    "建兰": "示范",
    "兰行": "研学",
    "兰苑": "校园",
}


def escaped(s: str) -> str:
    """返回 json 转义后的形态（不含首尾引号），如 陈双 -> \\u9648\\u53cc。"""
    return json.dumps(s, ensure_ascii=True)[1:-1]


def load_original_names() -> dict[str, str]:
    """从备份库读出 {原始姓名: student_no}。"""
    if not REF_DB.exists():
        raise SystemExit(f"找不到参照备份：{REF_DB}")
    con = sqlite3.connect(f"file:{REF_DB}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT name, student_no FROM students").fetchall()
    finally:
        con.close()
    return {name: no for name, no in rows if name}


def build_mapping() -> dict[str, str]:
    """原始姓名 -> 脱敏姓名。以 student_no 为桥。"""
    orig = load_original_names()
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT student_no, name FROM students").fetchall()
    finally:
        con.close()
    no_to_anon = {no: nm for no, nm in rows}
    mapping: dict[str, str] = {}
    for name, no in orig.items():
        anon = no_to_anon.get(no)
        if anon and anon != name:
            mapping[name] = anon
    return mapping


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    name_map = build_mapping()
    terms = dict(name_map)
    terms.update(EXTRA_TERMS)
    # 长的先替换，避免短名吃掉长名（如「陈双」与「陈双X」）
    ordered = sorted(terms.items(), key=lambda kv: -len(kv[0]))

    # 预编译：每个词的原形/转义形 -> 目标原形/转义形
    pairs: list[tuple[str, str, str, str]] = []
    for src, dst in ordered:
        pairs.append((src, dst, escaped(src), escaped(dst)))

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    tables = [r[0] for r in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]

    total_cells = 0
    report: dict[str, int] = {}
    for t in tables:
        cols = [c[1] for c in cur.execute(f"PRAGMA table_info('{t}')")]
        pk = [c[1] for c in cur.execute(f"PRAGMA table_info('{t}')") if c[5]]
        pk_col = pk[0] if pk else "rowid"
        rows = cur.execute(f"SELECT rowid AS _rid, * FROM '{t}'").fetchall()
        for row in rows:
            updates: dict[str, str] = {}
            for c in cols:
                v = row[c]
                if not isinstance(v, str) or not v:
                    continue
                nv = v
                for src, dst, esrc, edst in pairs:
                    if esrc and esrc in nv:      # 转义形态优先（避免与字面混淆）
                        nv = nv.replace(esrc, edst)
                    if src in nv:
                        nv = nv.replace(src, dst)
                if nv != v:
                    updates[c] = nv
            if updates:
                total_cells += len(updates)
                report[f"{t}.{','.join(updates)}"] = report.get(
                    f"{t}.{','.join(updates)}", 0) + 1
                if not args.dry_run:
                    sets = ", ".join(f'"{c}" = ?' for c in updates)
                    cur.execute(
                        f"UPDATE '{t}' SET {sets} WHERE rowid = ?",
                        [*updates.values(), row["_rid"]])

    print(f"命中单元格 {total_cells} 处：")
    for k, v in sorted(report.items(), key=lambda kv: -kv[1]):
        print(f"  {k}: {v}")
    if args.dry_run:
        print("--dry-run：未写入。")
        con.close()
        return 0

    con.commit()
    cur.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    con.close()
    print("已写入并 checkpoint。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
