"""内置 MONI 数据源的适配器封装。

MONI 的取数逻辑（VFS 路径发现、科目筛选、分层线与年级名次补算）保持不变，
这里只把它包装成与自定义数据源相同的接口，让路由层能用同一套
``sources/{key}/*`` 端点驱动两种来源。**不改变任何 MONI 行为**。
"""

from __future__ import annotations

from typing import Any

from ...config import Settings
from ...models import SchoolDataSource
from ..plugin_manager import PluginManager, PluginValidationError
from .config import default_token_profile

MONI_SOURCE_KEY = "moni"
MONI_SOURCE_NAME = "MONI 学生数据"


class MoniSource:
    kind = "moni"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.source_key = MONI_SOURCE_KEY
        self.name = MONI_SOURCE_NAME
        self.enabled = True

    @property
    def plugin_id(self) -> str:
        return MONI_SOURCE_KEY

    def config_payload(self) -> dict[str, Any]:
        """与 ``GET /school-sync/moni/config`` 等价的配置视图。"""
        manager = PluginManager(self.settings.data_dir)
        plugin = manager.get_plugin(MONI_SOURCE_KEY) or {}
        runtime = plugin.get("runtime") or {}
        return {
            "transport": "http",
            "endpoint": runtime.get("url", ""),
            "headers": {},
            "auth": {"type": "bearer", "token_profile": default_token_profile(MONI_SOURCE_KEY)},
            "tools": {"list": "moni_vfs_list", "read": "moni_vfs_read",
                      "query": "moni_vfs_query_jsonl"},
            "paths": {
                "classes": "/classes/.list.jsonl",
                "term": "/school/current-term.json",
                "exams": "/classes/{class_id}/exams/.list.jsonl",
            },
            "field_map": {},
            "builtin": True,
        }

    def health(self) -> dict[str, Any]:
        manager = PluginManager(self.settings.data_dir)
        try:
            result = manager.health_check(MONI_SOURCE_KEY)
        except (KeyError, PluginValidationError) as exc:
            return {"health": "unavailable", "error": str(exc), "tool_count": 0}
        return {
            "health": result.get("health"),
            "error": result.get("error"),
            "tool_count": len(result.get("tools") or []),
        }

    def sync(self, *, dry_run: bool = False) -> dict[str, Any]:
        # 延迟导入：moni_sync 会反过来用到本包的解码与入库尾部，顶层导入会成环。
        from .. import moni_sync

        return moni_sync.sync_current_term(self.settings, dry_run=dry_run)

    def sync_roster(self, *, dry_run: bool = False) -> dict[str, Any]:
        from .. import moni_sync

        return moni_sync.sync_moni_roster(self.settings, dry_run=dry_run)


def ensure_moni_source(session, *, kind: str = "mcp") -> SchoolDataSource:
    """确保 ``school_data_sources`` 里有 MONI 的登记行（便于统一列出与统计）。"""
    from .registry import ensure_source_row

    return ensure_source_row(
        session, source_key=MONI_SOURCE_KEY, name=MONI_SOURCE_NAME, kind=kind,
        config={"builtin": True}, enabled=True)
