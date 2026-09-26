# TeachMate × DeepSeek Harness 集成接口说明（B3-00 基线）

> 版本：B3-00（第三批基线冻结）
> 状态：**已冻结**。本文件是 TeachMate 与 Harness 之间唯一权威接口基线；
> 任何与真实 vendored SDK 不符的描述、方法或事件格式，一律以 `vendor/deepseek-harness-upstream/python/sdk` 源码为准。

## 1. 架构角色（冻结声明）

| 角色 | 组件 | 责任边界 |
|---|---|---|
| 教学数据事实源 | **Workbench**（FastAPI + SQLite） | 学期/班级/考试/学生/成绩/附件/证据的唯一事实源；所有查询只读；数据不出本地 |
| 正式 Agent 内核 | **DeepSeek Harness**（vendored SDK 子进程） | 唯一产生模型回合的 Agent；多轮上下文/工具调用/最终答案都在 Harness 内完成 |
| 接口层 | **HarnessManager**（backend.app.agent.runtime.harness_manager） | 常驻进程管理、协议调用、事件广播、健康检查；只委托 vendored SDK，不自实现协议 |
| 业务映射 | **SessionMapper**（backend.app.agent.session_mapper） | AgentSession ↔ Harness sessionId 的稳定映射与 context_revision |
| 事件投影 | **HarnessEventProjector / EventStore** | 把真实 notification 投影为内部事件并落库（seq 原子分配） |
| Education Bridge | backend.app.agent.education_bridge（B3-03） | Harness 访问 Workbench 教学数据的唯一白名单入口 |
| Python Agent | backend.app.agent（legacy 路径） | **仅临时回滚**（AGENT_RUNTIME=legacy）；不新增功能，不在本批次同步开发 |

**冻结规则（禁止违反）**：

1. 禁止修改 `vendor/deepseek-harness-upstream` 下任何核心源码（sdk / sdk-runtime / 插件）。
   只允许在仓库根新增或扩展 Workbench 自有代码。
2. Harness 是唯一正式 Agent 内核；产生「最终答案」的路径只有 Harness SDK。
3. `AGENT_RUNTIME=harness` 是唯一正式模式；未配置/未启动时必须明确失败，**不得静默回退**到
   Python Agent 或 HTTP 适配器。
4. `AGENT_RUNTIME=harness-http` 仅作为已冻结的历史回滚开关，不再演进；`AGENT_RUNTIME=legacy`
   保留 Python Agent 回滚路径（B3-00 之后不再新增能力）。
5. 任何代码中不得出现 Harness 不存在的 RPC 方法（见 §2 方法清单）；失败必须 fail-closed。

## 2. 真实 JSON-RPC 协议（由 vendored SDK 实现）

Harness 运行时只支持以下方法；**不存在** `session/create`、`session/send`、`session/close`、
`session/cancel`：

| 方法 | 方向 | 语义 |
|---|---|---|
| `initialize` | 客户端 → 运行时 | 进程启动时由 SDK 自动完成；设置 cwd/provider/model/maxTokens |
| `session/prompt` | 客户端 → 运行时 | 提交一次回合（sessionId + contentBlocks）；**只返回 `{messageId}`** |
| `shutdown` | 客户端 → 运行时 | 关闭运行时；SDK close 时重发并回收进程 |

`session/prompt` 的**最终答案**不在 RPC 响应里，而是通过通知流获得：
`agent/inbox/spliced` 回执 → `session.event`（调用了 assistant/message、turn/start 等）→
`session.status=idle`。SDK 的 `Session.run()` 已把「发 prompt + 监听通知 + 等 idle + 提取
最终 assistant 消息」封装为一次回合，返回 `RunResult.session_id / final_response /
finish_reason / events / notifications`。**上层禁止把 messageId 当最终答案**。

### 2.1 harness_manager 只暴露真实方法

`HarnessManager.SUPPORTED_RPC_METHODS = frozenset({"initialize", "session/prompt", "shutdown"})`。
`call()` 对任何其他方法名直接抛错（fail-closed）。

### 2.2 会话复用与取消

- 会话通过 `sessionId` 复用，不会二次 create；
- 取消 = 本地取消标记（`cancel_session()`）+ 回合在下一个 notification 处提前终止 +
  广播 `session.status=cancelled`。不存在远端强制中止。

## 3. 事件格式（真实 notification）

SDK 回调 `Notification(method, payload)`，事件格式：

```jsonc
// session.event
{"method": "session.event",
 "payload": {"sessionId": "<ssid>", "event": {"type": "assistant/message", "data": {...}}}}

// session.status
{"method": "session.status",
 "payload": {"sessionId": "<ssid>", "status": "idle"}}
```

常见 event type：
- `message.start` / `assistant/message` / `message.delta`（流式 delta 过滤不落库）
- `turn/start` / `turn/end`
- `tool_call.start` / `tool_call.end`（tool 事件，B3-03 工具调用通过它可见）
- `agent/finished` / `agent/end`
- `agent/inbox/spliced`（内部回执，过滤）
- `usage.update`（token 用量）
- `error`（运行时错误，不打印完整输入）

## 4. 运行时资产与启动（官方定位）

| 资产 | 位置（dev 模式） |
|---|---|
| JSON-RPC 运行时 | `vendor/deepseek-harness-upstream/python/sdk-runtime/node_modules/@deepseek-ai/dsh-sdk-jsonrpc-demo/lib/bin.js`（node ≥ 22.19 执行）；dev 模式优先走官方 `deepseek_harness_runtime.resolve_bundled_launch_args("node")`，依次回退到 repo 内闭包 |
| 默认 Cordis 配置 | `backend/assets/cordis/teachmate.cordis.yml`（B3-03 建立；只含白名单教学工具，不含 bash/fs/子代理/HTTP） |
| 会话根目录 | `<settings.data_dir>/harness-sessions` |

环境变量（SDK 注入）：`DSH_CORDIS_CONFIG`、`DSH_SESSION_ROOT`、`DSH_CWD`、
`DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL`。API Key 只经 SDK 环境变量传递，**禁止**写入
日志 / DB / 事件 / 配置接口返回。

## 5. Education Bridge 设计（B3-03）

Harness 里只挂白名单工具：考试概览 / 分数分布 / 学生趋势 / 风险信号 / 错题统计 /
正式附件读取 / 报告提交。scope（`run_id`/`term_id`/`class_id`/`exam_id`/`student_id`）
由服务器注入，模型不自行提交。工具结果只返回脱敏事实 + evidence ID + 计算说明；
实现上由 TeachMate 侧受限 CLI 执行只读查询后把 evidence 落库（见 `education_bridge/`）。

## 6. 隐私基线（B3-04）

- 身份词典：学生姓名 / 学号 / 联系电话 / 教师姓名 / 学校名称，构建于每次运行（`identity_dict`）；
- 匿名编号：同一 session/context revision 内稳定，跨会话不复用，scope 变化重生成；
- 出站内容：当前问题 / 历史 user / 历史 assistant / 会话摘要 / 正式附件 / 工具结果 /
  修复重试 prompt 全部经 `PrivacyMapper.sanitize_text` 后才发 Provider；
- Harness 会话日志（DSH_SESSION_ROOT）只含脱敏内容。

## 7. 方法清单（禁止出现的假协议）

| 禁止方法 | 原因 | B3-00 处置 |
|---|---|---|
| `session/create` | 真实协议无此方法 | 已 fail-closed |
| `session/send` | 不存在 | fail-closed |
| `session/close` | 不存在 | fail-closed |
| `session/cancel` | 真实取消是本地语义 | `cancel_session()` 是本地标记，非 RPC |

> 搜索仓库，凡以 `session/create|session/send|session/close|session/cancel` 作为真正 RPC
> 发出的路径，都必须已移除；`cancel_session` 是本地语义的命名接口，不是 RPC 方法，允许保留。