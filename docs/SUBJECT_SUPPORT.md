# 学科支持（全学科化）

老师首次进入软件时先选一次任教学科，之后界面用语与功能模块按学科切换。
**数据层键名一律不变**，所以换学科只是换一套说法，不会动到任何历史数据。

## 1. 内置学科

初中全科 11 项，注册表在 `backend/app/services/subjects.py` 的 `SUBJECTS`：

| key | 名称 | 顶栏默认学科名 |
| --- | --- | --- |
| `chinese` | 语文 | 初中语文 |
| `math` | 数学 | 初中数学 |
| `english` | 英语 | 初中英语 |
| `physics` | 物理 | 初中物理 |
| `chemistry` | 化学 | 初中化学 |
| `biology` | 生物 | 初中生物 |
| `politics` | 道德与法治 | 初中道德与法治 |
| `history` | 历史 | 初中历史 |
| `geography` | 地理 | 初中地理 |
| `it` | 信息技术 | 初中信息技术 |
| `other` | 其他 | 初中 |

老师在下拉里选，不需要手输。`other` 的词表退化成不带学科前缀的通用措辞
（"总分""入学成绩""分数趋势"），避免出现"其他总分"这类读起来别扭的文案。

## 2. 三个可裁剪模块

`dictation`（默写）/ `recite`（背诵）/ `writing`（写作）默认**只对语文、英语开启**，
其余学科不会出现这三个入口。要给某个学科开启，把该学科的 `modules`
从 `_CORE_ONLY` 改成 `_FULL_MODULES` 即可，其它代码不用动。

模块标题也可以按学科覆盖（`module_labels`）。语文当前是：

| 模块 key | 英语显示 | 语文显示 |
| --- | --- | --- |
| `dictation` | 默写成绩 | 古诗文默写 |
| `recite` | 背诵成绩 | 课文背诵 |
| `writing` | 写作成绩 | 作文训练 |

## 3. 界面文案词表

每个学科声明一份 `labels`，键固定为 `LABEL_KEYS`：

| 键 | 英语取值 | 用在哪里 |
| --- | --- | --- |
| `score_total` | 英语总分 | 成绩表列名、考试类型选项、导出列名 |
| `entrance_score` | 入学英语 | 名册表头、学生档案、仪表盘 KPI、导出列名 |
| `score_column` | 英语成绩 | 成绩表编辑单元格的可读标签、数据源科目过滤提示 |
| `score_short` | 英语分数 | 学生明细表头、趋势图系列名 |
| `score_single` | 英语单科成绩 | 数据源未返回单科成绩时的提示 |
| `score_trend` | 英语分数趋势 | 趋势图标题 |
| `score_ranking` | 英语排名 | 导入识别列名、导出列名、使用教程 |
| `exam_default` | 英语考试 | 考试历史卡片标题、空态文案 |
| `ability_disclaimer` | 不代表英语水平 | 成长森林免责说明 |

**英语取值必须逐位不变**（`backend/tests/test_subjects.py` 有专门断言），
这是回归基线：引入学科不能改动任何既有文案。

## 4. 数据层不动

以下键名保持原样，与学科无关：

- `exam.scores[*].英语`（工作台快照里的分数键）
- `enrollment.entrance_english`（数据库列名）
- `exam_type = english_total`（考试类型枚举值）

导出到 Excel 的**列名**会跟着学科变（例如"语文总分"），但导入识别同时保留历史英语写法，
所以老文件仍然能导入。识别列表由 `subjectScoreImportAliases()` /
`subjectRankImportAliases()` 生成，逻辑是"当前学科写法优先，历史英语写法兜底"。

## 5. 设置与接口

- 学科存在后端 `AppSetting.subject_key`（**安装级**，不是学期级）。老师选一次，
  新建学期不用重选，要改在设置页改。
- `GET /api/v1/subjects` 返回 `{ subjects, current, chosen, default }`：
  `chosen` 表示老师是否显式选过（前端据此决定要不要弹选科窗口）。
  只要库里写过 `subject_key` 就算选过，即便取值是脏值——脏值会被归一为默认学科，
  但不会反复弹窗打扰老师。
- `PATCH /api/v1/settings` 接受 `subject_key`。写路径**严格校验**：非法 key 直接
  422 并给出可执行的错误提示（列出可选学科）；读路径**容错**：旧值/脏值回退默认学科。
- `SettingsRead.subject`（自由文本的"学科名称"）保持原样，只用于顶栏显示。
  老师可以在设置页把它改成"七年级语文"这类写法；顶栏优先显示老师手写的值，
  没写才用学科默认名。切换学科时，只有当这个字段为空或还等于旧学科默认名，
  才会被新学科默认名替换，不会覆盖老师手写的值。

## 6. 新增一个学科

1. 在 `backend/app/services/subjects.py` 的 `SUBJECTS` 里加一条（key / label /
   teacher_subject_default / modules / module_labels / question_types / labels）；
2. 跑 `backend/tests/test_subjects.py`，注册表完整性用例会自动检查文案键是否齐全、
   模块 key 是否合法；
3. 前端不需要改动——清单、词表、模块开关都从 `GET /api/v1/subjects` 来。

## 7. 相关文件

| 位置 | 作用 |
| --- | --- |
| `backend/app/services/subjects.py` | 学科注册表（唯一数据源） |
| `backend/app/schemas/entities.py` | `SettingsRead/Patch.subject_key`、`SubjectsRead` |
| `backend/app/routers/core.py` | `GET /api/v1/subjects`、设置读写 |
| `workbench-assets/workbench-core.js` | 学科配置块：`subjectText` / `visibleModules` / `setSubjectKey` |
| `workbench-assets/workbench-views.js` | 导航裁剪、顶栏标题、设置页下拉 |
| `workbench-assets/workbench-interactions.js` | 选科弹窗、设置页即时切换、导入导出列名 |
