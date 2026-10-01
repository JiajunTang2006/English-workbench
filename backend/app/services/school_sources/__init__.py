"""学校数据源适配器包。

把「学校数据从哪来」与「数据怎么入库」解耦：

- ``mcp_text``：MCP 返回值的通用解码。
- ``fields``：配置式字段映射。
- ``config``：数据源配置的解析与校验。
- ``generic_mcp``：配置驱动的通用 MCP 数据源。
- ``moni``：内置 MONI 适配器（行为不变）。
- ``registry``：数据源注册、插件物化与配置读写。
- ``runner``：取到 payload 之后共用的入库尾部。

对外只需 ``resolve_source`` / ``list_source_rows`` / ``save_source_config``。
"""

from __future__ import annotations

from .config import SourceConfigError, default_token_profile, display_config, parse_config
from .generic_mcp import GenericMcpSource, GenericSourceError, build_source
from .moni import MONI_SOURCE_KEY, MONI_SOURCE_NAME, MoniSource, ensure_moni_source
from .registry import (
    RESERVED_SOURCE_KEYS,
    delete_source,
    ensure_source_row,
    get_source_row,
    key_hint,
    list_source_rows,
    materialize_plugin,
    normalize_source_key,
    remove_plugin,
    resolve_source,
    save_source_config,
    source_status,
    token_configured,
)

__all__ = [
    "MONI_SOURCE_KEY",
    "MONI_SOURCE_NAME",
    "RESERVED_SOURCE_KEYS",
    "GenericMcpSource",
    "GenericSourceError",
    "MoniSource",
    "SourceConfigError",
    "build_source",
    "default_token_profile",
    "delete_source",
    "display_config",
    "ensure_moni_source",
    "ensure_source_row",
    "get_source_row",
    "key_hint",
    "list_source_rows",
    "materialize_plugin",
    "normalize_source_key",
    "parse_config",
    "remove_plugin",
    "resolve_source",
    "save_source_config",
    "source_status",
    "token_configured",
]
