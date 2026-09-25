# TeachMate / WorkBuddy 数据接口（只读）

将 TeachMate 的确定性教学事实、已生成的分析报告与证据化工作流以本地 MCP 形式暴露，
供 Codex、WorkBuddy 等 MCP 客户端读取。PDF、PPT 等正式材料可以由 WorkBuddy 基于这些
结构化结果生成，TeachMate 不需要重复实现文档排版。
本地 MCP over stdio，零外部依赖（仅标准库），由 Codex 本地启动后通过回环地址调用 TeachMate FastAPI。

## 架构

- `server/`：MCP stdio 服务器（标准库实现）。
  - `schemas.py` 工具定义；`client.py` HTTP 客户端；`auth.py` 配对/令牌；`tools.py` 调度；`error.py` 错误码；`__main__.py` 传输与 CLI。
- `.codex-plugin/plugin.json`：符合 Codex 官方清单格式的插件清单，可直接被 Codex 识别。
- `.teachmate-plugin/plugin.json`：TeachMate 扩展清单，声明权限、配对策略和 Plugin Manager 运行时信息。
- `.mcp.json`：MCP server 注册（Codex 读取）。
- `skills/`：四个教学工作流 Skill（analyze-exam / diagnose-student / build-review-plan /
  generate-personalized-practice）。

## 启用服务端（TeachMate 侧）

1. 设置页将 `codex_plugin_enabled` 设为 `true`（或环境变量 `AGENT_CODEX_PLUGIN_ENABLED=true`）。
2. 在设置页生成一次性配对码。

## 配对（本机一次性）

```bash
cd plugins/teachmate
python3 server/__main__.py pair \
  --code <配对码> \
  --teacher-token <WORKBENCH_TOKEN> \
  --base-url http://127.0.0.1:8765
```

令牌保存在 `plugins/teachmate/.data/client_token.json`（scope=teaching.read）。教师可在 TeachMate 设置页查看最后使用时间并一键撤销。

## 运行（由 MCP 客户端启动）

Codex、WorkBuddy 或其他 MCP 客户端读取 `.mcp.json` 后，以 `python3 -m server` 启动本进程（stdio）。
如果客户端不自动读取该文件，可以把同样的 `command`、`args` 和 `env` 配置复制到它的 MCP 配置中。
TeachMate 安装器会优先读取 `.teachmate-plugin/plugin.json`，同时保留 Codex 清单用于跨宿主安装。
`TEACHMATE_PLUGIN_BASE_URL` 必须指向 TeachMate API（默认 `http://127.0.0.1:8765`）。

## WorkBuddy 使用的核心接口

TeachMate 设置页的“插件管理”中提供“连接 WorkBuddy”按钮。点击后会自动启用只读插件能力、
生成连接令牌并展示完整配置，复制配置到 WorkBuddy 即可，不需要手动创建配对码或执行配对命令。
下面的命令行配对流程仍保留，适合自动化部署或没有打开 TeachMate 页面时使用。

先调用 `list_teaching_scopes` 获取学期、班级和考试的真实 ID，再调用
`list_analysis_reports` 查看哪些范围已经生成报告，最后读取选中的报告：

```text
get_latest_analysis_report(
  term_id=<学期 ID>,
  class_id=<班级 ID>,
  exam_id=<考试 ID>,
  identify=false
)
```

该工具只读取 TeachMate 已完成的结构化报告，不会重新分析；`scope` 会同时返回学期、班级
和考试的 ID 与名称，`run` 返回分析完成时间，`report` 返回结论摘要、主要发现、行动建议
和限制说明，`data_quality` 返回本次范围的参与、缺考和缺分统计，`evidence_ids` 可用于按需
调用 `get_evidence` 展开证据。新报告同时返回生成时冻结的 `scope_snapshot`；即使之后修改
成绩，也不会把当前统计拼接到历史报告中。

`class_id` 传入后是强约束：报告运行记录和成绩统计都必须属于该班级；不传则只读取“全体班级”
报告，不会随机取某个班级。学生信息默认匿名化；只有在明确需要生成教师正式材料时才传
`identify=true`。

### 个性化出题

WorkBuddy 可以先调用 `search_students` 找到当前学期的学生，再调用
`get_student_learning_profile` 和 `get_student_practice_context`。后者从 TeachMate 的正式学期
画像、逐题作答、已确认试卷题目和错因评估中组装只读训练上下文，返回薄弱知识点、错因分布和真实错题；
不会直接访问 SQLite，也不会写回画像或练习结果。

推荐调用顺序：

```text
search_students(term_id=<学期 ID>, class_id=<班级 ID>, keyword="张", identify=true)
get_student_learning_profile(student_id=<学生 ID>, term_id=<学期 ID>)
get_student_practice_context(student_id=<学生 ID>, term_id=<学期 ID>, limit=20)
```

`generate-personalized-practice` Skill 会据此生成基础巩固、同类变式和迁移题，并要求将低置信度
错因标记为训练假设，不把它表述成确定诊断。原题只用于理解考点和错误模式，不直接复制。

## 隐私与审计

- 插件令牌仅含 `teaching.read` scope；写能力不因升级自动获得。
- 服务端只保存令牌摘要与权限/时间戳，绝不保存明文或 API Key。
- 越学期、未确认资料正文、原始哈希等一律不可见。
- 撤销令牌后所有工具立即返回 401。

## 手动试点清单（L2-E，需本机安装 Codex CLI）

- [ ] `codex` 能识别三个 Skill（analyze-exam / diagnose-student / build-review-plan）
- [ ] 黄金任务：分析考试 → 数字与 TeachMate 页面差异为 0
- [ ] 负向任务：跨学期访问被拒；撤销令牌后工具立即不可用
- [ ] TeachMate 未启动时，`get_teachmate_status` 返回明确恢复指引
