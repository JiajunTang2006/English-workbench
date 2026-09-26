const { JSDOM } = require('jsdom');
const { loadWorkbenchHtml } = require('./helpers/workbench_html');

const html = loadWorkbenchHtml();
const terms = [
  { id: 1, code: '2026-S1', name: '2026年第一学期', starts_on: null, ends_on: null, status: 'active' },
  { id: 2, code: '2026-S2', name: '2026年第二学期', starts_on: null, ends_on: null, status: 'active' },
];
const states = {
  1: { schema: 4, teacher: { name: '教师', subject: '初中英语' }, classes: ['711'], students: [{ id: '01', name: '张三', class: '711' }], exams: [], todos: [] },
  2: { schema: 4, teacher: { name: '教师', subject: '初中英语' }, classes: ['712'], students: [{ id: '02', name: '李四', class: '712' }], exams: [], todos: [] },
};
let activeTermId = 1;
const response = (payload, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => payload });

const errors = [];
const dom = new JSDOM(html, {
  runScripts: 'dangerously',
  url: 'http://127.0.0.1:8765/workbench?token=test-token',
  beforeParse(window) {
    window.fetch = async (url, options = {}) => {
      const target = String(url);
      if (target === '/api/v1/terms' && (!options.method || options.method === 'GET')) return response(terms);
      if (target === '/api/v1/terms/current' && options.method === 'PUT') {
        activeTermId = Number(JSON.parse(options.body).term_id);
        return response(terms.find(term => term.id === activeTermId));
      }
      if (target === '/api/v1/terms/current') return response(terms.find(term => term.id === activeTermId));
      if (target === '/api/v1/runtime') return response({ version: '0.7.0' });
      const match = target.match(/^\/api\/v1\/terms\/(\d+)\/workspace-state$/);
      if (match) return response({ state: states[Number(match[1])], revision: 1, updated_at: null });
      return response({ detail: `unexpected ${target}` }, 404);
    };
  },
});
const window = dom.window;
const doc = window.document;
window.onerror = (message, url, line) => errors.push({ message, line });

setTimeout(() => {
  const results = [];
  const check = (name, value) => results.push({ name, pass: Boolean(value) });
  const selector = doc.getElementById('termSelect');
  check('数据库模式显示学期选择器', !doc.getElementById('termSwitcher').hidden);
  check('学期选择器列出两个学期', selector.options.length === 2);
  check('默认选中当前学期', selector.value === '1');
  selector.value = '2';
  selector.dispatchEvent(new window.Event('change', { bubbles: true }));

  setTimeout(() => {
    check('切换请求更新当前学期', activeTermId === 2);
    check('切换后选择器保持第二学期', doc.getElementById('termSelect').value === '2');
    [...doc.querySelectorAll('.nav-item')].find(item => item.dataset.key === 'stu').click();
    check('切换后只显示第二学期学生', doc.getElementById('workarea').textContent.includes('李四') && !doc.getElementById('workarea').textContent.includes('张三'));
    check('新建学期入口可用', Boolean(doc.getElementById('termAddBtn')));
    check('全程无脚本错误', errors.length === 0);
    const passed = results.every(item => item.pass);
    console.log(JSON.stringify(results, null, 2));
    console.log('OVERALL:', passed ? 'PASS' : 'FAIL');
    dom.window.close();
    process.exit(passed ? 0 : 1);
  }, 350);
}, 700);
