const { JSDOM } = require('jsdom');
const { loadWorkbenchHtml } = require('./helpers/workbench_html');

const html = loadWorkbenchHtml();
const initialState = {
  schema: 4,
  teacher: { name: '教师', subject: '初中英语' },
  classes: [],
  students: [],
  exams: [{ id: 'exam-local-1', name: '月考', scores: {}, fullScore: 100 }],
  archivedExams: [],
  currentExamId: 'exam-local-1',
  todos: [],
};
const dom = new JSDOM(html, {
  runScripts: 'dangerously',
  url: 'https://localhost/',
  beforeParse(window) {
    window.localStorage.setItem('hye_db_v1', JSON.stringify(initialState));
  },
});

setTimeout(() => {
  const originalSetItem = dom.window.localStorage.setItem.bind(dom.window.localStorage);
  dom.window.localStorage.setItem = () => { throw new Error('存储空间不足'); };
  // jsdom 的 Storage 方法位于原型上，需要覆盖原型才能模拟浏览器写入失败。
  const storagePrototype = Object.getPrototypeOf(dom.window.localStorage);
  const originalPrototypeSetItem = storagePrototype.setItem;
  storagePrototype.setItem = () => { throw new Error('存储空间不足'); };

  const result = dom.window.eval(`commitMutation(() => {
    state.todos.push({ id: 'local-failure', title: '不应保留', priority: '中', date: '2026-08-10', done: false });
    currentExamId = 'exam-local-2';
    state.currentExamId = currentExamId;
  }, { successMessage: '不应成功' })`);
  const current = dom.window.eval('({ todoCount: state.todos.length, currentExamId, stateCurrentExamId: state.currentExamId })');
  const saved = JSON.parse(originalSetItem === undefined ? '{}' : dom.window.localStorage.getItem('hye_db_v1'));
  const checks = [
    { name: 'localStorage.setItem 失败返回失败结果', pass: result.ok === false },
    { name: '本地存储失败回滚 state 和 currentExamId', pass: current.todoCount === 0 && current.currentExamId === 'exam-local-1' && current.stateCurrentExamId === 'exam-local-1' },
    { name: '本地存储失败未覆盖原持久化数据', pass: saved.todos.length === 0 && saved.currentExamId === 'exam-local-1' },
    { name: '本地存储失败不显示成功提示', pass: !dom.window.document.body.textContent.includes('不应成功') },
  ];
  storagePrototype.setItem = originalPrototypeSetItem;
  console.log(JSON.stringify(checks, null, 2));
  const passed = checks.every(item => item.pass);
  console.log('OVERALL:', passed ? 'PASS' : 'FAIL');
  dom.window.close();
  process.exit(passed ? 0 : 1);
}, 600);
