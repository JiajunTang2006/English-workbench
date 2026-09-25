# English Workbench 文件结构

这是工作台项目的文件说明，方便后续维护、备份和交付。

## 日常运行

- `workbench.html`：前端页面入口。
- `workbench-assets/`：前端样式、脚本和本地第三方资源。
- `backend/`：本地 API、SQLite 数据库迁移和服务代码。
- `backend/requirements.lock`：经过测试的 Python 依赖版本锁定文件。
- `launcher.py`：默认启动 TeachMate Web + Python 数据服务；`WORKBENCH_FRONTEND=legacy` 可回退旧页面。
- `windows_launcher.py`：桌面启动器使用的 TeachMate + Python 双进程入口。
- `teachmate_runtime.py`：TeachMate Harness 启动、端口分配和退出清理。
- `blank_launcher.py`：空白版启动入口，会使用独立的空白数据目录。

## 启动与打包

- `start_macos.command`：Mac 本地启动脚本。
- `start_windows_dev.bat`：Windows 开发环境启动脚本。
- `build_macos_app.sh`：生成 Mac 应用。
- `build_windows.bat`、`build_windows.ps1`：在 Windows x64 上自动准备独立环境，默认使用稳定 Python Agent 生成 EXE 与分发 ZIP。

## 测试与示例数据

- `tests/`：功能和回归测试脚本。
- `test_data/`：导入测试用的学生、考试和成绩样例数据。
- `tools/`：语法检查、数据导入和备份校验工具。

## 交付文件

- `release/EnglishWorkBench_macOS_arm64_Blank_0.9.0-beta.2.zip`：当前 macOS Apple Silicon 空白版。
- `release/EnglishWorkBench_Windows_x64_Blank_BuildSource_0.9.0-beta.2.zip`：精简 Windows 空白构建源码包；需在 Windows 10/11 x64 上运行 `build_windows.bat` 生成 EXE。
- `release/EnglishWorkBench_Blank_0.9.0-beta.2.update-baseline.json` 与两个平台的 `*.contents.sha256`：后续差分更新使用的版本基线。

当前后端迁移已到 `20260901_0027`，原卷与错题附件的文件内容存放在数据目录的 `attachments/`，SQLite 只保存元数据、校验值和关联学期；数据库提交后的文件移动/删除由 `pending_file_operations` 持久化并支持启动重试。视觉分析、学校同步、Token 用量、学生画像与四路批量分析均已纳入当前迁移链。

## 文档和开发记录

- `docs/`：当前架构、开发计划、产品说明与构建文档。
- `docs/development-history/`：精选的关键开发过程与长期维护笔记。
- `docs/reviews/`：保留的前后端审查报告。

## 构建依赖

- `backend/requirements.lock`：Python 开发与打包依赖，可据此重建 `.venv/`。
- `package-lock.json`：前端测试和工具依赖，可据此重建 `node_modules/`。

`.venv/`、`node_modules/`、`build/`、`dist/` 和打包缓存均为可重新生成内容，不纳入源码归档。正式学生数据始终位于系统应用数据目录，不放入项目；匿名 `test_data/` 和 `release/` 中的当前空白交付包应当保留。
