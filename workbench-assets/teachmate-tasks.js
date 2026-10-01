/* Teaching workspaces: explicit adoption, bounded context and recoverable edits. */
(function () {
  'use strict';
  var tasks = [], active = null, resourceKey = '', generation = 0, loadedTerm = null;
  var busy = false, error = '', selectedArtifact = null, showArchived = false;
  var sourceRun = null, focus = null, feedbackRunId = null;
  var onTaskCreated = null;
  var panelOpen = false, panelTab = 'practice', canvas = null, canvasKey = 0, canvasChat = false, canvasNode = null, roster = [];
  var phases = { diagnose: '核对诊断', design: '准备教学', implement: '课堂实施', review: '反馈复盘', completed: '已完成', archived: '已归档' };
  var kinds = { lesson_flow: '课堂流程', student_handout: '学生练习', teacher_key: '教师答案', followup_assessment: '后续测评' };
  var feedbackKinds = { implementation: '实施记录', observation: '课堂观察', correction: '教师修正', assessment: '复测记录' };
  function esc(value) { return String(value == null ? '' : value).replace(/[&<>"']/g, function (c) { return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]; }); }
  function term() { return typeof currentTermId !== 'undefined' ? Number(currentTermId) : null; }
  function snapshot() { return teachMateState.getSnapshot(); }
  function currentTaskId() { var snap = snapshot(); var s = snap.sessions.find(function (item) { return Number(item.id) === Number(snap.currentSessionId); }); return s && s.teaching_task_id || null; }
  function request(path, method, body) {
    var opts = { method: method || 'GET' };
    if (body !== undefined) { opts.headers = { 'Content-Type': 'application/json' }; opts.body = JSON.stringify(body); }
    return apiRequest('/api/v1/teaching' + path, opts);
  }
  function redraw() { if (typeof render === 'function') render(); }
  function notify(message) { if (typeof showToast === 'function') showToast(message); }
  function writable() { return active && !['completed', 'archived'].includes(active.phase); }
  function today() { return new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date()); }
  function button(act, label, attrs, disabled) { return '<button type="button" class="tm-task-btn" data-act="tm-task-' + act + '" ' + (attrs || '') + (disabled ? ' disabled' : '') + '>' + esc(label) + '</button>'; }
  function field(label, id, value, area) {
    return '<div class="tm-task-field"><label for="' + id + '">' + esc(label) + '</label>' + (area
      ? '<textarea id="' + id + '" rows="4">' + esc(value) + '</textarea>'
      : '<input id="' + id + '" value="' + esc(value) + '">') + '</div>';
  }
  function value(id) { var el = document.getElementById(id); return el ? el.value.trim() : ''; }
  function releaseResources() { if (window.teachMatePractice) window.teachMatePractice.releaseSource(); }
  function close() {
    onTaskCreated = null;
    var modal = document.getElementById('modal');
    if (modal && modal.classList.contains('show')) { closeModal(); return; }
    releaseResources(); canvas = null; canvasNode = null; canvasChat = false; panelOpen = true; redraw();
  }
  function present(title, body, footer) {
    closeModal(); releaseResources(); canvasNode = null;
    canvas = { taskId: currentTaskId(), key: ++canvasKey, title: title, body: body, footer: footer || '' };
    canvasChat = false; panelOpen = false; redraw();
  }
  function canvasForm(title, body, act, label) {
    present(title, '<div class="tm-task-form">' + body + '<p id="tm-task-form-error" role="alert"></p></div>',
      button('close', '取消') + button(act, label || '保存'));
  }
  function layoutClass() {
    return (panelOpen ? ' tm-task-panel-open' : '') + (canvas ? ' tm-task-canvas-open' : '') + (canvasChat ? ' tm-task-chat-open' : '');
  }
  function canvasHtml() {
    if (!canvas || Number(canvas.taskId) !== Number(currentTaskId())) return '';
    return '<section class="tm-task-canvas" id="tmTaskCanvas" data-canvas-key="' + canvas.key + '" aria-label="任务编辑区"><div class="tm-canvas-heading"><div><span class="tm-task-meta">当前任务内容</span><h2>' + esc(canvas.title) + '</h2></div><div class="tm-task-actions">' + button('canvas-back', '返回任务内容') + '</div></div><div class="tm-canvas-body">' + canvas.body + '</div>' + (canvas.footer ? '<div class="tm-canvas-footer">' + canvas.footer + '</div>' : '') + '</section>';
  }
  function captureCanvas() {
    var node = document.getElementById('tmTaskCanvas');
    if (canvas && node && Number(node.dataset.canvasKey) === canvas.key) canvasNode = node;
  }
  function preservedCanvas() {
    return canvas && Number(canvas.taskId) === Number(currentTaskId()) && canvasNode && Number(canvasNode.dataset.canvasKey) === canvas.key ? canvasNode : null;
  }
  function form(title, body, act, label) {
    openModal(title, '<div class="tm-task-form">' + body + '<p id="tm-task-form-error" role="alert"></p></div>',
      '<button type="button" class="btn" data-act="tm-task-close">取消</button><button type="button" class="btn btn-primary" data-act="tm-task-' + act + '">' + esc(label || '保存') + '</button>');
  }
  async function refresh(force) {
    var termId = term(), taskId = currentTaskId(), key = termId + ':' + taskId;
    if (!termId) { tasks = []; active = null; loadedTerm = null; resourceKey = ''; return; }
    if (!force && key === resourceKey) return;
    resourceKey = key; var ticket = ++generation; error = '';
    if (!active || Number(active.id) !== Number(taskId)) { releaseResources(); active = null; roster = []; canvas = null; canvasNode = null; canvasChat = false; panelOpen = false; panelTab = 'practice'; }
    try {
      var results = await Promise.all([request('/tasks?term_id=' + termId + '&include_archived=true'), taskId ? request('/tasks/' + taskId) : Promise.resolve(null)]);
      if (ticket !== generation || termId !== term()) return;
      tasks = results[0]; active = results[1]; loadedTerm = termId;
      if (active && active.student_ids.length) {
        var names = await apiRequest('/api/v1/students?term_id=' + termId + (active.class_id ? '&class_id=' + active.class_id : ''));
        if (ticket !== generation || termId !== term()) return;
        roster = names;
      }
    } catch (e) { if (ticket !== generation) return; error = e.message || '教学任务读取失败'; resourceKey = ''; }
    redraw();
  }
  function home() {
    var visible = loadedTerm === term() ? tasks.filter(function (t) { return showArchived ? t.phase === 'archived' : t.phase !== 'archived'; }) : [];
    var html = '<section class="tm-task-home" aria-label="教学任务"><div class="tm-task-heading"><div><h3>' + (showArchived ? '归档任务' : '教学任务') + '</h3><p>把目标、资料和课堂反馈放在一起。</p></div>' + '<div class="tm-task-actions">' + button('new', '新建教学任务', 'data-primary="true"') + '</div></div>';
    if (error) html += '<p role="alert">' + esc(error) + '</p>' + button('refresh', '重新加载');
    if (!visible.length && !error) html += '<div class="tm-task-empty"><span class="material-symbols-rounded" aria-hidden="true">' + (showArchived ? 'inventory_2' : 'assignment') + '</span><strong>' + (loadedTerm !== term() ? '正在读取教学任务…' : showArchived ? '还没有归档任务' : '从一个教学目标开始') + '</strong><p>' + (showArchived ? '完成并归档的任务会保留在这里。' : '创建讲评或辅导任务，随时继续准备、修改和复盘。') + '</p></div>';
    html += '<div class="tm-task-grid">' + visible.map(function (t) {
      return '<button class="tm-task-card" type="button" data-act="tm-task-open" data-task-id="' + t.id + '"><span>' + esc(phases[t.phase]) + '</span><strong>' + esc(t.title) + '</strong><small>' + esc(t.goal) + '</small></button>';
    }).join('') + '</div><div class="tm-task-home-footer">' + button('toggle-archived', showArchived ? '返回进行中的任务' : '查看归档') + (visible.length ? '<span>' + visible.length + ' 个任务</span>' : '') + '</div></section>';
    return html;
  }
  function intro() {
    return '<div class="tm-task-intro"><span class="tm-task-meta">一起推进这项教学任务</span><h3>' + esc(active ? active.goal : '从当前目标开始') + '</h3><p>说说学生遇到了什么问题，或直接提出你的想法。练习、资料和反馈会保存在这项任务中。</p><div class="tm-task-actions">' +
      button('prompt-diagnose', '聊聊学生的难点', '', !writable()) + button('panel-tab', '查看任务内容', 'data-panel-tab="practice"') + '</div></div>';
  }
  function header() {
    if (!active) return '<div class="tm-task-context"><strong>正在读取教学任务…</strong></div>';
    var t = active, snap = snapshot(), names = snap.contextNames || {}, c = t.constraints || {};
    var className = (names.class || {})[t.class_id] || snap.selectedClassName || (t.class_id ? '班级 #' + t.class_id : '全部班级');
    var examName = (names.exam || {})[t.exam_id] || (t.exam_id ? '考试 #' + t.exam_id : '未绑定考试');
    var target = t.target_type === 'class' ? '全班' : t.student_ids.map(function (id) { var s = roster.find(function (s) { return s.id === id; }); return s ? s.name + '（' + s.student_no + '）' : (names.student || {})[id] || '学生 #' + id; }).join('、');
    var focused = focus && Number(focus.sessionId) === Number(snap.currentSessionId) ? getArtifact(focus.id) : null;
    var focusHtml = focused ? '<div class="tm-context-focus">正在修改《' + esc(focused.title) + '》' + button('clear-focus', '结束本次修改') + '</div>' : '';
    return '<div class="tm-task-context"><div class="tm-context-copy"><h2>' + esc(t.title) + '</h2><div class="tm-context-scope"><span>' + esc(className) + '</span><button type="button" class="tm-context-target" data-act="tm-task-practice-targets" title="' + esc(target) + '"' + (!writable() ? ' disabled' : '') + '>' + esc(target) + '</button><span>' + esc(examName) + '</span><span>' + Number(c.lesson_minutes || 40) + ' 分钟</span></div>' + focusHtml + '</div><div class="tm-context-controls"><details class="tm-task-menu"><summary>' + esc(phases[t.phase]) + '</summary><div class="tm-task-menu-items">' + ['diagnose', 'design', 'implement', 'review'].map(function (p) { return button('phase', phases[p], 'data-phase="' + p + '" aria-pressed="' + (t.phase === p) + '"', !writable()); }).join('') + '</div></details>' + (canvas ? button('canvas-chat', canvasChat ? '返回编辑' : '打开对话', 'aria-expanded="' + canvasChat + '"') : '') + button('edit', '任务设置', '', !writable()) + button('panel-toggle', panelOpen && !canvas ? '收起内容' : '任务内容', 'aria-expanded="' + (panelOpen && !canvas) + '"') + '</div></div>';
  }
  function workspace() {
    if (!currentTaskId()) return '';
    var html = '<section role="complementary" class="tm-task-workspace" aria-label="教学工作区"' + (!panelOpen || canvas ? ' hidden' : '') + '><div class="tm-workspace-heading"><h3>任务内容</h3>' + button('panel-toggle', '收起内容', 'aria-label="收起任务内容"') + '</div><div class="tm-workspace-tabs" role="tablist" aria-label="任务内容分类">' + [['practice', '练习'], ['materials', '资料'], ['feedback', '反馈']].map(function (tab) { return '<button type="button" role="tab" id="tm-task-tab-' + tab[0] + '" data-act="tm-task-panel-tab" data-panel-tab="' + tab[0] + '" aria-selected="' + (panelTab === tab[0]) + '" aria-controls="tm-task-tab-content">' + tab[1] + '</button>'; }).join('') + '</div>';
    if (!active) return html + '<p>' + esc(error || '正在读取教学任务…') + '</p>' + button('refresh', '重新加载') + '</section>';
    var t = active, locked = !writable();
    html += '<div class="tm-workspace-content" id="tm-task-tab-content" role="tabpanel" aria-labelledby="tm-task-tab-' + panelTab + '"><p class="tm-task-goal">' + esc(t.goal) + '</p>';
    if (panelTab === 'materials') {
      html += '<section><div class="tm-task-heading"><h4>教学材料</h4>' + button('artifact-new', '添加材料', '', locked) + '</div>';
      var run = snapshot().currentRun;
      if (run && run.status === 'completed') html += button('adopt', '采用本轮报告', 'data-run-id="' + run.id + '"', locked);
      if (!t.artifacts.length) html += '<p class="tm-task-empty">采用已生成的教学包，或添加自己的材料。</p>';
      html += t.artifacts.map(function (a) { return '<article class="tm-task-artifact"><span class="tm-task-meta">' + esc(kinds[a.kind]) + ' · 第 ' + a.revision + ' 版</span><h5>' + esc(a.title) + '</h5><p>' + esc(a.body.slice(0, 180)) + '</p><div class="tm-task-actions">' + button('artifact-edit', '编辑', 'data-artifact-id="' + a.id + '"', locked) + '<details class="tm-task-menu"><summary>更多</summary><div class="tm-task-menu-items">' + button('artifact-ai', '请 AI 修改', 'data-artifact-id="' + a.id + '"', locked) + button('artifact-revisions', '比较版本', 'data-artifact-id="' + a.id + '"') + button('artifact-print', a.kind === 'teacher_key' ? '打印教师答案' : '预览与打印', 'data-artifact-id="' + a.id + '"') + '</div></details></div></article>'; }).join('') + '</section>';
    } else if (panelTab === 'practice') {
      if (window.teachMatePractice) html += window.teachMatePractice.workspace(t, 'practice');
    } else {
      if (window.teachMatePractice) html += window.teachMatePractice.workspace(t, 'feedback');
      html += '<section><h4>课堂观察与复测记录</h4><div class="tm-task-actions">' + button('feedback', '记录课堂反馈', '', locked) + button('assessment', '录入复测', '', locked) + '</div><p class="tm-task-meta">' + esc(t.review.note) + '</p>';
      html += t.review.objectives.map(function (r) { return '<p class="tm-task-review"><strong>' + esc(r.objective) + '</strong><br>当次答对 ' + r.correct + '/' + r.attempts + '；独立完成新题 ' + r.independent_new_correct + '/' + r.independent_new_attempts + '</p>'; }).join('');
      html += t.feedback.slice(-5).reverse().map(function (f) { return '<article class="tm-task-feedback"><strong>' + esc(feedbackKinds[f.kind]) + '</strong><p>' + esc(f.note) + '</p></article>'; }).join('') + '<div class="tm-task-actions">' + button('prompt-review', '根据反馈调整教学', '', locked) + '</div></section>';
    }
    html += '</div><div class="tm-workspace-footer"><details id="tmTaskOperations" data-menu-key="' + esc(t.id + ':' + t.phase) + '" class="tm-task-menu"><summary>任务操作</summary><div class="tm-task-menu-items">' + button('new-session', '新对话继续任务', '', locked) + button('complete', t.phase === 'completed' ? '重新打开任务' : '完成本任务') + button('archive', t.phase === 'archived' ? '恢复任务' : '归档') + '</div></details>' + button('home', '返回任务首页') + '</div></section>';
    return html;
  }
  async function openTask(id, fresh) {
    var task = await request('/tasks/' + Number(id));
    var sessionId = !fresh && task.session_ids[0];
    if (!sessionId) {
      var session = await request('/tasks/' + task.id + '/sessions', 'POST', {});
      sessionId = session.id;
    }
    teachMateState.setSessions(await teachMateApi.listSessions(term()));
    // Keep the existing session scope resolver in sync with the task.
    if (teachMateState.setSelectedExamId) teachMateState.setSelectedExamId(task.exam_id || '');
    teachMateState.setBindCurrentExam(!!task.exam_id);
    if (teachMateState.setSelectedPluginId) teachMateState.setSelectedPluginId('');
    teachMateState._pendingQuickTask = null;
    var classes = await teachMateApi.listClasses(task.term_id);
    var classroom = classes.find(function (c) { return c.id === task.class_id; });
    if (teachMateState.setSelectedClassName) teachMateState.setSelectedClassName(classroom ? classroom.name : '');
    if (typeof selectedStudentIds !== 'undefined') selectedStudentIds.clear();
    await tmSelectSession(sessionId);
    await refresh(true);
  }
  function constraintsForm(c) {
    c = c || {};
    return field('课堂时长（分钟）', 'tm-task-minutes', c.lesson_minutes || 40) + '<label class="tm-task-check"><input type="checkbox" id="tm-task-no-homework" ' + (c.no_homework !== false ? 'checked' : '') + '>不增加课后作业</label>' +
      field('年级', 'tm-task-grade', c.grade || '') + field('教材与当前进度', 'tm-task-curriculum', c.curriculum || '') + field('已经教过的内容', 'tm-task-taught', c.taught_content || '', true) + field('课堂活动偏好', 'tm-task-preference', c.activity_preference || '') + field('可用设备与材料', 'tm-task-equipment', c.equipment || '');
  }
  function constraintsValue() {
    return { lesson_minutes: Number(value('tm-task-minutes')), no_homework: document.getElementById('tm-task-no-homework').checked, grade: value('tm-task-grade'), curriculum: value('tm-task-curriculum'), taught_content: value('tm-task-taught'), activity_preference: value('tm-task-preference'), equipment: value('tm-task-equipment') };
  }
  function getArtifact(id) { return active && active.artifacts.find(function (a) { return Number(a.id) === Number(id); }); }
  function artifactForm(a) {
    selectedArtifact = a || null;
    panelTab = 'materials';
    canvasForm(a ? '编辑教学材料' : '添加教学材料', '<label class="tm-task-field">材料用途<select id="tm-task-kind">' + Object.keys(kinds).map(function (k) { return '<option value="' + k + '" ' + (a && a.kind === k ? 'selected' : '') + '>' + kinds[k] + '</option>'; }).join('') + '</select></label>' + field('标题', 'tm-task-artifact-title', a && a.title || '') + field('正文', 'tm-task-artifact-body', a && a.body || '', true) + field('步骤或题目（每行一项）', 'tm-task-artifact-items', a && a.items.join('\n') || '', true), 'artifact-save');
  }
  function prompt(mode, artifact) {
    var text, capability = 'general_chat';
    focus = artifact ? { id: artifact.id, sessionId: snapshot().currentSessionId } : null;
    if (artifact) text = '请只修改教学材料 #' + artifact.id + '《' + artifact.title + '》（第 ' + artifact.revision + ' 版）。保持教学目标，其他材料不变。请先让我补充具体修改要求。';
    else if (mode === 'diagnose') { text = '围绕当前教学目标核对考试问题。区分观察事实、可能错因和待核实证据；给出一项可以在课堂验证的活动。'; capability = active.exam_id ? 'exam_analysis' : 'general_chat'; }
    else if (mode === 'review') text = '根据当前教学任务的课堂反馈和复测汇总，说明已有改善证据、仍待核实的问题，并调整下一次教学。区分独立新题和提示后作答，不将当次答对视为稳定掌握。';
    else { text = '按当前教学目标、课时和已教内容准备讲评教学包，包含课堂流程、分层学生练习、独立教师答案和后续测评。核对答案与题目一致性，每个活动说明如何检查学习效果。'; capability = active.exam_id ? 'review_plan' : 'general_chat'; }
    teachMateState._pendingQuickTask = capability;
    var pluginId = capability === 'general_chat' ? '' : capability;
    if (teachMateState.setSelectedPluginId) teachMateState.setSelectedPluginId(pluginId);
    releaseResources(); canvas = null; panelOpen = false; canvasChat = false;
    teachMateState.setDraft(text); redraw();
    var input = document.querySelector('[data-act="tm-input"]'); if (input) input.focus();
    notify('要求已填入对话框，可修改后发送');
  }
  async function phase(p) { await request('/tasks/' + active.id, 'PATCH', { expected_revision: active.revision, phase: p }); await refresh(true); }
  async function assessmentForm() {
    var students = await apiRequest('/api/v1/students?term_id=' + active.term_id + (active.class_id ? '&class_id=' + active.class_id : ''));
    panelTab = 'feedback';
    canvasForm('录入复测表现', '<p>可记录一条作答，或导入 CSV / Excel。导入列：学号、目标、正确、独立完成、新题、日期；正确等列填写 1/0 或 是/否。</p><label class="tm-task-field">学生<select id="tm-task-student">' + students.map(function (s) { return '<option value="' + s.id + '">' + esc(s.student_no + ' ' + s.name) + '</option>'; }).join('') + '</select></label>' + field('检查的学习目标', 'tm-task-objective', active.goal) + '<label class="tm-task-field">作答日期<input type="date" id="tm-task-date" value="' + today() + '"></label>' + '<label class="tm-task-check"><input type="checkbox" id="tm-task-correct">答对</label><label class="tm-task-check"><input type="checkbox" id="tm-task-independent" checked>独立完成（未使用提示）</label><label class="tm-task-check"><input type="checkbox" id="tm-task-new-question" checked>使用新题</label>' + field('观察说明', 'tm-task-assessment-note', '', true) + '<label class="tm-task-field">批量导入（选择后代替单条录入）<input type="file" id="tm-task-assessment-file" accept=".csv,.xlsx,.tsv"></label>', 'assessment-save', '保存复测记录');
    window.teachMateTasks.assessmentStudents = students;
  }
  function parseBool(v) {
    if (['1', 'true', '是', '正确', '答对'].includes(String(v).trim().toLowerCase())) return true;
    if (['0', 'false', '否', '错误', '答错'].includes(String(v).trim().toLowerCase())) return false;
    throw new Error('正确、独立完成、新题列只能填 1/0 或 是/否');
  }
  async function assessmentRows() {
    var file = document.getElementById('tm-task-assessment-file').files[0];
    if (!file) return [{ student_id: Number(value('tm-task-student')), objective: value('tm-task-objective'), observed_on: value('tm-task-date'), correct: document.getElementById('tm-task-correct').checked, independent: document.getElementById('tm-task-independent').checked, new_question: document.getElementById('tm-task-new-question').checked, note: value('tm-task-assessment-note') }];
    if (file.size > 2 * 1024 * 1024) throw new Error('复测文件最多 2 MB');
    await ensureXlsx();
    var textFile = /\.(csv|tsv)$/i.test(file.name);
    var book = textFile ? XLSX.read(await file.text(), { type: 'string', raw: true })
      : XLSX.read(await file.arrayBuffer(), { type: 'array', cellDates: false });
    var rows = XLSX.utils.sheet_to_json(book.Sheets[book.SheetNames[0]], { raw: false });
    if (!rows.length || rows.length > 500) throw new Error('每批需包含 1 至 500 条作答');
    var students = window.teachMateTasks.assessmentStudents || [];
    return rows.map(function (r) {
      var no = String(r['学号'] || r.student_no || '').trim();
      var student = students.find(function (s) { return String(s.student_no) === no; });
      if (!student) throw new Error('无法匹配学号 ' + no + '，请检查当前班级');
      return { student_id: student.id, objective: String(r['目标'] || r.objective || ''), correct: parseBool(r['正确'] === undefined ? r.correct : r['正确']), independent: parseBool(r['独立完成'] === undefined ? r.independent : r['独立完成']), new_question: parseBool(r['新题'] === undefined ? r.new_question : r['新题']), observed_on: String(r['日期'] || r.observed_on || ''), note: String(r['备注'] || r.note || '') };
    });
  }
  function printArtifact(a) {
    var win = window.open('', '_blank'); if (!win) throw new Error('请允许打开材料预览窗口');
    win.document.write('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>' + esc(a.title) + '</title><style>body{max-width:800px;margin:40px auto;padding:20px;font:16px/1.8 sans-serif}p,li{white-space:pre-wrap}@media print{button{display:none}}</style></head><body><p>' + esc(kinds[a.kind]) + '</p><h1>' + esc(a.title) + '</h1><p>' + esc(a.body) + '</p><ol>' + a.items.map(function (i) { return '<li>' + esc(i) + '</li>'; }).join('') + '</ol><button onclick="window.print()">打印 / 保存 PDF</button></body></html>');
    win.document.close(); win.opener = null;
  }
  async function action(act, el, options) {
    var selectedMenu = el && typeof el.closest === 'function' && el.closest('details.tm-task-menu');
    if (selectedMenu) selectedMenu.open = false;
    if (!act.startsWith('tm-task-')) return false;
    var name = act.slice(8);
    // Reading a task's content must stay responsive while its chat is loading.
    if (name === 'panel-toggle' || name === 'panel-tab' || name === 'canvas-back') {
      releaseResources(); canvas = null; canvasNode = null; canvasChat = false;
      panelOpen = name === 'panel-toggle' ? !panelOpen : true;
      if (name === 'panel-tab') panelTab = ['practice', 'materials', 'feedback'].includes(el.dataset.panelTab) ? el.dataset.panelTab : 'practice';
      redraw(); return true;
    }
    if (name === 'canvas-chat') { canvasChat = !canvasChat; redraw(); return true; }
    if (name.startsWith('prompt-')) { prompt(name.slice(7)); return true; }
    if (busy) return true;
    try {
      busy = true;
      if (name.startsWith('practice-') && window.teachMatePractice) await window.teachMatePractice.action(name, el, active);
      else if (name === 'close') close();
      else if (name === 'refresh') await refresh(true);
      else if (name === 'clear-focus') { focus = null; redraw(); }
      else if (name === 'home') { canvas = null; panelOpen = false; await tmNewChat(); }
      else if (name === 'toggle-archived') { showArchived = !showArchived; redraw(); }
      else if (name === 'open') await openTask(el.dataset.taskId);
      else if (name === 'new-session') await openTask(active.id, true);
      else if (name === 'new' || name === 'edit' || name === 'from-report') {
        onTaskCreated = name === 'new' && options && typeof options.onCreated === 'function' ? options.onCreated : null;
        sourceRun = name === 'from-report' ? Number(el.dataset.runId) : null;
        var editing = name === 'edit', t = editing ? active : {};
        form(editing ? '教学目标与条件' : '新建教学任务', field('任务名称', 'tm-task-title', t.title || '') + field('学生应学会什么', 'tm-task-goal', t.goal || '', true) + (editing ? '' : '<p class="tm-task-meta">' + (sourceRun ? '范围沿用这份报告；创建后将报告材料加入任务。' : '范围采用当前学期、班级及所选考试；创建后固定保留。') + '</p>') + constraintsForm(t.constraints), editing ? 'save' : 'create');
      } else if (name === 'create' || name === 'save') {
        var payload = { title: value('tm-task-title'), goal: value('tm-task-goal'), constraints: constraintsValue() };
        var result;
        if (name === 'create' && sourceRun) {
          result = await request('/tasks/from-report/' + sourceRun, 'POST', payload);
        } else if (name === 'create') {
          var snap = snapshot(); var examId = snap.bindCurrentExam && snap.selectedExamId ? Number(snap.selectedExamId) : null;
          payload.term_id = term();
          var className = snap.selectedClassName != null ? snap.selectedClassName : normalizeClassFilter(dashboardClass);
          payload.class_id = className ? await teachMateApi.findClassIdByName(className, term()) : null;
          if (className && !payload.class_id) throw new Error('所选班级不存在，请刷新后重试');
          payload.exam_id = examId;
          result = await request('/tasks', 'POST', payload);
        } else { payload.expected_revision = active.revision; result = await request('/tasks/' + active.id, 'PATCH', payload); }
        var continueCreation = name === 'create' ? onTaskCreated : null;
        close(); await openTask(result.id);
        if (continueCreation) await continueCreation(active);
      } else if (name === 'phase') await phase(el.dataset.phase);
      else if (name === 'complete') await phase(active.phase === 'completed' ? 'review' : 'completed');
      else if (name === 'archive') { await phase(active.phase === 'archived' ? 'design' : 'archived'); if (active.phase === 'archived') await tmNewChat(); }
      else if (name === 'artifact-new') artifactForm(null);
      else if (name === 'artifact-edit') artifactForm(getArtifact(el.dataset.artifactId));
      else if (name === 'artifact-ai') prompt('modify', getArtifact(el.dataset.artifactId));
      else if (name === 'artifact-print') printArtifact(getArtifact(el.dataset.artifactId));
      else if (name === 'artifact-save') {
        var content = { kind: value('tm-task-kind'), title: value('tm-task-artifact-title'), body: value('tm-task-artifact-body'), items: value('tm-task-artifact-items').split('\n').map(function (s) { return s.trim(); }).filter(Boolean) };
        if (selectedArtifact) { content.expected_revision = selectedArtifact.revision; await request('/tasks/' + active.id + '/artifacts/' + selectedArtifact.id, 'PUT', content); }
        else await request('/tasks/' + active.id + '/artifacts', 'POST', content);
        close(); await refresh(true);
      } else if (name === 'artifact-revisions') {
        selectedArtifact = getArtifact(el.dataset.artifactId);
        var revisions = await request('/tasks/' + active.id + '/artifacts/' + selectedArtifact.id + '/revisions');
        openModal('材料版本 · ' + selectedArtifact.title, '<div class="tm-task-form"><p>当前为第 ' + selectedArtifact.revision + ' 版。恢复会创建新版本，原始内容仍保留。</p>' + revisions.map(function (r) { return '<details class="tm-task-revision"><summary>第 ' + r.revision + ' 版' + (r.revision === selectedArtifact.revision ? '（当前）' : '') + '</summary><pre>' + esc(r.content.body + '\n' + r.content.items.join('\n')) + '</pre>' + button('artifact-restore', '恢复这一版', 'data-revision="' + r.revision + '"', !writable() || r.revision === selectedArtifact.revision) + '</details>'; }).join('') + '</div>');
      } else if (name === 'artifact-restore') {
        await request('/tasks/' + active.id + '/artifacts/' + selectedArtifact.id + '/restore', 'POST', { expected_revision: selectedArtifact.revision, revision: Number(el.dataset.revision) }); close(); await refresh(true);
      } else if (name === 'adopt') { panelTab = 'materials'; panelOpen = true; await request('/tasks/' + active.id + '/adopt/' + Number(el.dataset.runId), 'POST', {}); await refresh(true); notify('报告材料已加入教学工作区'); }
      else if (name === 'feedback') {
        feedbackRunId = el.dataset.runId ? Number(el.dataset.runId) : null;
        form('记录课堂反馈', '<label class="tm-task-field">记录类型<select id="tm-task-feedback-kind"><option value="implementation">已经实施的活动</option><option value="observation">课堂观察</option><option value="correction">修正 AI 判断</option></select></label>' + field('对应的结论或活动', 'tm-task-finding', el.dataset.finding || '') + '<label class="tm-task-field">修正原因（可选）<select id="tm-task-reason"><option value="">不指定</option><option value="not_taught">这部分还没教</option><option value="ambiguous_question">题目存在歧义</option><option value="time_limit">时间不足</option><option value="other">其他原因</option></select></label>' + field('你的观察与修正', 'tm-task-feedback-note', '', true), 'feedback-save');
        if (feedbackRunId) document.getElementById('tm-task-feedback-kind').value = 'correction';
      } else if (name === 'feedback-save') {
        await request('/tasks/' + active.id + '/feedback', 'POST', { kind: value('tm-task-feedback-kind'), note: value('tm-task-feedback-note'), finding_title: value('tm-task-finding'), correction_reason: value('tm-task-reason'), source_run_id: feedbackRunId }); close(); await refresh(true);
      } else if (name === 'assessment') await assessmentForm();
      else if (name === 'assessment-save') {
        var rows = await assessmentRows();
        await request('/tasks/' + active.id + '/feedback', 'POST', { kind: 'assessment', note: value('tm-task-assessment-note') || '教师录入复测作答表现', observations: rows }); close(); await refresh(true);
      }
    } catch (e) {
      var msg = e.message || '操作失败'; var alert = document.getElementById('tm-task-form-error');
      if (alert) alert.textContent = msg; else notify(msg);
    } finally { busy = false; }
    return true;
  }
  window.teachMateTasks = { home: home, workspace: workspace, intro: intro, header: header, canvasHtml: canvasHtml, captureCanvas: captureCanvas, preservedCanvas: preservedCanvas, layoutClass: layoutClass, present: present, canvasForm: canvasForm, close: close, refresh: refresh, action: action, currentTaskId: currentTaskId, escape: esc, parseBool: parseBool, focusArtifactId: function (sessionId) { return focus && Number(focus.sessionId) === Number(sessionId) ? focus.id : null; } };
  window.teachMateTasks.startTaskCreation = function (onCreated) { return action('tm-task-new', { dataset: {} }, { onCreated: onCreated }); };
  teachMateState.subscribe(function () { refresh(false); });
})();
