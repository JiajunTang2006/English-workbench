const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..', 'workbench-assets');
const views = fs.readFileSync(path.join(root, 'workbench-views.js'), 'utf8');
const css = fs.readFileSync(path.join(root, 'workbench-app.css'), 'utf8');

test('MONI exam with no English scores explains that aggregate totals were not substituted', () => {
  assert.match(views, /String\(exam\?\.id \|\| ''\)\.startsWith\('moni:'\)/);
  assert.match(views, /英语单科成绩尚未提供/);
  assert.match(views, /没有用四科总分替代/);
  assert.match(views, /getScoredStudents\(exam, state\.students\)/);
  assert.match(css, /\.score-source-warning/);
});
