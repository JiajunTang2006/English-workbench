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

setTimeout(() => {
  const results = [];
  function check(name, cond) { results.push({ name, pass: !!cond }); }

  // 默写成绩模块在导航中，且位于 成绩管理 与 背诵成绩 之间
  const navItems = [...doc.querySelectorAll('.nav-item')];
  const keys = navItems.map(n => n.dataset.key);
  check('导航含默写成绩模块', keys.includes('dictation'));
  const iScore = keys.indexOf('score'), iDict = keys.indexOf('dictation'), iRecite = keys.indexOf('recite');
  check('位置：成绩管理<默写成绩<背诵成绩', iScore < iDict && iDict < iRecite);

  // 点击默写成绩模块
  navItems.find(n => n.dataset.key === 'dictation').click();
  let wa = doc.getElementById('workarea');
  check('默写模块含711/712子模块', wa.innerHTML.includes('711班') && wa.innerHTML.includes('712班'));
  let rows = [...doc.querySelectorAll('#workarea tbody tr')];
  check('默认显示全部班级(92人)', rows.length === 92);
  check('按学号首行20261101', rows[0].children[0].textContent.trim() === '20261101');
  check('无班级列', !wa.querySelector('thead').textContent.includes('班级'));

  // 切到712班子模块
  const t712 = doc.querySelector('[data-act="dict-class-select"]');
  t712.value = '712';
  t712.dispatchEvent(new window.Event('change', { bubbles: true }));
  rows = [...doc.querySelectorAll('#workarea tbody tr')];
  check('切换712班(46人)', rows.length === 46 && rows.every(r=>r.children[0].textContent.trim().startsWith('202612')));
  const t712new = doc.querySelector('[data-act="dict-class-select"]');
  check('712班选项保持选中', t712new.value === '712');

  // 默写成绩可编辑
  const cell = doc.querySelector('[data-act="dict-edit"]');
  cell.textContent = '95';
  cell.dispatchEvent(new window.Event('blur'));
  // 重新渲染后再次查询该生单元格
  const cell2 = doc.querySelector('[data-act="dict-edit"]');
  check('默写成绩可编辑保存', cell2 && String(cell2.textContent.trim()) === '95');

  const allPass = results.every(r => r.pass) && errors.length === 0;
  console.log(JSON.stringify(results, null, 2));
  console.log('Script errors:', errors.length ? errors : 'none');
  console.log('OVERALL:', allPass ? 'PASS' : 'FAIL');
  try { window.localStorage.getItem('hye_db_v1'); } catch(e){}
  dom.window.close();
  process.exit(allPass ? 0 : 1);
}, 600);
