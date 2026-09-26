const { JSDOM } = require('jsdom');
const { loadWorkbenchHtml } = require('./helpers/workbench_html');
const { createWorkbenchFixture } = require('./fixtures/workbench_fixture');

const html = loadWorkbenchHtml();
const fixture = createWorkbenchFixture();
fixture.exams = [{
  id: 'workflow-exam', name: '流程测试考试', date: '2026-08-01', fullScore: 100,
  type: 'english_total', examKind: 'regular', tierLines: { a: 90, b: 75, c: 60 }, classGradeRanks: {},
  scores: Object.fromEntries(fixture.students.map(student => [student.id, { 英语: Number(student.english) || '', classAtExam: student.class, attendanceStatus: 'present' }])),
}];
fixture.currentExamId = 'workflow-exam';
const errors = [];
const dom = new JSDOM(html, {
  runScripts: 'dangerously',
  url: 'https://localhost/',
  beforeParse(window) { window.localStorage.setItem('hye_db_v1', JSON.stringify(fixture)); },
});
const window = dom.window;
const document = window.document;
window.onerror = (message, url, line) => errors.push({ message, line });

const results = [];
const check = (name, pass) => results.push({ name, pass: Boolean(pass) });
const goto = key => [...document.querySelectorAll('.nav-item')].find(node => node.dataset.key === key).click();
const blurWithValue = (cell, value) => {
  cell.textContent = value;
  cell.dispatchEvent(new window.FocusEvent('blur', { bubbles: true }));
};

setTimeout(() => {
  goto('score');
  const firstCell = document.querySelector('[data-act="score-edit"]');
  const studentId = firstCell.dataset.sid;
  const original = firstCell.textContent.trim();
  const storageBeforeEdit = window.localStorage.getItem('hye_db_v1');

  blurWithValue(firstCell, '999');
  check('非法成绩在单元格内明确提示', firstCell.classList.contains('score-invalid') && firstCell.getAttribute('aria-invalid') === 'true');
  check('非法成绩不会形成待保存修改', !document.querySelector('.unsaved-indicator'));

  blurWithValue(firstCell, '88');
  check('合法修改显示未保存状态', document.querySelector('.unsaved-indicator')?.textContent.includes('1 项未保存'));
  check('修改单元格带未保存标识', document.querySelector(`[data-act="score-edit"][data-sid="${studentId}"]`)?.classList.contains('score-dirty'));
  let persisted = JSON.parse(window.localStorage.getItem('hye_db_v1'));
  check('暂存修改尚未写入数据库', window.localStorage.getItem('hye_db_v1') === storageBeforeEdit);

  document.querySelector('[data-act="score-undo"]').click();
  check('撤销恢复原成绩和已保存状态', document.querySelector(`[data-act="score-edit"][data-sid="${studentId}"]`)?.textContent.trim() === original && !document.querySelector('.unsaved-indicator'));

  blurWithValue(document.querySelector(`[data-act="score-edit"][data-sid="${studentId}"]`), '88');
  document.querySelector('[data-act="score-save-pending"]').click();

  setTimeout(() => {
    persisted = JSON.parse(window.localStorage.getItem('hye_db_v1'));
    const savedExam = persisted.exams.find(exam => exam.id === persisted.currentExamId) || persisted.exams[0];
    check('显式保存后成绩写入数据库', savedExam?.scores?.[studentId]?.英语 === 88);
    check('保存后清除未保存标识', !document.querySelector('.unsaved-indicator'));

    const visibleStudents = [...document.querySelectorAll('[data-act="score-edit"]')].slice(0, 2).map(cell => cell.dataset.sid);
    document.querySelector('[data-act="score-batch-paste"]').click();
    document.getElementById('m-score-paste').value = `${visibleStudents[0]}\t81\n${visibleStudents[1]}\t82`;
    document.getElementById('m-score-paste-stage').click();
    check('批量粘贴通过校验后形成待保存修改', document.querySelector('.unsaved-indicator')?.textContent.includes('2 项未保存'));

    document.querySelector('[data-act="score-batch-paste"]').click();
    document.getElementById('m-score-paste').value = `${visibleStudents[0]}\t999`;
    document.getElementById('m-score-paste-stage').click();
    check('批量粘贴非法值阻止暂存并定位行号', document.getElementById('batch-paste-errors').textContent.includes('第1行') && document.getElementById('batch-paste-errors').textContent.includes('0-100'));

    check('全程无脚本错误', errors.length === 0);
    const pass = results.every(item => item.pass);
    console.log(JSON.stringify(results, null, 2));
    console.log('Script errors:', errors.length ? errors : 'none');
    console.log('OVERALL:', pass ? 'PASS' : 'FAIL');
    dom.window.close();
    process.exit(pass ? 0 : 1);
  }, 50);
}, 650);
