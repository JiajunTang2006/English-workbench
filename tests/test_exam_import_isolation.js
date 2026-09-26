const fs = require('fs');
const path = require('path');
const { JSDOM, ResourceLoader } = require('jsdom');

const { loadWorkbenchHtml } = require('./helpers/workbench_html');
const html = loadWorkbenchHtml({ includeThirdParty: true });
class LocalAssetLoader extends ResourceLoader {
  fetch(url) {
    const pathname = new URL(url).pathname;
    if (pathname.startsWith('/workbench-assets/')) return Promise.resolve(fs.readFileSync(path.join(__dirname, '..', 'workbench-assets', path.basename(pathname))));
    return null;
  }
}

const fixture = {
  schema: 4, dataContract: 2, teacher: { name: '', subject: '初中英语' }, classes: ['711'],
  settings: { excellent: 90, pass: 60, criticalLow: 55 },
  students: [{ id: 'A001', name: '张三', class: '711', english: 70, target: '', weakTags: '', phone: '', seat: '', evaluationTags: [], evaluationNote: '' }],
  exams: [{ id: 'entrance-1', name: '入学考试', date: '2026-08-01', fullScore: 100, type: 'english_total', examKind: 'entrance', scores: { A001: { 英语: 70, gradeRank: 20, attendanceStatus: 'present', classAtExam: '711' } }, tierLines: null, classGradeRanks: {} }],
  currentExamId: 'entrance-1', recitations: [], writings: [], errors: [], paperDocuments: [], critical: [], todos: [], dictation: {}, dictationNames: ['自定义1', '自定义2'], studentTags: [], classAliases: {}
};

const dom = new JSDOM(html, {
  runScripts: 'dangerously', resources: new LocalAssetLoader(), url: 'https://localhost/',
  beforeParse(window) { window.localStorage.setItem('hye_db_v1', JSON.stringify(fixture)); }
});

setTimeout(() => {
  const workbookFor = score => ({
    SheetNames: ['711班'],
    Sheets: { '711班': dom.window.XLSX.utils.aoa_to_sheet([['学号', '姓名', '班级', '英语', '年级排名'], ['A001', '张三', '711', score, 20]]) }
  });
  const checks = [];
  const check = (name, pass) => checks.push({ name, pass: Boolean(pass) });

  const createExam = (name, date) => {
    dom.window.openExamModal();
    dom.window.document.getElementById('m-exam-name').value = name;
    dom.window.document.getElementById('m-exam-date').value = date;
    dom.window.document.getElementById('m-exam-full').value = '100';
    dom.window.document.getElementById('m-exam-tier-a').value = '90';
    dom.window.document.getElementById('m-exam-tier-b').value = '75';
    dom.window.document.getElementById('m-exam-tier-c').value = '60';
    dom.window.document.getElementById('m-exam-save').click();
  };

  createExam('9月月考', '2026-09-30');
  dom.window.importFromWorkbooks([{ name: '2026年9月月考-七年级11班.xlsx', workbook: workbookFor(82) }]);
  let saved = JSON.parse(dom.window.localStorage.getItem('hye_db_v1'));
  const september = saved.exams.find(exam => exam.name.includes('9月月考'));
  check('先新建9月月考后导入会填充这张成绩表', Boolean(september && september.examKind === 'regular'));
  check('9月月考成绩正确保存', september?.scores?.A001?.英语 === 82);
  check('入学考试成绩未被9月月考覆盖', saved.exams.find(exam => exam.examKind === 'entrance')?.scores?.A001?.英语 === 70);

  createExam('10月月考', '2026-10-31');
  dom.window.importFromWorkbooks([{ name: '2026年10月月考-七年级11班.xlsx', workbook: workbookFor(88) }]);
  saved = JSON.parse(dom.window.localStorage.getItem('hye_db_v1'));
  const october = saved.exams.find(exam => exam.name.includes('10月月考'));
  check('先新建10月月考后导入会填充这张成绩表', Boolean(october && october.examKind === 'regular'));
  check('9月和10月成绩彼此不覆盖', saved.exams.find(exam => exam.name.includes('9月月考'))?.scores?.A001?.英语 === 82 && october?.scores?.A001?.英语 === 88);
  check('考试历史保留入学、9月、10月三张表', saved.exams.length === 3);

  console.log(JSON.stringify(checks, null, 2));
  const pass = checks.every(item => item.pass);
  console.log('OVERALL:', pass ? 'PASS' : 'FAIL');
  dom.window.close();
  process.exit(pass ? 0 : 1);
}, 550);
