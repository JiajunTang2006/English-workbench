const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..', 'workbench-assets');
const views = fs.readFileSync(path.join(root, 'workbench-views.js'), 'utf8');
const core = fs.readFileSync(path.join(root, 'workbench-core.js'), 'utf8');
const css = fs.readFileSync(path.join(root, 'workbench-app.css'), 'utf8');

test('MONI exam with no English scores explains that aggregate totals were not substituted', () => {
  assert.match(views, /String\(exam\?\.id \|\| ''\)\.startsWith\('moni:'\)/);
  // 提示文案按学科取词；英语学科下拼出来仍是「英语单科成绩尚未提供」。
  assert.match(views, /名单和考试已同步，\$\{subjectHtml\('score_single'\)\}尚未提供/);
  assert.match(core, /score_single: '英语单科成绩'/);
  assert.match(views, /没有用四科总分替代/);
  assert.match(views, /getScoredStudents\(exam, state\.students\)/);
  assert.match(css, /\.score-source-warning/);
});
