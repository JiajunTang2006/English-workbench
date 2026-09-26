// 回归测试：全新/空名单状态不自动恢复默认班级，页面保持可用
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const { loadWorkbenchHtml } = require('./helpers/workbench_html');
const html = loadWorkbenchHtml();
const broken = JSON.stringify({ schema: 1, classes: [], students: [] });
const dom = new JSDOM(html, {
  runScripts: 'dangerously',
  url: 'https://localhost/',
  beforeParse(window) {
    window.localStorage.setItem('hye_db_v1', broken);
    window.HTMLElement.prototype.scrollIntoView = function () {};
  }
});
const window = dom.window, doc = window.document;

setTimeout(() => {
  const results = [];
  const check = (name, condition) => results.push({ name, pass: !!condition });
  const goto = key => [...doc.querySelectorAll('.nav-item')].find(item => item.dataset.key === key).click();

  check('空名单状态不崩溃', !!doc.getElementById('workarea'));
  check('顶部没有默认班级', doc.querySelectorAll('#classSelect option').length === 1);
  goto('dictation');
  check('默写模块显示空状态', doc.body.innerHTML.includes('暂无班级') || doc.body.innerHTML.includes('暂无学生'));
  goto('score');
  check('成绩模块保留名单导入入口', doc.body.innerHTML.includes('智能导入'));
  goto('dash');
  check('仪表盘可以打开', doc.body.innerHTML.includes('最近考试概览'));

  const allPass = results.every(result => result.pass);
  console.log(JSON.stringify(results, null, 2));
  console.log('OVERALL:', allPass ? 'PASS' : 'FAIL');
  dom.window.close();
  process.exit(allPass ? 0 : 1);
}, 600);
