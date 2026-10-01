"""学校数据源注册表。

两类来源用同一套接口对外：

- **内置 MONI**：取数逻辑不动，只是被包成适配器（``moni.MoniSource``）。
- **自定义 MCP 数据源**：由 ``school_data_sources`` 里的配置驱动
  （``generic_mcp.GenericMcpSource``）。为了让既有的 MCP 宿主（PluginManager：
  http/stdio transport、Bearer 令牌、TLS 兼容、健康检查）直接可用，配置会
  「物化」成 ``plugins/installed/<plugin_id>/.teachmate-plugin/plugin.json``。
  这样换学校只是换一份配置，不需要新增代码或插件包。

约定：
- 数据源标识（``source_key``）同时用作插件 id 与令牌档案名，因此必须是小写
  ``[a-z0-9._-]``；两个数据源不会互相覆盖令牌。
- 令牌只写 keyvault，配置里只留 profile 名；任何对外返回都经过遮蔽。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...config import Settings
from ...models import SchoolDataSource
from ...agent.keyvault import api_key_configured, clear_api_key, load_api_key, save_api_key
from .config import (
    SourceConfigError,
    default_token_profile,
    display_config,
    missing_required_fields,
    parse_config,
    runtime_for,
)
from .generic_mcp import GenericMcpSource
from .moni import MONI_SOURCE_KEY, MoniSource

SOURCE_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")
RESERVED_SOURCE_KEYS = {MONI_SOURCE_KEY, "mock"}


def normalize_source_key(value: Any) -> str:
    key = str(value or "").strip().lower()
    if not SOURCE_KEY_RE.fullmatch(key):
        raise SourceConfigError(
            "数据源标识只能用小写字母、数字、点、下划线或连字符，长度 2-64，且以字母或数字开头"
        )
    if key in RESERVED_SOURCE_KEYS:
        raise SourceConfigError(f"「{key}」是保留标识，请换一个名字")
    return key


# ---- 数据库登记 ----


def ensure_source_row(session: Session, *, source_key: str, name: str, kind: str,
                      config: dict[str, Any] | None = None, enabled: bool = True) -> SchoolDataSource:
    row = session.scalar(select(SchoolDataSource).where(SchoolDataSource.source_key == source_key))
    if row is None:
        row = SchoolDataSource(source_key=source_key, name=name, kind=kind,
                               config_json=dict(config or {}), enabled=enabled)
        session.add(row)
        session.flush()
        return row
    row.name = name
    row.kind = kind
    row.config_json = dict(config or {})
    row.enabled = bool(enabled)
    session.flush()
    return row


def get_source_row(session: Session, source_key: str) -> SchoolDataSource | None:
    return session.scalar(
        select(SchoolDataSource).where(SchoolDataSource.source_key == source_key))


def list_source_rows(session: Session) -> list[SchoolDataSource]:
    return list(session.scalars(select(SchoolDataSource).order_by(SchoolDataSource.id)))


# ---- 适配器解析 ----


def resolve_source(settings: Settings, session: Session, source_key: str):
    """按标识解析适配器；内置 MONI 永远可用，未知标识抛 ``KeyError``。"""
    if source_key == MONI_SOURCE_KEY:
        return MoniSource(settings)
    row = get_source_row(session, source_key)
    if row is None:
        raise KeyError(source_key)
    if row.kind != "generic_mcp":
        raise SourceConfigError(f"数据源「{source_key}」的类型 {row.kind} 暂不支持同步")
    return GenericMcpSource(
        settings, source_key=row.source_key, name=row.name,
        raw_config=row.config_json, enabled=bool(row.enabled))


# ---- 插件物化 ----


def _plugin_dir(settings: Settings, plugin_id: str) -> Path:
    return Path(settings.data_dir) / "plugins" / "installed" / plugin_id


def materialize_plugin(settings: Settings, *, source_key: str, name: str,
                       config: dict[str, Any]) -> str:
    """把配置写成插件清单，让 PluginManager 能像对待内置插件一样调用它。"""
    parsed = parse_config(config, source_key=source_key)
    plugin_id = parsed.plugin_id
    root = _plugin_dir(settings, plugin_id)
    manifest_dir = root / ".teachmate-plugin"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "id": plugin_id,
        "display_name": name,
        "description": f"自定义 MCP 学校数据源：{name}",
        "version": "1.0.0",
        "api_version": "teachmate-plugin/v1",
        "source": "external",
        "runtime": {"transport": "http", "url": parsed.endpoint},
        "permissions": ["teaching.read"],
        "capabilities": [
            {"id": parsed.tool("list"), "name": "列出数据目录"},
            {"id": parsed.tool("read"), "name": "读取数据文件"},
            {"id": parsed.tool("query"), "name": "查询数据"},
        ],
        "skills": [],
        "removable": True,
        "enabled_by_default": True,
        "ui_visible": False,
    }
    (manifest_dir / "plugin.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    from ..plugin_manager import PluginManager

    PluginManager(settings.data_dir).configure_runtime(plugin_id, runtime_for(parsed))
    return plugin_id


def remove_plugin(settings: Settings, *, source_key: str) -> None:
    """删除物化出来的插件清单与运行时覆盖（数据源被删除时调用）。"""
    plugin_id = f"school-{source_key}"
    root = _plugin_dir(settings, plugin_id)
    if root.exists():
        import shutil

        shutil.rmtree(root, ignore_errors=True)
    from ..plugin_manager import PluginManager

    manager = PluginManager(settings.data_dir)
    overrides = manager._read_runtime_overrides()  # noqa: SLF001 - 同一模块族内的清理
    if plugin_id in overrides:
        overrides.pop(plugin_id, None)
        manager._write_runtime_overrides(overrides)  # noqa: SLF001


# ---- 配置读写 ----


def token_configured(settings: Settings, source_key: str) -> bool:
    return api_key_configured(default_token_profile(source_key), data_dir=settings.data_dir)


def key_hint(settings: Settings, source_key: str) -> str | None:
    key = load_api_key(default_token_profile(source_key), data_dir=settings.data_dir) or ""
    if not key:
        return None
    return "••••••••" + key[-4:] if len(key) >= 4 else "••••••••"


def save_source_config(
    settings: Settings,
    session: Session,
    *,
    source_key: str,
    name: str,
    config: dict[str, Any],
    bearer_token: str | None = None,
    enabled: bool = True,
) -> SchoolDataSource:
    """校验并保存配置：校验不通过时什么都不写，通过后依次写令牌、插件清单与登记行。"""
    key = normalize_source_key(source_key)
    display_name = " ".join(str(name or "").split())[:150] or key
    parsed = parse_config(config, source_key=key)
    missing = missing_required_fields(parsed, with_exams=bool(parsed.path("exams")))
    if missing:
        raise SourceConfigError("字段映射缺少关键项：" + "、".join(missing))

    token = str(bearer_token or "").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    if parsed.auth_type == "bearer" and token:
        try:
            save_api_key(token, default_token_profile(key), data_dir=settings.data_dir)
        except OSError as exc:
            raise SourceConfigError("本机令牌保存失败，请重启 WorkBench 后再试") from exc

    materialize_plugin(settings, source_key=key, name=display_name, config=config)
    return ensure_source_row(session, source_key=key, name=display_name,
                             kind="generic_mcp", config=config, enabled=enabled)


def delete_source(settings: Settings, session: Session, *, source_key: str) -> None:
    """删除自定义数据源：登记行、物化插件与专属令牌一起清掉，不留孤儿。

    内置 MONI 与保留标识不允许走这里（``normalize_source_key`` 会拒绝），
    避免误删内置来源。
    """
    key = normalize_source_key(source_key)
    row = get_source_row(session, key)
    if row is None:
        raise KeyError(key)
    session.delete(row)
    session.flush()
    remove_plugin(settings, source_key=key)
    clear_api_key(default_token_profile(key), data_dir=settings.data_dir)


def source_status(settings: Settings, row: SchoolDataSource) -> dict[str, Any]:
    """数据源列表项：配置回显已遮蔽，令牌只给「是否已配置 + 尾号提示」。"""
    payload: dict[str, Any] = {
        "source_key": row.source_key,
        "name": row.name,
        "kind": row.kind,
        "enabled": bool(row.enabled),
        "last_sync_at": row.last_sync_at,
        "token_configured": token_configured(settings, row.source_key),
        "key_hint": key_hint(settings, row.source_key),
    }
    if row.source_key == MONI_SOURCE_KEY:
        # 内置来源的配置不是「字段映射」那一套，配置视图仍由 /moni/config 提供；
        # 列表里只标记它是内置的，避免把它当成配置损坏。
        payload["builtin"] = True
        payload["configurable"] = False
        payload["config"] = MoniSource(settings).config_payload()
        return payload
    if row.kind != "generic_mcp":
        # 由导入过程自动登记的来源（mock、旧版 mcp）没有可编辑的字段映射配置，
        # 不要拿它去走通用配置校验，否则列表里会冒出一堆假报错。
        payload["builtin"] = False
        payload["configurable"] = False
        payload["config"] = None
        return payload
    payload["builtin"] = False
    payload["configurable"] = True
    try:
        payload["config"] = display_config(row.config_json, source_key=row.source_key)
    except SourceConfigError as exc:
        # 配置损坏时不要让整个列表接口失败，返回错误说明让教师能去修。
        payload["config"] = None
        payload["config_error"] = str(exc)
    return payload
