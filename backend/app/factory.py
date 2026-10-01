from datetime import datetime, timezone
from secrets import compare_digest
import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from sqlalchemy import select, update

from .auth import TOKEN
from .config import get_settings
from .database import create_session_factory, migration_needed, run_migrations, set_global_session_factory
from .models import WorkspaceState
from .routers import router
from .services.backups import create_backup
from .services.file_operations import process_pending_file_operations
from .services.legacy_attachments import migrate_legacy_documents
from .runtime_session import BrowserSessionLifecycle
from .version import APP_VERSION
from .services.plugin_manager import PluginManager


def _project_root() -> Path:
    """Return the directory containing bundled HTML/assets in source or frozen builds."""
    frozen_root = getattr(sys, "_MEIPASS", None)
    return Path(frozen_root) if frozen_root else Path(__file__).resolve().parents[2]


def create_app(settings=None) -> FastAPI:
    settings = settings or get_settings()
    settings.ensure_directories()
    database_path = settings.data_dir / "workbench.db"
    if database_path.is_file() and migration_needed(settings.database_url):
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        create_backup(
            database_path,
            settings.backups_dir / f"pre_migration_{timestamp}",
            kind="pre_migration",
            attachments_dir=settings.attachments_dir,
        )
    run_migrations(settings.database_url)
    session_factory = create_session_factory(settings.database_url)
    set_global_session_factory(session_factory)
    with session_factory() as migration_session:
        has_legacy_documents = any(
            document.get("content") and not document.get("attachmentId")
            for workspace in migration_session.scalars(select(WorkspaceState)).all()
            for document in (workspace.state_json or {}).get("paperDocuments", [])
        )
        if has_legacy_documents:
            migrate_legacy_documents(migration_session, settings)
        process_pending_file_operations(migration_session)

    # --- 启动恢复：唯一的正式恢复服务 (agent_runs.recovery, B2-05) ---
    # 恢复失败时保留原状态并记录脱敏诊断；不使用任何语义相反的旧实现。
    with session_factory() as recovery_session:
        try:
            from .services.agent_runs.recovery import run_full_recovery
            report = run_full_recovery(recovery_session)
            if report.errors:
                logging.getLogger(__name__).warning(
                    "启动恢复：遇到 %d 个错误（详情见诊断日志，不打印业务数据）",
                    len(report.errors),
                )
        except Exception as exc:
            logging.getLogger(__name__).error(
                "启动恢复失败（%s）：保留原始状态，已回滚本次恢复改动，"
                "不调用语义相反的旧实现",
                type(exc).__name__,
            )
            recovery_session.rollback()

    # --- P0-2: 从 DB 恢复运行时配置覆盖 ---
    try:
        from .agent.config import restore_runtime_config_from_db
        if restore_runtime_config_from_db():
            logging.getLogger(__name__).info(
                "启动恢复：已从数据库恢复 Agent 运行时配置覆盖"
            )
    except Exception:
        logging.getLogger(__name__).warning(
            "启动恢复：恢复 Agent 运行时配置失败，将使用环境变量默认值",
            exc_info=True,
        )

    # --- lifespan：启动后台 Worker，shutdown 时优雅停止；harness 常驻运行时同生命周期 ---
    from .services.job_worker import JobWorker, set_global_worker
    from .services.job_handlers import register_all_handlers
    from .agent.runtime.harness_runtime import (
        attach_event_projection,
        is_harness_mode,
        setup_harness_manager,
    )
    from .agent.config import get_agent_config

    worker = JobWorker(session_factory)
    register_all_handlers(worker)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # B3-06：捕获主事件循环，供 Harness 工作线程安全广播事件
        from .agent.runtime.event_projection import set_main_event_loop
        import asyncio as _asyncio
        set_main_event_loop(_asyncio.get_running_loop())

        harness_manager = None
        if is_harness_mode():
            # B2-01：AGENT_RUNTIME=harness → HarnessManager 唯一正式生产入口
            harness_manager = setup_harness_manager(settings, get_agent_config())
            if harness_manager is not None:
                attach_event_projection(harness_manager, session_factory)
                try:
                    await harness_manager.start()
                    logging.getLogger(__name__).info("Harness 常驻运行时已启动（B2-01）")
                except Exception:
                    logging.getLogger(__name__).warning(
                        "Harness 常驻运行时启动失败（保持未启动状态，运行将明确失败）",
                        exc_info=True,
                    )
            else:
                logging.getLogger(__name__).info(
                    "Harness 未配置（无 API Key/cordis），运行时未启动"
                )

        # L1：清理上次异常退出（kill -9 / 掉电）残留的上传临时文件。
        # 正常失败路径由上传路由的 finally 清理，此处只补进程被强杀的场景。
        try:
            from .services.attachment_uploads import cleanup_stale_upload_temp_files

            cleanup_stale_upload_temp_files(settings.attachments_dir)
        except Exception:
            logging.getLogger(__name__).warning(
                "启动清理：上传临时文件清理失败（不阻塞启动）", exc_info=True,
            )

        worker.recover_on_startup()
        worker.start()

        # 启动恢复不能挡住 ASGI 的 startup handshake。恢复任务组时会创建
        # 多个异步分析任务，而这些任务的早期数据库准备仍包含同步 ORM
        # 操作；直接在 lifespan 中 await 它们会让 Uvicorn 长时间停在
        # ``Waiting for application startup``，前端也就永远拿不到页面。
        # 将恢复调度放到独立任务，并让出一个事件循环 tick，先完成服务
        # 启动，再继续恢复，页面可立即打开且恢复仍会自动进行。
        recovery_task = None

        async def _schedule_recovered_work() -> None:
            await _asyncio.sleep(0.05)
            try:
                from .services.agent_runs.scheduler import schedule_recovered_runs
                recovered_runs = await schedule_recovered_runs(session_factory)
                from .services.agent_groups import schedule_recovered_groups
                recovered_groups = await schedule_recovered_groups(session_factory)
                if recovered_runs or recovered_groups:
                    logging.getLogger(__name__).info(
                        "启动恢复调度已放入后台：runs=%d, groups=%d",
                        len(recovered_runs), len(recovered_groups),
                    )
            except _asyncio.CancelledError:
                raise
            except Exception:
                # 恢复失败不应再次拖垮已经可用的工作台；具体运行会保留
                # 原状态，并由诊断接口/日志提供后续处理线索。
                logging.getLogger(__name__).warning(
                    "启动恢复调度未完成（不阻塞工作台启动）", exc_info=True,
                )

        recovery_task = _asyncio.create_task(_schedule_recovered_work())
        moni_task = None
        import os as _os
        if _os.getenv("MONI_AUTO_SYNC", "0").strip().lower() in {"1", "true", "yes", "on"}:
            async def _run_moni_sync():
                try:
                    from .services.moni_sync import sync_current_term
                    result = await _asyncio.to_thread(sync_current_term, settings)
                    logging.getLogger(__name__).info("MONI自动同步完成：%s", {key: result.get(key) for key in ("classes", "exams", "students", "item_scores", "skipped")})
                except Exception:
                    logging.getLogger(__name__).warning("MONI自动同步未完成（不阻塞工作台启动）", exc_info=True)
            moni_task = _asyncio.create_task(_run_moni_sync())
        try:
            yield
        finally:
            if recovery_task is not None and not recovery_task.done():
                recovery_task.cancel()
                try:
                    await recovery_task
                except _asyncio.CancelledError:
                    pass
            if moni_task is not None and not moni_task.done():
                moni_task.cancel()
                try:
                    await moni_task
                except _asyncio.CancelledError:
                    pass
            if harness_manager is not None and is_harness_mode():
                try:
                    await harness_manager.shutdown()
                except Exception:
                    logging.getLogger(__name__).warning(
                        "Harness 常驻运行时关闭异常", exc_info=True
                    )
            worker.stop()
            set_global_worker(None)
            from .agent.factory import close_orchestrator
            await close_orchestrator()
            # 清理捕获的主循环（避免测试间残留）
            set_main_event_loop(None)

    app = FastAPI(title="TeachMate 教学工作台", version=APP_VERSION, lifespan=lifespan)
    app.state.settings = settings
    app.state.session_factory = session_factory
    app.state.browser_session = BrowserSessionLifecycle()
    app.state.job_worker = worker
    app.state.plugin_manager = PluginManager(settings.data_dir)
    set_global_worker(worker)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )
    app.include_router(router)

    @app.get("/health")
    def health():
        return {"status": "ok", "service": "english-workbench"}

    @app.websocket("/api/v1/runtime/session")
    async def browser_runtime_session(websocket: WebSocket):
        supplied_token = websocket.query_params.get("token", "")
        try:
            valid_token = compare_digest(supplied_token.encode("utf-8"), TOKEN.encode("utf-8"))
        except (UnicodeEncodeError, TypeError):
            valid_token = False
        if not valid_token:
            await websocket.close(code=4401, reason="invalid local token")
            return
        await websocket.accept()
        app.state.browser_session.connect()
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            app.state.browser_session.disconnect()

    project_root = _project_root()
    workbench_page = project_root / "workbench.html"
    workbench_assets = project_root / "workbench-assets"
    if workbench_assets.is_dir():
        app.mount(
            "/workbench-assets",
            StaticFiles(directory=workbench_assets),
            name="workbench-assets",
        )
    if workbench_page.is_file():
        @app.get("/workbench", include_in_schema=False)
        def database_workbench():
            # 工作台是本地开发/桌面运行入口，HTML 内的资源版本号会随前端改动
            # 更新；禁止页面壳本身被浏览器复用，确保刷新能拿到最新 CSS/JS 引用。
            return FileResponse(
                workbench_page,
                headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
            )

        @app.get("/", include_in_schema=False)
        def workbench_index():
            return RedirectResponse(url="/workbench")

    return app
