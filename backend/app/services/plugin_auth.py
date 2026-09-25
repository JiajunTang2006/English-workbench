"""Codex 插件访问令牌存储（L2-D 认证/隐私/审计）。

设计（见 docs/TEACHMATE_LONG_TERM_DEVELOPMENT_PLAN.md §9.5）：
- 服务端与浏览器 Token 分离；插件令牌仅持有 `teaching.read` scope。
- 服务端只保存令牌摘要（sha256）与权限/时间戳，绝不保存明文或反向可解值。
- 配对码一次性、短时有效；交换后立即可用，可一键撤销。
- 文件存储，对齐既有 provider/api_key 做法，避免引入数据库迁移；
  单进程服务用进程内锁 + 原子 os.replace 保证一致性。
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from secrets import compare_digest, token_urlsafe

logger = logging.getLogger(__name__)

PLUGIN_SCOPE = "teaching.read"
PAIRING_CODE_TTL_SECONDS = 300
TOKEN_PREFIX = "tm_plugin_"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash_token(raw: str) -> str:
    import hashlib

    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class PluginTokenRecord:
    def __init__(self, *, id: str, token_hash: str, scope: str,
                 issued_at: datetime, last_used_at: datetime | None = None,
                 revoked: bool = False, expires_at: datetime | None = None) -> None:
        self.id = id
        self.token_hash = token_hash
        self.scope = scope
        self.issued_at = issued_at
        self.last_used_at = last_used_at
        self.revoked = revoked
        self.expires_at = expires_at

    def to_meta(self) -> dict:
        return {
            "id": self.id,
            "scope": self.scope,
            "issued_at": self.issued_at.isoformat(),
            "last_used_at": self.last_used_at.isoformat() if self.last_used_at else None,
            "revoked": self.revoked,
        }

    def is_valid(self) -> bool:
        if self.revoked:
            return False
        if self.expires_at is not None and self.expires_at <= _now():
            return False
        return True


class PluginTokenStore:
    """基于 data_dir/plugin 的文件令牌存储。"""

    def __init__(self, data_dir: Path) -> None:
        self._root = Path(data_dir)
        self._tokens_path = self._root / "access_tokens.json"
        self._codes_path = self._root / "pairing_codes.json"
        self._lock = threading.Lock()

    # --- 内部读写（必须在锁内调用）---
    def _read_json(self, path: Path) -> dict:
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8") or "{}")
        except (json.JSONDecodeError, OSError):
            logger.warning("插件令牌文件读取失败，按空处理: %s", path)
            return {}

    def _write_json(self, path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        # 原子替换，避免半写文件
        import os

        os.replace(tmp, path)

    def _records(self) -> dict[str, PluginTokenRecord]:
        raw = self._read_json(self._tokens_path)
        out: dict[str, PluginTokenRecord] = {}
        for rid, item in (raw.get("tokens") or {}).items():
            out[rid] = PluginTokenRecord(
                id=rid,
                token_hash=item["token_hash"],
                scope=item.get("scope", PLUGIN_SCOPE),
                issued_at=datetime.fromisoformat(item["issued_at"]),
                last_used_at=datetime.fromisoformat(item["last_used_at"]) if item.get("last_used_at") else None,
                revoked=bool(item.get("revoked", False)),
                expires_at=datetime.fromisoformat(item["expires_at"]) if item.get("expires_at") else None,
            )
        return out

    def _codes(self) -> dict[str, dict]:
        return self._read_json(self._codes_path).get("codes") or {}

    # --- 配对 ---
    def create_pairing_code(self) -> str:
        code = token_urlsafe(16)
        expires_at = _now() + timedelta(seconds=PAIRING_CODE_TTL_SECONDS)
        with self._lock:
            data = self._read_json(self._codes_path)
            codes = data.get("codes") or {}
            codes[code] = {"issued_at": _now().isoformat(), "expires_at": expires_at.isoformat()}
            data["codes"] = codes
            self._write_json(self._codes_path, data)
        return code

    def exchange_pairing_code(self, code: str) -> dict | None:
        """用一次性配对码交换 teaching.read 令牌；失败返回 None。"""
        with self._lock:
            data = self._read_json(self._codes_path)
            codes = data.get("codes") or {}
            entry = codes.get(code)
            if not entry:
                return None
            expires = datetime.fromisoformat(entry["expires_at"]) if entry.get("expires_at") else None
            if expires is not None and expires <= _now():
                codes.pop(code, None)
                data["codes"] = codes
                self._write_json(self._codes_path, data)
                return None
            # 一次性消费
            codes.pop(code, None)
            data["codes"] = codes
            self._write_json(self._codes_path, data)

            raw = TOKEN_PREFIX + token_urlsafe(32)
            rid = uuid.uuid4().hex
            issued_at = _now()
            tokens = self._read_json(self._tokens_path).get("tokens") or {}
            tokens[rid] = {
                "token_hash": _hash_token(raw),
                "scope": PLUGIN_SCOPE,
                "issued_at": issued_at.isoformat(),
                "last_used_at": None,
                "revoked": False,
                "expires_at": None,
            }
            self._write_json(self._tokens_path, {"tokens": tokens})
        return {"token": raw, "scope": PLUGIN_SCOPE, "issued_at": issued_at, "expires_at": None}

    def issue_access_token(self) -> dict:
        """由已认证的 TeachMate 页面直接创建只读插件令牌。

        这是配对流程的无交互版本：教师已经通过本地页面认证，因此不再要求
        手工复制浏览器令牌或执行命令。服务端仍只保存摘要，调用方拿到的令牌
        仅具备 teaching.read 权限，并可在设置页撤销。
        """
        raw = TOKEN_PREFIX + token_urlsafe(32)
        rid = uuid.uuid4().hex
        issued_at = _now()
        with self._lock:
            tokens = self._read_json(self._tokens_path).get("tokens") or {}
            tokens[rid] = {
                "token_hash": _hash_token(raw),
                "scope": PLUGIN_SCOPE,
                "issued_at": issued_at.isoformat(),
                "last_used_at": None,
                "revoked": False,
                "expires_at": None,
            }
            self._write_json(self._tokens_path, {"tokens": tokens})
        return {"token": raw, "scope": PLUGIN_SCOPE,
                "issued_at": issued_at, "expires_at": None}

    # --- 校验 ---
    def verify_token(self, raw: str) -> PluginTokenRecord | None:
        if not raw or not raw.startswith(TOKEN_PREFIX):
            return None
        h = _hash_token(raw)
        with self._lock:
            for rec in self._records().values():
                if compare_digest(rec.token_hash, h):
                    return rec if rec.is_valid() else None
        return None

    def record_use(self, token_id: str) -> None:
        with self._lock:
            data = self._read_json(self._tokens_path)
            tokens = data.get("tokens") or {}
            item = tokens.get(token_id)
            if not item:
                return
            item["last_used_at"] = _now().isoformat()
            self._write_json(self._tokens_path, {"tokens": tokens})

    # --- 撤销与列举 ---
    def revoke_token(self, token_id: str) -> bool:
        with self._lock:
            data = self._read_json(self._tokens_path)
            tokens = data.get("tokens") or {}
            item = tokens.get(token_id)
            if not item:
                return False
            item["revoked"] = True
            self._write_json(self._tokens_path, {"tokens": tokens})
        return True

    def list_tokens(self) -> list[dict]:
        with self._lock:
            return [rec.to_meta() for rec in self._records().values()]
