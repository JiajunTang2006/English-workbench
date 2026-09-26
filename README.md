# English Workbench（英语教学工作台）

**让数据替老师干活，让成长被学生看见。**

面向初中英语教师的**完全本地运行**教学工作台与教学智能体。班级、成绩、原卷与错题、过程性记录统一在一个工作台里管理；内置教学智能体 **TeachMate** 在教师指定的范围内读取数据，产出**可追溯**的分析报告与教学建议；**英语成长森林**把学习参与和相对自身基线的进步记录为可解释的成长事件。

> 当前版本 **0.9.0-beta.2**（Beta，尚未正式发布）。正式程序不内置任何班级、学生或成绩数据。

---

## 下载

### macOS（Apple Silicon）

| 项目 | 内容 |
| --- | --- |
| 安装包 | [EnglishWorkBench_macOS_arm64_Blank_0.9.0-beta.2.dmg](https://github.com/JiajunTang2006/English-workbench/releases/download/v0.9.0-beta.2/EnglishWorkBench_macOS_arm64_Blank_0.9.0-beta.2.dmg) |
| 版本 | 0.9.0-beta.2（空白版） |
| 适用系统 | macOS 12 及以上 · Apple Silicon（arm64） |
| 文件大小 | 约 157 MB |
| SHA-256 | `8a276345814def6de642187e95f07e53f0e7e74cebca42600ce9d66ce05e96d2` |

**空白版说明。** 不预置任何班级、学生、成绩或密钥；首次启动使用独立数据目录 `~/Library/Application Support/English Workbench Blank/`，不会读取或覆盖已有的工作台数据。

**安装。** 打开 DMG，把 `EnglishWorkBench` 拖进「应用程序」。首次打开若提示"无法验证开发者"，在「系统设置 → 隐私与安全性」中允许运行，或右键点击图标选择「打开」。

### Windows（x64）

| 项目 | 内容 |
| --- | --- |
| 安装包 | [EnglishWorkBench_Windows_x64_Blank_0.9.0-beta.2.zip](https://github.com/JiajunTang2006/English-workbench/releases/download/v0.9.0-beta.2/EnglishWorkBench_Windows_x64_Blank_0.9.0-beta.2.zip) |
| 版本 | 0.9.0-beta.2（空白版） |
| 适用系统 | Windows 10 / 11 · x64 |
| 文件大小 | 约 54 MB |
| SHA-256 | `4616325d1fb29d397c92792521fa0989b9d4fd0324d042c0a4eac4adb00b2807` |

**空白版说明。** 与 macOS 版一致，不预置任何班级、学生、成绩或密钥，首次启动使用独立数据目录 `%APPDATA%\English Workbench Blank\`，不会读取或覆盖已有的工作台数据。

**安装（绿色免安装）。** 下载 zip 后**完整解压**（不要只把 exe 拖出来，程序依赖同目录的 `_internal/`），双击 `EnglishWorkBench.exe` 运行，可整体移动到任意目录。首次运行若出现 SmartScreen 提示，点击「更多信息 → 仍要运行」。构建步骤见 [WINDOWS_GUIDE.md](WINDOWS_GUIDE.md)。

### 所有版本

历史版本与校验文件见 [Releases](https://github.com/JiajunTang2006/English-workbench/releases)。

---

## 这是什么

初中英语教学要同时处理日常表现和阶段性测评：默写反映词汇与拼写，背诵、写作、作业记录提供日常信息，考试呈现特定时间与任务条件下的测评结果。这些记录用途不同、解释边界也不同，分散在表格、纸质材料和原卷附件里时，教师需要额外整理、核对和关联，才能形成连续的学情判断。

English Workbench 把这条链路放进一个本地工作台：以**学期、班级、学生**为共同关联保存各类记录，由业务程序完成数据校验与统计计算，再由智能体在教师指定的范围内调用教学工具，形成带证据编号的分析报告与建议，经教师审核后用于讲评、辅导和复习安排。

### 核心能力

| 模块 | 说明 |
| --- | --- |
| 班级 / 学期 / 学生 | 按学期维护档案与在班关系，新学期不会覆盖历史学期 |
| 成绩管理与统计 | 录入或导入考试（Excel/CSV），维护满分、缺考、优秀/合格阈值与 A/B/C 分层线，输出平均分、分布、排名与趋势 |
| 过程性记录 | 默写、背诵、写作与日常作业按轮次记录，与考试结果同屏查看；各类记录保留原有含义 |
| 原卷与错题 | 试卷附件 + 错题记录，保留题号、题型、考点与来源，构成后续分析的数据来源 |
| TeachMate 智能体 | 考试分析、学生个体诊断、复习计划；批量诊断先生成执行方案由教师确认范围与消耗 |
| 英语成长森林 | 以树木阶段与事件记录呈现学习参与、持续投入和相对自身基线的进步，奖励可追溯、可更正 |
| 备份与恢复 | 启动新版时先做 SQLite 一致性备份再迁移；完整备份包含数据库、附件与校验清单 |

### 设计取向

- **开箱即用的低门槛。** 智能体运行所需的基础指令与工具调用已在软件内预置，模型服务通常只需填入 API Key 即可启用；各插件内置任务提示词，教师无需手工编写分析要求。
- **预留的数据接口。** 从设计上保留了与外部数据源和上层智能体的对接空间：既可接入学校数据接口，把成绩等业务数据纳入工作台分析，也可把教学数据对接到 WorkBuddy 等其他智能体，避免能力被锁在单一应用内。
- **本地可控与数据安全。** 教学数据完全保存在本地 SQLite，教师对存储、备份与导出拥有直接控制权；日常记录与本地统计无需联网即可运行。
- **保留用户自由。** 用户可自行安装插件，也可以通过智能体对软件本身进行改造与增强。

### 当前局限

- **尚不成熟。** 目前只做过小范围测试，仍有不少问题需要真实使用才能暴露和修复。
- **没有统一的数据库或服务端。** 不集中管理所有用户的数据，因此没有跨设备的账号体系与云端协作。
- **目前只覆盖单一学科。** 适配其他学科需要调整数据字段、知识条目与评价规则，并重新验证。

---

## 使用流程

1. 启动应用 → 建立当前学期与班级，导入学生名单；
2. 在「成绩管理」录入或导入考试（表头可位于前 12 行，至少提供"学号、姓名、英语"三列），并填写该场考试的 A/B/C 分层线；
3. 录入默写、背诵、作业等过程性记录，上传原卷、登记错题；
4. 在 TeachMate 中发起**考试分析** → 查看带证据的分析发现与建议 → 核对、修改后确认；
5. 对个别或一批学生发起**学生诊断**，按需生成**复习计划**；
6. 在**成长森林**查看奖励来源与更正记录；
7. 定期在「班级与设置」中导出完整备份。

---

## 数据目录与备份

| 平台 | 目录 |
| --- | --- |
| macOS（空白版） | `~/Library/Application Support/English Workbench Blank/` |
| macOS（开发启动器） | `~/Library/Application Support/workbench/` |
| Windows（空白版） | `%APPDATA%/English Workbench Blank/` |

目录包含 `workbench.db`、`backups/`、`imports/`、`exports/`、`attachments/` 和 `logs/`。

完整备份是一个目录而不只是单个 SQLite 文件，包含 `workbench.db`、`attachments/`、`manifest.json`（版本、附件大小与 SHA-256）和 `checksum.sha256`。

```bash
# 校验备份
python3 tools/verify_backup.py /path/to/backup-directory

# 恢复（需先完全退出应用）
python3 tools/restore_backup.py /path/to/backup-directory \
  --data-dir "$HOME/Library/Application Support/workbench"
```

---

## 安全边界

- 只监听 `127.0.0.1`，不对外暴露服务；
- 每次启动使用随机 Token；
- 运行时不需要云服务，AI 能力仅在配置模型 API Key 后启用；
- 前端不直接访问 SQLite，数据库结构变化只通过 Alembic 迁移；
- 数据库升级前自动备份，批量写入使用事务；
- 学生、考试和学期默认归档而非直接删除，历史数据可追溯；
- Excel、ZIP、图标字体和图表组件均为项目内本地资源，不依赖 CDN，断网可用。

---

## 仓库说明

本仓库是**空白版源码快照**，保留应用代码、构建脚本、测试与文档。

- **不包含**：教师/学生数据库、附件、密钥、测试数据、本地临时工作区（`clone_work/`、`design-preview/`）与构建校验产物（`release/`）。
- 回归测试在代码中生成合成数据，不需要真实学生资料。

## 许可与致谢

第三方资源版本与许可见 [workbench-assets/THIRD_PARTY_NOTICES.md](workbench-assets/THIRD_PARTY_NOTICES.md)，完整性校验见 [workbench-assets/checksums.sha256](workbench-assets/checksums.sha256)。

