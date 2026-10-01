// 成长森林（成长树）前端模块验收：导航注册、森林卡片、单人明细、
// 范围切换清理批量选择、学期切换作废缓存。
//
// 事实全部来自 /api/v1/growth/*，前端不实现第二份计分规则；缺失证据必须
// 显示「待记录 / 样本较少」，不能用 0 顶替。
const { JSDOM } = require('jsdom');
const { loadWorkbenchHtml } = require('./helpers/workbench_html');

const html = loadWorkbenchHtml();
const terms = [
  { id: 1, code: '2026-S1', name: '2026年第一学期', starts_on: null, ends_on: null, status: 'active' },
  { id: 2, code: '2026-S2', name: '2026年第二学期', starts_on: null, ends_on: null, status: 'active' },
];
const states = {
  1: { schema: 4, teacher: { name: '教师', subject: '初中英语' }, classes: ['711', '712'], students: [
    { id: '01', name: '张三', class: '711' }, { id: '02', name: '李四', class: '712' }], exams: [], todos: [] },
  2: { schema: 4, teacher: { name: '教师', subject: '初中英语' }, classes: ['721'], students: [
    { id: '03', name: '王五', class: '721' }], exams: [], todos: [] },
};

function forestFor(termId) {
  if (termId === 2) {
    return {
      term_id: 2, class_id: null, rule_version: 'growth-v3',
      manual_entry_policy: 'teacher_confirmed_v1',
      // 第二位老师给了不同的分值：多套标准分叉，界面必须显式说明不宜横向比较。
      active_teacher: { id: 2, name: '王老师' },
      manual_presets: [
        { type: 'task_completed', label: '完成学习任务', points: 5, default_points: 2, visible: true, customized: true },
        { type: 'spaced_review', label: '间隔复习/达标复测', points: 3, default_points: 3, visible: true, customized: false },
      ],
      standard_conflict: { conflict: true, teachers: 2, divergent_types: ['task_completed'] },
      stages: [
        { min: 0, name: '种子', icon: '🌰' }, { min: 30, name: '发芽', icon: '🌱' },
        { min: 80, name: '小树苗', icon: '🌿' }, { min: 160, name: '茁壮成长', icon: '🪴' },
        { min: 270, name: '开花', icon: '🌸' }, { min: 400, name: '结果', icon: '🍎' },
        { min: 560, name: '森林之星', icon: '🌳' },
      ],
      summary: { student_count: 1, total_points: 0, average_points: 0, active_this_week: 0, blossomed_count: 0 },
      students: [{
        student_id: 3, student_no: '03', name: '王五', class_id: 3, class_name: '721',
        term_points: 0, legacy_points: 0, stage_index: 0, stage_name: '种子', stage_icon: '🌰',
        stage_min: 0, next_stage: { min: 30, name: '发芽', icon: '🌱' }, stage_progress: 0,
        week_points: 0, coverage: { recorded_events: 0, active_days: 0 },
        dimensions: { vocabulary: { status: 'no_evidence' }, grammar: { status: 'no_evidence' },
          reading: { status: 'no_evidence' }, listening: { status: 'no_evidence' },
          writing: { status: 'no_evidence' } },
        source_revision: 'rev-term2',
      }],
    };
  }
  return {
    term_id: 1, class_id: null, rule_version: 'growth-v3',
    manual_entry_policy: 'teacher_confirmed_v1',
    // 补录预设由后端按「当前教师」下发：前端不再自带第二份分值表，
    // 因此把「完成学习任务」改成 3 分，界面上的快捷按钮与默认输入值都应跟着变。
    active_teacher: { id: 1, name: '默认教师' },
    manual_presets: [
      { type: 'task_completed', label: '完成学习任务', points: 3, default_points: 2, visible: true, customized: true },
      { type: 'correction_verified', label: '完成订正并确认', points: 2, default_points: 2, visible: true, customized: false },
      { type: 'spaced_review', label: '间隔复习/达标复测', points: 3, default_points: 3, visible: true, customized: false },
      { type: 'teacher_observation', label: '课堂/阅读表现', points: 1, default_points: 1, visible: true, customized: false },
      { type: 'weekly_goal', label: '达成个人周目标', points: 2, default_points: 2, visible: true, customized: false },
    ],
    standard_conflict: { conflict: false, teachers: 1, divergent_types: [] },
    stages: [
      { min: 0, name: '种子', icon: '🌰' }, { min: 30, name: '发芽', icon: '🌱' },
      { min: 80, name: '小树苗', icon: '🌿' }, { min: 160, name: '茁壮成长', icon: '🪴' },
      { min: 270, name: '开花', icon: '🌸' }, { min: 400, name: '结果', icon: '🍎' },
      { min: 560, name: '森林之星', icon: '🌳' },
    ],
    summary: { student_count: 2, total_points: 36, average_points: 18, active_this_week: 1, blossomed_count: 0 },
    students: [
      {
        student_id: 1, student_no: '01', name: '张三', class_id: 1, class_name: '711',
        term_points: 36, legacy_points: 6, stage_index: 1, stage_name: '发芽', stage_icon: '🌱',
        stage_min: 30, next_stage: { min: 80, name: '小树苗', icon: '🌿' }, stage_progress: 0.12,
        week_points: 6, coverage: { recorded_events: 5, active_days: 3 },
        dimensions: { vocabulary: { status: 'ok' }, grammar: { status: 'no_evidence' },
          reading: { status: 'insufficient_comparable_history' }, listening: { status: 'no_evidence' },
          writing: { status: 'no_evidence' } },
        source_revision: 'rev-term1-a',
      },
      {
        student_id: 2, student_no: '02', name: '李四', class_id: 2, class_name: '712',
        term_points: 0, legacy_points: 0, stage_index: 0, stage_name: '种子', stage_icon: '🌰',
        stage_min: 0, next_stage: { min: 30, name: '发芽', icon: '🌱' }, stage_progress: 0,
        week_points: 0, coverage: { recorded_events: 0, active_days: 0 },
        dimensions: { vocabulary: { status: 'no_evidence' }, grammar: { status: 'no_evidence' },
          reading: { status: 'no_evidence' }, listening: { status: 'no_evidence' },
          writing: { status: 'no_evidence' } },
        source_revision: 'rev-term1-b',
      },
    ],
  };
}

function detailFor(studentId) {
  return {
    student_id: studentId, student_no: '01', name: '张三', class_id: 1, class_name: '711',
    term_id: 1,
    snapshot: {
      student_id: studentId, term_id: 1, rule_version: 'growth-v3',
      term_points: 36, legacy_points: 6, stage_index: 1, stage_name: '发芽', stage_icon: '🌱',
      stage_min: 30, next_stage: { min: 80, name: '小树苗', icon: '🌿' }, stage_progress: 0.12,
      week_key: '2026-W14', week_points: 6,
      stages: [
        { min: 0, name: '种子', icon: '🌰' }, { min: 30, name: '发芽', icon: '🌱' },
        { min: 80, name: '小树苗', icon: '🌿' },
      ],
      coverage: { recorded_events: 5, active_days: 3, first_record_date: '2026-04-01', last_record_date: '2026-04-05' },
      dimensions: {
        vocabulary: { status: 'ok', value: 0.72, observations: 4, trend: 'up' },
        grammar: { status: 'no_evidence', value: null, observations: 0 },
        reading: { status: 'insufficient_comparable_history', value: null, observations: 2 },
        listening: { status: 'no_evidence', value: null, observations: 0 },
        writing: { status: 'no_evidence', value: null, observations: 0 },
      },
      source_revision: 'rev-term1-a', computed_at: '2026-04-02T10:00:00+00:00',
    },
    records: [
      { event_id: 11, event_type: 'legacy_manual', event_label: '历史手工记录',
        business_date: '2025-09-30', note: '旧版迁移', legacy_points: 6, applied_points: 0,
        cap_reason: 'legacy', reversible: true, proposed_points: 0 },
      { event_id: 12, event_type: 'task_completed', event_label: '完成学习任务',
        business_date: '2026-04-02', note: '完成课堂任务', legacy_points: null, applied_points: 2,
        cap_reason: 'none', reversible: true, proposed_points: 2, actor: '王老师' },
      { event_id: 13, event_type: 'task_completed', event_label: '完成学习任务',
        business_date: '2026-04-02', note: '完成课堂任务', legacy_points: null, applied_points: 0,
        cap_reason: 'category_daily_awards', reversible: false, proposed_points: 2 },
      { event_id: 14, event_type: 'exam_completed', event_label: '参加可比测评',
        business_date: '2026-04-05', note: '参加单元测', legacy_points: null, applied_points: 4,
        cap_reason: 'none', reversible: true, proposed_points: 4,
        progress_points: 2, baseline_rate: 0.35, score_rate: 0.45, exam_id: 7 },
    ],
    pending_corrections: [],
    history: [{ term_id: 0, term_name: '2025 秋季', term_points: 18, stage_name: '发芽' }],
  };
}

let activeTermId = 1;
const forestRequests = [];
const response = (payload, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => payload });

const errors = [];
const dom = new JSDOM(html, {
  runScripts: 'dangerously',
  url: 'http://127.0.0.1:8765/workbench?token=test-token',
  beforeParse(window) {
    window.fetch = async (url, options = {}) => {
      const target = String(url);
      if (target === '/api/v1/terms' && (!options.method || options.method === 'GET')) return response(terms);
      if (target === '/api/v1/terms/current' && options.method === 'PUT') {
        activeTermId = Number(JSON.parse(options.body).term_id);
        return response(terms.find(term => term.id === activeTermId));
      }
      if (target === '/api/v1/terms/current') return response(terms.find(term => term.id === activeTermId));
      if (target === '/api/v1/runtime') return response({ version: '0.7.0' });
      const workspace = target.match(/^\/api\/v1\/terms\/(\d+)\/workspace-state$/);
      if (workspace) return response({ state: states[Number(workspace[1])], revision: 1, updated_at: null });
      const forest = target.match(/^\/api\/v1\/growth\/forest\?term_id=(\d+)$/);
      if (forest) {
        const termId = Number(forest[1]);
        forestRequests.push(termId);
        return response(forestFor(termId));
      }
      const detail = target.match(/^\/api\/v1\/growth\/students\/(\d+)\?term_id=(\d+)$/);
      if (detail) return response(detailFor(Number(detail[1])));
      return response({ detail: `unexpected ${target}` }, 404);
    };
  },
});
const window = dom.window;
const doc = window.document;
window.onerror = (message, url, line) => errors.push({ message, line });

const results = [];
const check = (name, value) => results.push({ name, pass: Boolean(value) });

function wait(ms) { return new Promise(resolve => setTimeout(resolve, ms)); }
function navItem(key) { return [...doc.querySelectorAll('.nav-item')].find(item => item.dataset.key === key); }

(async () => {
  await wait(60);

  check('导航注册成长森林入口', Boolean(navItem('growth')) && navItem('growth').textContent.includes('成长森林'));
  check('成长森林位于学生管理下面', navItem('stu').nextElementSibling === navItem('growth') && navItem('growth').classList.contains('nav-item-child'));

  navItem('growth').click();
  await wait(80);

  const area = doc.getElementById('workarea');
  check('森林页展示班级统计', area.textContent.includes('班级总营养') && area.textContent.includes('棵树'));
  check('森林页渲染每名学生的树卡片', area.querySelectorAll('.growth-card').length === 2);
  check('每棵树保留手工加分入口', area.querySelectorAll('.growth-add').length === 2 && [...area.querySelectorAll('.growth-add')].every(button => button.textContent.includes('营养')));
  check('卡片展示阶段与营养', area.textContent.includes('发芽 · 36 营养') && area.textContent.includes('种子 · 0 营养'));
  check('阶段阈值来自后端', area.textContent.includes('🌰0') && area.textContent.includes('🌳560'));
  check('明确说明营养不是能力等级', area.textContent.includes('不是英语能力等级'));
  check('森林请求按当前学期发起', forestRequests[0] === 1);
  check('森林页标出当前补录标准归属', area.textContent.includes('当前补录标准') && area.textContent.includes('默认教师'));
  check('单套标准不误报分叉警告', !area.textContent.includes('不宜直接横向比较'));

  // 批量补录模式：勾选后切换全局班级范围必须清空选择（方案 §1.2）
  [...area.querySelectorAll('[data-act="growth-batch-toggle"]')][0].click();
  await wait(40);
  const card = [...area.querySelectorAll('.growth-card')][0];
  card.click();
  await wait(40);
  check('批量模式可勾选学生', doc.getElementById('workarea').textContent.includes('已选') &&
    doc.getElementById('workarea').textContent.includes('1 人'));

  const classSelect = doc.getElementById('classSelect');
  classSelect.value = '712';
  classSelect.dispatchEvent(new window.Event('change', { bubbles: true }));
  await wait(80);
  const afterFilter = doc.getElementById('workarea').textContent;
  check('切换班级范围清空批量选择', !afterFilter.includes('已选') && afterFilter.includes('批量补录'));
  check('班级范围筛选后只显示该班学生', afterFilter.includes('李四') && !afterFilter.includes('张三'));
  const stats = [...doc.getElementById('workarea').querySelectorAll('.growth-stat b')]
    .map(item => item.textContent.trim());
  check('班级统计与可见班级一致', stats[0] === '1' && stats[1] === '0' && stats[2] === '0');

  // 森林页自己的班级 chips 与全局范围共用一份状态
  const allChip = [...doc.getElementById('workarea').querySelectorAll('[data-act="growth-class"]')]
    .find(chip => chip.dataset.cls === '');
  allChip.click();
  await wait(60);
  check('森林页班级 chips 与全局范围同步', doc.getElementById('classSelect').value === '' &&
    doc.getElementById('workarea').textContent.includes('张三'));

  // 单人明细：五个分支、待记录/样本较少、历史营养（旧规则）
  [...doc.getElementById('workarea').querySelectorAll('.growth-card')][0].click();
  await wait(80);

  const modal = doc.getElementById('modalBody').textContent;
  const modalHtml = doc.getElementById('modalBody').innerHTML;
  check('明细展示五个能力分支', ['词汇', '语法', '阅读', '听力', '写作'].every(label => modal.includes(label)));
  check('无证据分支显示待记录', modal.includes('待记录'));
  check('样本不足显示样本较少', modal.includes('样本较少'));
  check('有证据分支显示平滑得分率', modal.includes('平滑得分率 72.0%'));
  check('历史营养单独标注（旧规则）', modal.includes('历史营养（旧规则）6'));
  check('补录记录可撤销，封顶记录不可撤销', modal.includes('该类别当天次数已达上限'));
  check('进步分单独标注并给出相对自己的基线', modal.includes('进步 +2') && modalHtml.includes('基线 35.0%'));
  check('展示历史年轮', modal.includes('2025 秋季'));
  check('展示规则版本与记录覆盖', modal.includes('growth-v3') && modal.includes('5 条'));
  // 补录预设由后端下发：把「完成学习任务」设成 3 分后，快捷按钮与默认输入值都要跟着变，
  // 前端不得再自带一份硬编码分值表。
  check('快捷补录分值来自后端预设', modal.includes('＋3 完成学习任务') && modalHtml.includes('value="3"'));
  check('补录记录标出记录人', modal.includes('王老师'));

  // 学期切换：作废成长缓存并重新拉取新学期森林
  doc.getElementById('modalClose').click();
  const before = forestRequests.length;
  const termSelect = doc.getElementById('termSelect');
  termSelect.value = '2';
  termSelect.dispatchEvent(new window.Event('change', { bubbles: true }));
  await wait(150);

  check('切换学期后重新请求成长森林', forestRequests.length > before && forestRequests[forestRequests.length - 1] === 2);
  const term2 = doc.getElementById('workarea').textContent;
  check('新学期森林不残留旧学期学生', term2.includes('王五') && !term2.includes('张三'));
  check('新学期标出该学期教师与标准', term2.includes('王老师') && term2.includes('当前补录标准'));
  check('多套标准分叉时显式提示不可比', term2.includes('不同的加分标准') && term2.includes('不宜直接横向比较'));

  check('全程无脚本错误', errors.length === 0);

  const passed = results.every(item => item.pass);
  console.log(JSON.stringify(results, null, 2));
  if (!passed) console.log('FAILURES:', JSON.stringify(results.filter(item => !item.pass), null, 2));
  console.log('OVERALL:', passed ? 'PASS' : 'FAIL');
  dom.window.close();
  process.exit(passed ? 0 : 1);
})();
