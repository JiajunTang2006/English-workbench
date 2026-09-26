const fs = require('fs');
const path = require('path');
const test = require('node:test');
const assert = require('node:assert/strict');

const { loadWorkbenchHtml } = require('./helpers/workbench_html');
const html = loadWorkbenchHtml();

test('正式 HTML 不内置学生名单或家长电话', () => {
  assert.doesNotMatch(html, /DEFAULT_STUDENTS|DEFAULT_CLASSES/);
  assert.doesNotMatch(html, /\{"id":"\d+","name":"/);
  assert.doesNotMatch(html, /"phone":"1\d{10}"/);
  assert.match(html, /classes:\s*\[\],\s*\n\s*settings:/);
  assert.match(html, /students:\s*\[\],\s*\n\s*archivedStudents:\s*\[\],\s*\n\s*exams:/);
});

test('正式运行逻辑不内置具体班级编号', () => {
  const generator = fs.readFileSync(path.join(__dirname, '..', 'tools', 'gen_dictation_data.js'), 'utf8');
  assert.doesNotMatch(html, /\b(?:711|712)\b/);
  assert.doesNotMatch(generator, /\b(?:711|712)\b/);
});
