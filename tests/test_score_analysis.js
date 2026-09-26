const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const { loadWorkbenchHtml } = require('./helpers/workbench_html');
const html = loadWorkbenchHtml();
const fixture = {
  schema: 3,
  teacher: { name: '测试老师', subject: '初中英语' },
  classes: ['701', '702'],
  settings: { excellent: 90, pass: 60, criticalLow: 55 },
  students: [
    { id: '1', name: '甲', class: '701', english: 80 },
    { id: '2', name: '乙', class: '701', english: 80 },
    { id: '3', name: '丙', class: '701', english: 70 },
    { id: '4', name: '丁', class: '701', english: 50 },
    { id: '5', name: '戊', class: '702', english: 90 },
    { id: '6', name: '己', class: '702', english: 60 },
  ],
  exams: [
    { id: 'e1', name: '期中', date: '2026-05-01', fullScore: 100, tierLines: { a: 90, b: 75, c: 60 }, classGradeRanks: { '701': 2, '702': 5 }, scores: {
      '1': { 英语: 95, gradeRank: 1 }, '2': { 英语: 95, gradeRank: 50 }, '3': { 英语: 70, gradeRank: 150 },
      '4': { 英语: 50, gradeRank: null }, '5': { 英语: 90, gradeRank: 250 }, '6': { 英语: 60, gradeRank: 350 },
    } },
    { id: 'e2', name: '期末', date: '2026-07-01', fullScore: 120, tierLines: { a: 105, b: 85, c: 70 }, classGradeRanks: { '701': 1 }, scores: {
      '1': { 英语: 110, gradeRank: 2 }, '2': { 英语: 100, gradeRank: 40 }, '3': { 英语: 80, gradeRank: 130 }, '4': { 英语: 60, gradeRank: 230 },
    } },
  ],
  currentExamId: 'e1',
};

const chartOptions = {};
const dom = new JSDOM(html, {
  runScripts: 'dangerously',
  url: 'https://localhost/',
  beforeParse(window) {
    window.localStorage.setItem('hye_db_v1', JSON.stringify(fixture));
    window.echarts = {
      init(node) {
        return {
          setOption(option) { chartOptions[node.id] = option; },
          dispose() {},
          resize() {},
        };
      },
    };
  },
});

const { document } = dom.window;
const results = [];
const check = (name, value) => results.push({ name, pass: Boolean(value) });

setTimeout(() => {
  [...document.querySelectorAll('.nav-item')].find(node => node.dataset.key === 'score').click();
  document.querySelector('.tab[data-tab="tier"]').click();
  const cards = [...document.querySelectorAll('#scorePanel .analysis-card')];
  check('分层分析含三个纵向卡片', cards.length === 3);
  check('考试概览含均分最高分优秀率及格率', cards[0].textContent.includes('76.7') && cards[0].textContent.includes('95') && cards[0].textContent.includes('50%') && cards[0].textContent.includes('83.3%'));
  check('全部汇总不展示单一班级年级名次', cards[0].textContent.includes('切换到具体班级查看'));
  check('年级名次按每100名动态分档', cards[1].textContent.includes('1–100名') && cards[1].textContent.includes('301–400名'));
  check('未录入排名不进入比例分母', cards[1].textContent.includes('已录 5 人') && cards[1].textContent.includes('1人 · 不计比例'));
  check('学业等级人数按本次分层线计算', [...cards[2].querySelectorAll('.level-card strong')].map(node => node.textContent.trim()).join(',') === '3,0,2,1');

  document.querySelector('.tab[data-tab="table"]').click();
  const rows = [...document.querySelectorAll('#scorePanel tbody tr')];
  check('同分学生班级排名并列且后续跳号', rows.slice(0, 4).map(row => row.children[4].textContent.trim()).join(',') === '1,1,3,4');
  check('年级排名来自导入数据', rows[0].children[5].textContent.trim() === '1');

  [...document.querySelectorAll('[data-act="score-class"]')].find(node => node.dataset.cls === '701').click();
  document.querySelector('.tab[data-tab="tier"]').click();
  document.querySelector('[data-act="score-class-grade-rank"]').click();
  check('具体班级可录入班级年级名次', Boolean(document.getElementById('m-class-grade-rank')));
  document.getElementById('m-class-grade-rank').value = '4';
  document.getElementById('m-class-grade-rank-save').click();
  const saved = JSON.parse(dom.window.localStorage.getItem('hye_db_v1'));
  check('班级年级名次按考试和班级保存', saved.exams.find(item => item.id === 'e1').classGradeRanks['701'] === 4);

  document.querySelector('.tab[data-tab="compare"]').click();
  setTimeout(() => {
    check('历次对比含三张趋势卡片', document.querySelectorAll('#scorePanel .analysis-card').length === 3);
    check('平均分趋势按考试生成', chartOptions['trend-average-chart']?.series?.[0]?.data?.length === 2);
    check('班级年级名次纵轴反向', chartOptions['trend-rank-chart']?.yAxis?.inverse === true);
    check('A/B层率默认显示且优秀及格默认隐藏', chartOptions['trend-rate-chart']?.legend?.selected?.优秀率 === false && chartOptions['trend-rate-chart']?.legend?.selected?.及格率 === false);

    const pass = results.every(item => item.pass);
    console.log(JSON.stringify(results, null, 2));
    console.log('OVERALL:', pass ? 'PASS' : 'FAIL');
    dom.window.close();
    process.exit(pass ? 0 : 1);
  }, 50);
}, 450);
