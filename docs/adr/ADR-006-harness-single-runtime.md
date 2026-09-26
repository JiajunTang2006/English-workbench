# ADR-006：Harness 作为唯一生产 Agent 内核

**日期：** 2026-08-17  
**状态：** 已接受

## 背景

TeachMate 当前同时维护两条 Agent 运行路径：

1. **Python Agent**：自研的 Provider → Loop → Orchestrator 链路，在 Python 进程内直接调用模型 API；
2. **Harness Headless 适配器**：通过子进程调用 DeepSeek Harness CLI，每次提问启动一个新 Node 进程。

`run_executor.py` 同时维护这两条路径，通过 `AGENT_RUNTIME` 环境变量切换。这种双路径带来以下问题：

- 两套事件模型、两套工具注册和两套证据提取逻辑需要同步维护；
- Harness 路径没有连续会话，每次提问丢失上下文；
- Python Agent 的流式输出、Token 计量和上下文压缩能力弱于 Harness；
- 开发和测试负担高，新功能需要两次实现。

## 决定

**Harness 成为唯一生产 Agent 内核。** Python Agent 只在 `0.9.0-alpha` 到 `0.9.0` 期间作为 `AGENT_RUNTIME=legacy` 回滚开关保留，连续两个版本无回滚后在 `1.0.0` 删除。

## 备选方案

1. **保留双路径长期并存**：维护成本持续上升，且两条路径的行为差异导致 UI 和证据一致性难以保证。
2. **完全重写 Python Agent**：不利用 Harness 已有的 Agent Loop、Session Persistence、Token Meter 和 Context Compaction 能力。
3. **直接删除 Python Agent**：没有回滚期，迁移期间发现问题无法快速恢复。

## 原因

- Harness 已具备 Session Persistence、Checkpoint、Token Meter 和 Context Compaction，不需要重新实现；
- JSON-RPC 常驻进程模式比每次启动 CLI 更高效，且支持连续会话；
- 统一到单一内核后，事件映射、工具白名单和证据提取只需维护一套；
- 保留 legacy 开关提供安全回滚期，降低迁移风险。

## 后果

- `0.9.0-alpha`：Harness 默认，`AGENT_RUNTIME=legacy` 可回滚到 Python Agent；
- `0.9.0-beta/rc`：试点只使用 Harness，记录所有回滚原因；
- `0.9.0`：Harness 唯一公开路径，legacy 开关隐藏；
- `1.0.0`：删除 Python Provider/Loop/Orchestrator 通用实现；
- 删除前必须把 Python Agent 中仍有价值的隐私、证据、Schema 和成本逻辑迁移到统一服务层。

## 回滚方式

在 `0.9.0` 之前，设置 `AGENT_RUNTIME=legacy` 即可回滚到 Python Agent。回滚后 Harness 相关代码不执行，但不删除。

## 事实源

| 数据 | 权威事实源 | 说明 |
|---|---|---|
| 学期、班级、学生、考试、成绩 | WorkBench SQLite | Harness 只能通过只读教育工具访问 |
| 模型会话与原始 Agent 事件 | Harness Session Log | 保留连续对话、工具调用和模型事件 |
| 教师确认后的评价与报告 | WorkBench SQLite | AI 草稿不能覆盖教师确认文本 |

## 取消语义

- Harness `session/cancel` 只取消目标 session 的当前 turn，不关闭其他 session；
- 取消成功必须出现 `run.cancelled` 终态事件；
- 若本阶段无法安全增加协议扩展，临时策略为标记结果丢弃 + 重启 Harness 进程，UI 明确显示为强制终止（该策略不得作为 `1.0.0` 最终实现）。

## 失败恢复语义

- Harness 崩溃后健康检查变为 `unavailable`，自动重启有上限（默认 3 次/10 分钟）；
- 正在运行的 turn 标记为 `interrupted`，根据 `attempts` 决定是否重试；
- 已产生外部付费请求但结果未知时，不静默重复调用，先提示教师确认重试。
