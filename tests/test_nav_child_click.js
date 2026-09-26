// 回归测试：模拟"真实浏览器点击子元素"——点击导航项的图标 span / 文字 span，而不是外层 div
const { JSDOM } = require('jsdom');
const { loadWorkbenchHtml } = require('./helpers/workbench_html');
const { createWorkbenchFixture } = require('./fixtures/workbench_fixture');
const html = loadWorkbenchHtml();

const errors = [];
const fixture = createWorkbenchFixture();
const dom = new JSDOM(html, { runScripts: 'dangerously', url: 'https://localhost/', beforeParse(window) { window.localStorage.setItem('hye_db_v1', JSON.stringify(fixture)); } });
const window = dom.window, doc = window.document;
window.onerror = (msg, url, line) => errors.push({ msg, line });
window.HTMLElement.prototype.scrollIntoView = function () {};
const goto = k => {
  const item = [...doc.querySelectorAll('.nav-item')].find(n => n.dataset.key === k);
  const span = item.querySelector('span');   // 图标 span（子元素）
  span.click();                               // 模拟真实浏览器点到图标上
};

setTimeout(() => {
  const results = [];
  const check = (n, c) => results.push({ name: n, pass: !!c });

  // 依次点击每个导航项的"图标 span"，验证能切换
  const markers = {
    dash: '最近考试概览', stu: '新增学生', score: 'score-class', dictation: 'dict-class',
    recite: 'recite-add', writing: 'writing-add', errors: 'error-add',
    todo: 'todo-add', settings: 'settings-save'
  };
  for (const k of Object.keys(markers)) {
    goto(k);
    check(`点击图标可进入 ${k}`, doc.getElementById('workarea').innerHTML.includes(markers[k]));
  }

  // 再验证点击"文字 span"也能切换
  const item = [...doc.querySelectorAll('.nav-item')].find(n => n.dataset.key === 'score');
  item.querySelectorAll('span')[1].click();
  check('点击文字span也可进入成绩管理', doc.getElementById('workarea').innerHTML.includes('score-class'));

  // 子标签点击也兼容（subtab 内为纯文本，本就 OK；再验证一次）
  const sub = [...doc.querySelectorAll('[data-act="score-class"]')].find(s => s.dataset.cls === '712');
  sub.click();
  const rows = [...doc.querySelectorAll('#workarea tbody tr')];
  check('点击712子标签显示712班', rows.length === 46 && rows.every(r => r.children[2].textContent.trim() === '712班'));

  check('主导航使用真实按钮', [...doc.querySelectorAll('.nav-item')].every(item => item.tagName === 'BUTTON'));
  check('当前导航提供无障碍状态', doc.querySelector('.nav-item.active')?.getAttribute('aria-current') === 'page');

  check('全程无脚本错误', errors.length === 0);
  if (errors.length) console.log('Script errors:', JSON.stringify(errors));

  const allPass = results.every(r => r.pass);
  console.log(JSON.stringify(results, null, 2));
  console.log('OVERALL:', allPass ? 'PASS' : 'FAIL');
  dom.window.close();
  process.exit(allPass ? 0 : 1);
}, 700);
