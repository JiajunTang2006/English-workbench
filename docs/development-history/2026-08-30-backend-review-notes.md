# 2026-08-30

## 后端代码审查（backend/app，278 文件 / 3.6 万行）

产出：`docs/backend-code-review.md`

方法：compileall → ruff 全量（E/F/W/C4/B/SIM/UP/ARG/PIE/RET/PT/C90/PL，1421 项）→ 高危模式 grep → 核心模块人工深读 → 全量 pytest（1158 passed）→ 关键缺陷最小复现。

### 确认的缺陷

1. **P0 SSE 游标混用**（`app/routers/agent.py:937/953/960`）：`event_stream()` 用 `index += 1` 推进，但游标语义是 EventStore 全局 seq（`next_after` 返回 `max(seq)`）。seq 跳跃时 index 落后 → 事件重复推送。已用 `RunState` 原类复现：次轮取到 5 条（期望 2 条），105/106/107 重复。修复：`index = max(index, ev.seq if ev.seq is not None else index + 1)`。
2. **P0 TaskRegistry 内存泄漏**（`app/agent/task_registry.py:287`）：`cleanup_old()` 全项目无调用点，`_runs` 全局单例只增不减。桌面应用长期驻留会持续增长。
3. **P1 幂等缺口**（`job_worker.py:661`）：`submit_job` 幂等状态集缺 `waiting_ocr`（Worker 自产于 `:546`）→ 图片附件重复提交会产生第二个任务，重复计费。
4. **P1 heartbeat 空转**（`job_worker.py:42`）：`HEARTBEAT_TIMEOUT_SECONDS` 定义后无引用，`_heartbeat_loop` 只写 `updated_at` 无消费者 → 僵尸任务只能靠重启恢复。
5. **P1 缺 `Any` 导入**（`run_executor.py:508/554/1216`，ruff F821）：有 `from __future__ import annotations` 故运行时不崩，但 `get_type_hints()` 会 NameError。同类：`registry/capabilities.py:107` 的 `"ToolRegistry"`。
6. **P2 自赋值死代码**：`agent/capabilities/` 下 4 个文件（exam_analysis:22 / exam_ingestion:18 / review_plan:18 / student_diagnosis:18）导入后 `X = X`。
7. **P2 `datetime.utcnow()`**（`agent/config.py:350,437`）：仅此 2 处 naive，其余 69 处为 `now(timezone.utc)`。Py3.12 起弃用。可复用 `app.models.entities.utcnow()` helper。
8. **P2 导入风格混用**：6 个文件用 `from backend.app.*` 绝对导入（agent_runs/{repository,service,scheduler,recovery}、agent/session_mapper、agent/runtime/harness_event_projector），打包时是踩坑点。

### 复杂度热点
`_execute_harness_run_managed`（run_executor.py:745）圈复杂度 **58**；`apply_payload`(school_sync:375) 35；`switch_provider`(routers/agent:2110) 32。`routers/agent.py` 2491 行。

### 已核实健康（勿误改）
- SQL 注入：全 ORM，仅 PRAGMA 与 alembic_version 两处常量 SQL
- 路径穿越：`safe_storage_path` 三重校验（name 一致性 / 绝对路径 / parents），resolve 后判定
- 上传：魔数 + MIME + 50MB + PDF 200 页 + 5000 万像素 + Office ZIP 内部结构
- 启动迁移事务：migrate_legacy_documents、process_pending_file_operations **均有 commit**（曾怀疑未提交，已排除）
- 取消语义：CancelledError 各分支均正确 raise 且先落 cancelled 状态
- `plugin_catalog` 的同步 urlopen 在 `def`（非 async def）路由 → FastAPI 线程池，不阻塞事件循环

### 环境备注
- macOS 无 `timeout` 命令，改用 pytest 直接跑
- ruff/pytest 装在 `/Users/tangjiajun/.workbuddy/binaries/python/envs/default`，跑测试约 87 秒

---

## 第二轮复检（用户改完后）

### 修复验收：8 项全过
1. SSE 游标 → 抽出 `_advance_event_cursor()`（agent.py:863），3 处调用点改用。原复现场景验证：次轮 2 条（修复前 5 条），seq=[108,109] 无重复；seq=None 退化为 +1
2. 注册表清理 → `register()` 末尾 `if len(self._runs) > _RUNS_SOFT_LIMIT: await self.cleanup_old()`，锁外调用避免重入死锁
3. `waiting_ocr` → job_worker.py:726 已补
4. heartbeat → `HEARTBEAT_TIMEOUT_SECONDS` 在 job_worker.py:389 生效；task_registry 另加 `stale_running_seconds`（6h）僵尸强制 failed
5. `Any` 导入 → run_executor.py:21
6. 自赋值 → ruff PLW0127 归零
7. `utcnow` → **复检时发现未改，我补的**：config.py:350/437 → `datetime.now(timezone.utc)`，两处延迟导入加 `timezone`
8. 绝对导入 → 归零

### 复检新发现并已修复
**P0 回归：路由装饰器错位**（agent.py:862）
抽出 `_advance_event_cursor` 时 `@router.get("/runs/{run_id}/events")` 留在原处被新函数抢走，`get_run_events` 从未注册为路由。
- FastAPI **不校验路径参数是否在函数签名中** → 启动静默通过，无报错
- 请求会打到 `_advance_event_cursor(index, ev)` → 缺 index/ev 查询参数 → **422**
- 1158 个测试全绿仍未抓到：全是直接调内部函数，无端点 HTTP 往返断言
- 修复：装饰器下移。端到端验证（TestClient + `Authorization: Bearer {app.auth.TOKEN}`）三种模式均返回 404（错位则会 422）

**教训（重要，写进长期记忆）**：在路由函数上方插入辅助函数时装饰器会被"继承"；这类缺陷只有 HTTP 往返测试能抓到。

**P2**：capabilities.py:107 的 `"ToolRegistry"` 前向引用触发 F821（误报，函数内 113 行有延迟导入）。用 `TYPE_CHECKING` 块消除，F821 归零，无循环导入。

### 顺带核查
- `attachment_security.py` 改动是**安全增强**：新增 .docx/.doc + `_validate_office_zip` 结构校验；`.xlsx`/`.xls` 从 `or` 混判（可改名绕过）改为严格区分
- `RunState.created_at: float = field(default_factory=time.monotonic)` 与清理逻辑时钟同源，正确

### 最终状态
1167 passed / 0 failed（+9，新增 tests/test_review_p0_fixes.py）；F821=0、PLW0127=0、utcnow=0、绝对导入=0；路由正确挂载。
