const { JSDOM } = require('jsdom');
const { loadWorkbenchHtml } = require('./helpers/workbench_html');

const html = loadWorkbenchHtml();
const originalState = {
  schema: 4,
  teacher: { name: '教师', subject: '初中英语' },
  classes: [], students: [], todos: [],
  exams: [{ id: 'exam-local-1', name: '月考', scores: {}, fullScore: 100 }],
  archivedExams: [], currentExamId: 'exam-local-1',
};
const archivedState = {
  ...originalState,
  exams: [],
  archivedExams: [{ ...originalState.exams[0], status: 'archived' }],
  currentExamId: '',
};
const requests = [];
const response = (payload, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => payload });
const dom = new JSDOM(html, {
  runScripts: 'dangerously',
  url: 'http://127.0.0.1:8765/workbench?token=test-token',
  beforeParse(window) {
    window.fetch = async (url, options = {}) => {
      const target = String(url);
      requests.push({ target, method: options.method || 'GET', body: options.body ? JSON.parse(options.body) : null });
      if (target === '/api/v1/terms') return response([{ id: 7, code: '2026-S1', name: '测试学期', status: 'active' }]);
      if (target === '/api/v1/terms/current') return response({ id: 7, code: '2026-S1', name: '测试学期', status: 'active' });
      if (target === '/api/v1/runtime') return response({ version: 'test' });
      if (target === '/api/v1/terms/7/workspace-state') return response({ state: originalState, revision: 4, updated_at: null });
      if (target === '/api/v1/attachments?term_id=7') return response([]);
      if (target === '/api/v1/exams?term_id=7&include_archived=true') return response([{ id: 31, source_key: 'exam-local-1', name: '月考', status: 'active' }]);
      if (target === '/api/v1/exams/31/archive?term_id=7' && options.method === 'POST') {
        return response({ exam: { id: 31, source_key: 'exam-local-1', status: 'archived' }, state: archivedState, revision: 5 });
      }
      return response({ detail: `unexpected ${target}` }, 404);
    };
  },
});

setTimeout(async () => {
  const result = await dom.window.eval('deleteCurrentExam()');
  const mutationRequest = requests.find(item => item.target.includes('/api/v1/exams/31/archive'));
  const workspacePuts = requests.filter(item => item.target.includes('/workspace-state') && item.method === 'PUT');
  const current = dom.window.eval('({ active: state.exams.length, archived: state.archivedExams.length, currentExamId, revision: databaseRevision })');
  const checks = [
    { name: '数据库普通删除调用考试归档接口', pass: result && Boolean(mutationRequest) },
    { name: '归档请求携带 term_id、source_key 和 expected_revision', pass: mutationRequest?.target.endsWith('term_id=7') && mutationRequest.body?.source_key === 'exam-local-1' && mutationRequest.body?.expected_revision === 4 },
    { name: '组合接口成功后采用响应 state 和 revision', pass: current.active === 0 && current.archived === 1 && current.currentExamId === '' && current.revision === 5 },
    { name: '组合接口成功后不重复 PUT workspace-state', pass: workspacePuts.length === 0 },
  ];
  console.log(JSON.stringify(checks, null, 2));
  const passed = checks.every(item => item.pass);
  console.log('OVERALL:', passed ? 'PASS' : 'FAIL');
  dom.window.close();
  process.exit(passed ? 0 : 1);
}, 700);
