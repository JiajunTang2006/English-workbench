# 2026-08-29 前端视觉审计

- 对 WorkBench + TeachMate 前端做了完整视觉审计，输出报告与截图证据到 `deliverables/frontend-audit/`。
- 核心结论：三份 CSS 存在三套色板/变量/断点体系；WorkBench 多处被 TeachMate 品牌色染色；组件级 button/input/card/table/modal/empty 样式离散；移动端 header 与响应式字号待收敛。
- 已给出 P0/P1/P2 可执行优化清单与推荐推进顺序。

## 第一轮色彩归一（Miro 基准，已完成）

- 方向确认：整体保持 Miro 风格不变，其余微调。TeachMate 定版配方（#f7f8fa/#1c1c1e/#555a6a/#e0e2e8/#ffd02f）为基准，WorkBench 往它靠拢。
- `workbench-app.css`：Miro 主题层表面色暖米系→TeachMate 中性灰系（#F5F4F1→#f7f8fa、#E5E3DE→#e0e2e8、#6B6786→#555a6a 等）；全文件 #050038→#1c1c1e；--md-on-surface-variant 从 #4262FF 改 #555a6a（表头/导航文字不再蓝紫）；24 处 rgba(5,0,56,*) 阴影→rgba(28,28,30,*)。
- `teachmate.css`：22 处蓝调阴影（rgba(17,23,53)/(25,34,77)/(29,40,89)/(11,18,51)）→rgba(28,28,30,*)，遵守阴影统一近黑约定。
- `workbench-enhancements.css`：9 种蓝灰 fallback 对齐 TeachMate 值。
- 版本号 bump 至 202608291430；audit-responsive.cjs 33/33 通过；备份在 /tmp/bak-*.css。
- 主色 #4262FF 保留（Miro 官方蓝，TeachMate 也用作辅助强调）；遗留：重复变量声明未清理、type scale/圆角/断点收敛未做，属第二轮。

## 第二轮结构收敛（已完成）

- 变量清理：`workbench-app.css` 删除第 1 段死 Tailwind 蓝 :root（11 变量全被覆盖）；594 层 MD3 块重写仅保留未覆盖声明；657 层删 13 行被 Miro 层覆盖的行（剩 --md-muted 并对齐为 #8e91a0）。:root 块从 5 个减到 4 个，每个 token 全文件仅一处定义。
- 字号归并：15 个中间档（9/11.5/13.5/14.5/15/17/19/21/22/23/25/26/30/34/38px）就近归并到 10/12/13/14/16/18/20/24/28/32/36px scale，共 114 处。
- 圆角归并：3/5→4、7/9→8、11/13/14→12、15/17/18→16，共 92 处（复合值/药丸/百分比不动）。
- 断点归并：700/720→680、620→640、1439/1441→1440，共 13 处；大断点（900/1000/1100/1200/1300）未动，留待专项。
- 遗留 rgba(60,64,67) M3 灰阴影→rgba(28,28,30)。
- 验证：花括号配对 0 差异；audit-responsive 33/33 通过；截图确认无视觉回归。
- 教训：A2 按文本删行时 657 层与 594 层的 --space 行文本相同，replace 命中第一个出现导致 assert 失败——同文本多处出现时必须先重写前者或用位置锚定。
- 第三轮遗留：按钮语义体系、空状态重做（需动 teachmate/workbench views JS）、移动端 header 重构、TeachMate 欢迎页移动端标题截断修复。

## TeachMate「分析中」视觉优化（已完成）

- 改动：`teachmate-views.js`（_renderRunningIndicator 重写：新增 _renderSkeletonLines/_renderStatusPill；thinking 标题加 psychology 品牌黄图标）、`teachmate-state.js`（修复 bug：1s 计时器/MODEL_THINKING 曾把"正在读取数据/正在校验"等阶段覆盖为固定"生成回复中"；现阶段在 #tmRunningStage、计时在 #tmRunningElapsed 分离更新）、`teachmate.css`（状态胶囊化+tabular-nums、running-dot 品牌黄光晕、骨架 shimmer 行、思考流打字光标、spark 呼吸动画、prefers-reduced-motion 降级）。
- 版本 bump 202608291540；JS 语法检查通过；audit-responsive 33/33 通过；截图证据 deliverables/frontend-audit/*tm-running.png（桌面+移动，注入模拟状态验证，不依赖 API Key）。
- 教训：spark 图标选择器需 .tm-thinking-title 前缀提高特异性，否则被 .tm-thinking-toggle .material-symbols-rounded 通用规则覆盖。

## 修复：TeachMate 页内标题条桌面重复（用户截图反馈）

- 现象：桌面顶栏「冯老师 · 初中英语教师助手」下方又出现一行同名标题（.tm-mobile-toolbar）。
- 根因：工具条显示断点写成 max-width:1440px，桌面（≤1440）也显示，与顶栏重复；另有一段完全重复的 @media (max-width:1440px) 块。
- 修复：断点收到 680px（仅移动端显示，含 display:flex 完整规则移入 680 块）；合并重复媒体查询；1440 块内显式 display:none。版本 202608291550。
- 验证：1440/1280 visible=false，390 visible=true。
