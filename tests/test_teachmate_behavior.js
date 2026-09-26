/**
 * P1-G: TeachMate 行为测试
 *
 * 升级原有的源码字符串匹配测试为真实的 jsdom 行为测试。
 * 覆盖：草稿持久化、上下文名称缓存、状态机转换、错误处理、证据存储、视图契约。
 */

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM, ResourceLoader } = require('jsdom');
const { loadWorkbenchHtml } = require('./helpers/workbench_html');

const root = path.join(__dirname, '..');

class LocalAssetLoader extends ResourceLoader {
  fetch(url) {
    const pathname = new URL(url).pathname;
    if (pathname.startsWith('/workbench-assets/')) {
      const filename = path.basename(pathname);
      const filepath = path.join(root, 'workbench-assets', filename);
      if (fs.existsSync(filepath)) {
        return Promise.resolve(fs.readFileSync(filepath));
      }
    }
    return null;
  }
}

function createDomEnv(apiMocks) {
  const html = loadWorkbenchHtml();
  const dom = new JSDOM(html, {
    runScripts: 'dangerously',
    resources: new LocalAssetLoader(),
    url: 'https://localhost/',
    pretendToBeVisual: true,
  });
  const window = dom.window;

  window.fetch = async (url, opts) => {
    const urlStr = String(url);
    const cleanUrl = urlStr.replace(/https?:\/\/localhost/, '');
    const mock = apiMocks[cleanUrl];
    if (mock) {
      const response = typeof mock === 'function' ? mock({ url: cleanUrl, method: (opts && opts.method) || 'GET', body: opts && opts.body }) : mock;
      return {
        ok: true,
        status: 200,
        json: async () => response,
        text: async () => JSON.stringify(response),
      };
    }
    return {
      ok: false,
      status: 404,
      json: async () => ({ detail: 'Not found' }),
      text: async () => '{"detail":"Not found"}',
    };
  };

  const store = {};
  window.localStorage = {
    getItem: (k) => store[k] || null,
    setItem: (k, v) => { store[k] = String(v); },
    removeItem: (k) => { delete store[k]; },
    clear: () => { Object.keys(store).forEach(k => delete store[k]); },
  };

  return { dom, window };
}

function waitForScripts(window, ms) {
  return new Promise(resolve => setTimeout(resolve, ms || 500));
}

// ============ 行为测试：状态机 ============

test('P1-G: draft persistence per session', async () => {
  const { dom, window } = createDomEnv({});
  await waitForScripts(window, 500);

  const tms = window.teachMateState;
  assert.ok(tms, 'teachMateState should be available');

  tms.setSessions([
    { id: 1, title: 'Test', term_id: 1, class_id: 5, exam_id: 3 },
    { id: 2, title: 'Test2', term_id: 1, class_id: 6, exam_id: 4 },
  ]);
  tms.setCurrentSession(1);

  tms.setDraft('analyzing content');
  assert.equal(tms.getDraft(), 'analyzing content');

  tms.setCurrentSession(2);
  assert.equal(tms.getDraft(), '', 'new session should have empty draft');

  tms.setCurrentSession(1);
  assert.equal(tms.getDraft(), 'analyzing content', 'draft should restore');

  tms.clearDraft();
  assert.equal(tms.getDraft(), '', 'draft should be cleared');

  dom.window.close();
});

test('P1-G: context names cache', async () => {
  const { dom, window } = createDomEnv({});
  await waitForScripts(window, 500);

  const tms = window.teachMateState;
  assert.ok(tms);

  tms.setContextNames({
    class: { 5: 'Class1' },
    exam: { 3: 'Midterm' },
    student: { 42: 'Alice' },
  });

  const snapshot = tms.getSnapshot();
  assert.ok(snapshot.contextNames);
  assert.equal(snapshot.contextNames.class[5], 'Class1');
  assert.equal(snapshot.contextNames.exam[3], 'Midterm');
  assert.equal(snapshot.contextNames.student[42], 'Alice');

  dom.window.close();
});

test('P1-G: run state transitions', async () => {
  const { dom, window } = createDomEnv({});
  await waitForScripts(window, 500);

  const tms = window.teachMateState;
  assert.ok(tms);

  let snapshot = tms.getSnapshot();
  assert.equal(snapshot.runState, 'idle');

  tms.startSubmitting();
  snapshot = tms.getSnapshot();
  assert.equal(snapshot.runState, 'submitting');
  assert.ok(snapshot.isRunning);

  tms.handleSendResponse({ run_id: 123, status: 'queued' });
  snapshot = tms.getSnapshot();
  assert.equal(snapshot.runState, 'queued');
  assert.equal(snapshot.currentRunId, 123);
  assert.ok(snapshot.canCancel);

  dom.window.close();
});

test('P1-G: error handling', async () => {
  const { dom, window } = createDomEnv({});
  await waitForScripts(window, 500);

  const tms = window.teachMateState;
  assert.ok(tms);

  tms.setError('network error');
  let snapshot = tms.getSnapshot();
  assert.equal(snapshot.error, 'network error');

  tms.setError(null);
  snapshot = tms.getSnapshot();
  assert.equal(snapshot.error, null);

  dom.window.close();
});

test('P1-G: evidence storage per run', async () => {
  const { dom, window } = createDomEnv({});
  await waitForScripts(window, 500);

  const tms = window.teachMateState;
  assert.ok(tms);

  tms.handleSendResponse({ run_id: 42, status: 'running' });
  const snapshot = tms.getSnapshot();
  assert.equal(snapshot.currentRunId, 42);
  assert.ok(snapshot.evidence !== undefined);

  dom.window.close();
});

test('P1-G: plugin settings match the built-in extension contract', async () => {
  const { dom, window } = createDomEnv({});
  await waitForScripts(window, 500);

  window.teachMateState.setPlugins({ plugins: [
    { id: 'exam_analysis', display_name: '考试分析', description: '分析整体成绩、分数段和薄弱知识点。', version: '1.0.0', source: 'bundled', enabled: true, health: 'ready', permissions: ['teaching.read'], capabilities: [{ id: 'exam_analysis' }] },
    { id: 'student_diagnosis', display_name: '学生诊断', description: '支持全班批量或点名单人诊断。', version: '1.2.0', source: 'bundled', enabled: true, health: 'ready', permissions: ['teaching.read'], capabilities: [{ id: 'student_diagnosis' }] },
    { id: 'review_plan', display_name: '复习计划', description: '把分析结果整理成可执行的复习安排。', version: '1.0.0', source: 'bundled', enabled: true, health: 'ready', permissions: ['teaching.read'], capabilities: [{ id: 'review_plan' }] },
    { id: 'moni', display_name: 'MONI 学生数据', description: '通过只读 MCP 读取已授权的学生、班级和教学数据。', version: '1.0.0', source: 'bundled', enabled: true, health: 'ready', ui_visible: false, permissions: ['teaching.read'], capabilities: [{ id: 'moni_vfs_list' }] },
  ] });
  window.teachMateApi.disablePlugin = async () => ({ id: 'exam_analysis', display_name: '考试分析', description: '分析整体成绩、分数段和薄弱知识点。', version: '1.0.0', source: 'bundled', enabled: false, health: 'disabled', permissions: ['teaching.read'], capabilities: [{ id: 'exam_analysis' }] });
  window.tmOpenAgentSettings('plugins');
  assert.equal(window.document.querySelectorAll('.tm-settings-plugin-row').length, 3);
  assert.equal(window.document.querySelector('.tm-settings-count-badge').textContent, '3 个内置插件');
  assert.deepEqual(
    [...window.document.querySelectorAll('.tm-settings-plugin-state')].map(node => node.textContent),
    ['已启用', '已启用', '已启用'],
  );

  // 三个教学插件的第一操作是「详情」（原「管理」改为详情）
  const detailButtons = [...window.document.querySelectorAll('[data-act="tm-settings-plugin-details"]')];
  assert.equal(detailButtons.length, 3);
  assert.equal(detailButtons[0].textContent.trim(), '详情');

  // 详情页：介绍、标签、用法步骤与示例问法
  detailButtons[0].click();
  assert.equal(window.document.querySelectorAll('.tm-guide-prompt').length, 3);
  assert.ok(window.document.querySelector('.tm-guide-back'));
  assert.ok(window.document.querySelector('.tm-guide-tags'));
  assert.ok(window.document.querySelector('.tm-guide-steps'));

  // 返回功能扩展
  window.document.querySelector('[data-act="tm-settings-details-back"]').click();
  assert.equal(window.document.querySelectorAll('.tm-settings-plugin-row').length, 3);

  // 二级详情页的 Esc / X 也应只返回上一层设置页
  window.document.querySelector('[data-act="tm-settings-plugin-details"]').click();
  window.document.dispatchEvent(new window.KeyboardEvent('keydown', {
    key: 'Escape', bubbles: true, cancelable: true,
  }));
  assert.equal(window.document.querySelectorAll('.tm-settings-plugin-row').length, 3);
  window.document.querySelector('[data-act="tm-settings-plugin-details"]').click();
  window.document.getElementById('modalClose').click();
  assert.equal(window.document.querySelectorAll('.tm-settings-plugin-row').length, 3);

  // 管理仍可达：详情页内的 停用插件 打开原管理弹窗
  window.document.querySelector('[data-act="tm-settings-plugin-details"]').click();
  window.document.querySelector('#tmSettingsContent [data-act="tm-settings-plugin-toggle"]').click();
  assert.equal(window.document.getElementById('modalTitle').textContent, '管理插件');
  assert.equal(window.document.querySelector('[data-act="tm-settings-plugin-toggle-confirm"]').textContent, '停用插件');
  window.document.querySelector('[data-act="tm-settings-plugin-toggle-confirm"]').click();
  await new Promise(resolve => setTimeout(resolve, 0));

  window.tmOpenAgentSettings('plugins');
  assert.equal(window.document.querySelector('.tm-settings-plugin-state').textContent, '已停用');
  assert.ok(window.document.querySelector('.tm-settings-plugin-row.is-disabled'));

  dom.window.close();
});

test('P1-G: hidden data-source plugins stay out of TeachMate UI', async () => {
  const { dom, window } = createDomEnv({});
  await waitForScripts(window, 500);

  window.teachMateState.setPlugins({ plugins: [
    { id: 'exam_analysis', display_name: '考试分析', description: '分析整体成绩。', version: '1.0.0', source: 'bundled', enabled: true, health: 'ready', capabilities: [{ id: 'exam_analysis' }] },
    { id: 'moni', display_name: 'MONI 学生数据', description: '后台数据源', version: '1.0.0', source: 'bundled', enabled: true, health: 'ready', ui_visible: false, capabilities: [{ id: 'moni_vfs_list' }] },
  ] });

  window.tmOpenAgentSettings('plugins');
  assert.equal(window.document.querySelectorAll('.tm-settings-plugin-row').length, 1);
  assert.equal(window.document.querySelector('.tm-settings-count-badge').textContent, '1 个内置插件');
  assert.equal(window.document.querySelector('.tm-settings-plugin-list').textContent.includes('MONI 学生数据'), false);

  dom.window.close();
});

test('P1-G: plugin guide prompt fills composer and selects plugin', async () => {
  const { dom, window } = createDomEnv({});
  await waitForScripts(window, 500);

  window.teachMateState.setPlugins({ plugins: [
    { id: 'exam_analysis', display_name: '考试分析', description: '分析整体成绩、分数段和薄弱知识点。', version: '1.0.0', source: 'bundled', enabled: true, health: 'ready', permissions: ['teaching.read'], capabilities: [{ id: 'exam_analysis' }] },
  ] });
  window.tmOpenAgentSettings('plugins');
  window.document.querySelector('[data-act="tm-settings-plugin-details"]').click();
  const promptButton = window.document.querySelector('.tm-guide-prompt');
  assert.ok(promptButton, '详情页应有示例问法');
  const expectedPrompt = promptButton.dataset.prompt;

  promptButton.click();

  // 插件选中 + 问法进草稿（模型发送时以该插件能力执行）；设置弹窗关闭。
  // 「调用 ××」状态条由 selectedPluginId 派生渲染（views 层读取同一状态），
  // jsdom 不断言 DOM chip，只断言驱动它的状态契约。
  const snapshot = window.teachMateState.getSnapshot();
  assert.equal(snapshot.selectedPluginId, 'exam_analysis');
  assert.equal(window.teachMateState._pendingQuickTask, 'exam_analysis');
  assert.equal(window.teachMateState.getDraft(), expectedPrompt);
  assert.equal(window.document.getElementById('modal').classList.contains('show'), false);

  dom.window.close();
});

test('P1-G: welcome quick cards show plugin invocation chip and fill prompt', async () => {
  const { dom, window } = createDomEnv({});
  await waitForScripts(window, 500);

  window.teachMateState.setPlugins({ plugins: [
    { id: 'exam_analysis', display_name: '考试分析', description: '分析整体成绩、分数段和薄弱知识点。', version: '1.0.0', source: 'bundled', enabled: true, health: 'ready', permissions: ['teaching.read'], capabilities: [{ id: 'exam_analysis' }] },
    { id: 'student_diagnosis', display_name: '学生诊断', description: '支持全班批量或点名单人诊断。', version: '1.2.0', source: 'bundled', enabled: true, health: 'ready', permissions: ['teaching.read'], capabilities: [{ id: 'student_diagnosis' }] },
    { id: 'review_plan', display_name: '复习计划', description: '把分析结果整理成可执行的复习安排。', version: '1.0.0', source: 'bundled', enabled: true, health: 'ready', permissions: ['teaching.read'], capabilities: [{ id: 'review_plan' }] },
  ] });

  // 切到 TeachMate 视图（欢迎页三张快捷卡）
  const tabButton = window.document.querySelector('[data-act="tab-switch"][data-tab="teachmate"]');
  tabButton.click();
  await new Promise(resolve => setTimeout(resolve, 700));
  // 视图加载会异步重拉插件列表（jsdom 无后端 → 清空），点击前重新注入
  window.teachMateState.setPlugins({ plugins: [
    { id: 'exam_analysis', display_name: '考试分析', description: '分析整体成绩、分数段和薄弱知识点。', version: '1.0.0', source: 'bundled', enabled: true, health: 'ready', permissions: ['teaching.read'], capabilities: [{ id: 'exam_analysis' }] },
    { id: 'student_diagnosis', display_name: '学生诊断', description: '支持全班批量或点名单人诊断。', version: '1.2.0', source: 'bundled', enabled: true, health: 'ready', permissions: ['teaching.read'], capabilities: [{ id: 'student_diagnosis' }] },
    { id: 'review_plan', display_name: '复习计划', description: '把分析结果整理成可执行的复习安排。', version: '1.0.0', source: 'bundled', enabled: true, health: 'ready', permissions: ['teaching.read'], capabilities: [{ id: 'review_plan' }] },
  ] });

  const card = window.document.querySelector('[data-act="tm-suggestion"][data-quick-task="exam_analysis"]');
  assert.ok(card, '欢迎页应有「分析考试成绩」快捷卡');
  const expectedPrompt = card.dataset.prompt;

  card.click();

  // 点击快捷卡：填入问法 + 选中插件 → 出现「调用 考试分析」状态条
  const snapshot = window.teachMateState.getSnapshot();
  assert.equal(snapshot.selectedPluginId, 'exam_analysis');
  assert.equal(window.teachMateState._pendingQuickTask, 'exam_analysis');
  assert.equal(window.teachMateState.getDraft(), expectedPrompt);
  const input = window.document.getElementById('tmInput');
  assert.ok(input && input.value === expectedPrompt);
  const chip = window.document.querySelector('[data-act="tm-plugin-clear"]');
  assert.ok(chip, '应出现「调用 插件」状态条');
  assert.ok(chip.textContent.includes('考试分析'));

  // 取消状态条：插件选择与 pendingQuickTask 一并清空
  chip.click();
  assert.equal(window.teachMateState.getSnapshot().selectedPluginId, '');
  assert.equal(window.teachMateState._pendingQuickTask, null);

  dom.window.close();
});

test('P1-G: built-in tutorials open as modals', async () => {
  const { dom, window } = createDomEnv({});
  await waitForScripts(window, 500);

  window.tmOpenTutorial('moni');
  assert.equal(window.document.getElementById('modalTitle').textContent, 'MONI 数据同步教程');
  assert.ok(window.document.querySelectorAll('.tm-tutorial-section').length >= 4);
  assert.ok(window.document.querySelector('.tm-guide-steps'));

  window.tmOpenTutorial('model');
  assert.equal(window.document.getElementById('modalTitle').textContent, 'AI 模型配置教程');
  assert.ok(window.document.body.textContent.includes('platform.deepseek.com'));

  dom.window.close();
});

test('P1-G: closing a tutorial returns to the TeachMate settings page', async () => {
  const { dom, window } = createDomEnv({});
  await waitForScripts(window, 500);

  window.tmOpenAgentSettings('models');
  window.document.querySelector('[data-act="tm-open-tutorial"]').click();
  assert.equal(window.document.getElementById('modalTitle').textContent, 'AI 模型配置教程');

  window.document.dispatchEvent(new window.KeyboardEvent('keydown', {
    key: 'Escape', bubbles: true, cancelable: true,
  }));
  assert.equal(window.document.getElementById('modal').classList.contains('show'), true);
  assert.equal(window.document.querySelector('#tmSettingsContent h2').textContent, '模型');

  window.document.querySelector('[data-act="tm-open-tutorial"]').click();
  window.document.getElementById('modalClose').click();
  assert.equal(window.document.getElementById('modal').classList.contains('show'), true);
  assert.equal(window.document.querySelector('#tmSettingsContent h2').textContent, '模型');

  dom.window.close();
});

test('WorkBuddy connection uses the simplified read-only flow', async () => {
  const { dom, window } = createDomEnv({});
  await waitForScripts(window, 500);

  window.teachMateApi.listWorkBuddyConnections = async () => ([{
    id: 'token-1', scope: 'teaching.read', revoked: false,
  }]);
  window.teachMateApi.connectWorkBuddy = async () => ({
    scope: 'teaching.read',
    config: { mcpServers: { teachmate: { command: 'python', args: ['-m', 'server'] } } },
  });
  window.tmOpenAgentSettings('plugins');
  await new Promise(resolve => setTimeout(resolve, 0));

  assert.ok(window.document.querySelector('.tm-workbuddy-card'));
  assert.equal(window.document.getElementById('tm-workbuddy-connection-status').textContent, '已连接');
  const workBuddyCopy = window.document.querySelector('.tm-workbuddy-card-copy').textContent;
  assert.match(workBuddyCopy, /可以继续生成 PDF、PPT/);
  assert.doesNotMatch(workBuddyCopy, /10 个只读数据接口|默认隐藏学生身份|随时可以断开/);
  window.document.querySelector('[data-act="tm-connect-workbuddy"]').click();
  await new Promise(resolve => setTimeout(resolve, 0));

  assert.equal(window.document.getElementById('modalTitle').textContent, '连接 WorkBuddy');
  assert.match(window.document.querySelector('.tm-workbuddy-connect').textContent, /无需配对码/);
  assert.ok(window.document.getElementById('tmWorkBuddyConfig'));
  assert.equal(window.document.querySelector('.tm-workbuddy-advanced').open, false);

  dom.window.close();
});

test('P1-G: personalization settings expose tone, custom prompt and user address', async () => {
  const { dom, window } = createDomEnv({});
  await waitForScripts(window, 500);
  window.teachMateState.setPersonalization({
    user_address: '冯老师',
    tone: 'custom',
    custom_prompt: '先总结，再给三条行动建议。',
  });
  window.tmOpenAgentSettings('personalization');
  assert.equal(window.document.getElementById('tm-settings-user-address').value, '冯老师');
  assert.equal(window.document.getElementById('tm-settings-tone').value, 'custom');
  assert.equal(window.document.getElementById('tm-settings-custom-prompt').value, '先总结，再给三条行动建议。');
  assert.equal(window.document.getElementById('tm-settings-custom-tone-wrap').hidden, false);
  assert.match(window.document.body.textContent, /严谨务实/);
  assert.match(window.document.body.textContent, /亲和友好/);
  dom.window.close();
});

test('P1-G: settings opens on models without the removed system page', async () => {
  const { dom, window } = createDomEnv({});
  await waitForScripts(window, 500);
  window.tmOpenAgentSettings();
  const settingsModal = window.document.querySelector('#modal');
  assert.doesNotMatch(settingsModal.textContent, /使用设置/);
  assert.doesNotMatch(settingsModal.textContent, /成绩同步/);
  assert.equal(settingsModal.querySelector('[data-settings-tab="system"]'), null);
  assert.equal(settingsModal.querySelector('[data-settings-tab="data-sync"]'), null);
  assert.equal(settingsModal.querySelector('.tm-settings-page-head h2').textContent, '模型');

  window.tmOpenAgentSettings('token-usage');
  const usageText = window.document.querySelector('#modal').textContent;
  assert.match(usageText, /使用情况/);
  assert.doesNotMatch(usageText, /估算费用|费用情况/);
  assert.doesNotMatch(usageText, /USAGE INSIGHTS|Token 用量/);
  await new Promise(resolve => setTimeout(resolve, 30));
  dom.window.close();
});

test('P1-G: composer separates tools, class and exam scope controls', () => {
  const views = fs.readFileSync(path.join(root, 'workbench-assets', 'teachmate-views.js'), 'utf8');
  const interactions = fs.readFileSync(path.join(root, 'workbench-assets', 'teachmate-interactions.js'), 'utf8');
  assert.match(views, /data-act="tm-class-toggle"/);
  assert.match(views, /data-act="tm-class-option"/);
  assert.match(views, /data-act="tm-exam-toggle"/);
  assert.doesNotMatch(views, /data-act="tm-plus-select-exam"/);
  assert.match(views, /data-act="tm-plus-select-plugin"/);
  assert.match(views, /tm-plugin-chip/);
  assert.match(views, /tmExamMenu/);
  assert.match(views, /tmClassMenu/);
  assert.match(views, /tmPluginMenu/);
  assert.match(interactions, /TM_PLUGIN_ICON_POOL/);
  assert.match(interactions, /function tmOpenToolSubmenu\(id\)/);
  assert.match(interactions, /function tmToggleClassMenu\(button\)/);
  assert.match(interactions, /function tmSelectClass\(option\)/);
  assert.match(interactions, /tmFileDropBound/);
  assert.match(interactions, /event\.dataTransfer\.files/);
  assert.match(interactions, /tmOpenPluginInstallModal\(zipFiles\[0\]\)/);
  assert.match(interactions, /\.zip,application\/zip/);
  assert.match(interactions, /ext === ['"]\.zip['"]/);
  assert.doesNotMatch(interactions, /function tmOpenToolSubmenu\(id\)\s*\{\s*tmCloseImportMenu\(\)/);
  const css = fs.readFileSync(path.join(root, 'workbench-assets', 'teachmate.css'), 'utf8');
  assert.match(css, /tm-composer\.tm-drag-over/);
  assert.match(css, /\.tm-tool-submenu[^\{]*\{[^}]*left:\s*calc\(var\(--tm-tool-menu-width\)/s);
  assert.match(css, /\.tm-scope-picker\s*\{/);
  assert.match(css, /\.tm-class-menu\s*,\s*\n?\s*body\.tab-teachmate \.tm-exam-menu/);
  for (const icon of ['analytics', 'diagnosis', 'plan', 'document', 'spark', 'target']) {
    assert.ok(fs.existsSync(path.join(root, 'workbench-assets', 'plugin-icons', `${icon}.svg`)), `missing ${icon}.svg`);
  }
});

test('P1-G: selecting all classes updates the class picker label', async () => {
  const { dom, window } = createDomEnv({});
  await waitForScripts(window, 500);
  window.teachMateState.reset();
  window.teachMateState.setSelectedClassName('');
  const html = window.renderTeachMate();
  const area = window.document.getElementById('workarea');
  area.innerHTML = html;
  const picker = area.querySelector('.tm-class-picker-wrap .tm-scope-picker-label');
  assert.ok(picker);
  assert.equal(picker.textContent, '全部班级');
  dom.window.close();
});

// ============ source contract tests (regression baseline) ============

test('P1-G contract: budget confirmation wired', () => {
  const read = name => fs.readFileSync(path.join(root, 'workbench-assets', name), 'utf8');
  const api = read('teachmate-api.js');
  const state = read('teachmate-state.js');
  const views = read('teachmate-views.js');
  const interactions = read('teachmate-interactions.js');
  assert.match(api, /\/runs\/\$\{runId\}\/confirm/);
  assert.match(state, /confirmCurrentRun/);
  assert.match(views, /data-act="tm-confirm-budget"/);
  assert.match(interactions, /tm-confirm-budget/);
});

test('P1-G contract: structured answer rendering', () => {
  const views = fs.readFileSync(path.join(root, 'workbench-assets', 'teachmate-views.js'), 'utf8');
  for (const field of ['answer.findings', 'answer.recommendations', 'answer.limitations']) {
    assert.ok(views.includes(field), `missing renderer for ${field}`);
  }
  assert.match(views, /msg\.run_id/);
});

test('P1-G contract: session management actions', () => {
  const views = fs.readFileSync(path.join(root, 'workbench-assets', 'teachmate-views.js'), 'utf8');
  const interactions = fs.readFileSync(path.join(root, 'workbench-assets', 'teachmate-interactions.js'), 'utf8');
  for (const action of ['tm-session-search', 'tm-rename-session', 'tm-archive-session']) {
    assert.ok(views.includes(action), `missing ${action} view`);
  }
  assert.doesNotMatch(views, /data-act="tm-open-trash"/, 'archived conversations should not be shown in the sidebar');
  assert.match(interactions, /data-settings-tab=\\"archive\\"|tmSettingsTabHtml\(tab\)/);
  assert.match(interactions, /restoreSession/);
});

// ============ P1-F contract: context card uses session-fixed IDs ============

test('P1-G contract: context card uses session IDs not globals', () => {
  const views = fs.readFileSync(path.join(root, 'workbench-assets', 'teachmate-views.js'), 'utf8');
  assert.match(views, /session\.class_id/);
  assert.match(views, /session\.exam_id/);
  assert.match(views, /session\.student_id/);
  assert.match(views, /ctxNames/);
});

test('U6: completed analysis renders an active workspace and report area', async () => {
  const { dom, window } = createDomEnv({});
  await waitForScripts(window, 500);
  const tms = window.teachMateState;
  const answer = {
    summary: '班级平均分为 78.4 分，宾语从句是主要薄弱点。',
    findings: [{ title: '宾语从句正确率较低', description: '相关题目正确率为 54%，说明学生在从句结构识别上存在明显困难', evidence_ids: ['ev-1'] }],
    recommendations: [{ action: '生成分层复习计划', rationale: '建议先围绕从句结构进行分层练习，再根据练习结果调整难度', priority: 1, supports: ['ev-1'] }],
    limitations: ['试卷结构尚未录入，暂不能进行逐题诊断。'],
  };
  tms.reset();
  tms.setSessions([{ id: 1, title: '初二3班期中考试分析', term_id: 1, class_id: 5, exam_id: 3 }]);
  tms.setCurrentSession(1);
  tms.setContextNames({ class: { 5: '初二3' }, exam: { 3: '期中考试' } });
  tms.setMessages([
    { role: 'user', content_text: '帮我分析初二3班期中考试成绩。' },
    { role: 'assistant', content_text: '完整长报告原文：主要发现……行动建议……局限……', structured_answer: answer, run_id: 9, started_at: '2026-08-26T08:00:00Z', completed_at: '2026-08-26T08:01:08Z' },
  ]);
  tms.setReportAnswer(answer);
  tms.setCurrentRun({ id: 9, status: 'completed', started_at: '2026-08-26T08:00:00Z', completed_at: '2026-08-26T08:01:08Z' });
  const html = window.renderTeachMate();
  assert.match(html, /tm-layout-active/);
  assert.match(html, /data-testid="report-canvas"/);
  assert.match(html, /tm-inline-report-summary/);
  assert.match(html, /本次用时 1分08秒/);
  const host = dom.window.document.createElement('div');
  host.innerHTML = html;
  const chatSummary = host.querySelector('.tm-structured-summary');
  assert.ok(chatSummary, '结构化分析消息应保留摘要气泡');
  assert.match(chatSummary.textContent, /班级平均分为 78\.4 分/);
  assert.doesNotMatch(chatSummary.textContent, /主要发现|行动建议|局限/);
  assert.doesNotMatch(chatSummary.textContent, /完整长报告原文/);
  assert.ok(host.querySelector('[data-testid="report-findings"]'), '报告中应保留主要发现');
  assert.ok(host.querySelector('[data-testid="report-recommendations"]'), '报告中应保留行动建议');
  assert.match(host.querySelector('.tm-finding-claim').textContent, /相关题目正确率为 54%/);
  assert.match(host.querySelector('.tm-rec-copy p').textContent, /建议先围绕从句结构/);
  assert.equal(host.querySelector('[data-testid="report-limitations"]'), null, '主报告不展示局限段落');
  assert.equal(host.querySelector('.tm-evidence-refs'), null, '主报告不展示证据引用');
  assert.equal(host.querySelector('.tm-report-stat-grid-compact').children.length, 2, '主报告概览只展示两项业务指标');
  assert.match(html, /data-act="tm-class-toggle"/);
  assert.match(html, /data-act="tm-exam-toggle"/);
  assert.doesNotMatch(html, /tm-right-panel/);
  assert.doesNotMatch(html, /右侧可查看|打开完整报告/);
  assert.doesNotMatch(html, /预计\s*[¥￥]|预算金额/);
  assert.doesNotMatch(html, /data-act="tm-plus-select-exam"/);
  dom.window.close();
});

test('U6: ordinary assistant chat keeps its original content', async () => {
  const { dom, window } = createDomEnv({});
  await waitForScripts(window, 500);
  const tms = window.teachMateState;
  tms.reset();
  tms.setSessions([{ id: 1, title: '普通对话', term_id: 1, class_id: 5, exam_id: 3 }]);
  tms.setCurrentSession(1);
  tms.setMessages([{ role: 'assistant', content_text: '这是普通聊天回复，不是考试分析报告。' }]);
  const html = window.renderTeachMate();
  assert.match(html, /这是普通聊天回复，不是考试分析报告。/);
  assert.doesNotMatch(html, /tm-structured-summary/);
  assert.doesNotMatch(html, /tm-inline-report-summary/);
  dom.window.close();
});

test('U6: text-only analysis replies are laid out as readable report sections', async () => {
  const { dom, window } = createDomEnv({});
  await waitForScripts(window, 500);
  const tms = window.teachMateState;
  tms.reset();
  tms.setSessions([{ id: 1, title: '学生层级分析', term_id: 1, class_id: 5, exam_id: 3 }]);
  tms.setCurrentSession(1);
  tms.setMessages([{
    role: 'assistant',
    content_text: [
      '学生层级分析',
      '### 结论摘要',
      '当前 45 名考生按分数段可划分为**优秀 / 良好 / 中等 / 薄弱**四个层级。',
      '### 主要发现',
      '| 层级 | 分数范围 | 人数 | 占比 |',
      '|------|----------|------|------|',
      '| 优秀 (A) | 90–100 | 23 | 51.1% |',
      '| 良好 (B) | 80–90 | 13 | 28.9% |',
      '指标数值参与人数91平均分85.97合格率95.6%优秀率65.9%最高 / 最低99.5 / 39.5标准差12.03',
      '层级分数范围人数占比优秀 (A)90–1002351.1%良好 (B)80–901328.9%中等 (C)70–80511.1%待提升 (D)<7048.9%',
      '### 薄弱层（D级）细分',
      '分数段人数30–40（明显偏弱）150–60（接近及格线）160–70（勉强及格）2',
      '### 建议',
      '建议优先补充低分层学生的基础训练，并在补充数据后继续做逐题诊断。',
    ].join('\n'),
  }]);
  const host = dom.window.document.createElement('div');
  host.innerHTML = window.renderTeachMate();
  assert.ok(host.querySelector('.tm-plain-analysis-message'));
  assert.equal(host.querySelectorAll('.tm-text-report-section-title').length, 4);
  assert.equal(host.querySelectorAll('.md-table').length, 1);
  assert.doesNotMatch(host.querySelector('.tm-plain-analysis').textContent, /###|\*\*|层级 \| 分数范围/);
  assert.equal(host.querySelectorAll('.tm-text-report-tier-card').length, 4);
  assert.match(host.querySelector('.tm-text-report-tier-card.tier-0').textContent, /23.*51\.1%/);
  assert.match(host.querySelector('.tm-text-report-tier-card.tier-3').textContent, /4.*8\.9%/);
  assert.equal(host.querySelectorAll('.tm-text-report-metric-card').length, 6);
  assert.equal(host.querySelectorAll('.tm-text-report-band-card').length, 3);
  assert.match(host.querySelector('.tm-text-report-band-card').textContent, /30–40.*明显偏弱.*1/);
  assert.match(host.textContent, /建议优先补充低分层学生/);
  dom.window.close();
});

test('U6: running analysis renders progress without estimated time or budget copy', async () => {
  const { dom, window } = createDomEnv({});
  await waitForScripts(window, 500);
  const tms = window.teachMateState;
  tms.reset();
  tms.setSessions([{ id: 1, title: '分析任务', term_id: 1, class_id: 5, exam_id: 3 }]);
  tms.setCurrentSession(1);
  tms.setMessages([{ role: 'user', content_text: '分析这次考试。' }]);
  tms.startSubmitting();
  tms.handleRunEvent({ event: 'run.started', timestamp: '2026-08-26T08:00:00Z', data: {} });
  tms.setTimeline([
    { kind: 'tool', state: 'done', label: '读取成绩数据' },
    { kind: 'model', state: 'running', text: '正在定位薄弱知识点' },
  ]);
  const html = window.renderTeachMate();
  assert.match(html, /tm-layout-active/);
  assert.match(html, /tm-generating-process/);
  assert.match(html, /读取成绩数据/);
  assert.match(html, /正在定位薄弱知识点/);
  assert.match(html, /已用时/);
  assert.doesNotMatch(html, /tm-assistant-label|tm-assistant-label-mark/);
  assert.doesNotMatch(html, /预计\s*[¥￥]|预算金额|已耗时|已思考/);
  dom.window.close();
});

test('U6: runtime-provided public progress copy is rendered dynamically', async () => {
  const { dom, window } = createDomEnv({});
  await waitForScripts(window, 500);
  const tms = window.teachMateState;
  tms.reset();
  tms.setSessions([{ id: 1, title: '分析任务', term_id: 1 }]);
  tms.setCurrentSession(1);
  tms.setMessages([{ role: 'user', content_text: '请分析这次考试。' }]);
  tms.startSubmitting();
  tms.handleRunEvent({ event: 'run.started', timestamp: '2026-08-26T08:00:00Z', data: { title: '准备分析范围', summary: '已锁定本次考试和班级', next_action: '读取题目与成绩数据' } });
  tms.handleRunEvent({ event: 'model.started', timestamp: '2026-08-26T08:00:01Z', data: { title: '定位失分集中点', summary: '正在比较题型、分数段和历史表现', next_action: '生成可验证的教学建议' } });
  const html = window.renderTeachMate();
  assert.match(html, /定位失分集中点/);
  assert.match(html, /正在比较题型、分数段和历史表现/);
  assert.match(html, /生成可验证的教学建议/);
  assert.doesNotMatch(html, /准备分析范围/);
  dom.window.close();
});

test('U6: progress events append and update the visible timeline incrementally', async () => {
  const { dom, window } = createDomEnv({});
  await waitForScripts(window, 500);
  const tms = window.teachMateState;
  tms.reset();
  tms.setSessions([{ id: 1, title: '分析任务', term_id: 1 }]);
  tms.setCurrentSession(1);
  tms.setMessages([{ role: 'user', content_text: '请分析这次考试。' }]);
  tms.startSubmitting();
  // 真实发送接口会在 run.started 到达前把能力设置为结构化分析。
  tms.setCurrentRun({ id: 10, status: 'queued', capability: 'exam_analysis' });

  const renderedSnapshots = [];
  const unsubscribe = tms.subscribe(snapshot => {
    renderedSnapshots.push(snapshot.timeline.map(item => ({
      text: item.text,
      state: item.state,
    })));
  });

  tms.handleRunEvent({ event: 'run.started', timestamp: '2026-08-26T08:00:00Z', data: {} });
  assert.equal(tms.getSnapshot().timeline.length, 0, 'run.started 不能伪造工作流步骤');

  tms.handleRunEvent({
    event: 'tool.started',
    timestamp: '2026-08-26T08:00:01Z',
    data: { tool_name: '读取成绩数据', call_id: 'call-1' },
  });
  assert.equal(tms.getSnapshot().timeline.length, 1);
  assert.equal(tms.getSnapshot().timeline[0].state, 'running');

  tms.handleRunEvent({
    event: 'tool.completed',
    timestamp: '2026-08-26T08:00:02Z',
    data: { tool_name: '读取成绩数据', call_id: 'call-1', summary: '成绩数据读取完成' },
  });
  assert.equal(tms.getSnapshot().timeline.length, 1);
  assert.equal(tms.getSnapshot().timeline[0].state, 'done');
  assert.match(tms.getSnapshot().timeline[0].detail, /成绩数据读取完成/);

  tms.handleRunEvent({
    event: 'model.started',
    timestamp: '2026-08-26T08:00:03Z',
    data: { title: '定位薄弱知识点', summary: '正在比较题型表现' },
  });
  assert.equal(tms.getSnapshot().timeline.length, 2);
  assert.equal(tms.getSnapshot().timeline[1].state, 'running');
  assert.ok(renderedSnapshots.length >= 4, '每个时间线变化都应触发一次视图刷新，实际刷新 ' + renderedSnapshots.length + ' 次');

  unsubscribe();
  dom.window.close();
});

test('U6: duplicate run.started events do not duplicate the initialization row', async () => {
  const { dom, window } = createDomEnv({});
  await waitForScripts(window, 500);
  const tms = window.teachMateState;
  tms.reset();
  tms.setSessions([{ id: 1, title: '分析任务', term_id: 1 }]);
  tms.setCurrentSession(1);
  tms.setMessages([{ role: 'user', content_text: '请分析这次考试。' }]);
  tms.startSubmitting();
  tms.setCurrentRun({ id: 10, status: 'queued', capability: 'exam_analysis' });

  tms.handleRunEvent({ seq: 1, event: 'run.started', data: {} });
  tms.handleRunEvent({ seq: 2, event: 'run.started', data: { message: 'Harness 常驻运行已启动' } });
  assert.equal(tms.getSnapshot().timeline.filter(item => item.kind === 'plan').length, 0);
  dom.window.close();
});

test('U6: running analysis without real steps only shows analysis status', async () => {
  const { dom, window } = createDomEnv({});
  await waitForScripts(window, 500);
  const tms = window.teachMateState;
  tms.reset();
  tms.setSessions([{ id: 1, title: '分析任务', term_id: 1 }]);
  tms.setCurrentSession(1);
  tms.setMessages([{ role: 'user', content_text: '请分析这次考试。' }]);
  tms.startSubmitting();
  tms.setCurrentRun({ id: 10, status: 'queued', capability: 'exam_analysis' });
  tms.handleRunEvent({ event: 'run.started', data: {} });
  const html = window.renderTeachMate();
  assert.match(html, /分析中/);
  assert.doesNotMatch(html, /过程消息/);
  assert.doesNotMatch(html, /已接收分析任务|正在准备数据范围和分析上下文/);
  dom.window.close();
});

test('U6: ordinary chat stays lightweight despite generic model lifecycle events', async () => {
  const { dom, window } = createDomEnv({});
  await waitForScripts(window, 500);
  const tms = window.teachMateState;
  tms.reset();
  tms.setSessions([{ id: 1, title: '普通对话', term_id: 1 }]);
  tms.setCurrentSession(1);
  tms.setMessages([{ role: 'user', content_text: '你好' }]);
  tms.startSubmitting();
  tms.handleRunEvent({ event: 'run.started', timestamp: '2026-08-26T08:00:00Z', data: {} });
  tms.handleRunEvent({ event: 'model.started', timestamp: '2026-08-26T08:00:01Z', data: { session_id: 'chat-1' } });
  const html = window.renderTeachMate();
  assert.match(html, /正在回复/);
  assert.doesNotMatch(html, /tm-analysis-progress/);
  dom.window.close();
});

test('U6: completed report stays above a follow-up analysis in the same timeline', async () => {
  const { dom, window } = createDomEnv({});
  await waitForScripts(window, 500);
  const tms = window.teachMateState;
  const answer = { summary: '测试报告', findings: [], recommendations: [] };
  tms.reset();
  tms.setSessions([{ id: 1, title: '分析任务', term_id: 1, class_id: 5, exam_id: 3 }]);
  tms.setCurrentSession(1);
  tms.setMessages([
    { role: 'assistant', content_text: '第一轮分析已完成。', structured_answer: answer, run_id: 9, started_at: '2026-08-26T08:00:00Z', completed_at: '2026-08-26T08:01:08Z' },
    { role: 'user', content_text: '请继续分析第4题。' },
  ]);
  tms.startSubmitting();
  tms.handleRunEvent({ event: 'run.started', timestamp: '2026-08-26T08:03:00Z', data: {} });
  tms.setTimeline([{ kind: 'tool', state: 'running', label: '读取第4题数据' }]);
  const html = window.renderTeachMate();
  assert.match(html, /tm-inline-report-summary/);
  assert.match(html, /考试分析报告/);
  assert.match(html, /读取第4题数据/);
  assert.ok(html.indexOf('tm-inline-report-summary') < html.indexOf('tm-generating-process'));
  assert.doesNotMatch(html, /tm-right-panel/);
  dom.window.close();
});

test('U6: inline report exposes expandable data quality details', async () => {
  const { dom, window } = createDomEnv({});
  await waitForScripts(window, 500);
  const tms = window.teachMateState;
  const answer = { summary: '数据质量测试', findings: [], recommendations: [] };
  tms.reset();
  tms.setSessions([{ id: 1, title: '分析任务', term_id: 1, class_id: 5, exam_id: 3 }]);
  tms.setCurrentSession(1);
  tms.setMessages([{ role: 'assistant', content_text: '分析已完成。', structured_answer: answer, run_id: 9 }]);
  tms.setDataReady({ ready: true, issues: [{ message: '3条重复记录已排除' }] });
  const host = window.document.createElement('div');
  host.innerHTML = window.renderTeachMate();
  window.document.body.appendChild(host);
  const toggle = host.querySelector('[data-act=\"tm-toggle-inline-quality\"]');
  const detail = host.querySelector('.tm-inline-report-quality-detail');
  assert.ok(toggle);
  assert.equal(detail.hidden, true);
  window.handleTeachMateAction('tm-toggle-inline-quality', toggle);
  assert.equal(detail.hidden, false);
  assert.match(detail.textContent, /3条重复记录已排除/);
  dom.window.close();
});
