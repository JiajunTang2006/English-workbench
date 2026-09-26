const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const { loadWorkbenchHtml } = require('./helpers/workbench_html');
const html = loadWorkbenchHtml();
const fixture = {
  schema: 4, dataContract: 2, teacher: { name: '', subject: '初中英语' }, classes: ['711'],
  settings: { excellent: 90, pass: 60, criticalLow: 55 },
  students: [{ id: 'A001', name: '张三', class: '711', english: 70, target: '', weakTags: '', phone: '', seat: '', evaluationTags: [], evaluationNote: '' }],
  exams: [
    { id: 'entrance', name: '入学考试', date: '2026-08-01', fullScore: 100, type: 'english_total', examKind: 'entrance', scores: { A001: { 英语: 70, gradeRank: 2, attendanceStatus: 'present', classAtExam: '711' } }, tierLines: { a: 90, b: 75, c: 60 }, classGradeRanks: {} },
    { id: 'monthly', name: '9月月考', date: '2026-09-30', fullScore: 100, type: 'english_total', examKind: 'regular', scores: { A001: { 英语: 95, gradeRank: 1, attendanceStatus: 'present', classAtExam: '711' } }, tierLines: { a: 90, b: 75, c: 60 }, classGradeRanks: {} }
  ],
  currentExamId: 'entrance', recitations: [], writings: [], errors: [], paperDocuments: [], critical: [], todos: [], dictation: {}, dictationNames: ['自定义1', '自定义2'], studentTags: [], classAliases: {}
};

const dom = new JSDOM(html, { runScripts: 'dangerously', url: 'https://localhost/', beforeParse(window) { window.localStorage.setItem('hye_db_v1', JSON.stringify(fixture)); } });
setTimeout(() => {
  const checks = [];
  const check = (name, pass) => checks.push({ name, pass: Boolean(pass) });
  const select = dom.window.document.querySelector('[data-act="dashboard-exam"]');
  check('考试概览提供考试切换', Boolean(select && select.options.length === 2));
  check('排行榜没有独立考试切换', !dom.window.document.querySelector('[data-act="dashboard-leaderboard-exam"]'));
  check('初始概览和排行榜跟随入学考试', dom.window.document.querySelector('.dash-exam-meta-card')?.textContent.includes('入学考试') && dom.window.document.querySelector('.dash-leaderboard-card')?.textContent.includes('入学考试') && dom.window.document.querySelector('.dash-lb-table')?.textContent.includes('70'));
  select.value = 'monthly';
  select.dispatchEvent(new dom.window.Event('change', { bubbles: true }));
  const overview = dom.window.document.querySelector('.dash-exam-meta-card')?.textContent || '';
  const leaderboard = dom.window.document.querySelector('.dash-leaderboard-card')?.textContent || '';
  check('切换后考试概览与排行榜同步为9月月考', overview.includes('9月月考') && leaderboard.includes('9月月考') && dom.window.document.querySelector('.dash-lb-table')?.textContent.includes('95'));
  console.log(JSON.stringify(checks, null, 2));
  const pass = checks.every(item => item.pass);
  console.log('OVERALL:', pass ? 'PASS' : 'FAIL');
  dom.window.close();
  process.exit(pass ? 0 : 1);
}, 550);
