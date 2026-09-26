const fs = require('fs');
const path = require('path');
const { JSDOM, ResourceLoader } = require('jsdom');
const test = require('node:test');
const assert = require('node:assert/strict');

const { loadWorkbenchHtml } = require('./helpers/workbench_html');
const html = loadWorkbenchHtml();
class LocalAssetLoader extends ResourceLoader {
  fetch(url) {
    const pathname = new URL(url).pathname;
    if (pathname.startsWith('/workbench-assets/')) return Promise.resolve(fs.readFileSync(path.join(__dirname, '..', 'workbench-assets', path.basename(pathname))));
    return null;
  }
}

test('日程批量导入只接受 CSV 或 TXT 文件', () => {
  const interactions = fs.readFileSync(path.join(__dirname, '..', 'workbench-assets', 'workbench-interactions.js'), 'utf8');
  assert.match(interactions, /id="todo-import-file" accept="\.csv,\.txt"/);
  assert.match(interactions, /isAllowedTodoImportFile\(file\)/);
  assert.match(interactions, /日程导入仅支持 CSV 或 TXT 文件/);
  assert.match(interactions, /\/\\\.\(csv\|txt\)\$\/i/);
});

test('待办事项支持月历、日期时间和旧数据迁移', async () => {
  const fixture = {
    schema: 4, dataContract: 2, teacher: { name: '', subject: '初中英语' }, classes: [], settings: { excellent: 90, pass: 60, criticalLow: 55 },
    students: [], exams: [], currentExamId: '', todos: [{ id: 'legacy', title: '旧待办', priority: '高', deadline: '2030-05-19', done: false }],
    studentTags: [], recitations: [], writings: [], errors: [], critical: [], alignments: [], customs: [], dictation: {}, dictationNames: ['自定义1']
  };
  const dom = new JSDOM(html, {
    runScripts: 'dangerously', resources: new LocalAssetLoader(), url: 'https://localhost/',
    beforeParse(window) { window.localStorage.setItem('hye_db_v1', JSON.stringify(fixture)); }
  });
  await new Promise(resolve => setTimeout(resolve, 550));
  [...dom.window.document.querySelectorAll('.nav-item')].find(node => node.dataset.key === 'todo').click();
  assert.ok(dom.window.document.querySelector('.todo-calendar-title'));
  assert.ok(dom.window.document.querySelector('[data-act="todo-calendar-today"]'));
  dom.window.document.querySelector('[data-act="todo-add"]').click();
  dom.window.document.getElementById('m-todo-title').value = '安排家长会';
  dom.window.document.getElementById('m-todo-date').value = '2030-05-20';
  dom.window.document.getElementById('m-todo-time').value = '15:30';
  dom.window.document.getElementById('m-todo-notes').value = '准备签到表';
  dom.window.document.getElementById('m-todo-save').click();
  const saved = JSON.parse(dom.window.localStorage.getItem('hye_db_v1'));
  const created = saved.todos.find(todo => todo.title === '安排家长会');
  assert.equal(created.date, '2030-05-20');
  assert.equal(created.deadline, '2030-05-20');
  assert.equal(created.time, '15:30');
  assert.equal(created.notes, '准备签到表');
  assert.equal(saved.todos.find(todo => todo.id === 'legacy').date, '2030-05-19');
  assert.match(dom.window.document.body.textContent, /安排家长会/);
  dom.window.close();
});

test('课表文本导入按每行日期区分单日事项和每周重复', async () => {
  const fixture = {
    schema: 4, dataContract: 2, teacher: { name: '', subject: '初中英语' }, classes: [], settings: { excellent: 90, pass: 60, criticalLow: 55 },
    students: [], exams: [], currentExamId: '', todos: [], studentTags: [], recitations: [], writings: [], errors: [], critical: [], alignments: [], customs: [], dictation: {}, dictationNames: ['自定义1']
  };
  const dom = new JSDOM(html, {
    runScripts: 'dangerously', resources: new LocalAssetLoader(), url: 'https://localhost/',
    beforeParse(window) { window.localStorage.setItem('hye_db_v1', JSON.stringify(fixture)); }
  });
  await new Promise(resolve => setTimeout(resolve, 550));
  [...dom.window.document.querySelectorAll('.nav-item')].find(node => node.dataset.key === 'todo').click();
  dom.window.document.querySelector('[data-act="todo-import"]').click();
  assert.ok(dom.window.document.querySelector('.todo-import-grid-scroll'));
  assert.equal(dom.window.document.querySelectorAll('.todo-import-date-field').length, 2);
  dom.window.document.getElementById('m-todo-import-text').value = [
    '七年级预备役家委培训会｜周四｜00:00｜23:59｜｜2026-08-20｜2026-08-20｜',
    '新课程培训｜周三｜09:00｜11:00｜｜2026-08-26｜2026-09-30｜',
    '七年级全体家长会｜周六｜14:00｜16:00｜｜2026-08-22｜2026-08-22｜'
  ].join('\n');
  dom.window.document.querySelector('[data-act="todo-import-apply-text"]').click();
  dom.window.document.querySelector('[data-act="todo-import-confirm"]').click();
  await new Promise(resolve => setTimeout(resolve, 100));
  const saved = JSON.parse(dom.window.localStorage.getItem('hye_db_v1'));
  const singleDay = saved.todos.find(todo => todo.title === '七年级预备役家委培训会');
  const weekly = saved.todos.find(todo => todo.title === '新课程培训');
  assert.equal(saved.todos.length, 3);
  assert.equal(singleDay.date, '2026-08-20');
  assert.equal(singleDay.repeatRule, null);
  assert.equal(singleDay.allDay, true);
  assert.equal(weekly.repeatRule.frequency, 'weekly');
  assert.deepEqual(weekly.repeatRule.weekdays, [3]);
  assert.equal(weekly.repeatRule.startDate, '2026-08-26');
  assert.equal(weekly.repeatRule.endDate, '2026-09-30');
  assert.equal(saved.todos.find(todo => todo.title === '七年级全体家长会').startTime, '14:00');
  dom.window.close();
});
