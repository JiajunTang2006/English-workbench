// ================================================================
// U5-05 UI 自动化：TeachMate 任务工作台契约测试
//
// 覆盖 U5-02 组件拆分 + U5-03 UX 闭环的接线完整性：
//   1. 新组件文件（report/evidence/onboarding/a11y）已被页面引用；
//   2. 报告画布渲染结构化契约（摘要/发现/建议/局限/证据引用）；
//   3. 证据检查器展示计算公式、分子/分母、规则版本；
//   4. 教师确认面板保留 AI 原稿与教师修改两栏；
//   5. 首次设置卡反映 Provider 配置状态；
//   6. 数据就绪检查给出可执行修复入口；
//   7. 导出（打印 / JSON）与复制诊断接线；
//   8. a11y 播报与焦点管理存在。
// 全部为静态契约断言，不依赖真实 API Key。
// ================================================================

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..', 'workbench-assets');
const read = (name) => fs.readFileSync(path.join(root, name), 'utf8');

const html = fs.readFileSync(path.join(__dirname, '..', 'workbench.html'), 'utf8');
const report = read('teachmate-report.js');
const evidence = read('teachmate-evidence.js');
const onboarding = read('teachmate-onboarding.js');
const a11y = read('teachmate-a11y.js');
const state = read('teachmate-state.js');
const views = read('teachmate-views.js');
const interactions = read('teachmate-interactions.js');
const api = read('teachmate-api.js');
const css = read('teachmate.css');

// --- U5-02: 组件已被页面引用 ---
test('U5-02: new components are wired into the page', () => {
  for (const name of ['teachmate-report.js', 'teachmate-evidence.js', 'teachmate-onboarding.js', 'teachmate-a11y.js']) {
    assert.ok(html.includes(name), `workbench.html 缺少 ${name}`);
  }
  // 页面加载顺序：依赖组件先于消费组件
  const order = ['teachmate-report.js', 'teachmate-evidence.js', 'teachmate-onboarding.js', 'teachmate-a11y.js', 'teachmate-views.js', 'teachmate-interactions.js'];
  const idx = order.map((n) => html.indexOf(n));
  for (let i = 1; i < idx.length; i++) {
    assert.ok(idx[i] > idx[i - 1], `${order[i]} 应位于 ${order[i-1]} 之后`);
  }
});

// --- U5-03: 报告画布契约 ---
test('U5-03: report canvas renders the four contract sections', () => {
  for (const field of ['answer.summary', 'answer.findings', 'answer.recommendations', 'answer.limitations']) {
    assert.ok(report.includes(field), `报告画布缺少 ${field}`);
  }
  assert.match(report, /renderReportCanvas/);
  assert.match(report, /tm-report-canvas/);
  // 发现内可点击跳转证据
  assert.match(report, /tm-evidence-ref-btn/);
  assert.match(report, /tm-view-evidence-id/);
  // 建议按优先级排序
  assert.match(report, /a\.priority \|\| 99/);
});

// --- U5-03: 教师确认面板（AI 原稿 vs 教师修改，分别保留） ---
test('U5-03: confirm panel separates AI original and teacher edit', () => {
  assert.match(report, /renderConfirmPanel/);
  assert.match(report, /tmAiOriginalText/);
  assert.match(report, /tmTeacherText/);
  assert.match(report, /readonly/);
  // 状态徽章（草稿 / 已确认）
  assert.match(report, /tm-confirm-status-/);
  // 保存与确认两个动作
  assert.match(report, /tm-evaluation-save/);
  assert.match(report, /tm-evaluation-confirm/);
});

// --- U5-03: 证据检查器（来源 / 计算公式 / 分子分母 / 规则版本）---
test('U5-03: evidence inspector surfaces formula, ratio and rule version', () => {
  for (const field of ['calculation_formula', 'numerator', 'denominator', 'rule_id', 'rule_version', 'source_entity', 'source_page', 'source_question_no']) {
    assert.ok(evidence.includes(field), `证据检查器缺少 ${field}`);
  }
  assert.match(evidence, /renderEvidenceDetail/);
  assert.match(evidence, /tm-evidence-calc/);
  assert.match(evidence, /tm-evidence-rule/);
  assert.match(evidence, /openEvidenceById/);
});

// --- U5-03: 首次设置卡（模型 / API Key / 连接测试 / 预算上限）---
test('U5-03: setup card shows provider readiness and budget', () => {
  assert.match(onboarding, /renderSetupCard/);
  for (const probe of ['api_key_configured', 'agent_enabled', 'text_agent_enabled', 'budgetLimit', 'runConnectionTest', '连接测试']) {
    assert.ok(onboarding.includes(probe), `onboarding 缺少 ${probe}`);
  }
  assert.match(onboarding, /tm-setup-/);
  // 状态类通过拼接生成：'...tm-setup-' + statusCls（ok/warning/error）
  assert.match(onboarding, /tm-setup-'\s*\+\s*statusCls/);
});

// --- U5-03: 数据就绪检查（缺失考试 / 成绩时给出可执行修复入口）---
test('U5-03: data readiness offers actionable fix entry points', () => {
  assert.match(onboarding, /checkDataReadiness/);
  assert.match(onboarding, /listClasses/);
  assert.match(onboarding, /listExams/);
  assert.match(onboarding, /tm-import-classes/);
  assert.match(onboarding, /tm-open-exams/);
  assert.match(interactions, /tmJumpToImport/);
  assert.match(interactions, /tmJumpToExams/);
});

// --- U5-03: 导出（打印 / JSON）与诊断复制 ---
test('U5-03: export print/JSON and diagnostics copy are wired', () => {
  assert.match(report, /exportForPrint/);
  assert.match(report, /exportJSON/);
  assert.match(interactions, /tm-export-print/);
  assert.match(interactions, /tm-export-json/);
  assert.match(interactions, /tm-copy-diagnostics/);
  assert.match(interactions, /navigator\.clipboard/);
  // 打印视图包含证据清单与生成时间
  assert.match(report, /证据清单/);
});

// --- U5-03: 运行时间线投影 ---
test('U5-03: run timeline projects tool/model/validation phases', () => {
  assert.match(state, /_appendTimelineFromEvent/);
  assert.match(state, /TOOL_STARTED/);
  assert.match(state, /TOOL_COMPLETED/);
  assert.match(state, /VALIDATION_STARTED/);
  assert.match(state, /VALIDATION_FAILED/);
  assert.match(state, /timeline/);
  assert.match(views, /run-timeline/);
  assert.match(views, /tm-timeline/);
});

// U6：聊天内可解释进度卡（只展示公开工作说明，不展示原始思维链）
test('U6: chat progress card exposes current step and next action', () => {
  assert.match(state, /progressConnection/);
  assert.match(state, /不在教师端展示原始思维链/);
  assert.match(state, /tool_name/);
  assert.match(views, /tm-analysis-progress/);
  // 过程行（tm-proc-row）承载"当前步"：行身份 data-step-id 稳定，
  // running 行整条扫光；tm-proc-list 承载展开的过程步骤列表。
  assert.match(views, /tm-proc-row/);
  assert.match(views, /tm-proc-list/);
  assert.match(views, /下一步：/);
  assert.match(views, /tmRunningConnection/);
  assert.match(views, /data-act="tm-cancel"/);
  assert.match(css, /tm-progress-step/);
  assert.match(css, /tm-progress-connection\.is-slow/);
  assert.match(views, /progressMode.*light/);
});

// --- U5-03: 教师确认写入评价（后端 Evaluation 契约） ---
test('U5-03: evaluation confirm flows through API layer', () => {
  for (const fn of ['listEvaluations', 'createEvaluation', 'updateEvaluation', 'confirmEvaluation', 'getEvaluationAudit']) {
    assert.ok(api.includes(fn), `api 缺少 ${fn}`);
  }
  assert.match(interactions, /tmSaveEvaluation/);
  assert.match(interactions, /createEvaluation\(payload\)/);
  assert.match(interactions, /confirmEvaluation\(saved\.id\)/);
  assert.match(interactions, /aiOriginal/);
  assert.match(interactions, /teacherText/);
});

// --- U5-04: 无障碍 ---
test('U5-04: a11y announcer, focus management and reduced-motion exist', () => {
  assert.match(a11y, /announce/);
  assert.match(a11y, /rememberFocus/);
  assert.match(a11y, /restoreFocus/);
  assert.match(a11y, /trapFocus/);
  assert.match(a11y, /watchRunStatus/);
  assert.match(a11y, /prefers-reduced-motion/);
  assert.match(css, /prefers-reduced-motion:\s*reduce/);
  // 状态播报不逐 Token：announce 只在状态机转跳时调用
  assert.match(state, /setRunState/);
  const a11yWatch = a11y.match(/watchRunStatus/);
  assert.ok(a11yWatch, 'watchRunStatus 未导出');
  // 弹层焦点接管（openModal 后聚焦、closeModal 后归还）
  assert.match(a11y, /wrapModalFocus/);
  assert.match(a11y, /__tmA11y/);
  assert.match(a11y, /focusFirst\(modal\)/);
  assert.match(interactions, /wrapModalFocus/);
});

// --- U5-04: 键盘可达与焦点顺序 ---
test('U5-04: keyboard a11y hooks are present', () => {
  assert.match(interactions, /handleTeachMateKeydown/);
  assert.match(interactions, /key === 'Enter'/);
  assert.match(interactions, /key === 'Escape'/);
  assert.match(views, /aria-live/);
  assert.match(html, /aria-label/);
});

// --- U5-04: 风险与状态不只靠颜色（带文字/图标） ---
test('U5-04: error and warn states carry icon + text', () => {
  assert.match(views, /tm-error-bar/);
  assert.match(views, /material-symbols-rounded">error/);
  assert.match(css, /tm-timeline-item-error/);
});

// --- U5-03: 附件上传（U4 管线前台入口，完成门禁：全流程不进入终端） ---
test('U5-03: attachment upload is wired into the chat input', () => {
  assert.match(api, /createAttachment/);
  assert.match(api, /listAttachments/);
  assert.match(api, /parseAttachment/);
  assert.match(api, /body\.attachment_ids = attachmentIds/);  // API 层拼装附件字段
  assert.match(interactions, /tmAttachFile/);
  assert.match(interactions, /readAsDataURL/);
  assert.match(interactions, /addPendingAttachment/);
  assert.match(interactions, /attachmentIds = teachMateState\.getPendingAttachments/);
  assert.match(interactions, /sendMessage\(sessionId, text, quickTask, attachmentIds, modelId\)/);
  assert.match(views, /tm-attach-chip/);
  assert.match(views, /data-act="tm-attach"/);
  assert.match(css, /\.tm-attach-chips/);
  assert.doesNotMatch(views, /附件分析暂未开放/);
});
