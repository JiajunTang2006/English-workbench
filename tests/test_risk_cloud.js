const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const { loadWorkbenchHtml } = require('./helpers/workbench_html');
const html = loadWorkbenchHtml();
const score = (value, classAtExam = '711', gradeRank = null) => ({ 英语: value, classAtExam, gradeRank });
const fixture = {
  schema: 4,
  dataContract: 2,
  teacher: { name: '测试老师', subject: '英语' },
  classes: ['711', '712'],
  settings: { excellent: 90, pass: 60, criticalLow: 55 },
  students: [
    { id: 'c1', name: '持续C', class: '711', english: 70 },
    { id: 'd1', name: '持续D', class: '712', english: 40 },
    { id: 'old', name: '旧记录', class: '711', english: 70 },
  ],
  exams: [
    { id: 'e0', name: '最早考试', date: '2026-01-01', fullScore: 100, tierLines: { a: 90, b: 75, c: 60 }, scores: { c1: score(70), d1: score(40, '712'), old: score(70) } },
    { id: 'e1', name: '第二次考试', date: '2026-02-01', fullScore: 100, tierLines: { a: 90, b: 75, c: 60 }, scores: { c1: score(70), d1: score(40, '712'), old: score(90) } },
    { id: 'e2', name: '第三次考试', date: '2026-03-01', fullScore: 100, tierLines: { a: 90, b: 75, c: 60 }, scores: { c1: score(70), d1: score(40, '712'), old: score(90) } },
    { id: 'e3', name: '第四次考试', date: '2026-04-01', fullScore: 100, tierLines: { a: 90, b: 75, c: 60 }, scores: { c1: score(70), d1: score(40, '712'), old: score(90) } },
    { id: 'e4', name: '第五次考试', date: '2026-05-01', fullScore: 100, tierLines: { a: 90, b: 75, c: 60 }, scores: { c1: score(70), d1: score(40, '712', 53), old: score(90) } },
    { id: 'e5', name: '第六次考试', date: '2026-06-01', fullScore: 100, tierLines: { a: 90, b: 75, c: 60 }, scores: { c1: score(90), d1: score(40, '712', 120), old: score(90) } },
  ],
  currentExamId: 'e5',
};

const errors = [];
const dom = new JSDOM(html, {
  runScripts: 'dangerously',
  url: 'https://localhost/',
  beforeParse(window) { window.localStorage.setItem('hye_db_v1', JSON.stringify(fixture)); },
});
const { document } = dom.window;
dom.window.onerror = (message, url, line) => errors.push({ message, line });

setTimeout(() => {
  const results = [];
  const check = (name, value) => results.push({ name, pass: Boolean(value) });
  const cloudText = selector => [...document.querySelectorAll(`${selector} .risk-cloud-name`)].map(node => node.textContent.trim());

  check('左侧不再显示临界生跟踪', ![...document.querySelectorAll('.nav-item')].some(node => node.dataset.key === 'critical'));
  check('考试概览包含ABCD四层比例', document.querySelectorAll('.dash-level-bar-col').length === 4);
  check('优秀率和及格率显示为KPI卡片', document.querySelectorAll('.dash-kpi-teal .dash-kpi-value').length === 1 && document.querySelectorAll('.dash-kpi-pink .dash-kpi-value').length === 1);
  check('考试概览不再使用优秀及格进度条', !document.querySelector('.dashboard-overview-layout .chart-row'));
  check('重点关注学生标题已更新', document.body.innerHTML.includes('重点关注学生'));
  check('重点关注区域提供班级下拉', document.querySelector('select[data-act="risk-class"]')?.querySelectorAll('option').length === 3);
  check('仪表盘有C/D两个数据云', document.querySelector('.risk-cloud-card.c-level') && document.querySelector('.risk-cloud-card.d-level'));
  check('重点学生改为紧凑卡片布局', document.querySelectorAll('.risk-student-grid').length === 2 && document.querySelectorAll('.risk-student-card').length > 0);
  check('重点学生显示出现次数分布条', document.querySelectorAll('.risk-frequency-track').length === 2);
  check('重点学生不再使用旧气泡节点', !document.querySelector('.risk-bubble'));
  check('只统计最新五次考试', cloudText('.risk-cloud-card.c-level').includes('持续C') && !cloudText('.risk-cloud-card.c-level').includes('旧记录'));
  check('旧考试不再计入后学生从数据云移除', !cloudText('.risk-cloud-card.c-level').includes('旧记录'));
  const dEvidence = document.querySelector('.risk-cloud-card.d-level .risk-evidence-list')?.textContent || '';
  check('D类学生显示观察频次证据', cloudText('.risk-cloud-card.d-level').includes('持续D') && dEvidence.includes('近5次考试中5次处于D层'));
  check('D类学生显示最近排名变化证据', dEvidence.includes('最近一次年级排名下降67位'));

  const classSelect = document.getElementById('classSelect');
  classSelect.value = '711';
  classSelect.dispatchEvent(new dom.window.Event('change', { bubbles: true }));
  check('风险数据云支持按班级筛选', cloudText('.risk-cloud-card.d-level').length === 0 && document.querySelector('.risk-cloud-card.c-level'));
  check('风险层级不只依赖颜色', document.body.textContent.includes('C层 · 需跟进') && document.body.textContent.includes('D层 · 优先干预'));

  const riskSelect = document.querySelector('select[data-act="risk-class"]');
  riskSelect.value = '712';
  riskSelect.dispatchEvent(new dom.window.Event('change', { bubbles: true }));
  check('重点关注区域下拉可独立筛选班级', cloudText('.risk-cloud-card.c-level').length === 0 && cloudText('.risk-cloud-card.d-level').includes('持续D'));
  check('全程无脚本错误', errors.length === 0);

  const pass = results.every(item => item.pass);
  console.log(JSON.stringify(results, null, 2));
  console.log('Script errors:', errors.length ? errors : 'none');
  console.log('OVERALL:', pass ? 'PASS' : 'FAIL');
  dom.window.close();
  process.exit(pass ? 0 : 1);
}, 600);
