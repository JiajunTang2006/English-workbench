"""视觉分析 API 路由（L3，TeachMatePluginAPI 之外的独立前缀 /api/v1/vision）。

端点：
- POST /analyze            触发视觉分析（状态机 queued→running→pending_review）
- GET  /results            列出某附件的分析结果
- GET  /results/{id}       单结果（原图引用+识别文本+低置信区，不返回图像 Base64）
- PATCH /results/{id}      教师确认/拒绝（confirm/reject），可附修正
- GET  /privacy-preview    返回隐私遮挡策略说明（教师可预览将发送内容）

门禁：
- 要求 vision_analysis_enabled 开关 + require_token；
- 学期归属复核（附件必须属于当前 term）；
- 教师确认前结果不进入正式上下文（由 analysis_service 保证）。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..agent.config import get_agent_config
from ..auth import require_token
from ..models.entities import Attachment, AttachmentAnalysisResult, Term
from ..services.vision.analysis_service import VisionAnalysisService
from ..services.vision.router import route as modality_route
from ..services.vision.provider import VisionTask

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/vision", dependencies=[Depends(require_token)])


def require_vision_enabled(request: Request):
    enabled = get_agent_config().feature_flags.get("vision_analysis_enabled", False)
    if not enabled:
        raise HTTPException(
            status_code=503,
            detail={"code": "vision_disabled",
                    "message": "视觉分析功能未启用（vision_analysis_enabled=false）"},
        )


def get_session(request: Request):
    db = request.app.state.session_factory()
    try:
        yield db
    finally:
        db.close()


def _require_attachment_in_term(db: Session, attachment_id: int, term_id: int) -> Attachment:
    att = db.get(Attachment, attachment_id)
    if att is None or att.term_id != term_id:
        raise HTTPException(
            status_code=404,
            detail={"code": "not_found", "message": "附件不存在或不属于当前学期"},
        )
    return att


class AnalyzeRequest(BaseModel):
    attachment_id: int
    task: VisionTask = "exam_understanding"
    result_type: str = "vision"


class ReviewRequest(BaseModel):
    action: str  # confirm / reject
    correction: dict | None = None


@router.post("/analyze", dependencies=[Depends(require_vision_enabled)])
async def analyze(req: AnalyzeRequest, request: Request, db: Session = Depends(get_session)):
    term_id = _current_term_id(db)
    att = _require_attachment_in_term(db, req.attachment_id, term_id)
    # 多模态路由：未开启视觉/无 Provider 时 fail-closed
    decision = modality_route(
        original_name=att.original_name, task=req.task,
        vision_enabled=True,
    )
    if decision.reason.endswith("fail_closed"):
        raise HTTPException(
            status_code=503,
            detail={"code": "vision_unavailable",
                    "message": "未配置可用视觉 Provider，无法执行视觉分析"},
        )
    svc = VisionAnalysisService(db, data_dir=request.app.state.settings.data_dir)
    result = await svc.analyze_attachment(
        attachment_id=req.attachment_id, task=req.task,
        result_type=req.result_type, provider_name=decision.provider_name,
    )
    return {"code": "ok", "result": result}


@router.get("/results")
def list_results(attachment_id: int, request: Request, db: Session = Depends(get_session)):
    term_id = _current_term_id(db)
    _require_attachment_in_term(db, attachment_id, term_id)
    svc = VisionAnalysisService(db)
    return {"code": "ok", "results": svc.list_results(attachment_id)}


@router.get("/results/{result_id}")
def get_result(result_id: int, request: Request, db: Session = Depends(get_session)):
    svc = VisionAnalysisService(db)
    r = svc.get_result(result_id)
    if r is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "结果不存在"})
    # 学期归属复核：结果所属附件必须属于当前 term
    att = db.get(Attachment, r["attachment_id"])
    if att is None or att.term_id != _current_term_id(db):
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "越权访问"})
    return {"code": "ok", "result": r}


@router.patch("/results/{result_id}", dependencies=[Depends(require_vision_enabled)])
def review(result_id: int, req: ReviewRequest, request: Request, db: Session = Depends(get_session)):
    svc = VisionAnalysisService(db)
    r = svc.get_result(result_id)
    if r is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "结果不存在"})
    att = db.get(Attachment, r["attachment_id"])
    if att is None or att.term_id != _current_term_id(db):
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "越权访问"})
    try:
        updated = svc.teacher_review(result_id=result_id, action=req.action,
                                     correction=req.correction)
    except ValueError as e:
        raise HTTPException(status_code=400, detail={"code": "bad_request",
                                                    "message": str(e)})
    return {"code": "ok", "result": updated}


@router.get("/privacy-preview")
def privacy_preview():
    """教师可预览的隐私策略（不返回图像，仅说明遮挡规则）。"""
    return {
        "code": "ok",
        "policy": {
            "redacted_fields": ["姓名", "学号", "学校", "二维码", "联系方式"],
            "method": "本地矩形遮挡，图像不离开本机",
            "pii_detection": "依赖视觉 Provider 返回 bbox；兜底规则仅做审计标记",
            "logs": "不记录图像 Base64 / OCR 原文 / 完整 Provider 响应",
        },
    }


def _current_term_id(db: Session) -> int:
    from ..services.terms import current_term_id

    tid = current_term_id(db)
    if tid is None:
        raise HTTPException(status_code=400, detail={"code": "no_active_term",
                                                    "message": "无活跃学期"})
    return tid
