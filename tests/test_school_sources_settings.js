// 设置页「其他学校数据源（自定义 MCP）」验收（补丁 B 前端）：
// 内置 MONI 保持不变；换学校只换一份配置（端点 + 鉴权 + 路径模板 + 字段映射）。
// 这里校验列表渲染、编辑器预填、越界拦截、保存、测试连接、同步与删除确认。
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

const REQUIRED_MAP = {
  'class.external_id': ['classId'],
  'class.name': ['className'],
  'exam.external_id': ['examId'],
  'exam.name': ['examName'],
  'student.external_id': ['studentId'],
  'student.name': ['name'],
};

function moniRow() {
  return {
    source_key: 'moni', name: 'MONI 学生数据', kind: 'mcp', enabled: true, builtin: true,
    token_configured: false, key_hint: null,
    config: { endpoint: 'https://t-mcp.fufenxi.com/api/t-mcp/mcp', headers: {}, auth: { type: 'bearer' }, tools: {}, paths: {}, field_map: {} },
  };
}

function customRow(sourceKey, name) {
  return {
    source_key: sourceKey, name, kind: 'generic_mcp', enabled: true, builtin: false,
    token_configured: true, key_hint: '••••••••oken',
    config: {
      endpoint: 'https://mcp.example.edu/api/mcp',
      headers: { 'X-Api-Key': '********' },
      auth: { type: 'bearer', token_profile: `school-${sourceKey}` },
      tools: { list: 'vfs_list', read: 'vfs_read', query: 'vfs_query_jsonl' },
      paths: {
        classes: '/classes/.list.jsonl',
        exams: '/classes/{class_id}/exams/.list.jsonl',
        students: '/classes/{class_id}/exams/{exam_id}/students/.list.jsonl',
      },
      subject_filter: { field: 'subjectName', any_of: ['英语'] },
      full_score: 100,
      term: { external_id: '2026-S1', name: '2026 学年第一学期', code: '2026-S1' },
      field_map: { ...REQUIRED_MAP },
    },
  };
}

const response = (payload, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => payload });

// 已经配好分类取值对照的来源：用来验收「编辑时能回显成可读文本」。
function aliasRow() {
  return {
    source_key: 'hz-alias', name: '已配对照表的学校', kind: 'generic_mcp', enabled: true, builtin: false,
    token_configured: false, key_hint: null,
    config: {
      endpoint: 'https://mcp.alias.edu/api/mcp',
      headers: {}, auth: { type: 'none' },
      tools: { list: 'vfs_list', read: 'vfs_read', query: 'vfs_query_jsonl' },
      paths: { classes: '/classes/.list.jsonl', roster: '/classes/{class_id}/students/.list.jsonl' },
      field_map: { ...REQUIRED_MAP, 'question.knowledge': ['knowledgeNodes'], 'exam.kind': ['examKind'] },
      value_aliases: {
        'question.knowledge': { '宾语从句': ['从句', 'Object Clause'] },
        'exam.kind': { entrance: ['入学考'] },
      },
      tier_aliases: { '优秀': 'A', '良好': 'B' },
    },
  };
}

const errors = [];
const listRequests = [];
const createBodies = [];
const updateBodies = [];
const testCalls = [];
const syncCalls = [];
const deleteCalls = [];

let sources = [moniRow(), customRow('hz-english', '杭州某校英语数据'), aliasRow()];

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
      if (target.startsWith('/api/v1/attachments')) return response([]);
      if (target.startsWith('/api/v1/school-sync/moni/config')) return response({ configured: false });
      if (target.startsWith('/api/v1/ai/')) return response({ detail: 'unavailable' }, 404);

      if (target === '/api/v1/school-sync/sources' && method === 'GET') {
        listRequests.push('GET');
        return response(sources);
      }
      if (target === '/api/v1/school-sync/sources' && method === 'POST') {
        const body = JSON.parse(options.body);
        createBodies.push(body);
        const row = customRow(body.source_key, body.name);
        row.config = body.config;
        row.token_configured = Boolean(body.bearer_token);
        sources = [...sources.filter(item => item.source_key !== body.source_key), row];
        return response(row, 201);
      }
      const update = target.match(/^\/api\/v1\/school-sync\/sources\/([^/]+)\/config$/);
      if (update && method === 'PUT') {
        const body = JSON.parse(options.body);
        updateBodies.push({ key: update[1], body });
        sources = sources.map(item => (item.source_key === update[1]
          ? { ...item, name: body.name, config: body.config }
          : item));
        return response(sources.find(item => item.source_key === update[1]));
      }
      const test = target.match(/^\/api\/v1\/school-sync\/sources\/([^/]+)\/test$/);
      if (test && method === 'POST') {
        testCalls.push(test[1]);
        return response({ status: 'ok', health: 'ready', tool_count: 3, error: null, key_hint: '••••••••oken' });
      }
      const sync = target.match(/^\/api\/v1\/school-sync\/sources\/([^/]+)\/sync/);
      if (sync && method === 'POST') {
        syncCalls.push(sync[1]);
        return response({ status: 'completed', summary: { roster_students: 42, classes: 2, exams: 3, warnings: ['有学生缺少姓名，已跳过该条'] } });
      }
      const remove = target.match(/^\/api\/v1\/school-sync\/sources\/([^/]+)$/);
      if (remove && method === 'DELETE') {
        deleteCalls.push(remove[1]);
        sources = sources.filter(item => item.source_key !== remove[1]);
        return response({ status: 'deleted', source_key: remove[1] });
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
const editor = () => doc.getElementById('school-source-editor');
const rowFor = (key) => [...doc.querySelectorAll('.school-source-row')]
  .find(row => row.textContent.includes(key));

(async () => {
  await wait(60);
  navItem('settings').click();
  await wait(120);

  // 列表
  check('设置页渲染学校数据源卡片', Boolean(doc.getElementById('school-source-list')) &&
    doc.body.textContent.includes('其他学校数据源'));
  check('卡片不显示实现说明', !doc.body.textContent.includes('不需要改代码'));
  const rows = [...doc.querySelectorAll('.school-source-row')];
  check('内置 MONI 与自定义来源同时列出', rows.length === 3 &&
    rows.some(row => row.textContent.includes('moni')) &&
    rows.some(row => row.textContent.includes('hz-english')));
  const moniRowEl = rowFor('moni');
  check('内置来源标为内置且不可编辑', moniRowEl.textContent.includes('内置') &&
    !moniRowEl.querySelector('[data-act="school-source-edit"]'));
  const customRowEl = rowFor('hz-english');
  check('自定义来源显示端点与令牌状态', customRowEl.textContent.includes('https://mcp.example.edu/api/mcp') &&
    customRowEl.textContent.includes('令牌已保存'));

  // 分类取值对照表：编辑已有来源时回显成可读文本
  rowFor('hz-alias').querySelector('[data-act="school-source-edit"]').click();
  await wait(60);
  const aliasText = doc.getElementById('school-source-alias-map').value;
  check('对照表回显成「逻辑字段: 统一叫法 = 上游叫法」',
    aliasText.includes('question.knowledge: 宾语从句 = 从句, Object Clause') &&
    aliasText.includes('exam.kind: entrance = 入学考'));
  check('分层别名回显成「上游叫法 = A」',
    doc.getElementById('school-source-tier-aliases').value.includes('优秀 = A'));
  const reference = doc.getElementById('school-source-alias-reference').textContent;
  check('参考清单列出可归一的分类字段',
    reference.includes('question.knowledge') && reference.includes('exam.kind') &&
    reference.includes('student.tier') && reference.includes('question.teaching_block'));
  check('已配对照表时状态栏报告条数',
    doc.getElementById('school-source-alias-status').textContent.includes('2 条取值归一') &&
    doc.getElementById('school-source-alias-status').textContent.includes('2 条分层别名'));
  doc.querySelector('[data-act="school-source-cancel"]').click();
  await wait(40);

  // 新建：编辑器展开且字段映射预填模板
  check('编辑器默认收起', editor().hidden === true);
  doc.querySelector('[data-act="school-source-new"]').click();
  await wait(40);
  check('新建后展开编辑器', editor().hidden === false &&
    doc.getElementById('school-source-editor-title').textContent === '新建数据源');
  const template = JSON.parse(doc.getElementById('school-source-field-map').value);
  check('新建时预填字段映射模板', template['student.external_id'] && template['exam.full_score']);
  check('模板已含分类字段与逐题明细',
    template['question.knowledge'] && template['exam.kind'] && template['student.tier'] &&
    template['item.selected_option'] && template['item.time_spent_ms']);
  check('模板已含全部关键字段', doc.getElementById('school-source-missing').textContent.includes('关键字段映射已齐全'));
  check('新建时对照表为空并说明不会猜',
    doc.getElementById('school-source-alias-map').value === '' &&
    doc.getElementById('school-source-alias-status').textContent.includes('按原样保留'));

  // 越界拦截：缺标识 / 非法 JSON 都不发请求
  const createBefore = createBodies.length;
  doc.querySelector('[data-act="school-source-save"]').click();
  await wait(40);
  check('缺数据源标识时不发请求', createBodies.length === createBefore &&
    doc.getElementById('school-source-editor-status').textContent.includes('请填写数据源标识'));

  doc.getElementById('school-source-key').value = 'hz-2';
  doc.getElementById('school-source-name').value = '第二所学校';
  doc.getElementById('school-source-endpoint').value = 'https://mcp.other.edu/api/mcp';
  doc.getElementById('school-source-field-map').value = '{ 这不是 JSON';
  doc.querySelector('[data-act="school-source-save"]').click();
  await wait(40);
  check('字段映射非法 JSON 时不发请求', createBodies.length === createBefore &&
    doc.getElementById('school-source-editor-status').textContent.includes('不是合法 JSON'));

  // 正常保存
  doc.getElementById('school-source-field-map').value = JSON.stringify(REQUIRED_MAP, null, 2);
  doc.getElementById('school-source-auth').value = 'bearer';
  doc.getElementById('school-source-token').value = 'school-token';
  doc.getElementById('school-source-term-id').value = '2026-S1';
  doc.getElementById('school-source-path-classes').value = '/classes/.list.jsonl';
  doc.getElementById('school-source-path-exams').value = '/classes/{class_id}/exams/.list.jsonl';
  doc.getElementById('school-source-path-students').value = '/classes/{class_id}/exams/{exam_id}/students/.list.jsonl';
  doc.getElementById('school-source-subject-field').value = 'subjectName';
  doc.getElementById('school-source-subject-values').value = '英语,English';
  const listBefore = listRequests.length;
  doc.querySelector('[data-act="school-source-save"]').click();
  await wait(120);
  check('保存发出 POST 并带回填路径', createBodies.length === createBefore + 1 &&
    createBodies[0].config.paths.exams === '/classes/{class_id}/exams/.list.jsonl');
  check('令牌档案名由数据源标识派生',
    createBodies[0].config.auth.token_profile === 'school-hz-2' &&
    createBodies[0].bearer_token === 'school-token');
  check('科目过滤随表单一起提交',
    createBodies[0].config.subject_filter.any_of.join(',') === '英语,English');
  check('学期随表单一起提交', createBodies[0].config.term.external_id === '2026-S1');
  check('保存后刷新列表', listRequests.length === listBefore + 1);
  check('保存后编辑器切到编辑态且标识只读',
    doc.getElementById('school-source-key').readOnly === true &&
    doc.getElementById('school-source-editor-title').textContent.includes('第二所学校'));

  // 分类取值对照表：越界拦截与提交
  const updateBefore = updateBodies.length;
  const aliasStatus = () => doc.getElementById('school-source-alias-status').textContent;
  doc.getElementById('school-source-alias-map').value = 'question.knowledge 宾语从句 = 从句';
  doc.querySelector('[data-act="school-source-save"]').click();
  await wait(40);
  check('对照表写法不完整时不发请求', updateBodies.length === updateBefore &&
    doc.getElementById('school-source-editor-status').textContent.includes('分类取值对照表有问题'));
  check('对照表写法错误即时提示行号', aliasStatus().includes('第 1 行'));

  doc.getElementById('school-source-alias-map').value = 'question.mood: 时态 = 时态错误';
  doc.querySelector('[data-act="school-source-save"]').click();
  await wait(40);
  check('对照表字段名不在清单里时拦截', updateBodies.length === updateBefore &&
    aliasStatus().includes('不是可以归一的分类字段'));

  doc.getElementById('school-source-alias-map').value = 'question.knowledge: 宾语从句 = 从句';
  doc.querySelector('[data-act="school-source-save"]').click();
  await wait(40);
  check('对照表字段没在字段映射里时拦截', updateBodies.length === updateBefore &&
    aliasStatus().includes('还没有在字段映射里说明读哪一列'));

  doc.getElementById('school-source-field-map').value = JSON.stringify(
    { ...REQUIRED_MAP, 'question.knowledge': ['knowledgeNodes'] }, null, 2);
  doc.getElementById('school-source-alias-map').value = [
    '# 以 # 开头的行会被忽略',
    'question.knowledge: 宾语从句 = 从句, Object Clause',
    'question.knowledge: 宾语从句 = ＣＬＡＵＳＥ',
  ].join('\n');
  doc.getElementById('school-source-tier-aliases').value = '优秀 = 甲等';
  doc.querySelector('[data-act="school-source-save"]').click();
  await wait(40);
  check('分层别名目标层越界时拦截', updateBodies.length === updateBefore &&
    aliasStatus().includes('目标层只能是 A/B/C/D'));

  doc.getElementById('school-source-tier-aliases').value = '优秀 = A\n良好 = b';
  doc.querySelector('[data-act="school-source-save"]').click();
  await wait(120);
  check('对照表校验通过后随配置一起提交', updateBodies.length === updateBefore + 1 &&
    updateBodies[0].key === 'hz-2');
  const sentConfig = updateBodies[0].body.config;
  check('提交的对照表按字段分组且忽略注释行',
    sentConfig.value_aliases['question.knowledge']['宾语从句'].join('|') === '从句|Object Clause|ＣＬＡＵＳＥ');
  check('分层别名大写化后提交',
    sentConfig.tier_aliases['优秀'] === 'A' && sentConfig.tier_aliases['良好'] === 'B');
  check('对照表没有写进字段映射（避免回显重复）',
    !('enum' in (sentConfig.field_map['question.knowledge'] || {})) &&
    Array.isArray(sentConfig.field_map['question.knowledge']));
  check('保存后对照表按服务端回显重新预填',
    doc.getElementById('school-source-alias-map').value.includes('question.knowledge: 宾语从句 = 从句, Object Clause, ＣＬＡＵＳＥ') &&
    doc.getElementById('school-source-tier-aliases').value.includes('良好 = B'));

  // 同一个上游叫法映射成两个统一值时必须报错，不能静默取舍
  const conflictBefore = updateBodies.length;
  doc.getElementById('school-source-alias-map').value = [
    'question.knowledge: 宾语从句 = 从句',
    'question.knowledge: 定语从句 = 从句',
  ].join('\n');
  doc.querySelector('[data-act="school-source-save"]').click();
  await wait(40);
  check('上游叫法冲突时即时报错并拦截', aliasStatus().includes('已经被映射成') &&
    updateBodies.length === conflictBefore);

  // 即时反馈：字段编辑时就更新，不必先点保存
  doc.getElementById('school-source-alias-map').value = 'question.knowledge: 宾语从句 = 从句';
  doc.getElementById('school-source-alias-map').dispatchEvent(new window.Event('change', { bubbles: true }));
  await wait(40);
  check('编辑对照表即时报数，不用先保存', aliasStatus().includes('1 条取值归一'));
  doc.getElementById('school-source-alias-map').value = 'question.knowledge: 宾语从句 =';
  doc.getElementById('school-source-alias-map').dispatchEvent(new window.Event('change', { bubbles: true }));
  await wait(40);
  check('编辑对照表即时报缺上游叫法', aliasStatus().includes('至少写一个上游叫法'));
  doc.getElementById('school-source-alias-map').value = 'question.knowledge: 宾语从句 = 从句';
  doc.getElementById('school-source-alias-map').dispatchEvent(new window.Event('change', { bubbles: true }));
  await wait(40);

  // 测试连接
  const testBefore = testCalls.length;
  rowFor('hz-2').querySelector('[data-act="school-source-test"]').click();
  await wait(60);
  check('测试连接调用对应数据源', testCalls.length === testBefore + 1 && testCalls[testCalls.length - 1] === 'hz-2');
  check('测试成功后状态栏报告工具数',
    doc.getElementById('school-source-status').textContent.includes('连接正常') &&
    doc.getElementById('school-source-status').textContent.includes('3 个工具'));

  // 立即同步：带出提示
  const syncBefore = syncCalls.length;
  rowFor('hz-2').querySelector('[data-act="school-source-sync"]').click();
  await wait(120);
  check('立即同步调用对应数据源', syncCalls.length === syncBefore + 1 && syncCalls[syncCalls.length - 1] === 'hz-2');
  const syncStatus = doc.getElementById('school-source-status').textContent;
  check('同步状态包含学生与考试数', syncStatus.includes('学生 42 人') && syncStatus.includes('考试 3 场'));
  check('同步提示透传后端警告', syncStatus.includes('缺少姓名'));

  // 删除：先弹确认
  rowFor('hz-2').querySelector('[data-act="school-source-delete"]').click();
  await wait(40);
  check('删除先弹确认并说明保留已导入数据',
    doc.getElementById('modalBody').textContent.includes('本机保存的令牌') &&
    doc.getElementById('modalBody').textContent.includes('不会被删除'));
  const deleteBefore = deleteCalls.length;
  doc.querySelector('[data-act="school-source-delete-confirm"]').click();
  await wait(80);
  check('确认后删除数据源', deleteCalls.length === deleteBefore + 1 && deleteCalls[deleteCalls.length - 1] === 'hz-2');
  check('删除后列表不再包含该来源', !rowFor('hz-2'));

  check('全程无脚本错误', errors.length === 0);

  const passed = results.every(item => item.pass);
  console.log(JSON.stringify(results, null, 2));
  if (!passed) console.log('FAILURES:', JSON.stringify(results.filter(item => !item.pass), null, 2));
  console.log('OVERALL:', passed ? 'PASS' : 'FAIL');
  dom.window.close();
  process.exit(passed ? 0 : 1);
})();
