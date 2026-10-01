// 全学科化验收（前端）：
// 老师首次进入先选学科，学科决定导航模块与界面用语；
// 有教学数据时服务端禁止跨学科切换；空工作台只更新安装级学科设置。
// 这里校验选科弹窗、模块裁剪、模块改名、文案替换与设置页即时切换。
const { JSDOM } = require('jsdom');
const { loadWorkbenchHtml } = require('./helpers/workbench_html');

const html = loadWorkbenchHtml();
const terms = [
  { id: 1, code: '2026-S1', name: '2026年第一学期', starts_on: null, ends_on: null, status: 'active' },
];
const workspaceState = {
  schema: 4,
  teacher: { name: '周老师', subject: '初中英语' },
  classes: ['711'],
  students: [{ id: '01', name: '张三', class: '711' }],
  exams: [], todos: [],
};

const CORE_MODULES = ['dash', 'stu', 'growth', 'score', 'homework', 'errors', 'todo', 'settings'];
const LANGUAGE_MODULES = ['dictation', 'recite', 'writing'];

function labelsFor(label) {
  return {
    score_total: `${label}总分`,
    entrance_score: `入学${label}`,
    score_column: `${label}成绩`,
    score_short: `${label}分数`,
    score_single: `${label}单科成绩`,
    score_trend: `${label}分数趋势`,
    score_ranking: `${label}排名`,
    exam_default: `${label}考试`,
    ability_disclaimer: `不代表${label}水平`,
  };
}

function subject(key, label, { modules, moduleLabels, questionTypes }) {
  return {
    key, label,
    teacher_subject_default: `初中${label}`,
    modules,
    module_labels: moduleLabels,
    question_types: questionTypes,
    labels: labelsFor(label),
  };
}

const subjectPayload = {
  subjects: [
    subject('chinese', '语文', {
      modules: [...CORE_MODULES, ...LANGUAGE_MODULES],
      moduleLabels: { dictation: '古诗文默写', recite: '课文背诵', writing: '作文训练' },
      questionTypes: ['基础知识', '作文'],
    }),
    subject('math', '数学', {
      modules: CORE_MODULES,
      moduleLabels: {},
      questionTypes: ['选择题', '解答题'],
    }),
    subject('english', '英语', {
      modules: [...CORE_MODULES, ...LANGUAGE_MODULES],
      moduleLabels: {},
      questionTypes: ['听力', '作文'],
    }),
  ],
  current: 'english',
  chosen: false,
  default: 'english',
};

const response = (payload, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => payload });

const errors = [];
const settingsPatches = [];
const statePuts = [];
let chosen = false;
let currentSubject = 'english';
let rejectSubjectSwitch = false;

const dom = new JSDOM(html, {
  runScripts: 'dangerously',
  url: 'http://127.0.0.1:8765/workbench?token=test-token',
  beforeParse(window) {
    window.fetch = async (url, options = {}) => {
      const target = String(url);
      const method = options.method || 'GET';
      if (target === '/api/v1/terms' && method === 'GET') return response(terms);
      if (target === '/api/v1/terms/current') return response(terms[0]);
      if (target === '/api/v1/runtime') return response({ version: '0.9.0' });
      if (target === '/api/v1/terms/1/workspace-state' && method === 'GET') {
        return response({ state: workspaceState, revision: 1, updated_at: null });
      }
      if (target === '/api/v1/terms/1/workspace-state' && method === 'PUT') {
        statePuts.push(JSON.parse(options.body));
        return response({ state: {}, revision: 2, updated_at: null });
      }
      if (target.startsWith('/api/v1/attachments')) return response([]);
      if (target.startsWith('/api/v1/school-sync/moni/config')) return response({ configured: false });
      if (target.startsWith('/api/v1/school-sync/sources')) return response([]);
      if (target.startsWith('/api/v1/ai/')) return response({ detail: 'unavailable' }, 404);
      if (target.startsWith('/api/v1/growth/teacher-presets')) {
        return response({
          term_id: 1, rule_version: 'growth-v4',
          teachers: [{ id: 1, name: '默认教师', is_active: true }],
          active_teacher: { id: 1, name: '默认教师' },
          presets: [{ type: 'task_completed', label: '完成学习任务', points: 2, default_points: 2, visible: true, customized: false }],
          max_points: 10,
          conflict: { conflict: false, teachers: 1, divergent_types: [] },
        });
      }
      if (target === '/api/v1/subjects' && method === 'GET') {
        return response({ ...subjectPayload, current: currentSubject, chosen });
      }
      if (target === '/api/v1/settings' && method === 'PATCH') {
        const body = JSON.parse(options.body);
        settingsPatches.push(body);
        if (rejectSubjectSwitch && body.subject_key) return response({ detail: '已有考试，不能切换学科' }, 409);
        if (body.subject_key) {
          currentSubject = body.subject_key;
          chosen = true;
        }
        return response({ subject_key: currentSubject, subject: '初中英语' });
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
const navKeys = () => [...doc.querySelectorAll('.nav-item')].map(item => item.dataset.key);
const navText = (key) => navItem(key)?.textContent.trim() || '';
const modalTitle = () => doc.getElementById('modalTitle').textContent;
const modalOpen = () => doc.getElementById('modal').classList.contains('show');

(async () => {
  await wait(80);

  // 1. 首次进入：弹窗选科
  check('首次进入弹出选科弹窗', modalOpen() && modalTitle() === '选择任教学科');
  const choices = [...doc.querySelectorAll('#modal input[name="subject-choice"]')];
  check('弹窗列出全部学科', choices.length === 3 && choices.map(input => input.value).join(',') === 'chinese,math,english');
  check('默认选中当前学科', choices.find(input => input.checked)?.value === 'english');

  // 2. 选数学：模块被裁剪，文案跟着换
  choices.find(input => input.value === 'math').checked = true;
  doc.querySelector('#modal [data-act="subject-confirm"]').click();
  await wait(120);

  check('选科会写回安装级设置', settingsPatches.length === 1 && settingsPatches[0].subject_key === 'math');
  check('选完关闭弹窗', !modalOpen());
  check('数学看不到默写/背诵/写作', navKeys().join(',') === 'dash,stu,growth,score,homework,errors,todo,settings');
  check('数学保留核心模块', Boolean(navItem('dash')) && Boolean(navItem('score')) && Boolean(navItem('settings')));

  navItem('score').click();
  await wait(60);
  check('成绩页列名换成数学总分', doc.getElementById('workarea').textContent.includes('数学总分'));
  check('成绩页不再出现英语总分', !doc.getElementById('workarea').textContent.includes('英语总分'));

  navItem('stu').click();
  await wait(60);
  check('学生表头换成入学数学', doc.getElementById('workarea').textContent.includes('入学数学'));

  // 3. 设置页：下拉改学科立即生效
  navItem('settings').click();
  await wait(120);
  const select = doc.getElementById('sett-subject-key');
  check('设置页有任教学科下拉', Boolean(select) && select.value === 'math');
  check('设置页说明模块开关口径', doc.body.textContent.includes('默写、背诵、写作只对语文与英语开放'));
  check('数学工作区不显示英语 MONI 同步输入', !doc.getElementById('moni-api-key') && doc.getElementById('workarea').textContent.includes('MONI 目前只提供英语单科同步'));

  select.value = 'chinese';
  select.dispatchEvent(new window.Event('change', { bubbles: true }));
  await wait(120);

  check('下拉改学科立即写回设置', settingsPatches.length === 2 && settingsPatches[1].subject_key === 'chinese');
  check('语文重新出现默写/背诵/写作', navKeys().includes('dictation') && navKeys().includes('recite') && navKeys().includes('writing'));
  check('语文模块标题按学科改名', navText('dictation').includes('古诗文默写') && navText('recite').includes('课文背诵') && navText('writing').includes('作文训练'));
  check('学科名跟随学科默认值', doc.getElementById('sett-subject').value === '初中语文');
  check('顶栏标题跟着学科换', doc.getElementById('appTitle').textContent.includes('初中语文工作台'));

  // 4. 英语回退：切回英语时模块名恢复原样
  const selectAfter = doc.getElementById('sett-subject-key');
  check('切换后设置页下拉已重绘', Boolean(selectAfter) && selectAfter.value === 'chinese');
  selectAfter.value = 'english';
  selectAfter.dispatchEvent(new window.Event('change', { bubbles: true }));
  await wait(120);
  check('切回英语模块名恢复默认', navText('dictation').includes('默写成绩') && navText('writing').includes('写作成绩'));
  check('切回英语后文案恢复', navText('recite').includes('背诵成绩'));
  navItem('score').click();
  await wait(60);
  check('英语成绩页仍是英语总分', doc.getElementById('workarea').textContent.includes('英语总分'));

  // 5. 选过学科之后不再重复弹窗
  check('二次进入不再弹选科窗口', !modalOpen());
  check('切换学科不额外改写工作台快照', statePuts.length === 0);
  // 已有教学数据时服务端拒绝切换；界面和设置下拉保持原学科。
  rejectSubjectSwitch = true;
  navItem('settings').click();
  await wait(30);
  const rejectedSelect = doc.getElementById('sett-subject-key');
  rejectedSelect.value = 'math';
  rejectedSelect.dispatchEvent(new window.Event('change', { bubbles: true }));
  await wait(90);
  check('服务端拒绝后前端学科保持不变', currentSubject === 'english' && doc.getElementById('sett-subject-key').value === 'english' && navKeys().includes('dictation'));
  check('全程无脚本错误', errors.length === 0);

  const passed = results.every(item => item.pass);
  console.log(JSON.stringify(results, null, 2));
  if (errors.length) console.log('ERRORS:', JSON.stringify(errors, null, 2));
  console.log('OVERALL:', passed ? 'PASS' : 'FAIL');
  dom.window.close();
  process.exit(passed ? 0 : 1);
})();
