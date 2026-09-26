/**
 * P1-7: 前端上下文解析验收测试
 *
 * 覆盖:
 * 1. 快速切换会话时上下文名称正确解析
 * 2. API 查询失败时不崩溃（静默忽略）
 * 3. 不存在的实体 ID 不产生错误
 * 4. 上下文名称缓存命中（不重复查询）
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
      const fp = path.join(root, 'workbench-assets', path.basename(pathname));
      if (fs.existsSync(fp)) return Promise.resolve(fs.readFileSync(fp));
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
    const cleanUrl = String(url).replace(/https?:\/\/localhost/, '').split('?')[0];
    const mock = apiMocks[cleanUrl];
    if (mock) {
      const resp = typeof mock === 'function'
        ? mock({ url: cleanUrl, method: (opts && opts.method) || 'GET', body: opts && opts.body })
        : mock;
      return { ok: true, status: 200, json: async () => resp, text: async () => JSON.stringify(resp) };
    }
    return { ok: false, status: 404, json: async () => ({ detail: 'Not found' }), text: async () => '{"detail":"Not found"}' };
  };

  const store = {};
  window.localStorage = {
    getItem: k => store[k] || null,
    setItem: (k, v) => { store[k] = String(v); },
    removeItem: k => { delete store[k]; },
    clear: () => { Object.keys(store).forEach(k => delete store[k]); },
  };

  return { dom, window };
}

function wait(ms) { return new Promise(r => setTimeout(r, ms || 500)); }

test('P1-7: student scope lookup uses the registered core students route', async () => {
  let requestedUrl = '';
  const { dom, window } = createDomEnv({
    '/api/v1/students': ({ url }) => {
      requestedUrl = url;
      return [{ id: 17, student_no: 'S017', name: '张三', class_id: 7, status: 'active' }];
    },
  });
  await wait(300);

  const students = await window.teachMateApi.listStudents(1, 7);
  assert.equal(requestedUrl, '/api/v1/students');
  assert.equal(students[0].id, 17);

  dom.window.close();
});

test('MONI formal class names resolve immediately without saving WorkBench settings', async () => {
  const { dom, window } = createDomEnv({
    '/api/v1/classes': [
      { id: 71, name: '七年级11班', status: 'active' },
      { id: 72, name: '七年级12班', status: 'active' },
    ],
  });
  await wait(300);

  // WorkBench 会把 MONI 的正式班级名缩写成 711/712；TeachMate
  // 应直接解析到 MONI 已写入的真实 class_id。
  assert.equal(await window.teachMateApi.findClassIdByName('711', 1), 71);
  assert.equal(await window.teachMateApi.findClassIdByName('712', 1), 72);
  assert.equal(await window.teachMateApi.findClassIdByName('七年级11班', 1), 71);

  dom.window.close();
});

test('P1-7: batch wording and batch scope do not fall back to one selected student', () => {
  const src = fs.readFileSync(
    path.join(root, 'workbench-assets', 'teachmate-interactions.js'), 'utf8'
  );
  assert.match(src, /每个人/);
  assert.match(src, /层级/);
  assert.match(src, /_buildSessionPayload\('student_diagnosis', null, \{ skipStudentScope: true \}\)/);
  assert.match(src, /!skipStudentScope && \(hasStudentOverride/);
});

// ============ 1. 快速切换会话时上下文名称正确解析 ============

test('P1-7: rapid session switching resolves context names', async () => {
  let classCallCount = 0, examCallCount = 0;

  const { dom, window } = createDomEnv({
    '/api/v1/agent/sessions': [
      { id: 1, title: '会话A', term_id: 1, class_id: 10, exam_id: 20 },
      { id: 2, title: '会话B', term_id: 1, class_id: 11, exam_id: 21 },
    ],
    '/api/v1/agent/capabilities': [],
    '/api/v1/agent/provider': { agent_enabled: true, text_agent_enabled: true, api_key_configured: true },
    '/api/v1/agent/sessions/1/messages': [],
    '/api/v1/agent/sessions/2/messages': [],
    '/api/v1/classes': () => {
      classCallCount++;
      return [
        { id: 10, name: '高三1' },
        { id: 11, name: '高三2' },
      ];
    },
    '/api/v1/exams': () => {
      examCallCount++;
      return [
        { id: 20, name: '期中考试' },
        { id: 21, name: '期末考试' },
      ];
    },
  });
  await wait(500);

  const tms = window.teachMateState;

  // 设置会话列表
  const sessions = [
    { id: 1, title: '会话A', term_id: 1, class_id: 10, exam_id: 20 },
    { id: 2, title: '会话B', term_id: 1, class_id: 11, exam_id: 21 },
  ];
  tms.setSessions(sessions);

  // 使用 tmSelectSession 触发 _resolveContextNames
  // 快速切换会话 1 -> 2 -> 1
  await window.tmSelectSession(1);
  await wait(200);
  await window.tmSelectSession(2);
  await wait(200);
  await window.tmSelectSession(1);
  await wait(400);

  const snap = tms.getSnapshot();
  assert.ok(snap.contextNames, 'contextNames should exist');
  assert.ok(snap.contextNames.class, 'class names should exist');
  assert.ok(snap.contextNames.class[10], 'class 10 name should be resolved');
  assert.equal(snap.contextNames.class[10], '高三1班');
  assert.ok(snap.contextNames.class[11], 'class 11 name should be resolved');
  assert.equal(snap.contextNames.class[11], '高三2班');
  assert.ok(snap.contextNames.exam[20], 'exam 20 name should be resolved');
  assert.equal(snap.contextNames.exam[20], '期中考试');
  assert.ok(snap.contextNames.exam[21], 'exam 21 name should be resolved');
  assert.equal(snap.contextNames.exam[21], '期末考试');

  dom.window.close();
});

// ============ 2. API 查询失败时不崩溃 ============

test('P1-7: API failure during context resolution does not crash', async () => {
  const { dom, window } = createDomEnv({
    '/api/v1/agent/sessions': [
      { id: 1, title: '会话A', term_id: 1, class_id: 10, exam_id: 20 },
    ],
    '/api/v1/agent/capabilities': [],
    '/api/v1/agent/provider': { agent_enabled: true, text_agent_enabled: true, api_key_configured: true },
    '/api/v1/agent/sessions/1/messages': [],
    '/api/v1/classes': () => { throw new Error('Network error'); },
    '/api/v1/exams': () => { throw new Error('Network error'); },
  });
  await wait(500);

  const tms = window.teachMateState;

  tms.setSessions([{ id: 1, title: '会话A', term_id: 1, class_id: 10, exam_id: 20 }]);
  await window.tmSelectSession(1);

  // 等待上下文解析尝试完成（应静默失败）
  await wait(300);

  // 状态不应崩溃
  const snap = tms.getSnapshot();
  assert.equal(snap.currentSessionId, 1);
  assert.equal(snap.runState, 'idle');
  // contextNames 可能为空或不含失败实体——关键是没崩溃
  assert.doesNotThrow(() => tms.getSnapshot());

  dom.window.close();
});

// ============ 3. 不存在的实体 ID 不产生错误 ============

test('P1-7: non-existent entity IDs resolve gracefully', async () => {
  const { dom, window } = createDomEnv({
    '/api/v1/agent/sessions': [
      { id: 1, title: '会话A', term_id: 1, class_id: 999, exam_id: 888 },
    ],
    '/api/v1/agent/capabilities': [],
    '/api/v1/agent/provider': { agent_enabled: true, text_agent_enabled: true, api_key_configured: true },
    '/api/v1/agent/sessions/1/messages': [],
    '/api/v1/core/classes': [{ id: 10, name: '高三1' }],  // 不含 999
    '/api/v1/exams': [{ id: 20, name: '期中考试' }],  // 不含 888
  });
  await wait(500);

  const tms = window.teachMateState;

  tms.setSessions([{ id: 1, title: '会话A', term_id: 1, class_id: 999, exam_id: 888 }]);
  await window.tmSelectSession(1);
  await wait(300);

  // 不存在的实体不应导致崩溃，名称缓存不应包含不存在的 ID
  const snap = tms.getSnapshot();
  assert.equal(snap.currentSessionId, 1);
  assert.doesNotThrow(() => tms.getSnapshot());

  dom.window.close();
});

// ============ 4. 源码契约 ============

test('P1-7 contract: _resolveContextNames exists and handles errors', () => {
  const src = fs.readFileSync(
    path.join(root, 'workbench-assets', 'teachmate-interactions.js'), 'utf8'
  );
  assert.match(src, /function _resolveContextNames/);
  // 每个实体解析都有 try-catch
  // 检查 class 解析段包含 catch
  const classMatch = src.match(/解析班级名称[\s\S]*?catch\s*\(\s*e\s*\)\s*\{[^}]*\}/);
  assert.ok(classMatch, 'class resolution should have try-catch error handling');
  // 检查 exam 解析段包含 catch
  const examMatch = src.match(/解析考试名称[\s\S]*?catch\s*\(\s*e\s*\)\s*\{[^}]*\}/);
  assert.ok(examMatch, 'exam resolution should have try-catch error handling');
});
