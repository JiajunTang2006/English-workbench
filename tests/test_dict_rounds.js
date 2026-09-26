// 测试：默写轮次卡片、单轮成绩表与班级切换。
const { JSDOM } = require('jsdom');

const { loadWorkbenchHtml } = require('./helpers/workbench_html');
const { createWorkbenchFixture } = require('./fixtures/workbench_fixture');
const html = loadWorkbenchHtml();
const seed = createWorkbenchFixture();

const errors = [];
const dom = new JSDOM(html, {
  runScripts: 'dangerously',
  url: 'https://localhost/',
  beforeParse(window) {
    window.localStorage.setItem('hye_db_v1', JSON.stringify(seed));
  },
});
const window = dom.window;
const doc = window.document;
window.onerror = (msg, url, line) => errors.push({ msg, line });

setTimeout(() => {
  const results = [];
  const check = (name, value) => results.push({ name, pass: Boolean(value) });
  [...doc.querySelectorAll('.nav-item')].find(item => item.dataset.key === 'dictation').click();

  let cards = [...doc.querySelectorAll('[data-act="dict-round-select"]')];
  let rows = [...doc.querySelectorAll('#workarea tbody tr')];
  check('默认显示两个默写轮次卡片', cards.length === 2);
  check('默认显示全部班级92人', rows.length === 92);
  check('首轮首行分数与JSON一致', Number(rows[0].children[2].textContent.trim()) === seed.dictation['20261101'][0]);

  cards[1].click();
  rows = [...doc.querySelectorAll('#workarea tbody tr')];
  check('点击卡片切换到第二轮', doc.querySelector('.writing-task-card.active')?.dataset.r === '1');
  check('第二轮首行分数与JSON一致', Number(rows[0].children[2].textContent.trim()) === seed.dictation['20261101'][1]);

  doc.querySelector('[data-act="dict-add-round"]').click();
  cards = [...doc.querySelectorAll('[data-act="dict-round-select"]')];
  check('添加后出现第三轮', cards.length === 3);
  cards[2].click();
  const title = doc.querySelector('[data-act="dict-name"][data-r="2"]');
  title.textContent = '第一单元';
  title.dispatchEvent(new window.Event('blur', { bubbles: true }));
  check('轮次标题可改名', doc.querySelector('[data-act="dict-name"][data-r="2"]')?.textContent.trim() === '第一单元');

  doc.querySelector('[data-act="dict-delete-round"]').click();
  check('删除当前轮次前显示确认弹窗', doc.getElementById('m-dict-round-delete')?.value === '2');
  doc.querySelector('[data-act="dict-round-delete-confirm"]').click();
  check('确认后恢复两个轮次', doc.querySelectorAll('[data-act="dict-round-select"]').length === 2);

  const classSelect = doc.querySelector('[data-act="dict-class-select"]');
  classSelect.value = '712';
  classSelect.dispatchEvent(new window.Event('change', { bubbles: true }));
  rows = [...doc.querySelectorAll('#workarea tbody tr')];
  check('班级下拉切换到712班', rows.length === 46 && rows.every(row => row.children[0].textContent.trim().startsWith('202612')));

  const scoreCell = doc.querySelector('[data-act="dict-edit"][data-sid="20261201"]');
  scoreCell.textContent = '88';
  scoreCell.dispatchEvent(new window.Event('blur', { bubbles: true }));
  check('默写成绩失焦后保存', doc.querySelector('[data-act="dict-edit"][data-sid="20261201"]')?.textContent.trim() === '88');
  check('全程无脚本错误', errors.length === 0);

  const passed = results.every(item => item.pass);
  console.log(JSON.stringify(results, null, 2));
  console.log('Script errors:', errors.length ? errors : 'none');
  console.log('OVERALL:', passed ? 'PASS' : 'FAIL');
  dom.window.close();
  process.exit(passed ? 0 : 1);
}, 700);
