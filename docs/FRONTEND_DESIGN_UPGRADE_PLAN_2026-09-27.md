# 前端设计升级规划（对标 GitHub · 2026-09-27）

> 目标：参考成熟开源项目的前端设计语言，把 WorkBench + TeachMate 从「能用的内部管理系统」推进到「有主张、有节奏、有交互反馈」的产品级界面。
> 范围约束：**只做设计语言与交互规划，不改动任何代码**。所有内容严格落在 `workbench-assets/` 与 TeachMate 模块内，不触碰 WorkBench shell（含 Miro overlay）。

---

## 0. 结论先行

1. 现在最大的问题不是「配色不好看」，而是 **两套设计语言并行**：shell 走 MD3 语义令牌（`--md-*`），TeachMate 走自建令牌（`--tm-*`），且 `workbench-app.css` 里仍残留 Tailwind 硬编码色（`#f1f5f9` / `#eff6ff` / `#f4f5f7` / `#dbeafe`）。视觉上呈现「半边工业后台、半边暖纸应用」。
2. 次大的问题不是「缺动效」，而是 **缺通用交互层**：没有统一的动效 token、没有 Toast + 撤销、没有骨架屏、没有命令面板、没有分层空状态。这些是「高级感」的地基，比加花哨动画重要得多。
3. 参照物要分三类去抄，而不是只抄一家：**教育后台抄信息密度**（art-design-pro / shadcn-admin），**AI 对话抄可操作性**（assistant-ui / vercel chatbot / agent-chat-ui），**成长森林抄成就感**（kana-dojo / react-duolingo）。本项目独有的优势是「本地优先 + 有真实 agent 调用链」，这两点在开源里对标项很少，值得做成差异化。

---

## 1. 对标清单（已逐个校验存在性与活跃度）

### A. 信息密集型教育后台 —— 借「密度、层级、导航」

| 仓库 | ★ | 语言 | 借什么 |
|---|---|---|---|
| [Daymychen/art-design-pro](https://github.com/Daymychen/art-design-pro) | 5.9k | Vue | **首选参考**。中文团队、设计驱动的后台：分组可折叠侧栏、卡片 hover 抬升、Tab 页签工作区、克制的灰阶。与你的「暖纸 + 中性灰」取向最接近 |
| [satnaing/shadcn-admin](https://github.com/satnaing/shadcn-admin) | 14.6k | TS | 现代 admin 组件基线：**⌘K 命令面板**、数据表列固定/批量操作、**分层空状态**、主题切换、统一焦点环 |
| [frappe/lms](https://github.com/frappe/lms) | 3.3k | TS | 课程/班级卡片体系、列表→详情的主从布局、Espresso UI 的克制配色 |
| [vbenjs/vue-vben-admin](https://github.com/vbenjs/vue-vben-admin) | 33.5k | Vue | 多级菜单 + **标签页工作区（tab bar）**范式，适合你 11 个模块的频繁切换 |
| [rolling-scopes/rsschool-app](https://github.com/rolling-scopes/rsschool-app) | 10.4k | TS | 学生视角的成绩/任务仪表盘，信息密度处理得很好 |
| [openedx/openedx-platform](https://github.com/openedx/openedx-platform) | 8.2k | Python | 教务级导航分组与面包屑的信息架构 |
| [oppia/oppia](https://github.com/oppia/oppia) | 6.8k | Python | 学习者向的「闯关 / 进度 / 庆祝」状态机，直接可映射到成长森林 |

### B. AI 对话界面 —— 借「流式、可操作、可追溯」

| 仓库 | ★ | 语言 | 借什么 |
|---|---|---|---|
| [assistant-ui/assistant-ui](https://github.com/assistant-ui/assistant-ui) | 12.3k | TS | 消息状态机（running / complete / error）、**工具调用折叠块**、Copy / Retry / 分支 |
| [vercel/chatbot](https://github.com/vercel/chatbot) | 21k | TS | 消息级 hover 操作、**一轮结束后的 Suggested Actions**、Artifact 侧栏、滚动到底 pill |
| [langchain-ai/agent-chat-ui](https://github.com/langchain-ai/agent-chat-ui) | 3.2k | TS | **工具调用可视化 + 逐步推理展示**——正对你后端的 SSE / job workers / agent routers |
| [mckaywrigley/chatbot-ui](https://github.com/mckaywrigley/chatbot-ui) | 33.3k | TS | 会话侧栏信息架构（搜索 / 文件夹 / 置顶 / 重命名） |
| [lobehub/lobehub](https://github.com/lobehub/lobehub) | 82.8k | TS | 中文 AI 产品最佳实践：模型切换、指令快捷栏、插件入口、暗色打磨 |
| [udecode/plate](https://github.com/udecode/plate) | 16.6k | TS | 若「原卷与错题」后续要做富文本批注，这是现成底座 |

### C. 游戏化 / 成长 —— 借「成就感」

| 仓库 | ★ | 语言 | 借什么 |
|---|---|---|---|
| [lingdojo/kana-dojo](https://github.com/lingdojo/kana-dojo) | 3.5k | TS | Duolingo 式极简：XP 条、连续天数、路径节点、恰到好处的微动效 |
| [bryanjenningz/react-duolingo](https://github.com/bryanjenningz/react-duolingo) | 469 | TS | **3D 厚底按钮**（按下位移 2px）、关卡路径地图 |
| [sanidhyy/duolingo-clone](https://github.com/sanidhyy/duolingo-clone) | 657 | TS | 关卡地图 + 结算庆祝页 |
| [ankitects/anki](https://github.com/ankitects/anki) | 31.6k | Rust | 「今日待复习」队列 UI，可映射到「待办事项」 |

### D. 动效与微交互 —— 借「配方，而不是库」

| 仓库 | ★ | 语言 | 借什么 |
|---|---|---|---|
| [DavidHDev/react-bits](https://github.com/DavidHDev/react-bits) | 48k | JS | 可直接抄的动效配方：Spotlight、Animated List、Tilt Card |
| [magicuidesign/magicui](https://github.com/magicuidesign/magicui) | 22.4k | MDX | 数字滚动、边框光束、渐变文字、Dock |
| [barvian/number-flow](https://github.com/barvian/number-flow) | 7.7k | TS | **KPI 数字滚动**——对成绩、均分、营养值很关键 |
| [imskyleen/animate-ui](https://github.com/imskyleen/animate-ui) | 4.3k | TS | Motion 驱动的 Tab 滑块、开关过渡 |
| [tremorlabs/tremor](https://github.com/tremorlabs/tremor) | 3.6k | TS | 仪表盘组件与图表卡片范式、骨架屏 |
| [tsparticles/tsparticles](https://github.com/tsparticles/tsparticles) | 9k | TS | 里程碑撒花（低频高光场合用） |

### E. 数据可视化

| 仓库 | ★ | 语言 | 借什么 |
|---|---|---|---|
| [apache/echarts](https://github.com/apache/echarts) | 67.4k | TS | 项目已内置。建议启用 `dataZoom` / `brush` / `markLine` / 自定义主题 |
| [microsoft/data-formulator](https://github.com/microsoft/data-formulator) | 17.4k | Python | 「对话式分析」交互范式：图表可由 AI 直接创建与改写——与你 agent 能力天然契合 |

### F. 本地优先桌面应用的「气质」

| 仓库 | ★ | 语言 | 借什么 |
|---|---|---|---|
| [actualbudget/actual](https://github.com/actualbudget/actual) | 29.2k | TS | 本地优先的**即时感**（无 loading 兜底）、**撤销 Toast**、键盘优先 |
| [codexu/note-gen](https://github.com/codexu/note-gen) | 12.8k | TS | 中文本地优先 + AI：排版克制、动效节制，与你的定位最像 |

### G. 组件底座

| 仓库 | ★ | 语言 | 借什么 |
|---|---|---|---|
| [shadcn-ui/ui](https://github.com/shadcn-ui/ui) | 124.7k | TS | 可访问性基线、统一焦点环、变体体系（不迁框架也能抄思想） |

---

## 2. 现状诊断（可逐条核对）

| # | 问题 | 证据位置 |
|---|---|---|
| 1 | **两套令牌并行 + 硬编码残色** | `workbench-app.css` 中 `#f1f5f9` / `#eff6ff` / `#f4f5f7` / `#dbeafe` / `#dcfce7`；`teachmate.css` 自建 `--tm-*` 21 个令牌 |
| 2 | **导航偏「内部系统」** | `aside .nav-item` 仅 hover 背景 + active 浅蓝底 `#eff6ff`；无左侧指示条、无分组（`MODULES` 里 `growth` 靠 `parent:'stu'` 隐式缩进）、无折叠态、无 ⌘K |
| 3 | **KPI 卡模板化** | `.kpi` = 淡色底 + 24px 数字 + 12px 标签；无环比、无 sparkline、无异常标记 |
| 4 | **空状态纯文本** | 仪表盘「本场考试尚未设置分层线，暂无从层分布数据」、趋势「暂无考试趋势数据」、成长树同样 |
| 5 | **表格是纯后台风** | `th{background:#f4f5f7}`、`tr:nth-child(even) td{background:#fafafa}`、`tr:hover td{background:#f1f5f9}`；斑马纹 + 灰表头 + 全边框 |
| 6 | **聊天区缺 AI 产品标配** | 无流式光标、无「思考中 / 工具调用」折叠块、无消息级操作（复制 / 重试 / 引用）、无追问 chips（仅首屏 3 张静态建议卡） |
| 7 | **成长森林表现力不足** | 卡片只有「距离小树苗还差 23」+ 4px 细进度条 + 「本周 +57 记录 4 条」；无生长隐喻、无跃迁庆祝、无班级横向对比 |
| 8 | **缺通用交互层** | 无 Toast + 撤销（对补录 / 撤销 / 更正极关键）、无骨架屏、动效节奏散落（`.16s` / `0.2s` / `0.15s` 混用）、无统一动效 token |
| 9 | **大屏未「占满」** | `main{padding:20px}` + 固定卡片 gap；≥1600px 时右侧留白，未做「密度随宽度」策略 |

---

## 3. 升级规划（四层）

### L0 — 令牌层：统一地基

- **收敛为单一语义令牌**：保留 `--md-*` 作为壳层语义名，把 `--tm-*` 的值**映射到同一套语义名**（只改值、不改名，避免大范围替换类名）。
- **补齐缺失令牌**：
  - 动效：`--dur-1: 120ms` / `--dur-2: 200ms` / `--dur-3: 320ms`；`--ease-standard: cubic-bezier(.2,0,0,1)`；`--ease-emphasized: cubic-bezier(.2,0,0,1.2)`
  - 层级：`--z-nav` / `--z-dropdown` / `--z-modal` / `--z-toast`
  - 密度：`--density-compact` / `--density-cozy`
- **颜色收敛**：清除硬编码 Tailwind 残色，统一走语义色；`needs improvement` 保持橙色（不用深黄 `#8A6D00`）。
- **数字排版**：全局 `font-variant-numeric: tabular-nums`；标题数字建立字号 / 字重阶梯，避免「24px 到处跑」。

### L1 — 基础组件层：先立骨架

| 组件 | 做法 | 参考 |
|---|---|---|
| 侧栏 | 分组 + 可折叠 + **主色 2px 指示条滑动** + 折叠态图标浮出 tooltip | art-design-pro / shadcn-admin |
| 顶栏 | 面包屑 + 页面标题 + 右侧主操作；**⌘K 命令面板**作为一级导航 | shadcn-admin |
| 数据表 | 去掉斑马纹，改「发丝分隔线 + 行 hover 抬升 + 列固定 + 行选择 + 批量操作条」；sticky 表头滚动加阴影 | art-design-pro |
| 空状态 | 插画（项目已有 `workbench-assets/illustrations/`）+ 标题 + 说明 + **下一步 CTA** | shadcn-admin / oppia |
| 加载 | 骨架屏替代空白（首次加载现在完全空白） | tremor |
| 反馈 | Toast + **「撤销」按钮**，用于补录 / 撤销 / 更正 | actual budget |
| 焦点环 | 统一 `--focus-ring`，键盘完全可达 | shadcn-ui |

### L2 — 领域组件层：把「教学」做出来

- **KPI 卡**：数字滚动 + 环比 delta（**涨红跌绿**）+ 迷你 sparkline + 点击下钻。无数据用「待录入」态，**绝不用 0 填充**。
- **图表**：ECharts 换暖纸主题（轴 / 网格 / tooltip 全部走 token）；启用 `markLine`（年级均分 / 及格线）、`dataZoom`、`brush`；趋势图加「考试节点」标记。
- **成长森林**：
  - 细条 → 有生长感的进度（环形 / 茎叶形态）
  - 阶段跃迁时撒花 + 卡片光晕（低频高光，只在跃迁触发）
  - 增加「班级营养榜」横向条，补上缺失的横向对比
- **聊天**：
  - 流式光标 + 打字节奏
  - **「思考中 / 工具调用」折叠块**——你有 SSE / job / agent 后端，这是刚需而非装饰
  - 消息 hover 操作：复制 / 重试 / 引用为新问题
  - 一轮结束后追加「追问 chips」
  - 证据引用可点击，跳回来源记录（契合你的「事件账本」约定）
- **成长详情账本**：快速补录 chips 从粉色调改为暖纸中性 + 主色描边；表格视觉改「时间轴 / 账本」而非普通表格——语义本来就是事件账本。
- **范围切换**：班级 / 学期切换统一为「分段控件 + 滑块动画」，切换时内容交叉淡入。

### L3 — 页面 / 布局层：占满与节奏

- **密度随宽度**：`≥1600px` 三栏（导航 / 主区 / 洞察侧栏），`1280–1600px` 两栏，`<1280px` 单栏 + 抽屉。让大屏真正被用起来。
- **页面统一骨架**：页头（标题 + 范围 + 操作）→ KPI 条 → 主区（表格 / 图表）→ 次级区。
- **滚动分层**：整页只滚 `main`；卡片内表格独立滚动 + sticky 表头（部分已做，需统一）。
- **侧栏可折叠**：折叠后主区多出约 180px，给数据表更多列。

### L4 — 动效层：灵动的关键，也最容易做脏

- **统一节奏**：进入 200ms、退出 120ms、布局位移 320ms；**禁止 `transition: all`**。
- **高价值动效白名单**（少而准）：
  1. 数字滚动（KPI / 营养值）
  2. 侧栏指示条滑动 + 折叠展开
  3. 行 hover 抬升 + 批量选择态
  4. Tab / 分段控件滑块
  5. 模态从触发点缩放进入（scale + fade）
  6. 阶段跃迁撒花（低频高光）
  7. 聊天流式光标 + 折叠块高度展开
- **降级**：全部尊重 `prefers-reduced-motion`，统一降为淡入。
- **衔接**：骨架屏 shimmer 与内容交叉淡入，避免「跳一下」。

---

## 4. 分阶段实施路线（不动功能语义）

### P0 — 收益最大、风险最低
令牌统一 + 清理硬编码色 + 建立动效 token；侧栏（分组 / 指示条 / 折叠）；数据表去斑马纹；空状态组件化；Toast + 撤销。

### P1 — 体验质变
KPI 升级 + 数字滚动；ECharts 主题化 + `markLine` + 下钻；聊天区流式 / 工具折叠 / 消息操作 / 追问 chips；成长森林生长感 + 跃迁庆祝。

### P2 — 打磨与差异化
密度随宽度策略；⌘K 命令面板；暗色模式；键盘导航与 a11y 全量对齐。

---

## 5. 边界与风险

1. **范围**：只动 `workbench-assets/` 与 TeachMate 模块；WorkBench shell（含 Miro overlay）保持原样。
2. **CSS 体量**：`teachmate.css` 4.2k 行 + `workbench-app.css` 2k 行，**升级前建议先做一次去重 / 分层**，否则新增令牌会被现有选择器特异性压住，出现「改了没生效」。
3. **测试**：前端约 150 项 jsdom 测试。改类名或 DOM 结构会影响 `tests/helpers/workbench_html.js` 白名单与选择器断言，需同步更新。
4. **动效预算**：约定「同一时刻最多 N 个元素在动」，否则会从「高级」滑向「廉价炫技」。
5. **不做的事**：不引入前端框架、不重写模块、不改动后端契约与计分规则。

---

## 附：一句话抄法速查

| 你的模块 | 直接抄谁 | 抄哪一点 |
|---|---|---|
| 侧栏 / 顶栏 | art-design-pro、shadcn-admin | 分组折叠 + 指示条 + ⌘K |
| 仪表盘 KPI / 图表 | tremor、number-flow、echarts 官方案例 | 数字滚动 + sparkline + markLine |
| 数据表 | art-design-pro | 去斑马纹 + 列固定 + 批量操作条 |
| TeachMate 对话 | assistant-ui、vercel chatbot、agent-chat-ui | 工具调用折叠 + 消息操作 + Suggested Actions |
| 成长森林 | kana-dojo、react-duolingo | XP 条 + 路径感 + 跃迁庆祝 |
| 全局反馈 | actual budget、note-gen | 即时感 + 撤销 Toast + 克制动效 |
| 空状态 / 加载 | shadcn-admin、tremor | 插画 + CTA + 骨架屏 |
