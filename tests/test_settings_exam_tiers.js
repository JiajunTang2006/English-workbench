const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const { loadWorkbenchHtml } = require('./helpers/workbench_html');
const html = loadWorkbenchHtml();
const fixture = {
  schema: 1,
  teacher: { name: '王老师', subject: '英语' },
  classes: ['701'],
  settings: { excellent: 90, pass: 60, criticalLow: 55 },
  students: [
    { id: '1', name: '学生甲', class: '701', english: 80 },
    { id: '2', name: '学生乙', class: '701', english: 70 },
    { id: '3', name: '学生丙', class: '701', english: 60 },
    { id: '4', name: '学生丁', class: '701', english: 50 },
  ],
  exams: [
    { id: 'exam-150', name: '150分考试', fullScore: 150, scores: {
      '1': { 英语: 140 }, '2': { 英语: 105 }, '3': { 英语: 75 }, '4': { 英语: 45 },
    } },
    { id: 'exam-120', name: '120分考试', fullScore: 120, scores: {} },
  ],
  currentExamId: 'exam-150',
};

const dom = new JSDOM(html, {
  runScripts: 'dangerously',
  url: 'https://localhost/',
  beforeParse(window) { window.localStorage.setItem('hye_db_v1', JSON.stringify(fixture)); },
});
const { document } = dom.window;
const results = [];
const check = (name, value) => results.push({ name, pass: Boolean(value) });
const goto = key => [...document.querySelectorAll('.nav-item')].find(node => node.dataset.key === key).click();

setTimeout(() => {
  check('产品标题包含教师与学科', document.getElementById('appTitle').textContent === '王老师 · 英语工作台');
  check('教师姓名显示为使用者信息', document.getElementById('userNameLabel').textContent === '王老师');
  check('顶部不再显示当前页面标签', !document.getElementById('currentPageLabel'));

  goto('settings');
  document.getElementById('sett-name').value = '李老师';
  document.getElementById('sett-subject').value = '初中英语';
  document.getElementById('sett-excellent').value = '80';
  document.getElementById('sett-pass').value = '50';
  document.querySelector('[data-act="settings-save"]').click();
  check('保存后产品标题随设置更新', document.getElementById('appTitle').textContent === '李老师 · 初中英语工作台');
  check('保存后使用者信息联动', document.getElementById('userNameLabel').textContent === '李老师');
  check('浏览器标题使用当前页面和产品名', document.title === '班级与设置 · 李老师 · 初中英语工作台');
  check('右上角不再显示个人菜单入口', !document.getElementById('userMenuBtn'));
  check('设置页不再显示旧临界生线', !document.getElementById('sett-critical'));
  check('班级设置页不再放考试分层线', !document.getElementById('sett-tier-a'));

  goto('dash');
  const dashText = document.getElementById('workarea').textContent;
  const dashHtml = document.getElementById('workarea').innerHTML;
  check('150分制优秀线按80%换算为120分', dashHtml.includes('dash-kpi-pink') && dashText.includes('≥120.0分'));
  check('150分制及格线按50%换算为75分', dashHtml.includes('dash-kpi-teal') && dashText.includes('≥75.0分'));

  goto('score');
  document.querySelector('[data-act="score-add-exam"]').click();
  check('新增考试弹窗包含三条分层线', document.getElementById('m-exam-tier-a') && document.getElementById('m-exam-tier-b') && document.getElementById('m-exam-tier-c'));
  document.getElementById('modalClose').click();
  document.querySelector('[data-act="score-edit-exam"]').click();
  check('分层线已移动到考试弹窗', document.getElementById('m-exam-tier-a') && document.getElementById('m-exam-tier-b') && document.getElementById('m-exam-tier-c'));
  document.getElementById('m-exam-tier-a').value = '130';
  document.getElementById('m-exam-tier-b').value = '100';
  document.getElementById('m-exam-tier-c').value = '70';
  document.getElementById('m-exam-save').click();
  document.querySelector('.tab[data-tab="tier"]').click();
  const tierText = document.getElementById('scorePanel').textContent;
  const levelCards = [...document.querySelectorAll('#scorePanel .level-card')];
  check('分层线使用本场考试手动值', tierText.includes('≥ 130.0') && tierText.includes('100.0–＜130.0') && tierText.includes('70.0–＜100.0') && tierText.includes('＜ 70.0'));
  check('ABCD人数按手动分层线计算', levelCards.map(node => node.querySelector('strong').textContent.trim()).join(',') === '1,1,1,1');

  document.querySelector('.tab[data-tab="table"]').click();
  const examSelect = document.querySelector('[data-act="score-exam"]');
  examSelect.value = 'exam-120';
  examSelect.dispatchEvent(new dom.window.Event('change', { bubbles: true }));
  document.querySelector('[data-act="score-edit-exam"]').click();
  check('不同考试不沿用上一场分层线', document.getElementById('m-exam-tier-a').value === '' && document.getElementById('m-exam-tier-b').value === '' && document.getElementById('m-exam-tier-c').value === '');
  document.getElementById('modalClose').click();

  document.querySelector('.tab[data-tab="tier"]').click();
  check('未设置分层线时明确提示', document.getElementById('scorePanel').textContent.includes('尚未设置A、B、C分层线'));
  check('未设置时可直接打开考试弹窗', Boolean(document.querySelector('[data-act="score-edit-exam"]')));

  const pass = results.every(item => item.pass);
  console.log(JSON.stringify(results, null, 2));
  console.log('OVERALL:', pass ? 'PASS' : 'FAIL');
  dom.window.close();
  process.exit(pass ? 0 : 1);
}, 500);
