"""路由注册回归测试（评审复检发现的 P0：装饰器被辅助函数抢占）。

背景：在 `async def get_run_events` 上方插入模块级辅助函数时，若锚点落在
装饰器与函数之间，`@router.get` 会抢给辅助函数——FastAPI 不校验路径参数
是否存在于函数签名，启动静默通过，但事件流/轮询接口全部 422 失效。
本轮 1158 个测试全是函数级调用，没有一个 HTTP 往返，因此未拦截。

本文件用两层断言堵死这类缺陷：
1. 路由表断言：openapi schema 中路径存在，且绑定的处理函数是真实端点
   （operationId 暴露函数名），任何下划线开头的私有辅助函数占位即失败；
2. HTTP 往返断言：带认证请求真实打到端点处理逻辑（不存在 run → 404）。
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.auth import TOKEN
from backend.app.config import Settings
from backend.app.factory import create_app

# 关键端点清单：路径 → 处理函数名（装饰器错位即会偏离）
EXPECTED_AGENT_RUN_ROUTES = {
    ("GET", "/api/v1/agent/runs/{run_id}/events"): "get_run_events",
}


@pytest.fixture(scope="module")
def client():
    app = create_app(Settings(data_dir=Path(tempfile.mkdtemp())))
    with TestClient(app) as test_client:
        yield app, test_client


def _iter_operations(app):
    schema = app.openapi()
    for path, item in schema["paths"].items():
        for method, op in item.items():
            if method in {"parameters", "summary", "description"}:
                continue
            yield method.upper(), path, (op.get("operationId") or "")


def test_run_events_route_bound_to_real_handler(client):
    app, _ = client
    operations = {(method, path): op_id for method, path, op_id in _iter_operations(app)}
    for (method, path), handler in EXPECTED_AGENT_RUN_ROUTES.items():
        operation_id = operations.get((method, path))
        assert operation_id is not None, f"路由未注册: {method} {path}"
        assert operation_id.startswith(handler), (
            f"{method} {path} 被函数 {operation_id} 占据（期望 {handler}）——"
            "装饰器错位回归"
        )


def test_no_private_helper_occupies_any_route(client):
    """任何路由的绑定函数不得是下划线开头的私有辅助函数。"""
    for method, path, operation_id in _iter_operations(client[0]):
        assert not operation_id.startswith("_"), (
            f"{method} {path} 被私有辅助函数占据: {operation_id}"
        )


def test_run_events_http_roundtrip_reaches_handler(client):
    """HTTP 往返：带认证请求真实进入 get_run_events 处理逻辑。"""
    _, test_client = client
    headers = {"Authorization": f"Bearer {TOKEN}"}
    # 不存在的 run → 端点处理逻辑返回 404（错位时这里是 422 参数错误）
    response = test_client.get(
        "/api/v1/agent/runs/999999/events", headers=headers)
    assert response.status_code == 404, (
        f"期望 404（端点逻辑），实际 {response.status_code}: {response.text[:200]}"
    )
    # 未带 token → 认证拦截 401（证明依赖链正常）
    anonymous = test_client.get("/api/v1/agent/runs/999999/events")
    assert anonymous.status_code == 401
