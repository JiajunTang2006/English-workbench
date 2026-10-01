# TeachMate 长期开发项目方案说明书

> 2026-09-30 优先级更新：[后续开发任务总表](TEACHMATE_NEXT_DEVELOPMENT_TASKS.md)统一管理教师对话核心、个人专项/分层练习、教案、作答反馈、复测与学生互动的排期及验收。先修对话核心，再接通教学与学生使用链路；新任务尚未实现。[教师对话核心改造方案](TEACHMATE_CONVERSATION_CORE_PLAN.md)保留技术设计。下文保留既有长期建设背景，不代表最新完成状态；已交付范围见 [1.0 改造说明](TEACHMATE_1_0_UPGRADE.md)。

> 项目主题：Codex 兼容插件、Multipart 附件链路、多模态教学分析、Word/PDF 文档产出
>
> 基线版本：`0.9.0-beta.2`
>
> 方案日期：2026-08-22
>
> 适用对象：产品负责人、开发者、测试人员、后续维护者

## 1. 项目摘要

本项目将在现有 TeachMate 本地教学工作台基础上，按以下顺序完成四项长期能力建设：

1. 接通前端 Multipart 流式上传，替换当前 Base64 文件传输；
2. 把 TeachMate 封装为兼容 ChatGPT/Codex 的只读插件 MVP；
3. 建立图片、扫描 PDF、OCR、视觉模型、教师校对和证据追踪组成的多模态闭环；
4. 建立统一文档生成层，支持本地及第三方 API 生成 PDF 和 Word。

整体策略不是重写现有系统，而是在稳定边界上逐层扩展：

- FastAPI + SQLite 继续作为教学数据唯一事实源；
- Harness 继续承担 TeachMate 自身的 Agent 回合；
- Codex 插件通过 MCP 调用 TeachMate，不直接访问 SQLite；
- 图片和扫描件先经过受控视觉处理、教师确认，再进入正式上下文；
- Word/PDF 由独立文档渲染层生成，Agent 只负责结构化内容；
- 所有新能力都必须保留现有隐私、证据、审计、预算和故障恢复边界。

按一名熟悉现有项目的全职开发者估算，核心开发量约为 28 至 44 个开发日。考虑设计确认、真实数据试点、跨平台验证和返工缓冲，建议按 8 至 12 个自然周组织项目。

## 2. 建设背景

### 2.1 当前产品形态

TeachMate 当前是面向单教师的本地桌面教学工作台：

```text
桌面启动器
  │
  ├── TeachMate Web（默认本地入口 127.0.0.1:8765）
  │      ├── 教学聊天
  │      ├── 会话与报告
  │      └── 附件选择与资料库
  │
  ├── Workbench FastAPI（默认内部端口 8766）
  │      ├── 学期、班级、学生、考试和成绩 API
  │      ├── Agent 会话、运行、SSE 和证据 API
  │      ├── 附件、解析、后台任务和文件安全
  │      └── SQLite 与本地附件目录
  │
  └── 常驻 Harness
         ├── 模型会话
         ├── 工具调用
         ├── 事件投影
         └── Education Bridge
```

### 2.2 已有工程基础

现有代码已经具备本项目需要的大部分底层能力：

- 本地 FastAPI 服务与随机 Token 鉴权；
- SQLite 数据迁移、备份和恢复；
- 学期、班级、学生、考试和成绩作用域；
- Agent 会话、运行记录、持久事件、SSE 和取消恢复；
- 确定性统计、证据账本、结构化报告与教师确认；
- 附件元数据、本地文件存储、SHA-256、安全检查和后台解析；
- PDF、DOCX、XLSX、文本等本地解析基础；
- Provider 配置、API Key 管理、用量和预算基础；
- 前端、后端、端到端和发布门禁测试。

### 2.3 当前关键缺口

| 领域 | 已有能力 | 当前缺口 |
|---|---|---|
| 文件上传 | 后端已有 Multipart 分块上传 | 前端仍使用 FileReader + Base64 |
| 文档解析 | 文本 PDF、DOCX、XLSX 可提取文字 | 图片和扫描 PDF 没有真实 OCR 闭环 |
| 模型输入 | Harness 支持 `contentBlocks` | 当前运行只构造文本块 |
| 视觉模型 | 已有配置和抽象骨架 | Vision Provider 与 OCRService 仍为占位实现 |
| Codex 扩展 | FastAPI 已有稳定业务能力 | 没有 MCP Server、Skill、manifest 和插件认证 |
| 报告产出 | 已有打印 HTML 和 JSON | 没有稳定 PDF、DOCX、产物记录和第三方 Provider |

## 3. 项目目标与非目标

### 3.1 总体目标

- 教师可以稳定上传大文件、图片和扫描 PDF；
- TeachMate 可以处理图像、手写内容和扫描材料，并保留教师确认；
- Codex 可以通过安装插件读取 TeachMate 教学事实并完成证据化工作流；
- 教师可以一键生成可归档 PDF 和可编辑 Word；
- 第三方服务接入不会绕过隐私、成本、审计和本地事实边界；
- 新能力不破坏原有 Workbench、TeachMate 和 Harness 路径。

### 3.2 首期非目标

- 不把 TeachMate 改造成多租户云平台；
- 不让 Codex 插件直接读取 SQLite；
- 不在插件内复制第二套 TeachMate Agent；
- 不开放修改成绩、删除学生、确认评价等高风险插件工具；
- 不修改 `vendor/deepseek-harness-upstream` 核心源码；
- 不把未经教师确认的 OCR 或视觉结果写入正式教学事实；
- 不承诺当前 DeepSeek 文本适配器能够直接理解图片；
- 不在没有隐私评估的情况下把学生原始附件发送给第三方文档服务。

## 4. 核心设计原则

### 4.1 事实与解释分离

分数、排名、比例、趋势和风险信号由本地 SQL/Python 计算。模型只解释事实、组织报告和提出建议，不生成关键数字。

### 4.2 插件是适配层，不是新后端

Codex 插件通过 MCP 调用 TeachMate FastAPI。业务校验、数据作用域和审计仍由 TeachMate 服务端执行。

### 4.3 先结构化，后生成文件

Agent 输出统一结构化报告；文档服务把报告映射为 `DocumentSpec`；PDF、Word 或第三方 API 都消费同一中间格式。

### 4.4 未确认资料不得成为事实

附件状态必须经过上传、解析或视觉处理、教师校对、确认和正式资料晋升。任何失败、低置信度、待确认或拒绝结果都不能进入正式上下文。

### 4.5 按模型能力路由

系统必须知道模型是否支持文本、图片、PDF、结构化输出和工具调用。不支持图片的模型不得收到图片内容块。

### 4.6 本地优先，远端可选

能够本地完成的解析、脱敏、PDF 生成和数据查询优先本地完成。远端视觉或文档服务作为显式可配置 Provider。

### 4.7 每阶段可独立发布和回退

Multipart、Codex 插件、视觉闭环和文档生成分别使用功能开关、独立版本和迁移，不能形成一次性大爆炸发布。

## 5. 目标总体架构

```text
                         ┌──────────────────────────┐
                         │ ChatGPT / Codex          │
                         │ Plugin Skills + MCP      │
                         └────────────┬─────────────┘
                                      │ 本地 stdio MCP
                                      ▼
                         ┌──────────────────────────┐
                         │ TeachMate Plugin Adapter │
                         │ Schema/Auth/Tool Policy  │
                         └────────────┬─────────────┘
                                      │ 127.0.0.1 + scoped token
                                      ▼
┌──────────────────┐       ┌──────────────────────────┐
│ TeachMate Web    │──────▶│ Workbench FastAPI        │
│ Multipart/UI/SSE │       │ 唯一业务与数据事实入口   │
└──────────────────┘       └───────┬─────────┬────────┘
                                    │         │
                         ┌──────────▼───┐ ┌──▼─────────────────┐
                         │ SQLite       │ │ File/Object Storage │
                         │ Facts/Audit  │ │ Attachments/Exports │
                         └──────────────┘ └──┬─────────────────┘
                                             │
                              ┌──────────────▼──────────────┐
                              │ Media Processing Pipeline   │
                              │ Parse/Render/OCR/Vision     │
                              └──────────────┬──────────────┘
                                             │ confirmed results
                              ┌──────────────▼──────────────┐
                              │ FormalContext + Evidence    │
                              └──────────────┬──────────────┘
                                             │
                              ┌──────────────▼──────────────┐
                              │ Harness Text Agent          │
                              │ Optional native image route │
                              └──────────────┬──────────────┘
                                             │ StructuredAnswer
                              ┌──────────────▼──────────────┐
                              │ Document Service            │
                              │ Local/Remote PDF & DOCX     │
                              └─────────────────────────────┘
```

## 6. 项目阶段总览

| 阶段 | 名称 | 主要交付 | 预计开发量 | 依赖 |
|---|---|---|---:|---|
| L0 | 项目基线与协议冻结 | 契约、功能开关、测试基线 | 1 至 2 天 | 无 |
| L1 | Multipart 前端接通 | 浏览器流式上传、进度、取消 | 1 至 2 天 | L0 |
| L2 | Codex 只读插件 MVP | 本地 MCP、3 Skills、配对认证 | 8 至 12 天 | L1 |
| L3 | 多模态视觉闭环 | 扫描预处理、OCR、教师确认、证据 | 11 至 16 天 | L1；复用 L2 契约 |
| L4 | PDF/Word 文档能力 | DocumentSpec、本地导出、第三方 Provider | 8 至 14 天 | L2、L3 的结构化输出 |
| L5 | 集成、跨平台和试点 | 发布门禁、真实样例、运维手册 | 4 至 6 天 | L1-L4 |

L2 和 L3 在 L1 完成后可以部分并行，但单人开发仍建议严格按表中顺序执行，减少同时修改附件、API、插件和 Harness 链路造成的回归。

## 7. L0：项目基线与协议冻结

### 7.1 目标

把当前工作区状态、公开协议和发布边界冻结下来，为后续四个阶段提供一致事实来源。

### 7.2 工作项

- 记录当前分支、未提交文件、应用版本和迁移版本；
- 运行并记录 `npm run check`、前端测试、后端测试、e2e 和 release check；
- 确认正式学生数据与测试数据隔离；
- 定义以下版本化协议：
  - `AttachmentUploadAPI v1`；
  - `TeachMatePluginAPI v1`；
  - `VisionResult v1`；
  - `DocumentSpec v1`；
- 为新能力增加功能开关：
  - `multipart_ui_enabled`；
  - `codex_plugin_enabled`；
  - `vision_analysis_enabled`；
  - `document_export_enabled`；
  - `remote_document_provider_enabled`；
- 明确各阶段数据库迁移、备份和回滚策略。

### 7.3 交付物

- 协议 Schema 与示例；
- 基线测试记录；
- 功能开关与默认值；
- 风险登记表；
- 每阶段独立发布与回退说明。

### 7.4 完成门禁

- 当前完整发布门禁通过，或已有失败单独登记；
- 新协议不直接暴露 ORM、绝对路径、API Key 或未确认正文；
- 所有新功能默认可关闭；
- 不修改或删除用户现有未提交内容。

## 8. L1：Multipart 前端接通

### 8.1 目标

让 TeachMate 前端实际使用已经存在的后端 Multipart 流式上传端点，消除 Base64 内存放大和 15MB 前端硬限制，为图片和扫描 PDF 奠定稳定传输基础。

### 8.2 当前事实

- 后端已有 `/api/v1/attachments/upload`；
- 后端按 1MB 分块读取、计算 SHA-256、限制 50MB、写临时文件并原子移动；
- 前端当前仍用 `FileReader.readAsDataURL()`；
- 前端当前只接受 PDF、XLSX、CSV、TXT、Markdown 和 DOCX；
- 图片尚未进入前端上传入口。

### 8.3 工作项

#### 前端 API

- 在 `teachmate-api.js` 新增 `uploadAttachment()`；
- 使用 `FormData` 发送 `file`、`title`、`term_id`；
- 不手动设置 Multipart 的 `Content-Type`；
- 确保公共请求层不会自动覆盖为 JSON；
- 统一错误码、超时和认证头处理。

#### 前端交互

- 删除正常路径中的 FileReader/Base64；
- 增加 PNG、JPEG 选择；
- 显示上传、校验、解析、等待 OCR、可发送和失败状态；
- 支持用户取消上传；
- 支持同内容重复文件的幂等提示；
- 从服务端读取实际限制，不再硬编码 15MB。

#### 后端补强

- 保留旧 JSON/Base64 端点作为一个版本周期的兼容入口；
- 为 Multipart 增加统一响应错误码；
- 增加临时文件启动清理；
- 增加上传指标：大小、耗时、状态，不记录正文；
- 明确并发上传上限。

### 8.4 测试

- 1KB、15MB、49MB 正常上传；
- 51MB 中途停止并返回 413；
- 0 字节、伪造 MIME、损坏图片、假 PDF、路径穿越；
- 网络中断、用户取消、数据库提交失败；
- 重复内容幂等；
- 所有失败路径不遗留 `.upload-*.tmp`；
- 上传后原有附件解析和消息关联不回归。

### 8.5 完成定义

- 浏览器不再把文件转换为 Base64；
- 前后端大小限制一致；
- 图片能够上传并进入 `pending_ocr`；
- 旧附件和资料库功能保持可用；
- release check 通过。

## 9. L2：Codex 只读插件 MVP

### 9.1 目标

将 TeachMate 的确定性教学事实和证据化工作流以兼容 ChatGPT/Codex 的插件形式交付。首版为本地、只读、可撤销和可审计插件。

### 9.2 插件形态

采用 OpenAI 当前插件结构：

```text
plugins/teachmate/
├── .codex-plugin/
│   └── plugin.json
├── .mcp.json
├── skills/
│   ├── analyze-exam/SKILL.md
│   ├── diagnose-student/SKILL.md
│   └── build-review-plan/SKILL.md
├── server/
│   ├── __main__.py
│   ├── client.py
│   ├── auth.py
│   ├── schemas.py
│   └── tools.py
├── assets/
└── tests/

.agents/plugins/marketplace.json
```

首版 MCP Server 使用 stdio，由 Codex 本地启动，再通过回环地址调用 TeachMate FastAPI。

### 9.3 工具范围

#### 首版工具

| 工具 | 用途 | 敏感等级 | 默认审批 |
|---|---|---:|---|
| `get_teachmate_status` | 版本、健康和能力检查 | 低 | 自动 |
| `list_teaching_scopes` | 学期、班级和考试选择 | 低 | 自动 |
| `get_exam_snapshot` | 确定性考试统计和数据质量 | 中 | 自动 |
| `get_student_learning_profile` | 匿名学生趋势和薄弱项 | 高 | 提示 |
| `get_review_plan_facts` | 复习计划事实集合 | 中 | 自动 |
| `list_formal_materials` | 已确认资料元数据 | 中 | 自动 |
| `read_formal_material` | 分页读取确认正文 | 高 | 提示 |
| `get_evidence` | 展开证据与计算来源 | 中 | 自动 |

#### 首版禁止能力

- 任意 SQL；
- 文件系统绝对路径；
- 修改成绩或学生档案；
- 确认、删除、归档等写操作；
- 未确认附件正文；
- 原始 API Key、Token、哈希和隐私映射表。

### 9.4 Skills

#### `analyze-exam`

- 确认学期、班级和考试；
- 获取考试快照和数据质量；
- 获取必要证据；
- 输出事实、解释、局限和行动建议；
- 所有关键结论关联 evidence ID。

#### `diagnose-student`

- 明确学生与考试范围；
- 默认使用匿名标识；
- 区分事实、推断和教师待确认项；
- 不把单次考试结论扩大为长期能力判断。

#### `build-review-plan`

- 读取共同错误和知识点事实；
- 输出优先级、目标、课时、材料和复查指标；
- 每个计划项有事实依据和可验证结果。

### 9.5 插件认证

浏览器启动 Token 与插件 Token 分离：

1. TeachMate 设置页生成一次性配对码；
2. MCP Server 用配对码交换 `teaching.read` 令牌；
3. 令牌保存在插件数据目录或系统凭据库；
4. 服务端仅保存令牌摘要和权限；
5. 教师可以查看最后使用时间并一键撤销；
6. 写能力未来使用独立 scope，不因插件升级自动获得。

### 9.6 MCP 实现要求

- 每个工具有稳定名称、描述、输入 Schema 和输出 Schema；
- 返回结构化内容与简短模型可读文本；
- 错误码区分服务未启动、未认证、越权、范围错误和数据不完整；
- MCP 层不复制业务逻辑，只做协议和错误映射；
- FastAPI 再次验证学期、班级、考试和学生归属；
- 工具日志只记录工具、作用域、耗时和状态；
- 插件与后端通过 `api_version` 协商兼容性。

### 9.7 分阶段实施

#### L2-A：公开 API 契约，1 至 2 天

- 新增插件状态和聚合查询 API；
- 从内部服务返回稳定 DTO；
- 增加契约快照测试。

#### L2-B：本地 MCP Server，2 至 3 天

- 实现 stdio 服务；
- 注册首版只读工具；
- 完成健康、超时、重试和错误映射。

#### L2-C：插件包装与 Skills，1 至 2 天

- 创建 manifest、MCP 配置和本地 marketplace；
- 编写三个 Skill；
- 配置工具审批策略。

#### L2-D：认证、隐私和审计，2 至 3 天

- 配对、撤销、scope 和匿名标识；
- 跨学期、伪造 ID、过期令牌和重放测试。

#### L2-E：Codex 试点，2 天

- Codex CLI 与桌面安装测试；
- 黄金任务、负向任务和结果对账；
- macOS、Windows 各验证一次。

### 9.8 完成定义

- 插件可以从本地 marketplace 安装；
- 新 Codex 任务能识别三个教学工作流；
- 确定性数字与 TeachMate 页面差异为 0；
- 未确认资料和跨学期数据泄漏为 0；
- 撤销插件 Token 后立即无法访问；
- TeachMate 未启动时返回明确恢复指引；
- 插件不依赖修改 vendored Harness。

## 10. L3：多模态视觉闭环

### 10.1 目标

让系统能够真实理解图片、扫描 PDF、答题卡和手写内容，并把识别结果转化为经过教师确认、带证据定位的正式教学资料。

### 10.2 推荐路线

首选“独立视觉 Provider + 现有文本 Harness”路线：

```text
图片或扫描 PDF
  → 本地预处理
  → OCR/视觉 Provider
  → 页级结构化结果
  → 教师校对与确认
  → FormalContext + Evidence
  → 现有 Harness 文本分析
```

这已经是完整的产品级多模态能力。Harness 原生图片内容块作为可选增强，不作为第一版完成条件。

### 10.3 数据模型

#### `attachment_derivatives`

- `id`
- `attachment_id`
- `kind`: `page_image`、`thumbnail`、`ocr_text`、`vision_observation`
- `page_no`
- `storage_name`
- `mime_type`
- `size_bytes`
- `sha256`
- `width`、`height`
- `created_at`

#### `attachment_analysis_results`

- `id`
- `attachment_id`
- `provider`
- `model_name`
- `result_type`: `ocr`、`layout`、`handwriting`、`vision`
- `status`: `queued`、`running`、`pending_review`、`confirmed`、`rejected`、`failed`
- `content_json`
- `cost_yuan`
- `provider_request_id`
- `created_at`、`confirmed_at`

大规模页级结果不继续无限塞入 `attachments.metadata_json`。旧字段保留兼容读取，并通过迁移逐步转向独立版本化表。

### 10.4 状态机

```text
uploaded
  → parsing
  ├── pending_review        文本文件本地解析成功
  └── pending_ocr           图片或无文本扫描页
        → vision_running
        → pending_review
        ├── confirmed
        │     → formal_context/evidence
        ├── rejected
        └── failed
```

### 10.5 视觉 Provider 契约

```python
class VisionProvider(Protocol):
    async def analyze(
        self,
        *,
        images: list[ImageInput],
        task: Literal["ocr", "layout", "handwriting", "exam_understanding"],
        response_schema: dict,
    ) -> VisionResult: ...
```

`VisionResult` 必须包含：

- 页级文本；
- 文本块坐标；
- 表格或题目结构；
- 每个区域的置信度；
- 低置信度与不可识别区域；
- Provider、模型和请求 ID；
- 用量与实际费用；
- 安全过滤或失败原因。

Provider 不直接写数据库，统一服务层负责状态、幂等、审计和证据。

### 10.6 模型能力声明

每个 Provider 档案增加：

- `supports_image_input`；
- `supports_pdf_input`；
- `supports_structured_output`；
- `supports_handwriting`；
- `max_images_per_request`；
- `max_image_bytes`；
- `max_total_pixels`；
- `data_retention_mode`；
- 视觉定价和估算规则。

### 10.7 分阶段实施

#### L3-A：图片和扫描 PDF 预处理，3 至 4 天

- 方向修正、颜色归一、受控缩放和缩略图；
- 扫描 PDF 受控 DPI 页面渲染；
- 文本 PDF 优先本地提取；
- 只对无文本或低置信度页调用视觉服务；
- 页级派生物保存哈希、尺寸和来源；
- 限制总页数、总像素、解码后大小和并发数。

#### L3-B：真实 OCR/视觉 Provider，4 至 6 天

- 实现至少一个真实 Provider；
- 完成异步调用、超时、重试和错误分类；
- 页级批处理与部分失败恢复；
- 记录请求 ID、用量和费用；
- 低置信度页面允许二次识别。

#### L3-C：教师校对和证据，2 至 3 天

- 原图、识别文本和低置信区域并排展示；
- 教师修改、确认或拒绝；
- 原始识别结果和教师修正追加保存；
- Evidence 记录页码、区域、模型版本和确认版本；
- FormalContext 只读取 confirmed 结果。

#### L3-D：多模态能力路由，2 至 3 天

- `exam_ingestion` 等能力声明所需模态；
- 根据文件、任务和模型能力选择本地解析、OCR、视觉或纯文本；
- 成本预估纳入页数、图像数量和视觉价格；
- 无视觉 Provider 时 fail-closed；
- 文本任务不产生无必要视觉费用。

#### L3-E：可选 Harness 原生图片输入，4 至 8 天

仅在真实需求证明有必要时实施：

- 使用 Harness durable attachment service 取得图片引用；
- `contentBlocks` 加入 image block；
- Cordis 使用支持图片输入的 Provider；
- 当前 DeepSeek text-only 路径继续拒绝图片；
- 通过项目自有组合或插件扩展，不修改 vendor 核心；
- 覆盖历史图片、模型切换、会话压缩和附件回收测试。

### 10.8 隐私门禁

- 视觉外发前遮挡姓名、学号、学校、二维码和联系方式；
- 教师能预览将发送的页面；
- 真实身份映射只留本地；
- 不把图片 Base64、OCR 原文或完整 Provider 响应写入普通日志；
- Provider 必须声明数据留存和删除策略；
- 原图、派生图和视觉结果分别设置保留期限；
- 文档内恶意提示不能改变系统和工具权限。

### 10.9 完成定义

- 图片和扫描 PDF 通过 Multipart 上传；
- 系统真实调用 OCR/视觉能力；
- 结果含页码、区域、置信度、版本和费用；
- 教师确认前不能进入正式上下文；
- 最终报告可追溯到原始页面或区域；
- text-only 模型不会收到图片块；
- 超时、取消、部分失败和隐私阻断可安全恢复。

## 11. L4：Word/PDF 文档能力

### 11.1 目标

把 TeachMate 的结构化报告稳定生成 PDF 和 DOCX，并允许未来接入第三方文档 API，而不把文档格式逻辑写进 Agent 或插件。

### 11.2 核心中间格式

新增 `DocumentSpec v1`，至少覆盖：

- 文档标题和元数据；
- 章节、段落和列表；
- 表格；
- 图表引用；
- 证据脚注或附录；
- 页眉、页脚、页码；
- 分页提示；
- 字体、主题和模板版本；
- 隐私等级与可外发标记。

输入来源：

```text
StructuredAnswer
+ Evidence
+ 教师确认文本
+ 教学范围元数据
        ↓
DocumentSpec v1
```

### 11.3 数据模型

#### `document_export_jobs`

- `id`
- `run_id`、`session_id`
- `format`: `pdf`、`docx`
- `provider`
- `template_id`、`template_version`
- `status`
- `input_snapshot_json`
- `remote_job_id`
- `output_artifact_id`
- `error_message`
- `created_at`、`completed_at`

#### `generated_artifacts`

- `id`
- `kind`
- `storage_name`
- `mime_type`
- `size_bytes`
- `sha256`
- `expires_at`
- `contains_personal_data`
- `created_at`

### 11.4 Provider 抽象

```python
class DocumentProvider(Protocol):
    async def submit(
        self,
        spec: DocumentSpec,
        *,
        format: Literal["pdf", "docx"],
        template: str,
        idempotency_key: str,
    ) -> ExportHandle: ...

    async def poll(self, handle: ExportHandle) -> ExportStatus: ...
    async def download(self, handle: ExportHandle, target: Path) -> GeneratedArtifact: ...
```

统一支持同步返回、轮询和 webhook 三类第三方服务，但对上层都表现为持久后台任务。

### 11.5 实施顺序

#### L4-A：本地 PDF MVP，2 至 3 天

- 定义 `DocumentSpec v1`；
- 把当前报告画布映射为稳定 HTML；
- 后台生成 PDF；
- 保存到 `exports/`；
- 登记 MIME、大小、SHA-256 和生成来源；
- 提供下载与重新生成接口。

选择 PDF 优先的原因：当前已有打印 HTML，路径短，版式也比 DOCX 更容易冻结。

#### L4-B：本地 DOCX，3 至 5 天

- 建立 DOCX 模板和模板字段；
- 支持标题、段落、表格、页眉页脚和证据附录；
- 保持教师可编辑；
- 解包检查 DOCX 结构和外部关系；
- 验证 Word、WPS 和 LibreOffice 的基本兼容性。

#### L4-C：第三方文档 Provider，3 至 5 天

- 配置 Provider、凭据和允许格式；
- 支持异步任务、轮询或 webhook；
- 使用幂等键防止重复计费；
- 外发前显示脱敏预览和费用确认；
- 下载后验证 MIME、大小、文件头和哈希；
- 第三方失败时允许回退本地 Provider。

#### L4-D：模板管理与运维，2 至 4 天

- 模板版本、预览、启用、回滚；
- 中文字体和品牌资源；
- 产物过期、清理、备份和审计；
- macOS、Windows 打包验证；
- Codex 插件增加需确认的 `export_report` 写工具。

### 11.6 第三方 API 准入要求

- HTTPS 和服务端鉴权；
- 明确数据留存与删除策略；
- 支持结构化字段或模板，不只接受自然语言；
- 支持幂等键；
- 返回文件或稳定异步任务 ID；
- 有明确限流、超时和错误码；
- 支持中文字体、表格和分页；
- 允许下载后进行本地安全复验。

不向文档 API 发送学生原始答题图片。第三方只接收已确认、已脱敏的 `DocumentSpec` 和必要排版资源。

### 11.7 完成定义

- 同一报告可以生成 PDF 和 DOCX；
- 产物具有版本、来源、哈希和审计；
- PDF 中文字体、分页和打印稳定；
- DOCX 可以在主流办公软件编辑；
- 第三方失败不导致报告内容丢失；
- 重试不会重复计费或生成无法追踪的文件；
- 插件导出属于显式需确认写操作。

## 12. L5：集成、发布和真实试点

### 12.1 集成任务

- 统一功能开关和设置页；
- 统一 Provider 状态、费用和错误展示；
- 统一附件、视觉和导出后台任务中心；
- 统一生成产物和原始附件的生命周期；
- 统一 Codex 插件与 TeachMate 页面的证据编号；
- 更新启动器、macOS 和 Windows 打包清单；
- 更新备份清单和恢复校验。

### 12.2 黄金场景

至少验证以下端到端场景：

1. 上传成绩 Excel，Codex 插件生成证据化考试分析；
2. 上传文字 PDF，TeachMate 提取文本并形成报告；
3. 上传一张学生答题图片，OCR 后经教师修正进入学生诊断；
4. 上传扫描 PDF，仅低置信度页调用视觉 Provider；
5. 把确认后的考试报告导出 PDF；
6. 把同一报告导出可编辑 DOCX；
7. 第三方文档 Provider 超时后回退本地生成；
8. 撤销 Codex 插件 Token 后所有工具立即不可用。

### 12.3 发布门禁

- 前端语法和单元测试通过；
- 后端完整测试通过；
- e2e 通过；
- release check 通过；
- macOS、Windows 安装包验证通过；
- 数据库从空库和当前正式库升级通过；
- 升级前备份和失败恢复通过；
- 确定性数字错误率为 0；
- 未确认资料进入正式上下文事件为 0；
- 身份字段未授权外发事件为 0；
- 插件跨学期越权事件为 0；
- 导出文件 MIME、哈希和下载校验通过。

## 13. 跨阶段公共能力

### 13.1 API 版本化

- 所有插件、视觉和文档接口包含 `api_version`；
- 只新增兼容字段时保持小版本；
- 删除或重解释字段必须新增大版本；
- 客户端遇到不支持版本时明确失败，不猜测字段。

### 13.2 幂等

- 附件以学期 + SHA-256 幂等；
- 视觉任务以附件版本 + Provider + 模型 + 任务类型幂等；
- 文档任务以报告快照 + 模板版本 + 格式幂等；
- 远端提交使用 idempotency key；
- 重试和应用恢复不得重复计费。

### 13.3 后台任务

统一任务状态：

```text
queued → running → waiting_review / completed
                  ↘ retry_wait → running
                  ↘ failed / cancelled
```

每个任务必须记录：类型、作用域、进度、重试次数、下次重试时间、费用、错误分类和产物。

### 13.4 可观测性

记录但不泄漏正文：

- 请求与任务 ID；
- Provider 和模型；
- 输入页数、图片数、字节数；
- 延迟和重试次数；
- token 或视觉计费单位；
- 估算与实际费用；
- 结果状态和错误分类；
- 教师确认、修改或拒绝动作。

### 13.5 数据保留

| 数据 | 默认策略 |
|---|---|
| 原始附件 | 由教师管理，随正式备份 |
| 临时上传文件 | 成功或失败后立即清理 |
| 页面派生图 | 可配置保留，默认随原附件 |
| OCR/视觉原始结果 | 版本化保留，用于审计 |
| 教师确认结果 | 正式保留，不覆盖历史 |
| 出站匿名预览 | 短期审计后清理 |
| Word/PDF 产物 | 可配置过期或手动归档 |
| 第三方远端产物 | 下载验证后请求远端删除 |

## 14. 版本与发布策略

TeachMate 主程序与 Codex 插件分别版本化：

| 里程碑 | TeachMate 建议版本 | 插件建议版本 | 交付 |
|---|---|---|---|
| L1 | `0.9.0-beta.3` | 无 | Multipart 前端接通 |
| L2 | `0.9.0-rc.1` | `0.1.0-local` | 只读插件 MVP |
| L3-A/B | `0.10.0-alpha.1` | `0.1.x` | 图片/扫描 PDF + 视觉 Provider |
| L3-C/D | `0.10.0-beta.1` | `0.2.0` | 教师确认、证据和多模态路由 |
| L4-A/B | `0.10.0-rc.1` | `0.2.x` | 本地 PDF/DOCX |
| L4-C/D | `0.10.0` | `0.3.0` | 第三方文档 Provider 和导出工具 |
| L5 | `1.0.0` 候选 | `1.0.0` 候选 | 跨平台、试点和正式文档 |

版本号可按实际发布计划调整，但里程碑边界不应混合。

## 15. 项目排期建议

### 15.1 单人开发节奏

| 周期 | 建议工作 |
|---|---|
| 第 1 周 | L0、L1、Multipart 发布 |
| 第 2 至 3 周 | L2 API、MCP Server、Skills |
| 第 4 周 | L2 认证、Codex 试点、修复 |
| 第 5 至 6 周 | L3 图片/PDF 预处理和视觉 Provider |
| 第 7 周 | L3 教师校对、证据和多模态路由 |
| 第 8 周 | L4 本地 PDF 和 DOCX |
| 第 9 周 | L4 第三方 Provider 与模板管理 |
| 第 10 至 12 周 | L5 跨平台、真实数据试点、发布收口 |

### 15.2 每批开发纪律

- 每个里程碑单独分支或独立提交批次；
- 一个批次只包含一个数据库迁移主题；
- 每日更新开发日志和测试结果；
- 每个 Provider 都要有模拟器，测试不依赖真实付费 API；
- 真实 API 验证使用匿名样例和费用上限；
- 未通过当前阶段门禁前不开始下一个高风险阶段。

## 16. 风险登记

| ID | 风险 | 影响 | 缓解措施 |
|---|---|---|---|
| R1 | 当前工作区已有大量未提交修改 | 合并冲突和回归难定位 | 新目录优先、分阶段提交、先记录基线 |
| R2 | Multipart 前后端限制不一致 | 用户体验混乱 | 服务端返回能力与限制，前端不硬编码 |
| R3 | DeepSeek 适配器不支持图片 | 原生图片块运行失败 | 独立视觉 Provider；按能力路由 |
| R4 | OCR 低置信度被当成事实 | 错误教学结论 | 教师确认、置信度、页级证据、fail-closed |
| R5 | 学生图片包含身份信息 | 未成年人隐私泄漏 | 本地遮挡、预览、匿名映射、Provider 留存审查 |
| R6 | MCP 直接暴露内部服务 | 越权或协议不稳定 | 独立公开 DTO、scope、契约测试 |
| R7 | 插件与 TeachMate 产生两套分析 | 成本和结论不一致 | 插件读取事实，不复制 Agent |
| R8 | 视觉和文档调用重复计费 | 超出预算 | 幂等键、持久任务、实际费用审计 |
| R9 | 大规模 OCR JSON 膨胀 | SQLite 性能和备份压力 | 独立结果表、派生文件、分页读取 |
| R10 | DOCX/PDF 跨平台版式不一致 | 交付质量差 | 模板版本、字体嵌入、渲染与打开验证 |
| R11 | 第三方文档 API 数据留存不透明 | 隐私风险 | 本地默认、供应商准入、远端删除 |
| R12 | 公共插件发布要求扩大 | 项目失控 | 首版本地只读，公共发布另立项目 |

## 17. 关键决策点

### 决策点 A：Multipart 完成后

Go：49MB 文件稳定上传，失败无临时文件，旧路径不回归。

No-Go：公共请求层无法可靠处理 FormData，或跨平台上传仍有内存峰值问题。

### 决策点 B：Codex 插件 MVP 后

Go：真实教学任务中，插件工具选择正确，数字与页面一致，教师认为工作流有价值。

No-Go：插件只能重复 TeachMate 聊天功能，或认证和本地启动体验过于复杂。

### 决策点 C：视觉 Provider 后

Go：OCR/视觉准确率达到教师可校对水平，成本和延迟可接受。

No-Go：低置信度过高或身份遮挡不可控。此时保留上传与本地解析，不开放正式视觉分析。

### 决策点 D：Harness 原生图片输入

Go：视觉预处理文本仍明显损失关键版面信息，且已确认图像模型 Provider 和附件服务稳定。

No-Go：独立视觉 Provider 结果已经满足教学任务。此时延后原生图片块，降低维护成本。

### 决策点 E：第三方文档 API

Go：本地生成无法满足模板或企业工作流，第三方通过隐私、中文和幂等准入。

No-Go：本地 PDF/DOCX 已满足需要，或第三方无法承诺数据删除。

## 18. 最终验收指标

### 功能

- Multipart 上传成功率达到试点目标；
- Codex 插件三个 Skill 均能稳定触发；
- 图片和扫描 PDF 能形成可确认结果；
- 报告能生成 PDF 和 DOCX；
- 所有产物可下载、校验和追溯。

### 正确性

- 确定性数字错误率：0%；
- 无证据关键结论率：0%；
- 未确认 OCR/视觉结果进入正式上下文：0 次；
- 导出报告与确认报告内容不一致：0 次。

### 隐私与安全

- 未授权真实身份外发：0 次；
- 跨学期插件访问：0 次；
- 未撤销 Token 在撤销后继续访问：0 次；
- 临时上传文件残留：0；
- 第三方下载文件安全复验覆盖率：100%。

### 可靠性

- 应用重启后任务可恢复或明确终止；
- 取消操作不会继续产生费用；
- 同一请求重试不重复计费；
- 数据库迁移失败可从升级前备份恢复；
- macOS 和 Windows 发布门禁通过。

## 19. 项目交付清单

- 本长期项目方案说明书；
- 四类版本化协议与 Schema；
- Multipart 前端与兼容后端；
- TeachMate Codex 插件目录；
- 本地 marketplace；
- 本地 MCP Server 和只读工具；
- 三个教学 Skills；
- 插件配对、撤销和审计；
- 图片与扫描 PDF 预处理；
- 真实 OCR/视觉 Provider；
- 教师视觉校对界面；
- 视觉证据与 FormalContext 接线；
- `DocumentSpec v1`；
- 本地 PDF 和 DOCX Provider；
- 可选第三方文档 Provider；
- 导出产物管理；
- 单元、契约、集成、e2e 和跨平台测试；
- 配置、隐私、运维、备份和发布文档。

## 20. 建议立即启动的第一个开发批次

第一批只实施 L0 + L1：

1. 保存当前测试基线；
2. 定义 Multipart 前端 API；
3. 把 `tmAttachFile()` 切换为 FormData；
4. 增加图片选择和上传状态；
5. 保留旧 Base64 API 兼容；
6. 完成大小、取消、断线、重复和临时文件测试；
7. 通过完整发布门禁后独立提交。

该批次不接真实视觉 Provider、不创建 MCP 工具、不修改 Harness。它是后续所有能力的最小稳定基础。

## 21. 参考资料与专题附件

### 项目内专题方案

- `archive/2026-08-23-completed/CODEX_PLUGIN_DEVELOPMENT_PLAN.md`
- `archive/2026-08-23-completed/MULTIPART_MULTIMODAL_DEVELOPMENT_PLAN.md`
- `archive/2026-08-23-completed/WORD_PDF_EXPORT_FEASIBILITY.md`
- `harness-integration.md`
- `archive/2026-08-23-completed/AI_AGENT_DEVELOPMENT_ROADMAP.md`
- `archive/2026-08-23-completed/AI_AGENT_DEVELOPMENT_LOG.md`

### 关键代码依据

- Multipart 后端：`../backend/app/routers/attachments.py`
- 前端附件 API：`../workbench-assets/teachmate-api.js`
- 前端上传交互：`../workbench-assets/teachmate-interactions.js`
- 附件解析任务：`../backend/app/services/job_handlers/attachment_parse.py`
- OCR 服务：`../backend/app/services/ocr.py`
- 视觉 Provider：`../backend/app/agent/providers/vision.py`
- Harness 运行输入：`../backend/app/agent/run_executor.py`
- 正式资料门禁：`../backend/app/services/agent_analysis/formal_context.py`
- 当前报告导出：`../workbench-assets/teachmate-report.js`
- 后台任务：`../backend/app/services/background_jobs.py`、`../backend/app/services/job_worker.py`

### OpenAI/Codex 资料

- 插件架构：<https://developers.openai.com/plugins/concepts/plugins>
- 插件打包：<https://developers.openai.com/plugins/build/plugins>
- Skills：<https://developers.openai.com/plugins/concepts/skills>
- MCP Server：<https://developers.openai.com/plugins/concepts/mcp-server>
- Codex 开源仓库：<https://github.com/openai/codex>

## 22. 最终建议

本项目应坚持“先打通基础传输，再开放外部插件，再建立视觉事实，最后生成正式文档”的顺序。

最重要的架构判断有三项：

1. Codex 插件不替换 TeachMate，而是复用 TeachMate 的事实与证据能力；
2. Multipart 只是文件传输，多模态完成的标志是视觉处理、教师确认和证据闭环；
3. Word/PDF 不应由模型直接拼文件，而应由统一 `DocumentSpec` 和可替换 Provider 生成。

按本方案执行，四项能力可以逐步交付、独立验证、随时回退，并为未来公共插件、更多视觉模型和更多文档服务保留扩展空间。
