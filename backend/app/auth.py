import os
from secrets import token_urlsafe

from fastapi import Header, HTTPException, status

TOKEN = os.getenv("WORKBENCH_TOKEN") or token_urlsafe(32)


def require_token(authorization: str | None = Header(default=None)) -> None:
    if authorization != f"Bearer {TOKEN}":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效的本地访问令牌")
