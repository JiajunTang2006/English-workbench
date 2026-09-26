# 项目长期记忆（WorkBench / TeachMate）

## 最近补丁记录

- **2026-09-02 学生诊断全班画像**：插件显示名改为“学生诊断”；未写姓名时默认
  对当前班级全体学生生成画像，写明姓名或学号时保留单人诊断。同时修复旧交互脚本
  缓存导致仍追问姓名的问题。修复和重新打包检查见
  [`2026-09-02-student-diagnosis-batch-hotfix.md`](2026-09-02-student-diagnosis-batch-hotfix.md)。
- **2026-09-02 MCP / MONI 接口问题**：TeachMate 曾请求不存在的
  `/api/v1/core/classes`、`/api/v1/core/students`，并且无法把 MONI 正式班级名
  `七年级11班` 与 WorkBench 缩写 `711` 识别为同一班级，导致同步后还要额外点
  “保存设置”。根因、修复、回归测试和下次 Windows 补丁清单见
  [`2026-09-02-mcp-interface-hotfix-notes.md`](2026-09-02-mcp-interface-hotfix-notes.md)。

## 前端架构与关键约定

- **TeachMate 与 WorkBench 双模式外壳**：`workbench.html` 加载 `workbench-app.css` + `workbench-enhancements.css` + `teachmate.css` 三套样式，`enhancements.css` 后加载、优先级最高（其 `!important`/后定义规则会压过 `teachmate.css`）。
- **sidebar 宽度控制（重要）**：
  - WorkBench：`workbench-app.css` 的 `aside { width }` 与 `workbench-enhancements.css` 的 `--wb-sidebar-width` 变量共同决定（变量还驱动顶栏第一列对齐）。当前桌面 = **260px**。
  - TeachMate：**真实宽度由 `workbench-enhancements.css` 控制** —— `body.tab-teachmate { --tm-sidebar-width }` + `body.tab-teachmate #sidebar { width: var(--tm-sidebar-width) !important }`；该变量同时驱动 `body.tab-teachmate header` 第一列 grid。当前桌面 = **260px**（与 WorkBench 统一）。
  - ⚠️ `teachmate.css` 里 `body.tab-teachmate #sidebar { width: 240px/272px }` 及媒体查询里的 232/252/min(288,86vw) **均为被 !important 覆盖的死代码**，改 TeachMate 视觉宽度/对齐应直接改 `--tm-sidebar-width` 变量，别在 teachmate.css 反复改被覆盖的 base。
- **WorkBench/TeachMate 顶部切换器**：在 `.header-left-zone`（header grid 第一列，宽度=`--wb-sidebar-width`）内；若被 `flex:1 1 auto` 拉伸，需改成 `flex:0 0 auto` + 内部 `.nav-tab-switcher { width:auto }`（见 enhancements.css line ~802）。
- **版本号强刷**：改动 CSS 后务必 bump `workbench.html` 里对应 `<link>?v=...`，否则浏览器/打包缓存导致不生效；macOS 打包 App 需重新构建才生效。

## 验证方式

- Playwright 验证脚本统一放 `tools/verify-*.cjs`，用 `node tools/xxx.cjs` 跑（node 走 managed 路径 `/Users/tangjiajun/.workbuddy/binaries/node/versions/22.22.2/bin/node`）。
- file:// 直接打开 `workbench.html` 时，`/api/v1/*` 的 fetch 必然失败（scheme 不支持），属预期，验证布局时忽略这类 console error。

## TeachMate 视觉系统（Miro 风格，2026-08-20 定版）

- **主色 `--tm-primary` = `#1c1c1e`**（近黑，Miro 黑药丸）。`#5268f7` 靛蓝是 AI 通用紫，是首要去 AI 味对象。
- **品牌黄 `--tm-brand-yellow` = `#ffd02f`**（Miro 签名黄）。品牌徽标 `.tm-brand-mark` = 黄底 + `#1c1c1e` 黑字。
- **品牌蓝 `#4262ff`** 用于链接、焦点环 `rgba(66,98,255,.22)`、状态图标（附件状态、考试选择）。不是主色，是辅助强调。
- **便利贴三色系**（建议卡 `.tm-suggestion-*`）：黄 `#fff4c4`、薄荷绿 `#d8fbf6`（teal-light）、柔和粉 `#fde0f0`（brand-rose）。Miro 风格核心视觉，禁止替换为靛蓝/紫色。
- **中性灰尺度**（替代原来蓝调灰）：`--tm-bg #f7f8fa`（surface）、`--tm-surface #ffffff`、`--tm-surface-subtle #fafbfc`（surface-soft）、`--tm-primary-soft #f4f5f7`、`--tm-border #e0e2e8`（hairline）、`--tm-border-strong #c7cad5`（hairline-strong）。`--tm-text #1c1c1e`（ink）、`--tm-text-secondary #555a6a`（slate）、`--tm-text-muted #8e91a0`（stone）。
- **语义色**：success `#00b473`（Miro success-accent）、warning `#b45309`（橙色，替代深黄 #8A6D00）、danger `#d1383f`。
- **阴影色统一近黑 `rgba(28,28,30,...)`**，禁用 `rgba(11,18,51,...)` 蓝调阴影。
- **「去 AI 味」检查清单**：
  1. 不能有 `#6075fa→#4f64eb` 之类的靛蓝渐变（用户气泡、发送按钮、品牌徽标等）。
  2. 不能有 `#f1f3ff`/`#eef1ff`/`#e9edff` 靛蓝淡底（已被中性灰取代）。
  3. 不能有 `--tm-primary: #5268f7` / `#566cf6`（靛蓝主色）。
  4. 焦点环可用 Miro 蓝 `rgba(66,98,255,...)`（不是 AI 味）。
- **改动范围约束**：仅 teachmate.css + workbench.html；workbench-app.css、workbench-enhancements.css 与 JS 不动。

## 后端约定与踩过的坑（2026-08-30）

- **FastAPI 路由装饰器陷阱（已踩，务必注意）**：在路由函数**上方**插入新的辅助函数时，原本的 `@router.get(...)` 会被新函数"继承"，导致真正的处理函数失去路由注册。FastAPI **不校验路径参数是否存在于函数签名**，启动时静默通过、不报错，只有运行期才暴露（通常表现为 422）。
  - 检测手段：单元测试若只直接调用内部函数则**抓不到**，必须做端点级 HTTP 往返断言。
  - 自检脚本：`from app.routers.agent import router; [(r.path, r.endpoint.__name__) for r in router.routes]`。
- **SSE 事件游标语义**：游标是 EventStore 的**全局 seq**（跨 run 单调递增），不能用"条数 +1"推进，否则多 run 并发导致 seq 跳跃时会重复推送。统一走 `_advance_event_cursor(index, ev)`（agent.py:863），`seq is None` 的纯内存事件才退化为 `+1`。
- **时间字段统一用 aware**：全项目统一 `datetime.now(timezone.utc)`，禁用 `datetime.utcnow()`（naive，Py3.12 起弃用）。需要时复用 `app.models.entities.utcnow()` helper。
- **注册表内存护栏**：`TaskRegistry._runs` 是全局单例且只增不减，`cleanup_old()` 由 `register()` 在锁外触发（`_RUNS_SOFT_LIMIT=100`）；另用 `stale_running_seconds`（6h）把僵尸非终态强制 failed。`RunState.created_at` 用 `time.monotonic()`，与清理逻辑时钟同源。
- **导入风格**：统一相对导入，禁用 `from backend.app.*` 绝对导入（PyInstaller 冻结打包时会踩坑）。
- **后台任务非终态状态集**：新增任何非终态 status（如 `waiting_ocr`）时，必须同步更新 `submit_job` 的幂等状态集，否则重复提交会产生第二个任务、重复计费。

## Agent / 第三方 API 链路（2026-08-20）

- **DeepSeek 账户当前余额不足**：真实请求返回 HTTP 402 Insufficient Balance；Key 有效、鉴权/网络/模型名全部正常，充值前无法出词。402 已在 `deepseek_text.py` / `openai_compat_text.py` / `routers/agent.py /provider/test` 识别为可读提示（「连接可达但余额不足」）。
- **Key 存储**：应用 keyvault = `<data_dir>/provider/api_key`（0600）；本机实际路径为 `~/Library/Application Support/冯老师初中英语工作台/provider/api_key`（default_data_dir 命中 legacy 目录）。前端「测试连接」显示 402 提示即 Key 已配置。
- **附件发送语义**：PDF/Excel 走「本地解析成文本 → 教师确认 → FormalContextProvider 注入模型上下文」，**不是**把原文件二进制上传给 DeepSeek（Chat Completions 不支持文件上传）；若要上传原文件需接支持文件的服务。
- **DocumentParser 用 multiprocessing 做进程级超时**：CLI 脚本调用必须加 `if __name__ == "__main__":` guard，否则 spawn 子进程重跑主模块抛 RuntimeError（uvicorn 运行无此问题）。
