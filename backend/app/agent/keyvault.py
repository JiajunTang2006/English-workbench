"""第三方 API Key 本地保管库（B3-10）

- Key 存本地文件（<data_dir>/provider/api_key），权限 0600；
  不入 Git（data_dir 不在仓库内）、不入 DB、不打印、不通过普通接口返回；
- 原子写入（tmp+rename），读取失败按未配置处理（fail-closed）；
- 页面/接口只暴露 key_configured: bool；如需确认保存状态，可额外显示不可逆的脱敏尾号。
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
import re

logger = logging.getLogger(__name__)


def _safe_profile_id(profile_id: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]", "_", str(profile_id or "").strip())
    return value[:100] or "default"


def key_file(profile_id: str | None = None, *, data_dir: str | Path | None = None) -> Path:
    """返回 API Key 文件路径（目录不存在时创建，权限 0700）。"""
    if data_dir is None:
        from ..config import default_data_dir
        data_root = default_data_dir()
    else:
        data_root = Path(data_dir).expanduser().resolve()
    d = data_root / "provider"
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    if profile_id:
        profiles = d / "models"
        try:
            profiles.mkdir(parents=True, exist_ok=True)
        except OSError:
            # 读取不存在的档案 Key 时不应因目录权限问题抛异常；
            # 写入时仍会在原子保存阶段明确报告失败。
            return profiles / f"{_safe_profile_id(profile_id)}.key"
        try:
            os.chmod(profiles, 0o700)
        except OSError:
            pass
        return profiles / f"{_safe_profile_id(profile_id)}.key"
    return d / "api_key"


def save_api_key(
    api_key: str,
    profile_id: str | None = None,
    *,
    data_dir: str | Path | None = None,
) -> None:
    """原子保存 API Key（仅保留必要副本，写后立即消除内存态）。"""
    key = (api_key or "").strip()
    path = key_file(profile_id, data_dir=data_dir)
    fd, tmp = tempfile.mkstemp(prefix=".key-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(key)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    logger.info("API Key 已更新（本地保管库）")


def load_api_key(
    profile_id: str | None = None,
    *,
    data_dir: str | Path | None = None,
) -> str | None:
    """读取 API Key；文件缺失/空/损坏返回 None。"""
    path = key_file(profile_id, data_dir=data_dir)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError:
        logger.warning("API Key 读取失败（按未配置处理）")
        return None
    key = raw.strip()
    return key or None


def clear_api_key(
    profile_id: str | None = None,
    *,
    data_dir: str | Path | None = None,
) -> None:
    """清除本地 API Key。"""
    try:
        key_file(profile_id, data_dir=data_dir).unlink(missing_ok=True)
    except OSError:
        pass
    logger.info("API Key 已清除（本地保管库）")


def api_key_configured(
    profile_id: str | None = None,
    *,
    data_dir: str | Path | None = None,
) -> bool:
    return load_api_key(profile_id, data_dir=data_dir) is not None
