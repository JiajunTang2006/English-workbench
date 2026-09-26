# L0 基线冻结记录（Baseline Freeze Record）

> 对应长期开发方案 `TEACHMATE_LONG_TERM_DEVELOPMENT_PLAN.md` 第 L0 阶段（基线冻结 / 协议版本化）。
> 本文件在 **开始 L0/L1 改动之前** 记录代码库冻结点，作为后续每一阶段迁移、回滚与回归对比的锚。

## 1. 冻结点事实（Freeze Facts）

| 项 | 值 | 来源 |
| --- | --- | --- |
| 分支 | `main` | `git rev-parse --abbrev-ref HEAD` |
| HEAD | `8144fdf` | `git rev-parse --short HEAD` |
| 未提交改动总数 | **118** | `git status --porcelain \| wc -l` |
| └ 已修改（M） | 75 | `git status --porcelain` 首列统计 |
| └ 未跟踪（??） | 43 | 同上 |
| 应用版本 | `0.9.0-beta.2` | `backend/app/version.py::APP_VERSION` |
| 数据库迁移号 | `20260817_0016` | `backend/app/version.py::SCHEMA_REVISION` |
| 冻结日期 | 2026-08-22 | 本文件创建时间 |

> ⚠️ **R1 风险已实锤**：基线时已有 118 个未提交改动（含 75 个已修改文件），远大于方案预期。
> 因此本轮 **不执行 `git commit`**，只修改文件；所有 L0/L1 改动以「叠加在未提交改动之上」的方式落地，
> 由本文件与 `L0_RISK_REGISTER.md` 记录，便于后续统一评审与原子提交。

> 📌 **SCHEMA_REVISION 推进说明**：冻结值为 `20260817_0016`。后续里程碑新增数据库迁移会推进该值——
> L3 新增迁移 `20260822_0017_l3_vision_tables.py`（两张视觉表），已将 `SCHEMA_REVISION` 同步 bump 至
> `20260822_0017`；L4 新增迁移 `20260822_0018_l4_document_export.py`（两张文档导出表），已 bump 至
> `20260822_0018`。冻结值仅代表 L0/L1 起点，不代表最终值；每阶段迁移号见各阶段记录文档。

## 2. 本轮（L0 + L1）已落地的改动

### L0（基线冻结 / 协议）
- `backend/app/agent/config.py`
  - 新增 4 个默认关闭的功能开关：`multipart_ui_enabled`、`codex_plugin_enabled`、`document_export_enabled`、`remote_document_provider_enabled`。
  - 新增 `FEATURE_FLAG_MILESTONES`（开关→里程碑映射，供设置页分组）。
  - 新增 `FEATURE_FLAG_DEPENDENCIES`（fail-closed 依赖：`remote_document_provider_enabled` → `document_export_enabled`）。
  - 新增 `_apply_feature_flag_dependencies()`（依赖未开启时强制关闭，避免不一致状态）。
  - 环境变量映射：`AGENT_MULTIPART_UI_ENABLED` / `AGENT_CODEX_PLUGIN_ENABLED` / `AGENT_DOCUMENT_EXPORT_ENABLED` / `AGENT_REMOTE_DOCUMENT_PROVIDER_ENABLED`；构建后统一跑依赖解析。
  - `restore_runtime_config_from_db()` 兼容历史持久化配置：补齐缺失开关键（取默认关闭）并重跑依赖解析。
- 协议 schema（`L0-2`，见 `docs/L0_PROTOCOL_SCHEMAS.md`）：`AttachmentUploadAPI v1`、`TeachMatePluginAPI v1`、`VisionResult v1`、`DocumentSpec v1`。

### L1（前端多部件流式上传）
- 后端补强（`backend/app/services/attachment_uploads.py` 新增，`backend/app/routers/attachments.py` 扩展）：
  - `/api/v1/attachments/capabilities` 能力声明接口（限制、格式、稳定错误码、开关、指标）。
  - 统一错误码 `UploadErrorCode` + `upload_http_error()`（响应体 `{detail:{code,message}}`）。
  - 多部件路径补充：MIME/扩展名一致性门禁、并发上限（429 `UPLOAD_BUSY`）、启动清理残留临时文件、上传指标（不记录正文）。
- 前端（`workbench-assets/`）：
  - `teachmate-api.js`：新增 `uploadAttachment()`（XHR + FormData，带进度回调 `onProgress` 与取消 `signal`）；不手动设 `Content-Type`。
  - `teachmate-interactions.js`：重写 `tmAttachFile` → 按 `/capabilities` 读取限制与开关；多部件开启时走 `uploadAttachment`（进度+取消），关闭时回退 Base64；新增 PNG/JPEG；状态 `uploading`/`validating`/`parsing`/`pending_ocr`/`ready`/`failed`；幂等去重复用提示。
  - `teachmate-views.js`：附件 chip 渲染支持上传进度条与「取消上传」。
  - `teachmate.css`：上传进度条样式。
- 测试：`backend/tests/test_l1_multipart_upload.py`（34 例，全部通过）。

## 3. 既有测试失败（Pre-existing Failures，非本轮引入）

以下失败在 L0/L1 改动之前即存在，与本次改动无关；不计入 L0/L1 回归阻断项，
但应在发布门禁中单独登记、随本轮一并修复或显式豁免。

| 层 | 命令 | 失败数 | 失败用例（prior-session 观测） | 与 L0/L1 关系 |
| --- | --- | --- | --- | --- |
| 前端单测 | `npm test` | 2 | `tests/test_sidebar_toggle.js`、`tests/test_p16_behavior.js::P1-6`（考试绑定默认关闭、显式 opt-in 才加数字 exam_id） | 无关（UI 组件测试） |
| 后端单测 | `pytest` | 1 | `test_b3_11_whitelist_hardening.py::test_cordis_generated_plugin_files_exist`（缺 pnpm 路径 `teachmate-runtime/harness/node_modules/.pnpm/.../dsh-token-meter/lib/index.js`） | 无关（Codex 白名单测试，属 L2 准备） |
| e2e | `npm run test:e2e` | 4 | `tests/e2e/teachmate_flow.spec.js` U5-05 `#tmHeaderSearchBtn` 不可见（TeachMate UI 改版遗留） | 无关（UI 改版遗留） |

> 注：失败计数随用户持续改动可能变动，发布前以 `python3 tools/release_check.py` 实跑为准（见 `L0_RISK_REGISTER.md`）。

## 4. 冻结约定（Freeze Conventions）

1. **协议字段只增不删**：`AttachmentUploadAPI v1` 等 schema 仅新增兼容字段时保持小版本；删除/重解释字段必须升大版本。
2. **开关默认全关**：任何未达发布门禁的能力默认 `False`；关闭 Agent 后原有工作台完全不受影响。
3. **失败关闭（fail-closed）**：依赖开关的前置未开时，依赖开关强制关闭。
4. **不在协议里暴露**：ORM 内部字段、绝对路径、API Key、未确认的模型输出内容均不得进入前端协议/日志。
5. **不提交**：本轮改动仅落盘，待统一评审后再由用户决定是否原子提交。

## 5. 回滚锚点

- 若 L1 多部件路径出问题：将 `AGENT_MULTIPART_UI_ENABLED` 保持未设置（默认 `False`），前端自动回退 Base64，无需代码回滚。
- 若后端 `attachment_uploads.py` 引入回归：删除该模块并在 `attachments.py` 还原 `/capabilities` 与 `upload_attachment` 为多部件前的实现（保留 B2-04 原始流式逻辑）。
- 若前端回退需要：将 `teachmate-interactions.js` 的 `tmAttachFile` 还原为 Base64 版本（git 历史中存在）。
