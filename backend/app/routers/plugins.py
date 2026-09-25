"""TeachMate 外部插件宿主 API（Plugin Manager v1）。"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request

from ..auth import require_token
from ..services.plugin_manager import PluginManager, PluginValidationError

router = APIRouter(prefix="/api/v1/plugins", tags=["plugins"],
                   dependencies=[Depends(require_token)])


def _manager(request: Request) -> PluginManager:
    manager = getattr(request.app.state, "plugin_manager", None)
    if manager is None:
        manager = PluginManager(request.app.state.settings.data_dir)
        request.app.state.plugin_manager = manager
    return manager


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


@router.get("")
def list_plugins(request: Request):
    plugins = [item for item in _manager(request).list_plugins() if item.get("ui_visible", True)]
    return {"plugins": plugins, "api_version": "teachmate-plugin/v1"}


@router.get("/{plugin_id}")
def get_plugin(plugin_id: str, request: Request):
    plugin = _manager(request).get_plugin(plugin_id)
    if plugin is None:
        raise _error(404, "plugin_not_found", "插件不存在")
    return plugin


@router.post("/{plugin_id}/enable")
def enable_plugin(plugin_id: str, request: Request):
    try:
        return _manager(request).set_enabled(plugin_id, True)
    except KeyError:
        raise _error(404, "plugin_not_found", "插件不存在")


@router.post("/{plugin_id}/disable")
def disable_plugin(plugin_id: str, request: Request):
    try:
        return _manager(request).set_enabled(plugin_id, False)
    except KeyError:
        raise _error(404, "plugin_not_found", "插件不存在")


@router.get("/{plugin_id}/health")
def plugin_health(plugin_id: str, request: Request):
    try:
        return _manager(request).health_check(plugin_id)
    except KeyError:
        raise _error(404, "plugin_not_found", "插件不存在")


@router.post("/{plugin_id}/call")
async def call_plugin_tool(plugin_id: str, request: Request):
    try:
        payload = await request.json()
        tool_name = str((payload or {}).get("tool") or "").strip()
        if not tool_name:
            raise PluginValidationError("缺少 tool")
        return _manager(request).call_tool(plugin_id, tool_name, (payload or {}).get("arguments") or {})
    except KeyError:
        raise _error(404, "plugin_not_found", "插件不存在")
    except (json.JSONDecodeError, PluginValidationError) as error:
        raise _error(422, "plugin_call_failed", str(error))


@router.delete("/{plugin_id}")
def delete_plugin(plugin_id: str, request: Request):
    try:
        _manager(request).remove(plugin_id)
    except KeyError:
        raise _error(404, "plugin_not_found", "插件不存在")
    except PluginValidationError as error:
        raise _error(409, "plugin_not_removable", str(error))
    return {"ok": True, "id": plugin_id}


@router.post("/install")
async def install_plugin(request: Request):
    """安装本地插件目录或 ZIP。

    JSON 方式：{"path": "/absolute/path/to/plugin.zip"}。
    ZIP 直传方式：Content-Type=application/zip，并提供 X-Plugin-Filename。
    两种方式都要求本机教师令牌，避免普通网页直接触发安装。
    """
    manager = _manager(request)
    try:
        content_type = (request.headers.get("content-type") or "").split(";", 1)[0].lower()
        if content_type == "application/zip" or content_type == "application/octet-stream":
            raw = await request.body()
            if len(raw) > 50 * 1024 * 1024:
                raise PluginValidationError("插件包超过 50MB")
            with tempfile.NamedTemporaryFile(prefix="teachmate-plugin-", suffix=".zip", delete=False) as handle:
                handle.write(raw)
                archive = Path(handle.name)
            try:
                return manager.install_archive(archive, filename=request.headers.get("x-plugin-filename", "plugin.zip"))
            finally:
                archive.unlink(missing_ok=True)
        payload = await request.json()
        source_path = Path(str((payload or {}).get("path") or "")).expanduser().resolve()
        if source_path.is_dir():
            with tempfile.NamedTemporaryFile(prefix="teachmate-plugin-", suffix=".zip", delete=False) as handle:
                archive = Path(handle.name)
            try:
                import shutil
                import zipfile
                total_size = 0
                with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
                    for path in source_path.rglob("*"):
                        if path.is_symlink() or not path.is_file() or ".staging" in path.parts:
                            continue
                        total_size += path.stat().st_size
                        if total_size > 50 * 1024 * 1024:
                            raise PluginValidationError("插件目录超过 50MB")
                        bundle.write(path, path.relative_to(source_path))
                return manager.install_archive(archive, filename=source_path.name)
            finally:
                archive.unlink(missing_ok=True)
        if not source_path.is_file():
            raise PluginValidationError("插件路径不存在")
        return manager.install_archive(source_path, filename=source_path.name)
    except (json.JSONDecodeError, PluginValidationError) as error:
        raise _error(422, "plugin_invalid", str(error))
    except OSError as error:
        raise _error(400, "plugin_install_failed", str(error))


@router.get("/catalog/list")
def plugin_catalog(request: Request):
    """读取配置的第三方插件目录；未配置时 fail-closed 返回空目录。"""
    registry_url = os.getenv("TEACHMATE_PLUGIN_REGISTRY_URL", "").strip()
    if not registry_url:
        return {"configured": False, "plugins": []}
    from urllib.request import Request as UrlRequest, urlopen
    try:
        registry_url = PluginManager._registry_url(registry_url)
        req = UrlRequest(registry_url, headers={"Accept": "application/json"})
        with urlopen(req, timeout=5) as response:  # noqa: S310 - URL 来自管理员环境变量
            payload = json.loads(response.read().decode("utf-8"))
        return {"configured": True, "plugins": payload.get("plugins", []) if isinstance(payload, dict) else []}
    except Exception as error:
        raise _error(503, "plugin_registry_unavailable", f"插件目录暂时不可用：{type(error).__name__}")


@router.post("/install-from-catalog")
async def install_from_catalog(request: Request):
    try:
        payload = await request.json()
        plugin_id = str((payload or {}).get("id") or "").strip()
        version = (payload or {}).get("version")
        if not plugin_id:
            raise PluginValidationError("缺少插件 id")
        return _manager(request).install_from_catalog(plugin_id, str(version) if version else None)
    except (json.JSONDecodeError, PluginValidationError) as error:
        raise _error(422, "plugin_invalid", str(error))
    except Exception as error:
        raise _error(503, "plugin_registry_unavailable", f"插件安装服务暂时不可用：{type(error).__name__}")
