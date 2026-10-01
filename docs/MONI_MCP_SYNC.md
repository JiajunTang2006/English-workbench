# 学校数据源：MONI 与自定义 MCP

项目内置了 `moni` 只读插件。令牌不写入仓库，也不发送到 TeachMate 前端。

MONI 只是内置的「已登记数据源」之一。换学校、或对方接口与 MONI 不同时，不必改代码：
在设置页新增一个自定义 MCP 数据源，填端点、鉴权、路径模板与字段映射即可，详见文末
《通用 MCP 数据源》。两个来源共用同一段入库逻辑，写出的数据口径一致。

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

## 通用 MCP 数据源

内置 MONI 只认它自己的接口。为了能接任意学校的 MCP 服务，设置页的
「其他学校数据源（自定义 MCP）」允许教师自己填一份配置，把对方接口映射成与 MONI
相同的数据口径。MONI 与自定义数据源共用同一段入库逻辑（学期、班级、学生、在班关系、
外部 ID 映射与 `SchoolSyncRun` 审计），因此同步出来的数据在后端没有区别。

数据源列表里，内置 MONI 标「内置」且不可编辑；由导入流程自动登记的来源（`mock`、
旧版 `mcp`）标「自动登记」，它们没有字段映射配置，也不需要配。

### 需要填什么

| 项 | 说明 |
|---|---|
| 来源标识 | 数据源键名，小写字母、数字、点、下划线或连字符，长度 2–64，以字母或数字开头，例如 `hz-001`；`moni`、`mock` 为内置保留，不能占用 |
| 名称 | 显示名，例如「杭州某校学生数据」 |
| 端点 | MCP 服务地址，必须是 `http(s)://` |
| 鉴权 | 默认 Bearer 令牌；也可在请求头里加固定头（如 `X-Tenant`）。名字里带 token/secret/key 等字样的请求头在页面上一律遮蔽回显 |
| 路径模板 | 六类取数路径：`term`、`classes`、`roster`、`exams`、`questions`、`students`。其中 `classes`（班级列表）必填，`exams` 与 `roster` 至少填一个，否则没有可同步的内容。模板里可用 `{class_id}`、`{exam_id}` 占位符，由适配器逐层替换 |
| 学期 | 学期外部 ID / 名称 / 编码；上游不提供当前学期信息时用它兜底 |
| 科目过滤 | 只同步指定科目的成绩，避免把语数等其它科目当成英语导入 |
| 满分兜底 | 上游不给满分时使用的默认满分 |
| 字段映射 | 把上游字段名映射到逻辑字段，见下 |
| 分类取值对照 | 把上游的取值口径归一到 WorkBench 的统一叫法（如「从句」→「宾语从句」、「甲等」→ A），见下 |

### 字段映射写法

键是逻辑字段，值是候选字段路径，**写多个用逗号分隔，取第一个非空**：

```
exam.external_id = examId, exam_id, id
student.name     = name, studentName
```

路径支持 `a.b`、`a[0].b`、`a["k"]`、`$.a.b` 几种写法。约定：

- 空字符串与空列表算「没有值」，但数字 `0` 是真实值，不会被当成缺失；
- 取不到的字段一律留空，不用 0 顶替；
- 关键字段缺失时同步前就会报错并指出是哪个字段，而不是悄悄少导入一批人。

必需字段：`class.external_id`、`class.name`、`student.external_id`、`student.name`；
涉及考试时还需 `exam.external_id`、`exam.name`。可映射的逻辑字段还包括
`term.*`、`class.grade`、`exam.date`、`exam.full_score`、`exam.kind`、
`exam.paper_revision`、`exam.tier_a_cutoff`/`tier_b_cutoff`/`tier_c_cutoff`、
`question.*`（含难度、认知层级、知识点、能力点、易错点、教学模块）、
`item.*`（逐题得分、选择项、耗时、修改次数、犹豫时间、易错点、教学模块）、
`student.student_no`、`student.gender`、`student.status`、`student.total_score`、
`student.class_rank`、`student.grade_rank`、`student.global_rank`、`student.tier`。
设置页新建数据源时会预填一份常见命名模板，可以按对方实际返回改，用不到的行直接删掉。

### 分类取值归一（对照表）

字段**名**对上了，字段**取值**的口径还可能对不上：同一类东西，甲校叫「宾语从句」，
乙校叫「从句」或 `Object Clause`；分层甲校叫「甲/乙/丙」，WorkBench 只认 A/B/C/D。
设置页的「分类取值对照（可选）」就是解决这一层的，每行写一条：

```
# 以 # 开头的行会被忽略
question.knowledge: 宾语从句 = 从句, Object Clause
question.difficulty: 中等 = 3, medium
exam.kind: entrance = 入学考, 分班考
```

格式是「逻辑字段: 统一叫法 = 上游叫法1, 上游叫法2」。多个上游叫法用逗号、顿号或分号
分隔。查表时上游取值做 NFKC 归一（全角转半角）、去首尾空白、大小写不敏感，所以
`ELITE` / `elite` / `ＥＬＩＴＥ` 视为同一个值。可以填对照表的字段是：

- `question.difficulty`、`question.cognitive`、`exam.kind`
- `question.knowledge`、`question.ability`、`question.pitfall`、`question.teaching_block`
- `item.pitfall`、`item.teaching_block`
- `student.tier`（分层别名，目标只允许 A/B/C/D）

约定：

- 对照表里的字段必须先在字段映射里说明读哪一列，否则保存会被拒绝，并提示补哪一项；
- 标签类字段（知识点、能力点、易错点、教学模块）按元素逐个查表，而不是把整个列表
  拼成一个字符串去匹配；归一后按顺序去重；
- **没有写进对照表的上游取值按原样保留**，不会被猜成别的东西——这样漏配的别名事后能
  在数据里看见，而不是被静默改写成错误分类；
- 同一个上游叫法被映射成两个不同统一值时直接报错，不做「谁先谁赢」的静默取舍；
- 对照表不会写回字段映射：回显给设置页的永远是教师原样填的那一份。

学生分层的别名可以单独写在「学生分层别名」里，每行「上游叫法 = A」，例如
`优秀 = A`。它与内置 MONI 的分层表叠加（同名覆盖），`ELITE`/`KEY`/`GOOD`/`REGULAR`
以及 `A`/`B`/`C`/`D`、`优秀`/`良好`/`合格`/`一般` 默认已经认得。若某场考试的分层字段
有值但一条都归一不到 A/B/C/D，同步会在 warnings 里明确提示，分层线保持为空而不是猜。

对应的配置字段是 `value_aliases` 与 `tier_aliases`：

```json
{
  "value_aliases": {
    "question.knowledge": {"宾语从句": ["从句", "Object Clause"]},
    "exam.kind": {"entrance": ["入学考", "分班考"]}
  },
  "tier_aliases": {"优秀": "A", "良好": "B"}
}
```

### 令牌

自定义数据源的令牌单独保存在本机保管库，档案名是 `school-<来源标识>`；内置 MONI 沿用
历史上的 `moni` 档案名。两者互不覆盖，删除数据源时会一并清掉登记行、物化插件与令牌，
不留孤儿。

### 接口

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/v1/school-sync/sources` | 列出所有数据源（配置遮蔽、令牌只回「是否已配置 + 尾号」） |
| POST | `/api/v1/school-sync/sources` | 新建自定义数据源 |
| GET | `/api/v1/school-sync/sources/{key}/config` | 读取单个数据源配置 |
| PUT | `/api/v1/school-sync/sources/{key}/config` | 修改配置 |
| DELETE | `/api/v1/school-sync/sources/{key}` | 删除自定义数据源 |
| POST | `/api/v1/school-sync/sources/{key}/test` | 测试连接 |
| POST | `/api/v1/school-sync/sources/{key}/sync?dry_run=true` | 同步（`dry_run` 只读取不写库） |

原有的 `/api/v1/school-sync/moni/config|test|sync` 保留为兼容别名，行为不变。
