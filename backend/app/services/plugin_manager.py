"""TeachMate 插件宿主（Plugin Contract v1）。

插件管理器只负责生命周期和安全边界，不直接执行插件业务逻辑：
- bundled 插件随软件发布，但仍通过 manifest 注册；
- installed 插件存放在 data_dir/plugins/installed；
- 外部插件只允许通过 MCP/HTTP 或 TeachMate 稳定 API 交互；
- 安装先写临时目录，校验通过后再原子移动，失败不污染现有插件。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from urllib.parse import urlparse
import zipfile
from pathlib import Path
from typing import Any


PLUGIN_API_VERSION = "teachmate-plugin/v1"
MCP_PROTOCOL_VERSION = "2025-11-25"
PLUGIN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")
ALLOWED_PERMISSIONS = {
    "teaching.read",
    "teaching.write",
    "documents.read",
    "documents.write",
    "export.word",
    "export.pdf",
}


class PluginValidationError(ValueError):
    """插件清单或压缩包不符合安全契约。"""


class PluginManager:
    """管理 bundled/installed 插件的发现、状态、安装和健康检查。"""

    def __init__(self, data_dir: Path, bundled_root: Path | None = None) -> None:
        self.data_dir = Path(data_dir)
        self.root = self.data_dir / "plugins"
        self.installed_root = self.root / "installed"
        self.registry_path = self.root / "registry.json"
        self.bundled_root = bundled_root or Path(__file__).resolve().parents[3] / "plugins" / "bundled"
        self.root.mkdir(parents=True, exist_ok=True)
        self.installed_root.mkdir(parents=True, exist_ok=True)

    # ---- registry ----
    @staticmethod
    def _registry_url(value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise PluginValidationError("插件目录地址必须使用 http/https")
        return value

    def _read_registry(self) -> dict[str, Any]:
        if not self.registry_path.exists():
            return {"plugins": {}}
        try:
            data = json.loads(self.registry_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {"plugins": {}}
        except (OSError, json.JSONDecodeError):
            return {"plugins": {}}

    def _read_runtime_overrides(self) -> dict[str, Any]:
        path = self.root / "runtime-config.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _write_runtime_overrides(self, value: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix="runtime-config.", suffix=".tmp", dir=self.root)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(value, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            os.replace(tmp_name, self.root / "runtime-config.json")
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)

    def configure_runtime(self, plugin_id: str, runtime: dict[str, Any]) -> None:
        """保存 HTTP 插件运行时覆盖；敏感令牌由调用方放入 keyvault。"""
        overrides = self._read_runtime_overrides()
        overrides[plugin_id] = runtime
        self._write_runtime_overrides(overrides)

    def _write_registry(self, data: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix="registry.", suffix=".tmp", dir=self.root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            os.replace(tmp_name, self.registry_path)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)

    # ---- manifests ----
    @staticmethod
    def _manifest_path(plugin_root: Path) -> Path | None:
        for relative in (Path(".teachmate-plugin/plugin.json"), Path(".codex-plugin/plugin.json")):
            candidate = plugin_root / relative
            if candidate.is_file():
                return candidate
        return None

    @staticmethod
    def validate_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(manifest, dict):
            raise PluginValidationError("plugin.json 必须是 JSON 对象")
        plugin_id = str(manifest.get("id") or manifest.get("name") or "").strip().lower()
        if not PLUGIN_ID_RE.fullmatch(plugin_id):
            raise PluginValidationError("插件 id 必须是 2-64 位小写字母、数字、点、下划线或连字符")
        version = str(manifest.get("version") or "").strip()
        if not version or len(version) > 64:
            raise PluginValidationError("插件 version 不能为空且不能超过 64 个字符")
        api_version = str(manifest.get("api_version") or manifest.get("api") or PLUGIN_API_VERSION)
        if api_version not in {PLUGIN_API_VERSION, "codex-plugin/v1"}:
            raise PluginValidationError(f"不支持的插件 API 版本：{api_version}")
        permissions = manifest.get("permissions", manifest.get("scope", []))
        if isinstance(permissions, str):
            permissions = [permissions]
        if not isinstance(permissions, list) or any(str(item) not in ALLOWED_PERMISSIONS for item in permissions):
            raise PluginValidationError("插件 permissions 含有未知权限")
        capabilities = manifest.get("capabilities", [])
        if not isinstance(capabilities, list):
            raise PluginValidationError("插件 capabilities 必须是数组")
        runtime = manifest.get("runtime", manifest.get("server", {}))
        if runtime and not isinstance(runtime, dict):
            raise PluginValidationError("插件 runtime/server 必须是对象")
        transport = str(runtime.get("transport", "internal")) if runtime else "internal"
        if transport not in {"internal", "stdio", "http"}:
            raise PluginValidationError("插件 transport 仅支持 internal、stdio、http")
        if transport == "http":
            endpoint = str(runtime.get("url") or runtime.get("endpoint") or "").strip()
            parsed_endpoint = urlparse(endpoint)
            if parsed_endpoint.scheme not in {"http", "https"} or not parsed_endpoint.netloc:
                raise PluginValidationError("HTTP 插件 runtime 缺少有效的 url/endpoint")
        return {
            "id": plugin_id,
            "name": plugin_id,
            "display_name": str(manifest.get("display_name") or manifest.get("displayName") or plugin_id),
            "description": str(manifest.get("description") or ""),
            "version": version,
            "api_version": PLUGIN_API_VERSION,
            "source": str(manifest.get("source") or "external"),
            "permissions": [str(item) for item in permissions],
            "capabilities": capabilities,
            "skills": manifest.get("skills", []) if isinstance(manifest.get("skills", []), list) else [],
            "runtime": {"transport": transport, **runtime},
            "removable": bool(manifest.get("removable", True)),
            "enabled_by_default": bool(manifest.get("enabled_by_default", True)),
            "ui_visible": bool(manifest.get("ui_visible", True)),
        }

    def _read_manifest(self, root: Path) -> dict[str, Any] | None:
        path = self._manifest_path(root)
        if path is None:
            return None
        try:
            return self.validate_manifest(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, PluginValidationError):
            return None

    def _discover_roots(self) -> list[tuple[Path, str]]:
        roots: list[tuple[Path, str]] = []
        if self.bundled_root.is_dir():
            roots.extend((item, "bundled") for item in sorted(self.bundled_root.iterdir()) if item.is_dir())
        if self.installed_root.is_dir():
            roots.extend((item, "external") for item in sorted(self.installed_root.iterdir()) if item.is_dir())
        return roots

    def list_plugins(self) -> list[dict[str, Any]]:
        registry = self._read_registry().get("plugins", {})
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for root, source in self._discover_roots():
            manifest = self._read_manifest(root)
            if not manifest or manifest["id"] in seen:
                continue
            seen.add(manifest["id"])
            saved = registry.get(manifest["id"], {})
            runtime_override = self._read_runtime_overrides().get(manifest["id"], {})
            runtime = {**manifest["runtime"], **runtime_override} if isinstance(runtime_override, dict) else manifest["runtime"]
            enabled = bool(saved.get("enabled", manifest["enabled_by_default"]))
            result.append({
                **manifest,
                "runtime": runtime,
                "source": source,
                "path": str(root),
                "enabled": enabled,
                "health": "ready" if enabled else "disabled",
            })
        return result

    def get_plugin(self, plugin_id: str) -> dict[str, Any] | None:
        return next((item for item in self.list_plugins() if item["id"] == plugin_id), None)

    def set_enabled(self, plugin_id: str, enabled: bool) -> dict[str, Any]:
        plugin = self.get_plugin(plugin_id)
        if plugin is None:
            raise KeyError(plugin_id)
        registry = self._read_registry()
        states = registry.setdefault("plugins", {})
        states[plugin_id] = {"enabled": bool(enabled), "version": plugin["version"]}
        self._write_registry(registry)
        return self.get_plugin(plugin_id) or plugin

    # ---- installation ----
    @staticmethod
    def _safe_extract(archive: Path, destination: Path) -> None:
        with zipfile.ZipFile(archive) as bundle:
            for info in bundle.infolist():
                member = Path(info.filename)
                if member.is_absolute() or ".." in member.parts:
                    raise PluginValidationError("插件压缩包包含越界路径")
                # Unix mode 的 symlink 条目可能把解压目录指向外部路径，拒绝后再解压。
                if ((info.external_attr >> 16) & 0o170000) == 0o120000:
                    raise PluginValidationError("插件压缩包不允许包含符号链接")
                target = (destination / member).resolve()
                if destination.resolve() not in target.parents and target != destination.resolve():
                    raise PluginValidationError("插件压缩包包含非法路径")
            bundle.extractall(destination)

    def install_archive(self, archive: Path, *, filename: str = "plugin.zip") -> dict[str, Any]:
        if not archive.is_file() or archive.stat().st_size > 50 * 1024 * 1024:
            raise PluginValidationError("插件包不存在或超过 50MB")
        with tempfile.TemporaryDirectory(prefix="teachmate-plugin-") as temp:
            unpacked = Path(temp) / "unpacked"
            unpacked.mkdir()
            self._safe_extract(archive, unpacked)
            candidates = [unpacked] + [item for item in unpacked.iterdir() if item.is_dir()]
            source_root = next((item for item in candidates if self._manifest_path(item)), None)
            if source_root is None:
                raise PluginValidationError("插件包缺少 .teachmate-plugin/plugin.json 或 .codex-plugin/plugin.json")
            manifest = self._read_manifest(source_root)
            if manifest is None:
                raise PluginValidationError("插件清单无效")
            target = self.installed_root / manifest["id"]
            staging = self.installed_root / f".{manifest['id']}.staging"
            previous = self.installed_root / f".{manifest['id']}.previous"
            if staging.exists():
                shutil.rmtree(staging)
            if previous.exists():
                shutil.rmtree(previous)
            shutil.copytree(source_root, staging)
            # 升级采用可回滚的目录交换：先保留旧版本，避免进程在替换
            # 窗口崩溃后插件目录被清空。
            if target.exists():
                os.replace(target, previous)
            try:
                os.replace(staging, target)
            except Exception:
                if previous.exists() and not target.exists():
                    os.replace(previous, target)
                raise
            if previous.exists():
                shutil.rmtree(previous)
            registry = self._read_registry()
            registry.setdefault("plugins", {})[manifest["id"]] = {
                "enabled": manifest["enabled_by_default"],
                "version": manifest["version"],
                "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                "filename": filename,
            }
            self._write_registry(registry)
        return self.get_plugin(manifest["id"]) or manifest

    def install_from_catalog(self, plugin_id: str, version: str | None = None) -> dict[str, Any]:
        """从管理员配置的插件目录下载并校验插件。

        目录服务只负责返回 metadata；真正安装前仍要求 sha256 匹配。
        未配置 TEACHMATE_PLUGIN_REGISTRY_URL 时 fail-closed。
        """
        registry_url = os.getenv("TEACHMATE_PLUGIN_REGISTRY_URL", "").strip()
        if not registry_url:
            raise PluginValidationError("尚未配置 TEACHMATE_PLUGIN_REGISTRY_URL")
        registry_url = self._registry_url(registry_url)
        request = urllib.request.Request(registry_url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310 - 仅使用管理员配置地址
            payload = json.loads(response.read().decode("utf-8"))
        entries = payload.get("plugins", []) if isinstance(payload, dict) else []
        entry = next((item for item in entries if str(item.get("id")) == plugin_id and (version is None or str(item.get("version")) == version)), None)
        if not isinstance(entry, dict) or not entry.get("download_url"):
            raise PluginValidationError("插件目录中找不到指定版本")
        download_url = self._registry_url(str(entry["download_url"]))
        download_request = urllib.request.Request(download_url, headers={"Accept": "application/zip"})
        with urllib.request.urlopen(download_request, timeout=20) as response:  # noqa: S310
            data = response.read(50 * 1024 * 1024 + 1)
        if len(data) > 50 * 1024 * 1024:
            raise PluginValidationError("插件包超过 50MB")
        expected = str(entry.get("sha256") or "").lower()
        actual = hashlib.sha256(data).hexdigest()
        if not expected or expected != actual:
            raise PluginValidationError("插件包 sha256 校验失败")
        with tempfile.NamedTemporaryFile(prefix="teachmate-remote-plugin-", suffix=".zip", delete=False) as handle:
            handle.write(data)
            archive = Path(handle.name)
        try:
            return self.install_archive(archive, filename=f"{plugin_id}-{version or 'latest'}.zip")
        finally:
            archive.unlink(missing_ok=True)

    # ---- runtime ----
    def _stdio_exchange(self, plugin: dict[str, Any], messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        runtime = plugin.get("runtime") or {}
        command = runtime.get("command")
        args = runtime.get("args", [])
        if not command or not isinstance(args, list):
            raise PluginValidationError("插件 stdio runtime 缺少 command/args")
        env = {"PATH": os.getenv("PATH", ""), "PYTHONUNBUFFERED": "1"}
        env.update({str(k): str(v) for k, v in (runtime.get("env") or {}).items()})
        payload = "".join(json.dumps(message, ensure_ascii=False) + "\n" for message in messages)
        try:
            proc = subprocess.run(
                [str(command), *[str(arg) for arg in args]],
                cwd=Path(plugin["path"]).resolve(), input=payload, text=True,
                capture_output=True, timeout=10, check=False, env=env,
            )
        except subprocess.TimeoutExpired as error:
            raise PluginValidationError("插件进程超时") from error
        if proc.returncode != 0:
            raise PluginValidationError(proc.stderr[-300:] or "插件进程退出失败")
        responses: list[dict[str, Any]] = []
        for line in proc.stdout.splitlines():
            try:
                value = json.loads(line)
                if isinstance(value, dict) and "id" in value:
                    responses.append(value)
            except json.JSONDecodeError:
                continue
        return responses

    def _http_exchange(self, plugin: dict[str, Any], messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """调用 Streamable HTTP MCP；保留认证头和 initialize 返回的会话。"""
        runtime = plugin.get("runtime") or {}
        endpoint = str(runtime.get("url") or runtime.get("endpoint") or "").strip()
        self._registry_url(endpoint)
        headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
        static_headers = runtime.get("headers") or runtime.get("http_headers") or {}
        if isinstance(static_headers, dict):
            headers.update({str(key): str(value) for key, value in static_headers.items()})
        env_headers = runtime.get("env_http_headers") or {}
        if isinstance(env_headers, dict):
            for header_name, env_name in env_headers.items():
                value = os.getenv(str(env_name), "").strip()
                if value:
                    headers[str(header_name)] = value
        token_env = runtime.get("bearer_token_env_var") or runtime.get("bearer_token_env")
        token = os.getenv(str(token_env), "").strip() if token_env else ""
        if not token and runtime.get("bearer_token_profile"):
            try:
                from ..agent.keyvault import load_api_key
                token = load_api_key(
                    str(runtime["bearer_token_profile"]),
                    data_dir=self.data_dir,
                ) or ""
            except OSError:
                token = ""
        if token:
            headers["Authorization"] = f"Bearer {token}"
        elif runtime.get("require_bearer_token", False):
            raise PluginValidationError("MCP 插件未配置 Bearer 令牌")

        def decode_response(raw: bytes, content_type: str) -> dict[str, Any]:
            text = raw.decode("utf-8")
            if "text/event-stream" in content_type:
                data_lines = [line[5:].lstrip() for line in text.splitlines() if line.startswith("data:")]
                text = data_lines[-1] if data_lines else ""
            value = json.loads(text)
            if not isinstance(value, dict):
                raise PluginValidationError("MCP 返回不是 JSON 对象")
            return value

        ca_bundle = str(runtime.get("ca_bundle") or os.getenv("MONI_MCP_CA_BUNDLE") or os.getenv("SSL_CERT_FILE") or "").strip()
        ssl_context = None
        if ca_bundle:
            try:
                ssl_context = ssl.create_default_context(cafile=ca_bundle)
            except (OSError, ssl.SSLError) as error:
                raise PluginValidationError(f"MCP TLS CA 配置无效：{type(error).__name__}") from error
        elif sys.platform == "darwin":
            # Python/OpenSSL 不会自动读取 macOS Keychain；系统浏览器和 curl
            # 能信任的公开 CA，需从系统信任库导入到内存中的 SSL context。
            try:
                system_roots = subprocess.check_output(
                    ["security", "find-certificate", "-a", "-p", "/Library/Keychains/System.keychain", "/System/Library/Keychains/SystemRootCertificates.keychain"],
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                ).decode("utf-8", "ignore")
                if system_roots.strip():
                    ssl_context = ssl.create_default_context()
                    ssl_context.load_verify_locations(cadata=system_roots)
            except (OSError, subprocess.SubprocessError, ssl.SSLError):
                # 导入失败时仍使用 OpenSSL 默认信任库，绝不退化为关闭 TLS 校验。
                ssl_context = ssl.create_default_context()

        responses: list[dict[str, Any]] = []
        session_id: str | None = None
        protocol_version = MCP_PROTOCOL_VERSION
        for message in messages:
            request_headers = dict(headers)
            if session_id:
                request_headers["Mcp-Session-Id"] = session_id
                request_headers["MCP-Protocol-Version"] = protocol_version
            request = urllib.request.Request(
                endpoint,
                data=json.dumps(message, ensure_ascii=False).encode("utf-8"),
                headers=request_headers,
                method="POST",
            )
            open_kwargs = {"timeout": 10}
            if ssl_context is not None:
                open_kwargs["context"] = ssl_context
            with urllib.request.urlopen(request, **open_kwargs) as response:  # noqa: S310 - manifest 已校验协议
                getheader = getattr(response, "getheader", None)
                if callable(getheader):
                    session_id = getheader("Mcp-Session-Id") or session_id
                content_type = str(getheader("Content-Type") if callable(getheader) else "application/json")
                raw = response.read()
                if not raw:
                    # MCP notifications (例如 notifications/initialized) 通常返回 202 空响应。
                    continue
                decoded = decode_response(raw, content_type)
                negotiated = str((decoded.get("result") or {}).get("protocolVersion") or "").strip()
                if negotiated:
                    protocol_version = negotiated
                responses.append(decoded)
        return responses

    @staticmethod
    def _initialize_message(message_id: int = 1) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": message_id,
            "method": "initialize",
            "params": {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "teachmate-workbench", "version": "1.0"},
            },
        }

    def call_tool(self, plugin_id: str, tool_name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        plugin = self.get_plugin(plugin_id)
        if plugin is None:
            raise KeyError(plugin_id)
        if not plugin["enabled"]:
            raise PluginValidationError("插件已停用")
        runtime = plugin.get("runtime") or {}
        if runtime.get("transport") not in {"stdio", "http"}:
            raise PluginValidationError("当前仅支持 stdio/http 插件调用")
        declared = plugin.get("capabilities") or []
        declared_ids = {str(item.get("id") if isinstance(item, dict) else item) for item in declared}
        if declared_ids and tool_name not in declared_ids:
            raise PluginValidationError("工具未在插件 manifest 中声明")
        exchange = self._stdio_exchange if runtime.get("transport") == "stdio" else self._http_exchange
        responses = exchange(plugin, [
            self._initialize_message(),
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": tool_name, "arguments": arguments or {}}},
        ])
        response = next((item for item in responses if item.get("id") == 2), None)
        if response is None:
            raise PluginValidationError("插件未返回工具结果")
        if "error" in response:
            raise PluginValidationError(str(response["error"].get("message") or "插件工具调用失败"))
        return response.get("result") or {}

    def health_check(self, plugin_id: str) -> dict[str, Any]:
        plugin = self.get_plugin(plugin_id)
        if plugin is None:
            raise KeyError(plugin_id)
        if not plugin["enabled"]:
            return {"id": plugin_id, "health": "disabled", "tools": []}
        runtime = plugin.get("runtime") or {}
        if runtime.get("transport") == "internal":
            return {"id": plugin_id, "health": "ready", "tools": plugin.get("capabilities", [])}
        if runtime.get("transport") not in {"stdio", "http"}:
            return {"id": plugin_id, "health": "unknown", "tools": plugin.get("capabilities", [])}
        try:
            exchange = self._stdio_exchange if runtime.get("transport") == "stdio" else self._http_exchange
            responses = exchange(plugin, [
                self._initialize_message(),
                {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            ])
            response = next((item for item in responses if item.get("id") == 2), {})
            if response.get("error"):
                message = response["error"].get("message") if isinstance(response["error"], dict) else response["error"]
                return {"id": plugin_id, "health": "failed", "tools": [], "error": str(message or "MCP 工具列表请求失败")}
            tools = (response.get("result") or {}).get("tools", [])
            return {"id": plugin_id, "health": "ready", "tools": tools}
        except urllib.error.HTTPError as error:
            return {"id": plugin_id, "health": "failed", "tools": [], "error": f"HTTP {error.code}"}
        except urllib.error.URLError:
            return {"id": plugin_id, "health": "failed", "tools": [], "error": "网络连接失败"}
        except (OSError, subprocess.TimeoutExpired, PluginValidationError) as error:
            return {"id": plugin_id, "health": "failed", "tools": [], "error": type(error).__name__}

    def remove(self, plugin_id: str) -> None:
        plugin = self.get_plugin(plugin_id)
        if plugin is None:
            raise KeyError(plugin_id)
        if plugin["source"] == "bundled" or not plugin["removable"]:
            raise PluginValidationError("软件自带插件不可卸载")
        shutil.rmtree(Path(plugin["path"]).resolve())
        registry = self._read_registry()
        registry.setdefault("plugins", {}).pop(plugin_id, None)
        self._write_registry(registry)
