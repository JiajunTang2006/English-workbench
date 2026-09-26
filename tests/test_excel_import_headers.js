const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const { loadWorkbenchHtml } = require('./helpers/workbench_html');
const html = loadWorkbenchHtml();
const fixture = {
  schema: 1,
  classes: ['711'],
  students: [],
  exams: [{ id: 'exam-1', name: '导入测试', fullScore: 100, scores: {} }],
  currentExamId: 'exam-1',
};

const dom = new JSDOM(html, {
  runScripts: 'dangerously',
  url: 'https://localhost/',
  beforeParse(window) {
    window.localStorage.setItem('hye_db_v1', JSON.stringify(fixture));
    window.XLSX = { utils: { sheet_to_json: sheet => sheet } };
  },
});

setTimeout(() => {
  const results = [];
  const check = (name, pass) => results.push({ name, pass: Boolean(pass) });
  const workbook = {
    SheetNames: ['无班级命名的工作表'],
    Sheets: {
      无班级命名的工作表: [
        ['学号', '姓名', '成绩', '班级', '年级名次', '排名'],
        ['A001', '张三', 88, '713', 123, 9],
      ],
    },
  };

  dom.window.importFromWorkbook(workbook);
  const saved = JSON.parse(dom.window.localStorage.getItem('hye_db_v1'));
  const student = saved.students.find(item => item.id === 'A001');
  const exam = saved.exams.find(item => item.id === 'exam-1');

  check('识别“成绩”列而不是固定第5列', exam.scores.A001.英语 === 88);
  check('识别年级名次别名', exam.scores.A001.gradeRank === 123);
  check('不把含义不清的“排名”当作班级排名', exam.scores.A001.gradeRank !== 9);
  check('识别班级列', student.class === '713');
  check('导入新班级后同步班级列表', saved.classes.includes('713'));

  const pass = results.every(result => result.pass);
  console.log(JSON.stringify(results, null, 2));
  console.log('OVERALL:', pass ? 'PASS' : 'FAIL');
  dom.window.close();
  process.exit(pass ? 0 : 1);
}, 350);
