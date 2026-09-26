// 全模块冒烟测试：逐个打开 12 个模块 + 关键交互，全程监控脚本错误
// 注意：每次点击前重新查询导航项（renderNav 会重绘导航，旧节点点击无效）
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
const goto = k => [...doc.querySelectorAll('.nav-item')].find(n => n.dataset.key === k).click();

setTimeout(() => {
  const results = [];
  const check = (n, c) => results.push({ name: n, pass: !!c });

  // 1) 逐个打开所有导航模块，按特征内容断言
  const modules = [
    ['dash', '最近考试概览'],
    ['stu', '新增学生'],
    ['score', 'score-class'],
    ['dictation', 'dict-class-select'],
    ['recite', 'recite-add'],
    ['writing', 'writing-add'],
    ['errors', 'error-add'],
    ['todo', 'todo-add'],
    ['settings', 'settings-save']
  ];
  for (const [k, marker] of modules) {
    goto(k);
    const wa = doc.getElementById('workarea');
    check(`模块 ${k} 打开且有特征内容`, !!wa && wa.innerHTML.includes(marker));
  }

  // 2) 成绩管理：711/712 子模块 + 排序
  goto('score');
  const s711 = [...doc.querySelectorAll('[data-act="score-class"]')].find(s => s.dataset.cls === '711');
  check('成绩管理有711子标签', !!s711);
  s711.click();
  let rows = [...doc.querySelectorAll('#workarea tbody tr')];
  check('成绩管理 711班 显示46人', rows.length === 46 && rows.every(r => r.children[2].textContent.trim() === '711班'));
  const sDesc = [...doc.querySelectorAll('[data-act="score-sort"]')].find(b => b.dataset.sort === 'scoreDesc');
  sDesc.click();
  rows = [...doc.querySelectorAll('#workarea tbody tr')];
  const vals = rows.map(r => parseFloat(r.children[3].textContent.trim()));
  check('成绩高→低排序有效', vals.every((v,i) => i === 0 || Number.isNaN(v) || Number.isNaN(vals[i-1]) || vals[i-1] >= v));

  // 3) 默写成绩：班级下拉
  goto('dictation');
  const sel = doc.querySelector('select[data-act="dict-class-select"]');
  check('默写成绩有班级下拉', !!sel && [...sel.options].some(option => option.value === '712'));
  sel.value = '712';
  sel.dispatchEvent(new window.Event('change', { bubbles: true }));

  // 4) 背诵成绩 / 写作成绩
  goto('recite');
  check('背诵成绩含任务列表', doc.getElementById('workarea').innerHTML.includes('背诵任务'));
  goto('writing');
  check('写作成绩含任务列表和表格', doc.getElementById('workarea').innerHTML.includes('写作任务') && doc.getElementById('workarea').innerHTML.includes('写作成绩表'));

  // 5) 仪表盘排行榜与重点关注学生
  goto('dash');
  check('仪表盘不含默写成绩卡片', !doc.getElementById('workarea').innerHTML.includes('默写成绩（按学号排列）'));
  check('仪表盘含考试前十排行榜且不再独立切换考试', doc.body.innerHTML.includes('本次考试前十') && !doc.querySelector('[data-act="dashboard-leaderboard-exam"]'));
  check('仪表盘含C/D重点关注学生数据云', doc.body.innerHTML.includes('重点关注学生') && doc.querySelector('.risk-cloud-card.c-level') && doc.querySelector('.risk-cloud-card.d-level'));
  check('排行榜不显示 undefined 占位文本', !doc.querySelector('.dashboard-leaderboard')?.textContent.includes('undefined'));

  check('全程无脚本错误', errors.length === 0);
  if (errors.length) console.log('Script errors:', JSON.stringify(errors));

  const allPass = results.every(r => r.pass);
  console.log(JSON.stringify(results, null, 2));
  console.log('OVERALL:', allPass ? 'PASS' : 'FAIL');
  dom.window.close();
  process.exit(allPass ? 0 : 1);
}, 700);
