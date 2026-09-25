"""Education Bridge 作用域保管库（B3-03）

服务器在每个 Harness 回合开始前，把该回合的受控作用域
（run_id/term_id/class_id/exam_id/student_id）原子写入
``<scope_root>/<harness_session_id>.json``；Education Bridge 插件在工具调用时
读取同一文件并作为唯一 scope 来源。回合终态后删除，避免泄漏。

重要语义：
- scope 由服务器写入，模型不可见，也不能通过工具参数提交；
- 写文件使用 tmp+rename 原子替换，跨进程读取总是拿到完整内容；
- 目录权限收紧（0700），文件权限 0600；
- 只存整数 id 与 run_id，不存任何学生姓名/电话等隐私数据。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SCOPE_FILE_PERMS = 0o600
_SCOPE_DIR_PERMS = 0o700

# P1-2：每个 scope 根目录一把全局 asyncio 锁 —— 把「写 scope → Harness 回合
# → 清 scope」整体串行化。同一时刻只有一个回合持有 scope，插件单活动文件
# 语义始终成立；第二个任务在锁外等待（状态 queued），不会直接失败。
_SCOPE_LOCKS: dict[str, asyncio.Lock] = {}
_LOCKS_MUTEX = asyncio.Lock()


def get_scope_lock(data_dir: Path | str) -> asyncio.Lock:
    """返回 scope 根目录对应的串行锁（进程内单例）。"""
    key = str(Path(data_dir).resolve())
    existing = _SCOPE_LOCKS.get(key)
    if existing is not None:
        return existing
    # 并发创建保护（asyncio 单线程内此竞态极轻微，双锁兜底）
    lock = asyncio.Lock()
    _SCOPE_LOCKS[key] = lock
    return lock


def scope_dir(data_dir: Path | str) -> Path:
    """返回 scope 根目录（不存在时创建，权限 0700）。

    传入的 data_dir 即作用域根目录（服务器为
    <settings.data_dir>/harness-session-scopes）。
    """
    d = Path(data_dir)
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, _SCOPE_DIR_PERMS)
    except OSError:
        pass
    return d


def scope_file(data_dir: Path | str, harness_session_id: str) -> Path:
    return scope_dir(data_dir) / f"{harness_session_id}.json"


def write_scope(
    data_dir: Path | str,
    harness_session_id: str,
    *,
    run_id: int | None = None,
    term_id: int | None = None,
    class_id: int | None = None,
    exam_id: int | None = None,
    student_id: int | None = None,
    capability: str | None = None,
    tool_policy: list[str] | None = None,
) -> Path:
    """原子写入回合 scope。重复调用幂等（覆盖为最新值）。

    ``capability`` / ``tool_policy`` 由服务器按 run 预先计算（v3 分析包路径）：
    bridge 端对每个工具调用做策略门禁。正式分析回合始终写入最小白名单；
    普通对话可写入安全只读白名单，显式空列表才表示该 run 不允许任何工具。
    """
    payload = {
        "run_id": run_id,
        "term_id": term_id,
        "class_id": class_id,
        "exam_id": exam_id,
        "student_id": student_id,
        "capability": capability,
        "tool_policy": tool_policy,
    }
    path = scope_file(data_dir, harness_session_id)
    fd, tmp = tempfile.mkstemp(prefix=".scope-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        os.chmod(tmp, _SCOPE_FILE_PERMS)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def read_scope(data_dir: Path | str, harness_session_id: str) -> dict[str, Any] | None:
    """读取 scope；文件缺失或解析失败返回 None（fail-closed）。"""
    path = scope_file(data_dir, harness_session_id)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning("scope 文件缺失（harness_session=%s）", harness_session_id)
        return None
    except OSError:
        return None
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        logger.warning("scope 文件损坏: %s", path)
        return None
    if not isinstance(data, dict):
        return None
    return data


def clear_scope(data_dir: Path | str, harness_session_id: str) -> None:
    """删除回合 scope 文件（回合结束后调用，防止残留）。"""
    try:
        scope_file(data_dir, harness_session_id).unlink(missing_ok=True)
    except OSError:
        logger.debug("scope 清理失败（无妨）: session=%s", harness_session_id)


def reset_scopes(data_dir: Path | str, *, keep: str | None = None) -> None:
    """清除 scope 根目录中的所有残留 scope 文件（含进程崩溃遗留）。

    只在持有全局串行锁后调用；keep 为当前回合的 harness_session_id，
    其 scope 文件保留（可能刚写入或由本回合写入）。.tmp 半成品一并清理。
    fail-closed：目录不存在不做任何事（后续写入会重建）。
    """
    root = Path(data_dir)
    if not root.is_dir():
        return
    for f in root.iterdir():
        if not f.is_file():
            continue
        if f.name.endswith(".json"):
            if keep and f.name == f"{keep}.json":
                continue
            try:
                f.unlink()
                logger.info("清理残留 scope 文件: %s", f.name)
            except OSError:
                logger.debug("scope 残留清理失败（无害）: %s", f.name)
        elif f.name.startswith(".scope-"):
            try:
                f.unlink()
            except OSError:
                pass


_SCOPE_KEYS = ("run_id", "term_id", "class_id", "exam_id", "student_id")


def allowlisted_scope(scope: dict[str, Any]) -> dict[str, int | None]:
    """只保留白名单整数字段；其余字段一律丢弃（fail-closed）。"""
    out: dict[str, int | None] = {}
    for key in _SCOPE_KEYS:
        val = scope.get(key)
        if isinstance(val, bool) or not isinstance(val, int):
            out[key] = None
        elif val <= 0:
            out[key] = None
        else:
            out[key] = val
    return out
