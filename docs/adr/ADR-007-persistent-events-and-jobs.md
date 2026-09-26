# ADR-007：持久化运行事件与后台任务

**日期：** 2026-08-17  
**状态：** 已接受

## 背景

当前 `TaskRegistry` 只在内存保存运行事件，应用重启后无法重放。`BackgroundJobManager` 仍是 Stub，附件解析等长任务无法可靠执行。前端使用每秒轮询获取运行状态，既浪费资源又无法实时反映进度。

## 决定

1. **运行事件持久化到 SQLite**：新增 `analysis_run_events` 表，每个事件有 `run_id`、`seq`（单次运行内严格递增）、`event_type`、`payload_json`（脱敏后）、`source` 和 `created_at`。`UNIQUE(run_id, seq)` 约束保证顺序。
2. **SQLite 持久队列**：复用 `background_jobs` 表，不引入 Redis/Kafka/Celery。默认单 Worker、FIFO，数据库事务认领任务防止重复执行。
3. **前端切换 Fetch SSE**：使用 `fetch()` + `Authorization: Bearer` + `ReadableStream` 解析 SSE，不用原生 `EventSource`（避免 Token 放进 URL）。SSE 不可用时才回退轮询。
4. **`TaskRegistry` 降级为活跃任务索引**：不再承担事件事实源职责，只维护当前活跃任务的路由信息。

## 备选方案

1. **引入 Redis + Celery**：增加外部依赖和部署复杂度，与单教师本地工作台定位不符。
2. **继续用内存 TaskRegistry**：重启丢失、无法恢复、无法审计。
3. **原生 EventSource**：不支持自定义 Header，Token 必须放进 URL，有安全风险。

## 原因

- SQLite 已是本项目的数据底座，零额外依赖；
- 持久队列 + 事务认领足以满足单教师单 Worker 的并发需求；
- Fetch SSE 兼顾实时性和安全性，且能复用现有 Bearer Token 认证。

## 后果

- `analysis_run_events` 是 Harness 事件的 UI/审计投影，不取代 Harness 原始 Session Log；
- 事件 `seq` 用于断线重连，前端保存游标，从 `after` 恢复；
- 后台任务支持 heartbeat、attempts、checkpoint、idempotency_key、取消、重试和指数退避；
- 应用退出时停止接新任务，给当前任务有限收尾时间；
- 启动时恢复 `queued` 任务，审计 `interrupted` 的 `running` 任务；
- 事件 payload 不得保存 API Key、完整 Prompt 或未脱敏工具输出。

## 回滚方式

- 持久事件和后台任务均为新增表和新增代码，不影响现有 `analysis_runs` 和 `analysis_evidence`；
- 前端 SSE 失败时自动回退轮询，不阻塞使用；
- 如需完全回滚，删除新增表和代码，恢复 `TaskRegistry` 为事件源即可。

## 事实源

| 数据 | 权威事实源 | 说明 |
|---|---|---|
| 运行事件序列 | `analysis_run_events` (SQLite) | UI 重放和审计的投影 |
| 后台任务状态 | `background_jobs` (SQLite) | 持久队列的事实源 |
| 原始 Agent 事件 | Harness Session Log | 不可被 SQLite 投影取代 |
