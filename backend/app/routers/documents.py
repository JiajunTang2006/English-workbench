"""L4 文档导出 API 路由（方案 §11）。

端点：
- POST /api/v1/documents/export   提交导出任务（local 同步完成，remote 预留）
- GET  /api/v1/documents/jobs/{id} 查询任务状态与产物元数据
- GET  /api/v1/documents/download/{artifact_id} 下载产物（防穿越 + 结构复验）

门禁：
- 要求 document_export_enabled 开关（直接读原始开关，绕过 agent 总闸，与 L1/L2/L3 一致）；
- provider.type=remote 仅在 remote_document_provider_enabled 同时开启时接受，否则 409；
- 内容只来自已确认结构化答案，不渲染未确认模型输出。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..agent.config import get_agent_config
from ..auth import require_token
from ..models.entities import GeneratedArtifact
from ..schemas.document_export import DocumentSpec
from ..services.attachment_security import safe_storage_path
from ..services.document_export import DocumentExportService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/documents", tags=["documents"],
                   dependencies=[Depends(require_token)])


def require_document_export_enabled(request: Request):
    enabled = get_agent_config().feature_flags.get("document_export_enabled", False)
    if not enabled:
        raise HTTPException(
            status_code=503,
            detail={"code": "document_export_disabled",
                    "message": "文档导出功能未启用（document_export_enabled=false）"},
        )


def get_session(request: Request):
    db = request.app.state.session_factory()
    try:
        yield db
    finally:
        db.close()


@router.post("/export", dependencies=[Depends(require_document_export_enabled)])
async def export_document(req: DocumentSpec, request: Request, db: Session = Depends(get_session)):
    # 远端 Provider fail-closed：依赖 remote_document_provider_enabled
    if req.provider.type == "remote":
        remote_ok = get_agent_config().feature_flags.get(
            "remote_document_provider_enabled", False
        )
        if not remote_ok:
            raise HTTPException(
                status_code=409,
                detail={"code": "remote_provider_not_enabled",
                        "message": "远端文档 Provider 未启用（需同时开启 document_export_enabled 与 remote_document_provider_enabled）"},
            )
        # L4-C 远端 Provider 不在本轮实现：明确提示而非静默失败
        raise HTTPException(
            status_code=501,
            detail={"code": "remote_provider_not_implemented",
                    "message": "远端文档 Provider（L4-C）尚未实现，当前仅支持本地导出"},
        )

    svc = DocumentExportService(db, data_dir=request.app.state.settings.data_dir)
    spec = req.model_dump()
    try:
        result = await svc.export_document(
            spec=spec,
            template_version="1.0",
            session_id=req.source.session_ids[0] if req.source.session_ids else None,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("L4 文档导出异常")
        raise HTTPException(status_code=500, detail={"code": "export_failed",
                                                     "message": str(e)[:300]})
    return {"code": "ok", **result}


@router.get("/jobs/{job_id}")
def get_job(job_id: int, request: Request, db: Session = Depends(get_session)):
    svc = DocumentExportService(db, data_dir=request.app.state.settings.data_dir)
    result = svc.get_job(job_id)
    if result is None:
        raise HTTPException(status_code=404, detail={"code": "not_found",
                                                     "message": "导出任务不存在"})
    return {"code": "ok", **result}


@router.get("/download/{artifact_id}")
def download_artifact(artifact_id: int, request: Request, db: Session = Depends(get_session)):
    artifact = db.get(GeneratedArtifact, artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail={"code": "not_found",
                                                     "message": "产物不存在"})

    try:
        path = safe_storage_path(request.app.state.settings.exports_dir, artifact.storage_name)
    except ValueError:
        raise HTTPException(status_code=400, detail={"code": "invalid_path",
                                                     "message": "产物路径无效"})

    if not path.exists():
        raise HTTPException(status_code=404, detail={"code": "file_missing",
                                                     "message": "产物文件缺失"})

    # 下载后安全复验（方案 §11.6）：DOCX 必须是合法 OOXML 包
    if artifact.mime_type.endswith("wordprocessingml.document"):
        from ..services.attachment_security import _validate_office_zip_path
        try:
            _validate_office_zip_path(
                path,
                required_members={
                    "[Content_Types].xml", "word/document.xml",
                    "_rels/.rels", "word/_rels/document.xml.rels",
                },
                label="Word 文档",
            )
        except ValueError as e:
            raise HTTPException(status_code=422, detail={"code": "artifact_corrupted",
                                                         "message": f"产物结构校验失败：{e}"})

    return FileResponse(
        path,
        media_type=artifact.mime_type,
        filename=artifact.storage_name,
        headers={"Content-SHA256": artifact.sha256},
    )
