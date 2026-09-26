// 测试：JSON 备份导入流程（File → FileReader → migrate → 渲染），验证中文姓名不乱码
const { JSDOM } = require('jsdom');

const { loadWorkbenchHtml } = require('./helpers/workbench_html');
const { createWorkbenchFixture } = require('./fixtures/workbench_fixture');
const html = loadWorkbenchHtml();
const jsonContent = JSON.stringify(createWorkbenchFixture());

const errors = [];
const dom = new JSDOM(html, {
  runScripts: 'dangerously',
  url: 'https://localhost/',
  beforeParse(window) { window.HTMLElement.prototype.scrollIntoView = function () {}; }
});
const window = dom.window, doc = window.document;
window.onerror = (msg, url, line) => errors.push({ msg, line });
const goto = k => [...doc.querySelectorAll('.nav-item')].find(n => n.dataset.key === k).click();

setTimeout(() => {
  const results = [];
  const check = (n, c) => results.push({ name: n, pass: !!c });

  // 1) 导入前：仪表盘排行榜仍可打开
  goto('dash');
  check('导入前仪表盘排行榜存在', doc.body.innerHTML.includes('本次考试前十'));

  // 2) 构造 File 并触发导入（走 handleImport 完整流程）
  goto('settings');
  const input = doc.getElementById('fileInput');
  const file = new window.File([jsonContent], 'dictation_scores_fixture.json', { type: 'application/json' });
  Object.defineProperty(input, 'files', { value: [file], configurable: true });
  input.dispatchEvent(new window.Event('change', { bubbles: true }));

  // FileReader 异步，等它读完
  setTimeout(() => {
    check('导入成功提示', doc.getElementById('toast').textContent.includes('JSON 导入成功'));

    // 3) 导入后仪表盘仍正常，中文姓名不乱码
    goto('dash');
    const body = doc.body.innerHTML;
    check('导入后仪表盘排行榜存在', body.includes('本次考试前十'));

    // 4) 默写模块：随机分数已就位（学号|姓名|默写1|默写2 → 默写1是第3列）
    goto('dictation');
    const wa = doc.getElementById('workarea');
    check('中文姓名正常(测试学生001)', wa.innerHTML.includes('测试学生001'));
    check('姓名未乱码(无æ)', !wa.innerHTML.includes('æ'));
    const firstScore = wa.querySelector('tbody tr td:nth-child(3)').textContent.trim();
    const seed = JSON.parse(jsonContent);
    check('默写1首行分数与JSON一致', parseFloat(firstScore) === seed.dictation['20261101'][0]);

    // 5) 切712班
    const classSelect = doc.querySelector('[data-act="dict-class-select"]');
    classSelect.value = '712';
    classSelect.dispatchEvent(new window.Event('change', { bubbles: true }));
    const rows = [...doc.querySelectorAll('#workarea tbody tr')];
    check('712班默写表正常(46人)', rows.length === 46 && rows.every(r=>String(r.children[0].textContent.trim()).startsWith('202612')));

    check('全程无脚本错误', errors.length === 0);
    if (errors.length) console.log('Script errors:', JSON.stringify(errors));

    const allPass = results.every(r => r.pass);
    console.log(JSON.stringify(results, null, 2));
    console.log('OVERALL:', allPass ? 'PASS' : 'FAIL');
    dom.window.close();
    process.exit(allPass ? 0 : 1);
  }, 400);
}, 700);
