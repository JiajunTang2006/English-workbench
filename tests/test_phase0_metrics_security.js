const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const { loadWorkbenchHtml } = require('./helpers/workbench_html');
const html = loadWorkbenchHtml();
const malicious = '<img id="xss-marker" src=x onerror="window.__xss=true">';
const fixture = {
  schema: 1,
  teacher: { name: '冯老师', subject: '初中英语' },
  classes: ['711'],
  settings: { excellent: 90, pass: 60, criticalLow: 55 },
  students: [
    { id: '1', name: malicious, class: '711', english: 40 },
    { id: '2', name: '正常学生', class: '711', english: 60 },
  ],
  exams: [{ id: 'exam-1', name: '150分制测试', fullScore: 150, scores: { '1': { 英语: 90 }, '2': { 英语: 135 } } }],
  currentExamId: 'exam-1',
  todos: Array.from({ length: 7 }, (_, index) => ({ id: String(index), title: index === 0 ? malicious : `待办${index}`, done: false })),
};

const dom = new JSDOM(html, {
  runScripts: 'dangerously',
  url: 'https://localhost/',
  beforeParse(window) { window.localStorage.setItem('hye_db_v1', JSON.stringify(fixture)); },
});

setTimeout(() => {
  const doc = dom.window.document;
  const results = [];
  const check = (name, pass) => results.push({ name, pass: Boolean(pass) });
  const values = [...doc.querySelectorAll('.dash-kpi-value')].map(node => node.textContent.trim());
  const pageText = doc.getElementById('workarea').textContent;
  const pageHtml = doc.getElementById('workarea').innerHTML;

  check('入学均分使用入学成绩而非当前考试', values[1] === '50.0');
  check('150分制及格线按比例换算', pageHtml.includes('dash-kpi-teal') && pageText.includes('≥90.0分'));
  check('150分制优秀线按比例换算', pageHtml.includes('dash-kpi-pink') && pageText.includes('≥135.0分'));
  check('考试满分不再写死为100', pageText.includes('150.0'));
  check('待办KPI显示全部未完成数量', pageText.includes('7 条未完成'));
  check('待办标题不会创建注入节点', !doc.querySelector('#xss-marker') && pageText.includes(malicious));

  [...doc.querySelectorAll('.nav-item')].find(node => node.dataset.key === 'stu').click();
  const studentText = doc.getElementById('workarea').textContent;
  check('学生姓名不会创建注入节点', !doc.querySelector('#xss-marker') && studentText.includes(malicious));
  check('脚本未执行', dom.window.__xss !== true);

  const pass = results.every(result => result.pass);
  console.log(JSON.stringify(results, null, 2));
  console.log('OVERALL:', pass ? 'PASS' : 'FAIL');
  dom.window.close();
  process.exit(pass ? 0 : 1);
}, 400);
