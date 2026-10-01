const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..', 'workbench-assets');
const read = name => fs.readFileSync(path.join(root, name), 'utf8');

test('TeachMate budget confirmation is wired end to end', () => {
  const api = read('teachmate-api.js');
  const state = read('teachmate-state.js');
  const views = read('teachmate-views.js');
  const interactions = read('teachmate-interactions.js');
  assert.match(api, /\/runs\/\$\{runId\}\/confirm/);
  assert.match(state, /confirmCurrentRun/);
  assert.match(views, /data-act="tm-confirm-budget"/);
  assert.match(interactions, /tm-confirm-budget/);
});

test('TeachMate renders the backend structured answer contract', () => {
  const views = read('teachmate-views.js');
  for (const field of ['answer.findings', 'answer.recommendations', 'answer.limitations']) {
    assert.ok(views.includes(field), `missing renderer for ${field}`);
  }
  assert.match(views, /msg\.run_id/);
});

test('TeachMate session management exposes search, archive and recycle actions', () => {
  const views = read('teachmate-views.js');
  const interactions = read('teachmate-interactions.js');
  for (const action of ['tm-session-search', 'tm-rename-session', 'tm-archive-session']) {
    assert.ok(views.includes(action), `missing ${action} view`);
  }
  assert.doesNotMatch(views, /data-act="tm-open-trash"/, 'archived conversations should not be shown in the sidebar');
  assert.match(interactions, /data-settings-tab=\\"archive\\"|tmSettingsTabHtml\(tab\)/);
  assert.match(interactions, /restoreSession/);
});

test('TeachMate product shell keeps a consistent light theme and responsive evidence access', () => {
  const views = read('teachmate-views.js');
  const css = read('teachmate.css');
  const enhancements = read('workbench-enhancements.css');
  assert.match(views, /tm-mobile-toolbar/);
  assert.match(views, /tm-suggestion-copy/);
  assert.doesNotMatch(css, /prefers-color-scheme:\s*dark/);
  assert.match(css, /\.tm-right-panel\s*\{\s*display:\s*none/s);
  assert.match(css, /\.tm-settings-modal-content \.tm-settings-sidebar\s*\{[^}]*padding-top:\s*8px/s);
  assert.match(css, /\.tm-settings-modal-content \.tm-settings-sidebar\s*\{[^}]*position:\s*static !important[^}]*width:\s*100% !important/s);
  assert.match(enhancements, /body\.tab-teachmate #sidebar\s*\{\s*padding-top:\s*6px !important/s);
  assert.match(enhancements, /body:not\(\.tab-teachmate\) #sidebar\s*\{\s*padding-top:\s*8px !important/s);
  assert.match(enhancements, /\.container\s*\{[^}]*min-height:\s*calc\(100vh\s*-\s*56px\) !important/s);
  assert.match(enhancements, /\.container\s*>\s*aside,\s*\.container\s*>\s*#sidebar\s*\{[^}]*top:\s*56px !important/s);
});

test('TeachMate assistant identity uses the product mark instead of the legacy anime avatar', () => {
  const views = read('teachmate-views.js');
  const css = read('teachmate.css');
  assert.match(views, /app-icon-256\.png\?v=202608241930/);
  assert.doesNotMatch(views, /teachmate-icon\.jpg/);
  assert.match(css, /\.tm-welcome-icon[^}]*overflow:\s*hidden/s);
  assert.match(css, /#f4dda0|#f2dda5/);
});

test('TeachMate home restores original chat and analysis entry points', () => {
  const views = read('teachmate-views.js');
  assert.doesNotMatch(views, /teachMateTasks\.home\(\)/);
  assert.match(views, /你可以这样问我/);
  for (const capability of ['exam_analysis', 'student_diagnosis', 'review_plan']) {
    assert.ok(views.includes('data-quick-task="' + capability + '"'));
  }
  assert.match(views, /quickUnavailable\('exam_analysis'\)/);
  assert.match(views, /quickUnavailable\('student_diagnosis'\)/);
  assert.match(views, /quickUnavailable\('review_plan'\)/);
});

test('TeachMate keeps a newly created empty conversation on the welcome page', () => {
  const views = read('teachmate-views.js');
  assert.match(views, /const hasMessages = Array\.isArray\(snapshot\.messages\) && snapshot\.messages\.length > 0/);
  assert.match(views, /const showWelcome = !snapshot\.scopePrompt && \(!hasSession \|\| \(!hasMessages && !snapshot\.isRunning && snapshot\.runState !== 'waiting_confirmation'\)\)/);
  assert.match(views, /const scopePromptHtml = _renderScopePrompt\(snapshot\.scopePrompt\)/);
  assert.match(views, /const messagesHtml = showWelcome \? _renderWelcomePage\(\) : _renderMessages\(snapshot\)/);
});

test('TeachMate preserves manual scroll and keeps cancellation available during queued runs', () => {
  const workbenchViews = read('workbench-views.js');
  const teachmateViews = read('teachmate-views.js');
  assert.match(workbenchViews, /previousScrollBottomGap < 24/);
  assert.match(workbenchViews, /Math\.abs\(nextMessages\.scrollTop - restoredScrollTop\)/);
  assert.match(teachmateViews, /snapshot\.currentRunId/);
  assert.match(teachmateViews, /tm-progress-cancel/);
});

test('原卷资料删除为确认后永久删除，不再提供归档入口', () => {
  const views = read('workbench-views.js');
  const interactions = read('workbench-interactions.js');
  assert.doesNotMatch(views, /data-act="error-doc-archive"/);
  assert.match(interactions, /error-doc-delete-confirm/);
  assert.match(interactions, /delete-with-workspace/);
  assert.match(interactions, /确认删除原卷资料/);
});

test('TeachMate 外部附件不进入 WorkBench 资料库选择器', () => {
  const interactions = read('teachmate-interactions.js');
  const api = read('teachmate-api.js');
  const core = read('workbench-core.js');
  assert.match(interactions, /metadata\.source/);
  assert.match(interactions, /source === 'paper_documents'/);
  assert.match(interactions, /source: 'teachmate-ui'/);
  assert.match(api, /form\.append\('source', opts\.source\)/);
  assert.match(core, /source === 'paper_documents'/);
  assert.match(core, /!source \|\| source === 'paper_documents'/);
});

test('TeachMate chat owns the viewport and keeps message alignment stable', () => {
  const css = read('teachmate.css');
  const enhancements = read('workbench-enhancements.css');
  assert.match(css, /body\.tab-teachmate main\s*\{[^}]*display:\s*flex\s*!important[^}]*overflow:\s*hidden\s*!important/s);
  assert.match(css, /\.tm-messages\s*\{[^}]*overflow-y:\s*auto\s*!important/s);
  assert.match(css, /\.tm-message-user\s*\{[^}]*justify-content:\s*flex-end\s*!important/s);
  assert.match(css, /\.tm-message-ai\s*\{[^}]*justify-content:\s*flex-start\s*!important/s);
  assert.match(enhancements, /header\s*\{[^}]*display:\s*grid\s*!important[^}]*grid-template-columns:/s);
  assert.match(enhancements, /#tmHeaderSearchBtn\s*\{[^}]*order:\s*10/s);
});

test('TeachMate exam analysis exposes optional database exam binding', () => {
  const state = read('teachmate-state.js');
  const views = read('teachmate-views.js');
  const interactions = read('teachmate-interactions.js');
  assert.match(state, /bindCurrentExam/);
  assert.match(views, /tm-import-group/);
  assert.match(views, /tm-exam-menu/);
  assert.match(views, /不选择考试/);
  assert.match(interactions, /tm-exam-option/);
  assert.match(interactions, /_buildSessionPayload\([^)]*\)/);
  assert.match(interactions, /explicitlyBindExam/);
});

test('TeachMate sends the selected model id and does not disable cards while provider loads', () => {
  const api = read('teachmate-api.js');
  const views = read('teachmate-views.js');
  const interactions = read('teachmate-interactions.js');
  assert.match(api, /if \(modelId\) body\.model_id = modelId/);
  assert.match(interactions, /currentModelId \|\| null/);
  assert.match(interactions, /sendMessage\(sessionId, text, quickTask, attachmentIds, modelId,\s*null, initialSnapshot\.selectedPluginId === 'targeted_practice' \? 'targeted_practice' : null\)/);
  assert.match(views, /if \(!provider\) return ''/);
  assert.match(views, /\(provider && unavailableReason\)/);
  assert.match(views, /tm-composer-actions[^\n]*modelPicker \+ sendBtn/);
  assert.match(views, /data-act="tm-model-toggle"/);
  assert.match(interactions, /activateModelProfile\(profileId\)/);
});

test('TeachMate uses a restrained thinking indicator and preserves plugin labels on user messages', () => {
  const views = read('teachmate-views.js');
  const css = read('teachmate.css');
  assert.match(views, /tm-thinking-wait/);
  assert.doesNotMatch(views, /tm-skeleton-lines/);
  assert.match(css, /\.tm-thinking-wait-dots i/);
  assert.match(views, /msg\.capability/);
  assert.match(views, /tm-message-user-stack/);
  assert.match(views, /tm-message-plugin-chip/);
});
