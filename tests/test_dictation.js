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

  // 仪表盘不再内嵌默写成绩，改为独立模块
  let wa = doc.getElementById('workarea');
  check('仪表盘不再内嵌默写成绩表', !wa.innerHTML.includes('默写成绩（按学号排列）'));
  check('仪表盘提供考试前十排行榜', wa.innerHTML.includes('本次考试前十'));

  // 背诵成绩 - 班级名单
  const reciteNav = [...doc.querySelectorAll('.nav-item')].find(n => n.dataset.key === 'recite');
  reciteNav.click();
  wa = doc.getElementById('workarea');
  check('背诵成绩含任务列表', wa.innerHTML.includes('背诵任务'));
  check('背诵成绩列出两班', wa.innerHTML.includes('711班') && wa.innerHTML.includes('712班'));

  // 写作成绩 - 空任务卡片与表格录入
  const writeNav = [...doc.querySelectorAll('.nav-item')].find(n => n.dataset.key === 'writing');
  writeNav.click();
  wa = doc.getElementById('workarea');
  check('写作成绩含任务列表', wa.innerHTML.includes('写作任务'));
  check('写作成绩使用表格模式', wa.innerHTML.includes('写作成绩表'));

  const allPass = results.every(r => r.pass) && errors.length === 0;
  console.log(JSON.stringify(results, null, 2));
  console.log('Script errors:', errors.length ? errors : 'none');
  console.log('OVERALL:', allPass ? 'PASS' : 'FAIL');
  try { window.localStorage.getItem('hye_db_v1'); } catch(e){}
  dom.window.close();
  process.exit(allPass ? 0 : 1);
}, 600);
