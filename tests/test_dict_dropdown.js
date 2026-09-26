const { JSDOM } = require('jsdom');
const { loadWorkbenchHtml } = require('./helpers/workbench_html');
const { createWorkbenchFixture } = require('./fixtures/workbench_fixture');
const html = loadWorkbenchHtml();

const errors = [];
const fixture = createWorkbenchFixture();
const dom = new JSDOM(html, { runScripts: 'dangerously', url: 'https://localhost/', beforeParse(window) { window.localStorage.setItem('hye_db_v1', JSON.stringify(fixture)); } });
const window = dom.window;
const doc = window.document;
window.onerror = (msg, url, line) => { errors.push({ msg, line }); };
// jsdom 不实现 scrollIntoView，打桩避免报错
window.HTMLElement.prototype.scrollIntoView = function () {};

setTimeout(() => {
  const results = [];
  function check(name, cond) { results.push({ name, pass: !!cond }); }

  // 进入默写成绩模块（全局上下文默认全部班级）
  const navItems = [...doc.querySelectorAll('.nav-item')];
  navItems.find(n => n.dataset.key === 'dictation').click();
  let wa = doc.getElementById('workarea');

  const sel = wa.querySelector('select[data-act="dict-class-select"]');
  check('默写模块含班级下拉菜单', !!sel);
  const values = [...sel.options].map(option => option.value);
  check('下拉含全部、711班和712班', values.includes('') && values.includes('711') && values.includes('712'));
  check('默认显示全部班级', sel.value === '');
  sel.value = '712';
  sel.dispatchEvent(new window.Event('change', { bubbles: true }));

  setTimeout(() => {
    const sel2 = doc.querySelector('select[data-act="dict-class-select"]');
    const rows = [...doc.querySelectorAll('#workarea tbody tr')];
    check('选择后切到712班', rows.length === 46 && rows.every(r=>r.children[0].textContent.trim().startsWith('202612')));
    check('重绘后保留712班', sel2.value === '712');
    check('当前班级学生成绩可编辑', !!doc.querySelector('td[data-act="dict-edit"][data-sid="20261201"]'));

    const allPass = results.every(r => r.pass) && errors.length === 0;
    console.log(JSON.stringify(results, null, 2));
    console.log('Script errors:', errors.length ? errors : 'none');
    console.log('OVERALL:', allPass ? 'PASS' : 'FAIL');
    dom.window.close();
    process.exit(allPass ? 0 : 1);
  }, 200);
}, 600);
