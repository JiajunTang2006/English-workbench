const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const requests = [];
let modal = '';
const context = {
  window: {}, document: { addEventListener() {} },
  DATABASE_MODE: true, currentTermId: 1,
  render() {}, showToast() {}, closeModal() {},
  openModal(_title, body) { modal = body; },
  escapeHtml(value) { return String(value); },
  escapeAttr(value) { return String(value); },
  apiRequest(url, options) {
    return new Promise((resolve, reject) => requests.push({ url, options, resolve, reject }));
  },
};
vm.createContext(context);
const source = fs.readFileSync('workbench-assets/workbench-growth.js', 'utf8')
  .replace('window.renderGrowth = renderGrowth;',
    'window.inspectGrowth = () => ({ loaded: growthLoaded, data: growthData }); ' +
    'window.openQuick = growthOpenQuick; window.recordItems = growthRecordItems; ' +
    'window.renderGrowth = renderGrowth;');
vm.runInContext(source, context);

(async () => {
  const first = context.window.loadGrowthForest();
  context.currentTermId = 2;
  context.window.growthInvalidate();
  const second = context.window.loadGrowthForest();
  assert.equal(requests.length, 2);
  requests[1].resolve({ term_id: 2, rule_version: 'growth-v4', students: [
    { student_id: 10, name: '甲' }], summary: { student_count: 1, total_points: 0,
      average_points: 0, active_this_week: 0, blossomed_count: 0 } });
  requests[0].resolve({ term_id: 1, students: [] });
  await Promise.all([first, second]);
  assert.equal(context.window.inspectGrowth().data.term_id, 2);
  context.window.openQuick(10);
  assert.match(modal, /教师手工加分/);
  assert.match(modal, /growthCustomPoints/);

  const saved = context.window.recordItems([10], 'teacher_bonus', '主动帮助同学', 3);
  await context.window.recordItems([10], 'teacher_bonus', '主动帮助同学', 3);
  const posts = requests.filter(item => item.url.endsWith('/activities'));
  assert.equal(posts.length, 1);
  const body = JSON.parse(posts[0].options.body);
  assert.equal(body.items[0].points, 3);
  posts[0].reject(new Error('network'));
  await saved;
  const retry = context.window.recordItems([10], 'teacher_bonus', '主动帮助同学', 3);
  const retried = requests.filter(item => item.url.endsWith('/activities'));
  assert.equal(retried.length, 2);
  assert.equal(JSON.parse(retried[1].options.body).request_id, body.request_id);
  retried[1].reject(new Error('network'));
  await retry;
  console.log('growth race and manual bonus: PASS');
})().catch(error => { console.error(error); process.exitCode = 1; });
