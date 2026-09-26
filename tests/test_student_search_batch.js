const { JSDOM } = require('jsdom');
const { loadWorkbenchHtml } = require('./helpers/workbench_html');
const { createWorkbenchFixture } = require('./fixtures/workbench_fixture');
const html = loadWorkbenchHtml();
const fixture = createWorkbenchFixture();
const dom = new JSDOM(html, { runScripts: 'dangerously', url: 'https://localhost/', beforeParse(window) {
  window.localStorage.setItem('hye_db_v1', JSON.stringify(fixture));
} });
const { document: doc } = dom.window;
dom.window.HTMLElement.prototype.scrollIntoView = function () {};
const results = [];
const check = (name, pass) => results.push({ name, pass: !!pass });
const goto = key => [...doc.querySelectorAll('.nav-item')].find(n => n.dataset.key === key).click();
const input = (el, value) => { el.value = value; el.dispatchEvent(new dom.window.Event('input', { bubbles: true })); };

setTimeout(() => {
  goto('stu');
  let rows = [...doc.querySelectorAll('#workarea tbody tr')];
  check('学生管理默认每页显示30人', rows.length === 30 && doc.querySelector('[data-act="student-page-size"]').value === '30');
  check('学生管理显示分页状态', doc.querySelector('.table-pagination')?.textContent.includes('1 / 4'));
  check('姓名列和表头启用固定样式', Boolean(doc.querySelector('th.student-name-col') && doc.querySelector('td.student-name-col')));
  input(doc.querySelector('[data-act="stu-search"]'), '  测试学生001  ');
  rows = [...doc.querySelectorAll('#workarea tbody tr')];
  check('学生姓名支持trim和中文部分匹配', rows.length === 1 && rows[0].textContent.includes('测试学生001'));
  check('搜索后显示清除按钮', !!doc.querySelector('[data-act="stu-search-clear"]'));
  doc.querySelector('[data-act="stu-search-clear"]').click();
  check('清除后恢复分页名单', doc.querySelectorAll('#workarea tbody tr').length === 30);

  doc.querySelector('[data-act="stu-batch-toggle"]').click();
  input(doc.querySelector('[data-act="stu-search"]'), '202611');
  rows = [...doc.querySelectorAll('#workarea tbody tr')];
  check('学号部分匹配只过滤当前结果', rows.length === 30);
  doc.querySelector('[data-act="stu-select-visible"]').click();
  check('全选当前页不选择隐藏学生', doc.querySelector('[data-act="stu-batch-archive"]').textContent.includes('批量归档') && doc.querySelectorAll('input[data-act="stu-select"]:checked').length === rows.length);
  const first = doc.querySelector('input[data-act="stu-select"]');
  first.click();
  check('取消一行后表头为indeterminate', doc.querySelector('[data-act="stu-select-all"]').indeterminate === true);

  goto('dictation');
  const before = fixture.dictation['20261101'][0];
  input(doc.querySelector('[data-act="dict-search"]'), '20261101');
  check('默写搜索只显示匹配学生', doc.querySelectorAll('#workarea tbody tr').length === 1);
  check('默写搜索不改变成绩', doc.querySelector('[data-act="dict-edit"]').textContent.trim() === String(before));

  goto('stu');
  if (doc.querySelector('[data-act="stu-batch-toggle"]').textContent.trim() === '批量管理') doc.querySelector('[data-act="stu-batch-toggle"]').click();
  input(doc.querySelector('[data-act="stu-search"]'), '20261101');
  doc.querySelector('[data-act="stu-select-visible"]').click();
  doc.querySelector('[data-act="stu-batch-archive"]').click();
  check('批量归档使用确认弹窗', doc.getElementById('modalTitle').textContent === '批量归档学生');
  doc.querySelector('[data-act="delete-cancel"]').click();
  check('取消确认不会归档学生', doc.querySelectorAll('#workarea tbody tr').length === 1);
  doc.querySelector('[data-act="stu-batch-archive"]').click();
  doc.querySelector('[data-act="archive-confirm-batch"]').click();
  const persisted = JSON.parse(dom.window.localStorage.getItem('hye_db_v1'));
  check('批量归档从在读名单移除学生', !persisted.students.some(s => s.id === '20261101'));
  check('批量归档保留在归档名单', persisted.archivedStudents.some(s => s.id === '20261101'));
  check('批量归档保留默写成绩', Boolean(persisted.dictation['20261101']));

  goto('settings');
  doc.querySelector('[data-act="archived-manage"]').click();
  doc.querySelector('[data-act="archived-restore"][data-id="20261101"]').click();
  const restored = JSON.parse(dom.window.localStorage.getItem('hye_db_v1'));
  check('归档学生可从数据维护恢复', restored.students.some(s => s.id === '20261101') && !restored.archivedStudents.some(s => s.id === '20261101'));

  console.log(JSON.stringify(results, null, 2));
  const pass = results.every(r => r.pass);
  console.log('OVERALL:', pass ? 'PASS' : 'FAIL');
  dom.window.close();
  process.exit(pass ? 0 : 1);
}, 700);
