# L0 风险登记表与回滚手册（Risk Register & Rollback）

> 长期开发方案 L0 阶段交付物。登记跨阶段风险，并为每个阶段给出**迁移 / 备份 / 回滚**具体操作。
> 配合 `L0_BASELINE_RECORD.md` 与 `L0_PROTOCOL_SCHEMAS.md` 使用。

---

## 1. 风险登记表

| ID | 风险 | 等级 | 触发条件 | 缓解（已落地 / 策略） |
| --- | --- | --- | --- | --- |
| **R1** | 未提交改动堆积，无法干净提交/回滚 | **高** | 基线已 118 个未提交（75 M + 43 ??） | 本轮**不提交**，只改文件；改动叠加在基线之上，由本表记录；后续统一评审后原子提交 |
| **R2** | 协议破坏前端兼容 | 中 | 删除/重解释 `capabilities` 字段 | 协议只增不删；大版本变更升 v2（`L0_PROTOCOL_SCHEMAS.md` §5） |
| **R3** | 多部件大文件超时/打满内存 | 中 | 49MB 文件 + 弱网 + 并发 | 后端 1MB 分块流式 + SHA 增量；并发上限 3（超 429）；前端 XHR + 进度 + 取消 |
| **R4** | 功能开关误开连带启用 Agent | 中 | `multipart_ui_enabled` 等被误判 | `agent_enabled` 总闸：子能力在 Agent 关闭时一律 `False`；`is_feature_enabled` 已实现 |
| **R5** | 新能力上线即故障难回退 | 中 | L1 多部件路径异常 | 开关默认全关；关闭即前端自动回退 Base64，**无需代码回滚** |
| **R6** | 版本/迁移号与代码不一致 | 低 | 只改代码不改 `SCHEMA_REVISION` | 前端导出/运行时读 `/api/v1/runtime` 为唯一事实源（`teachMateApi.getRuntime`） |
| **R7** | 发布门禁因既有失败中断 | 中 | 既有 7 例单测/e2e 失败阻断 `release_check` | 既有失败在 `L0_BASELINE_RECORD.md` §3 单独登记，不计入 L0/L1 回归；门禁实跑后人工裁决 |
| **R8** | 依赖开关不一致（远端文档 Provider） | 低 | 开了 `remote_document_provider_enabled` 但没开 `document_export_enabled` | `FEATURE_FLAG_DEPENDENCIES` fail-closed：前置未开则强制关闭 |
| **R9** | 持久化配置缺新开关键 | 低 | 历史 DB 配置不含 L1–L4 开关 | `restore_runtime_config_from_db()` 补齐缺失键（取默认关）+ 重跑依赖解析 |

---

## 2. 通用回滚原则

1. **开关优先**：任何能力异常，第一步将对应 `AGENT_*_ENABLED` 环境变量移除（或置 `false`），
   前端即回退兼容路径，无需改代码。
2. **不提交也能回退**：因 R1，本轮不提交；如需临时撤销某文件改动，用
   `git checkout -- <file>`（仅对**已跟踪**文件有效；未跟踪文件用 `git clean -n` 预览后谨慎删除）。
3. **原子提交**：L0/L1 全部落地并通过门禁后，建议由用户一次性 `git add` 相关文件并原子提交，
   避免继续叠加导致提交粒度混乱。

---

## 3. 分阶段操作手册

### L0（基线冻结 / 协议）
- **迁移**：无需数据迁移。仅新增协议文档与功能开关默认值。
- **备份**：`git stash list` 确认无半成品；记录 `HEAD=8144fdf` 与未提交计数（见基线记录）。
- **回滚**：删除 `docs/L0_*.md`；`config.py` 移回 L0 前（撤销 4 个新开关与依赖解析）。
  影响面小，但建议整体原子提交后回滚而非逐文件。

### L1（前端多部件流式上传）★ 本轮
- **迁移**：前端从「硬编码 15MB + Base64」迁移到「`/capabilities` 驱动 + FormData/XHR」。
- **备份**：
  - 后端：`backend/tests/test_l1_multipart_upload.py` 已覆盖（34 例通过），作为回归锚。
  - 前端：旧 `tmAttachFile`（Base64 版）仍在 git 历史，可一键还原。
- **回滚（两种粒度）**：
  - **软回滚（推荐）**：保持代码，仅确保 `AGENT_MULTIPART_UI_ENABLED` 未设置 → 前端走 Base64，行为等同 L1 前。
  - **硬回滚**：
    1. `teachmate-interactions.js` 还原 `tmAttachFile` 为 Base64 版；
    2. `teachmate-api.js` 删除 `uploadAttachment`；
    3. `teachmate-views.js` / `teachmate.css` 撤销进度条相关改动；
    4. 后端 `attachment_uploads.py` + `attachments.py` 的 `/capabilities` 与 MIME 门禁可保留或还原（保留不影响旧路径）。

### L2（Codex 只读插件）— 后续轮次
- **迁移**：新增 MCP Server 进程与配对令牌；`codex_plugin_enabled` 打开。
- **备份**：插件清单遵循 `TeachMatePluginAPI v1`；令牌只存环境变量，**不入库不进日志**。
- **回滚**：置 `AGENT_CODEX_PLUGIN_ENABLED=false` 并停掉插件进程；前端隐藏插件入口。

### L3（多模态视觉 OCR）— 后续轮次
- **迁移**：图片附件解析产物遵循 `VisionResult v1`；`vision_analysis_enabled` 打开需 `agent_enabled`。
- **备份**：OCR 结果入既有 attachment 解析表；保留 `pending_ocr` 状态供前端展示。
- **回滚**：置 `AGENT_VISION_ANALYSIS_ENABLED=false`；图片附件回到「需 OCR，暂不可发送」提示（既有行为）。

### L4（本地 PDF/DOCX 导出）— 后续轮次
- **迁移**：导出请求遵循 `DocumentSpec v1`；`document_export_enabled` 打开。
- **备份**：导出为本地文件落地，不涉及远端写入；`remote` provider 受 fail-closed 约束。
- **回滚**：置 `AGENT_DOCUMENT_EXPORT_ENABLED=false`；`remote_document_provider_enabled` 因依赖自动关闭（R8）。

---

## 4. 发布门禁执行（L1-3）

```
python3 tools/release_check.py
```

门禁为「任一阶段失败即非零退出」。既有失败（R7，见基线记录 §3）会令其在 stage 03 中断；
处理策略：
- **L0/L1 新增代码** 的回归项（前端语法 `npm run check`、L1 后端 `test_l1_multipart_upload.py`）必须全绿；
- 既有 7 例失败**不阻断 L0/L1 验收**，但需在提交前由用户决定修复或显式豁免；
- 每次门禁实跑后，将实际失败计数回填基线记录 §3，避免事实漂移。

---

## 5. 备份清单（本轮产出物）

| 文件 | 角色 | 回滚价值 |
| --- | --- | --- |
| `docs/L0_BASELINE_RECORD.md` | 冻结点 | 回滚锚点 |
| `docs/L0_PROTOCOL_SCHEMAS.md` | 协议契约 | 跨阶段兼容依据 |
| `docs/L0_RISK_REGISTER.md` | 本文件 | 回滚操作手册 |
| `backend/tests/test_l1_multipart_upload.py` | L1 回归 | 回归绿标 |
| `backend/app/services/attachment_uploads.py` | L1 后端补强 | 可独立删除 |
| `backend/app/agent/config.py` | L0 开关 | 撤销 4 开关即回退 |
