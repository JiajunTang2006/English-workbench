const { JSDOM } = require('jsdom');

const { loadWorkbenchHtml } = require('./helpers/workbench_html');
const { createWorkbenchFixture } = require('./fixtures/workbench_fixture');
const html = loadWorkbenchHtml();
const seed = createWorkbenchFixture();
seed.dictation['20261101'][0] = '';
seed.recitations = [{
  id: 'stats-recitation',
  title: '统计用背诵任务',
  scope: 'Unit 1',
  status: {
    '20261101': 'A',
    '20261102': 'B',
    '20261103': 'C',
    '20261104': 'F'
  }
}];

const errors = [];
const dom = new JSDOM(html, {
  runScripts: 'dangerously',
  url: 'https://localhost/',
  beforeParse(window) {
    window.localStorage.setItem('hye_db_v1', JSON.stringify(seed));
    window.HTMLElement.prototype.scrollIntoView = function () {};
  }
});
const window = dom.window;
const doc = window.document;
window.onerror = (msg, url, line) => errors.push({ msg, line });

setTimeout(() => {
  const results = [];
  const check = (name, value) => results.push({ name, pass: Boolean(value) });
  const goto = key => [...doc.querySelectorAll('.nav-item')].find(item => item.dataset.key === key).click();

  goto('dictation');
  let wa = doc.getElementById('workarea');
  const statsCard = wa.querySelector('[data-act="dict-round-stats"][data-r="0"]');
  check('默写轮次卡片可点击设置统计', !!statsCard);
  statsCard.click();
  check('分数段弹窗显示10行配置', doc.querySelectorAll('#modalBody [id^="m-dict-range-label-"]').length === 10);

  doc.getElementById('m-dict-range-label-0').value = '不及格';
  doc.getElementById('m-dict-range-min-0').value = '0';
  doc.getElementById('m-dict-range-max-0').value = '59.5';
  doc.getElementById('m-dict-range-label-1').value = '及格';
  doc.getElementById('m-dict-range-min-1').value = '60';
  doc.getElementById('m-dict-range-max-1').value = '100';
  doc.getElementById('m-dict-range-save').click();
  const saved = JSON.parse(window.localStorage.getItem('hye_db_v1'));
  check('默写分数段保存到数据库', saved.dictationRanges?.[0]?.length === 2 && saved.dictationRanges[0][1].min === 60);
  wa = doc.getElementById('workarea');
  check('默写统计不把空白成绩算作0分', wa.textContent.includes('已录91人'));
  check('默写卡片显示满分统计', wa.textContent.includes('满分'));
  check('默写卡片显示分数段统计', wa.textContent.includes('不及格') && wa.textContent.includes('及格'));

  goto('recite');
  wa = doc.getElementById('workarea');
  const reciteSummary = wa.querySelector('.recite-stat-summary');
  check('背诵任务显示ABCF统计', reciteSummary && ['A 1人', 'B 1人', 'C 1人', 'F 1人'].every(text => reciteSummary.textContent.includes(text)));

  const allPass = results.every(item => item.pass) && errors.length === 0;
  console.log(JSON.stringify(results, null, 2));
  console.log('Script errors:', errors.length ? errors : 'none');
  console.log('OVERALL:', allPass ? 'PASS' : 'FAIL');
  dom.window.close();
  process.exit(allPass ? 0 : 1);
}, 700);
