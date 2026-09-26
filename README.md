# English Workbench

当前版本：0.9.0-beta.2（Beta，尚未正式发布）。正式程序代码不内置班级、学生或成绩数据；自动化测试使用 `tests/fixtures/` 与 `test_data/` 中的匿名合成数据，`release/` 只保留当前空白安装包及其更新基线。

## 成绩导入约定

支持 Excel（`.xlsx`/`.xls`）和 CSV 转换后的表格。表头可以位于前 12 行，至少提供“学号、姓名、英语”三列；“班级”可直接提供，年级排名优先识别“英语排名”，也兼容“年级排名/年级名次/grade_rank”。缺考可在“缺考/出勤状态”列填写。一次选择多个班级文件会合并为同一场“入学考试”，并自动建立第一场基线考试。

红绿表的填色不是程序依赖项。每场考试的 A/B/C 三条线在成绩管理中单独填写，保存后该场考试的 ABCD 分层、优秀率和及格率会同步更新。

## 版本升级、备份与恢复

数据存放在系统应用数据目录的 SQLite 数据库中，不与 HTML/代码混在一起。启动新版时会先对旧数据库做完整性校验备份，再执行数据库迁移；旧版 JSON 备份也可以从“班级与设置 → 导入 JSON 备份”恢复。升级失败时保留迁移前备份，不会覆盖原数据。

完整备份是一个目录，不只是单独的 SQLite 文件，包含：

- `workbench.db`：SQLite 一致性备份；
- `attachments/`：数据库中登记的实际附件文件；
- `manifest.json`：数据库版本、附件大小和 SHA-256；
- `checksum.sha256`：数据库校验和。

生成备份后可以先执行校验：

```bash
python3 tools/verify_backup.py /path/to/backup-directory
```

恢复必须在应用完全退出、没有进程占用数据库时执行。恢复前会再次校验备份，并先把当前数据目录保留为带时间戳的安全副本：

```bash
python3 tools/restore_backup.py /path/to/backup-directory \\
  --data-dir "$HOME/Library/Application Support/workbench"
```

命令成功后重新启动应用，检查当前学期、workspace 和附件下载。输出中的 `safety_copy` 是恢复前的数据副本，确认恢复结果和附件均正常后再按运维保留策略清理；如果恢复失败，正式数据不会被损坏。应用启动时还会重试数据库中记录的待完成文件操作，因此附件删除或移动遇到临时文件系统错误不会静默丢失。

完全本地运行、面向单教师的初中英语教学工作台。当前启动器默认打开基于 DeepSeek Harness 的 TeachMate 前端，数据仍由现有 FastAPI 保存到 SQLite；原 WorkBench 页面保留为可回退入口。

## 当前版本

当前重构里程碑为 `0.9.0-beta.2`，已实现：

- Alembic 数据库迁移，兼容旧 `create_all()` 数据库；
- 学期、学期班级和学生在班关系；顶部可切换、新建，设置页可编辑、归档和恢复学期；
- 目标分、薄弱项、家长电话和座位等随学期保存，不会由新学期覆盖历史学期；
- 工作区状态、班级、学生与考试查询按学期隔离；
- 旧工作区写入会安全增量同步规范化核心表，不会因部分状态缺失而自动删除或归档数据；
- 前端已拆为 HTML、样式、核心数据层、视图层和交互层资源；
- 数据库升级前自动创建 SQLite 一致性备份；
- 学生与考试支持显式归档、恢复和可审计的清理操作；永久删除学期前自动备份、校验并记录影响范围；
- 原卷与错题附件改为 SQLite 元数据 + 本地文件存储，支持本地上传、预览和下载，不再把大文件塞进完整工作区 JSON；
- 前端写入统一经过保存队列，失败自动回滚，遇到修订号冲突会阻止覆盖并提示重新加载；
- 原来的页面、布局、菜单和 12 个模块可直接运行在数据库模式；
- 正式 HTML 不再内置任何学生姓名、学号、成绩或家长电话，初始班级和学生均为空；
- 优秀线和及格线按满分百分比配置，仪表盘按当前考试满分自动换算；
- 新增或修改考试时一并填写该场考试的 A/B/C 年级分层线，不使用固定分数或全局分数线代替；
- 教师姓名与学科设置会同步更新页面标题；
- Excel、ZIP 和图标字体均随项目本地提供，断网不影响页面、导入导出或图标；
- 服务地址和端口集中配置，应用版本与数据库版本分别集中维护；
- 完整工作台状态 API，带修订号冲突检测和变更日志；
- 班级、学生、设置 API；
- 考试、题型、总分、缺考状态、并列排名和统计 API；
- 学生年级排名、动态班级排名及“考试+班级”年级名次数据结构；
- 分层分析三卡片、每100名年级排名分布、考试级 A/B/C/D 分布和三组历次趋势图；
- 学生综合档案接口，提供历次考试、得分率和排名；
- 启动器自动生成 Token、启动 TeachMate Web 和 Python 数据服务并打开新的教学页面；
- 旧 JSON 可完整写入工作台状态，并同步迁移班级、学生、设置、考试和成绩；
- 旧版页面的统计口径、Excel 表头识别、HTML/XML 转义等安全修复。

默写、背诵、写作、错题、教学干预、待办和自定义表目前已能随完整状态保存到 SQLite，后续仍需逐个拆成规范化业务表。

> **发布状态说明：** 当前为 `0.9.0-beta.2`，尚未正式发布。`release/` 中的桌面包属于本地构建产物，应由当前源码和对应平台的构建脚本重新生成；Windows EXE必须在 Windows 10/11 x64 上构建。

## 启动方式

### TeachMate 默认入口

启动器会让 TeachMate Harness Web 占用原来的浏览器入口端口，并让旧 WorkBench FastAPI 在相邻内部端口提供教育数据。浏览器只打开 TeachMate，SQLite 数据和原有教育 API 保持不变。

macOS：

```text
start_macos.command
```

Windows：

```text
start_windows_dev.bat
```

TeachMate 启动器会：

1. 优先使用项目 `.venv`；
2. 生成本次启动的随机 Token；
3. 检查并备份、升级 SQLite；
4. 让 TeachMate 占用原来的浏览器入口端口（默认 `127.0.0.1:8765`）；
5. 将 WorkBench API 放到相邻内部端口（默认 `127.0.0.1:8766`）；
6. 优先打开内嵌的 WorkBench 桌面窗口（未安装 `pywebview` 时自动回退浏览器），并将本次 Token 传给教育 Bridge；
7. 关闭桌面窗口只隐藏到应用菜单/系统托盘；macOS 再次点击 Dock/Finder 中的应用会恢复窗口。使用 `Command + Q`、应用菜单“退出”或“完全退出”时才停止 TeachMate 和 Python 数据服务。

也可以在终端启动 TeachMate：

```bash
python3 launcher.py
```

首次使用前，需要在 Harness 目录完成一次前端构建：

```bash
cd vendor/deepseek-harness-upstream
pnpm run build
cd ../..
python3 launcher.py
```

如需临时回到旧 WorkBench 页面：

```bash
WORKBENCH_FRONTEND=legacy python3 launcher.py
```

可用 `TEACHMATE_UI_PORT` 和 `WORKBENCH_BACKEND_PORT` 覆盖前端、内部 API 端口。

当前直接替换模式要求本机已有 Node.js，并且 Harness 已完成构建；macOS/Windows 安装包还需要把精简后的 Node/Harness 运行时一起打包，这一项暂不影响源码启动器使用。

要让安装包也直接打开 TeachMate，先生成随包分发的运行时（约数百 MB，构建时需要联网安装生产依赖）：

```bash
python3 tools/build_teachmate_runtime.py
```

然后运行 `build_macos_app.sh`。Windows 版直接双击 `build_windows.bat`，脚本会在缺少 Windows 专用运行时时自动安装 pnpm 并执行运行时构建。

## 首次安装

Python 3.11（开发与正式基线版本）：

```bash
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
# Harness SDK 来自仓库内 vendor/（不从 PyPI 下载）
python -m pip install -e vendor/deepseek-harness-upstream/python/sdk-runtime
python -m pip install -e vendor/deepseek-harness-upstream/python/sdk
# 其余依赖按 backend/requirements.lock 固定版本安装
python -m pip install -r backend/requirements.lock
# 如果希望在本机直接使用内嵌桌面窗口：
python -m pip install 'pywebview>=5.3,<7'
npm install
```

桌面软件打包和 WebView 行为见 [docs/DESKTOP_APP_BUILD.md](docs/DESKTOP_APP_BUILD.md)。

Windows 激活环境时使用 `.venv\Scripts\activate`。

Python 版本兼容性：正式基线为 Python 3.11；3.13 作为可选兼容验证（未纳入 CI 门禁）。

依赖事实源与锁定：

- 声明：`backend/pyproject.toml`（运行依赖 `[project].dependencies`，测试依赖 `[project.optional-dependencies].test`）；
- 固定版本：`backend/requirements.lock`（在全新 Python 3.11 虚拟环境中安装 requirements.txt 后生成，不含无关包）；
- Harness SDK（`deepseek-harness-sdk` / `deepseek-harness-runtime-bin`）明确来自 `vendor/`，不来自 PyPI；
- 如果要主动升级依赖，请先在全新虚拟环境中更新锁定文件，再重新运行完整测试，再制作交付包。

## 开发运行

后端：

```bash
WORKBENCH_DATA_DIR=/path/to/dev-data python -m backend.app
```

旧 WorkBench 页面仍由后端在 `/workbench` 托管；TeachMate 由 Harness WebServer 托管。主机和入口端口可用 `WORKBENCH_HOST`、`WORKBENCH_PORT` 修改，例如：

```bash
WORKBENCH_PORT=9000 python3 launcher.py
```

## 完全离线运行

正式工作台运行时不会向 CDN 或 Google Fonts 请求资源。Excel 处理、ZIP 压缩和图标字体位于 `workbench-assets/`，安装完成后即使电脑断网也可正常使用。第三方资源版本和许可见 `workbench-assets/THIRD_PARTY_NOTICES.md`，完整性校验见 `workbench-assets/checksums.sha256`。

## 数据目录

macOS：

```text
~/Library/Application Support/workbench/
```

Windows：

```text
%APPDATA%/workbench/
```

空白桌面发行版使用 `%APPDATA%/English Workbench Blank/`，开发启动器默认使用上面的 `workbench/`。目录包含 `workbench.db`、`backups/`、`imports/`、`exports/`、`attachments/` 和 `logs/`。程序也会兼容读取改名前的旧数据目录，升级后不会丢失已有数据。开发时用 `WORKBENCH_DATA_DIR` 指定独立目录，不要使用正式数据做测试。

## 迁移旧数据

如有旧版 JSON 备份，可以直接在数据库版设置页导入，或执行：

```bash
WORKBENCH_DATA_DIR=/path/to/data python -m tools.import_legacy_json /path/to/backup.json
```

当前迁移范围：

- 原页面的完整工作台状态（所有 12 个模块）；
- 同时规范化班级、学生、教师与分数线设置、考试和英语总分。

迁移按源文件 SHA-256 防止同一备份重复导入，并输出读取、新增、更新、跳过、错误及各领域记录数。也可以直接在数据库版页面点击“导入旧版 JSON”。正式切换前仍需保留旧 JSON 原文件。

## 测试

全部测试使用项目内匿名 fixture 或独立临时数据目录，绝不读写正式应用数据目录；不会为测试关闭鉴权。

```bash
# JavaScript 语法检查（前端资源 + 工具脚本）
npm run check

# 前端 Node 回归与安全测试（118 项，jsdom 环境）
npm test

# 后端测试（1089 项：迁移、备份、API、代理、后台任务、学校同步、视觉和附件解析）
npm run test:backend

# 浏览器端到端测试（22 项 = 11 用例 × desktop/narrow）
# 自动拉起临时测试服务：独立数据目录 + 固定测试 Token + 端口 18323，
# 无需手工启动服务或输入 Token；结束后自动关闭并释放端口
npm run test:e2e

# 统一发布质量门禁（环境/版本 → 语法 → 前端 → 后端 → 空库迁移冒烟 →
# e2e → 依赖完整性 → 敏感文件检查 → 生成 release-manifest.sha256）
# 任一阶段失败返回非零；全部通过才更新发布校验清单
npm run release_check
```

Windows EXE 稳定版构建：

```text
build_windows.bat
```

需要 Windows 10/11 x64 和 Python 3.11 x64，默认不需要 Node.js。脚本会生成 `dist\EnglishWorkBench\EnglishWorkBench.exe` 与可分发的 `release\EnglishWorkBench_Windows_x64.zip`；详见 [WINDOWS_GUIDE.md](WINDOWS_GUIDE.md)。实验性 Harness 构建需显式运行 `build_windows.ps1 -WithHarness`，并使用 Node.js 22.19 或更高版本。

发布前 `npm run release_check` 必须全部通过。

## 安全边界

- 只监听 `127.0.0.1`；
- 每次启动使用随机 Token；
- 运行时不需要云服务；
- 前端不直接访问 SQLite；
- 数据库结构变化只通过 Alembic；
- 数据库升级前自动备份；
- 批量写入使用事务；
- 学生、考试和学期默认归档，不直接删除历史数据；
- Excel、ZIP、图标字体和图表组件均为项目内本地资源，不依赖 CDN。
