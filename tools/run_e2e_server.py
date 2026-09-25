#!/usr/bin/env python3
"""e2e 专用测试服务启动器 (S0-03)

供 `npm run test:e2e`（Playwright webServer）自动拉起面向浏览器测试的
FastAPI 服务。特点：

- 创建独立临时数据目录，绝不对正式应用数据目录做任何读写；
- 固定仅供测试使用的 Token（与 tests/e2e/fixtures.js 保持一致）；
- 明确离线测试运行模式：不配置任何模型 Provider、不拉起真实 Harness；
- 暴露 /health 供健康检查；
- 接收 SIGINT/SIGTERM，触发 uvicorn 优雅关闭（lifespan shutdown 会
  依次停止后台 Worker 与 Agent 运行器）；
- 正常退出后清理临时数据目录（--keep 可保留用于诊断）。

用法::

    python3 tools/run_e2e_server.py [--port 18323] [--token <token>] [--keep]
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import tempfile
from pathlib import Path

# 保证 `backend.app...` 可导入：webServer 启动本脚本时 cwd 可能是项目根，
# 但 sys.path[0] 会指向 tools/，需要显式加入项目根。
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

E2E_DEFAULT_PORT = 18323
# 仅供测试使用的固定 Token。此常量与 tests/e2e/fixtures.js 中的
# E2E_TOKEN 必须保持一致；它不是生产密钥，只对本测试服务有效。
E2E_DEFAULT_TOKEN = "workbench-e2e-test-token-18323"


def server_started_successfully(server: object) -> bool:
    """兼容不同 uvicorn 版本，判断服务是否完成过启动。"""
    return bool(getattr(server, "started", False))


def main() -> int:
    parser = argparse.ArgumentParser(description="e2e 测试服务启动器")
    parser.add_argument("--port", type=int, default=int(os.getenv("WORKBENCH_PORT", E2E_DEFAULT_PORT)), help="监听端口（默认 18323）")
    parser.add_argument("--data-dir", default=None, help="临时数据目录（默认自动创建并清理）")
    parser.add_argument("--token", default=os.getenv("WORKBENCH_TOKEN", E2E_DEFAULT_TOKEN), help="仅供测试使用的 Token")
    parser.add_argument("--keep", action="store_true", help="退出后保留临时数据目录（诊断用）")
    args = parser.parse_args()

    data_dir = Path(args.data_dir) if args.data_dir else None
    created_tmp = False
    if data_dir is None:
        data_dir = Path(tempfile.mkdtemp(prefix="workbench-e2e-data-"))
        created_tmp = True
    data_dir = data_dir.resolve()

    # --- 环境约束：临时数据目录 + 测试端口 + 测试 Token + 离线模式 ---
    os.environ["WORKBENCH_DATA_DIR"] = str(data_dir)
    os.environ["WORKBENCH_PORT"] = str(args.port)
    os.environ["WORKBENCH_TOKEN"] = args.token
    # 离线测试运行模式：不配置任何模型 Provider（页面保持"未就绪即可浏览"的
    # 离线数据视图），并确保不会尝试连接模型供应商。
    for key in ("AGENT_TEXT_API_KEY_ENV", "AGENT_VISION_API_KEY_ENV"):
        os.environ.pop(key, None)

    print(f"[e2e-server] 数据目录: {data_dir}")
    print(f"[e2e-server] 监听: http://127.0.0.1:{args.port}（仅测试 Token 有效）")

    # 延迟导入：先让环境变量生效，再导入应用工厂
    from backend.app.config import Settings
    from backend.app.factory import create_app

    try:
        settings = Settings(data_dir=data_dir, host="127.0.0.1", port=args.port)
        settings.ensure_directories()
        app = create_app(settings)
    except Exception:
        print("[e2e-server] 应用创建失败", flush=True)
        import traceback
        traceback.print_exc()
        if created_tmp and not args.keep:
            import shutil
            shutil.rmtree(data_dir, ignore_errors=True)
        return 2

    import uvicorn

    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=args.port,
            log_level=os.getenv("E2E_LOG_LEVEL", "warning"),
            access_log=False,
        )
    )

    def _shutdown(signum, _frame) -> None:
        print(f"[e2e-server] 收到信号 {signum}，开始优雅关闭（Worker 与运行器将一并停止）", flush=True)
        server.should_exit = True

    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(signum, _shutdown)
        except (ValueError, OSError, AttributeError):
            # 非主线程或平台不支持时忽略，交由 uvicorn 默认处理
            pass

    exit_code = 0
    try:
        server.run()
        if server_started_successfully(server):
            print("[e2e-server] 服务已优雅退出", flush=True)
        else:
            exit_code = 1
    except KeyboardInterrupt:
        pass
    finally:
        if created_tmp and not args.keep:
            import shutil
            shutil.rmtree(data_dir, ignore_errors=True)
            print(f"[e2e-server] 已清理临时数据目录: {data_dir}", flush=True)
        elif args.keep:
            print(f"[e2e-server] 保留临时数据目录（--keep）: {data_dir}", flush=True)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
