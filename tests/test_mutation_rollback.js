const { JSDOM } = require('jsdom');
const { loadWorkbenchHtml } = require('./helpers/workbench_html');

const html = loadWorkbenchHtml();
const initialState = {
  schema: 4,
  teacher: { name: '教师', subject: '初中英语' },
  classes: ['711'],
  students: [],
  exams: [
    { id: 'exam-1', name: '考试一', scores: {}, fullScore: 100 },
    { id: 'exam-2', name: '考试二', scores: {}, fullScore: 100 },
  ],
  archivedExams: [],
  currentExamId: 'exam-1',
  todos: [],
};
const response = (payload, status = 200) => ({
  ok: status >= 200 && status < 300,
  status,
  json: async () => payload,
});

let putMode = 'success';
let putCount = 0;
let revision = 1;
const errors = [];
const dom = new JSDOM(html, {
  runScripts: 'dangerously',
  url: 'http://127.0.0.1:8765/workbench?token=test-token',
  beforeParse(window) {
    window.fetch = async (url, options = {}) => {
      const target = String(url);
      if (target === '/api/v1/terms') return response([{ id: 1, code: '2026-S1', name: '测试学期', status: 'active' }]);
      if (target === '/api/v1/terms/current') return response({ id: 1, code: '2026-S1', name: '测试学期', status: 'active' });
      if (target === '/api/v1/runtime') return response({ version: 'test' });
      if (target === '/api/v1/attachments?term_id=1') return response([]);
      if (target === '/api/v1/terms/1/workspace-state' && (!options.method || options.method === 'GET')) {
        return response({ state: initialState, revision, updated_at: null });
      }
      if (target === '/api/v1/terms/1/workspace-state' && options.method === 'PUT') {
        putCount++;
        if (putMode === '500') return response({ detail: '服务器保存失败' }, 500);
        if (putMode === '409') return response({ detail: '数据已被另一页面修改' }, 409);
        if (putMode === 'timeout') {
          const error = new window.DOMException('aborted', 'AbortError');
          throw error;
        }
        revision++;
        return response({ state: JSON.parse(options.body).state, revision, updated_at: null });
      }
      return response({ detail: `unexpected ${target}` }, 404);
    };
  },
});
const window = dom.window;
window.onerror = (message, url, line) => errors.push({ message, line });

const wait = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));
const snapshot = () => window.eval('({ todos: state.todos.map(item => item.title), currentExamId, stateCurrentExamId: state.currentExamId, blocked: databaseWriteBlocked })');
const mutate = title => window.eval(`commitMutation(() => {
  state.todos.push({ id: ${JSON.stringify(title)}, title: ${JSON.stringify(title)}, priority: '中', date: '2026-08-10', deadline: '2026-08-10', time: '', notes: '', done: false });
  currentExamId = 'exam-2';
  state.currentExamId = currentExamId;
}, { successMessage: '不应出现的成功提示', renderNavigation: true })`);

setTimeout(async () => {
  const checks = [];
  const check = (name, value) => checks.push({ name, pass: Boolean(value) });

  putMode = '500';
  const serverFailure = await mutate('server-failure');
  let current = snapshot();
  check('HTTP 500 返回失败结果', !serverFailure.ok && serverFailure.error?.status === 500);
  check('HTTP 500 回滚 state 和 currentExamId', current.todos.length === 0 && current.currentExamId === 'exam-1' && current.stateCurrentExamId === 'exam-1');
  check('HTTP 500 不显示成功提示', !window.document.body.textContent.includes('不应出现的成功提示'));

  putMode = 'success';
  const retry = await mutate('retry-success');
  current = snapshot();
  check('非 409 失败后保存队列仍可重试', retry.ok && current.todos.includes('retry-success'));

  putMode = 'timeout';
  const timeoutFailure = await mutate('timeout-failure');
  current = snapshot();
  check('请求超时转换为 REQUEST_TIMEOUT', !timeoutFailure.ok && timeoutFailure.error?.code === 'REQUEST_TIMEOUT');
  check('请求超时回滚 mutation', !current.todos.includes('timeout-failure') && current.currentExamId === 'exam-2');

  putMode = '409';
  const conflict = await mutate('conflict');
  current = snapshot();
  check('HTTP 409 回滚并阻塞后续写入', !conflict.ok && conflict.error?.status === 409 && current.blocked && !current.todos.includes('conflict'));
  const countAfterConflict = putCount;
  const blocked = await mutate('blocked');
  current = snapshot();
  check('409 后的新 mutation 在请求前被拒绝并回滚', !blocked.ok && putCount === countAfterConflict && !current.todos.includes('blocked'));
  check('失败后 DOM 与回滚状态一致', !window.document.getElementById('workarea').textContent.includes('conflict'));
  check('全程无脚本错误', errors.length === 0);

  console.log(JSON.stringify(checks, null, 2));
  const passed = checks.every(item => item.pass);
  console.log('OVERALL:', passed ? 'PASS' : 'FAIL');
  await wait(0);
  dom.window.close();
  process.exit(passed ? 0 : 1);
}, 700);
