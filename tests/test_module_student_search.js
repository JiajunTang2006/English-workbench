const { JSDOM } = require('jsdom');

const { loadWorkbenchHtml } = require('./helpers/workbench_html');
const { createWorkbenchFixture } = require('./fixtures/workbench_fixture');
const html = loadWorkbenchHtml();
const fixture = createWorkbenchFixture();
fixture.writings = [{ id: 'writing-search-task', title: '搜索测试写作', date: '2026-09-01', fullScore: 20, scores: { '20261101': 18, '20261102': 16 } }];
fixture.recitations = [{ id: 'recite-search-task', title: '搜索测试背诵', scope: 'Unit 1', status: { '20261101': { level: 'A' }, '20261102': { level: 'B' } } }];
const errors = [];
const dom = new JSDOM(html, { runScripts: 'dangerously', url: 'https://localhost/', beforeParse(window) { window.localStorage.setItem('hye_db_v1', JSON.stringify(fixture)); } });
const window = dom.window;
const doc = window.document;
window.onerror = (msg, url, line) => errors.push({ msg, line });

setTimeout(() => {
  const results = [];
  const check = (name, value) => results.push({ name, pass: Boolean(value) });
  const goto = key => [...doc.querySelectorAll('.nav-item')].find(item => item.dataset.key === key).click();
  const typeSearch = (act, value) => {
    const input = doc.querySelector(`[data-act="${act}"]`);
    input.value = value;
    input.dispatchEvent(new window.Event('input', { bubbles: true }));
  };

  goto('dictation');
  check('默写模块有姓名搜索框', !!doc.querySelector('[data-act="dict-search"]'));
  typeSearch('dict-search', '测试学生001');
  const dictRows = [...doc.querySelectorAll('.card tbody tr')].map(row => row.textContent);
  check('默写按部分姓名筛选', dictRows.some(text => text.includes('测试学生001')) && !dictRows.some(text => text.includes('测试学生002')));

  goto('recite');
  check('背诵模块有姓名搜索框', !!doc.querySelector('[data-act="recite-search"]'));
  typeSearch('recite-search', '测试学生001');
  check('背诵按部分姓名筛选', doc.getElementById('workarea').textContent.includes('测试学生001') && !doc.getElementById('workarea').textContent.includes('测试学生002'));

  goto('writing');
  check('写作模块有姓名搜索框', !!doc.querySelector('[data-act="writing-search"]'));
  typeSearch('writing-search', '测试学生001');
  check('写作按部分姓名筛选', doc.getElementById('workarea').textContent.includes('测试学生001') && !doc.getElementById('workarea').textContent.includes('测试学生002'));

  goto('stu');
  const input = doc.querySelector('[data-act="stu-search"]');
  input.dispatchEvent(new window.CompositionEvent('compositionstart', { bubbles: true, data: 'h' }));
  input.value = 'h';
  const composingInput = new window.Event('input', { bubbles: true });
  Object.defineProperty(composingInput, 'isComposing', { value: true });
  input.dispatchEvent(composingInput);
  input.value = '胡';
  input.dispatchEvent(new window.CompositionEvent('compositionend', { bubbles: true, data: '胡' }));
  check('中文输入法完成后不残留拼音', doc.querySelector('[data-act="stu-search"]')?.value === '胡' && !doc.querySelector('[data-act="stu-search"]')?.value.includes('huh'));

  const allPass = results.every(item => item.pass) && errors.length === 0;
  console.log(JSON.stringify(results, null, 2));
  console.log('Script errors:', errors.length ? errors : 'none');
  console.log('OVERALL:', allPass ? 'PASS' : 'FAIL');
  dom.window.close();
  process.exit(allPass ? 0 : 1);
}, 700);
