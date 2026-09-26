const test = require('node:test');
const assert = require('node:assert/strict');
const { loadWorkbenchHtml } = require('./helpers/workbench_html');

const html = loadWorkbenchHtml();

test('常用页面不展示重复操作说明或未配置功能', () => {
  assert.doesNotMatch(html, /点击卡片切换|导入数据会填充|观察依据来自|名单随最近考试|当前无需处理/);
  assert.doesNotMatch(html, /data-tab="suggest"|教学建议功能暂未配置/);
});

test('数据安全相关说明仍然保留', () => {
  assert.match(html, /此操作无法通过工作台恢复/);
  assert.match(html, /相关教学和成绩记录会继续保留/);
});
