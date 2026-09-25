"""LLM 使用量与成本记录服务。

每次 LLM 调用记录 provider、model、输入/输出 token、费用和 request ID。
不保存 API Key。
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from ...models.agent_entities import AnalysisRun, AgentSession, LlmUsageRecord

logger = logging.getLogger(__name__)


class UsageService:
    """LLM 使用量与成本记录服务。"""

    def __init__(self, db: Session) -> None:
        self._db = db

    def record_usage(
        self,
        *,
        run_id: int,
        provider: str,
        model_name: str,
        stage: str,
        input_tokens: int = 0,
        cache_read_tokens: int = 0,
        reasoning_tokens: int = 0,
        provider_prompt_tokens: int = 0,
        output_tokens: int = 0,
        cost_yuan: float = 0.0,
        provider_request_id: str | None = None,
    ) -> LlmUsageRecord:
        """记录一次 LLM 调用的使用量和成本。

        :param stage: text_analysis / vision_analysis / tool_call / report_generation
        注意：LlmUsageRecord 无 session_id 字段，通过 run_id 关联。
        """
        record = LlmUsageRecord(
            run_id=run_id,
            provider=provider,
            model_name=model_name,
            stage=stage,
            input_tokens=input_tokens,
            cache_read_tokens=cache_read_tokens,
            reasoning_tokens=reasoning_tokens,
            provider_prompt_tokens=provider_prompt_tokens or input_tokens + cache_read_tokens,
            output_tokens=output_tokens,
            cost_yuan=cost_yuan,
            provider_request_id=provider_request_id,
        )
        self._db.add(record)
        self._db.commit()
        self._db.refresh(record)
        return record

    def dashboard(self, range_key: str = "today") -> dict[str, Any]:
        """返回设置页使用的 Token 用量聚合数据，不包含提示词或回答正文。"""
        now = datetime.now(timezone.utc)
        if range_key == "today":
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            bucket_kind, bucket_count = "hour", 24
        elif range_key == "7d":
            start = now - timedelta(days=7)
            bucket_kind, bucket_count = "day", 7
        elif range_key == "30d":
            start = now - timedelta(days=30)
            bucket_kind, bucket_count = "day", 30
        elif range_key == "all":
            start = datetime.min.replace(tzinfo=timezone.utc)
            bucket_kind, bucket_count = "day", 0
        else:
            raise ValueError("range must be today, 7d, 30d or all")

        rows = list(self._db.scalars(
            select(LlmUsageRecord)
            .where(LlmUsageRecord.created_at >= start)
            .order_by(LlmUsageRecord.created_at)
        ))
        totals = self._aggregate(rows)
        grouped: dict[str, dict[str, Any]] = defaultdict(lambda: self._empty_bucket())
        for row in rows:
            created = row.created_at
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            local = created.astimezone()
            key = local.strftime("%Y-%m-%d-%H" if bucket_kind == "hour" else "%Y-%m-%d")
            bucket = grouped[key]
            bucket["label"] = local.strftime("%H:%M" if bucket_kind == "hour" else "%m/%d")
            bucket["timestamp"] = local.isoformat()
            self._add(bucket, row)

        buckets: list[dict[str, Any]] = []
        if bucket_kind == "hour":
            local_now = now.astimezone()
            base = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
            for i in range(bucket_count):
                point = base + timedelta(hours=i)
                key = point.strftime("%Y-%m-%d-%H")
                buckets.append(grouped.get(key) or {
                    **self._empty_bucket(), "label": point.strftime("%H:%M"), "timestamp": point.isoformat(),
                })
        else:
            if rows:
                first = rows[0].created_at
                if first.tzinfo is None:
                    first = first.replace(tzinfo=timezone.utc)
                first = first.astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
            else:
                first = now.astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
            days = bucket_count or max(1, (now.date() - first.date()).days + 1)
            first = max(first, now.astimezone() - timedelta(days=days - 1))
            first = first.replace(hour=0, minute=0, second=0, microsecond=0)
            for i in range(days):
                point = first + timedelta(days=i)
                key = point.strftime("%Y-%m-%d")
                buckets.append(grouped.get(key) or {
                    **self._empty_bucket(), "label": point.strftime("%m/%d"), "timestamp": point.isoformat(),
                })

        run_ids = sorted({row.run_id for row in rows})
        top_runs = []
        if run_ids:
            run_rows = list(self._db.execute(
                select(AnalysisRun, AgentSession.title)
                .outerjoin(AgentSession, AnalysisRun.session_id == AgentSession.id)
                .where(AnalysisRun.id.in_(run_ids))
            ))
            by_run: dict[int, list[LlmUsageRecord]] = defaultdict(list)
            for row in rows:
                by_run[row.run_id].append(row)
            for run, title in run_rows:
                run_usage = by_run.get(run.id, [])
                item = self._aggregate(run_usage)
                item.update({"run_id": run.id, "title": title or run.capability, "capability": run.capability,
                             "status": run.status, "created_at": run.created_at.isoformat() if run.created_at else None})
                item["risk"] = "critical" if item["total_tokens"] >= 64000 else ("warning" if item["total_tokens"] >= 32000 else "normal")
                top_runs.append(item)
            top_runs.sort(key=lambda item: item["total_tokens"], reverse=True)

        return {"range": range_key, "start_at": start.isoformat(), "end_at": now.isoformat(),
                "totals": totals, "buckets": buckets, "top_runs": top_runs[:8]}

    @staticmethod
    def _empty_bucket() -> dict[str, Any]:
        return {"label": "", "timestamp": "", "input_tokens": 0, "cache_read_tokens": 0,
                "output_tokens": 0, "reasoning_tokens": 0, "total_tokens": 0, "cost_yuan": 0.0, "calls": 0}

    @classmethod
    def _add(cls, target: dict[str, Any], row: LlmUsageRecord) -> None:
        target["input_tokens"] += int(row.input_tokens or 0)
        target["cache_read_tokens"] += int(row.cache_read_tokens or 0)
        target["output_tokens"] += int(row.output_tokens or 0)
        target["reasoning_tokens"] += int(row.reasoning_tokens or 0)
        target["total_tokens"] += int(row.input_tokens or 0) + int(row.cache_read_tokens or 0) + int(row.output_tokens or 0)
        target["cost_yuan"] += float(row.cost_yuan or 0)
        target["calls"] += 1

    @classmethod
    def _aggregate(cls, rows: list[LlmUsageRecord]) -> dict[str, Any]:
        result = cls._empty_bucket()
        for row in rows:
            cls._add(result, row)
        prompt = result["input_tokens"] + result["cache_read_tokens"]
        result["cache_hit_rate"] = round(result["cache_read_tokens"] / prompt * 100, 1) if prompt else 0.0
        return result

    def get_run_total_cost(self, run_id: int) -> float:
        """获取某次运行的总成本。"""
        result = self._db.scalar(
            select(func.sum(LlmUsageRecord.cost_yuan))
            .where(LlmUsageRecord.run_id == run_id)
        )
        return float(result or 0.0)

    def get_run_total_tokens(self, run_id: int) -> int:
        """获取某次运行的总 Token 数。"""
        result = self._db.scalar(
            select(
                func.sum(LlmUsageRecord.input_tokens + LlmUsageRecord.cache_read_tokens + LlmUsageRecord.output_tokens)
            ).where(LlmUsageRecord.run_id == run_id)
        )
        return int(result or 0)

    def list_usage_for_run(self, run_id: int) -> list[LlmUsageRecord]:
        """列出某次运行的全部 LLM 使用记录。"""
        stmt = (
            select(LlmUsageRecord)
            .where(LlmUsageRecord.run_id == run_id)
            .order_by(LlmUsageRecord.created_at)
        )
        return list(self._db.scalars(stmt))
