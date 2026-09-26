# 后端代码审查报告

审查范围：`backend/app`（278 个 Python 文件，约 3.6 万行）＋ `backend/tests`（101 个测试文件）
审查方式：全量语法编译、ruff 静态分析（E/F/W/C4/B/SIM/UP/ARG/PIE/RET/PT/C90/PL）、高危模式扫描、核心模块人工深读、全量测试回归、关键缺陷最小复现

**总体结论：工程质量高于平均水平。** 1158 个测试全部通过，SQL 层全 ORM 无注入，附件安全做了魔数＋大小＋页数＋像素的多重校验，取消路径与恢复语义处理得相当严谨。以下列出的是确有实据的问题，按严重度分级。

---

## P0 — 真实缺陷，建议尽快修复

### 1. SSE 事件流游标混用，导致重复推送

**位置**：`app/routers/agent.py:931-963`（`event_stream()`）

推送循环用 `index += 1` 推进游标，但游标的真实语义是 EventStore 分配的**全局 seq**（`RunState.next_after` 返回的是 `max(seq)`）。两者是不同的编号空间：只要 seq 出现跳跃（多个 run 并发写入 EventStore 时必然发生），`index` 就会落后于真实位置，已推送的事件被再次取出。

**最小复现**（`RunState` 原类，未 mock）：

```
next_after (JSON 轮询游标) = 100
SSE 首轮取到事件数 = 3 (期望 3)
SSE 推进后 index = 103 （实际最新 seq = 107）
SSE 次轮取到事件数 = 5 (期望 2)      ← 重复
取到的 seq = [105, 106, 107, 108, 109]   ← 105/106/107 重复推送
```

**修复方向**：按事件自身的 seq 推进，而非固定加一。

```python
for ev in events:
    yield ev.to_sse()
    index = max(index, ev.seq if ev.seq is not None else index + 1)
```

三处 `index += 1`（937、953、960 行）需同步修改。

---

### 2. 任务注册表内存只增不减

**位置**：`app/agent/task_registry.py:287`（`cleanup_old`）、`:104`（`self._runs`）

`TaskRegistry` 是全局单例（`:305`），`_runs: dict[int, RunState]` 每次运行写入一个条目，每个 `RunState` 还持有完整的 `events` 列表。`cleanup_old()` 定义了清理逻辑，**但全项目没有任何调用点**（含 tests 内也无调用）。

```
$ grep -rn "cleanup_old" app tests
app/agent/task_registry.py:287:    async def cleanup_old(...)   ← 仅定义
```

对于一个目标场景是长期驻留的桌面应用，这是持续的内存增长：使用越久，`_runs` 越大，永不清零。

**修复方向**：在 `complete()` / `fail()` / `cancel()` 进入终态后异步调用 `cleanup_old()`，或改为按终态事件驱动清理。

> 附带问题：`cleanup_old` 只删除终态条目。若最老的一批恰好是非终态（卡死的 running），则一个都删不掉，每次调用都是无效扫描。

---

## P1 — 逻辑缺陷 / 一致性风险

### 3. `waiting_ocr` 状态绕过幂等保护

**位置**：`app/services/job_worker.py:661`（`submit_job`）

```python
BackgroundJob.status.in_(["queued", "running", "waiting_confirmation"])
```

但 Worker 自己会产生第四种非终态 `waiting_ocr`（`:546` `_mark_waiting_ocr`，表示"附件已解析、等待外部 OCR"）。它不在幂等集合内，于是同一附件重复提交时幂等检查失效，会创建**第二个任务**——对图片类附件意味着重复的模型调用与重复计费。

**修复方向**：把 `waiting_ocr` 加入幂等状态集。

---

### 4. heartbeat 是"只写不读"的僵尸机制

**位置**：`app/services/job_worker.py:42`、`:500-511`

`HEARTBEAT_TIMEOUT_SECONDS = 60.0` 定义后**从未被任何代码引用**。`_heartbeat_loop` 每 5 秒更新 `updated_at`，但没有任何消费者去检测"超过 60 秒未更新"。

后果：进程被 `kill -9` 或掉电时，残留的 `running` 任务无法在运行期间被发现，只能等下次启动由 `recover_on_startup` 处理。心跳机制目前是空转的——每 5 秒一次无谓的数据库事务。

**修复方向**：要么让 `recover_on_startup` 真正基于 `updated_at` 超时判定（把中断任务重新入队），要么移除心跳线程以省掉常驻写入。

---

### 5. `run_executor.py` 缺失 `Any` 导入

**位置**：`app/agent/run_executor.py:508`、`:554`、`:1216`

三处返回注解使用 `-> Any`，但文件顶部只导入了 `TYPE_CHECKING`，未导入 `Any`（ruff F821）。

因文件有 `from __future__ import annotations`，注解延迟求值，当前**运行时不会崩溃**；但任何 `typing.get_type_hints()` 调用（Pydantic、部分依赖注入框架、文档生成）都会抛 `NameError`。同类问题见 `app/agent/registry/capabilities.py:107` 的 `"ToolRegistry"`。

**修复方向**：补 `from typing import Any, TYPE_CHECKING`。

---

## P2 — 待清理项

### 6. 四处自赋值死代码

`app/agent/capabilities/` 下 4 个文件在导入后立即自赋值，纯冗余（ruff PLW0127）：

| 文件 | 行 |
|---|---|
| `exam_analysis.py` | 22 |
| `exam_ingestion.py` | 18 |
| `review_plan.py` | 18 |
| `student_diagnosis.py` | 18 |

形如 `EXAM_ANALYSIS_OUTPUT_SCHEMA = EXAM_ANALYSIS_OUTPUT_SCHEMA`，上一行已 `from ..schema_contract import ...`，直接删除这几行即可。

### 7. `datetime.utcnow()` 已弃用且时区语义不一致

**位置**：`app/agent/config.py:350`、`:437`

全项目其余 **69 处**统一使用 `datetime.now(timezone.utc)`（aware），仅这两处用 `datetime.utcnow()`（naive，Python 3.12 起 DeprecationWarning，未来版本移除）。测试输出已可见告警：

```
app/agent/config.py:350: DeprecationWarning: datetime.datetime.utcnow() is deprecated
```

当前 `updated_at` 无下游比较所以尚未触发异常，但属于升级即炸的定时炸弹，且 naive/aware 混存会造成时间语义歧义。项目内已有现成的 `app.models.entities.utcnow()` helper 可直接复用。

### 8. 导入风格混用

6 个文件使用绝对导入 `from backend.app.*`，其余全部使用相对导入：

```
app/services/agent_runs/{repository,service,scheduler,recovery}.py
app/agent/{session_mapper,runtime/harness_event_projector}.py
```

这要求 `backend` 包必须在 `sys.path` 上，在 PyInstaller 冻结打包场景下是常见的踩坑点。建议统一为相对导入。

### 9. 复杂度热点

| 函数 | 位置 | 圈复杂度 |
|---|---|---|
| `_execute_harness_run_managed` | `run_executor.py:745` | **58** |
| `apply_payload` | `school_sync.py:375` | 35 |
| `switch_provider` | `routers/agent.py:2110` | 32 |
| `_make_payload` | `moni_sync.py:414` | 31 |
| `send_message` | `routers/agent.py:442` | 29 |
| `create_app` | `factory.py:35` | 29 |

`_execute_harness_run_managed` 复杂度 58、约 600 行，是维护风险最高的单点。另 `routers/agent.py` 单文件 2491 行且含 3 个 C901 违规，建议按资源维度拆分。

### 10. 静态分析欠账

ruff 全量（E/F/W/C4/B/SIM/UP/ARG/PIE/RET/PT/C90/PL）共 1421 项，其中可直接清理的：未使用导入 69、超长行 232、`datetime.utcnow` 72、可选注解可现代化 43、函数内延迟导入 422。建议固化 ruff 配置并纳入 CI 门禁。

---

## 已核实无问题的项（避免误改）

审查中逐项验证过、结论为**健康**的部分：

| 检查项 | 结论 |
|---|---|
| SQL 注入 | 全 SQLAlchemy ORM；仅 `PRAGMA` 与 `alembic_version` 两处常量 SQL，无字符串拼接 |
| 路径穿越 | `safe_storage_path` 做了 `name` 一致性、绝对路径、`.parents` 三重校验，且 `resolve()` 后判定，防护完整 |
| 上传校验 | 扩展名白名单＋MIME 一致性＋文件魔数＋50MB 限额＋PDF 200 页＋图片 5000 万像素，Office 包还校验 ZIP 内部结构 |
| 启动迁移事务 | `migrate_legacy_documents` 与 `process_pending_file_operations` 均有 `session.commit()`（曾怀疑未提交，已核实排除） |
| 取消语义 | `CancelledError` 在各分支均正确 `raise` 并先落 `cancelled` 状态，终态不会被后续覆盖 |
| 事件循环阻塞 | `plugin_catalog` 的同步 `urlopen` 位于 `def`（非 `async def`）路由，由 FastAPI 线程池执行，不阻塞事件循环 |
| 测试回归 | 1158 passed，0 failed |

---

## 建议修复顺序

1. SSE 游标（P0-1）——用户可见的功能缺陷，改动仅 3 行
2. 注册表清理（P0-2）——补调用点即可，长期运行的稳定性收益大
3. `waiting_ocr` 幂等（P1-3）——防重复计费，改动 1 行
4. 补 `Any` 导入 / 删自赋值 / 换 `utcnow`（P2-5~7）——低风险批量清理
5. 拆分 `_execute_harness_run_managed` 与 `routers/agent.py`——建议随下次功能改动渐进推进

---

# 复检记录（修复后第二轮）

## 修复验收

| # | 问题 | 状态 | 验证方式 |
|---|---|---|---|
| 1 | SSE 游标混用 | ✅ 已修 | 抽出 `_advance_event_cursor()`，3 处调用点改用；原复现场景验证：次轮取到 2 条（修复前 5 条），seq=[108,109] 无重复 |
| 2 | 注册表内存泄漏 | ✅ 已修 | `register()` 末尾接入 `cleanup_old()`（锁外调用避免重入死锁），新增 `stale_running_seconds` 僵尸回收 |
| 3 | `waiting_ocr` 幂等缺口 | ✅ 已修 | `job_worker.py:726` 状态集已补 `waiting_ocr` |
| 4 | heartbeat 空转 | ✅ 已修 | `HEARTBEAT_TIMEOUT_SECONDS` 已在 `job_worker.py:389` 生效 |
| 5 | 缺 `Any` 导入 | ✅ 已修 | `run_executor.py:21` 已导入 |
| 6 | 自赋值死代码 | ✅ 已清理 | ruff PLW0127 全部通过 |
| 7 | `datetime.utcnow()` | ✅ 已修（复检时补） | `config.py:350/437` 改为 `datetime.now(timezone.utc)` |
| 8 | 导入风格混用 | ✅ 已清理 | `from backend.app.*` 归零 |

## 复检中发现的新问题

### P0 回归：路由装饰器错位（已修复）

**位置**：`app/routers/agent.py:862`

修复 #1 时抽出 `_advance_event_cursor()` 辅助函数，但 `@router.get("/runs/{run_id}/events")` 装饰器留在原位置，被**新函数抢走**：

```
['GET'] /api/v1/agent/runs/{run_id}/events -> _advance_event_cursor   ← 错误
```

真正的 `get_run_events()` 变成普通函数，**从未注册为路由**。FastAPI 不校验"路径参数是否存在于函数签名"，启动时静默接受、不报错。

**影响**：SSE 事件流与 JSON 轮询接口全部失效。请求会被路由到 `_advance_event_cursor(index, ev)`，因缺少必需查询参数 `index`/`ev` 返回 **422**，TeachMate 事件推送完全不可用。

**为什么测试没抓到**：1158 个测试均为直接调用内部函数，没有针对该端点做真实 HTTP 往返断言。

**已修复**：装饰器下移到 `get_run_events()`。端到端验证（`TestClient` + Bearer Token）：

```
JSON 轮询模式 status = 404 (期望 404)
SSE 模式      status = 404 (期望 404)
带 after 参数 status = 404
```

若路由仍错位，此处会返回 422。路由表确认 `GET /api/v1/agent/runs/{run_id}/events -> get_run_events`，`_advance_event_cursor` 已不在路由中。

> **教训**：在路由函数上方插入辅助函数时，装饰器会被"继承"。建议补充端点的 HTTP 往返测试，而不只测内部函数。

### P2 遗留：F821 前向引用（已修复）

`registry/capabilities.py:107` 的 `"ToolRegistry"` 字符串注解触发 ruff F821。属误报（函数内 line 113 有延迟导入，`from __future__ import annotations` 保证注解不求值），已用 `TYPE_CHECKING` 块消除，F821 归零且无循环导入。

## 顺带核查（结论：无问题）

- **`attachment_security.py` 改动是安全增强**：新增 `.docx`/`.doc` 支持的同时补了 Open XML 的 ZIP 内部结构校验（`_validate_office_zip`），并把 `.xlsx`/`.xls` 的混判（原用 `or`，可改名绕过）改为严格区分——`.xlsx` 必须 ZIP 魔数、`.xls` 必须 OLE2 魔数。
- **`cleanup_old` 新增依赖已就位**：`RunState.created_at: float = field(default_factory=time.monotonic)`，与清理逻辑的 `time.monotonic()` 时钟同源，语义正确。

## 最终状态

| 检查项 | 结果 |
|---|---|
| 语法编译 | 通过 |
| 全量测试 | **1167 passed, 0 failed**（较首轮 +9，新增 `test_review_p0_fixes.py`） |
| ruff F821 | 0 |
| ruff PLW0127 | 0 |
| `datetime.utcnow()` | 0 |
| `from backend.app.*` | 0 |
| 路由注册 | `get_run_events` 已正确挂载 |
| 端到端 HTTP | 404 语义正确（非 422） |
