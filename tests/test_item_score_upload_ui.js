/**
 * 成绩表「上传小分」入口的行为回归。
 *
 * 覆盖四件事：
 * 1. 工具栏入口存在，粘贴「题型 + 题号（+ 小题号）+ 学号 + 得分」后调对接口、带对参数；
 * 2. 客户端的逐行校验能挡住坏数据，且不会发出请求；
 * 3. 后端整批 422（``item_score_rows_invalid``）的 ``problems`` 要逐行呈现在弹层里；
 * 4. 试卷骨架（question-metrics）能列出题型 / 题号 / 满分，并在没有已确认结构时明确说明。
 */

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const { loadWorkbenchHtml } = require('./helpers/workbench_html');

const root = path.join(__dirname, '..');

// teachmate-api.js 是外部脚本，公共 helper 不会内联它；这里单独补上，
// 否则 jsdom 里 window.teachMateApi 为空，上传入口拿不到 API。
function loadHtmlWithApi() {
  return loadWorkbenchHtml().replace(
    /<script src="\.\/workbench-assets\/(teachmate-api\.js)(?:\?[^"]*)"><\/script>/,
    (_match, filename) => `<script>\n${fs.readFileSync(path.join(root, 'workbench-assets', filename), 'utf8')}\n</script>`,
  );
}

const TERM_ID = 7;
const EXAM_KEY = 'midterm';
const EXAM_ID = 21;

const WORKSPACE_STATE = {
  schema: 4,
  teacher: { name: '教师', subject: '初中英语' },
  classes: ['711'],
  students: [
    { id: '01', name: '张三', class: '711' },
    { id: '02', name: '李四', class: '711' },
    { id: '03', name: '张三', class: '712' },
  ],
  todos: [],
  exams: [{
    id: EXAM_KEY, name: '期中考试', date: '2026-09-01', fullScore: 60,
    scores: { '01': { 英语: 45 }, '02': { 英语: 38 }, '03': { 英语: 41 } },
  }],
  archivedExams: [],
  currentExamId: EXAM_KEY,
};

const SKELETON = {
  exam_id: EXAM_ID,
  questions: [
    { question_id: 101, question_no: '1', sub_question_no: null, question_type: '听力', section_name: null, max_score: 10 },
    { question_id: 102, question_no: '2', sub_question_no: null, question_type: '听力', section_name: null, max_score: 20 },
    { question_id: 103, question_no: '3', sub_question_no: null, question_type: '阅读理解', section_name: null, max_score: 20 },
    { question_id: 104, question_no: '4', sub_question_no: 'a', question_type: '听力', section_name: null, max_score: 5 },
    { question_id: 105, question_no: '4', sub_question_no: 'b', question_type: '听力', section_name: null, max_score: 5 },
  ],
};

function createEnv(options = {}) {
  const requests = [];
  const response = (payload, status = 200) => ({
    ok: status >= 200 && status < 300,
    status,
    json: async () => payload,
    text: async () => JSON.stringify(payload),
  });
  const dom = new JSDOM(loadHtmlWithApi(), {
    runScripts: 'dangerously',
    url: `http://127.0.0.1:8765/workbench?token=test-token`,
    pretendToBeVisual: true,
    beforeParse(window) {
      window.fetch = async (url, opts = {}) => {
        const target = String(url);
        requests.push({ target, method: opts.method || 'GET', body: opts.body ? JSON.parse(opts.body) : null });
        if (target === '/api/v1/terms') return response([{ id: TERM_ID, code: '2026-S1', name: '测试学期', status: 'active' }]);
        if (target === '/api/v1/terms/current') return response({ id: TERM_ID, code: '2026-S1', name: '测试学期', status: 'active' });
        if (target === '/api/v1/runtime') return response({ version: 'test', schema: '1' });
        if (target === `/api/v1/terms/${TERM_ID}/workspace-state`) return response({ state: WORKSPACE_STATE, revision: 4, updated_at: null });
        if (target === `/api/v1/attachments?term_id=${TERM_ID}`) return response([]);
        if (target === `/api/v1/exams?term_id=${TERM_ID}`) {
          return response([{ id: EXAM_ID, source_key: EXAM_KEY, name: '期中考试', status: 'active' }]);
        }
        if (target === `/api/v1/exams/${EXAM_ID}/question-metrics?term_id=${TERM_ID}`) {
          return response(options.skeleton || SKELETON);
        }
        if (target === `/api/v1/exams/${EXAM_ID}/item-scores?term_id=${TERM_ID}` && opts.method === 'PUT') {
          return options.upload
            ? options.upload(response)
            : response({
              exam_id: EXAM_ID, paper_version: 1, paper_status: 'confirmed',
              written: 5, student_count: 3, overwritten_overrides: 0,
              total_score_missing_count: 1, question_types: ['听力', '阅读理解'],
              sections: [
                { section_name: '听力', max_score: 40, expected_items: 12, scored_items: 4, complete_student_count: 0, average_score: null },
                { section_name: '阅读理解', max_score: 20, expected_items: 3, scored_items: 1, complete_student_count: 1, average_score: 15 },
              ],
              skipped: [{ student_no: '02', question_type: '听力', question_no: '2', reason: 'previous_upload' }],
            });
        }
        return response({ detail: `unexpected ${target}` }, 404);
      };
    },
  });
  return { dom, window: dom.window, requests };
}

const wait = ms => new Promise(resolve => setTimeout(resolve, ms));

function openUploadModal(window) {
  const document = window.document;
  const navButton = [...document.querySelectorAll('.nav-item')].find(node => node.dataset.key === 'score');
  navButton.click();
  const trigger = document.querySelector('[data-act="score-item-upload"]');
  assert.ok(trigger, '成绩表工具栏应有「上传小分」按钮');
  trigger.click();
  assert.equal(document.getElementById('modalTitle').textContent, '上传逐题小分');
  return document;
}

test('上传小分：粘贴按题型的小分后调用 item-scores 并回显结果', async () => {
  const { dom, window, requests } = createEnv();
  await wait(600);
  const document = openUploadModal(window);
  await wait(50); // 等试卷骨架请求回来

  assert.match(document.querySelector('.item-score-skeleton-body').textContent, /听力/);

  document.getElementById('m-item-score-paste').value = [
    '题型\t题号\t学号\t得分',
    '听力\t1\t01\t10',
    '听力\t2\t02\t18',
    '阅读理解\t3\t01\t15',
    '听力\t4\ta\t01\t4',
    '听力\t4\tb\t02\t5',
  ].join('\n');
  document.getElementById('m-item-score-submit').click();
  await wait(60);

  const upload = requests.find(item => item.target.includes('/item-scores'));
  assert.ok(upload, '应发出小分上传请求');
  assert.equal(upload.method, 'PUT');
  assert.equal(upload.target, `/api/v1/exams/${EXAM_ID}/item-scores?term_id=${TERM_ID}`);
  assert.deepEqual(upload.body.rows, [
    { student_no: '01', question_type: '听力', question_no: '1', score: 10 },
    { student_no: '02', question_type: '听力', question_no: '2', score: 18 },
    { student_no: '01', question_type: '阅读理解', question_no: '3', score: 15 },
    { student_no: '01', question_type: '听力', question_no: '4', sub_question_no: 'a', score: 4 },
    { student_no: '02', question_type: '听力', question_no: '4', sub_question_no: 'b', score: 5 },
  ]);
  assert.equal(upload.body.overwrite_teacher_override, false);

  const result = document.getElementById('item-score-errors');
  assert.match(result.textContent, /已写入 5 条小分，覆盖 3 名学生/);
  assert.match(result.textContent, /听力/);
  assert.match(result.textContent, /1 名学生这场考试还没有总分/);
  assert.match(result.textContent, /另有 1 条被跳过/);
  dom.window.close();
});

test('上传小分：勾选覆盖时透传 overwrite_teacher_override', async () => {
  const { dom, window, requests } = createEnv();
  await wait(600);
  const document = openUploadModal(window);

  document.getElementById('m-item-score-paste').value = '听力\t1\t01\t10';
  document.getElementById('m-item-score-overwrite').checked = true;
  document.getElementById('m-item-score-submit').click();
  await wait(60);

  const upload = requests.find(item => item.target.includes('/item-scores'));
  assert.equal(upload.body.overwrite_teacher_override, true);
  dom.window.close();
});

test('上传小分：客户端逐行校验拦住坏数据且不发请求', async () => {
  const { dom, window, requests } = createEnv();
  await wait(600);
  const document = openUploadModal(window);

  document.getElementById('m-item-score-paste').value = [
    '听力\t1\t99\t10',        // 名单里没有这个学号
    '听力\t2\t01\t-3',        // 负数得分
    '听力\t3\t01',            // 列数不足
    '听力\t1\ta\t01\t10\t20', // 列数过多
    '听力\t1\t01\t10',        // 与第 6 行重复
    '听力\t1\t01\t8',
  ].join('\n');
  document.getElementById('m-item-score-submit').click();
  await wait(60);

  assert.equal(requests.filter(item => item.target.includes('/item-scores')).length, 0, '有本地错误时不应发请求');
  const text = document.getElementById('item-score-errors').textContent;
  assert.match(text, /第1行：名单里没有学号“99”对应的学生/);
  assert.match(text, /第2行：得分“-3”要填 0 或正数/);
  assert.match(text, /第3行：每行一条/);
  assert.match(text, /第4行：每行一条/);
  assert.match(text, /第6行：.*重复提交/);
  dom.window.close();
});

test('上传小分：同名不同班的学生必须用学号，不替教师猜人', async () => {
  const { dom, window, requests } = createEnv();
  await wait(600);
  const document = openUploadModal(window);

  // 「张三」在 711 和 712 各有一名，用姓名定位必须报错。
  document.getElementById('m-item-score-paste').value = '听力\t1\t张三\t10';
  document.getElementById('m-item-score-submit').click();
  await wait(60);

  assert.equal(requests.filter(item => item.target.includes('/item-scores')).length, 0);
  assert.match(document.getElementById('item-score-errors').textContent, /2 名学生都叫“张三”，请改写学号/);
  dom.window.close();
});

test('上传小分：后端整批 422 时逐行呈现 problems', async () => {
  const { dom, window } = createEnv({
    upload: response => response({
      detail: {
        code: 'item_score_rows_invalid',
        message: '2 条小分无法写入，已整批取消，未改动任何数据',
        problems: [
          { index: 0, student_no: '01', question_type: '听力', question_no: '4', reason: 'question_ambiguous', message: '该题型 + 题号在当前试卷里对应多道小题，请补上小题号（sub_question_no）' },
          { index: 1, student_no: '02', question_type: '听力', question_no: '2', reason: 'score_exceeds_max', message: '该题满分 20，提交 25' },
        ],
      },
    }, 422),
  });
  await wait(600);
  const document = openUploadModal(window);

  document.getElementById('m-item-score-paste').value = '听力\t4\t01\t5\n听力\t2\t02\t25';
  document.getElementById('m-item-score-submit').click();
  await wait(80);

  const text = document.getElementById('item-score-errors').textContent;
  assert.match(text, /2 条小分无法写入，已整批取消，未改动任何数据/);
  assert.match(text, /第1行 01 · 听力 4：该题型 \+ 题号在当前试卷里对应多道小题/);
  assert.match(text, /第2行 02 · 听力 2：该题满分 20，提交 25/);
  // 按钮必须恢复可用，教师改完能直接重传。
  assert.equal(document.getElementById('m-item-score-submit').disabled, false);
  dom.window.close();
});

test('上传小分：没有已确认试卷结构时给出可执行的说明', async () => {
  const { dom, window } = createEnv({
    skeleton: { exam_id: EXAM_ID, questions: [] },
    upload: response => response({ detail: '该考试还没有已确认的试卷结构，无法按题型写入小分' }, 404),
  });
  await wait(600);
  const document = openUploadModal(window);
  await wait(50);

  assert.match(document.querySelector('.item-score-skeleton-body').textContent, /还没有已确认的试卷结构/);

  document.getElementById('m-item-score-paste').value = '听力\t1\t01\t10';
  document.getElementById('m-item-score-submit').click();
  await wait(80);
  assert.match(document.getElementById('item-score-errors').textContent, /这场考试还没有已确认的试卷结构/);
  dom.window.close();
});
