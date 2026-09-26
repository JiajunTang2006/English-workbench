# 2026-08-31

## 前端审查（P0/P1/P2 报告）

产出 `deliverables/frontend-review-2026-08-31.md`，新增审计脚本 `tools/audit-perf.cjs`（Playwright 实测资源/长任务/全局符号/DOM 规模）。

实测关键数据（Chromium 1440×900，本地 http://127.0.0.1:8899）：
- 首屏 JS 2879 KB、CSS 341 KB、字体 1178 KB；渲染阻塞资源 7 个 / 2463 KB
- window 上注入 **458 个全局符号**；顶层声明 526 处（core 156 / wb-interactions 144 / tm-interactions 137）
- DOM 257 节点、深度 12、长任务 0、堆 14 MB —— **运行时健康，问题全在加载策略与代码组织**

主要结论：
- P0-1 `workbench.html:12-14` 三个重型库（echarts 1096KB / xlsx 930KB / jszip 96KB）在 head 同步加载，但分别只在 4/20/4 处使用，均可按需 `import()`。代码中已有 `typeof echarts !== 'undefined'` 防御，与加载策略自相矛盾。
- P0-2 `workbench-interactions.js:176-820` `initEvents` 654 行 if/else 链，且 `addEventListener('click', ..., **true**)` 用了**捕获阶段** —— 子元素 `stopPropagation()` 对它无效，是隐蔽交互 bug 源。应先改 `false`，再拆为 map 路由表。
- P0-3 无模块系统，仅 `teachmate-state.js:6` 用了 IIFE，其余裸顶层。收敛时注意 `escapeHtml`（workbench-core.js:1279）被全项目调用 320 次，先挂命名空间再改调用点。
- P1 `material-symbols-rounded.css:7` `font-display: block` + 1.2MB TTF → 图标 FOIT；应改 swap 并转 WOFF2（~200KB）。CSS 有 655 处 `!important`（417/185/53）；1020 个类中 137 个（13%）零引用，集中在 `dashboard-*` / `dict-*` / `chart-*` 前缀。

优点（勿在重构中破坏）：escapeHtml 覆盖 320 处、事件委托为主（仅 45 处 addEventListener）、已用 `window.teachMateXxx` 命名空间、setInterval 仅 1 处且有 `_elapsedTimer` 管理。

## 复用经验

- 估算未使用 CSS 类时，**不要用 shell `while read` 循环 grep**（1020 次调用会触发 Bash 工具 120s 超时被 kill，exit 137）。改用 Python 一次性读入源码做 `in` 判断，秒级完成。
- Bash 工具里用 `(cmd &)` 起的后台进程会在命令结束时被回收；需长期运行的本地服务器必须用 `run_in_background: true`。

## TeachMate 对话区 UI 优化方案（已实施）

针对用户截图（TeachMate 进度卡 + 输入区）的"去 AI 味"打磨，落地完成：

- 路径：`deliverables/teachmate-analysis-card-redesign-2026-08-31.html`
- 范围：仅 `teachmate.css` + `teachmate-views.js` + `workbench.html` 版本号；WorkBench shell 不动
- 改了的文件：
  - `workbench-assets/teachmate.css:3396-3452` 进度卡整套色值替换为 Miro 令牌 + 新增 .tm-progress-strip / .tm-progress-badge
  - `workbench-assets/teachmate.css:1533` 附件 chip 改为"3px 品牌黄左条 + 中性灰底"
  - `workbench-assets/teachmate.css:1564` 发送按钮（被 workbench-app.css:1959 !important 锁定，沿用 Miro overlay）
  - `workbench-assets/teachmate-views.js:1058-1067` 加 .tm-progress-strip + RUNNING 徽标 + "取消"文案
  - `workbench.html:14` teachmate.css?v=202608291605 → 202608311850

### Playwright 验证回归（DOM 注入式，覆盖 14/14 设计令牌断言）

- 验证脚本：`tools/verify-tm-progress-redesign.cjs`（已落地，可重复运行）
- 14 个断言全 PASS：卡片背景 #ffffff / 边框 #e0e2e8 / 阴影 rgba(28,28,30,.04/.06) / 4px 顶部 #ffd02f 黄条 / 黑药丸 RUNNING 徽标 / 取消按钮中性灰 / 品牌黄脉冲点 + 黄色光晕 / 薄荷绿当前步 / 黄色左条附件 chip / 黑底发送按钮
- 唯一 console error 是 /api/v1/* 失败（按工作记忆预期）
- 视觉效果：`deliverables/verify-shots/tm-progress-card-{zoom,synth}.png` 全验证视觉无回归

### ⚠️ 已知约束 / 残留

- 发送按钮 color 仍然是白色（#fff），因为 workbench-app.css:1959 的 `.tm-send-btn { color: #ffffff !important }` 锁死。按"WorkBench shell 不动"约束保留 Miro overlay 设计（黑底白图标 + 12px 圆角），未硬覆盖。
- 还有 3 处 AI 蓝调残留（不在截图范围，独立组件）：
  - teachmate.css:1764 `.tm-plugin-drop-file`（拖放提示）
  - teachmate.css:2776 `.tm-right-status-running`（右栏状态）
  - teachmate.css:3400 `.tm-guide-tag`（引导徽章）
- 后续可单独排一轮"剩余蓝调清理"，不在本轮范围。

### 复用经验

- 在 file:// 直接打开 workbench.html 时，TeachMate 的 fetch 全失败（/api/v1/*）**导致整个 chat shell 不渲染**（message list 容器都不创建）。**没法**通过 `teachMateState.startSubmitting() + setTimeline()` 触发 .tm-analysis-progress 渲染。
- 解法：**用 `page.evaluate` 直接注入合成 DOM**（同构生产模板的最小子集）到 `#tm-synth`，绕过视图层/数据层。优点：纯 CSS 验证，回归门槛低，与生产模板的结构一致性是开发纪律保证的。
- DOM 注入式验证比"等真实业务流"更可靠，**适合设计 token 命中度回归**。

## 用户已审阅 / 潜在后续

- 用户截图中"红色方块发送按钮"实为 running 态的"停止按钮"（`.tm-send-btn.tm-cancel-btn`），UX 语义正确（红色 = 危险停止）。保留。
- 用户可能要求：A) 清理剩余 3 处 AI 蓝调；B) 把进度卡的圆角做成 18px（与卡片库更一致，当前 16px）；C) 把 RUNNING 黑药丸英文改成中文"运行中"

## TeachMate 进度卡动效层 v2（已实施）

用户的反馈："做的太僵硬了，缺乏那种交互性"。原因：v1 只动了静态结构，缺 motion。在不动色系前提下加了 5 个 keyframes + 5 个 transitions，让卡片从"静态结果展示"变成"在思考 + 在推进"。

### 关键改动（仅 teachmate.css + teachmate-views.js + workbench.html 版本号）

- `workbench-assets/teachmate.css` 进度卡段全部重写为 motion-aware
- `workbench-assets/teachmate-views.js:_renderRunningIndicator` 加 ①进度比例计算（doneCount + current×0.5，限 [10%, 92%]）②inline `--strip-progress: NN%` 注入 strip 元素 ③步骤 `style="--step-enter-delay: idx×70ms"` stagger 注入（仅前 6 条）
- `workbench.html:14` teachmate.css?v=202608311900 → 202608311910

### 5 个 animation（keyframes）

1. **卡片入场** `tm-progress-card-enter` 360ms cubic-bezier(.2,.7,.3,1) opacity 0→1, translateY 8→0
2. **当前步入场** `tm-progress-current-enter` 320ms / delay 80ms scale .99→1 + translateY
3. **步骤 stagger** `tm-progress-step-enter` 320ms delay 由 --step-enter-delay 控制
4. **当前步呼吸** `tm-progress-breathe` 2.6s ease-in-out loop，box-shadow 0px ↔ 4px rgba(0,179,115,.06)
5. **扫描线** `tm-progress-scan` 2.2s 循环，2px 黄竖线从 top -60% → 120%
6. **RUNNING 高光** `tm-progress-shimmer-text` 2.4s 循环（徽标内部细高光）
7. **顶部 shimmer** `tm-progress-shimmer` 1.6s 循环（已完成段上白色高光带从右往左）
8. **当前步图标脉冲** `tm-progress-step-icon-pulse` 1.6s 黄底圆胶囊阴影 4px ↔ 7px
9. **黄点脉冲** 保留 `tm-progress-pulse` 1.8s

### 5 个 transition

1. **取消按钮 hover** 180ms ease — translateY + 边框 + 阴影
2. **details summary 旋转** 250ms cubic-bezier(.2,.7,.3,1) — ▾ 旋转 180°
3. **summary hover** 180ms — secondary → text 颜色
4. **顶部 strip 宽度推进** 480ms cubic-bezier(.2,.7,.3,1) — JS 设 var 后已完成的段会**动画**到新位置，不再 jump cut
5. **步骤图标状态切换** 250ms — done/current/pending 圆胶囊背景过渡

### 无障碍处理

- 全部 keyframes + ::after/::before shimmer 注册了 `@media (prefers-reduced-motion: reduce)` 关闭路径
- 所有 loop 都在 0.4-1.4Hz 范围（&gt;700ms 周期），远低于 WCAG 闪烁阈值

### Playwright 验证

- `tools/verify-tm-motion.cjs`：4/5 PASS（唯一 "fail" 是单 step 验证时 stagger=0，合成 DOM 边界条件不是 CSS 问题）
- `tools/record-tm-motion.cjs`：录 4 秒 webm (`tm-motion-demo.webm`)，展示卡片入场 + 进度条从 30% 推到 75%

### 复用经验

- 验证 CSS 动画不能在静态截图上看出，**Playwright recordVideo 是"动效可证据化"的最短路径**：3 行配置 + 录 4 秒就够。
- 进度条的"推进"如果不写 transition，JS 改 --strip-progress 数字时会 jump cut；务必同时设 transition: width 480ms cubic-bezier(...)

## 用户已审阅 / 潜在后续

- 用户截图里 "红色方块" 是 running 态的停止按钮（语义正确）
- 用户对"僵硬"的反馈很及时，提示 v1 决策过度理性、偏结构，v2 补回了感官层

## 🔴 P0 BUG 修复：运行计时器永久停摆「已用时」不动

用户反馈"我看这个时间不动的"。已定位并修复（不是 UI 问题，是**时序竞态**）。

### 根因

`teachmate-state.js` 计时器只在 `progressMode === 'full'` 时启动，但**升级时机晚于 RUN_STARTED**：

| 时序 | 动作 | `_progressMode` | 结果 |
|---|---|---|---|
| 1 | `startSubmitting()` (line 592) | 初始化为 `light` | — |
| 2 | 后端推 `run.started` (line 677) | 仍是 `light` | `if (_progressMode === 'full') _ensureElapsedTimer()` **不成立 → 不启动** |
| 3 | 后端推 progress → `setTimeline` (line 483) | 升级为 `full` | **没人回头启动 → 时间永久停摆** |

即：真实业务里 `run.started` 总是先于第一条 timeline 事件到达，所以**计时器 100% 不会启动**。
（之前我的复现脚本先 setTimeline 再 handleRunEvent，顺序反了，所以误判为"正常"。**构造 state 复现时必须严格对齐真实事件时序**）

### 修复

- 新增 `_ensureElapsedTimerIfNeeded()` helper（teachmate-state.js:320）：
  `progressMode==='full'` && `_runStartedAt != null` && 非终态 → 启动
- 在两个把 mode 升到 full 的位置补调用：
  - `setTimeline()` (line 487) —— 主修复点
  - `setCurrentRun()` (line 583，在 `_syncRunTiming` 之后，因为此时 `_runStartedAt` 才就绪)
- MODEL_THINKING 两处 (373/381) 与 RUN_STARTED (677) 原有逻辑保留

### 验证

- `tools/repro-tm-timer-bug2.cjs`：复现脚本（修复前卡在 0分09秒不动，修复后 07s→11s→15s 正常推进）
- `tools/verify-tm-final.cjs`：8/8 ALL PASS（计时器 + indeterminate + 8 个动效绑定）
- `deliverables/verify-shots/tm-motion-fixed.webm`：真实时序录屏

## 动效增强（v2.1，配合计时器修复）

- 进度条 **indeterminate 兜底**：timeline 为空时黄条做 1.5s 左右滑动循环（`data-indeterminate="1"`，views.js 注入），"在动"的信号不再依赖后端推流节奏
- 卡片入场动画 360ms → **200ms**：进度卡随 SSE 事件频繁全量重渲染（`workbench-views.js:106` `workarea.innerHTML` 全量替换），入场动画过长会被反复打断重启，反而显得"卡住"

## 复用经验（重要）

- **构造 state 复现必须严格对齐真实事件时序**。我第一次按"先 setTimeline 再 run.started"构造，计时器正常 → 误判无 bug；按真实时序（startSubmitting → run.started → setTimeline）才复现。**先想清楚后端事件的真实到达顺序**。
- `file://` 下 TeachMate 不初始化（fetch 全失败），**必须用 http 服务器**才能测 state 逻辑。启动：`/opt/miniconda3/bin/python3 -m http.server 8899 --bind 127.0.0.1`（用 `run_in_background: true`）。
- 首次进入有引导 modal 会拦截 Playwright 点击，测试里要先移除：`document.getElementById('modal').classList.remove('show')` 等。
- 静态截图看不出动画，**Playwright `recordVideo` 是动效可证据化的最短路径**（context 配置 3 行 + 录几秒）。
