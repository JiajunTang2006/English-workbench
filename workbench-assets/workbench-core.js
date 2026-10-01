    // ================= 常量与配置 =================
    const SCHEMA_VERSION = 4;
    const LS_KEY = 'hye_db_v1';

    // 第三方资源按需加载：避免启动时解析和执行 Excel、ZIP、图表库。
    // 资源使用经典脚本并暴露全局对象，因此这里使用 script 标签而非 import()。
    const localResourcePromises = new Map();
    const localActionLocks = new Set();
    const LOCAL_RESOURCE_VERSION = '202608190930';
    function loadLocalScriptOnce(filename, ready) {
      if (typeof ready === 'function' && ready()) return Promise.resolve();
      const src = `./workbench-assets/${filename}?v=${LOCAL_RESOURCE_VERSION}`;
      if (localResourcePromises.has(src)) return localResourcePromises.get(src);
      const promise = new Promise((resolve, reject) => {
        const script = document.createElement('script');
        script.src = src;
        script.async = true;
        script.onload = () => {
          if (typeof ready !== 'function' || ready()) resolve();
          else reject(new Error(`本地资源未正确初始化：${filename}`));
        };
        script.onerror = () => reject(new Error(`本地资源加载失败：${filename}`));
        document.head.appendChild(script);
      }).catch(error => {
        localResourcePromises.delete(src);
        throw error;
      });
      localResourcePromises.set(src, promise);
      return promise;
    }
    function ensureXlsx() {
      return loadLocalScriptOnce('xlsx.full.min.js', () => typeof window !== 'undefined' && typeof window.XLSX !== 'undefined');
    }
    function ensureJSZip() {
      return loadLocalScriptOnce('jszip.min.js', () => typeof window !== 'undefined' && typeof window.JSZip !== 'undefined');
    }
    function ensureEcharts() {
      return loadLocalScriptOnce('echarts.min.js', () => typeof window !== 'undefined' && typeof window.echarts !== 'undefined');
    }

    // 异步导入/导出动作共享轻量锁，防止用户重复点击生成重复文件或并行覆盖状态。
    async function runLocalActionOnce(key, action, trigger) {
      if (localActionLocks.has(key)) return false;
      localActionLocks.add(key);
      const element = trigger && typeof trigger === 'object' && 'disabled' in trigger ? trigger : null;
      const previousDisabled = element ? element.disabled : false;
      if (element) {
        element.disabled = true;
        element.setAttribute('aria-busy', 'true');
      }
      try {
        return await action();
      } catch (error) {
        console.error(error);
        if (typeof showToast === 'function') showToast(error?.message || '操作失败，请重试', 'error');
        return false;
      } finally {
        localActionLocks.delete(key);
        if (element) {
          element.disabled = previousDisabled;
          element.removeAttribute('aria-busy');
        }
      }
    }

    const DATABASE_MODE = ['http:', 'https:'].includes(window.location.protocol)
      && window.location.pathname.startsWith('/workbench');
    let databaseRevision = 0;
    let databaseSaveChain = Promise.resolve(true);
    let databaseWriteBlocked = false;
    let appRuntimeVersion = '浏览器存储版';
    let appRuntimeSchema = '—';
    let availableTerms = [];
    let managedTerms = [];
    let currentTermId = null;
    let termSwitchInProgress = false;
    let workbenchToken = '';
    const attachmentBlobUrls = new Map();
    if (DATABASE_MODE) {
      const tokenFromUrl = new URLSearchParams(window.location.search).get('token');
      workbenchToken = tokenFromUrl || sessionStorage.getItem('workbench_token') || '';
      if (tokenFromUrl) {
        sessionStorage.setItem('workbench_token', tokenFromUrl);
        const cleanUrl = new URL(window.location.href);
        cleanUrl.searchParams.delete('token');
        history.replaceState(null, '', cleanUrl.pathname + cleanUrl.search + cleanUrl.hash);
      }
    }

    let runtimeSessionSocket = null;
    let runtimeSessionReconnectTimer = null;
    let runtimeSessionConnectFailures = 0;

    function connectRuntimeSession() {
      if (!DATABASE_MODE || !workbenchToken || typeof window.WebSocket !== 'function') return;
      if (runtimeSessionSocket && runtimeSessionSocket.readyState < 2) return;
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const url = `${protocol}//${window.location.host}/api/v1/runtime/session?token=${encodeURIComponent(workbenchToken)}`;
      try {
        const socket = new WebSocket(url);
        let socketOpened = false;
        runtimeSessionSocket = socket;
        socket.addEventListener('open', () => {
          socketOpened = true;
          runtimeSessionConnectFailures = 0;
          socket.send('ready');
        });
        socket.addEventListener('close', event => {
          if (runtimeSessionSocket !== socket) return;
          runtimeSessionSocket = null;
          if (!socketOpened) runtimeSessionConnectFailures += 1;
          // WebSocket 在握手阶段收到 403 时，浏览器通常只暴露 1006，无法
          // 读取服务端的 4401。连续三次从未成功打开，基本可以确定这是旧
          // 浏览器页保存的过期令牌；停止无限重连，避免干扰新启动的桌面窗口。
          const staleHandshake = !socketOpened && runtimeSessionConnectFailures >= 3;
          if (event.code !== 4401 && !staleHandshake) {
            clearTimeout(runtimeSessionReconnectTimer);
            runtimeSessionReconnectTimer = setTimeout(connectRuntimeSession, 1500);
          } else if (staleHandshake) {
            try { sessionStorage.removeItem('workbench_token'); } catch (storageError) {}
          }
        });
      } catch (error) {
        clearTimeout(runtimeSessionReconnectTimer);
        runtimeSessionReconnectTimer = setTimeout(connectRuntimeSession, 1500);
      }
    }

    connectRuntimeSession();

    function apiErrorMessage(payload, status) {
      const detail = payload && payload.detail;

      if (typeof detail === 'string' && detail.trim()) return detail.trim();

      if (Array.isArray(detail)) {
        const messages = detail
          .map(item => item && (item.message || item.msg || item.error))
          .filter(Boolean)
          .map(String);
        if (messages.length) return messages.join('；');
      }

      if (detail && typeof detail === 'object') {
        const message = detail.message || detail.msg || detail.error;
        const code = detail.code;
        if (message && code) return `${String(message)}（${String(code)}）`;
        if (message) return String(message);
        if (code) return `请求失败（${String(code)}）`;
      }

      if (payload && typeof payload.message === 'string' && payload.message.trim()) {
        return payload.message.trim();
      }
      if (payload && typeof payload.error === 'string' && payload.error.trim()) {
        return payload.error.trim();
      }
      return `请求失败（${status}）`;
    }

    async function apiRequest(path, options = {}) {
      const headers = { ...(options.headers || {}), Authorization: `Bearer ${workbenchToken}` };
      const timeoutMs = options.timeoutMs ?? 15000;
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), timeoutMs);
      try {
        const response = await fetch(path, { ...options, headers, signal: options.signal || controller.signal });
        const payload = await response.json().catch(() => null);
        if (!response.ok) {
          const detail = payload && payload.detail;
          const error = new Error(apiErrorMessage(payload, response.status));
          error.status = response.status;
          error.payload = payload;
          error.detail = detail;
          if (detail && typeof detail === 'object' && !Array.isArray(detail) && detail.code) {
            error.code = String(detail.code);
          }
          if (response.status === 401 && DATABASE_MODE) {
            error.code = 'STALE_SESSION';
            try { sessionStorage.removeItem('workbench_token'); } catch (storageError) {}
          }
          throw error;
        }
        return payload;
      } catch (error) {
        if (error.name === 'AbortError') {
          const timeoutError = new Error('请求超时，请检查网络后重试');
          timeoutError.code = 'REQUEST_TIMEOUT';
          throw timeoutError;
        }
        throw error;
      } finally {
        clearTimeout(timeout);
      }
    }

    async function attachmentObjectUrl(id) {
      const key = String(id);
      if (attachmentBlobUrls.has(key)) return attachmentBlobUrls.get(key);
      const response = await fetch(`/api/v1/attachments/${encodeURIComponent(key)}/download`, {
        headers: { Authorization: `Bearer ${workbenchToken}` }
      });
      if (!response.ok) throw new Error(`附件下载失败（${response.status}）`);
      const url = URL.createObjectURL(await response.blob());
      attachmentBlobUrls.set(key, url);
      return url;
    }
    // ================= 学科配置 =================
    // 学科决定界面用语与导航模块：老师首次进入时选一次，之后可在设置页改。
    // 数据层键名不随学科变化（scores.英语、entrance_english 列、exam_type=english_total
    // 都保持原样），所以换学科只是换一套说法，不会动到任何历史数据。
    const SUBJECT_FALLBACK = {
      key: 'english',
      label: '英语',
      teacher_subject_default: '初中英语',
      modules: ['dash', 'stu', 'growth', 'score', 'dictation', 'recite', 'writing', 'homework', 'errors', 'todo', 'settings'],
      module_labels: {},
      question_types: ['听力', '阅读理解', '完形填空', '选词填空', '单词拼写', '语法填空', '作文'],
      labels: {
        score_total: '英语总分',
        entrance_score: '入学英语',
        score_column: '英语成绩',
        score_short: '英语分数',
        score_single: '英语单科成绩',
        score_trend: '英语分数趋势',
        score_ranking: '英语排名',
        exam_default: '英语考试',
        ability_disclaimer: '不代表英语水平'
      }
    };
    let subjectCatalog = [SUBJECT_FALLBACK];
    let subjectKey = SUBJECT_FALLBACK.key;
    // 老版本后端没有学科接口时按英语继续，并且不弹窗打断老师。
    let subjectChosen = true;

    function subjectConfig() {
      return subjectCatalog.find(item => item.key === subjectKey) || subjectCatalog[0] || SUBJECT_FALLBACK;
    }
    function subjectText(labelKey) {
      const value = subjectConfig().labels ? subjectConfig().labels[labelKey] : '';
      return value || SUBJECT_FALLBACK.labels[labelKey] || '';
    }
    // 学科词表是受控配置，但仍统一转义，避免后端配置被改动后进入 HTML。
    function subjectHtml(labelKey) {
      return escapeHtml(subjectText(labelKey));
    }
    function subjectName() {
      return subjectConfig().label || SUBJECT_FALLBACK.label;
    }
    function subjectBaseTitle() {
      // 顶栏标题优先用老师自己写的学科名（例如「七年级语文」），没写才用学科默认名。
      return String(state?.teacher?.subject || '').trim() || subjectConfig().teacher_subject_default || SUBJECT_FALLBACK.teacher_subject_default;
    }
    function enabledModuleKeys() {
      const modules = subjectConfig().modules;
      return new Set(Array.isArray(modules) && modules.length ? modules : SUBJECT_FALLBACK.modules);
    }
    function visibleModules() {
      const enabled = enabledModuleKeys();
      return MODULES.filter(module => enabled.has(module.key));
    }
    function moduleLabel(module) {
      if (!module) return '';
      return (subjectConfig().module_labels || {})[module.key] || module.label;
    }
    function subjectQuestionTypes() {
      const types = subjectConfig().question_types;
      return Array.isArray(types) && types.length ? types : SUBJECT_FALLBACK.question_types;
    }
    // 导入识别列名：先认当前学科的写法，再退回历史英语写法，保证老文件仍能导入。
    function subjectColumnAliases(labelKey, legacy) {
      const names = [subjectText(labelKey)].concat(legacy || []);
      return [...new Set(names.map(name => String(name || '').trim()).filter(Boolean))];
    }
    function subjectScoreImportAliases() {
      const genericAliases = ['总分', '得分', '成绩', 'total_score'];
      const legacyEnglishAliases = subjectKey === 'english'
        ? ['英语', 'english', 'english_total']
        : [];
      return subjectColumnAliases('score_total', [
        subjectText('score_column'), subjectName(), ...genericAliases, ...legacyEnglishAliases
      ]);
    }
    function subjectRankImportAliases() {
      return subjectColumnAliases('score_ranking', ['年级排名', '年级名次', '年级排行', 'grade_rank', 'graderank']);
    }

    async function loadSubjectCatalog() {
      if (!DATABASE_MODE) return false;
      try {
        const payload = await apiRequest('/api/v1/subjects');
        const list = Array.isArray(payload && payload.subjects) ? payload.subjects.filter(item => item && item.key) : [];
        if (list.length) subjectCatalog = list;
        const current = subjectCatalog.find(item => item.key === payload?.current);
        subjectKey = current ? current.key : subjectCatalog[0].key;
        syncTeacherSubject('english');
        document.title = `${subjectBaseTitle()} 教学工作台`;
        subjectChosen = payload?.chosen !== false;
        return true;
      } catch (error) {
        console.warn('学科配置加载失败，按英语工作台继续', error);
        subjectChosen = true;
        return false;
      }
    }

    // 老师学科名只在「没写过」或「还等于旧学科默认名」时才跟随学科走，
    // 否则会覆盖掉老师手写的「七年级语文」这类值。
    function syncTeacherSubject(previousKey) {
      if (!state) return;
      const next = subjectCatalog.find(item => item.key === subjectKey);
      if (!next) return;
      const previousDefault = subjectCatalog.find(item => item.key === previousKey)?.teacher_subject_default || '';
      const current = String(state.teacher?.subject || '').trim();
      const previous = subjectCatalog.find(item => item.key === previousKey);
      if (!current || current === previousDefault || current === previous?.label ||
          (previousKey === 'english' && current === '英语')) {
        state.teacher.subject = next.teacher_subject_default;
      }
    }

    async function setSubjectKey(nextKey, options = {}) {
      const target = subjectCatalog.find(item => item.key === nextKey);
      if (!target) return false;
      const previousKey = subjectKey;
      if (options.persist && DATABASE_MODE) {
        // Settings is the source of truth. A second workspace-state write here
        // would make a successful switch look failed if that unrelated save fails.
        await apiRequest('/api/v1/settings', {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ subject_key: target.key })
        });
      }
      subjectKey = target.key;
      subjectChosen = true;
      syncTeacherSubject(previousKey);
      document.title = `${subjectBaseTitle()} 教学工作台`;
      if (target.key !== previousKey) {
        if (typeof window.growthInvalidate === 'function') window.growthInvalidate();
        if (typeof teachMateState !== 'undefined') {
          teachMateState.setCurrentSession(null);
          teachMateState.setSessions([]);
          if (typeof teachMateState.clearAnalysisGroup === 'function') teachMateState.clearAnalysisGroup();
        }
        if (activeTab === 'teachmate' && typeof initTeachMate === 'function') void initTeachMate();
      }
      return true;
    }

    const MODULES = [
      { key: 'dash', label: '仪表盘', icon: 'side_navigation' },
      { key: 'stu', label: '学生管理', icon: 'groups' },
      { key: 'growth', label: '成长森林', icon: 'forest', parent: 'stu' },
      { key: 'score', label: '成绩管理', icon: 'bar_chart' },
      { key: 'dictation', label: '默写成绩', icon: 'edit_note' },
      { key: 'recite', label: '背诵成绩', icon: 'menu_book' },
      { key: 'writing', label: '写作成绩', icon: 'draw' },
      { key: 'homework', label: '日常作业', icon: 'task_alt' },
      { key: 'errors', label: '原卷与错题', icon: 'assignment' },
      { key: 'todo', label: '待办事项', icon: 'check_box' },
      { key: 'settings', label: '班级与设置', icon: 'settings' }
    ];
    const QUESTION_TYPES = ['听力','阅读理解','完形填空','选词填空','单词拼写','语法填空','作文'];

    // ================= 数据层 =================
    let state = null;
    let curModule = 'dash';
    // 启动始终进入 WorkBench，避免上一次停留在 TeachMate 导致工作台每次打开
    // 都落在聊天首页；用户仍可在页面内随时切换到 TeachMate。
    let activeTab = 'workbench';
    function setActiveTab(tab) {
      if (tab !== 'workbench' && tab !== 'teachmate') return;
      activeTab = tab;
      try { localStorage.setItem('workbench_active_tab', tab); } catch (e) {}
      // B3-15: 同步 URL 查询参数，分享链接可直接进入对应 tab
      try {
        const u = new URL(window.location.href);
        u.searchParams.set('tab', tab);
        window.history.replaceState(null, '', u.toString());
      } catch (e) {}
      document.body.classList.toggle('tab-workbench', tab === 'workbench');
      document.body.classList.toggle('tab-teachmate', tab === 'teachmate');
      document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.toggle('active', btn.dataset.tab === tab));
    }
    let scoreTab = 'table';
    let scoreSort = 'id';
    let scoreClass = '';
    let currentExamId = '';
    let dashboardClass = '';
    let studentClass = '';
    let reciteClass = '';
    let reciteSearchText = '';
    let reciteTaskId = '';
    let reciteBatchMode = false;
    const selectedReciteTasks = new Set();
    let writingClass = '';
    let writingSearchText = '';
    let writingTaskId = '';
    let writingBatchMode = false;
    const selectedWritingTasks = new Set();
    let riskCloudClass = '';
    let riskCloudExpanded = { C: false, D: false };
    let riskDetailLevel = 'C';
    let errorSelectedDocumentId = '';
    let dictationClass = '';
    let dictationRoundIndex = 0;
    let dictationBatchMode = false;
    const selectedDictationRounds = new Set();
    let todoCalendarDate = new Date();
    let todoViewMode = 'month';
    let todoWeekDate = new Date();
    let todoSelectedDate = '';
    let stuSearchText = '';
    let studentPage = 1;
    let studentPageSize = 30;
    let dictationSearchText = '';
    let homeworkClass = '';
    let homeworkSearchText = '';
    let homeworkActiveTaskId = '';
    let homeworkBatchMode = false;
    const visiblePhoneStudentIds = new Set();
    const selectedHomeworkStudents = new Set();
    let batchMode = false;
    const selectedStudentIds = new Set();
    const selectedTagIds = new Set();
    let batchDeletePending = false;
    let batchDeleteButton = null;
    const pendingScoreEdits = new Map();
    const scoreUndoStack = [];
    let scoreCharts = [];
    let studentCharts = [];
    let modalReturnStudentId = '';
    const SIDEBAR_PREF_KEY = 'workbench_sidebar_collapsed';
    let sidebarCollapsed = false;
    try { sidebarCollapsed = localStorage.getItem(SIDEBAR_PREF_KEY) === '1'; } catch (error) {}
    const DICTATION_FULL_SCORE = 100;
    const RECITE_LEVELS = ['A', 'B', 'C', 'F'];
    let importCache = null;
    let todoImportDraft = null;
    const recordViewModes = { score: 'single', dictation: 'single', recite: 'single', writing: 'single', homework: 'single' };

    function normalizeDictationRanges(raw) {
      if (!Array.isArray(raw)) return [];
      return raw.slice(0, 10).map(item => {
        if (!item || typeof item !== 'object') return null;
        const minRaw = item.min ?? '';
        const maxRaw = item.max ?? '';
        const min = minRaw === '' || minRaw == null ? null : Number(minRaw);
        const max = maxRaw === '' || maxRaw == null ? null : Number(maxRaw);
        if ((min == null && max == null) || (min != null && (!Number.isFinite(min) || min < 0 || min > DICTATION_FULL_SCORE)) || (max != null && (!Number.isFinite(max) || max < 0 || max > DICTATION_FULL_SCORE)) || (min != null && max != null && min > max)) return null;
        return { label: String(item.label || '').trim().slice(0, 30), min, max };
      }).filter(Boolean);
    }

    function dictationRangeLabel(range) {
      if (range.label) return range.label;
      if (range.min != null && range.max != null) return `${range.min}-${range.max}分`;
      if (range.min != null) return `≥${range.min}分`;
      return `≤${range.max}分`;
    }

    function getDictationRoundStats(roundIndex, students) {
      const list = Array.isArray(students) ? students : [];
      const scores = list
        .map(student => (state.dictation?.[student.id] || [])[roundIndex])
        .filter(value => value !== '' && value != null && Number.isFinite(Number(value)))
        .map(Number);
      const ranges = normalizeDictationRanges((state.dictationRanges || [])[roundIndex]);
      return {
        recorded: scores.length,
        fullScore: scores.filter(score => score === DICTATION_FULL_SCORE).length,
        ranges: ranges.map(range => ({ ...range, count: scores.filter(score => (range.min == null || score >= range.min) && (range.max == null || score <= range.max)).length }))
      };
    }

    function createDefaultState() {
      return {
        schema: SCHEMA_VERSION,
        dataContract: 2,
        teacher: { name: '', subject: '初中英语' },
        classes: [],
        settings: { excellent: 90, pass: 60, criticalLow: 55 },
        students: [],
        archivedStudents: [],
        exams: [],
        archivedExams: [],
        classAliases: {},
        studentTags: [],
        currentExamId: '',
        recitations: [],
        writings: [],
        errors: [],
        paperDocuments: [],
        archivedDocuments: [],
        critical: [],
        todos: [],
        dictation: {},
        dictationNames: [],
        dictationRanges: [],
        homeworkTasks: [],
        homeworkRecords: {}
      };
    }

    function normalizeTierLines(value, fullScore) {
      if (!value || typeof value !== 'object') return null;
      const a = Number(value.a);
      const b = Number(value.b);
      const c = Number(value.c);
      const max = Number(fullScore) || 100;
      if (![a, b, c].every(Number.isFinite) || c < 0 || !(a >= b && b >= c) || a > max) return null;
      return { a, b, c };
    }

    // 背诵任务统一使用 A/B/C/F 四档；旧版本的中文状态在迁移时保留语义。
    function normalizeReciteStatus(raw) {
      const source = raw && typeof raw === 'object' ? raw : {};
      const legacy = typeof raw === 'string' ? raw : '';
      let level = String(source.level || source.grade || source.status || legacy || '').trim().toUpperCase();
      if (level === '已过') level = 'A';
      else if (level === '需重背' || level === '未过') level = 'F';
      if (!RECITE_LEVELS.includes(level)) level = '';
      let retake = String(source.retake || source.retakeStatus || source.retry || '').trim();
      if (retake === '已过' || retake === '通过' || retake === 'passed') retake = 'passed';
      else if (retake === '未过' || retake === '未通过' || retake === 'failed' || retake === 'not_passed') retake = 'not_passed';
      else retake = '';
      return { level, retake: level === 'F' ? retake : '' };
    }

    function reciteStatusLabel(raw) {
      const status = normalizeReciteStatus(raw);
      if (!status.level) return '—';
      if (status.level !== 'F' || !status.retake) return status.level;
      return `${status.level} · 重背${status.retake === 'passed' ? '通过' : '未通过'}`;
    }

    function reciteStatusBadgeClass(raw) {
      const level = normalizeReciteStatus(raw).level;
      return level === 'A' ? 'badge-green' : level === 'B' ? 'badge-blue' : level === 'C' ? 'badge-orange' : level === 'F' ? 'badge-red' : 'badge-gray';
    }

    function pad2(value) { return String(value).padStart(2, '0'); }
    function formatLocalDate(date) {
      return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}`;
    }
    function parseLocalDate(value) {
      const match = String(value || '').match(/^(\d{4})-(\d{2})-(\d{2})$/);
      return match ? new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3])) : null;
    }
    function formatTodoDate(value) {
      const date = parseLocalDate(value);
      return date ? `${date.getFullYear()}年${date.getMonth() + 1}月${date.getDate()}日` : '未设置日期';
    }
    function ensureTodoDate() {
      const today = formatLocalDate(new Date());
      if (!todoSelectedDate) todoSelectedDate = today;
      if (!parseLocalDate(todoSelectedDate)) todoSelectedDate = today;
    }

    function getTodoStartTime(todo) {
      return /^\d{2}:\d{2}$/.test(String(todo?.startTime || todo?.time || '')) ? String(todo.startTime || todo.time) : '';
    }

    function getTodoEndTime(todo) {
      return /^\d{2}:\d{2}$/.test(String(todo?.endTime || '')) ? String(todo.endTime) : '';
    }

    function todoRepeatRange(todo) {
      const repeat = todo?.repeatRule;
      if (!repeat || repeat.frequency !== 'weekly' || !Array.isArray(repeat.weekdays) || !repeat.weekdays.length) return null;
      const start = parseLocalDate(repeat.startDate || todo.date);
      const end = parseLocalDate(repeat.endDate || repeat.startDate || todo.date);
      if (!start || !end || end < start) return null;
      return { repeat, start, end };
    }

    function expandTodoOccurrences(todos = state.todos) {
      const occurrences = [];
      (Array.isArray(todos) ? todos : []).forEach(todo => {
        const range = todoRepeatRange(todo);
        if (!range) {
          const date = String(todo.date || todo.deadline || '');
          if (date) occurrences.push({ todo, date, occurrenceId: `${todo.id}::${date}`, done: Boolean(todo.done) });
          return;
        }
        const cursor = new Date(range.start);
        let guard = 0;
        while (cursor <= range.end && guard++ < 3700) {
          if (range.repeat.weekdays.includes(cursor.getDay())) {
            const date = formatLocalDate(cursor);
            occurrences.push({ todo, date, occurrenceId: `${todo.id}::${date}`, done: Boolean(todo.completedDates?.[date]) });
          }
          cursor.setDate(cursor.getDate() + 1);
        }
      });
      return occurrences;
    }

    function normalizeClassName(value) {
      let raw = String(value ?? '').trim();
      if (!raw) return '';
      raw = raw.replace(/[０-９]/g, char => String.fromCharCode(char.charCodeAt(0) - 0xfee0)).replace(/[\s　]/g, '').replace(/[（）()]/g, '');
      const chineseGrades = { 一: '1', 二: '2', 三: '3', 四: '4', 五: '5', 六: '6', 七: '7', 八: '8', 九: '9' };
      const chinese = raw.match(/^([一二三四五六七八九])(?:年级|级)?(?:第)?([0-9]{1,2})班(?:级)?$/);
      if (chinese) return `${chineseGrades[chinese[1]]}${chinese[2]}`;
      const numeric = raw.match(/^([1-9][0-9]?)(?:年级|级)?(?:第)?([0-9]{1,2})班(?:级)?$/);
      if (numeric) return `${numeric[1]}${numeric[2]}`;
      const compact = raw.match(/^([1-9])([0-9]{1,2})班(?:级)?$/);
      if (compact) return `${compact[1]}${compact[2]}`;
      return raw;
    }

    function formatClassLabel(value) {
      const raw = String(value ?? '').trim();
      if (!raw) return '';
      return raw.endsWith('班') ? raw : `${raw}班`;
    }

    function findClassNameInText(value) {
      const raw = String(value ?? '').replace(/[\s　]/g, '');
      if (!raw) return '';
      const patterns = [/[一二三四五六七八九](?:年级|级)?(?:第)?[0-9]{1,2}班/, /[1-9][0-9]?(?:年级|级)?(?:第)?[0-9]{1,2}班/, /[1-9][0-9]{1,2}班/];
      for (const pattern of patterns) {
        const match = raw.match(pattern);
        if (match) return normalizeClassName(match[0]);
      }
      return '';
    }

    function normalizeClassAliases(value) {
      const aliases = {};
      if (!value || typeof value !== 'object') return aliases;
      Object.entries(value).forEach(([source, target]) => {
        const from = normalizeClassName(source);
        const to = normalizeClassName(target);
        if (from && to && from !== to) aliases[from] = to;
      });
      return aliases;
    }

    function resolveClassName(value, aliases = state?.classAliases) {
      let current = normalizeClassName(value);
      const seen = new Set();
      while (current && aliases?.[current] && !seen.has(current)) {
        seen.add(current);
        current = normalizeClassName(aliases[current]);
      }
      return current;
    }

    function migrate(data) {
      const base = createDefaultState();
      if (!data || typeof data !== 'object') return base;
      data.schema = SCHEMA_VERSION;
      data.dataContract = 2;
      data.teacher = {
        name: String(data.teacher?.name ?? base.teacher.name).trim(),
        subject: String(data.teacher?.subject ?? base.teacher.subject).trim()
      };
      data.classAliases = normalizeClassAliases(data.classAliases);
      data.students = Array.isArray(data.students) ? data.students : [];
      data.students = data.students.map(s => ({
        id: String(s.id ?? '').trim(), name: String(s.name ?? '').trim(),
        class: resolveClassName(s.class, data.classAliases), english: Number(s.english) || 0,
        target: s.target ?? '', weakTags: s.weakTags ?? '', phone: s.phone ?? '', seat: s.seat ?? '',
        evaluationTags: Array.isArray(s.evaluationTags) ? s.evaluationTags.map(String) : [],
        evaluationNote: String(s.evaluationNote ?? '').trim()
      })).filter(s => s.id && s.name && s.class);
      data.archivedStudents = Array.isArray(data.archivedStudents) ? data.archivedStudents.map(s => ({
        id: String(s.id ?? '').trim(), name: String(s.name ?? '').trim(),
        class: resolveClassName(s.class, data.classAliases), english: Number(s.english) || 0,
        target: s.target ?? '', weakTags: s.weakTags ?? '', phone: s.phone ?? '', seat: s.seat ?? '',
        evaluationTags: Array.isArray(s.evaluationTags) ? s.evaluationTags.map(String) : [],
        evaluationNote: String(s.evaluationNote ?? '').trim(),
        archivedAt: s.archivedAt || new Date().toISOString()
      })).filter(s => s.id && s.name && s.class) : [];
      data.studentTags = Array.isArray(data.studentTags) ? data.studentTags.map((tag, index) => ({
        id: String(tag?.id || `tag_${index}_${uid()}`), name: String(tag?.name || '').trim().slice(0, 40),
        locked: Boolean(tag?.locked), createdAt: tag?.createdAt || new Date().toISOString()
      })).filter(tag => tag.name) : [];
      data.studentTags = pruneEvaluationTags(data.studentTags);
      const tagIds = new Set(data.studentTags.map(tag => tag.id));
      data.students.forEach(student => { student.evaluationTags = student.evaluationTags.filter(tagId => tagIds.has(tagId)); });
      data.classes = Array.isArray(data.classes) ? [...new Set(data.classes.map(cls => resolveClassName(cls, data.classAliases)).filter(Boolean))] : [];
      data.students.forEach(s => { if (s.class && !data.classes.includes(s.class)) data.classes.push(s.class); });
      data.exams = Array.isArray(data.exams) ? data.exams : [];
      data.archivedExams = Array.isArray(data.archivedExams) ? data.archivedExams : [];
      [...data.exams, ...data.archivedExams].forEach((exam, index) => {
        if (!exam.id) exam.id = 'exam_' + index + '_' + uid();
        exam.fullScore = Number(exam.fullScore) > 0 ? Number(exam.fullScore) : 100;
        if (!exam.scores || typeof exam.scores !== 'object') exam.scores = {};
        Object.entries(exam.scores).forEach(([studentId, raw]) => {
          const source = raw && typeof raw === 'object' ? raw : { 英语: raw };
          const score = source.英语 ?? source.english_total ?? source.total_score;
          const rank = source.gradeRank ?? source.grade_rank ?? source.年级排名;
          const numericScore = score === '' || score == null ? null : Number(score);
          const numericRank = rank === '' || rank == null ? null : Number(rank);
          exam.scores[studentId] = {
            ...source,
            英语: Number.isFinite(numericScore) ? numericScore : '',
            gradeRank: Number.isInteger(numericRank) && numericRank > 0 ? numericRank : null,
            classAtExam: resolveClassName(source.classAtExam ?? source.class_at_exam ?? '', data.classAliases) || null
          };
        });
        exam.examKind = exam.examKind === 'entrance' ? 'entrance' : 'regular';
        exam.tierLines = normalizeTierLines(exam.tierLines, exam.fullScore);
        exam.scores = Object.fromEntries(Object.entries(exam.scores).map(([sid, score]) => [sid, {
          ...(score || {}),
          attendanceStatus: score?.attendanceStatus === 'absent' ? 'absent' : 'present'
        }]));
        const rawClassRanks = exam.classGradeRanks && typeof exam.classGradeRanks === 'object' ? exam.classGradeRanks : {};
        exam.classGradeRanks = Object.fromEntries(Object.entries(rawClassRanks).filter(([, rank]) => Number.isInteger(Number(rank)) && Number(rank) > 0).map(([cls, rank]) => [resolveClassName(cls, data.classAliases), Number(rank)]));
      });
      data.currentExamId = data.exams.some(exam => exam.id === data.currentExamId) ? data.currentExamId : (data.exams[0]?.id || '');
      currentExamId = data.currentExamId;
      data.settings = { ...base.settings, ...(data.settings || {}) };
      if (data.settings.dictRowPad == null) data.settings.dictRowPad = 8;
      if (data.settings.dictColW == null) data.settings.dictColW = 60;
      data.recitations = Array.isArray(data.recitations) ? data.recitations.map(item => {
        const status = item?.status && typeof item.status === 'object' ? Object.fromEntries(
          Object.entries(item.status).map(([sid, raw]) => [sid, normalizeReciteStatus(raw)])
        ) : {};
        return {
          ...item,
          id: String(item?.id || uid()),
          title: String(item?.title || '未命名背诵任务'),
          scope: String(item?.scope || ''),
          status
        };
      }) : [];
      data.writings = Array.isArray(data.writings) ? data.writings.map(item => {
        const legacyFullScore = Number(item?.fullScore ?? item?.maxScore ?? item?.pass ?? 12);
        return {
          ...item,
          id: String(item?.id || uid()),
          title: String(item?.title || '未命名写作任务'),
          date: String(item?.date || ''),
          fullScore: Number.isFinite(legacyFullScore) && legacyFullScore > 0 ? legacyFullScore : 12,
          scores: item?.scores && typeof item.scores === 'object' ? item.scores : {}
        };
      }) : [];
      data.errors = Array.isArray(data.errors) ? data.errors.map(item => ({ ...item, type: String(item.type || '').replaceAll('任务型阅读', '阅读理解') })) : [];
      data.paperDocuments = Array.isArray(data.paperDocuments) ? data.paperDocuments.filter(item => item && item.id && item.name && (item.content || item.attachmentId)) : [];
      data.archivedDocuments = Array.isArray(data.archivedDocuments) ? data.archivedDocuments.filter(item => item && item.id && item.name) : [];
      data.critical = Array.isArray(data.critical) ? data.critical : [];
      // 教考衔接已下线：旧备份中的字段不再加载，也不再写回数据库。
      delete data.alignments;
      data.todos = Array.isArray(data.todos) ? data.todos.map((todo, index) => {
        const legacyDate = String(todo?.date || todo?.deadline || '').trim();
        const startTime = /^\d{2}:\d{2}$/.test(String(todo?.startTime || todo?.time || '')) ? String(todo.startTime || todo.time) : '';
        const endTime = /^\d{2}:\d{2}$/.test(String(todo?.endTime || '')) ? String(todo.endTime) : '';
        const rawRepeat = todo?.repeatRule && typeof todo.repeatRule === 'object' ? todo.repeatRule : null;
        const repeatRule = rawRepeat?.frequency === 'weekly' && Array.isArray(rawRepeat.weekdays)
          ? { frequency: 'weekly', weekdays: rawRepeat.weekdays.map(Number).filter(day => day >= 0 && day <= 6), startDate: String(rawRepeat.startDate || legacyDate), endDate: String(rawRepeat.endDate || rawRepeat.startDate || legacyDate) }
          : null;
        return {
          id: String(todo?.id || `todo_${index}_${uid()}`),
          title: String(todo?.title || '').trim(),
          priority: ['高', '中', '低'].includes(todo?.priority) ? todo.priority : '中',
          date: /^\d{4}-\d{2}-\d{2}$/.test(legacyDate) ? legacyDate : '',
          deadline: /^\d{4}-\d{2}-\d{2}$/.test(legacyDate) ? legacyDate : '',
          time: startTime,
          startTime,
          endTime,
          allDay: todo?.allDay === true || !startTime,
          repeatRule,
          completedDates: todo?.completedDates && typeof todo.completedDates === 'object' ? todo.completedDates : {},
          notes: String(todo?.notes || '').trim(),
          done: Boolean(todo?.done),
          createdAt: todo?.createdAt || new Date().toISOString()
        };
      }).filter(todo => todo.title) : [];
      // 已下线模块的旧备份字段不再加载，也不再写回数据库。
      delete data.customs;
      data.dictation = data.dictation && typeof data.dictation === 'object' ? data.dictation : {};
      data.dictationNames = Array.isArray(data.dictationNames) ? data.dictationNames : [];
      const nRounds = data.dictationNames.length;
      const rawDictationRanges = Array.isArray(data.dictationRanges) ? data.dictationRanges : [];
      data.dictationRanges = Array.from({ length: nRounds }, (_, index) => normalizeDictationRanges(rawDictationRanges[index]));
      // 清理旧版本首次启动时自动生成的两个空白占位轮次；有过成绩或分数段配置的用户轮次保留。
      const isLegacyBlankDefault = data.dictationNames.length === 2
        && data.dictationNames[0] === '自定义1'
        && data.dictationNames[1] === '自定义2'
        && !data.dictationRanges.some(range => range.length)
        && !Object.values(data.dictation).some(values => Array.isArray(values) && values.some(value => value !== '' && value != null));
      if (isLegacyBlankDefault) {
        data.dictationNames = [];
        data.dictationRanges = [];
        Object.values(data.dictation).forEach(values => { if (Array.isArray(values)) values.splice(0, 2); });
      }
      data.students.forEach(s => {
        let v = data.dictation[s.id];
        if (!Array.isArray(v)) v = data.dictation[s.id] = (v === '' || v == null) ? [] : [v];
        while (v.length < nRounds) v.push('');
      });
      // ── homework 兼容初始化 ──
      data.homeworkTasks = Array.isArray(data.homeworkTasks) ? data.homeworkTasks : [];
      data.homeworkRecords = data.homeworkRecords && typeof data.homeworkRecords === 'object' ? data.homeworkRecords : {};
      return data;
    }

    async function loadData() {
      if (DATABASE_MODE) {
        const [terms, currentTerm, runtime] = await Promise.all([
          apiRequest('/api/v1/terms'),
          apiRequest('/api/v1/terms/current'),
          apiRequest('/api/v1/runtime').catch(() => null)
        ]);
        availableTerms = terms;
        currentTermId = Number(currentTerm.id);
        const payload = await apiRequest(`/api/v1/terms/${currentTermId}/workspace-state`);
        if (runtime) {
          appRuntimeVersion = `v${runtime.version}`;
          appRuntimeSchema = runtime.schema || '—';
        }
        databaseRevision = payload.revision;
        databaseWriteBlocked = false;
        databaseSaveChain = Promise.resolve(true);
        state = migrate(payload.state || createDefaultState());
        try {
          const attachments = await apiRequest(`/api/v1/attachments?term_id=${currentTermId}`);
          // TeachMate 直接上传的文件只属于对话上下文，不应被 WorkBench
          // 原卷资料库当作 paperDocuments 展示或进入删除流程。未标记 source
          // 的旧附件保持兼容，明确标记为 paper_documents 的才进入资料库。
          const remoteDocuments = (Array.isArray(attachments) ? attachments : [])
            .filter(item => {
              const source = item && item.metadata && item.metadata.source;
              return !source || source === 'paper_documents';
            })
            .map(item => ({
            id: `attachment-${item.id}`,
            attachmentId: Number(item.id),
            name: item.original_name,
            extension: String(item.original_name || '').split('.').pop()?.toUpperCase() || '文件',
            type: item.mime_type || '',
            size: Number(item.size_bytes) || 0,
            content: '',
            createdAt: item.created_at || new Date().toISOString()
          }));
          const localDocuments = (state.paperDocuments || []).filter(item => !item.attachmentId);
          const archivedAttachmentIds = new Set((state.archivedDocuments || []).filter(item => item && item.attachmentId).map(item => String(item.attachmentId)));
          state.paperDocuments = [...localDocuments, ...remoteDocuments.filter(item => !archivedAttachmentIds.has(String(item.attachmentId)))];
        } catch (error) {
          // 老版本后端没有附件接口时，继续使用工作台状态中的兼容记录。
          if (error.status !== 404) console.warn('附件列表加载失败，保留工作台中的本地资料记录', error);
        }
        dictationClass = '';
        homeworkClass = '';
        homeworkActiveTaskId = '';
        return;
      }
      try {
        const raw = localStorage.getItem(LS_KEY);
        state = migrate(raw ? JSON.parse(raw) : createDefaultState());
        dictationClass = '';
        homeworkClass = '';
        homeworkActiveTaskId = '';
      } catch (e) {
        console.error(e);
        state = createDefaultState();
      }
    }

    function renderTermSwitcher() {
      const container = document.getElementById('termSwitcher');
      const select = document.getElementById('termSelect');
      if (!container || !select) return;
      container.hidden = !DATABASE_MODE;
      if (!DATABASE_MODE) return;
      select.innerHTML = availableTerms.map(term => `<option value="${term.id}">${escapeHtml(term.name)}</option>`).join('');
      select.value = String(currentTermId || '');
      select.disabled = termSwitchInProgress;
    }

    function resetTermViewState() {
      currentExamId = state.currentExamId || state.exams[0]?.id || '';
      dashboardClass = '';
      studentClass = '';
      scoreClass = '';
      reciteClass = '';
      writingClass = '';
      riskCloudClass = '';
      dictationClass = '';
      homeworkClass = '';
      homeworkActiveTaskId = '';
      homeworkBatchMode = false;
      reciteBatchMode = false;
      writingBatchMode = false;
      stuSearchText = '';
      dictationSearchText = '';
      homeworkSearchText = '';
      reciteSearchText = '';
      writingSearchText = '';
      selectedStudentIds.clear();
      selectedDictationRounds.clear();
      selectedHomeworkStudents.clear();
      selectedReciteTasks.clear();
      selectedWritingTasks.clear();
      pendingScoreEdits.clear();
      scoreUndoStack.length = 0;
    }

    async function switchTerm(termId) {
      const nextId = Number(termId);
      if (!DATABASE_MODE || !nextId || nextId === currentTermId || termSwitchInProgress) return;
      if (pendingScoreEdits.size) {
        showToast('有尚未保存的成绩，请先保存或撤销后再切换学期', 'error');
        renderTermSwitcher();
        return;
      }
      await databaseSaveChain;
      if (databaseWriteBlocked) {
        showToast('当前页面存在保存冲突，请刷新后再切换学期', 'error');
        renderTermSwitcher();
        return;
      }
      termSwitchInProgress = true;
      renderTermSwitcher();
      try {
        await apiRequest('/api/v1/terms/current', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ term_id: nextId })
        });
        await loadData();
        resetTermViewState();
        // 成长森林缓存按学期隔离：切换学期必须作废旧快照与勾选，避免污染新页面（方案 §1.2）。
        if (typeof window.growthInvalidate === 'function') window.growthInvalidate();
        if (typeof window.growthResetSelection === 'function') window.growthResetSelection();
        renderNav();
        render();
        if (activeTab === 'teachmate' && typeof tmRefreshAvailableExams === 'function') tmRefreshAvailableExams(currentTermId);
        showToast(`已切换到${availableTerms.find(term => term.id === currentTermId)?.name || '所选学期'}`);
      } catch (error) {
        showToast(error.message || '学期切换失败', 'error');
      } finally {
        termSwitchInProgress = false;
        renderTermSwitcher();
      }
    }

    function saveData() {
      if (DATABASE_MODE) {
        if (databaseWriteBlocked) {
          const error = new Error('检测到数据版本冲突，请刷新页面后再操作');
          error.status = 409;
          return Promise.reject(error);
        }
        const snapshot = JSON.stringify(state);
        const targetTermId = currentTermId;
        const write = () => persistDatabaseState(snapshot, targetTermId);
        const result = databaseSaveChain.then(write);
        databaseSaveChain = result.catch(() => true);
        return result;
      }
      try {
        localStorage.setItem(LS_KEY, JSON.stringify(state));
        return true;
      } catch (e) {
        showToast('保存失败：' + e.message, 'error');
        return false;
      }
    }

    // 所有高风险写操作都应通过这个入口：先保存，后刷新和提示；失败时恢复内存快照。
    // 旧页面中的轻量交互仍可直接调用 saveData()，但数据库写入本身始终经过串行队列。
    let mutationInProgress = false;

    function cloneStateSnapshot(value = state) {
      try { return JSON.parse(JSON.stringify(value)); }
      catch (error) { console.error('无法创建数据快照', error); return null; }
    }

    function commitMutation(mutator, options = {}) {
      const { successMessage = '', rerender = true, renderNavigation = false, skipSave = false, rollbackSnapshot = null, rollbackContext = null } = options;
      if (mutationInProgress) return { ok: false, error: new Error('已有保存操作正在进行') };
      mutationInProgress = true;
      const snapshot = rollbackSnapshot || cloneStateSnapshot();
      const contextSnapshot = rollbackContext || { currentExamId };
      const rollback = () => {
        if (snapshot) state = snapshot;
        currentExamId = contextSnapshot.currentExamId;
        if (renderNavigation) renderNav();
        if (rerender) render();
      };
      const fail = error => {
        mutationInProgress = false;
        rollback();
        if (error) showToast(`保存失败：${error.message || error}`, 'error');
        return { ok: false, error };
      };
      const finish = (saved, result) => {
        mutationInProgress = false;
        if (saved === false) {
          rollback();
          return { ok: false, result };
        }
        if (renderNavigation) renderNav();
        if (rerender) render();
        if (successMessage) showToast(typeof successMessage === 'function' ? successMessage(result) : successMessage);
        return { ok: true, result };
      };
      try {
        // Keep browser-storage mutations synchronous for callers that need to
        // read the freshly saved state immediately after a click. Database
        // persistence still remains asynchronous and is awaited when needed.
        const mutationResult = mutator();
        const saveMutation = result => {
          if (skipSave) return finish(true, result);
          const saveResult = saveData();
          if (saveResult && typeof saveResult.then === 'function') {
            return saveResult.then(saved => finish(saved, result)).catch(fail);
          }
          return finish(saveResult, result);
        };
        if (mutationResult && typeof mutationResult.then === 'function') {
          return mutationResult.then(saveMutation).catch(fail);
        }
        return saveMutation(mutationResult);
      } catch (error) {
        return fail(error);
      }
    }

    async function persistDatabaseState(snapshot, targetTermId) {
      if (databaseWriteBlocked) {
        return false;
      }
      try {
        if (targetTermId !== currentTermId) throw new Error('学期已切换，已取消旧页面的保存请求');
        const payload = await apiRequest(`/api/v1/terms/${targetTermId}/workspace-state`, {
          method: 'PUT',
          // 原卷会以 Base64 随工作台状态保存，可能超过浏览器 keepalive 请求体限制；
          // 这是用户主动保存操作，不需要在页面卸载时 keepalive。
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${workbenchToken}`
          },
          body: JSON.stringify({
            state: (() => {
              const databaseState = JSON.parse(snapshot);
              databaseState.paperDocuments = Array.isArray(databaseState.paperDocuments)
                ? databaseState.paperDocuments.map(document => {
                  if (!document?.attachmentId) return document;
                  const { content, ...metadata } = document;
                  return metadata;
                })
                : [];
              return databaseState;
            })(),
            expected_revision: databaseRevision
          })
        });
        databaseRevision = payload.revision;
        return true;
      } catch (error) {
        if (error.status === 409) databaseWriteBlocked = true;
        console.error(error);
        if (databaseWriteBlocked) {
          showToast(`数据库保存失败：${error.message}`, 'error');
          openModal('数据版本冲突', '<p>另一个工作台页面已经修改了数据库。为防止覆盖数据，本页面已停止保存。</p><p>请刷新页面后再继续操作。</p>');
        }
        throw error;
      }
    }

    async function createDatabaseBackup() {
      if (!DATABASE_MODE) return true;
      await apiRequest(`/api/v1/terms/${currentTermId}/workspace-state/backup`, { method: 'POST' });
      return true;
    }

    function uid() { return Date.now().toString(36) + Math.random().toString(36).slice(2, 8); }

    function getFilteredStudents(cls) {
      let list = state.students;
      if (cls) {
        const resolvedClass = resolveClassName(cls);
        list = list.filter(s => resolveClassName(s.class) === resolvedClass);
      }
      return list;
    }

    function normalizeSearch(value) { return String(value ?? '').trim().toLowerCase(); }

    function filterStudentsByQuery(list, query) {
      const normalized = normalizeSearch(query);
      if (!normalized) return [...list];
      return list.filter(s => normalizeSearch(s.name).includes(normalized) || normalizeSearch(s.id).includes(normalized));
    }

    function getVisibleStudents(cls, query) {
      return filterStudentsByQuery(getFilteredStudents(cls), query);
    }

    function resetStudentViewState() {
      stuSearchText = '';
      studentPage = 1;
      dictationSearchText = '';
      batchMode = false;
      selectedStudentIds.clear();
    }

    function cleanupStudentReferences(ids) {
      const removed = new Set(ids);
      state.exams.forEach(exam => ids.forEach(id => { if (exam.scores) delete exam.scores[id]; }));
      ids.forEach(id => delete state.dictation[id]);
      state.recitations.forEach(item => ids.forEach(id => { if (item.status) delete item.status[id]; }));
      state.writings.forEach(item => ids.forEach(id => { if (item.scores) delete item.scores[id]; }));
      state.critical = state.critical.filter(item => !removed.has(item.sid));
    }

    function removeStudents(ids) {
      const uniqueIds = [...new Set(ids)].filter(id => state.students.some(s => s.id === id));
      if (!uniqueIds.length) return 0;
      const result = commitMutation(() => {
        state.students = state.students.filter(s => !uniqueIds.includes(s.id));
        cleanupStudentReferences(uniqueIds);
        selectedStudentIds.clear();
        return uniqueIds.length;
      });
      return result && typeof result.then === 'function' ? result.then(value => value.ok ? value.result : 0) : (result.ok ? result.result : 0);
    }

    function archiveStudents(ids) {
      const uniqueIds = [...new Set(ids)].filter(id => state.students.some(student => student.id === id));
      if (!uniqueIds.length) return 0;
      const result = commitMutation(() => {
        state.archivedStudents = Array.isArray(state.archivedStudents) ? state.archivedStudents : [];
        const archivedAt = new Date().toISOString();
        state.students.filter(student => uniqueIds.includes(student.id)).forEach(student => {
          state.archivedStudents = state.archivedStudents.filter(item => item.id !== student.id);
          state.archivedStudents.push({ ...student, archivedAt });
        });
        state.students = state.students.filter(student => !uniqueIds.includes(student.id));
        selectedStudentIds.clear();
        studentPage = 1;
        return uniqueIds.length;
      });
      return result && typeof result.then === 'function' ? result.then(value => value.ok ? value.result : 0) : (result.ok ? result.result : 0);
    }

    function restoreArchivedStudent(id) {
      const student = (state.archivedStudents || []).find(item => item.id === id);
      if (!student || state.students.some(item => item.id === id)) return false;
      const result = commitMutation(() => {
        const { archivedAt, ...restored } = student;
        state.students.push(restored);
        state.archivedStudents = state.archivedStudents.filter(item => item.id !== id);
        if (restored.class && !state.classes.includes(restored.class)) state.classes.push(restored.class);
        return true;
      });
      return result && typeof result.then === 'function' ? result.then(value => value.ok) : result.ok;
    }

    function purgeArchivedStudent(id) {
      if (!(state.archivedStudents || []).some(item => item.id === id)) return false;
      const result = commitMutation(() => {
        state.archivedStudents = state.archivedStudents.filter(item => item.id !== id);
        cleanupStudentReferences([id]);
        return true;
      });
      return result && typeof result.then === 'function' ? result.then(value => value.ok) : result.ok;
    }

    function getStudentPageData() {
      const filtered = getVisibleStudents(studentClass, stuSearchText);
      const totalPages = Math.max(1, Math.ceil(filtered.length / studentPageSize));
      studentPage = Math.min(Math.max(1, studentPage), totalPages);
      const start = (studentPage - 1) * studentPageSize;
      return { filtered, pageItems: filtered.slice(start, start + studentPageSize), totalPages, start };
    }

    function getAvailableClasses() {
      const configured = Array.isArray(state?.classes) ? state.classes : [];
      if (configured.length) return configured;
      return [...new Set((state?.students || []).map(student => student.class).filter(Boolean))].sort();
    }
    function normalizeClassFilter(value) {
      const selected = String(value || '');
      return getAvailableClasses().includes(selected) ? selected : '';
    }
    function setGlobalClassFilter(value) {
      const selected = normalizeClassFilter(value);
      dashboardClass = selected;
      studentClass = selected;
      scoreClass = selected;
      dictationClass = selected;
      reciteClass = selected;
      writingClass = selected;
      riskCloudClass = selected;
      stuSearchText = '';
      dictationSearchText = '';
      reciteSearchText = '';
      writingSearchText = '';
      selectedStudentIds.clear();
      studentPage = 1;
      // 成长森林跟随全局班级范围，并清空批量勾选，避免保留隐藏选择（方案 §1.2）。
      if (typeof window.growthSetClass === 'function') window.growthSetClass(selected);
      else if (typeof window.growthResetSelection === 'function') window.growthResetSelection();
      return selected;
    }
    function classSelectOptions(selected = '') {
      return `<option value="">全部班级</option>${getAvailableClasses().map(item => `<option value="${escapeAttr(item)}" ${item === selected ? 'selected' : ''}>${escapeHtml(formatClassLabel(item))}</option>`).join('')}`;
    }
    // 重点关注区域保留筛选值，但不在界面暴露具体班级编号。
    function riskClassSelectOptions(selected = '') {
      const classes = getAvailableClasses();
      return `<option value="">全部班级</option>${classes.map((item, index) => `<option value="${escapeAttr(item)}" ${item === selected ? 'selected' : ''}>班级${index + 1}</option>`).join('')}`;
    }
    function getCurrentClass() { return dashboardClass; }
    function getScoreClass() { return scoreClass; }

    function getCurrentExam() {
      const exam = state.exams.find(item => item.id === currentExamId) || state.exams[0];
      if (exam && currentExamId !== exam.id) {
        currentExamId = exam.id;
        state.currentExamId = exam.id;
      }
      return exam;
    }

    function scoreEditKey(examId, studentId) { return `${examId || ''}::${studentId || ''}`; }

    function getExamScore(exam, student) {
      const pending = pendingScoreEdits.get(scoreEditKey(exam?.id, student?.id));
      const value = pending ? pending.value : exam?.scores?.[student.id]?.英语;
      return value === '' || value == null || Number.isNaN(Number(value)) ? null : Number(value);
    }

    function stageScoreEdit(exam, student, value) {
      if (!exam || !student) return false;
      const key = scoreEditKey(exam.id, student.id);
      const existing = pendingScoreEdits.get(key);
      const originalRaw = exam.scores?.[student.id]?.英语;
      const original = originalRaw === '' || originalRaw == null || Number.isNaN(Number(originalRaw)) ? null : Number(originalRaw);
      const before = existing ? existing.value : original;
      if (before === value) return false;
      scoreUndoStack.push({ key, before });
      if (value === (existing ? existing.original : original)) pendingScoreEdits.delete(key);
      else pendingScoreEdits.set(key, { examId: exam.id, studentId: student.id, original: existing ? existing.original : original, value });
      return true;
    }

    async function savePendingScoreEdits() {
      if (!pendingScoreEdits.size) return true;
      pendingScoreEdits.forEach(edit => {
        const exam = state.exams.find(item => item.id === edit.examId);
        const student = state.students.find(item => item.id === edit.studentId);
        if (!exam || !student) return;
        exam.scores = exam.scores || {};
        exam.scores[student.id] = exam.scores[student.id] || {};
        exam.scores[student.id].英语 = edit.value == null ? '' : edit.value;
        exam.scores[student.id].classAtExam = exam.scores[student.id].classAtExam || student.class || null;
      });
      const saved = await Promise.resolve(saveData());
      if (saved !== false) {
        pendingScoreEdits.clear();
        scoreUndoStack.length = 0;
      }
      return saved !== false;
    }

    function undoPendingScoreEdit() {
      const last = scoreUndoStack.pop();
      if (!last) return false;
      const edit = pendingScoreEdits.get(last.key);
      if (!edit) return false;
      if (last.before === edit.original) pendingScoreEdits.delete(last.key);
      else pendingScoreEdits.set(last.key, { ...edit, value: last.before });
      return true;
    }

    function getStudentGradeRank(exam, student) {
      const value = exam?.scores?.[student.id]?.gradeRank;
      const rank = Number(value);
      return Number.isInteger(rank) && rank > 0 ? rank : null;
    }

    function getScoredStudents(exam, students) {
      return students.map(student => ({ student, score: getExamScore(exam, student) }))
        .filter(item => item.score !== null);
    }

    function calculateClassRanks(exam, students = state.students) {
      const byClass = new Map();
      students.forEach(student => {
        const score = getExamScore(exam, student);
        if (score === null) return;
        const classAtExam = resolveClassName(exam?.scores?.[student.id]?.classAtExam || student.class);
        if (!byClass.has(classAtExam)) byClass.set(classAtExam, []);
        byClass.get(classAtExam).push({ student, score });
      });
      const ranks = {};
      byClass.forEach(items => {
        items.sort((a, b) => b.score - a.score);
        let previousScore = null;
        let currentRank = 0;
        items.forEach((item, index) => {
          if (item.score !== previousScore) {
            currentRank = index + 1;
            previousScore = item.score;
          }
          ranks[item.student.id] = currentRank;
        });
      });
      return ranks;
    }

    function percentage(count, total) {
      return total ? Number((count / total * 100).toFixed(1)) : 0;
    }

    function calculateExamSummary(exam, students) {
      const scores = getScoredStudents(exam, students).map(item => item.score);
      const fullScore = Number(exam?.fullScore) || 100;
      const passScore = Number(state.settings.pass) * fullScore / 100;
      const excellentScore = Number(state.settings.excellent) * fullScore / 100;
      const total = scores.length;
      return {
        total,
        average: total ? Number((scores.reduce((sum, score) => sum + score, 0) / total).toFixed(1)) : null,
        highest: total ? Math.max(...scores) : null,
        excellentRate: percentage(scores.filter(score => score >= excellentScore).length, total),
        passRate: percentage(scores.filter(score => score >= passScore).length, total),
        excellentScore,
        passScore,
      };
    }

    function calculateGradeRankDistribution(exam, students) {
      const scored = getScoredStudents(exam, students);
      const ranks = scored.map(item => getStudentGradeRank(exam, item.student)).filter(rank => rank !== null);
      const validTotal = ranks.length;
      const bucketCount = validTotal ? Math.ceil(Math.max(...ranks) / 100) : 0;
      const buckets = Array.from({ length: bucketCount }, (_, index) => {
        const start = index * 100 + 1;
        const end = (index + 1) * 100;
        const count = ranks.filter(rank => rank >= start && rank <= end).length;
        return { label: `${start}–${end}`, count, percentage: percentage(count, validTotal) };
      });
      return { buckets, validTotal, missing: scored.length - validTotal };
    }

    function calculateAcademicLevelDistribution(exam, students) {
      const tierLines = normalizeTierLines(exam?.tierLines, exam?.fullScore);
      const scored = getScoredStudents(exam, students);
      if (!tierLines) return { configured: false, total: scored.length, levels: [] };
      const { a, b, c } = tierLines;
      const counts = {
        A: scored.filter(item => item.score >= a).length,
        B: scored.filter(item => item.score >= b && item.score < a).length,
        C: scored.filter(item => item.score >= c && item.score < b).length,
        D: scored.filter(item => item.score < c).length,
      };
      const total = scored.length;
      const ranges = { A: `≥ ${fmt(a)}`, B: `${fmt(b)}–＜${fmt(a)}`, C: `${fmt(c)}–＜${fmt(b)}`, D: `＜ ${fmt(c)}` };
      return {
        configured: true,
        total,
        tierLines,
        levels: ['A', 'B', 'C', 'D'].map(level => ({ level, count: counts[level], percentage: percentage(counts[level], total), range: ranges[level] })),
      };
    }

    function getRecentExams(limit = 5) {
      return state.exams
        .map((exam, index) => ({ exam, index }))
        .sort((left, right) => String(right.exam.date || '').localeCompare(String(left.exam.date || '')) || right.index - left.index)
        .slice(0, limit)
        .map(item => item.exam);
    }

    function stableRiskColor(studentId) {
      // 使用学生 ID 生成稳定的伪随机颜色：刷新页面后颜色不跳变，且不改变 C/D 卡片的原有配色。
      const palette = ['#1a73e8', '#7b1fa2', '#d81b60', '#00897b', '#ef6c00', '#5e35b1', '#c2185b', '#2e7d32', '#1565c0', '#8e24aa', '#00695c', '#ad1457'];
      let hash = 0;
      for (const char of String(studentId)) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
      return palette[hash % palette.length];
    }

    function calculateRiskClouds(cls, limit = 5) {
      const exams = getRecentExams(limit);
      const students = getFilteredStudents(cls);
      const groups = { C: [], D: [] };
      students.forEach(student => {
        const counts = { C: 0, D: 0 };
        const records = exams.map(exam => {
          const score = getExamScore(exam, student);
          const tier = getExamTier(exam, score);
          if (tier === 'C' || tier === 'D') counts[tier] += 1;
          return { exam, score, tier, gradeRank: getStudentGradeRank(exam, student) };
        });
        const rankedRecords = records.filter(record => record.gradeRank !== null);
        const rankDelta = rankedRecords.length >= 2 ? rankedRecords[0].gradeRank - rankedRecords[1].gradeRank : null;
        ['C', 'D'].forEach(level => {
          if (!counts[level]) return;
          const evidence = [`近${exams.length}次考试中${counts[level]}次处于${level}层`];
          if (rankDelta > 0) evidence.push(`最近一次年级排名下降${rankDelta}位`);
          else if (rankDelta < 0) evidence.push(`最近一次年级排名上升${Math.abs(rankDelta)}位`);
          else if (rankDelta === 0) evidence.push('最近两次年级排名持平');
          else if (records[0]?.tier === level) evidence.push(`最近一次仍处于${level}层`);
          groups[level].push({
            student,
            count: counts[level],
            evidence,
            rankDelta,
            latestTier: records[0]?.tier || null,
            color: stableRiskColor(student.id),
            fontSize: 16 + counts[level] * 5
          });
        });
      });
      Object.values(groups).forEach(group => group.sort((left, right) => right.count - left.count || String(left.student.name).localeCompare(String(right.student.name))));
      return { exams, groups };
    }

    function calculateHistoricalMetrics(cls) {
      const students = getFilteredStudents(cls);
      return [...state.exams]
        .sort((left, right) => String(left.date || '9999-12-31').localeCompare(String(right.date || '9999-12-31')) || String(left.name).localeCompare(String(right.name)))
        .map(exam => {
          const summary = calculateExamSummary(exam, students);
          const levels = calculateAcademicLevelDistribution(exam, students);
          const levelMap = Object.fromEntries(levels.levels.map(item => [item.level, item.percentage]));
          const classRank = cls ? Number(exam.classGradeRanks?.[resolveClassName(cls)]) : null;
          return {
            exam,
            label: `${exam.date ? exam.date.slice(5) + ' ' : ''}${exam.name}`,
            average: summary.average,
            classGradeRank: Number.isInteger(classRank) && classRank > 0 ? classRank : null,
            aRate: levels.configured ? levelMap.A : null,
            bRate: levels.configured ? levelMap.B : null,
            excellentRate: summary.total ? summary.excellentRate : null,
            passRate: summary.total ? summary.passRate : null,
          };
        });
    }

    function disposeScoreCharts() {
      scoreCharts.forEach(chart => {
        try { chart.dispose(); } catch (error) {}
      });
      scoreCharts = [];
    }

    async function renderScoreCharts() {
      disposeScoreCharts();
      const containers = ['trend-average-chart', 'trend-rank-chart', 'trend-rate-chart']
        .map(id => document.getElementById(id)).filter(Boolean);
      if (!containers.length) return;
      if (typeof echarts === 'undefined' && typeof navigator !== 'undefined' && /jsdom/i.test(navigator.userAgent || '')) {
        containers.forEach(container => { container.innerHTML = '<div class="trend-empty">本地图表组件未加载，请重新启动工作台。</div>'; });
        return;
      }
      try {
        await ensureEcharts();
      } catch (error) {
        containers.forEach(container => { container.innerHTML = '<div class="trend-empty">图表组件加载失败，请重试。</div>'; });
        return;
      }
      if (typeof echarts === 'undefined') {
        containers.forEach(container => { container.innerHTML = '<div class="trend-empty">本地图表组件未加载，请重试。</div>'; });
        return;
      }
      const cls = getScoreClass();
      const metrics = calculateHistoricalMetrics(cls);
      const labels = metrics.map(item => item.label);
      const common = {
        animationDuration: 350,
        tooltip: { trigger: 'axis', confine: true },
        grid: { left: 52, right: 24, top: 46, bottom: 58 },
        xAxis: { type: 'category', data: labels, axisLabel: { rotate: labels.length > 5 ? 25 : 0, hideOverlap: true } },
      };
      const create = (id, option) => {
        const node = document.getElementById(id);
        if (!node) return;
        const chart = echarts.init(node);
        chart.setOption(option);
        scoreCharts.push(chart);
      };
      create('trend-average-chart', {
        ...common,
        tooltip: { trigger: 'axis', confine: true, formatter(params) { const point = metrics[params?.[0]?.dataIndex]; return point ? `${escapeHtml(point.exam.name)}<br>${escapeHtml(point.exam.date || '未填写日期')}<br>平均分：${point.average ?? '—'}` : ''; } },
        legend: { data: ['平均分'] },
        yAxis: { type: 'value', name: '分数', scale: true },
        series: [{ name: '平均分', type: 'line', smooth: true, connectNulls: false, symbolSize: 8, data: metrics.map(item => item.average), lineStyle: { width: 3 } }],
      });
      if (cls) {
        create('trend-rank-chart', {
          ...common,
          tooltip: { trigger: 'axis', confine: true, formatter(params) { const point = metrics[params?.[0]?.dataIndex]; return point ? `${escapeHtml(point.exam.name)}<br>${escapeHtml(formatClassLabel(cls))}<br>年级排名：${point.classGradeRank ?? '—'}` : ''; } },
          legend: { data: ['班级年级名次'] },
          yAxis: { type: 'value', name: '名次', inverse: true, min: 1, minInterval: 1, scale: true },
          series: [{ name: '班级年级名次', type: 'line', smooth: true, connectNulls: false, symbolSize: 8, data: metrics.map(item => item.classGradeRank), lineStyle: { width: 3, color: '#7c4dff' }, itemStyle: { color: '#7c4dff' } }],
        });
      }
      create('trend-rate-chart', {
        ...common,
        legend: { data: ['A率', 'B率', '优秀率', '及格率'], selected: { 优秀率: false, 及格率: false } },
        yAxis: { type: 'value', name: '比例', min: 0, max: 100, axisLabel: { formatter: '{value}%' } },
        series: [
          { name: 'A率', type: 'line', connectNulls: false, data: metrics.map(item => item.aRate) },
          { name: 'B率', type: 'line', connectNulls: false, data: metrics.map(item => item.bRate) },
          { name: '优秀率', type: 'line', connectNulls: false, data: metrics.map(item => item.excellentRate) },
          { name: '及格率', type: 'line', connectNulls: false, data: metrics.map(item => item.passRate) },
        ],
      });
    }

    window.addEventListener('resize', () => scoreCharts.forEach(chart => {
      try { chart.resize(); } catch (error) {}
    }));

    function avg(arr) { return arr.length ? (arr.reduce((a,b)=>a+b,0)/arr.length).toFixed(1) : '0.0'; }

    function fmt(n) { return Number(n).toFixed(1); }

    function escapeHtml(value) {
      return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
    }

    function escapeAttr(value) { return escapeHtml(value); }

    function escapeXml(value) {
      return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&apos;');
    }
