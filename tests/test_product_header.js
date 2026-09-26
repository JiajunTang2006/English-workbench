const { JSDOM } = require('jsdom');
const { loadWorkbenchHtml } = require('./helpers/workbench_html');

const fixture = {
  schema: 4,
  teacher: { name: '周老师', subject: '初中英语' },
  classes: ['701', '702'],
  students: [
    { id: '01', name: '甲同学', class: '701' },
    { id: '02', name: '乙同学', class: '702' },
  ],
  exams: [],
  todos: [],
};

const dom = new JSDOM(loadWorkbenchHtml(), {
  runScripts: 'dangerously',
  url: 'https://localhost/',
  beforeParse(window) {
    window.localStorage.setItem('hye_db_v1', JSON.stringify(fixture));
    window.HTMLElement.prototype.scrollIntoView = function () {};
  },
});

setTimeout(() => {
  const { document } = dom.window;
  const results = [];
  const check = (name, value) => results.push({ name, pass: Boolean(value) });
  const goto = key => [...document.querySelectorAll('.nav-item')].find(node => node.dataset.key === key).click();
  const headerClass = document.getElementById('classSelect');

  check('品牌标题随教师与学科设置显示', document.getElementById('appTitle').textContent === '周老师 · 初中英语工作台');
  check('使用者信息独立显示', document.getElementById('userNameLabel').textContent === '周老师');
  check('顶部班级选择器可见且完整', !headerClass.hidden && headerClass.options.length === 3);

  headerClass.value = '701';
  headerClass.dispatchEvent(new dom.window.Event('change', { bubbles: true }));
  goto('stu');
  check('顶部不再显示当前页面标签', !document.getElementById('currentPageLabel'));
  check('全局班级筛选进入学生页面', document.getElementById('workarea').textContent.includes('甲同学') && !document.getElementById('workarea').textContent.includes('乙同学'));
  check('切换页面后班级上下文保持', document.getElementById('classSelect').value === '701');

  goto('recite');
  const localClass = document.querySelector('[data-act="recite-class"]');
  localClass.value = '702';
  localClass.dispatchEvent(new dom.window.Event('change', { bubbles: true }));
  check('页面内筛选同步回顶部', document.getElementById('classSelect').value === '702');
  goto('stu');
  check('同步后的班级作用于其他页面', document.getElementById('workarea').textContent.includes('乙同学') && !document.getElementById('workarea').textContent.includes('甲同学'));
  check('主界面不再常驻版本号', !document.getElementById('versionLabel'));

  const passed = results.every(item => item.pass);
  console.log(JSON.stringify(results, null, 2));
  console.log('OVERALL:', passed ? 'PASS' : 'FAIL');
  dom.window.close();
  process.exit(passed ? 0 : 1);
}, 650);
