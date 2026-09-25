"""内置 Token 用量面板 API。"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..auth import require_token
from ..services.agent_analysis.usage import UsageService

router = APIRouter(prefix="/api/v1/token-usage", tags=["token-usage"], dependencies=[Depends(require_token)])


@router.get("")
def get_token_usage(request: Request, range: str = Query("today", alias="range")):
    if range not in {"today", "7d", "30d", "all"}:
        raise HTTPException(status_code=422, detail="range must be today, 7d, 30d or all")
    with request.app.state.session_factory() as session:
        return UsageService(session).dashboard(range)
