"""运行事件存储 (U2-01 / U2-02)

管理运行事件的内存缓冲和持久化。
职责：
1. 缓冲活跃运行的事件
2. 提供增量查询接口（供 SSE / 轮询使用）
3. 持久化事件到 analysis_run_events 表 (U2-02)
4. 断线重连后从数据库恢复事件 (U2-02)
5. 终态后清理内存缓冲

线程安全：所有操作通过 asyncio.Lock 保护。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

# 需要从 payload 中脱敏的字段
_SENSITIVE_KEYS = frozenset({
    "api_key", "apiKey", "key", "token", "password",
    "prompt", "full_prompt", "system_prompt",
    "raw_output", "tool_output",
})


def _sanitize_payload(data: dict[str, Any]) -> dict[str, Any]:
    """脱敏事件 payload，移除 API Key、完整 Prompt 等。"""
    sanitized: dict[str, Any] = {}
    for key, value in data.items():
        if key.lower() in _SENSITIVE_KEYS or key in _SENSITIVE_KEYS:
            sanitized[key] = "[REDACTED]"
        elif isinstance(value, dict):
            sanitized[key] = _sanitize_payload(value)
        elif isinstance(value, list):
            sanitized[key] = [
                _sanitize_payload(v) if isinstance(v, dict) else v
                for v in value
            ]
        else:
            sanitized[key] = value
    return sanitized


@dataclass
class StoredEvent:
    """存储的运行事件。"""
    seq: int
    event_type: str
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    data: dict[str, Any] = field(default_factory=dict)

    def to_sse(self) -> str:
        """格式化为 SSE 行。"""
        import json
        payload = json.dumps(
            {
                "seq": self.seq,
                "event": self.event_type,
                "timestamp": self.timestamp,
                "data": self.data,
            },
            ensure_ascii=False,
        )
        return f"data: {payload}\n\n"

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "event": self.event_type,
            "timestamp": self.timestamp,
            "data": self.data,
        }


class EventStore:
    """
    运行事件存储。

    管理每个 run_id 的事件列表，支持增量查询。
    终态运行的事件保留一段时间后清理。
    """

    def __init__(self) -> None:
        self._events: dict[int, list[StoredEvent]] = {}
        self._seq_counters: dict[int, int] = {}
        self._lock = asyncio.Lock()

    async def append(
        self, run_id: int, event_type: str, **data: Any
    ) -> StoredEvent:
        """向运行追加一个事件。"""
        async with self._lock:
            if run_id not in self._events:
                self._events[run_id] = []
                self._seq_counters[run_id] = 0

            self._seq_counters[run_id] += 1
            seq = self._seq_counters[run_id]

            event = StoredEvent(
                seq=seq,
                event_type=event_type,
                data=data,
            )
            self._events[run_id].append(event)
            return event

    async def append_and_persist(
        self,
        run_id: int,
        event_type: str,
        *,
        db_session,
        source: str = "gateway",
        **data: Any,
    ) -> StoredEvent:
        """Atomically allocate a durable sequence and persist one event.

        When a process restarts, the first event continues after the maximum
        sequence already stored for the run instead of starting again at 1.
        """
        from sqlalchemy import func, select
        from backend.app.models.agent_entities import AnalysisRunEvent

        async with self._lock:
            if run_id not in self._events:
                max_seq = db_session.scalar(
                    select(func.max(AnalysisRunEvent.seq)).where(
                        AnalysisRunEvent.run_id == run_id
                    )
                ) or 0
                self._events[run_id] = []
                self._seq_counters[run_id] = int(max_seq)

            self._seq_counters[run_id] += 1
            event = StoredEvent(
                seq=self._seq_counters[run_id],
                event_type=event_type,
                data=data,
            )
            self._events[run_id].append(event)

            db_session.add(AnalysisRunEvent(
                run_id=run_id,
                seq=event.seq,
                event_type=event.event_type,
                payload_json=_sanitize_payload(event.data),
                source=source,
            ))
            db_session.flush()
            return event

    async def get_events(
        self, run_id: int, after_seq: int = 0
    ) -> list[StoredEvent]:
        """获取 run_id 在 after_seq 之后的事件（增量）。"""
        async with self._lock:
            events = self._events.get(run_id, [])
            return [e for e in events if e.seq > after_seq]

    async def get_all_events(self, run_id: int) -> list[StoredEvent]:
        """获取 run_id 的全部事件。"""
        async with self._lock:
            return list(self._events.get(run_id, []))

    async def get_latest_seq(self, run_id: int) -> int:
        """获取 run_id 的最新事件序号。"""
        async with self._lock:
            return self._seq_counters.get(run_id, 0)

    async def clear(self, run_id: int) -> None:
        """清理 run_id 的全部事件。"""
        async with self._lock:
            self._events.pop(run_id, None)
            self._seq_counters.pop(run_id, None)

    async def cleanup_terminal(self, max_entries: int = 100) -> None:
        """清理过多的事件缓冲。"""
        async with self._lock:
            if len(self._events) <= max_entries:
                return
            sorted_ids = sorted(self._events.keys())
            to_remove = sorted_ids[: len(sorted_ids) - max_entries]
            for rid in to_remove:
                self._events.pop(rid, None)
                self._seq_counters.pop(rid, None)

    # ------------------------------------------------------------------
    # U2-02: 持久化到数据库
    # ------------------------------------------------------------------

    async def persist_event(
        self,
        run_id: int,
        event: StoredEvent,
        source: str = "gateway",
        db_session=None,
    ) -> None:
        """
        将事件持久化到 analysis_run_events 表。

        :param db_session: SQLAlchemy Session。如果为 None 则不持久化。
        """
        if db_session is None:
            return

        from backend.app.models.agent_entities import AnalysisRunEvent

        sanitized = _sanitize_payload(event.data)

        row = AnalysisRunEvent(
            run_id=run_id,
            seq=event.seq,
            event_type=event.event_type,
            payload_json=sanitized,
            source=source,
        )
        db_session.add(row)
        db_session.flush()

    async def persist_batch(
        self,
        run_id: int,
        events: list[StoredEvent],
        source: str = "gateway",
        db_session=None,
    ) -> None:
        """批量持久化事件。"""
        if db_session is None or not events:
            return

        from backend.app.models.agent_entities import AnalysisRunEvent

        for event in events:
            sanitized = _sanitize_payload(event.data)
            row = AnalysisRunEvent(
                run_id=run_id,
                seq=event.seq,
                event_type=event.event_type,
                payload_json=sanitized,
                source=source,
            )
            db_session.add(row)
        db_session.flush()

    async def load_events_from_db(
        self,
        run_id: int,
        db_session,
        after_seq: int = 0,
    ) -> list[StoredEvent]:
        """
        从数据库加载事件（断线重连后恢复）。
        同时更新内存缓冲。
        """
        from sqlalchemy import select
        from backend.app.models.agent_entities import AnalysisRunEvent

        stmt = (
            select(AnalysisRunEvent)
            .where(
                AnalysisRunEvent.run_id == run_id,
                AnalysisRunEvent.seq > after_seq,
            )
            .order_by(AnalysisRunEvent.seq)
        )
        result = db_session.execute(stmt)
        rows = result.scalars().all()

        events: list[StoredEvent] = []
        async with self._lock:
            if run_id not in self._events:
                self._events[run_id] = []
                self._seq_counters[run_id] = 0

            for row in rows:
                event = StoredEvent(
                    seq=row.seq,
                    event_type=row.event_type,
                    timestamp=row.created_at.isoformat() if row.created_at else "",
                    data=row.payload_json or {},
                )
                events.append(event)
                # 更新内存缓冲（避免重复）
                existing_seqs = {item.seq for item in self._events[run_id]}
                if event.seq not in existing_seqs:
                    self._events[run_id].append(event)
                if row.seq > self._seq_counters.get(run_id, 0):
                    self._seq_counters[run_id] = row.seq

        return events

    async def get_max_persisted_seq(
        self, run_id: int, db_session
    ) -> int:
        """获取数据库中 run_id 的最大 seq。"""
        from sqlalchemy import select, func
        from backend.app.models.agent_entities import AnalysisRunEvent

        stmt = select(func.max(AnalysisRunEvent.seq)).where(
            AnalysisRunEvent.run_id == run_id
        )
        result = db_session.execute(stmt)
        return result.scalar() or 0


# 全局单例
_event_store: Optional[EventStore] = None


def get_event_store() -> EventStore:
    global _event_store
    if _event_store is None:
        _event_store = EventStore()
    return _event_store
