/**
 * P1-6: 真实行为测试（精简版）
 *
 * 覆盖 4 个核心交互流程：
 * 1. 发送消息（会话自动创建 + 状态机转换 + 草稿清除）
 * 2. 预算确认（waiting_confirmation -> confirm -> running）
 * 3. 取消运行（running -> cancelled）
 * 4. 搜索防乱序（先发后到不覆盖最新结果）
 *
 * 所有 API 请求均被 mock，Poll error 不产生未处理错误。
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

function createDomEnv(apiMocks, initialState) {
  const html = loadWorkbenchHtml();
  const dom = new JSDOM(html, {
    runScripts: 'dangerously',
    resources: new LocalAssetLoader(),
    url: 'https://localhost/',
    pretendToBeVisual: true,
    beforeParse(window) {
      if (initialState) {
        window.localStorage.setItem('hye_db_v1', JSON.stringify(initialState));
      }
    },
  });
  const window = dom.window;

  window.fetch = async (url, opts) => {
    const cleanUrl = String(url).replace(/https?:\/\/localhost/, '').split('?')[0];
    const mock = apiMocks[cleanUrl];
    if (mock) {
      const resp = typeof mock === 'function'
        ? mock({ url: cleanUrl, method: (opts && opts.method) || 'GET', body: opts && opts.body })
        : mock;
      const responseStatus = resp && resp.__responseStatus;
      const responseBody = responseStatus ? resp.body : resp;
      return {
        ok: !responseStatus || (responseStatus >= 200 && responseStatus < 300),
        status: responseStatus || 200,
        json: async () => responseBody,
        text: async () => JSON.stringify(responseBody),
      };
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

function createStateWithExam() {
  return {
    schema: 4,
    dataContract: 2,
    teacher: { name: '测试老师', subject: '初中英语' },
    classes: [],
    settings: { excellent: 90, pass: 60, criticalLow: 55 },
    students: [],
    archivedStudents: [],
    exams: [{
      id: 'exam-source-1',
      name: '九年级第一次月考',
      fullScore: 120,
      scores: {},
    }],
    archivedExams: [],
    classAliases: {},
    studentTags: [],
    currentExamId: 'exam-source-1',
    recitations: [],
    writings: [],
    errors: [],
    paperDocuments: [],
    critical: [],
    todos: [],
    dictation: {},
    dictationNames: ['自定义1', '自定义2'],
    dictationRanges: [[], []],
    homeworkTasks: [],
    homeworkRecords: {},
  };
}

function wait(ms) { return new Promise(r => setTimeout(r, ms || 500)); }

// ============ 1. 发送消息流程 ============

test('P1-6: send message creates session, transitions state, clears draft', async () => {
  let createCalled = false, sendCalled = false;

  const { dom, window } = createDomEnv({
    '/api/v1/agent/sessions': req => {
      if (req.method === 'POST') { createCalled = true; return { id: 42, title: '新对话', term_id: 1 }; }
      return [];
    },
    '/api/v1/agent/sessions/42/messages': req => {
      if (req.method === 'POST') { sendCalled = true; return { message_id: 100, run_id: 200, status: 'queued' }; }
      return [];
    },
    '/api/v1/agent/capabilities': [],
    '/api/v1/agent/provider': { agent_enabled: true, text_agent_enabled: true, api_key_configured: true },
    '/api/v1/agent/runs/200/events': { events: [], status: 'running', next_after: 100 },
  });
  await wait(500);

  const tms = window.teachMateState;
  const doc = window.document;

  tms.setProviderInfo({ agent_enabled: true, text_agent_enabled: true, api_key_configured: true });
  tms.setCapabilities([{ id: 'exam_analysis', name: '考试整体分析', requires_exam: false }]);
  // 默认能力即 exam_analysis；未选择数据库考试时也应允许自动创建会话并发送。
  tms._pendingQuickTask = null;

  // 渲染 TeachMate 到 DOM
  const wa = doc.getElementById('workarea');
  if (wa) wa.innerHTML = window.renderTeachMate();

  const tmInput = doc.getElementById('tmInput');
  assert.ok(tmInput, 'tmInput should exist');
  tmInput.value = '分析这次考试';
  tms.setDraft('分析这次考试');

  await window.tmSendMessage();
  await wait(100);

  assert.ok(createCalled, 'createSession should be called');
  assert.ok(sendCalled, 'sendMessage should be called');

  const snap = tms.getSnapshot();
  assert.equal(snap.currentSessionId, 42);
  assert.equal(snap.currentRunId, 200);
  assert.ok(['queued', 'running', 'submitting'].includes(snap.runState),
    'runState should be in active state, got: ' + snap.runState);

  const userMsg = snap.messages.find(m => m.role === 'user');
  assert.ok(userMsg, 'user message should be added');
  assert.equal(userMsg.content_text, '分析这次考试');

  assert.equal(tms.getDraft(), '', 'draft should be cleared after send');

  dom.window.close();
});

test('P1-6: student profile request defaults to a whole-class batch', async () => {
  let createdPayload = null;
  let groupPayload = null;
  let confirmPayload = null;
  let groupConfirmed = false;
  let groupCreateCount = 0;
  const initialState = createStateWithExam();
  initialState.students = [
    { id: 'local-zhangsan', name: '张三', studentNo: '001' },
    { id: 'local-lisi', name: '李四', studentNo: '002' },
  ];
  const { dom, window } = createDomEnv({
    '/api/v1/agent/sessions': req => {
      if (req.method === 'POST') {
        createdPayload = JSON.parse(req.body);
        return { id: 10, title: '批量学生画像', term_id: 1, exam_id: 21 };
      }
      return createdPayload
        ? [{ id: 9, title: '新对话', term_id: 1, exam_id: 21 }, { id: 10, title: '批量学生画像', term_id: 1, exam_id: 21 }]
        : [{ id: 9, title: '新对话', term_id: 1, exam_id: 21 }];
    },
    '/api/v1/agent/groups': req => {
      if (req.method === 'POST') {
        groupPayload = JSON.parse(req.body);
        groupCreateCount += 1;
        const ids = groupPayload.student_ids || [];
        return {
          id: groupCreateCount === 1 ? 700 : 701, status: 'waiting_confirmation', requested_student_count: ids.length,
          estimated_cost_yuan: ids.length * 0.3, estimated_tokens: ids.length * 6096, max_concurrency: 4,
          tasks: ids.map((id, index) => ({ id: index + 1, student_ids: [id], status: 'queued', task_role: 'student_worker' })),
        };
      }
      return null;
    },
    '/api/v1/agent/groups/700/confirm': req => {
      if (req.method === 'POST') {
        confirmPayload = JSON.parse(req.body);
        groupConfirmed = true;
      }
      return {
        id: 700, status: 'queued', requested_student_count: 2,
        estimated_cost_yuan: 0.6, estimated_tokens: 12192, max_concurrency: 4,
        tasks: [
          { id: 1, student_ids: [31], status: 'queued', task_role: 'student_worker' },
          { id: 2, student_ids: [32], status: 'queued', task_role: 'student_worker' },
        ],
      };
    },
    '/api/v1/agent/groups/701/confirm': req => {
      if (req.method === 'POST') {
        confirmPayload = JSON.parse(req.body);
        groupConfirmed = true;
      }
      return {
        id: 701, status: 'queued', requested_student_count: 1,
        estimated_cost_yuan: 0.3, estimated_tokens: 6096, max_concurrency: 4,
        tasks: [{ id: 1, student_ids: [31], status: 'queued', task_role: 'student_worker' }],
      };
    },
    '/api/v1/agent/groups/700': () => ({
      id: 700, status: groupConfirmed ? 'queued' : 'waiting_confirmation', requested_student_count: 2,
      estimated_cost_yuan: 0.6, estimated_tokens: 12192, max_concurrency: 4,
      tasks: [
        { id: 1, student_ids: [31], status: 'queued', task_role: 'student_worker' },
        { id: 2, student_ids: [32], status: 'queued', task_role: 'student_worker' },
      ],
    }),
    '/api/v1/agent/groups/700/cancel': { id: 700, status: 'cancelled', requested_student_count: 2, estimated_cost_yuan: 0.6, estimated_tokens: 12192, max_concurrency: 4, tasks: [] },
    '/api/v1/students': [
      { id: 31, name: '张三', student_no: '001' },
      { id: 32, name: '李四', student_no: '002' },
    ],
    '/api/v1/exams': [{ id: 21, source_key: 'exam-source-1', name: '九年级第一次月考' }],
    '/api/v1/agent/capabilities': [],
    '/api/v1/agent/provider': { agent_enabled: true, text_agent_enabled: true, api_key_configured: true },
  }, initialState);
  await wait(500);

  const tms = window.teachMateState;
  tms.setSessions([{ id: 9, title: '新对话', term_id: 1, exam_id: 21 }]);
  tms.setCurrentSession(9);
  tms.setPlugins({ plugins: [{
    id: 'student_diagnosis',
    display_name: '学生诊断',
    enabled: true,
    health: 'ready',
    capabilities: [{ id: 'student_diagnosis' }],
  }] });
  tms.setSelectedPluginId('student_diagnosis');

  window.setActiveTab('teachmate');
  const workarea = window.document.getElementById('workarea');
  workarea.innerHTML = window.renderTeachMate();
  window.document.getElementById('tmInput').value = '给我做个学生画像';

  await window.tmSendMessage();
  await wait(50);

  const snapshot = tms.getSnapshot();
  assert.ok(createdPayload, '默认全班诊断应创建批量会话');
  assert.equal(createdPayload.student_id, undefined, '批量会话不能绑定残留学生');
  assert.ok(groupPayload, '默认全班诊断应创建任务组');
  assert.deepEqual(groupPayload.student_ids, [31, 32], '全班任务必须包含班级内全部学生 ID');
  assert.equal(groupPayload.max_concurrency, 4);
  assert.equal(groupPayload.capability, 'student_diagnosis');
  assert.equal(groupPayload.require_confirmation, true, '全班任务必须先返回方案，不能直接运行');
  assert.match(snapshot.messages.at(-1).content_text, /全班.*2 名学生/);
  assert.equal(snapshot.analysisGroup.status, 'waiting_confirmation');
  assert.equal(snapshot.scopePrompt, null);
  assert.equal(tms.getDraft(), '');
  assert.equal(window.document.getElementById('tmInput').value, '');
  workarea.innerHTML = window.renderTeachMate();
  assert.match(workarea.textContent, /学生诊断执行方案/);
  assert.match(workarea.textContent, /张三/);
  assert.match(workarea.textContent, /李四/);
  assert.match(workarea.textContent, /12,192 tokens/);
  assert.doesNotMatch(workarea.textContent, /预计费用|预计 ¥|¥0\.60/);
  assert.doesNotMatch(workarea.textContent, /查看汇总/);
  assert.ok(window.document.querySelector('.tm-plan-confirm'), '输入区上方应显示方案确认面板');
  const confirmButton = window.document.querySelector('.tm-plan-confirm [data-act="tm-batch-confirm"]');
  assert.ok(confirmButton, '确认面板应提供“确认执行”按钮');
  assert.ok(window.document.querySelector('[data-act="tm-batch-edit"]'), '确认面板应提供“修改范围”按钮');

  window.handleTeachMateAction('tm-batch-edit', window.document.querySelector('[data-act="tm-batch-edit"]'));
  await wait(50);
  assert.ok(window.document.querySelector('.tm-batch-scope-editor'), '修改范围应打开学生勾选器');
  assert.equal(window.document.querySelectorAll('#tmBatchScopeList input[data-act="tm-batch-scope-student"]').length, 2);
  const lisiBox = window.document.querySelector('#tmBatchScopeList input[data-id="32"]');
  lisiBox.checked = false;
  window.handleTeachMateAction('tm-batch-scope-student', lisiBox);
  window.handleTeachMateAction('tm-batch-scope-save', window.document.querySelector('[data-act="tm-batch-scope-save"]'));
  await wait(80);
  assert.equal(groupCreateCount, 2, '应用范围应重新创建待确认方案');
  assert.deepEqual(groupPayload.student_ids, [31], '应用范围后只保留勾选的学生');
  assert.equal(tms.getSnapshot().analysisGroup.status, 'waiting_confirmation');

  const updatedConfirmButton = window.document.querySelector('.tm-plan-confirm [data-act="tm-batch-confirm"]');
  window.handleTeachMateAction('tm-batch-confirm', updatedConfirmButton);
  await wait(50);
  assert.deepEqual(confirmPayload, { confirmed_budget_yuan: 0.3 }, '只有教师确认后才调用执行接口');
  assert.equal(tms.getSnapshot().analysisGroup.status, 'queued');

  dom.window.close();
});

test('P1-6: completed batch renders only a status overview', async () => {
  const { dom, window } = createDomEnv({}, createStateWithExam());
  await wait(500);
  const tms = window.teachMateState;
  tms.setSessions([{ id: 88, title: '批量诊断', term_id: 1, class_id: 7, exam_id: 21 }]);
  tms.setCurrentSession(88);
  tms.setContextNames({ class: { 7: '711班' }, exam: { 21: '月考' }, student: { 31: '张三', 32: '李四', 33: '王五' } });
  tms.setAnalysisGroup({
    id: 800, status: 'partially_completed', requested_student_count: 3, estimated_tokens: 18000,
    max_concurrency: 4,
    tasks: [
      { id: 1, student_ids: [31], status: 'completed', task_role: 'student_worker' },
      { id: 2, student_ids: [32], status: 'running', task_role: 'student_worker' },
      { id: 3, student_ids: [33], status: 'failed', task_role: 'student_worker' },
    ],
  });
  const workarea = window.document.getElementById('workarea');
  workarea.innerHTML = window.renderTeachMate();
  assert.match(workarea.textContent, /1 已完成/);
  assert.match(workarea.textContent, /1 未完成/);
  assert.match(workarea.textContent, /1 失败/);
  assert.match(workarea.textContent, /重新生成未完成学生（2）/);
  assert.doesNotMatch(workarea.textContent, /查看汇总/);
  dom.window.close();
});

test('P1-6: failed batch tasks can be selectively retried', async () => {
  let retryPayload = null;
  const { dom, window } = createDomEnv({
    '/api/v1/agent/groups/800/retry': req => {
      retryPayload = JSON.parse(req.body);
      return {
        id: 800, status: 'queued', requested_student_count: 3, estimated_tokens: 18000,
        max_concurrency: 4,
        tasks: [
          { id: 1, student_ids: [31], status: 'completed', task_role: 'student_worker' },
          { id: 2, student_ids: [32], status: 'queued', task_role: 'student_worker' },
          { id: 3, student_ids: [33], status: 'failed', task_role: 'student_worker' },
        ],
      };
    },
    '/api/v1/agent/groups/800': { id: 800, status: 'queued', requested_student_count: 3, estimated_tokens: 18000, max_concurrency: 4, tasks: [] },
  }, createStateWithExam());
  await wait(500);
  const tms = window.teachMateState;
  tms.setSessions([{ id: 88, title: '批量诊断', term_id: 1, class_id: 7, exam_id: 21 }]);
  tms.setCurrentSession(88);
  tms.setContextNames({ class: { 7: '711班' }, exam: { 21: '月考' }, student: { 31: '张三', 32: '李四', 33: '王五' } });
  tms.setAnalysisGroup({
    id: 800, status: 'partially_completed', requested_student_count: 3, estimated_tokens: 18000, max_concurrency: 4,
    tasks: [
      { id: 1, student_ids: [31], status: 'completed', task_role: 'student_worker' },
      { id: 2, student_ids: [32], status: 'cancelled', task_role: 'student_worker' },
      { id: 3, student_ids: [33], status: 'failed', task_role: 'student_worker' },
    ],
  });
  window.setActiveTab('teachmate');
  const workarea = window.document.getElementById('workarea');
  workarea.innerHTML = window.renderTeachMate();
  const retryButton = workarea.querySelector('[data-act="tm-batch-retry"]');
  assert.ok(retryButton, 'completed batch with failures should offer retry');
  window.handleTeachMateAction('tm-batch-retry', retryButton);
  await wait(50);
  const retryBoxes = window.document.querySelectorAll('#tmBatchRetryList input[data-act="tm-batch-retry-student"]');
  assert.equal(retryBoxes.length, 2, 'only failed or cancelled students should be listed');
  retryBoxes[0].checked = false;
  window.handleTeachMateAction('tm-batch-retry-student', retryBoxes[0]);
  window.handleTeachMateAction('tm-batch-retry-save', window.document.querySelector('[data-act="tm-batch-retry-save"]'));
  await wait(100);
  assert.deepEqual(retryPayload, { student_ids: [33] }, 'teacher selection should be sent to retry API');
  assert.equal(tms.getSnapshot().analysisGroup.status, 'queued');
  dom.window.close();
});

test('P1-6: process rows keep the same DOM node across event updates', async () => {
  const { dom, window } = createDomEnv({}, createStateWithExam());
  await wait(500);
  const tms = window.teachMateState;
  tms.setSessions([{ id: 90, title: '过程测试', term_id: 1 }]);
  tms.setCurrentSession(90);
  window.setActiveTab('teachmate');
  tms.setCurrentRun({ id: 900, status: 'queued', capability: 'student_diagnosis', progress_mode: 'full' });
  tms.handleRunEvent({ event: 'run.started', timestamp: '2026-09-02T10:00:00Z', data: {} });
  tms.handleRunEvent({ event: 'tool.started', timestamp: '2026-09-02T10:00:01Z', data: { tool_name: 'get_student_list', call_id: 'c1' } });
  window.document.getElementById('workarea').innerHTML = window.renderTeachMate();
  await wait(50);
  const firstRow = window.document.querySelector('#tmProcessList .tm-proc-row');
  assert.ok(firstRow, 'a running process row should be rendered');
  tms.handleRunEvent({ event: 'tool.completed', timestamp: '2026-09-02T10:00:02Z', data: { tool_name: 'get_student_list', call_id: 'c1', summary: '已读取学生名单' } });
  await wait(50);
  assert.strictEqual(window.document.querySelector('#tmProcessList .tm-proc-row'), firstRow, 'event updates must patch the existing row');
  assert.equal(firstRow.getAttribute('data-state'), 'done');
  dom.window.close();
});

test('P1-6: an explicit whole-class request triggers a batch without selecting the plugin first', async () => {
  let groupPayload = null;
  const initialState = createStateWithExam();
  initialState.students = [{ id: 31, name: '张三', studentNo: '001' }];
  const { dom, window } = createDomEnv({
    '/api/v1/agent/sessions': req => req.method === 'POST'
      ? { id: 10, title: '全班学生诊断', term_id: 1, exam_id: 21 }
      : [{ id: 10, title: '全班学生诊断', term_id: 1, exam_id: 21 }],
    '/api/v1/agent/groups': req => {
      groupPayload = JSON.parse(req.body);
      return { id: 703, status: 'waiting_confirmation', requested_student_count: 1, estimated_cost_yuan: 0.3, estimated_tokens: 6096, max_concurrency: 1, tasks: [{ id: 3, student_ids: [31], status: 'queued', task_role: 'student_worker' }] };
    },
    '/api/v1/agent/groups/703': { id: 703, status: 'waiting_confirmation', requested_student_count: 1, estimated_cost_yuan: 0.3, estimated_tokens: 6096, max_concurrency: 1, tasks: [{ id: 3, student_ids: [31], status: 'queued', task_role: 'student_worker' }] },
    '/api/v1/students': [{ id: 31, name: '张三', student_no: '001' }],
    '/api/v1/exams': [{ id: 21, source_key: 'exam-source-1', name: '九年级第一次月考' }],
    '/api/v1/agent/capabilities': [],
    '/api/v1/agent/provider': { agent_enabled: true, text_agent_enabled: true, api_key_configured: true },
  }, initialState);
  await wait(500);

  const tms = window.teachMateState;
  tms.setPlugins({ plugins: [{
    id: 'student_diagnosis', display_name: '学生诊断', enabled: true,
    health: 'ready', capabilities: [{ id: 'student_diagnosis' }],
  }] });
  tms.setSelectedPluginId(null);
  tms._pendingQuickTask = null;
  window.setActiveTab('teachmate');
  window.document.getElementById('workarea').innerHTML = window.renderTeachMate();
  window.document.getElementById('tmInput').value = '给当前班级所有学生做学生画像，并给出个性化提升建议';

  await window.tmSendMessage();
  await wait(50);

  assert.ok(groupPayload, '明确的全班语义不应再追问学生姓名');
  assert.deepEqual(groupPayload.student_ids, [31]);
  assert.equal(groupPayload.capability, 'student_diagnosis');
  assert.equal(groupPayload.require_confirmation, true);
  assert.equal(tms.getSnapshot().scopePrompt, null);
  assert.doesNotMatch(
    (tms.getSnapshot().messages || []).map(message => message.content_text || '').join('\n'),
    /没有找到“做学生画像”/,
    '“所有学生做学生画像”不能把“做学生画像”误判成学生姓名',
  );

  dom.window.close();
});

test('P1-6: an explicit student name keeps diagnosis in the single-student flow', async () => {
  let createdPayload = null;
  let sentBody = null;
  const initialState = createStateWithExam();
  initialState.students = [{ id: 'local-zhangsan', name: '张三', studentNo: '001' }];
  const { dom, window } = createDomEnv({
    '/api/v1/agent/sessions': req => {
      if (req.method === 'POST') {
        createdPayload = JSON.parse(req.body);
        return { id: 10, title: '张三学生画像', term_id: 1, exam_id: 21, student_id: 31 };
      }
      return createdPayload
        ? [{ id: 9, title: '新对话', term_id: 1, exam_id: 21 }, { id: 10, title: '张三学生画像', term_id: 1, exam_id: 21, student_id: 31 }]
        : [{ id: 9, title: '新对话', term_id: 1, exam_id: 21 }];
    },
    '/api/v1/agent/sessions/9/messages': req => {
      if (req.method === 'POST') {
        sentBody = JSON.parse(req.body);
        return { message_id: 101, run_id: 201, status: 'queued', capability: 'student_diagnosis' };
      }
      return [];
    },
    '/api/v1/students': [{ id: 31, name: '张三', student_no: '001' }],
    '/api/v1/exams': [{ id: 21, source_key: 'exam-source-1', name: '九年级第一次月考' }],
    '/api/v1/agent/capabilities': [],
    '/api/v1/agent/provider': { agent_enabled: true, text_agent_enabled: true, api_key_configured: true },
    '/api/v1/agent/runs/201/events': { events: [], status: 'running', next_after: 0 },
  }, initialState);
  await wait(500);

  const tms = window.teachMateState;
  tms.setSessions([{ id: 9, title: '新对话', term_id: 1, exam_id: 21 }]);
  tms.setCurrentSession(9);
  tms.setPlugins({ plugins: [{
    id: 'student_diagnosis', display_name: '学生诊断', enabled: true,
    health: 'ready', capabilities: [{ id: 'student_diagnosis' }],
  }] });
  tms.setSelectedPluginId('student_diagnosis');

  window.setActiveTab('teachmate');
  window.document.getElementById('workarea').innerHTML = window.renderTeachMate();
  window.document.getElementById('tmInput').value = '分析张三的学生画像';

  await window.tmSendMessage();
  await wait(50);

  assert.equal(createdPayload, null, '点名学生应保留当前聊天，由本轮服务端解析绑定');
  assert.ok(sentBody, '点名学生后应真正发起单人诊断');
  assert.equal(sentBody.quick_task, 'student_diagnosis');
  assert.match(sentBody.content, /张三/);

  dom.window.close();
});

test('P1-6: score range semantics filter the default batch', async () => {
  let groupPayload = null;
  const initialState = createStateWithExam();
  initialState.students = [
    { id: 31, name: '甲同学', studentNo: '001' },
    { id: 32, name: '乙同学', studentNo: '002' },
  ];
  initialState.exams[0].scores = {
    31: { 英语: 85 },
    32: { 英语: 95 },
  };
  const { dom, window } = createDomEnv({
    '/api/v1/agent/sessions': req => req.method === 'POST'
      ? { id: 10, title: '批量学生画像', term_id: 1, exam_id: 21 }
      : [{ id: 10, title: '批量学生画像', term_id: 1, exam_id: 21 }],
    '/api/v1/agent/groups': req => {
      groupPayload = JSON.parse(req.body);
      return { id: 701, status: 'queued', requested_student_count: 1, completed_count: 0, failed_count: 0, cancelled_count: 0, tasks: [] };
    },
    '/api/v1/agent/groups/701': { id: 701, status: 'queued', requested_student_count: 1, completed_count: 0, failed_count: 0, cancelled_count: 0, tasks: [] },
    '/api/v1/students': [
      { id: 31, name: '甲同学', student_no: '001' },
      { id: 32, name: '乙同学', student_no: '002' },
    ],
    '/api/v1/exams': [{ id: 21, source_key: 'exam-source-1', name: '九年级第一次月考' }],
    '/api/v1/agent/capabilities': [],
    '/api/v1/agent/provider': { agent_enabled: true, text_agent_enabled: true, api_key_configured: true },
  }, initialState);
  await wait(500);

  const tms = window.teachMateState;
  tms.setPlugins({ plugins: [{
    id: 'student_diagnosis', display_name: '学生诊断', enabled: true,
    health: 'ready', capabilities: [{ id: 'student_diagnosis' }],
  }] });
  tms.setSelectedPluginId('student_diagnosis');
  window.setActiveTab('teachmate');
  window.document.getElementById('workarea').innerHTML = window.renderTeachMate();
  window.document.getElementById('tmInput').value = '分析80到90分之间的学生';

  await window.tmSendMessage();
  await wait(50);

  assert.ok(groupPayload, '分数范围应创建批量任务：' + JSON.stringify(tms.getSnapshot()));
  assert.deepEqual(groupPayload.student_ids, [31]);
  assert.equal(groupPayload.max_concurrency, 4);

  dom.window.close();
});

test('P1-6: a follow-up in a bound student session does not reopen a class batch', async () => {
  let sentBody = null;
  let groupCalled = false;
  const initialState = createStateWithExam();
  initialState.students = [{ id: 31, name: '张三', studentNo: '001' }];
  const session = { id: 9, title: '张三学生画像', term_id: 1, exam_id: 21, student_id: 31 };
  const { dom, window } = createDomEnv({
    '/api/v1/agent/sessions': [session],
    '/api/v1/agent/sessions/9/messages': req => {
      if (req.method === 'POST') {
        sentBody = JSON.parse(req.body);
        return { message_id: 102, run_id: 202, status: 'queued', capability: 'student_diagnosis' };
      }
      return [];
    },
    '/api/v1/agent/groups': () => {
      groupCalled = true;
      return { id: 702, status: 'queued', requested_student_count: 1, tasks: [] };
    },
    '/api/v1/students': [{ id: 31, name: '张三', student_no: '001' }],
    '/api/v1/exams': [{ id: 21, source_key: 'exam-source-1', name: '九年级第一次月考' }],
    '/api/v1/agent/capabilities': [],
    '/api/v1/agent/provider': { agent_enabled: true, text_agent_enabled: true, api_key_configured: true },
    '/api/v1/agent/runs/202/events': { events: [], status: 'running', next_after: 0 },
  }, initialState);
  await wait(500);

  const tms = window.teachMateState;
  tms.setSessions([session]);
  tms.setCurrentSession(9);
  tms.setPlugins({ plugins: [{
    id: 'student_diagnosis', display_name: '学生诊断', enabled: true,
    health: 'ready', capabilities: [{ id: 'student_diagnosis' }],
  }] });
  tms.setSelectedPluginId('student_diagnosis');
  window.setActiveTab('teachmate');
  window.document.getElementById('workarea').innerHTML = window.renderTeachMate();
  window.document.getElementById('tmInput').value = '继续分析他的薄弱点';

  await window.tmSendMessage();
  await wait(50);

  assert.ok(sentBody, '单人会话追问应继续发送到当前会话');
  assert.equal(groupCalled, false, '单人会话追问不能重新创建全班任务');
  assert.equal(tms.getSnapshot().currentSessionId, 9);

  dom.window.close();
});

test('P1-6: completed conversation can switch class and exam for a new analysis', async () => {
  let createdPayload = null;
  let sentSessionId = null;
  const initialState = createStateWithExam();
  initialState.classes = ['711', '712'];
  initialState.exams.push({ id: 'exam-source-2', name: '第二场考试', fullScore: 120, scores: {} });

  const { dom, window } = createDomEnv({
    '/api/v1/agent/sessions': req => {
      if (req.method === 'POST') {
        createdPayload = JSON.parse(req.body);
        return { id: 2, title: '新范围分析', term_id: 1, class_id: 12, exam_id: 22 };
      }
      return [{ id: 1, title: '原分析', term_id: 1, class_id: 11, exam_id: 21 }];
    },
    '/api/v1/agent/sessions/1/messages': [{ role: 'assistant', content_text: '原分析已完成。', run_id: 101 }],
    '/api/v1/agent/sessions/2/messages': req => {
      if (req.method === 'POST') {
        sentSessionId = 2;
        return { message_id: 202, run_id: 303, status: 'queued' };
      }
      return [];
    },
    '/api/v1/classes': [{ id: 12, name: '712' }],
    '/api/v1/exams': [{ id: 22, source_key: 'exam-source-2', name: '第二场考试' }],
    '/api/v1/agent/capabilities': [],
    '/api/v1/agent/provider': { agent_enabled: true, text_agent_enabled: true, api_key_configured: true },
    '/api/v1/agent/runs/303/events': { events: [], status: 'running', next_after: 0 },
  }, initialState);
  await wait(500);

  const tms = window.teachMateState;
  tms.setSessions([{ id: 1, title: '原分析', term_id: 1, class_id: 11, exam_id: 21 }]);
  tms.setCurrentSession(1);
  tms.setMessages([{ role: 'assistant', content_text: '原分析已完成。', run_id: 101 }]);
  tms.setContextNames({ class: { 11: '711班' }, exam: { 21: '第一场考试' } });
  tms.setAvailableExams([{ id: 22, source_key: 'exam-source-2', name: '第二场考试' }], 1);
  tms.setProviderInfo({ agent_enabled: true, text_agent_enabled: true, api_key_configured: true });

  window.setActiveTab('teachmate');
  const workarea = window.document.getElementById('workarea');
  workarea.innerHTML = window.renderTeachMate();
  const classToggle = window.document.querySelector('[data-act="tm-class-toggle"]');
  const examToggle = window.document.querySelector('[data-act="tm-exam-toggle"]');
  assert.equal(classToggle.disabled, false, '完成后班级选择器不应继续置灰');
  assert.equal(examToggle.disabled, false, '完成后考试选择器不应继续置灰');

  const classOption = window.document.querySelector('[data-act="tm-class-option"][data-class-name="712"]');
  window.handleTeachMateAction('tm-class-option', classOption);
  const examOption = Array.from(window.document.querySelectorAll('[data-act="tm-exam-option"]')).find(function (item) {
    return item.textContent.includes('第二场考试');
  });
  assert.ok(examOption, '已加载考试后应能找到第二场考试选项');
  window.handleTeachMateAction('tm-exam-option', examOption);
  assert.equal(tms.getSnapshot().selectedClassName, '712');
  assert.equal(tms.getSnapshot().selectedExamId, 'exam-source-2');

  const input = window.document.getElementById('tmInput');
  input.value = '请分析第二场考试';
  await window.tmSendMessage();
  await wait(100);

  dom.window.close();
  assert.deepEqual(createdPayload, { term_id: null, class_id: 12, exam_id: 22 }, '新的范围应传入真实数字 ID');
  assert.equal(sentSessionId, 2, '切换范围后消息应发送到新会话');
});

test('P1-6: structured API error is readable and submitting transitions to failed', async () => {
  const backendMessage = '当前任务缺少必要范围：exam_id。';
  const backendCode = 'SCOPE_MISSING_EXAM';

  const { dom, window } = createDomEnv({
    '/api/v1/agent/sessions': [{ id: 77, title: '附件分析', term_id: 1, exam_id: null }],
    '/api/v1/agent/sessions/77/messages': req => {
      if (req.method === 'POST') {
        return {
          __responseStatus: 409,
          body: { detail: { code: backendCode, message: backendMessage } },
        };
      }
      return [];
    },
    '/api/v1/agent/capabilities': [],
    '/api/v1/agent/provider': { agent_enabled: true, text_agent_enabled: true, api_key_configured: true },
  });
  await wait(500);

  const tms = window.teachMateState;
  const doc = window.document;
  tms.setSessions([{ id: 77, title: '附件分析', term_id: 1, exam_id: null }]);
  tms.setCurrentSession(77);

  const wa = doc.getElementById('workarea');
  if (wa) wa.innerHTML = window.renderTeachMate();
  const tmInput = doc.getElementById('tmInput');
  assert.ok(tmInput, 'tmInput should exist');
  tmInput.value = '帮我分析学生薄弱知识点';

  await window.tmSendMessage();
  await wait(50);

  const snap = tms.getSnapshot();
  assert.equal(snap.runState, 'failed');
  assert.equal(snap.isRunning, false);
  assert.match(snap.error, new RegExp(backendMessage));
  assert.match(snap.error, new RegExp(backendCode));
  assert.doesNotMatch(snap.error, /\[object Object\]/);

  const assistant = snap.messages.find(m => m.role === 'assistant');
  assert.ok(assistant, 'failed assistant message should exist');
  assert.match(assistant.content_text, new RegExp(backendMessage));
  assert.match(assistant.content_text, new RegExp(backendCode));
  assert.doesNotMatch(assistant.content_text, /\[object Object\]/);
  assert.equal(tms._pendingQuickTask, null);

  dom.window.close();
});

test('P1-6: exam binding defaults off and explicit opt-in adds numeric exam_id', async () => {
  const sessionPayloads = [];
  let nextSessionId = 50;

  const { dom, window } = createDomEnv({
    '/api/v1/agent/sessions': req => {
      if (req.method === 'POST') {
        sessionPayloads.push(JSON.parse(req.body));
        return { id: nextSessionId++, title: '新对话', term_id: null };
      }
      return [];
    },
    '/api/v1/exams': [{ id: 321, source_key: 'exam-source-1', name: '九年级第一次月考' }],
    '/api/v1/agent/capabilities': [],
    '/api/v1/agent/provider': { agent_enabled: true, text_agent_enabled: true, api_key_configured: true },
  }, createStateWithExam());
  await wait(500);

  const tms = window.teachMateState;
  const doc = window.document;
  tms.setProviderInfo({ agent_enabled: true, text_agent_enabled: true, api_key_configured: true });
  tms.setSessions([]);
  tms.setCurrentSession(null);

  const wa = doc.getElementById('workarea');
  if (wa) wa.innerHTML = window.renderTeachMate();

  const binding = doc.querySelector('[data-testid="exam-binding-choice"]');
  assert.equal(binding, null, 'exam binding helper row should not be rendered');
  assert.equal(tms.getSnapshot().bindCurrentExam, false);

  const newChat = doc.createElement('button');
  newChat.dataset.act = 'tm-new-chat';
  doc.body.appendChild(newChat);
  newChat.click();
  await wait(100);

  assert.equal(sessionPayloads.length, 0, 'clicking new chat must not create an empty backend session');
  assert.equal(tms.getSnapshot().currentSessionId, null);
  if (wa) wa.innerHTML = window.renderTeachMate();

  const firstInput = doc.getElementById('tmInput');
  firstInput.value = '分析这次考试';
  tms.setDraft('分析这次考试');
  await window.tmSendMessage();
  await wait(100);

  assert.equal(sessionPayloads.length, 1);
  assert.equal(Object.hasOwn(sessionPayloads[0], 'exam_id'), false,
    'default exam_analysis session must not include exam_id');

  tms.setSessions([]);
  tms.setCurrentSession(null);
  if (wa) wa.innerHTML = window.renderTeachMate();
  const examToggle = doc.querySelector('[data-act="tm-exam-toggle"]');
  const examMenu = doc.getElementById('tmExamMenu');
  assert.ok(examToggle, 'exam selector should be rendered');
  assert.ok(examMenu, 'exam menu should be rendered');
  assert.equal(examMenu.hidden, true);
  examToggle.click();
  await wait(20);
  assert.equal(examMenu.hidden, false, 'clicking the exam selector should keep the menu open');
  examToggle.click();
  await wait(20);
  assert.equal(examMenu.hidden, true, 'clicking the exam selector again should close the menu');
  const toggle = doc.querySelector('[data-act="tm-exam-option"][data-exam-id="exam-source-1"]');
  assert.ok(toggle, 'exam option should be rendered');
  toggle.click();
  await wait(20);
  assert.equal(tms.getSnapshot().bindCurrentExam, true);

  const secondNewChat = doc.createElement('button');
  secondNewChat.dataset.act = 'tm-new-chat';
  doc.body.appendChild(secondNewChat);
  secondNewChat.click();
  await wait(100);

  assert.equal(sessionPayloads.length, 1, 'starting another chat must still wait for its first message');
  if (wa) wa.innerHTML = window.renderTeachMate();
  const secondInput = doc.getElementById('tmInput');
  secondInput.value = '结合这次考试分析';
  tms.setDraft('结合这次考试分析');
  await window.tmSendMessage();
  await wait(100);

  assert.equal(sessionPayloads.length, 2);
  assert.equal(sessionPayloads[1].exam_id, 321,
    'explicit opt-in must resolve and include the numeric database exam id');

  dom.window.close();
});

// ============ 2. 预算确认流程 ============

test('P1-6: budget confirmation transitions from waiting to running', async () => {
  let confirmCalled = false;

  const { dom, window } = createDomEnv({
    '/api/v1/agent/sessions': [],
    '/api/v1/agent/capabilities': [],
    '/api/v1/agent/provider': { agent_enabled: true, text_agent_enabled: true, api_key_configured: true },
    '/api/v1/agent/runs/300': { id: 300, status: 'waiting_confirmation', estimated_cost_yuan: 0.05 },
    '/api/v1/agent/runs/300/confirm': req => {
      if (req.method === 'POST') { confirmCalled = true; return { status: 'queued' }; }
      return {};
    },
    '/api/v1/agent/runs/300/events': { events: [], status: 'running', next_after: 100 },
  });
  await wait(500);

  const tms = window.teachMateState;

  // 模拟已进入 waiting_confirmation 状态
  tms.setSessions([{ id: 10, title: 'Test', term_id: 1 }]);
  tms.setCurrentSession(10);
  tms.startSubmitting();
  await tms.handleSendResponse({ run_id: 300, status: 'waiting_confirmation' });

  assert.equal(tms.getSnapshot().runState, 'waiting_confirmation',
    'should be in waiting_confirmation state');

  // 执行确认
  await tms.confirmCurrentRun();
  await wait(100);

  assert.ok(confirmCalled, 'confirmRun API should be called');
  const snap = tms.getSnapshot();
  assert.ok(['queued', 'running'].includes(snap.runState),
    'should transition to queued/running after confirm, got: ' + snap.runState);

  dom.window.close();
});

// ============ 3. 取消运行流程 ============

test('P1-6: cancel run transitions to cancelled and stops polling', async () => {
  let cancelCalled = false;

  const { dom, window } = createDomEnv({
    '/api/v1/agent/sessions': [],
    '/api/v1/agent/capabilities': [],
    '/api/v1/agent/provider': { agent_enabled: true, text_agent_enabled: true, api_key_configured: true },
    '/api/v1/agent/runs/400/cancel': req => {
      if (req.method === 'POST') { cancelCalled = true; return { status: 'cancelled' }; }
      return {};
    },
    '/api/v1/agent/runs/400/events': { events: [], status: 'running', next_after: 100 },
  });
  await wait(500);

  const tms = window.teachMateState;

  tms.setSessions([{ id: 10, title: 'Test', term_id: 1 }]);
  tms.setCurrentSession(10);
  tms.startSubmitting();
  await tms.handleSendResponse({ run_id: 400, status: 'queued' });

  assert.ok(['queued', 'running'].includes(tms.getSnapshot().runState),
    'should be in active state before cancel');

  await tms.cancelCurrentRun();
  await wait(100);

  assert.ok(cancelCalled, 'cancelRun API should be called');
  assert.equal(tms.getSnapshot().runState, 'cancelled',
    'should be in cancelled state after cancel');

  dom.window.close();
});

// ============ 4. 搜索防乱序 ============

test('P1-6: search out-of-order responses do not overwrite latest', async () => {
  // 模拟两次搜索 API 响应乱序：第一次 API 慢返回旧结果，第二次 API 快返回新结果
  // 防乱序逻辑应丢弃第一次的过期响应
  let callCount = 0;
  const delays = [400, 0]; // 第一次慢，第二次快
  const results = [
    [{ id: 1, title: '旧结果（应该被丢弃）', term_id: 1 }],
    [{ id: 2, title: '新结果（应该保留）', term_id: 1 }],
  ];

  const { dom, window } = createDomEnv({
    '/api/v1/agent/sessions': () => {
      const idx = callCount++;
      const delay = delays[idx] || 0;
      const result = results[idx] || [];
      return new Promise(resolve => {
        setTimeout(() => resolve(result), delay);
      });
    },
    '/api/v1/agent/capabilities': [],
    '/api/v1/agent/provider': { agent_enabled: true, text_agent_enabled: true, api_key_configured: true },
  });
  await wait(500);

  const tms = window.teachMateState;

  // 第一次搜索（debounce 250ms 后发 API，API 400ms 后返回）
  window.tmSearchSessions('测试');
  // 等 debounce + 一点时间让第一次 API 发出
  await wait(300);
  // 第二次搜索（debounce 250ms 后发 API，API 立即返回）
  window.tmSearchSessions('新');

  // 等待所有搜索完成（第一次 API 400ms + 第二次 API 0ms + debounce 250ms + 余量）
  await wait(800);

  const snap = tms.getSnapshot();
  assert.ok(snap.sessions.length > 0, 'sessions should not be empty');
  assert.equal(snap.sessions[0].title, '新结果（应该保留）',
    'latest search result should win, got: ' + snap.sessions[0].title);

  dom.window.close();
});

// ============ 5. 源码契约 ============

test('P1-6 contract: tmSendMessage exists and is async', () => {
  const src = fs.readFileSync(path.join(root, 'workbench-assets', 'teachmate-interactions.js'), 'utf8');
  assert.match(src, /async function tmSendMessage/);
  assert.match(src, /async function tmCancelRun/);
  assert.match(src, /async function tmConfirmBudget/);
  assert.match(src, /function tmSearchSessions/);
});

test('P1-6 contract: state machine has all required states', () => {
  const src = fs.readFileSync(path.join(root, 'workbench-assets', 'teachmate-state.js'), 'utf8');
  // 状态机必须覆盖所有关键状态
  assert.match(src, /'submitting'/);
  assert.match(src, /'queued'/);
  assert.match(src, /'running'/);
  assert.match(src, /'waiting_confirmation'/);
  assert.match(src, /'completed'/);
  assert.match(src, /'degraded'/);
  assert.match(src, /'failed'/);
  assert.match(src, /'cancelled'/);
  // 取消和确认方法
  assert.match(src, /async function cancelCurrentRun/);
  assert.match(src, /async function confirmCurrentRun/);
});
