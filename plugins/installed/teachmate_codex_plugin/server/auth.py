"""插件配对与令牌持久化（客户端侧）。

配对是一次性手动步骤：教师在 TeachMate 设置页生成配对码，再用本机 WORKBENCH_TOKEN
（教师令牌）兑换 teaching.read 插件令牌并保存到本地。运行时仅使用插件令牌。
"""
from __future__ import annotations

import json
import os
import pathlib
import urllib.error
import urllib.request


def _token_path() -> pathlib.Path:
    base = os.getenv("TEACHMATE_PLUGIN_DATA_DIR")
    d = pathlib.Path(base) if base else pathlib.Path(__file__).resolve().parents[1] / ".data"
    return d / "client_token.json"


def load_token() -> str | None:
    configured = os.getenv("TEACHMATE_PLUGIN_TOKEN")
    if configured:
        return configured.strip() or None
    p = _token_path()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8")).get("token")
        except Exception:
            return None
    return None


def save_token(token: str) -> None:
    p = _token_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"token": token}, ensure_ascii=False), encoding="utf-8")


def pair(base_url: str, code: str, teacher_token: str) -> str:
    """用配对码 + 教师令牌兑换插件令牌。失败抛 RuntimeError。"""
    url = base_url.rstrip("/") + "/api/v1/plugin/auth/pair"
    req = urllib.request.Request(
        url, data=json.dumps({"code": code}).encode("utf-8"), method="POST",
    )
    req.add_header("Authorization", f"Bearer {teacher_token}")
    req.add_header("Content-Type", "application/json")
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = {}
        try:
            detail = json.loads(e.read().decode("utf-8") or "{}")
        except Exception:
            detail = {}
        d = detail.get("detail")
        msg = d.get("message") if isinstance(d, dict) else d
        raise RuntimeError(f"配对失败({e.code})：{msg}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"无法连接 TeachMate：{e.reason}")
    token = body.get("token")
    if not token:
        raise RuntimeError("配对返回缺少令牌")
    save_token(token)
    return token
