"""
SessionMapper — 业务会话到 Harness 会话的映射 (U1-03)

映射规则：tm-{agent_session.id}-{随机短后缀}
- 不得使用学生姓名/班级名/学号
- harness_session_id 唯一可空
- context_revision 跟踪上下文版本
"""

from __future__ import annotations

import secrets
import logging
from typing import Optional

from sqlalchemy.orm import Session

from ..models.agent_entities import AgentSession

logger = logging.getLogger(__name__)

# Harness session ID 前缀
_HARNESS_ID_PREFIX = "tm-"
# 随机后缀长度（hex 字符）
_SUFFIX_LEN = 8


def generate_harness_session_id(agent_session_id: int) -> str:
    """
    生成 Harness 会话 ID。

    格式：tm-{agent_session_id}-{random_hex_suffix}
    保证不包含学生姓名/班级名/学号等敏感信息。
    """
    suffix = secrets.token_hex(_SUFFIX_LEN // 2 + 1)[:_SUFFIX_LEN]
    return f"{_HARNESS_ID_PREFIX}{agent_session_id}-{suffix}"


def assign_harness_session(
    db: Session,
    agent_session_id: int,
) -> str:
    """
    为业务会话分配 Harness 会话 ID。

    如果已有 harness_session_id 则返回现有的；
    否则生成新的并持久化。
    """
    session = db.query(AgentSession).filter_by(id=agent_session_id).first()
    if not session:
        raise ValueError(f"AgentSession {agent_session_id} not found")

    if session.harness_session_id:
        return session.harness_session_id

    harness_id = generate_harness_session_id(agent_session_id)
    session.harness_session_id = harness_id
    session.context_revision = 0
    db.flush()

    logger.info(
        "Assigned harness_session_id=%s to agent_session=%d",
        harness_id,
        agent_session_id,
    )
    return harness_id


def assign_run_harness_session(
    db: Session,
    agent_session_id: int,
) -> tuple[str, int]:
    """为一个新的分析运行分配独立 Harness 会话。

    业务会话的长期记忆由数据库摘要与最近消息负责。每个分析运行使用新的
    Harness 会话，可以防止 Harness 内部历史、工具结果与应用层历史重复累积。
    ``AgentSession.harness_session_id`` 仅保存最近一次运行的映射，便于诊断；
    精确的运行映射始终写在 ``AnalysisRun.harness_session_id``。
    """
    session = db.query(AgentSession).filter_by(id=agent_session_id).first()
    if not session:
        raise ValueError(f"AgentSession {agent_session_id} not found")

    harness_id = generate_harness_session_id(agent_session_id)
    session.harness_session_id = harness_id
    session.context_revision = (session.context_revision or 0) + 1
    db.flush()
    logger.info(
        "Assigned fresh harness_session_id=%s to agent_session=%d (revision=%d)",
        harness_id,
        agent_session_id,
        session.context_revision,
    )
    return harness_id, session.context_revision


def bump_context_revision(
    db: Session,
    agent_session_id: int,
) -> int:
    """
    递增上下文版本号，返回新版本号。
    当会话上下文发生重大变化（如新增附件、修改 scope）时调用。
    """
    session = db.query(AgentSession).filter_by(id=agent_session_id).first()
    if not session:
        raise ValueError(f"AgentSession {agent_session_id} not found")

    session.context_revision = (session.context_revision or 0) + 1
    db.flush()

    logger.debug(
        "Bumped context_revision to %d for agent_session=%d",
        session.context_revision,
        agent_session_id,
    )
    return session.context_revision


def rotate_harness_session(
    db: Session,
    agent_session_id: int,
) -> int:
    """作用域变化时轮换 Harness 会话（B3-02）。

    - 生成新的 harness_session_id（旧 ID 不再使用，保留在历史 run 上供审计）；
    - context_revision +1；
    - 返回新 context_revision。
    调用方负责在后续 run 中写入新的 session id。
    """
    from sqlalchemy import update as sa_update

    db.execute(
        sa_update(AgentSession)
        .where(AgentSession.id == agent_session_id)
        .values(harness_session_id=generate_harness_session_id(agent_session_id))
    )
    new_rev = bump_context_revision(db, agent_session_id)
    db.commit()

    logger.info(
        "Rotated harness session for agent_session=%d (context_revision=%d)",
        agent_session_id,
        new_rev,
    )
    return new_rev


def get_harness_session_id(
    db: Session,
    agent_session_id: int,
) -> Optional[str]:
    """获取业务会话的 Harness 会话 ID，可能为 None。"""
    session = db.query(AgentSession).filter_by(id=agent_session_id).first()
    if not session:
        return None
    return session.harness_session_id


def validate_harness_session_id(harness_id: str) -> bool:
    """
    验证 Harness 会话 ID 格式是否合法。
    格式：tm-{数字}-{hex后缀}
    """
    if not harness_id or not harness_id.startswith(_HARNESS_ID_PREFIX):
        return False

    rest = harness_id[len(_HARNESS_ID_PREFIX):]
    parts = rest.split("-", 1)
    if len(parts) != 2:
        return False

    agent_session_part, suffix = parts
    if not agent_session_part.isdigit():
        return False
    if not suffix or len(suffix) > 16:
        return False

    try:
        int(suffix, 16)  # 验证是 hex
    except ValueError:
        return False

    return True
