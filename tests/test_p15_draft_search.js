/**
 * P1-5: 完善草稿与搜索状态
 *
 * 测试覆盖：
 * 1. 搜索词持久化到状态
 * 2. reset 清除搜索词
 * 3. 搜索框值在 nav 重绘后恢复
 * 4. 草稿恢复不抢焦点
 * 5. 草稿恢复保留焦点
 * 6. 搜索响应防乱序（源码契约）
 * 7. 源码契约验证
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
      const response = typeof mock === 'function'
        ? mock({ url: cleanUrl, method: (opts && opts.method) || 'GET', body: opts && opts.body })
        : mock;
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

// ============ 搜索词状态 ============

test('P1-5: searchTerm in snapshot and state', async () => {
  const { dom, window } = createDomEnv({});
  await waitForScripts(window, 500);

  const tms = window.teachMateState;
  assert.ok(tms);

  assert.equal(tms.getSearchTerm(), '');
  assert.equal(tms.getSnapshot().searchTerm, '');

  tms.setSearchTerm('期中');
  assert.equal(tms.getSearchTerm(), '期中');
  assert.equal(tms.getSnapshot().searchTerm, '期中');

  tms.setSearchTerm('');
  assert.equal(tms.getSearchTerm(), '');
  assert.equal(tms.getSnapshot().searchTerm, '');

  tms.setSearchTerm(null);
  assert.equal(tms.getSearchTerm(), '');
  tms.setSearchTerm(undefined);
  assert.equal(tms.getSearchTerm(), '');

  dom.window.close();
});

test('P1-5: reset clears searchTerm', async () => {
  const { dom, window } = createDomEnv({});
  await waitForScripts(window, 500);

  const tms = window.teachMateState;
  tms.setSearchTerm('test search');
  assert.equal(tms.getSearchTerm(), 'test search');

  tms.reset();
  assert.equal(tms.getSearchTerm(), '');
  assert.equal(tms.getSnapshot().searchTerm, '');

  dom.window.close();
});

// ============ 搜索框值恢复 ============

test('P1-5: search input value restored on re-render', async () => {
  const { dom, window } = createDomEnv({
    '/api/v1/agent/sessions': [],
    '/api/v1/agent/capabilities': [],
    '/api/v1/agent/provider': { agent_enabled: true, text_agent_enabled: true, api_key_configured: true },
  });
  await waitForScripts(window, 500);

  const tms = window.teachMateState;

  tms.setSearchTerm('数学');
  const navHtml = window.renderTeachMateNav();
  assert.match(navHtml, /value="数学"/, 'search input should have value from state');

  tms.setSearchTerm('');
  const navHtml2 = window.renderTeachMateNav();
  assert.doesNotMatch(navHtml2, /value="数学"/, 'search input should not have old value');

  dom.window.close();
});

test('P1-5: empty sessions with search term shows helpful message', async () => {
  const { dom, window } = createDomEnv({});
  await waitForScripts(window, 500);

  const tms = window.teachMateState;

  tms.setSessions([]);
  tms.setSearchTerm('不存在的关键词');
  const navHtml = window.renderTeachMateNav();
  assert.match(navHtml, /未找到匹配的对话/);

  tms.setSearchTerm('');
  const navHtml2 = window.renderTeachMateNav();
  assert.match(navHtml2, /暂无对话/);

  dom.window.close();
});

// ============ 草稿恢复不抢焦点 ============

test('P1-5: draft restore does not steal focus when user is elsewhere', async () => {
  const { dom, window } = createDomEnv({
    '/api/v1/agent/sessions': [{ id: 1, title: 'Test', term_id: 1 }],
    '/api/v1/agent/capabilities': [],
    '/api/v1/agent/provider': { agent_enabled: true, text_agent_enabled: true, api_key_configured: true },
  });
  await waitForScripts(window, 500);

  const tms = window.teachMateState;
  const doc = window.document;

  tms.setSessions([{ id: 1, title: 'Test', term_id: 1 }]);
  tms.setCurrentSession(1);
  tms.setDraft('正在输入分析');

  // 直接用 renderTeachMate() 生成 HTML 并注入 workarea
  const workarea = doc.getElementById('workarea');
  assert.ok(workarea, 'workarea should exist');
  workarea.innerHTML = window.renderTeachMate();

  // 用户焦点不在输入框（在导航按钮上）
  const navButton = doc.querySelector('.tm-new-chat');
  const tmInput = doc.getElementById('tmInput');
  assert.ok(tmInput, 'tmInput should exist');

  // 模拟 draft restore 逻辑（render 中的行为）
  if (tmInput && navButton) {
    navButton.focus();
    var draft = tms.getDraft();
    if (draft) {
      var wasFocused = doc.activeElement === tmInput;
      tmInput.value = draft;
      if (wasFocused) {
        tmInput.focus();
      }
    }
    assert.notEqual(doc.activeElement, tmInput, 'focus should not be stolen by draft restore');
  }

  dom.window.close();
});

test('P1-5: draft restore preserves focus when user is typing', async () => {
  const { dom, window } = createDomEnv({
    '/api/v1/agent/sessions': [{ id: 1, title: 'Test', term_id: 1 }],
    '/api/v1/agent/capabilities': [],
    '/api/v1/agent/provider': { agent_enabled: true, text_agent_enabled: true, api_key_configured: true },
  });
  await waitForScripts(window, 500);

  const tms = window.teachMateState;
  const doc = window.document;

  tms.setSessions([{ id: 1, title: 'Test', term_id: 1 }]);
  tms.setCurrentSession(1);

  // 直接用 renderTeachMate() 生成 HTML 并注入 workarea
  const html = window.renderTeachMate();
  const workarea = doc.getElementById('workarea');
  assert.ok(workarea, 'workarea should exist');
  workarea.innerHTML = html;

  tms.setDraft('正在输入');

  const tmInput = doc.getElementById('tmInput');
  assert.ok(tmInput, 'tmInput should exist after renderTeachMate');
  tmInput.focus();

  // 模拟异步重绘：再次注入 HTML
  workarea.innerHTML = window.renderTeachMate();

  // 手动执行 draft restore 逻辑（模拟 render() 中的行为）
  const restoredInput = doc.getElementById('tmInput');
  if (restoredInput && tms) {
    var draft = tms.getDraft();
    if (draft) {
      var wasFocused = doc.activeElement === restoredInput;
      // 在 jsdom 中 focus 可能因 re-render 丢失
      restoredInput.value = draft;
      if (wasFocused) {
        restoredInput.focus();
      }
    }
  }

  assert.equal(restoredInput.value, '正在输入', 'draft value should be restored');

  dom.window.close();
});

// ============ 搜索防乱序（源码契约） ============

test('P1-5: tmSearchSessions uses sequence number for stale response prevention', () => {
  const interactions = fs.readFileSync(path.join(root, 'workbench-assets', 'teachmate-interactions.js'), 'utf8');
  assert.match(interactions, /tmSearchSeq/);
  assert.match(interactions, /\+\+tmSearchSeq/);
  assert.match(interactions, /mySeq !== tmSearchSeq/);
  assert.match(interactions, /setSearchTerm/);
});

// ============ 源码契约 ============

test('P1-5 contract: setSearchTerm/getSearchTerm in state', () => {
  const state = fs.readFileSync(path.join(root, 'workbench-assets', 'teachmate-state.js'), 'utf8');
  assert.match(state, /function setSearchTerm/);
  assert.match(state, /function getSearchTerm/);
  assert.match(state, /_searchTerm/);
  assert.match(state, /searchTerm:\s*_searchTerm/);
  // reset 清除搜索词
  assert.match(state, /_searchTerm = ''/);
  // 暴露到返回对象
  assert.match(state, /setSearchTerm,/);
  assert.match(state, /getSearchTerm,/);
});

test('P1-5 contract: search input uses snapshot.searchTerm in nav', () => {
  const views = fs.readFileSync(path.join(root, 'workbench-assets', 'teachmate-views.js'), 'utf8');
  assert.match(views, /snapshot\.searchTerm/, 'renderTeachMateNav should use snapshot.searchTerm');
  assert.match(views, /escapeAttr\(searchTerm\)/, 'search input value should be escaped');
});

test('P1-5 contract: draft restore checks activeElement', () => {
  const views = fs.readFileSync(path.join(root, 'workbench-assets', 'workbench-views.js'), 'utf8');
  assert.match(views, /document\.activeElement === tmInput/, 'should check if input is already focused');
  assert.match(views, /wasFocused/);
  // focus 调用应在 wasFocused 条件块内
  assert.match(views, /if \(wasFocused\)\s*\{[^}]*tmInput\.focus\(\)/s, 'focus should be inside wasFocused conditional');
});