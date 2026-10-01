// 设置页「成长加分标准」验收（补丁 A 前端）：
// 每位老师各自一套补录分值，设置页负责维护教师档案与分值；分值不进计分引擎，
// 已入账的历史记录不受影响。这里校验读取、保存、切换、恢复默认、删除与越界拦截。
const { JSDOM } = require('jsdom');
const { loadWorkbenchHtml } = require('./helpers/workbench_html');

const html = loadWorkbenchHtml();
const terms = [
  { id: 1, code: '2026-S1', name: '2026年第一学期', starts_on: null, ends_on: null, status: 'active' },
];
const workspaceState = {
  schema: 4,
  teacher: { name: '教师', subject: '初中英语' },
  classes: ['711'],
  students: [{ id: '01', name: '张三', class: '711' }],
  exams: [], todos: [],
};

const teacherRows = [
  { id: 1, name: '默认教师', is_active: true },
  { id: 2, name: '王老师', is_active: false },
];

function presetsFor(taskPoints, customized) {
  return [
    { type: 'task_completed', label: '完成学习任务', points: taskPoints, default_points: 2, visible: true, customized },
    { type: 'correction_verified', label: '完成订正并确认', points: 2, default_points: 2, visible: true, customized: false },
    { type: 'teacher_observation', label: '课堂/阅读表现', points: 1, default_points: 1, visible: true, customized: false },
  ];
}

const response = (payload, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => payload });

const errors = [];
const putBodies = [];
const activateCalls = [];
const createCalls = [];
const deleteCalls = [];
const standardRequests = [];

// 服务端状态：当前教师、每位老师的 task_completed 分值。
let activeTeacherId = 1;
let taskPoints = 3;
let taskCustomized = true;

function standardPayload() {
  const activeName = activeTeacherId === 2 ? '王老师' : '默认教师';
  return {
    term_id: 1,
    rule_version: 'growth-v4',
    teachers: teacherRows.map(row => ({ ...row, is_active: row.id === activeTeacherId })),
    active_teacher: { id: activeTeacherId, name: activeName },
    presets: presetsFor(taskPoints, taskCustomized),
    max_points: 10,
    // 王老师（5 分）与默认教师（3 分）对同一类别给了不同分值 → 标准分叉。
    conflict: { conflict: activeTeacherId === 1 && taskPoints !== 5, teachers: 2, divergent_types: ['task_completed'] },
  };
}

const dom = new JSDOM(html, {
  runScripts: 'dangerously',
  url: 'http://127.0.0.1:8765/workbench?token=test-token',
  beforeParse(window) {
    window.fetch = async (url, options = {}) => {
      const target = String(url);
      const method = options.method || 'GET';
      if (target === '/api/v1/terms' && method === 'GET') return response(terms);
      if (target === '/api/v1/terms/current') return response(terms[0]);
      if (target === '/api/v1/runtime') return response({ version: '0.7.0' });
      if (target === '/api/v1/terms/1/workspace-state') return response({ state: workspaceState, revision: 1, updated_at: null });
      if (target.startsWith('/api/v1/school-sync/moni/config')) return response({ configured: false });
      if (target.startsWith('/api/v1/ai/')) return response({ detail: 'unavailable' }, 404);

      if (target.startsWith('/api/v1/growth/teacher-presets')) {
        if (method === 'GET') {
          standardRequests.push('GET');
          return response(standardPayload());
        }
        if (method === 'PUT') {
          const body = JSON.parse(options.body);
          putBodies.push({ url: target, body });
          if (Object.keys(body.presets).length === 0) {
            taskPoints = 2;
            taskCustomized = false;
          } else {
            taskPoints = Number(body.presets.task_completed.points);
            taskCustomized = taskPoints !== 2;
          }
          return response(standardPayload());
        }
      }

      const activate = target.match(/^\/api\/v1\/growth\/teachers\/(\d+)\/activate$/);
      if (activate && method === 'POST') {
        activateCalls.push(Number(activate[1]));
        activeTeacherId = Number(activate[1]);
        return response({ teachers: teacherRows, active_teacher: { id: activeTeacherId, name: '王老师' }, max_points: 10 });
      }
      if (target === '/api/v1/growth/teachers' && method === 'POST') {
        const body = JSON.parse(options.body);
        createCalls.push(body);
        return response({ teachers: teacherRows, active_teacher: { id: 2, name: body.name }, max_points: 10 });
      }
      const remove = target.match(/^\/api\/v1\/growth\/teachers\/(\d+)$/);
      if (remove && method === 'DELETE') {
        deleteCalls.push(Number(remove[1]));
        activeTeacherId = 1;
        return response({ teachers: [teacherRows[0]], active_teacher: { id: 1, name: '默认教师' }, max_points: 10 });
      }
      return response({ detail: `unexpected ${method} ${target}` }, 404);
    };
  },
});
const window = dom.window;
const doc = window.document;
window.onerror = (message, line) => errors.push({ message, line });

const results = [];
const check = (name, value) => results.push({ name, pass: Boolean(value) });
const wait = (ms) => new Promise(resolve => setTimeout(resolve, ms));
const navItem = (key) => [...doc.querySelectorAll('.nav-item')].find(item => item.dataset.key === key);

(async () => {
  await wait(60);

  navItem('settings').click();
  await wait(90);

  const card = doc.getElementById('growth-preset-rows');
  check('设置页渲染成长加分标准编辑器', Boolean(card) && doc.body.textContent.includes('成长加分标准'));
  check('编辑器不显示重复解释', !doc.body.textContent.includes('已入账的历史记录不会因此改变'));

  const select = doc.getElementById('growth-teacher-select');
  check('教师下拉来自后端列表', select && select.options.length === 2);
  check('当前教师被标记', select && select.options[0].textContent.includes('默认教师'));
  check('分值上限提示写入输入框', Boolean(card.querySelector('input[type="number"]')) &&
    card.querySelector('input[type="number"]').max === '10');
  check('已自定义类别标出出厂值', card.textContent.includes('已自定义 · 出厂 2'));

  const rows = [...card.querySelectorAll('[data-growth-preset]')];
  check('每个可补录类别一行', rows.length === 3);
  const taskRow = rows.find(row => row.dataset.growthPreset === 'task_completed');
  check('分值来自后端预设', taskRow.querySelector('[data-role="points"]').value === '3');

  // 越界分值必须被拦下，且不发出保存请求
  const beforeInvalid = putBodies.length;
  taskRow.querySelector('[data-role="points"]').value = '99';
  doc.querySelector('[data-act="growth-preset-save"]').click();
  await wait(40);
  check('越界分值不发出保存请求', putBodies.length === beforeInvalid);
  check('越界分值给出错误提示', doc.getElementById('growth-preset-status').textContent.includes('1–10'));
  check('越界分值状态标红', doc.getElementById('growth-preset-status').classList.contains('growth-status-error'));

  // 正常保存：把「完成学习任务」改成 6 分
  taskRow.querySelector('[data-role="points"]').value = '6';
  doc.querySelector('[data-act="growth-preset-save"]').click();
  await wait(60);
  check('保存发出 PUT 且带上当前教师', putBodies.length === beforeInvalid + 1 &&
    putBodies[putBodies.length - 1].url.includes('teacher_id=1'));
  check('保存请求体包含分值', putBodies[putBodies.length - 1].body.presets.task_completed.points === 6);
  check('保存后回读界面显示新分值',
    doc.querySelector('[data-growth-preset="task_completed"] [data-role="points"]').value === '6');
  check('保存后清除错误状态', !doc.getElementById('growth-preset-status').classList.contains('growth-status-error'));

  // 多套标准分叉时必须显式提示不可比
  check('多套标准分叉时提示不宜横向比较', doc.getElementById('growth-preset-conflict').textContent.includes('不宜直接横向比较'));

  // 切换教师：下拉即激活，并按新教师标准刷新
  const activateBefore = activateCalls.length;
  const standardBefore = standardRequests.length;
  select.value = '2';
  select.dispatchEvent(new window.Event('change', { bubbles: true }));
  await wait(90);
  check('切换教师调用激活接口', activateCalls.length === activateBefore + 1 && activateCalls[activateCalls.length - 1] === 2);
  check('切换后重新读取加分标准', standardRequests.length === standardBefore + 1);
  check('切换后状态栏标出新教师', doc.getElementById('growth-preset-status').textContent.includes('王老师'));

  // 新增教师：默认切换为当前
  doc.getElementById('growth-teacher-new').value = '李老师';
  doc.querySelector('[data-act="growth-teacher-add"]').click();
  await wait(60);
  check('新增教师提交姓名并默认激活', createCalls.length === 1 && createCalls[0].name === '李老师' && createCalls[0].activate === true);
  check('新增后清空输入框', doc.getElementById('growth-teacher-new').value === '');

  // 恢复默认分值：提交空 presets
  doc.querySelector('[data-act="growth-preset-reset"]').click();
  await wait(60);
  const resetBody = putBodies[putBodies.length - 1].body;
  check('恢复默认提交空预设', Object.keys(resetBody.presets).length === 0);
  check('恢复默认后回到出厂值',
    doc.querySelector('[data-growth-preset="task_completed"] [data-role="points"]').value === '2');
  check('恢复默认后不再标记已自定义',
    !doc.querySelector('[data-growth-preset="task_completed"]').textContent.includes('已自定义'));

  // 删除教师：先弹确认，确认后才发请求
  doc.querySelector('[data-act="growth-teacher-remove"]').click();
  await wait(40);
  check('删除教师先弹确认', doc.getElementById('modalBody').textContent.includes('加分标准档案') &&
    Boolean(doc.querySelector('[data-act="growth-teacher-remove-confirm"]')));
  const deleteBefore = deleteCalls.length;
  doc.querySelector('[data-act="growth-teacher-remove-confirm"]').click();
  await wait(60);
  check('确认后删除选中的教师', deleteCalls.length === deleteBefore + 1);

  check('全程无脚本错误', errors.length === 0);

  const passed = results.every(item => item.pass);
  console.log(JSON.stringify(results, null, 2));
  if (!passed) console.log('FAILURES:', JSON.stringify(results.filter(item => !item.pass), null, 2));
  console.log('OVERALL:', passed ? 'PASS' : 'FAIL');
  dom.window.close();
  process.exit(passed ? 0 : 1);
})();
