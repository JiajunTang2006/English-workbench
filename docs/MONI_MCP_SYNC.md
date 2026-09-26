# MONI MCP 学生名册同步

项目内置了 `moni` 只读插件。令牌不写入仓库，也不发送到 TeachMate 前端。

首次配置令牌：

```bash
python3 tools/configure_moni.py
```

脚本会把令牌保存到当前 Workbench 应用数据目录的 `provider/models/moni.key`，权限为 `0600`。也可以在启动 Workbench 前设置 `MONI_MCP_TOKEN` 环境变量；环境变量优先于本地保管库。

同步前需要知道 MONI 中已经授权的 JSON/JSONL 文件路径。建议先用 MONI 的目录/字段说明工具确认路径和字段，再执行预览：

```bash
python3 tools/sync_moni_students.py \
  --file-path /authorized/path/students.jsonl \
  --term-code 2026-fall \
  --term-name '2026 秋季学期' \
  --dry-run
```

确认摘要中的班级和学生数量后，去掉 `--dry-run` 执行正式同步。默认字段为 `student_no`、`name`、`class_name`、`external_id`、`gender`，均可通过参数调整。

同步会幂等写入学期、班级、学生、在班关系和外部 ID 映射，并记录 `SchoolSyncRun` 审计记录。对于 MONI 当前学期，当前授权班级中不在最新快照里的学生和班级会被归档，不删除学生档案或历史成绩；没有 MONI 外部映射的本地班级不受影响。写入后，WorkBench 和 TeachMate 共用同一数据库，TeachMate 的学生列表、学生档案与分析上下文即可使用这些数据。

MONI 返回新的学期 ID/编码时会自动创建并切换到对应学期。同一学生跨学期会复用同一份学生档案，但在每个学期分别建立在班关系；班级优先使用 MONI 的稳定外部 ID 识别，外部 ID 和班级名称都变化时会建立新班级，旧班级保留为历史归档。若外部 ID 不变而只是班级名称变化，则视为同一班级改名；如果外部 ID 变化但名称完全相同，则按学期内唯一班级名称复用原班级。

当前 MONI MCP 工具只读，自动同步只调用读取、列表和查询能力，不会向远端写数据。生产使用前应先在 MONI 侧确认授权范围和字段脱敏策略。

## 考试自动同步

WorkBench 启动器会在后台自动读取 MONI 当前学期的考试数据，不需要老师在前端导入。也可以手动运行同一条链路：

```bash
python3 tools/sync_moni_exams.py --dry-run
python3 tools/sync_moni_exams.py
```

同步内容包括：

- 考试名称、考试日期、考试满分；
- 学生总分、满分、班级排名、年级排名；MONI 返回官方英语年级名次时优先使用。只有当已授权班级覆盖考试分析声明的全年级范围时，才按英语单科成绩补算竞赛排名（同分并列）；覆盖不完整时保留为空，避免把“授权班级内排名”误标成年级排名；
- 英语单科 `subjectTier` 分层：`ELITE`、`KEY`、`GOOD`、`REGULAR` 自动映射为 WorkBench 的 A、B、C、D 层；A/B/C 取对应层英语成绩最低值，并按 0.5 分粒度填入分层线；
- 题号、小题号、得分、满分、得分率、是否答对、选择项；
- 题型、难度、认知层级、知识点、能力点、易错标签；
- MONI 返回的耗时、修改次数、犹豫时间和教学模块（有值才写入）。

同步器不固定教师租户中的班级 UUID、考试 UUID 或英语 `subjectId`（例如不会把
英语写死为 `29`）。它会先发现 `/classes` 或 `/class`，再从科目业务名称识别
英语。常见字段名变化时，先按稳定业务值（如 `ELITE/KEY/GOOD/REGULAR`）识别，
数值字段则结合 `.fields.jsonl` 的字段说明判断；API Key 只用于鉴权，不参与路径
或字段映射。总分、总分层级和班级排名不会被当作英语成绩或英语年级排名。

题目级数据按实际返回写入：如果 MONI 当前快照没有 `question-scores` 行，Workbench 会保留题目结构，但逐题得分为空，不会把缺失当作 0 分。同步使用外部稳定 ID 幂等更新，并保留老师已经手工覆盖的成绩。

自动同步失败不会阻塞 WorkBench 启动；失败信息只进入本地日志。可通过 `MONI_AUTO_SYNC=0` 临时关闭，或用 `--dry-run` 检查读取数量。

## 设置页配置

推荐直接在 WorkBench「班级与设置 → MONI 数据接口（MCP）」中填写 API Key；地址和 MCP JSON 结构已经内置。若需要手动配置，格式如下：

```json
{
  "mcpServers": {
    "moni": {
      "type": "http",
      "url": "https://t-mcp.fufenxi.com/api/t-mcp/mcp",
      "headers": {
        "Authorization": "Bearer 你的令牌"
      }
    }
  }
}
```

设置页提供「测试连接」和「测试并同步学生数据」按钮。令牌只保存在本机安全存储中，页面读取时会自动遮蔽。
