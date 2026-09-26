const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const { loadWorkbenchHtml } = require('./helpers/workbench_html');
const html = loadWorkbenchHtml();
const students = Array.from({ length: 10 }, (_, index) => ({ id: `c${index + 1}`, name: `重点学生${index + 1}`, class: '711', english: 70 }));
const exams = Array.from({ length: 5 }, (_, index) => ({
  id: `e${index + 1}`,
  name: `考试${index + 1}`,
  date: `2026-0${index + 1}-01`,
  fullScore: 100,
  tierLines: { a: 90, b: 75, c: 60 },
  scores: Object.fromEntries(students.map(student => [student.id, { 英语: 70, classAtExam: '711' }])),
}));
const fixture = {
  schema: 4,
  dataContract: 2,
  teacher: { name: '测试老师', subject: '英语' },
  classes: ['711'],
  settings: { excellent: 90, pass: 60, criticalLow: 55 },
  students,
  exams,
  currentExamId: exams[4].id,
  todos: [],
  errors: [],
  paperDocuments: [],
  dictation: {},
  recitations: [],
  writings: [],
  studentTags: [],
};

const errors = [];
const dom = new JSDOM(html, {
  runScripts: 'dangerously',
  url: 'https://localhost/',
  beforeParse(window) { window.localStorage.setItem('hye_db_v1', JSON.stringify(fixture)); },
});
dom.window.onerror = (message, url, line) => errors.push({ message, line });

setTimeout(() => {
  const { document } = dom.window;
  const results = [];
  const check = (name, value) => results.push({ name, pass: Boolean(value) });
  const more = document.querySelector('.risk-cloud-card.c-level .risk-cloud-more button');
  check('超过8名时显示查看全部按钮', Boolean(more));
  const main = document.querySelector('main');
  if (main) main.scrollTop = 500;
  more?.click();
  check('查看全部打开独立详情页', Boolean(document.querySelector('.risk-detail-page')));
  check('详情页展示完整名单', document.querySelectorAll('.risk-detail-page .risk-student-card').length >= 10);
  check('C类详情页只展示C类', Boolean(document.querySelector('.risk-detail-page .c-level')) && !document.querySelector('.risk-detail-page .d-level'));
  check('打开详情页自动置顶', main?.scrollTop === 0);
  check('详情页不再显示查看全部按钮', !document.querySelector('.risk-detail-page .risk-cloud-more'));
  document.querySelector('[data-act="risk-back"]')?.click();
  check('可返回仪表盘', Boolean(document.querySelector('.risk-attention-card')));
  check('全程无脚本错误', errors.length === 0);
  const pass = results.every(item => item.pass);
  console.log(JSON.stringify(results, null, 2));
  console.log('Script errors:', errors.length ? errors : 'none');
  console.log('OVERALL:', pass ? 'PASS' : 'FAIL');
  dom.window.close();
  process.exit(pass ? 0 : 1);
}, 700);
