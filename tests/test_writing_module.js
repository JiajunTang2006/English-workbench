const { JSDOM } = require('jsdom');

const { loadWorkbenchHtml } = require('./helpers/workbench_html');
const { createWorkbenchFixture } = require('./fixtures/workbench_fixture');
const html = loadWorkbenchHtml();
const fixture = createWorkbenchFixture();
fixture.writings = [];
const errors = [];
const dom = new JSDOM(html, { runScripts: 'dangerously', url: 'https://localhost/', beforeParse(window) { window.localStorage.setItem('hye_db_v1', JSON.stringify(fixture)); } });
const window = dom.window;
const doc = window.document;
window.onerror = (msg, url, line) => errors.push({ msg, line });

setTimeout(() => {
  const results = [];
  const check = (name, value) => results.push({ name, pass: Boolean(value) });
  const goto = key => [...doc.querySelectorAll('.nav-item')].find(item => item.dataset.key === key).click();
  goto('writing');
  let wa = doc.getElementById('workarea');
  check('写作页显示任务列表', wa.textContent.includes('写作任务'));

  wa.querySelector('[data-act="writing-add"]').click();
  check('创建写作任务只保留名称日期满分', !!doc.getElementById('m-wri-title') && !!doc.getElementById('m-wri-date') && !!doc.getElementById('m-wri-full') && !doc.querySelector('.m-wri-score'));
  doc.getElementById('m-wri-title').value = '第一单元写作';
  doc.getElementById('m-wri-date').value = '2026-09-01';
  doc.getElementById('m-wri-full').value = '20';
  doc.getElementById('m-wri-save').click();
  wa = doc.getElementById('workarea');
  check('创建后生成空写作表', wa.textContent.includes('第一单元写作') && !!wa.querySelector('.writing-table'));
  check('写作表只显示学号姓名成绩列', [...wa.querySelectorAll('.writing-table thead')].every(thead => thead.textContent.includes('学号') && thead.textContent.includes('姓名') && thead.textContent.includes('写作得分')));
  check('写作表不显示男女分组文字', !wa.textContent.includes('男生') && !wa.textContent.includes('女生'));

  const scoreCell = wa.querySelector('[data-act="writing-edit-score"]');
  scoreCell.textContent = '18';
  scoreCell.dispatchEvent(new window.Event('blur'));
  check('表格直接编辑写作成绩', doc.querySelector('[data-act="writing-edit-score"]').textContent.trim() === '18');
  check('写作卡片显示平均分', wa.textContent.includes('18.0') && wa.textContent.includes('平均分'));

  window.XLSX = { utils: { sheet_to_json: () => [
    ['学号', '姓名', '班级', '写作成绩'],
    ['20261101', '测试学生001', '711', 16],
    ['20261102', '测试学生002', '711', 15]
  ] } };
  window.importWritingFromWorkbooks([{ name: 'writing.xlsx', workbook: { SheetNames: ['711'], Sheets: { '711': {} } } }]);
  const saved = JSON.parse(window.localStorage.getItem('hye_db_v1'));
  check('智能导入填充当前任务', saved.writings?.[0]?.scores?.['20261102'] === 15);
  check('智能导入不新建任务', saved.writings?.length === 1);

  const allPass = results.every(item => item.pass) && errors.length === 0;
  console.log(JSON.stringify(results, null, 2));
  console.log('Script errors:', errors.length ? errors : 'none');
  console.log('OVERALL:', allPass ? 'PASS' : 'FAIL');
  dom.window.close();
  process.exit(allPass ? 0 : 1);
}, 700);
