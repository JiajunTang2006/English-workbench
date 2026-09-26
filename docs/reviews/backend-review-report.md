# 后端代码审查报告

审查时间：2026-08-28
范围：`backend/app` 全量（263 个 .py 文件），分 4 路并行审查（核心基础设施 / 路由层 / 服务层 / Agent 子系统），所有 P0/P1 发现均已人工二次核实代码上下文。

---

## 一、P0 — 必须修复的真实 bug（已逐条核实）

### 1. 启动恢复语义冲突：running 任务被永久判死，重试机制失效
- **位置**：`app/factory.py:60-67` + `app/services/agent_runs/recovery.py:382-392` + `app/services/job_worker.py:314-341` + `app/factory.py:141`
- **问题**：启动时 `run_full_recovery` 先把所有 `running` 的 BackgroundJob 直接置 `failed`（注释"不支持断点续传"）；之后 `worker.recover_on_startup()` 才扫描 `status=="running"` 按 attempts 退避重排队——此时已经扫不到任何 running 任务，B2-05 设计的"中断任务指数退避重试"永远不会生效。
- **修复**：让 `recovery.py` 的 job 分支复用 `JobWorker.recover_on_startup` 的重试语义（attempts < max 时重排队），或删除 `factory.py:141` 的 `recover_on_startup()` 调用并明确唯一恢复入口。

### 2. confirm_run 先提交状态再校验，校验失败后运行永久卡死
- **位置**：`app/routers/agent.py:1171-1196`
- **问题**：`transition_status(run.id, "queued")` + `commit()` 发生在原始用户消息校验之前；若找不到 `user_content`，抛 409 时 run 已落库为 `queued`，但没有任何后台任务接管，永远停在排队态。
- **修复**：先查 `user_content` 再做状态转换；或 409 分支中将状态回滚为 `waiting_confirmation`。

### 3. 证据持久化字段名不匹配，`local_fact_json` 永远为空
- **位置**：`app/agent/run_executor.py:313-321` vs `app/agent/evidence.py:107-108`
- **问题**：`EvidenceLedger.for_display()` 输出的键是 `"fact"`/`"summary"`，落库代码却用 `ev.get("local_fact")`、`ev.get("display_summary")` 取值 → `analysis_evidence.local_fact_json` 永远写空 dict、`display_summary` 永远为 None。降级报告（`_build_local_fallback_report`，run_executor.py:627）读到的 findings 全是空 detail，审计链路事实数据丢失。
- **修复**：落库改为 `ev.get("fact", {})` / `ev.get("summary")`；补一条单测断言 `local_fact_json` 非空。

### 4. preview_payload 对未知题目引用抛 KeyError 而非校验错误
- **位置**：`app/services/school_sync.py:113`
- **问题**：`question_map[item.question_external_id]` 在 item_scores 引用不存在的题目时直接 KeyError；`apply_payload` 第一行就调 preview，外部 MCP 同步数据会以 500 告终，而不是进入 `preview.errors` 的友好校验分支。
- **修复**：`question = question_map.get(...)`，为 None 时 `errors.append(f"{student.name} 引用了不存在的题目 {id}")`。

### 5. Education Bridge 串行锁可能泄漏，后续所有分析任务永久排队
- **位置**：`app/agent/run_executor.py:957-968`
- **问题**：`await bridge_lock.acquire()` 成功后、进入内层 `try:` 之前，`_set_run_status`/`session.commit()` 若抛异常会直接跳到外层 except 并 return，`finally` 中的 `release()` 不执行 → 锁被永久持有。
- **修复**：把 `acquire()` 之后的全部代码（含 commit）移入带 `finally: release()` 的 try 块，或改用 `async with bridge_lock:`。

### 6. BackgroundJobManager 的 `_running` 只进不出，内存无限增长
- **位置**：`app/services/background_jobs.py:41,75-79`
- **问题**：任务完成后没有任何路径从 `_running` 移除（全文件仅有 `:75` 一处写入），每个短任务永久持有 payload/result/事件对象，长驻进程确定性泄漏。
- **修复**：终态任务移入有上限的 `_finished`（如 OrderedDict 保留最近 200 条，超限淘汰），`get_status` 先查 running 再查 finished。

---

## 二、P1 — 高优先级风险

| # | 位置 | 问题 | 建议 |
|---|------|------|------|
| 7 | `routers/agent.py:2275-2287` | async 路由内用同步 `httpx.Client(timeout=15)`，测试连接期间整个事件循环停摆（SSE 心跳、其他请求全部阻塞） | 改 `httpx.AsyncClient` 或函数改 `def` 走线程池 |
| 8 | `agent/loop.py:359-362, 351-357` | `repair_call` 把**未脱敏**的 user_message 和验证失败的原始输出（可能正是因含隐私才失败）原文发给第三方 Provider；Harness 路径 `_repair_turn` 做了脱敏，legacy 路径漏了 | 在 repair_call 中先 `sanitize_text()` |
| 9 | `agent/task_registry.py:188-189, 212-213` | `complete()/fail()` 在锁外写 `state.events[-1].seq`，并发 emit 时 seq 会标到错误事件上，SSE 游标错乱 | 锁内捕获 event 引用，锁外只写 `event.seq` |
| 10 | `services/moni_sync.py:336-343` | 分页回退：`len(rows)==limit` 就继续 offset 翻页，若服务端忽略 offset 将死循环 | 加 max_pages 上限（如 100） |
| 11 | `services/exam_ingestion.py:101` | `subprocess.run(timeout=90)` 的 `TimeoutExpired` 逃逸，唯超时以 500 而非统一错误契约返回 | 捕获后返回 `{"ok": False, "error": "pdf_render_timeout"}` |
| 12 | `factory.py:203` | `compare_digest` 遇非 ASCII token 抛 TypeError（客户端可控输入触发未处理异常）；且鉴权在 `accept()` 之后 | `compare_digest(a.encode(), b.encode())`；鉴权通过后再 accept |
| 13 | `routers/vision.py:74/98/108/121`、`documents.py:69/89/100` | SQLAlchemy Session 从不关闭，依赖 GC 兜底，长运行可能耗尽连接池 | 统一改 `with get_session(request) as db:` |
| 14 | `routers/agent.py:2019-2028` | 删除当前激活的模型档案不清理 `text_model_profile_id`，留下悬空引用且 Key 已被清除 | 删除时同步清理 runtime config |
| 15 | `services/document_parser.py:168-175` | multiprocessing 未锁定 start method；spawn 子进程重 import `__main__` 副作用 + 每次重新 import pdfplumber 吃掉 1-3s 超时预算 | 显式 `get_context("spawn")`；入口脚本确认有 `__main__` guard；中期改常驻进程池 |
| 16 | `services/backups.py:188` | 备份 manifest 的 `schema` 硬编码为当前代码版本，pre-migration 备份的 schema 标记失真，`restore_backup` 版本校验形同虚设 | 从库内 `alembic_version` 读真实 revision |

## 三、P2 — 值得排期的优化与次要缺陷（摘要）

**性能 / 阻塞事件循环类**
- `agent/loop.py:158-161`：预算门禁 token 估算用 `len(json)//4`，中文低估约 4 倍；已有 CJK 感知的 `estimate_text_tokens` 未被使用，门禁形同虚设。
- `agent/loop.py:275-291`：`max_parallel_tools` 名不副实，工具实际串行执行，4 个工具最坏 120s+，建议 `asyncio.gather`。
- `routers/agent.py:624-632`：附件全文 token 估算（可达数十 MB）同步阻塞事件循环，建议 `asyncio.to_thread`。
- `routers/attachments.py:160-172`：async 上传路由内同步分块写盘。
- `routers/plugin.py:442-486`：N+1 查询 + limit 后置；`plugin.py:377-397` 重复全量查询。
- `services/school_sync.py:422-508`：学生循环内 N+1 密集（50 人 × 20 题一次同步 1000+ 查询）。
- `agent/tools/exam_tools.py:285-300`：`_get_knowledge_coverage` 每题 2 条 SQL 的 N+1。
- `services/agent_runs/event_store.py:133-158`：asyncio.Lock 内同步 DB flush 阻塞事件循环；`:293` O(n²) 去重。

**正确性 / 健壮性类**
- `routers/agent.py:550`：非考试会话首条消息必然 400（capability 回退 `exam_analysis` 但无 exam_id）。
- `routers/agent.py:1288-1294`：防重复提交的 `scope_json.contains(...)` LIKE 模式恒为假，属死代码。
- `services/file_operations.py:28-61`：失败操作无限重试，attempts 不封顶。
- `services/plugin_manager.py:259-261`：插件升级先 rmtree 再 replace，中途崩溃插件丢失；`:441-465` `call_tool` 未捕获 `TimeoutExpired`。
- `services/agent_runs/event_store.py:185-194`：按 run_id 淘汰旧事件不看是否 active，活跃 run 的 SSE 增量订阅可能断流。
- `agent/loop.py:200-209` + `agent/cost.py:162-171`：usage 缺失或预算控制关闭时成本恒 0，`budget_exceeded` 不可达。
- `agent/run_executor.py:1202-1204`：Harness managed 路径 tokens/cost 恒 0，计费审计主路径盲区。
- `agent/run_executor.py:575-577`：修复轮的 OutboundObserver 是新实例，出站审计 JSONL 断档。
- `services/job_worker.py:596`：backoff 入参语义差一档（首次重试实际 4s 而非文档的 BASE）。
- `services/vision/openai_compat.py:88-91`：429/5xx 重试无退避间隔。
- `agent/privacy.py:302,331`：学号正则把 6-10 位数字（含日期、考号）一律脱敏，破坏发给模型的数据事实。

**基础设施小项**
- `services/backups.py:27,69,133,177`：`with sqlite3.connect(...)` 只 commit 不 close，fd 靠 GC 释放。
- `app/database.py:76,94`：迁移专用 engine 未设 `busy_timeout`，双开实例启动时直接 `database is locked`。
- `app/factory.py:51-55`：每次启动全量反序列化所有 WorkspaceState 探测 legacy 文档，建议加迁移完成标志跳过。
- `app/__main__.py:8`：冻结环境下 `uvicorn.run("backend.app.main:app")` 字符串导入是 PyInstaller 常见失败点，建议直接传 app 对象。
- `app/config.py:59`：`os.sys.platform` 未公开 API，改 `sys.platform`。
- `routers/plugins.py:116-127`：目录安装跟随 symlink 且无大小限制。
- `routers/school_sync.py:59-72`：自定义凭证头（X-API-Key 等）GET 时原样回显。
- `app/routers/agent.py`：2363 行单文件承载 5 个职责域，建议拆分（会话/运行/预算/附件/Provider）。

## 四、审查确认无问题的关键面

- `attachment_security.safe_storage_path`、插件 `_safe_extract`（zip slip）、`plugin_auth`（sha256 + 一次性配对码）安全设计扎实。
- `keyvault.py` 原子写 + 0600 + fail-closed；三个 Provider 不记录 headers/请求体，402 立即抛错不重试，无 key 泄漏路径。
- `exams.py` revision CAS 乐观锁正确；`create_backup` 用 SQLite 在线 backup API，WAL 下一致性正确。
- 实体模型全部参数化绑定，无 SQL 注入面。

---

## 建议修复顺序

1. **第一批（数据/流程正确性，改动小）**：#1 恢复冲突、#2 confirm_run 顺序、#3 证据字段名、#4 KeyError、#5 锁泄漏、#6 内存泄漏
2. **第二批（隐私与稳定）**：#8 repair_call 脱敏、#9 游标错乱、#10 死循环上限、#11 超时契约、#12 非 ASCII token
3. **第三批（体验/性能）**：P2 中的事件循环阻塞类与 N+1 类
