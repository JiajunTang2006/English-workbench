"""学校成绩与学生名册同步 API。"""

from __future__ import annotations

from datetime import datetime
import json
import logging
from urllib.error import HTTPError, URLError

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import require_token
from ..agent.keyvault import api_key_configured, load_api_key, save_api_key
from ..models import BackgroundJob, SchoolDataSource, SchoolSyncRun
from ..schemas.school_sync import (
    SchoolDataSourceCreate, SchoolDataSourceWrite, SchoolSyncPayload,
    StudentRosterPayload, SyncApplyResponse, SyncPreviewResponse, MoniMcpConfig,
)
from ..services.school_sync import apply_payload, apply_student_roster, mock_payload, preview_payload
from ..services.job_worker import submit_job
from ..services.moni_sync import sync_current_term
from ..services.plugin_manager import PluginManager, PluginValidationError
from ..services.school_sources import (
    GenericSourceError,
    SourceConfigError,
    delete_source,
    ensure_moni_source,
    get_source_row,
    key_hint,
    list_source_rows,
    resolve_source,
    save_source_config,
    source_status,
)
from urllib.parse import urlparse
from .agent import _job_status_payload

router = APIRouter(prefix="/api/v1/school-sync", dependencies=[Depends(require_token)])
log = logging.getLogger(__name__)


def get_session(request: Request) -> Session:
    return request.app.state.session_factory()


def _clean_error_detail(exc: Exception) -> str:
    """把上游返回的错误压成一行有限长度的摘要，不带原始数据或令牌。"""
    detail = str(exc).strip()
    if detail.startswith("{"):
        try:
            payload = json.loads(detail)
            detail = str(payload.get("message") or payload.get("detail") or payload.get("error") or detail)
        except (TypeError, ValueError):
            pass
    return " ".join(detail.split())[:240]


def _require_source(session: Session, source_key: str) -> SchoolDataSource:
    row = get_source_row(session, str(source_key or "").strip().lower())
    if row is None:
        raise HTTPException(404, "数据源不存在")
    return row


def _moni_server(payload: MoniMcpConfig) -> dict:
    server = payload.mcpServers.get("moni")
    if not isinstance(server, dict):
        raise HTTPException(422, "配置中必须包含 mcpServers.moni")
    if str(server.get("type", "http")).lower() != "http":
        raise HTTPException(422, "MONI 目前只支持 type=http")
    url = str(server.get("url", "")).strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(422, "MONI 地址必须是有效的 http/https URL")
    headers = server.get("headers") or {}
    if not isinstance(headers, dict):
        raise HTTPException(422, "headers 必须是对象")
    return {"url": url, "headers": {str(key): str(value) for key, value in headers.items()}}


def _moni_key_hint(data_dir) -> str | None:
    # 与通用数据源共用同一套遮蔽口径（MONI 的档案名历史上就是 "moni"）。
    key = load_api_key("moni", data_dir=data_dir) or ""
    if not key:
        return None
    return "••••••••" + key[-4:] if len(key) >= 4 else "••••••••"


def _safe_header_display(name: str, value: str) -> str:
    """返回配置页面可展示的凭证头，不回显原始密钥。"""
    lowered = name.lower()
    if any(term in lowered for term in ("authorization", "api-key", "apikey", "token", "secret", "password", "credential", "key")):
        return "********"
    return value


@router.get("/moni/config")
def get_moni_config(request: Request):
    data_dir = request.app.state.settings.data_dir
    manager = PluginManager(request.app.state.settings.data_dir)
    plugin = manager.get_plugin("moni") or {}
    runtime = plugin.get("runtime") or {}
    headers = {
        str(name): _safe_header_display(str(name), str(value))
        for name, value in (runtime.get("headers") or {}).items()
    }
    if api_key_configured("moni", data_dir=data_dir):
        headers["Authorization"] = "Bearer ********"
    return {
        "mcpServers": {"moni": {"type": "http", "url": runtime.get("url", ""), "headers": headers}},
        "configured": api_key_configured("moni", data_dir=data_dir),
        "key_hint": _moni_key_hint(data_dir),
        "health": {"health": "configured" if plugin else "missing"},
    }


@router.put("/moni/config")
def put_moni_config(payload: MoniMcpConfig, request: Request):
    data_dir = request.app.state.settings.data_dir
    server = _moni_server(payload)
    headers = dict(server["headers"])
    authorization = str(headers.pop("Authorization", headers.pop("authorization", ""))).strip()
    if authorization and authorization.lower().startswith("bearer "):
        try:
            save_api_key(authorization[7:].strip(), "moni", data_dir=data_dir)
        except OSError as exc:
            raise HTTPException(500, "本机 API Key 保存失败，请重启 WorkBench 后再试") from exc
    elif authorization and authorization != "Bearer ********":
        raise HTTPException(422, "Authorization 必须使用 Bearer 令牌")
    runtime = {
        "transport": "http",
        "url": server["url"],
        "headers": headers,
        "bearer_token_profile": "moni",
        "require_bearer_token": True,
    }
    manager = PluginManager(request.app.state.settings.data_dir)
    manager.configure_runtime("moni", runtime)
    return {"status": "saved", "url": server["url"], "token_configured": api_key_configured("moni", data_dir=data_dir), "key_hint": _moni_key_hint(data_dir)}


@router.post("/moni/test")
def test_moni_config(request: Request):
    data_dir = request.app.state.settings.data_dir
    manager = PluginManager(request.app.state.settings.data_dir)
    try:
        result = manager.health_check("moni")
    except (KeyError, PluginValidationError) as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"status": "ok" if result.get("health") == "ready" else "failed", "health": result.get("health"), "tool_count": len(result.get("tools") or []), "error": result.get("error"), "key_hint": _moni_key_hint(data_dir)}


@router.post("/moni/sync")
def sync_moni_now(request: Request):
    with get_session(request) as session:
        from ..services.subjects import get_selected_subject
        subject = get_selected_subject(session)
        if subject.key != "english":
            raise HTTPException(
                409,
                f"MONI 当前同步器按英语单科字段读取；当前学科为{subject.label}。"
                "请切回英语同步，或配置对应学科的通用数据源。",
            )
    try:
        summary = sync_current_term(request.app.state.settings)
    except HTTPError as exc:
        if exc.code == 401:
            detail = "MONI 令牌未通过验证（HTTP 401），请检查 API Key"
        else:
            detail = f"MONI 服务返回 HTTP {exc.code}"
        raise HTTPException(502, detail) from exc
    except URLError as exc:
        raise HTTPException(502, "MONI 服务暂时无法连接，请稍后重试") from exc
    except Exception as exc:
        log.exception("MONI 同步失败")
        detail = _clean_error_detail(exc)
        suffix = f"：{detail}" if detail else ""
        raise HTTPException(502, f"MONI同步失败（{type(exc).__name__}）{suffix}") from exc
    return {"status": "completed", "summary": summary}


# ---- 通用数据源：内置 MONI 与自定义 MCP 走同一组端点 ----
#
# 换学校只是换一份配置（端点 + 鉴权 + 路径模板 + 字段映射），不需要新增代码或
# 插件包；自定义数据源会被「物化」成插件清单，从而复用既有的 MCP 宿主。
# ``/moni/*`` 保留为兼容别名，行为不变。


@router.get("/sources")
def list_sources(request: Request):
    """列出所有数据源；配置回显已遮蔽凭证，令牌只给「是否已配置 + 尾号」。"""
    settings = request.app.state.settings
    with get_session(request) as session:
        ensure_moni_source(session)
        session.commit()
        return [source_status(settings, row) for row in list_source_rows(session)]


@router.post("/sources", status_code=201)
def create_source(payload: SchoolDataSourceCreate, request: Request):
    settings = request.app.state.settings
    with get_session(request) as session:
        if get_source_row(session, str(payload.source_key).strip().lower()) is not None:
            raise HTTPException(409, "数据源标识已存在")
        try:
            row = save_source_config(
                settings, session, source_key=payload.source_key, name=payload.name,
                config=payload.config, bearer_token=payload.bearer_token,
                enabled=payload.enabled,
            )
        except SourceConfigError as exc:
            session.rollback()
            raise HTTPException(422, str(exc)) from exc
        session.commit()
        session.refresh(row)
        return source_status(settings, row)


@router.get("/sources/{source_key}/config")
def get_source_config(source_key: str, request: Request):
    settings = request.app.state.settings
    with get_session(request) as session:
        return source_status(settings, _require_source(session, source_key))


@router.put("/sources/{source_key}/config")
def put_source_config(source_key: str, payload: SchoolDataSourceWrite, request: Request):
    settings = request.app.state.settings
    with get_session(request) as session:
        _require_source(session, source_key)
        try:
            row = save_source_config(
                settings, session, source_key=source_key, name=payload.name,
                config=payload.config, bearer_token=payload.bearer_token,
                enabled=payload.enabled,
            )
        except SourceConfigError as exc:
            session.rollback()
            raise HTTPException(422, str(exc)) from exc
        session.commit()
        session.refresh(row)
        return source_status(settings, row)


@router.delete("/sources/{source_key}")
def delete_source_config(source_key: str, request: Request):
    """删除自定义数据源：登记行、物化插件与专属令牌一起清掉。"""
    settings = request.app.state.settings
    with get_session(request) as session:
        try:
            delete_source(settings, session, source_key=source_key)
        except SourceConfigError as exc:
            raise HTTPException(422, str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(404, "数据源不存在") from exc
        session.commit()
    return {"status": "deleted", "source_key": str(source_key).strip().lower()}


@router.post("/sources/{source_key}/test")
def test_source(source_key: str, request: Request):
    settings = request.app.state.settings
    with get_session(request) as session:
        try:
            source = resolve_source(settings, session, str(source_key or "").strip().lower())
        except KeyError as exc:
            raise HTTPException(404, "数据源不存在") from exc
        except SourceConfigError as exc:
            raise HTTPException(422, str(exc)) from exc
        try:
            health = source.health()
        except Exception as exc:  # noqa: BLE001 - 上游异常统一转成 502
            log.exception("数据源健康检查失败")
            raise HTTPException(502, f"数据源连接失败（{type(exc).__name__}）") from exc
        return {
            "status": "ok" if health.get("health") == "ready" else "failed",
            "health": health.get("health"),
            "tool_count": health.get("tool_count", 0),
            "error": health.get("error"),
            "key_hint": key_hint(settings, str(source_key or "").strip().lower()),
        }


@router.post("/sources/{source_key}/sync")
def sync_source(source_key: str, request: Request, dry_run: bool = False):
    """立即同步一次；``dry_run=true`` 只做取数与校验，不写库。"""
    settings = request.app.state.settings
    with get_session(request) as session:
        if str(source_key or "").strip().lower() == "moni":
            from ..services.subjects import get_selected_subject
            subject = get_selected_subject(session)
            if subject.key != "english":
                raise HTTPException(
                    409,
                    f"内置 MONI 同步器读取英语单科数据；当前学科为{subject.label}。"
                    "请使用对应学科的数据源。",
                )
        try:
            source = resolve_source(settings, session, str(source_key or "").strip().lower())
        except KeyError as exc:
            raise HTTPException(404, "数据源不存在") from exc
        except SourceConfigError as exc:
            raise HTTPException(422, str(exc)) from exc
    try:
        summary = source.sync(dry_run=dry_run)
    except GenericSourceError as exc:
        raise HTTPException(502, f"同步失败：{exc}") from exc
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - 上游异常统一转成 502
        log.exception("数据源同步失败")
        detail = _clean_error_detail(exc)
        suffix = f"：{detail}" if detail else ""
        raise HTTPException(502, f"同步失败（{type(exc).__name__}）{suffix}") from exc
    return {"status": "completed", "summary": summary}


@router.get("/mock/payload", response_model=SchoolSyncPayload)
def get_mock_payload():
    return mock_payload()


@router.post("/preview", response_model=SyncPreviewResponse)
def preview(payload: SchoolSyncPayload):
    return preview_payload(payload)


@router.post("/apply", response_model=SyncApplyResponse)
def apply(payload: SchoolSyncPayload, request: Request):
    with get_session(request) as session:
        try:
            run, summary = apply_payload(session, payload)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return SyncApplyResponse(
            source_key=payload.source_key,
            snapshot_id=payload.snapshot_id,
            counts={key: int(value) for key, value in summary.items() if isinstance(value, int)},
            warnings=list(summary.get("warnings") or []),
            errors=[], run_id=run.id, status=run.status,
        )


@router.post("/roster/preview")
def preview_roster(payload: StudentRosterPayload):
    return {
        "source_key": payload.source_key,
        "snapshot_id": payload.snapshot_id,
        "counts": {"classes": len(payload.classes), "students": len(payload.students)},
        "warnings": [],
        "errors": [],
    }


@router.post("/roster/apply")
def apply_roster(payload: StudentRosterPayload, request: Request):
    with get_session(request) as session:
        try:
            summary = apply_student_roster(session, payload)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {
            "source_key": payload.source_key,
            "snapshot_id": payload.snapshot_id,
            "status": "completed",
            "counts": summary,
        }


@router.post("/mock/jobs", status_code=202)
def queue_mock_sync(request: Request):
    """提交一次后台 mock 同步，验证后续接自动化调度的任务形态。"""
    payload = mock_payload()
    job_id = submit_job(
        request.app.state.session_factory,
        "school_sync",
        {"payload": payload.model_dump(mode="json")},
        idempotency_key=f"school-sync:{payload.source_key}:{payload.snapshot_id}",
    )
    return {"job_id": job_id, "status": "queued", "source_key": payload.source_key, "snapshot_id": payload.snapshot_id}


@router.post("/jobs", status_code=202)
def queue_sync(payload: SchoolSyncPayload, request: Request):
    """提交学校同步后台任务；真实 MCP/API 适配器复用同一入口。"""
    job_id = submit_job(
        request.app.state.session_factory,
        "school_sync",
        {"payload": payload.model_dump(mode="json")},
        idempotency_key=f"school-sync:{payload.source_key}:{payload.snapshot_id}",
    )
    return {
        "job_id": job_id,
        "status": "queued",
        "source_key": payload.source_key,
        "snapshot_id": payload.snapshot_id,
        "message": "已提交后台同步，页面可以安全离开。",
    }


@router.get("/jobs/{job_id}")
def get_sync_job(job_id: int, request: Request):
    with get_session(request) as session:
        job = session.get(BackgroundJob, job_id)
        if job is None or job.job_type != "school_sync":
            raise HTTPException(404, "同步任务不存在")
        return _job_status_payload(job)


@router.post("/jobs/{job_id}/retry")
def retry_sync_job(job_id: int, request: Request):
    with get_session(request) as session:
        job = session.get(BackgroundJob, job_id)
        if job is None or job.job_type != "school_sync":
            raise HTTPException(404, "同步任务不存在")
        worker = getattr(request.app.state, "job_worker", None)
        if worker is None or not worker.retry_job(job_id):
            raise HTTPException(409, "当前同步任务不可重试")
        session.expire(job)
        return _job_status_payload(session.get(BackgroundJob, job_id))


@router.get("/status")
def sync_status(request: Request):
    """返回当前同步任务和最近一次成功同步时间，供设置页首屏恢复状态。"""
    with get_session(request) as session:
        active = session.scalar(
            select(BackgroundJob)
            .where(BackgroundJob.job_type == "school_sync", BackgroundJob.status.in_(["queued", "running", "waiting_confirmation"]))
            .order_by(BackgroundJob.id.desc())
            .limit(1)
        )
        latest_success = session.scalar(
            select(SchoolSyncRun)
            .where(SchoolSyncRun.status == "completed")
            .order_by(SchoolSyncRun.completed_at.desc(), SchoolSyncRun.id.desc())
            .limit(1)
        )
        return {
            "active_job": _job_status_payload(active) if active else None,
            "last_success_at": latest_success.completed_at if latest_success else None,
            "last_success_run_id": latest_success.id if latest_success else None,
            "last_success_summary": latest_success.summary_json if latest_success else None,
        }


@router.get("/runs")
def list_runs(request: Request, limit: int = 20):
    limit = max(1, min(int(limit), 100))
    with get_session(request) as session:
        rows = session.scalars(select(SchoolSyncRun).order_by(SchoolSyncRun.id.desc()).limit(limit)).all()
        return [
            {"id": row.id, "source_id": row.source_id, "snapshot_id": row.snapshot_id, "status": row.status, "summary": row.summary_json, "error": row.error_message, "created_at": row.created_at, "completed_at": row.completed_at}
            for row in rows
        ]
