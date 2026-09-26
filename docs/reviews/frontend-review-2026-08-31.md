# 前端性能与工程结构修改方案 · 英语教学工作台

审查对象：`workbench.html`、`workbench-assets/` 及相关测试/打包流程  
修订日期：2026-08-31  
技术形态：原生 JavaScript + CSS、经典脚本、离线桌面应用

> 本文覆盖加载性能、代码组织、视觉体验、样式工程和前端安全基建。视觉问题与改造前截图以 `deliverables/frontend-audit/frontend-audit-report.md` 为事实源，本文将其纳入统一执行顺序和验收标准。

---

## 一、结论与边界

当前前端没有明显的运行时灾难，但存在五类值得治理的问题：

1. 三个第三方重型库在首屏同步加载，而绝大多数启动场景不会立即使用。
2. WorkBench 点击动作集中在一个大型分发器中，维护冲突和回归风险较高。
3. 经典脚本通过共享全局作用域形成大量隐式依赖，难以局部测试和演进。
4. 字体、CSS 优先级、缓存版本和发布资源仍依赖人工维护。
5. WorkBench 与 TeachMate 存在颜色互串、组件规则不统一、空状态薄弱和移动端布局失控等视觉问题。

原审计测得的资源体积和全局符号数量可作为线索，但 DOM、内存、长任务和启动耗时必须先通过正式运行入口重新测量，才能作为优化前后的验收基线。

本轮不做以下高风险操作：

- 不改变现有 click 事件的冒泡阶段。
- 不把 `workbench-interactions.js` 中的 `blur` 捕获监听改为冒泡。
- 不给大文件逐个机械套 IIFE。
- 不直接运行 PurgeCSS 自动删除所有“零引用”类。
- 不一次性清理全部 `!important`。
- 不在保留内联事件/样式的情况下直接启用严格 CSP。

---

## 二、已核实的现状

### 2.1 重型资源

`workbench.html:12-14` 在 `<head>` 中同步加载：

| 资源 | 当前体积（约） | 主要用途 | 首屏通常需要 |
|---|---:|---|---|
| `echarts.min.js` | 1.1 MB | 历次考试、学生详情、TeachMate 用量图表 | 否 |
| `xlsx.full.min.js` | 930 KB | Excel 导入与导出 | 否 |
| `jszip.min.js` | 96 KB | DOCX/ZIP 生成路径 | 否 |

三者适合按业务路径延迟加载，但“原始传输字节减少”不等于“用户可感知性能等比例提升”。优化效果必须用正式入口的 FCP、交互就绪时间和功能首次打开延迟验证。

### 2.2 事件分发

`workbench-interactions.js:176-557` 的 click 委托使用默认冒泡阶段：

```js
document.addEventListener('click', event => {
  // data-act 分发
});
```

`workbench-interactions.js:770-822` 的 `blur` 监听使用捕获阶段：

```js
workarea.addEventListener('blur', handleEditableBlur, true);
```

这是因为 `blur` 不冒泡。这里的 `true` 必须保留，否则后代 `contenteditable` 的失焦保存逻辑可能失效。

真正需要治理的是 click 分发器的体积和业务耦合，而不是事件阶段。TeachMate 已经通过 `act.startsWith('tm-')` 转交给 `handleTeachMateAction()`，WorkBench 可以沿用同一种“按业务域转交”的方式。

### 2.3 全局作用域

`workbench-core.js`、`workbench-views.js`、`workbench-interactions.js`、`teachmate-views.js` 和 `teachmate-interactions.js` 之间存在大量跨文件直接引用。经典脚本中的函数/`var` 可能成为 `window` 属性，顶层 `let`/`const` 虽不挂到 `window`，仍通过全局词法环境被后续脚本访问。

因此，全局收敛方向正确，但不是“每个文件包一层 IIFE”即可完成。机械包裹会切断跨文件依赖，并影响直接访问 `window.tmSendMessage` 等接口的现有测试。迁移前必须先建立依赖清单和稳定公共 API。

### 2.4 字体、CSS 与安全基建

- Material Symbols 当前使用约 1.2 MB 的 TTF，`font-display: block`。
- 三个主要 CSS 文件合计约有 655 处 `!important`。
- 存在疑似历史遗留类，但“源码字符串零引用”不能证明运行时一定无用。
- HTML/模板中仍有内联 `onclick` 和大量内联 `style`，会阻碍严格 CSP。

---

## 三、阶段 0：重建可信性能基线（必须先做）

### 3.1 修正审计入口

当前 `tools/audit-perf.cjs` 使用 `/workbench.html`。正式桌面入口由启动器打开 `/workbench?token=...`，审计也应通过相同入口进入，并确保：

- 后端 API 正常可用；
- 浏览器会话持有有效 token；
- 不落入“数据库连接失败”降级页面；
- Console 和 `pageerror` 为零；
- 页面加载了真实或可重复的种子数据。

### 3.2 测量矩阵

| 维度 | 场景 |
|---|---|
| 缓存 | 冷缓存、热缓存 |
| 数据 | 空数据、常规数据、较大数据集 |
| 页面 | WorkBench 仪表盘、成绩历次对比、TeachMate 欢迎页 |
| 平台 | Chromium 开发环境、macOS 打包应用、Windows 打包应用 |

每个场景至少运行 5 次，记录中位数和最慢一次，避免用单次 `window.load` 下结论。

### 3.3 基线指标与验收

- FCP、DOM Interactive、应用数据加载完成时间；
- 首屏脚本/样式/字体字节数；
- 主线程长任务数量和最长时长；
- 首次打开图表、首次导入 Excel、首次导出的等待时间；
- Console 错误、页面错误和未处理 Promise 拒绝；
- 冷启动与热启动差异。

审计脚本必须自动确认当前不是错误降级页，并在结果中记录入口 URL、版本、数据规模和缓存状态。保存一份优化前基线，后续阶段统一与其比较。

---

## 四、阶段 1：第三方库按需加载（优先实施）

### 4.1 统一加载器

新增一个无构建依赖的脚本加载器，并对同一资源复用 Promise：

```js
const resourcePromises = new Map();

function loadScriptOnce(src, ready) {
  if (ready()) return Promise.resolve();
  if (resourcePromises.has(src)) return resourcePromises.get(src);

  const promise = new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = src;
    script.onload = () => ready()
      ? resolve()
      : reject(new Error(`资源未正确初始化：${src}`));
    script.onerror = () => reject(new Error(`资源加载失败：${src}`));
    document.head.appendChild(script);
  }).catch(error => {
    resourcePromises.delete(src);
    throw error;
  });

  resourcePromises.set(src, promise);
  return promise;
}
```

本项目的第三方文件是经典 UMD 脚本，本文统一采用动态插入 `<script>`，不混用动态 `import()` 表述。

### 4.2 实施顺序

1. **XLSX**：在文件选择后、解析前加载；导出按钮点击后、生成工作簿前加载。
2. **JSZip**：在进入需要 DOCX/ZIP 的导出动作后加载。
3. **ECharts**：最后迁移；进入历次对比、打开学生详情或用量图表时加载，完成后再执行绘图函数。

XLSX 和 JSZip 的边界相对清晰，适合先验证加载器。ECharts 涉及 DOM 已渲染、弹窗关闭、快速切换页面等生命周期，需要额外检查“资源返回时目标节点是否仍存在”。

### 4.3 交互、验收与回滚

- 首次加载期间显示忙碌状态，并阻止重复提交；
- 加载失败显示可重试提示，不误报成需要重启；
- 同时触发多个入口时只发起一次资源请求；
- 页面已经离开时，不向失效 DOM 初始化图表；
- 所有资源继续从本地 `workbench-assets/` 加载，保持离线；
- Excel 导入/导出、DOCX/ZIP、三类图表通过回归测试和人工冒烟；
- 首屏指标相对阶段 0 基线有可重复改善。

若任一平台出现资源路径、打包遗漏或不可恢复失败，只恢复该单个库的同步加载，不回滚其他已稳定的库。

---

## 五、阶段 2：按业务域拆分事件分发器

### 5.1 保持事件语义不变

- click 继续使用默认冒泡；
- `blur` 捕获监听保持 `true`；
- 保留现有 `stopPropagation()` 行为；
- 不改变按钮、输入框、日期控件和下拉菜单的交互时序。

### 5.2 拆分方式

不建议一次性建立包含上百项的扁平 `ACTIONS` 对象。优先按业务域逐段转交：

```js
function handleWorkbenchClick(action, target) {
  if (action.startsWith('term-')) return handleTermAction(action, target);
  if (action.startsWith('stu-')) return handleStudentAction(action, target);
  if (action.startsWith('score-')) return handleScoreAction(action, target);
  if (action.startsWith('dict-')) return handleDictationAction(action, target);
  if (action.startsWith('recite-')) return handleReciteAction(action, target);
  if (action.startsWith('writing-')) return handleWritingAction(action, target);
  if (action.startsWith('hw-')) return handleHomeworkAction(action, target);
  if (action.startsWith('todo-')) return handleTodoAction(action, target);
  return handleSharedAction(action, target);
}
```

每次只迁移一个业务域，迁移前后保持相同的 `data-act`、参数读取、异步返回和渲染顺序。

### 5.3 配套测试与验收

- 点击按钮内图标时，`closest('[data-act]')` 仍能定位动作；
- checkbox 内层点击不会意外触发父卡片动作；
- 成绩、默写、写作的失焦保存正常；
- 中文输入法组合输入不会因重绘残留拼音；
- 弹窗、菜单、Escape 和焦点恢复行为不变；
- 异步动作不会重复提交；
- `initEvents()` 最终只负责绑定与顶层转发。

不以减少代码行数为验收目标，以依赖边界和回归稳定性为准。

---

## 六、阶段 3：建立模块边界与公共 API

### 6.1 先做依赖清单

按文件列出：私有符号、跨文件公共函数、共享可变状态、测试使用的 `window.*` 接口，以及依赖加载顺序的初始化代码。

### 6.2 过渡目标

在继续使用经典脚本时，可先建立单一命名空间：

```js
window.Workbench = {
  core: {},
  views: {},
  actions: {},
  resources: {},
  testing: {}
};
```

公共 API 显式导出，私有函数留在模块闭包内。`escapeHtml`、`escapeAttr` 等高频基础函数先进入稳定的共享工具对象，再迁移调用点。

### 6.3 迁移原则与验收

- 先迁移新增代码和低依赖模块，再处理 core/views/interactions；
- 每次只收敛一组符号，不逐文件机械套 IIFE；
- 共享状态优先改为读写方法，避免继续暴露可变对象；
- 测试只依赖明确的 testing/public API；
- 新代码不再新增未声明的 window 全局；
- 公共接口有清单和最小契约测试；
- 是否最终切换 ESM，与阶段 5 的构建决策一起确定。

---

## 七、阶段 4：视觉体系、字体和 CSS 渐进治理

视觉整改以 `deliverables/frontend-audit/frontend-audit-report.md` 的截图和问题清单为事实源。本阶段不做单纯“换颜色”，而是先冻结规范，再按组件和页面逐批落地。

### 7.1 统一设计 token 与皮肤边界

当前 `workbench-app.css`、`teachmate.css`、`workbench-enhancements.css` 存在多套颜色、圆角、阴影、字号和断点。先建立一份设计 token 表，至少包括：

- 页面背景、卡片表面、边框、主文字、次级文字；
- Primary、Secondary、Success、Warning、Danger 等语义色；
- `sm/md/lg` 三档圆角和阴影；
- 正文、小字、区块标题、页面标题字号；
- 组件高度、间距和 z-index 层级；
- 560、860、1200、1440 四档响应式断点。

公共 token 供 WorkBench 和 TeachMate 共用，TeachMate 私有品牌 token 使用 `--tm-*`。禁止 TeachMate 的黄色便利贴、近黑主色等私有样式无意覆盖 WorkBench；也不要求两个栏目完全同色，而是保证共享组件规则一致、品牌皮肤边界明确。

### 7.2 统一公共组件

按以下顺序建立规范并替换现有散落实现：

1. 按钮：Primary、Secondary、Tertiary、Danger，统一高度、圆角和 disabled/loading 状态；
2. 输入框、下拉框、搜索框：统一高度、边框、focus-visible 和错误状态；
3. 卡片、表格、弹窗：统一圆角、阴影、标题层级、内边距和操作区；
4. tab、pill、badge：明确各自用途，避免同一页面混用多套切换语言；
5. 空状态：统一图标、主文案、补充说明和主操作，不再只留大块空白。

公共组件每次只替换一个类型，并检查 WorkBench 与 TeachMate 两边，避免通过高优先级选择器继续互相覆盖。

### 7.3 页面级视觉问题

| 页面/区域 | 本轮重点 |
|---|---|
| 顶部工具栏 | 统一按钮、搜索框、学期/班级选择器的高度与权重；控制窄屏换行 |
| WorkBench 导航与仪表盘 | 清除 TeachMate 颜色互串；统一 active 状态和 KPI 语义色；补强图表空状态 |
| 学生管理 | 分离工具栏与表头层级；补齐行操作 hover/focus；统一分页器 |
| 成绩管理 | 收敛按钮类型；统一两组 tab 的交互语言；强化排名与分层信息层级 |
| 原卷与错题 | 减少嵌套灰框；统一表头；为空资料库提供上传入口 |
| 日常作业 | 合并重复空状态；把“查看全部记录”纳入明确的页头或列表操作区 |
| 待办日历 | 收敛月份标题、当前日期、跨月日期和详情卡片的视觉权重 |
| 设置页与导出弹窗 | 提高表单标签可读性；明确区块主次按钮；改善弹窗空间利用 |
| TeachMate | 调整欢迎页标题、副标题、头像、历史空状态和输入区 enabled/disabled 对比 |

### 7.4 移动端与响应式

- 390px 宽度下重新组织栏目切换、标题和菜单，避免 header 多行失控；
- TeachMate 欢迎标题使用有上下限的响应式字号，不能被顶部区域裁切；
- WorkBench KPI 在移动端改为紧凑网格或可扫读布局，避免占满整个首屏；
- TeachMate 会话栏、成果栏和菜单统一采用抽屉逻辑，并保证遮罩、Escape、返回焦点可用；
- 所有核心页面在 390、560、860、1200、1440px 检查横向溢出、文本裁切和点击目标尺寸。

### 7.5 图标字体

优先顺序：

1. 将 TTF 转为 WOFF2；
2. 工具链稳定后，按实际 glyph 做子集化；
3. 更新 CSS、第三方说明和 `checksums.sha256`；
4. 再比较 `block`、`swap` 等策略的真实视觉效果。

不能只因 FOIT 就直接改 `swap`。Material Symbols 依赖文本连字，字体未就绪时可能闪现 `menu`、`close` 等英文单词。最终策略以无明显空白、无连字文本闪烁、无布局抖动为准。

### 7.6 `!important` 与疑似死 CSS

- 新增样式默认禁止使用 `!important`，确需使用时写明原因；
- 只在重构触及的组件内清理；
- 优先通过统一 token、组件作用域和加载顺序消除冲突；
- 源码零引用类只进入“候选删除清单”；
- 扫描模板字符串、运行时 class 拼接和测试夹具；
- 为动态前缀建立 safelist；
- 小批删除，并执行桌面/移动端截图对比。

### 7.7 视觉验收

- 使用 `deliverables/frontend-audit/` 中现有截图作为改造前基线；
- 为 WorkBench 仪表盘、学生、成绩、错题、作业、待办、设置、导出弹窗和 TeachMate 欢迎/运行态生成新截图；
- 桌面端至少覆盖 1440×900 和 1366×768，移动端至少覆盖 390px 宽度；
- 自动检查横向溢出、标题裁切、控件重叠和不可见焦点；
- 人工确认颜色语义、信息层级、空状态和跨栏目皮肤边界；
- 视觉整改不得改变数据、权限、事件时序和业务行为。

CSS 治理应服务于上述视觉规范，避免先清理旧样式、随后因视觉重构再次改写同一区域。

---

## 八、阶段 5：构建、版本和发布自动化

### 8.1 最小目标

- 自有 JS/CSS 压缩；
- source map 生成与发布策略；
- HTML 资源版本号或内容哈希注入；
- 字体转换/子集化；
- 第三方资源校验更新；
- 构建产物完整性检查。

### 8.2 桌面打包约束

构建结果必须同时适配 `build_macos_app.sh`、`build_windows.ps1`、`build_windows.bat`、`EnglishWorkBench.spec`、`tools/release_check.py`、`THIRD_PARTY_NOTICES.md` 和 `checksums.sha256`。

不得破坏本地资源相对路径、离线运行、PyInstaller 收集路径和发布清单生成。

### 8.3 是否采用 ESM

- 若维持经典脚本，使用显式命名空间和依赖顺序检查；
- 若采用 ESM，构建器输出兼容当前桌面 Chromium 的稳定入口，并同步改造测试；
- 不在同一阶段同时进行大规模业务重构和模块格式迁移。

---

## 九、阶段 6：错误日志与 CSP

### 9.1 本地错误日志

接入 `error` 和 `unhandledrejection` 前，先定义日志位置、容量和轮转；对 token、API Key、学生姓名、电话和附件内容做脱敏；明确诊断信息导出方式及页面提示边界。

### 9.2 CSP 分阶段实施

1. 清除内联 `onclick`，统一迁移到事件委托；
2. 将可静态表达的内联 `style` 迁到 CSS 类；
3. 盘点第三方库对 `eval`、blob URL、worker 和 data URL 的需求；
4. 先启用 `Content-Security-Policy-Report-Only`；
5. 修完报告后再启用强制策略。

不能以“调用了很多次 `escapeHtml`”代替完整 XSS 审计。需要按每个 HTML sink 的数据来源、文本/属性/URL 上下文分别检查。

---

## 十、推荐执行顺序

| 顺序 | 工作 | 原因 |
|---:|---|---|
| 0 | 重建正式入口性能基线 | 先确保后续收益可验证 |
| 1 | XLSX、JSZip 懒加载 | 边界清晰、可独立回滚 |
| 2 | ECharts 懒加载 | 收益较高，但需处理异步生命周期 |
| 3 | 按业务域拆事件分发器 | 降低改动冲突，不改变事件语义 |
| 4 | 建依赖清单和公共 API | 为全局收敛与测试隔离铺路 |
| 5 | 冻结视觉 token，整改公共组件与关键页面 | 解决颜色互串、组件割裂和移动端问题 |
| 6 | 字体 WOFF2、CSS 渐进治理 | 在视觉规范确定后清理技术债 |
| 7 | 构建与缓存版本自动化 | 稳定离线发布和跨平台打包 |
| 8 | 本地错误日志、CSP | 在内联代码和隐式依赖减少后实施 |

---

## 十一、总体验收标准

- 正式入口冷启动指标相对基线有可重复改善；
- 首屏不加载 XLSX、JSZip、ECharts，首次使用时可见、可重试且不重复请求；
- 中文输入、失焦保存、菜单、弹窗和现有业务行为保持一致；
- WorkBench 动作按业务域隔离，新功能不再扩张单一 if/else 链；
- 新代码不新增隐式全局变量，公共 API 有清单和测试；
- WorkBench 与 TeachMate 的品牌皮肤边界清楚，不再发生颜色和组件样式互串；
- 按钮、输入框、卡片、表格、弹窗、tab 和空状态遵循统一组件规范；
- 390px 移动端无 header 失控、标题裁切、横向溢出或核心操作不可见；
- 主要页面完成桌面/移动端截图回归，视觉变化均可追溯到审计问题；
- 图标字体体积显著下降，且没有英文连字闪烁或布局抖动；
- CSS 删除均有运行时证据和截图回归，不发生动态类误删；
- macOS、Windows 打包、离线运行、资源校验和 release check 全部通过；
- 错误日志不泄露教师、学生、凭据或附件敏感信息；
- CSP 从 report-only 平稳过渡，不以关闭安全策略换取兼容。

## 十二、需要保留的现有优点

- 原生、离线、无 CDN 的运行方式；
- 以事件委托为主的交互模型；
- 现有 `escapeHtml` / `escapeAttr` 的上下文转义习惯；
- TeachMate 已建立的 `teachMateApi`、`teachMateState` 等显式边界；
- 当前较克制的首屏 DOM 结构；
- 数据库失败时仍能给出明确提示的降级能力。
