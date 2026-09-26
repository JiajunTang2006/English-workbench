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
  function rowsArr() { return [...doc.querySelectorAll('#scorePanel tbody tr')]; }

  // 导航到成绩管理
  const scoreNav = [...doc.querySelectorAll('.nav-item')].find(n => n.dataset.key === 'score');
  scoreNav.click();
  check('成绩管理已渲染', doc.getElementById('workarea').innerHTML.includes('score-sort'));
  const headers = [...doc.querySelectorAll('#scorePanel thead th')].map(node => node.textContent.trim());
  check('成绩表同时显示班级排名与年级排名', headers.includes('班级排名') && headers.includes('年级排名'));

  // 子模块标签存在
  const subtabs = doc.querySelectorAll('[data-act="score-class"]');
  check('班级子模块(全部/711/712)存在', subtabs.length === 3);

  // 点击 711 班子模块
  const t711 = [...subtabs].find(s => s.dataset.cls === '711');
  t711.click();
  let rows = rowsArr();
  let classes = rows.map(r => r.children[2].textContent.trim());
  check('点击711班→仅显示711学生(46)', classes.every(c => c === '711班') && classes.length === 46);
  const t711new = [...doc.querySelectorAll('[data-act="score-class"]')].find(s => s.dataset.cls === '711');
  check('711子模块高亮', t711new.classList.contains('active'));

  // 排序：成绩高→低
  const descBtn = [...doc.querySelectorAll('[data-act="score-sort"]')].find(b => b.dataset.sort === 'scoreDesc');
  descBtn.click();
  rows = rowsArr();
  let scores = rows.map(r => parseFloat(r.children[3].textContent.trim()));
  check('711班成绩高→低', scores.every((v,i) => i===0 || Number.isNaN(v) || Number.isNaN(scores[i-1]) || scores[i-1] >= v));

  // 删除性别排序断言：名单界面已不再展示性别
  // 点击 712 班子模块
  const t712 = [...doc.querySelectorAll('[data-act="score-class"]')].find(s => s.dataset.cls === '712');
  t712.click();
  rows = rowsArr();
  classes = rows.map(r => r.children[2].textContent.trim());
  check('点击712班→仅显示712学生(46)', classes.every(c => c === '712班') && classes.length === 46);

  // 点击 全部汇总
  const tAll = [...doc.querySelectorAll('[data-act="score-class"]')].find(s => s.dataset.cls === '');
  tAll.click();
  rows = rowsArr();
  check('全部汇总→显示92人', rows.length === 92);

  const allPass = results.every(r => r.pass) && errors.length === 0;
  console.log(JSON.stringify(results, null, 2));
  console.log('Script errors:', errors.length ? errors : 'none');
  console.log('OVERALL:', allPass ? 'PASS' : 'FAIL');
  try { window.localStorage.getItem('hye_db_v1'); } catch(e){}
  dom.window.close();
  process.exit(allPass ? 0 : 1);
}, 600);
