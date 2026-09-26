const { JSDOM } = require('jsdom');

const { loadWorkbenchHtml } = require('./helpers/workbench_html');
const { createWorkbenchFixture } = require('./fixtures/workbench_fixture');
const html = loadWorkbenchHtml();
const fixture = createWorkbenchFixture();
fixture.recitations = [{
  id: 'legacy-recitation',
  title: '旧版背诵任务',
  scope: 'Unit 1',
  status: { '20261101': '已过', '20261102': '未过' }
}];
const errors = [];
const dom = new JSDOM(html, {
  runScripts: 'dangerously',
  url: 'https://localhost/',
  beforeParse(window) { window.localStorage.setItem('hye_db_v1', JSON.stringify(fixture)); }
});
const window = dom.window;
const doc = window.document;
window.onerror = (msg, url, line) => errors.push({ msg, line });

setTimeout(() => {
  const results = [];
  const check = (name, value) => results.push({ name, pass: Boolean(value) });
  const goto = key => [...doc.querySelectorAll('.nav-item')].find(item => item.dataset.key === key).click();
  goto('recite');
  let wa = doc.getElementById('workarea');
  check('旧版已过迁移为A档', wa.querySelector('[data-act="recite-edit-status"][data-sid="20261101"]')?.value === 'A');
  check('旧版未过迁移为F档', wa.querySelector('[data-act="recite-edit-status"][data-sid="20261102"]')?.value === 'F');

  wa.querySelector('[data-act="recite-add"]').click();
  check('新建背诵任务提供ABCF四档', ['A', 'B', 'C', 'F'].every(level => [...doc.querySelector('.m-rec-level').options].some(option => option.value === level)));
  check('不再提供旧中文状态', !doc.querySelector('.m-rec-level').innerHTML.includes('未过') && !doc.querySelector('.m-rec-level').innerHTML.includes('已过'));
  const firstLevel = doc.querySelector('.m-rec-level');
  const firstRetake = doc.querySelector('.m-rec-retake');
  check('F档重背结果默认隐藏', firstRetake.hidden);
  firstLevel.value = 'F';
  firstLevel.dispatchEvent(new window.Event('change', { bubbles: true }));
  check('选择F后显示重背结果', !firstRetake.hidden && doc.querySelector('.m-rec-retake-placeholder').hidden);
  firstRetake.value = 'passed';
  doc.getElementById('m-rec-title').value = '第二单元背诵';
  doc.getElementById('m-rec-scope').value = 'Unit 2';
  doc.getElementById('m-rec-save').click();
  const saved = JSON.parse(window.localStorage.getItem('hye_db_v1'));
  const created = saved.recitations.find(item => item.title === '第二单元背诵');
  check('F档保存重背通过结果', created?.status?.['20261101']?.level === 'F' && created.status['20261101'].retake === 'passed');
  wa = doc.getElementById('workarea');
  check('页面显示F档重背通过', wa.querySelector('[data-act="recite-edit-status"][data-sid="20261101"]')?.value === 'F' && wa.querySelector('[data-act="recite-edit-retake"][data-sid="20261101"]')?.value === 'passed');

  const allPass = results.every(item => item.pass) && errors.length === 0;
  console.log(JSON.stringify(results, null, 2));
  console.log('Script errors:', errors.length ? errors : 'none');
  console.log('OVERALL:', allPass ? 'PASS' : 'FAIL');
  dom.window.close();
  process.exit(allPass ? 0 : 1);
}, 700);
