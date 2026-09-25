"""插件 HTTP 客户端（标准库 urllib，零依赖）。

只做协议与错误映射，不复制任何业务逻辑。opener 可注入以便测试。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request


class PluginClientError(Exception):
    def __init__(self, code: str, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class PluginClient:
    def __init__(self, base_url: str, token: str, api_version: str = "1.0",
                 *, opener=None) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.api_version = api_version
        self._opener = opener

    def call(self, method: str, path: str, params: dict | None = None) -> dict:
        url = self.base_url + path
        if params:
            q = "&".join(
                f"{k}={urllib.parse.quote(str(v), safe='')}"
                for k, v in params.items() if v is not None
            )
            if q:
                url += ("&" if "?" in url else "?") + q
        req = urllib.request.Request(url, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Accept", "application/json")
        try:
            if self._opener is not None:
                resp = self._opener(req)
            else:
                resp = urllib.request.urlopen(req, timeout=30)
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {}
        except urllib.error.HTTPError as e:
            detail = {}
            try:
                detail = json.loads(e.read().decode("utf-8") or "{}")
            except Exception:
                detail = {}
            d = detail.get("detail")
            code = d.get("code") if isinstance(d, dict) else None
            msg = d.get("message") if isinstance(d, dict) else d
            raise PluginClientError(code or "http_error", msg or e.reason, e.code)
        except urllib.error.URLError as e:
            raise PluginClientError("service_unavailable", f"无法连接 TeachMate：{e.reason}")
