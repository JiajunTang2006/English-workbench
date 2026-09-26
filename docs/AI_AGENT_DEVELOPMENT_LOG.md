# TeachMate 开发日志

> 历史日志（2026-08-23 前）已归档：[`archive/2026-08-23-completed/AI_AGENT_DEVELOPMENT_LOG.md`](archive/2026-08-23-completed/AI_AGENT_DEVELOPMENT_LOG.md)。

---

## 2026-08-30｜教学知识库与 RAG v3 落地（P0~P3）

**状态：** `DONE`（P4 评测调优待做）

### 背景

按 `docs/KNOWLEDGE_BASE_RAG.md`（v3 · 默认无检索）落地教学知识库与三个教学插件
（考试分析 / 学生诊断 / 生成复习计划）的"教学依据"链路：服务端预计算 +
单轮生成 + 证据可追溯，普通聊天完全隔离。

### 知识库入库（人工事实源）

- 位置：`backend/app/agent/knowledge/`，共 **122 条目 / 38 考点 ID**；
- 两份材料的分工：`curriculum-2022/`（课标 2022，"应然要求"层：三级内容要求、
  学业质量 3-1~3-17、核心素养、语法项目表）；`exam-zhejiang-2025/`（浙江中考
  命题解析，"考试诊断"层：题型→考点→错因映射、六类错因与置信度规则、复习任务）；
- 条目 schema 与两套分类体系（考点 ID 诊断层 / 课标项目内容层）约定见
  `knowledge/README.md`；证据等级 A/B/C/D 随条目携带。

### P0 知识编译器

- 新增 `backend/app/agent/knowledge_compiler.py`：解析 122 条目 → 路由表
  （7 题型 → 考点/错因，解析只认规范考点 ID 与六类错因白名单）、行动索引
  （考点/题型 → 短行动）、受控别名（449 项）、源文件哈希 manifest；
- 编译确定性、纯内存；`--write` 可落盘 `knowledge/compiled/*.json` 供审计
  （运行期始终用内存最新编译，不存在过期产物被静默使用的问题）。

### P1 分析包预计算

- 新增 `backend/app/agent/analysis_packet.py`：
  - `build_packet(db, capability, scope)` 为三个分析能力生成 Compact Analysis
    Packet：指标 + 薄弱诊断（知识点得分率 / 逐题得分率，含高频错误选项）+
    行动建议（编译层路由）+ 局限（数据可用性、置信度纪律、草稿卷提示）；
  - **证据预登记**：数据证据（computed_metric/db_metric）与教学依据证据
    （teaching_reference）在模型启动前落 `analysis_evidence`，诊断自带
    evidence_ids，submit_report 校验可直接解析；
  - 错因置信度执行 v3 纪律：笔试数据一律 low 档；学生包不携带姓名（模型侧
    只见"该生"）；`general_chat` 直接返回 None（5.4 边界）；
  - 预计算失败回退旧工具路径，不阻断分析。

### P2 单轮路径接入

- `run_executor._execute_harness_run_managed`：分析能力 run 启动时构建分析包，
  以回合末尾文本块注入（system prompt 保持静态，前缀缓存友好），注入前过运行
  级 PrivacyMapper 脱敏；
- `_build_harness_system_prompt` 新增 packet 模式：指示模型直接据此调用一次
  `submit_report`、禁止数据工具、深查最多两次；非 packet 模式保持原文案；
- 工具策略：`scope_vault.write_scope` 新增 `capability` / `tool_policy`，
  随 scope 文件下发；`bridge_cli.main` 增加策略门——packet 模式下数据工具
  一律拒绝（"标准路径请直接依据分析包调用 submit_report"），无 policy 的旧
  scope 保持全工具可用（向后兼容）。

### P3 教学依据精确深查

- bridge 新增工具 **`get_teaching_guidance`**（args: `query`）：规范 ID /
  受控别名精确匹配，返回单条短片段（≤600 字）+ 可信度 + 来源，落
  `teaching_reference` evidence；未命中返回明确错误（提示写入 limitations）；
- 仅在分析能力白名单内可用；`general_chat` 一律拒绝。

### general_chat 生效边界（硬约束）

三道门 + 测试：组装器对 general_chat 返回 None；scope 策略为空列表（bridge
拒绝一切工具）；深查工具实现内再拒一次。分析会话内追问不受影响（正常路径）。

### 验收

| 门禁 | 结果 |
|---|---|
| 全量后端测试 | **1130 passed**（新增 21：编译器 6 / 分析包 6 / bridge 策略与深查 9） |
| 编译器 | 122 条目 / 38 考点解析，ID 唯一，routing 7 题型，两次编译哈希一致 |
| 分析包 | 薄弱知识点/题目诊断、证据预登记可解析、review 优先级、趋势诊断 |
| 边界 | general_chat 无分析包/无证据/全工具拒绝（含策略外拒绝路径） |
| 兼容 | 旧 scope（无 tool_policy）行为不变，全量回归零失败 |

### P4 首批：黄金样例评测 + 观测埋点 + 插件策略预检（同日追加）

- **黄金样例评测** `backend/tests/test_golden_zhejiang_packets.py`（8 断言）：
  贴近浙江中考结构的确定性夹具（2 班 9 生、8 题七类题型+听力、缺考 1 人、
  草稿卷干扰题），断言薄弱知识点/题目按契约命中（top-3 + 总量 5）、题型错因
  路由正确、证据链全部可解析、听力地区边界（不含地市规则 + 显式局限声明）、
  草稿卷不泄漏、**token 效率代理验收：分析包文本 < v2 数据包 50% 且 ≤2600 字**；
- **观测埋点**：packet 模式 run 在 `input_summary_json.packet_stats` 持久化
  诊断数/包字符数/工具策略；新增 `teaching_reference_usage(db, run_id)` 统计
  深查证据条数；
- **插件策略预检**：`harness_plugin/index.mjs` 在 execute 前读取 scope 的
  `tool_policy`/`capability`，general_chat 与策略外工具在插件层直接拒绝
  （省一次子进程）；补齐 `get_teaching_guidance` 的 TOOL_DEFS 声明
  （此前模型在 harness 里看不到该工具），并同步 3-11 跨语言一致性测试；
- 修复：packet 策略构建异常路径的 NameError 隐患；action 文本压行。

### 记忆板块：试卷记忆落地（同日追加）

当初"两层记忆"设计中的**试卷记忆**层补齐（全局知识层此前已落地）：

- **数据模型**：`exam_paper_memories` 表（迁移 20260830_0025，SCHEMA_REVISION
  同步）：exam + 版本唯一，status draft/confirmed/superseded，content_md +
  来源 + 生成模型 + knowledge_gaps_json（未入分类表的知识点）；
- **生成**：`services/paper_memory.py`——读 confirmed 试卷结构 + 知识点标注，
  按语篇研读 What/Why/How 框架生成 ≤600 字记忆草稿（卷面结构 / 考点与课标
  要求 / 易错预设 / 复习钩子）；文本 provider 经 `agent.factory` 构造，可注入
  替身；重新生成只 supersede 旧草稿，当前 confirmed 版本在新草稿确认前继续生效；
- **确认**：教师直接确认或编辑后确认（编辑记为 manual 来源），同卷旧确认版
  自动 superseded；
- **分类白名单检测**：知识点逐一对照编译索引（考点 ID + 语法类目 + 受控别名），
  未命中进 knowledge_gaps 提示补录；`lookup` 移除包含匹配兜底（v3 要求
  "未命中即未知"的严格语义，原兜底会让"量子纠缠式阅读"命中"阅读"）；
- **分析包注入**：confirmed 记忆以 `paper_memory` 证据类型预登记并带 evidence ID
  注入包文本（≤800 字截断）；无记忆时局限显式声明"仅基于题目结构与逐题数据"；
  review_plan 包继承；`REPORT_ALLOWED_EVIDENCE_TYPES` 加入 paper_memory；
- **API**（`/api/v1/exams/{id}/paper-memory*`）：GET 最新 / POST generate /
  POST {memory_id}/confirm；三条路径均执行学期作用域校验，未配 Key 返回 503。

### 全链路冒烟测试（模拟模型，真实进程）

新增 `backend/tests/test_v3_pipeline_smoke.py`（3 条冒烟）：按 v3 运行逻辑完整走
"preflight 分析包 → packet 模式回合组装（prompt/scope 策略）→ 模拟模型深查与
submit_report（经真实 bridge CLI 子进程，与 harness 生产调用方式一致）→ 报告
落库与证据解析"；另含 review_plan 往返与 general_chat 边界冒烟（无分析包、
prompt 无知识标记、工具全拒）。

**冒烟即战果**：首轮运行即抓到真实集成缺口——`validate_report_evidence_references`
的证据类型白名单（`REPORT_ALLOWED_EVIDENCE_TYPES`）未包含 `teaching_reference`，
导致标准路径的报告引用教学依据会被拒。已修复（evidence.py 白名单加入
`teaching_reference`，与 5.4 边界不冲突：普通聊天仍无任何证据）。

### 评审修复：5 项问题闭环（同日追加）

外部评审（2×P1 / 2×P2 / 1×P3）全部修复，各配回归测试：

1. **[P1] 分析包文本遗漏证据 ID**：`packet_to_text` 此前只输出 signal 与行动，
   而标准路径要求模型引用"包中的 evidence ID"——模型唯一能看到的就是这份
   文本，等于必被证据校验拒绝。修复：每条诊断行追加 `［证据:ev-…］`
   （`_diagnostic_line`）；冒烟测试改为断言文本含证据 ID（原冒烟直接读
   Python 对象模拟模型，绕过了该问题——已改为文本断言堵住这类盲区）。
2. **[P1] 教师改分后派生字段为旧值**：`override_student_item_result` 现同步
   重算 `score_rate`（score/max）与 `correct`（score≥满分），教师改满分后
   不再被当作错题；配双向改判测试。
3. **[P2] 深查误执行包含匹配**：评审实测时该问题已在当日早些时候修复
   （"量子纠缠式阅读"误命中暴露后移除了包含匹配兜底）；本次补回归测试
   锁定严格语义（"宾语从句专题"/"不存在的阅读标签"必须未命中）。
4. **[P2] 2600 字预算裁剪无效**：原循环在装入诊断前检查长度，形同虚设
   （实测可输出 6980 字）。重写为先计入必留的局限/记忆，再逐条装入诊断；
   任一完整诊断超过剩余预算即从该条起丢弃，编号保持连续，不再允许首条异常
   冗长时突破总预算。测试用 500 字预算 + 超长夹具验证最终长度、整条丢弃与
   局限保留。
5. **[P3] 深查两次上限仅靠提示词**：bridge 深查工具新增硬门——成功调用
   计数持久化在 `run.packet_stats.guidance_calls`，第三次直接拒绝；
   分析包预登记的 teaching_reference 证据**不占额度**（只计深查工具自身），
   未命中不计次（错误输出无上下文扩张）。配 3 条边界测试。

### 验收（评审修复追加）

| 门禁 | 结果 |
|---|---|
| 全量后端测试 | **1173 passed**（含本日全部追加回归） |
| P1-1 | 注入文本含全部诊断 evidence ID（文本断言） |
| P1-2 | 满分改判 → score_rate=1.0/correct=True；低分改判 → 0.2/False |
| P2-1 | 子串查询（宾语从句专题等）全部未命中 |
| P2-2 | 500 字预算下超长诊断整条丢弃、总长受限、局限保留 |
| P3 | 第三次深查被拒；包预登记证据与未命中不占额度 |

### 外部评审修复：运行时稳定性 5 项（P0×2 / P1×3）

按评审建议顺序 1→5 修复，各配回归测试：

1. **[P0] SSE 事件重复推送**：`event_stream` 用"条数 +1"推进游标，但游标语义是
   EventStore 全局持久化 seq（跨 run 共享单调）——并发 run 写入使 seq 跳跃，
   落后游标导致已推事件重复取出（评审实测 105/106/107 重复）。修复：提取
   `_advance_event_cursor`（seq 存在取 `max(index, seq)`，未持久化才退化为本地
   序号），三处消费循环统一替换；配 seq 跳跃回归测试。
2. **[P0] 注册表内存只增不减**：`cleanup_old` 写了完整逻辑但全项目无调用点，
   `_runs` 全局单例 + 每 RunState 带完整事件列表 = 长期驻留持续增长。修复：
   `register()` 超过软上限（100）时机会性清理（锁外调用防重入死锁）；
   顺带修复附带缺陷——非终态停留超 6 小时视为进程内僵尸，标记 failed（含
   事件留痕）后允许移除，否则最老一批卡在 running 时一个都删不掉；
   新鲜运行不受数量护栏影响。
3. **[P1] 幂等缺口**：`submit_job` 幂等状态集缺 Worker 自产的 `waiting_ocr`，
   图片附件重复提交会重复调模型计费。已补。
4. **[P1] heartbeat 空转**：`HEARTBEAT_TIMEOUT_SECONDS` 定义后零引用。新增
   看门狗线程（随 worker 启停）周期检测心跳超时的僵尸 running 任务，按
   recover_on_startup 统一重试语义恢复（requeue 或 failed）；显式跳过当前
   执行中任务防双重执行；未命中不计成本（纯查询）。
5. **[P1] `Any` 未导入**：run_executor 三处标注依赖 `from __future__
   import annotations` 惰性求值，`get_type_hints()` 会 NameError。已补导入。

P2 清理：4 个 capabilities 模块自赋值死代码移除（保留 `capabilities/__init__`
的再导出契约，`_memory_payload` 类似教训——清理前先查外部引用）；6 个文件
15 处 `backend.app.*` 绝对导入相对化（PyInstaller 打包踩雷点）；评审提到的
config.py utcnow ×2 经核实**当前代码已不存在**（grep 全仓为 0，评审行号对应
的应是修复前快照）。未动的两项：`_execute_harness_run_managed` 复杂度重构与
ruff 配置进 CI（ infra 级，单独批次处理）。

### 复检：抓到并修复一个 P0 回归（装饰器错位）+ 路由注册护栏

外部复检在验证上述修复时发现一个 **P0 回归**：插入 `_advance_event_cursor`
辅助函数时锚点落在装饰器与函数之间，`@router.get("/runs/{run_id}/events")`
被辅助函数抢占——FastAPI 不校验路径参数是否存在于函数签名，启动静默通过，
但事件流/轮询接口全部 422 失效。**此前 1158 个测试全是函数级调用，没有一个
HTTP 往返，因此未拦截。**

处置：

- 装饰器下移归位（复检完成），端到端验证 JSON/SSE 模式均正确到达端点；
- **新增 `backend/tests/test_route_registration.py` 路由注册护栏**（3 断言）：
  ① openapi 路径存在且绑定函数为真实端点（operationId 暴露函数名，装饰器
  错位即偏离）；② 全应用任何路由不得被下划线开头的私有辅助函数占据；
  ③ HTTP 往返——带认证请求进入端点逻辑（不存在 run → 404 而非 422），
  无认证 → 401 认证拦截；
- 复检同时确认并修复：config.py 的 2 处 `datetime.utcnow()`（此前 grep 未
  命中系时序误差，以复检为准，现全仓为 0）；`registry/capabilities.py` 的
  F821 用 `TYPE_CHECKING` 块消除（原本是延迟导入的良性误报）；
- 顺带核查 `attachment_security.py` 改动为**安全增强**（新增 docx 支持同时
  补 Open XML ZIP 结构校验，xls/xlsx 混判收紧），无防护削弱。

### 验收（复检追加）

| 门禁 | 结果 |
|---|---|
| 全量后端测试 | **1170 passed**（追加 3 条路由注册护栏） |
| 路由挂载 | `/runs/{run_id}/events` → `get_run_events`，无任何私有函数占位 |
| 静态 | F821=0、PLW0127=0、utcnow=0、绝对导入=0 |
| 前端 | npm test 125/125 |

### 验收（运行时稳定性评审修复）

| 门禁 | 结果 |
|---|---|
| 全量后端测试 | **1173 passed**（含注册表跨序清理新增回归） |
| SSE 游标 | seq 跳跃场景游标推进到 max(seq)，重复推送缺陷锁定回归 |
| 注册表 | 软上限触发清理、僵尸强制失败、新鲜非终态不误杀 |
| Worker | waiting_ocr 幂等、僵尸 requeue/failed、当前任务豁免 |

### 验收（P4 追加）

| 门禁 | 结果 |
|---|---|
| 全量后端测试 | **1151 passed**（追加 8 个黄金断言 + 3 条全链路冒烟 + 10 条试卷记忆） |
| 黄金样例 | 8/8，连续 3 次运行稳定（修复 evidence 随机 ID 造成的偶然断言） |
| 工具一致性 | TOOL_DEFS == bridge 白名单（含 get_teaching_guidance） |
| 全链路冒烟 | 考试分析/review_plan 标准路径 + general_chat 边界，模拟模型经真实 bridge 子进程跑通 |
| 迁移 | 空库 0001 → 20260830_0025 升级通过（create_app 自动迁移验证） |

### 全量复查收口（同日最终）

- 修复试卷记忆新草稿提前撤销 confirmed 版本、GET/confirm 跨学期访问、
  记忆 evidence ID 未进入模型可见文本三处闭环问题；
- 修复注册表最老记录为运行态时，后续终态记录无法被数量护栏清理的问题；
- 同步 e2e 运行时迁移断言到 `20260830_0025`；
- 发布门禁：后端 **1173 passed**、前端 **125 passed**、浏览器端到端
  **22 passed**；空库迁移、依赖完整性、敏感文件扫描与发布清单均通过。

### 已知边界与后续

- harness 插件 tools/list 仍为静态声明（受宿主 API 限制）：数据工具由
  mjs 预检 + bridge 策略门双层拒绝，模型一次重试即收敛；
- v2/v3 真实模型对比评测（模型轮数、实测 token、报告质量）需要接真实
  provider 后按黄金样例跑批，未做；
- experience/ 非结构化经验库（FTS5 先行）按 v3 触发条件暂缓。

### 过程消息真实性修复（2026-09-03）

- `run.started` 仅表示任务生命周期开始，不再被前端伪装成“已接收分析任务/准备数据”步骤；只有后端真实发来的 `tool`、`model`、`step` 事件才进入过程消息。
- 当运行暂时没有真实步骤事件时，界面只显示“分析中”，不展示静态工作流；真实步骤到达后才展开过程卡，并原地更新完成状态。
- 前端回归测试覆盖无步骤兜底、重复 `run.started`、真实工具行更新及 DOM 节点稳定性。
- 普通聊天追问若当前会话本身未写入 `exam_id`，服务端会从同一会话最近一次有明确考试范围的运行继承只读作用域；同时强化提示词，分组/分层/复习等数据相关追问先查真实统计再回答。
- 修复 TeachMate 输入区班级选择器：明确选择“全部班级”后按钮显示“全部班级”，不再回退到占位文案“选择班级”。

### 2026-09-03：隐藏 MONI 前端插件入口
- **现象**：MONI 是 TeachMate 的后台只读数据源，但设置页把它作为可操作插件展示，容易让教师误以为需要在前端管理或主动调用。
- **处理**：将内置 `moni` manifest 的 `ui_visible` 设为 `false`。现有前端插件清单会过滤该标记，因此设置页和插件选择器不显示 MONI；后端插件发现、MONI 导入/同步及数据库数据不受影响。
- **验证**：补充前端回归测试，确认隐藏数据源不出现在插件卡片、数量统计和页面文本中。
