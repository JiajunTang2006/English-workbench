/* Teacher-only targeted practice and assessment workspace. */
(function () {
  'use strict';
  var current = null, students = [], parent = null, budget = null, sourcePool = [], selectedQuestion = null;
  var questionId = null, detailTab = 'questions', sourceUrl = null, sourceTicket = 0, sourceAbort = null;
  var levels = { basic: '基础', consolidation: '巩固', transfer: '迁移', progressive: '递进' };
  function esc(v) { return window.teachMateTasks.escape(v); }
  function btn(name, label, attrs, disabled) { return '<button type="button" class="tm-task-btn" data-act="tm-task-practice-' + name + '" ' + (attrs || '') + (disabled ? ' disabled' : '') + '>' + esc(label) + '</button>'; }
  function path(task, id) { return '/api/v1/teaching/tasks/' + task.id + (id ? '/practices/' + id : ''); }
  function req(url, method, data) { return apiRequest(url, { method: method || 'GET', headers: { 'Content-Type': 'application/json' }, body: data === undefined ? undefined : JSON.stringify(data) }); }
  function value(id) { var e = document.getElementById(id); return e ? e.value.trim() : ''; }
  function field(label, id, val, multiline) { return '<label class="tm-task-field">' + esc(label) + (multiline ? '<textarea id="' + id + '" rows="4">' + esc(val || '') + '</textarea>' : '<input id="' + id + '" value="' + esc(val || '') + '">') + '</label>'; }
  function modalForm(title, html, action, label) { openModal(title, '<div class="tm-task-form">' + html + '<p id="tm-task-form-error" role="alert"></p></div>', '<button class="btn" data-act="tm-task-close">取消</button>' + btn(action, label || '保存')); }
  function form(title, html, action, label) { teachMateTasks.canvasForm(title, html, 'practice-' + action, label); }
  function releaseSource() { sourceTicket += 1; if (sourceAbort) sourceAbort.abort(); sourceAbort = null; if (sourceUrl) URL.revokeObjectURL(sourceUrl); sourceUrl = null; }
  function selected() { return Array.from(document.querySelectorAll('[name="tm-practice-student"]:checked')).map(function (e) { return Number(e.value); }); }
  function choices(ids) { return '<fieldset class="tm-practice-students"><legend>练习对象（可多选）</legend>' + students.map(function (s) { return '<label class="tm-task-check"><input type="checkbox" name="tm-practice-student" value="' + s.id + '" ' + (ids.includes(s.id) ? 'checked' : '') + '>' + esc(s.name + ' · ' + s.student_no) + '</label>'; }).join('') + '</fieldset>'; }
  function modelUnavailableReason() {
    var provider = teachMateState.getSnapshot().providerInfo;
    return typeof _teachMateUnavailableReason === 'function' ? _teachMateUnavailableReason(provider) : '';
  }
  async function loadStudents(task) {
    students = await req('/api/v1/students?term_id=' + task.term_id + (task.class_id ? '&class_id=' + task.class_id : ''));
    if (task.target_type !== 'class') students = students.filter(function (s) { return (task.student_ids || []).includes(s.id); });
  }
  function workspace(task, mode) {
    var locked = ['completed', 'archived'].includes(task.phase) || (typeof tmIsPluginEnabled === 'function' && !tmIsPluginEnabled('targeted_practice'));
    var feedback = mode === 'feedback', html = '<section aria-label="专项练习与作答"><h4>' + (feedback ? '逐题作答与反馈' : '专项练习与作答') + '</h4>';
    if (!feedback) html += '<div class="tm-task-actions">' + btn('new', '生成专项练习', 'data-primary="true"', locked) + btn('sources', '从原错题选题', '', locked) + '<details class="tm-task-menu"><summary>更多</summary><div class="tm-task-menu-items">' + btn('weakness', '查看弱项与建议分组') + btn('manual', '手工添加练习', '', locked) + '</div></details></div>';
    if (!(task.practices || []).length) html += '<p class="tm-task-empty">' + (feedback ? '采用练习并录入学生作答后，可以在这里查看反馈与复测表现。' : '围绕学生的薄弱项生成练习，或从已确认的原错题开始。') + '</p>';
    html += (task.practices || []).map(function (p) {
      var summary = p.attempt_summary || {}, feedbackAct = summary.pending > 0 ? 'view-feedback' : summary.total > 0 ? 'retest' : 'attempt', feedbackLabel = summary.pending > 0 ? '核对作答' : summary.total > 0 ? '安排复测' : '录入作答';
      var attrs = 'data-practice-id="' + p.id + '"', primary = p.status === 'confirmed' ? btn(feedback ? feedbackAct : 'print', feedback ? feedbackLabel : '打印学生练习', attrs + ' data-primary="true"', feedback && locked) : btn('view', '核对并采用', attrs + ' data-primary="true"');
      return '<article class="tm-task-artifact"><span class="tm-task-meta">' + esc(levels[p.level]) + ' · ' + (p.status === 'confirmed' ? '已采用' : '待核对草稿') + ' · ' + p.question_count + ' 题' + (p.parent_id ? ' · 新题复测' : '') + '</span><h5><button class="tm-practice-title" data-act="tm-task-practice-' + (feedback ? 'view-feedback' : 'view') + '" ' + attrs + '>' + esc(p.title) + '</button></h5><p>' + esc(p.objective) + '</p><small>' + esc((p.student_names || []).join('、')) + '</small><div class="tm-task-actions">' + primary + '<details class="tm-task-menu"><summary>更多</summary><div class="tm-task-menu-items">' + btn('view', '查看练习与反馈', attrs) + btn('attempt', '录入作答', attrs, locked || p.status !== 'confirmed') + btn('retest', '生成新题复测', attrs, locked) + btn('print', '打印学生练习', attrs) + btn('key', '打印教师答案', attrs) + '</div></details></div></article>';
    }).join('');
    var rows = (task.practice_progress || {}).objectives || [];
    if (feedback && rows.length) html += '<h5>练习与复测表现</h5><p class="tm-task-meta">独立新题、提示后完成分别记录；一次答对不代表稳定掌握。</p><div class="tm-practice-table"><table><thead><tr><th>学生 / 目标</th><th>答对 / 作答</th><th>提示后答对</th><th>独立新题</th><th>复测</th><th>待核对</th></tr></thead><tbody>' + rows.map(function (r) { return '<tr><td>' + esc(r.student_name + ' / ' + r.objective) + '</td><td>' + r.correct + '/' + r.attempts + '</td><td>' + r.hinted_correct + '</td><td>' + r.independent_new_correct + '/' + r.independent_new_attempts + '</td><td>' + r.retest_correct + '/' + r.retest_attempts + '</td><td>' + r.pending + '</td></tr>'; }).join('') + '</tbody></table></div>';
    return html + '</section>';
  }
  async function generationForm(task, source, groupIds, groupLevel) {
    detailTab = 'questions'; questionId = null; parent = source || null; budget = null;
    await loadStudents(task);
    var ids = parent ? parent.student_ids : groupIds || task.student_ids || [];
    var unavailable = modelUnavailableReason();
    var modelNotice = unavailable ? '<p role="status">' + esc(unavailable) + '</p><button type="button" class="tm-task-btn" data-act="tm-model-custom">配置生成模型</button>' : '';
    form(parent ? '生成同目标的新题复测' : '生成专项练习', modelNotice + choices(ids) + field('练习目标', 'tm-practice-goal', parent ? parent.objective : task.goal.slice(0, 300)) + '<label class="tm-task-field">难度<select id="tm-practice-level">' + Object.keys(levels).map(function (k) { return '<option value="' + k + '" ' + (k === (groupLevel || (parent && parent.level) || 'progressive') ? 'selected' : '') + '>' + levels[k] + '</option>'; }).join('') + '</select></label><label class="tm-task-field">题量<input type="number" id="tm-practice-count" min="1" max="20" value="5"></label>' + field('补充要求（教材、未教内容、题型等）', 'tm-practice-note', '', true) + (parent ? '<label class="tm-task-field">计划复测日期（可选）<input type="date" id="tm-practice-scheduled"></label>' : '') + '<p>沿用任务中的年级、教材及已教内容。生成后先核对题目与答案，再采用。' + (parent ? '复测沿用原对象与目标，使用不同题目。' : '') + '</p>', 'generate', '生成练习');
  }
  function generationPayload() { return { objective: value('tm-practice-goal'), student_ids: selected(), level: value('tm-practice-level'), question_count: Number(value('tm-practice-count')), teacher_note: value('tm-practice-note'), parent_id: parent ? parent.id : null, scheduled_for: value('tm-practice-scheduled') || null, session_id: Number(teachMateState.getSnapshot().currentSessionId), confirmed_budget_yuan: budget }; }
  function questionHtml(q, key, locked) {
    var source = q.source || {}, pages = source.pages || [], modes = { full: '采用整篇原文', excerpt: '采用原文节选', omit: '无需阅读原文', question_only: '复用独立原题' };
    return '<article class="tm-practice-question"><div class="tm-task-heading"><h3>第 ' + q.position + ' 题 · ' + esc(levels[q.difficulty]) + '</h3>' + btn('edit-question', '修改这一题', 'data-question-id="' + q.id + '"', locked) + '</div>' + (source.original_reuse ? '<div class="tm-practice-provenance"><strong>' + esc(modes[source.context_mode] || '原题复用') + '</strong><p>' + esc(source.reason) + '</p><span>原卷第 ' + esc(source.question_no) + ' 题 · 第 ' + source.paper_version + ' 版 · 页 ' + esc(source.source_page || '待补充') + '</span>' + (pages.length ? '<div class="tm-task-actions">' + pages.filter(function (p, i) { return pages.findIndex(function (other) { return other.attachment_id === p.attachment_id; }) === i; }).map(function (p) { return btn('source-preview', '对照原始 PDF', 'data-attachment-id="' + Number(p.attachment_id) + '" data-source-page="' + Number(source.source_page || p.page || 1) + '"'); }).join('') + '</div>' : '<p>当前题目未保留可定位的 PDF 来源页。</p>') + '</div>' : '') + (q.passage ? '<section class="tm-practice-passage"><h4>阅读材料</h4><p class="tm-practice-text">' + esc(q.passage) + '</p></section>' : '') + '<p class="tm-practice-text">' + esc(q.prompt) + '</p>' + Object.keys(q.options || {}).map(function (k) { return '<p>' + esc(k + '. ' + q.options[k]) + '</p>'; }).join('') + (key ? '<details class="tm-practice-answer"><summary>答案、解析与提示</summary><p>' + esc(q.answers.join(' / ')) + '</p><p class="tm-practice-text">' + esc(q.explanation) + '</p>' + q.hints.map(function (h, i) { return '<p>' + (i + 1) + '. ' + esc(h) + '</p>'; }).join('') + '</details>' : '') + '</article>';
  }
  function view(task) {
    releaseSource();
    var p = current, writable = !['completed', 'archived'].includes(task.phase) && (typeof tmIsPluginEnabled !== 'function' || tmIsPluginEnabled('targeted_practice')), attrs = 'data-practice-id="' + p.id + '"';
    var q = p.questions.find(function (q) { return q.id === questionId; }) || p.questions[0]; questionId = q && q.id;
    var html = '<div class="tm-practice-detail-meta"><p>' + esc(p.objective) + ' · ' + esc((p.student_names || []).join('、')) + '</p><p class="tm-task-meta">' + esc(p.checks.note || '') + '</p></div><div class="tm-practice-detail-tabs">' + btn('detail-questions', '题目与答案', 'aria-pressed="' + (detailTab === 'questions') + '"') + btn('detail-attempts', '作答与反馈', 'aria-pressed="' + (detailTab === 'attempts') + '"') + '</div>';
    if (detailTab === 'questions') {
      html += '<div class="tm-practice-editor"><nav class="tm-practice-question-list" aria-label="练习题目"><strong>' + p.question_count + ' 道题</strong>' + p.questions.map(function (item) { return btn('select-question', '第 ' + item.position + ' 题', 'data-question-id="' + item.id + '" aria-pressed="' + (item.id === questionId) + '"'); }).join('') + '</nav><div class="tm-practice-preview">' + (q ? questionHtml(q, true, !writable) : '') + '<div id="tmPracticeSource" class="tm-practice-source" hidden></div></div></div>';
      if (p.status !== 'confirmed') html += '<div class="tm-practice-adoption"><label class="tm-task-check"><input id="tm-practice-checked-key" type="checkbox">已核对答案与解法</label><label class="tm-task-check"><input id="tm-practice-checked-scope" type="checkbox">已核对实际难度及已教范围</label>' + btn('confirm', '采用这份练习', attrs + ' data-primary="true"', !writable) + '</div>';
    } else {
      html += '<div class="tm-task-actions">' + btn('attempt', '录入作答', attrs + ' data-primary="true"', !writable || p.status !== 'confirmed') + btn('retest', '生成新题复测', attrs, !writable) + '</div>';
      if (!(p.attempts || []).length) html += '<p class="tm-task-empty">还没有学生作答。录入后可核对判断、记录反馈并安排复测。</p>';
      html += (p.attempts || []).map(function (a) { var s = students.find(function (s) { return s.id === a.student_id; }), q = p.questions.find(function (q) { return q.id === a.question_id; }); return '<article class="tm-task-artifact"><h4>' + esc(s ? s.name : '学生') + ' · 第 ' + (q ? q.position : '?') + ' 题</h4><p>' + esc(a.answer) + '</p><p>' + (a.correct === null ? '待教师核对' : a.correct ? '答对' : '未答对') + ' · ' + (a.independent ? '独立完成' : '使用帮助') + ' · ' + (a.new_question ? '首次作答新题' : '重复作答') + '</p><p>' + esc(a.feedback.feedback || a.feedback.note || '') + '</p><p>' + esc(a.feedback.next_step || '') + '</p><small>修正记录：' + a.corrections.length + '</small><div class="tm-task-actions">' + btn('correct', '教师修正', 'data-attempt-id="' + a.id + '"', !writable) + btn('feedback', '请 AI 给反馈建议', 'data-attempt-id="' + a.id + '"', !writable) + '</div></article>'; }).join('');
    }
    if (p.generation_usage) { var u = p.generation_usage; html += '<details class="tm-practice-usage"><summary>查看本次生成用量</summary><p>' + (u.usage_known ? '实际 Token ' + u.actual_tokens + ' · ' + (u.actual_cost_yuan != null ? '费用 ¥' + Number(u.actual_cost_yuan).toFixed(4) : '费用未知（未配置价格）') : '供应商未返回用量，实际费用未知') + ' · ' + (u.estimated_cost_yuan != null ? '预估 ¥' + Number(u.estimated_cost_yuan).toFixed(4) : '预估费用未知') + '</p></details>'; }
    teachMateTasks.present(p.title, html + '<p id="tm-task-form-error" role="alert"></p>');
  }
  async function show(task, id) { closeModal(); await loadStudents(task); current = await req(path(task, id)); view(task); }
  async function previewSource(el) {
    releaseSource(); var ticket = sourceTicket, canvas = document.getElementById('tmTaskCanvas');
    var pane = document.getElementById('tmPracticeSource'); if (!pane) return;
    pane.hidden = false; pane.parentElement.classList.add('has-source'); pane.textContent = '正在读取原始 PDF…';
    sourceAbort = new AbortController();
    var response;
    try { response = await fetch('/api/v1/attachments/' + Number(el.dataset.attachmentId) + '/download', { headers: { Authorization: 'Bearer ' + (workbenchToken || '') }, signal: sourceAbort.signal }); }
    catch (e) { if (e.name === 'AbortError') return; pane.textContent = '原始 PDF 读取失败，请重试。'; throw e; }
    if (!response.ok) { pane.textContent = '原始 PDF 读取失败，请检查资料是否仍存在。'; throw new Error(pane.textContent); }
    if (!(response.headers.get('content-type') || '').toLowerCase().includes('application/pdf')) { pane.textContent = '该来源不是 PDF，请在资料库中查看原文件。'; throw new Error(pane.textContent); }
    var blob = await response.blob();
    if (ticket !== sourceTicket || canvas !== document.getElementById('tmTaskCanvas')) return;
    sourceUrl = URL.createObjectURL(blob);
    var page = Math.max(1, Number(el.dataset.sourcePage) || 1), url = sourceUrl + '#page=' + page;
    pane.innerHTML = '<div class="tm-task-heading"><h4>原始 PDF · 第 ' + page + ' 页</h4>' + btn('source-close', '收起原文') + '<a href="' + esc(url) + '" target="_blank" rel="noopener">新窗口查看</a></div><iframe title="原始试卷 PDF" src="' + esc(url) + '"></iframe><a href="' + esc(url) + '" target="_blank" rel="noopener">在新窗口查看原卷</a>';
  }
  function printCopy(data, key) {
    var win = window.open('', '_blank'); if (!win) throw new Error('请允许打开练习预览窗口');
    var body = data.questions.map(function (q) { return '<section><h3>第 ' + q.position + ' 题</h3>' + (q.passage ? '<p>' + esc(q.passage) + '</p>' : '') + '<p>' + esc(q.prompt) + '</p>' + Object.keys(q.options || {}).map(function (k) { return '<p>' + esc(k + '. ' + q.options[k]) + '</p>'; }).join('') + (key ? '<p>答案：' + esc(q.answers.join(' / ')) + '</p><p>' + esc(q.explanation) + '</p>' : '<p class="answer-space">作答：</p>') + '</section>'; }).join('');
    win.document.write('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>' + esc(data.title) + '</title><style>body{max-width:800px;margin:32px auto;padding:20px;font:16px/1.8 sans-serif}p{white-space:pre-wrap}section{break-inside:avoid}.answer-space{min-height:90px}@media print{button{display:none}}</style></head><body><p>' + (key ? '教师答案' : '学生练习') + (data.status === 'confirmed' ? '' : ' · 待核对草稿') + '</p><h1>' + esc(data.title) + '</h1><p>' + esc(data.objective) + '</p>' + body + '<button onclick="window.print()">打印 / 保存 PDF</button></body></html>');
    win.document.close(); win.opener = null;
  }
  async function action(name, el, task) {
    if (!task) throw new Error('请先打开教学任务');
    var id = Number(el.dataset.practiceId || (current && current.id)), writable = !['completed', 'archived'].includes(task.phase);
    if (name === 'practice-source-preview') await previewSource(el);
    else if (name === 'practice-source-close') { releaseSource(); var pane = document.getElementById('tmPracticeSource'); if (pane) { pane.hidden = true; pane.innerHTML = ''; pane.parentElement.classList.remove('has-source'); } }
    else if (name === 'practice-select-question') { questionId = Number(el.dataset.questionId); view(task); }
    else if (name === 'practice-detail-questions' || name === 'practice-detail-attempts') { detailTab = name === 'practice-detail-questions' ? 'questions' : 'attempts'; view(task); }
    else if (name === 'practice-view-feedback') { detailTab = 'attempts'; questionId = null; await show(task, id); }
    else if (name === 'practice-new' || name === 'practice-group') {
      var groupIds = el.dataset.studentIds ? el.dataset.studentIds.split(',').map(Number) : null;
      await generationForm(task, null, groupIds, el.dataset.level);
    } else if (name === 'practice-retest') await generationForm(task, await req(path(task, id)));
    else if (name === 'practice-generate' || name === 'practice-generate-confirmed') {
      var unavailable = modelUnavailableReason(); if (unavailable) throw new Error(unavailable);
      var payload = generationPayload(); if (!payload.student_ids.length) throw new Error('请选择练习对象');
      var control = el; control.disabled = true; control.textContent = '正在生成…';
      try {
        current = await req(path(task) + '/practice/generate', 'POST', payload);
        await teachMateTasks.refresh(true); await show(task, current.id);
      } catch (e) {
        if (e.detail && e.detail.code === 'PRACTICE_BUDGET_CONFIRMATION') {
          budget = Number(e.detail.estimated_cost_yuan);
          document.getElementById('tm-task-form-error').textContent = '预计费用 ¥' + budget.toFixed(4) + '，确认后生成。';
          control.dataset.act = 'tm-task-practice-generate-confirmed'; control.textContent = '确认费用并生成';
        } else { control.textContent = '重新生成'; throw e; }
      } finally { control.disabled = false; }
    } else if (name === 'practice-view') { detailTab = 'questions'; questionId = null; await show(task, id); }
    else if (name === 'practice-confirm') {
      await req(path(task, id) + '/confirm', 'POST', { checked_answers: document.getElementById('tm-practice-checked-key').checked, checked_scope: document.getElementById('tm-practice-checked-scope').checked });
      closeModal(); teachMateTasks.close(); await teachMateTasks.refresh(true);
    } else if (name === 'practice-print' || name === 'practice-key') {
      var key = name === 'practice-key'; printCopy(await req(path(task, id) + (key ? '' : '?student_copy=true')), key);
    } else if (name === 'practice-weakness') {
      var data = await req(path(task) + '/practice/weaknesses');
      var groups = {};
      var html = '<div class="tm-task-form"><p>' + esc(data.note) + '</p>' + data.students.map(function (s) { (groups[s.suggested_level] || (groups[s.suggested_level] = [])).push(s.student_id); return '<article class="tm-task-artifact"><h4>' + esc(s.student_name) + '</h4><p>数据状态：' + esc(s.status) + ' · 缺少 ' + s.missing_items + ' 项逐题成绩</p><p>' + s.weak_sections.map(function (w) { return esc(w.section_name) + '：' + Math.round(w.score_rate * 100) + '%'; }).join('；') + '</p><p>建议起点：' + esc(levels[s.suggested_level]) + '（可由教师调整）</p></article>'; }).join('');
      html += Object.keys(groups).map(function (level) { return btn('group', '为' + levels[level] + '组准备练习', 'data-level="' + level + '" data-student-ids="' + groups[level].join(',') + '"', !writable); }).join('');
      teachMateTasks.present('弱项证据与建议分组', html + '<p id="tm-task-form-error" role="alert"></p></div>');
    } else if (name === 'practice-attempt') {
      await loadStudents(task); current = await req(path(task, id));
      form('录入逐题作答', '<label class="tm-task-field">学生<select id="tm-practice-student">' + students.filter(function (s) { return current.student_ids.includes(s.id); }).map(function (s) { return '<option value="' + s.id + '">' + esc(s.name + ' ' + s.student_no) + '</option>'; }).join('') + '</select></label><label class="tm-task-field">题目<select id="tm-practice-question">' + current.questions.map(function (q) { return '<option value="' + q.id + '">第 ' + q.position + ' 题：' + esc(q.prompt.slice(0, 45)) + '</option>'; }).join('') + '</select></label>' + field('学生答案', 'tm-practice-answer', '', true) + '<label class="tm-task-field">作答日期<input type="date" id="tm-practice-date" value="' + new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai' }).format(new Date()) + '"></label><label class="tm-task-field">使用提示级别<select id="tm-practice-hint"><option value="0">未使用提示</option><option value="1">启发线索</option><option value="2">关键步骤</option><option value="3">完整讲解</option></select></label><label class="tm-task-check"><input type="checkbox" id="tm-practice-viewed">作答前看过答案</label><label class="tm-task-field">批量导入 CSV / Excel（代替单条录入）<input type="file" id="tm-practice-file" accept=".csv,.xlsx,.tsv"></label><p>导入列：学号、题号、答案、日期、提示级别、查看答案、提交标识。提交标识用于避免重复录入。</p>', 'attempt-save');
    } else if (name === 'practice-attempt-save') {
      var file = document.getElementById('tm-practice-file').files[0];
      if (file) {
        if (file.size > 2 * 1024 * 1024) throw new Error('文件最多 2 MB');
        await ensureXlsx();
        var book = /\.(csv|tsv)$/i.test(file.name) ? XLSX.read(await file.text(), { type: 'string', raw: true }) : XLSX.read(await file.arrayBuffer(), { type: 'array' });
        var inputRows = XLSX.utils.sheet_to_json(book.Sheets[book.SheetNames[0]], { raw: false });
        if (!inputRows.length || inputRows.length > 500) throw new Error('每批需包含 1 至 500 条作答');
        var rows = inputRows.map(function (r) { var s = students.find(function (s) { return String(s.student_no) === String(r['学号'] || '').trim(); }), q = current.questions.find(function (q) { return String(q.position) === String(r['题号'] || '').trim(); });
          if (!s || !q) throw new Error('学号或题号不属于当前范围');
          return { student_id: s.id, question_id: q.id, answer: String(r['答案'] || ''), observed_on: String(r['日期'] || ''), hint_level: Number(r['提示级别'] || 0), answer_viewed: teachMateTasks.parseBool(r['查看答案'] === undefined ? '0' : r['查看答案']), submission_key: String(r['提交标识'] || '') }; });
        await req(path(task, current.id) + '/attempts/batch', 'POST', { rows: rows });
      } else await req(path(task, current.id) + '/attempts', 'POST', { student_id: Number(value('tm-practice-student')), question_id: Number(value('tm-practice-question')), answer: value('tm-practice-answer'), observed_on: value('tm-practice-date'), hint_level: Number(value('tm-practice-hint')), answer_viewed: document.getElementById('tm-practice-viewed').checked, submission_key: window.crypto.randomUUID() });
      closeModal(); teachMateTasks.close(); await teachMateTasks.refresh(true);
    } else if (name === 'practice-correct') {
      var attempt = current.attempts.find(function (a) { return a.id === Number(el.dataset.attemptId); });
      modalForm('修正作答判断', '<input type="hidden" id="tm-practice-attempt-id" value="' + attempt.id + '"><input type="hidden" id="tm-practice-revision" value="' + attempt.revision + '"><label class="tm-task-field">判断<select id="tm-practice-correct"><option value="true">答对</option><option value="false">未答对</option></select></label>' + field('修正理由', 'tm-practice-reason', '', true), 'correction-save');
    } else if (name === 'practice-correction-save') {
      await req(path(task, current.id) + '/attempts/' + value('tm-practice-attempt-id'), 'PATCH', { correct: value('tm-practice-correct') === 'true', reason: value('tm-practice-reason'), expected_revision: Number(value('tm-practice-revision')) });
      await teachMateTasks.refresh(true); await show(task, current.id);
    } else if (name === 'practice-feedback') {
      try {
        await req(path(task, current.id) + '/attempts/' + el.dataset.attemptId + '/feedback', 'POST', { session_id: Number(teachMateState.getSnapshot().currentSessionId), confirmed_budget_yuan: el.dataset.budget ? Number(el.dataset.budget) : null });
        await teachMateTasks.refresh(true); await show(task, current.id);
      } catch (e) {
        if (e.detail && e.detail.code === 'PRACTICE_BUDGET_CONFIRMATION') { el.dataset.budget = e.detail.estimated_cost_yuan; el.textContent = '确认费用并获取反馈'; throw new Error('预计费用 ¥' + Number(el.dataset.budget).toFixed(4) + '，点击确认后继续。'); }
        throw e;
      }
    } else if (name === 'practice-sources') {
      detailTab = 'questions'; questionId = null; await loadStudents(task); budget = null;
      var data = await req(path(task) + '/practice/sources'); sourcePool = data.questions;
      form('从原错题选题', choices(task.student_ids || []) + field('练习目标', 'tm-practice-goal', task.goal.slice(0, 300)) + '<p>' + esc(data.note) + '</p>' + sourcePool.map(function (q) { return '<label class="tm-task-check"><input type="checkbox" class="tm-practice-source" value="' + q.question_id + '">第 ' + esc(q.question_no) + ' 题 · ' + esc(q.question_type || '未标注题型') + ' · 页 ' + esc(q.source_page || '待补充') + ' · ' + q.wrong_student_ids.length + ' 人失分</label><p>' + esc(q.prompt.slice(0, 200)) + '</p>'; }).join('') + '<p>阅读题会请 AI 判断全文、节选或无需原文；独立填空直接复用。原文缺失时先补全试卷索引。采用前核对答案和语篇。</p>', 'reuse-save', '生成原题练习');
    } else if (name === 'practice-reuse-save') {
      var ids = Array.from(document.querySelectorAll('.tm-practice-source:checked')).map(function (e) { return Number(e.value); });
      try {
        current = await req(path(task) + '/practice/reuse', 'POST', { objective: value('tm-practice-goal'), student_ids: selected(), question_ids: ids, level: 'basic', session_id: Number(teachMateState.getSnapshot().currentSessionId), confirmed_budget_yuan: budget });
        await teachMateTasks.refresh(true); await show(task, current.id);
      } catch (e) {
        if (e.detail && e.detail.code === 'PRACTICE_BUDGET_CONFIRMATION') { budget = Number(e.detail.estimated_cost_yuan); el.textContent = '确认费用并生成'; throw new Error('预计费用 ¥' + budget.toFixed(4) + '，点击确认后生成。'); }
        throw e;
      }
    } else if (name === 'practice-edit-question') {
      selectedQuestion = current.questions.find(function (q) { return q.id === Number(el.dataset.questionId); });
      var q = selectedQuestion;
      form('修改题目并另存新版本', field('完整题干', 'tm-practice-prompt', q.prompt, true) + field('阅读原文（无需时留空）', 'tm-practice-passage', q.passage || '', true) + field('选项（每行：A: 内容；非选择题留空）', 'tm-practice-options', Object.keys(q.options).map(function (k) { return k + ': ' + q.options[k]; }).join('\n'), true) + field('参考答案（每行一个合理答案）', 'tm-practice-answers', q.answers.join('\n'), true) + field('解法与说明', 'tm-practice-explanation', q.explanation, true) + field('逐级提示（每行一级）', 'tm-practice-hints', q.hints.join('\n'), true) + '<p>保存为待核对的新练习，其他题目保留。原题版本和已有作答不会改变。</p>', 'question-save');
    } else if (name === 'practice-question-save') {
      var options = {};
      value('tm-practice-options').split('\n').filter(Boolean).forEach(function (line) { var split = line.search(/[:：]/); if (split < 1) throw new Error('选项格式为 A: 内容'); options[line.slice(0, split).trim()] = line.slice(split + 1).trim(); });
      var q = selectedQuestion;
      detailTab = 'questions'; questionId = null;
      current = await req(path(task, current.id) + '/questions/' + q.id + '/revise', 'POST', { objective: q.objective, kind: q.kind, prompt: value('tm-practice-prompt'), passage: value('tm-practice-passage'), options: options, answers: value('tm-practice-answers').split('\n').filter(Boolean), explanation: value('tm-practice-explanation'), hints: value('tm-practice-hints').split('\n').filter(Boolean), difficulty: q.difficulty });
      await teachMateTasks.refresh(true); await show(task, current.id);
    } else if (name === 'practice-manual') {
      detailTab = 'questions'; questionId = null; await loadStudents(task); parent = null;
      form('手工添加练习', choices(task.student_ids || []) + field('练习标题', 'tm-practice-title', '') + field('练习目标', 'tm-practice-goal', task.goal.slice(0, 300)) + '<label class="tm-task-field">题型<select id="tm-practice-kind"><option value="text">开放作答</option><option value="number">数值 / 分数</option></select></label>' + field('完整题干', 'tm-practice-prompt', '', true) + field('参考答案（每行一个合理答案）', 'tm-practice-answers', '', true) + field('解法与说明', 'tm-practice-explanation', '', true) + '<p>先添加一题作为诊断或复测基础；多题专项练习可使用 AI 生成。</p>', 'manual-save');
    } else if (name === 'practice-manual-save') {
      var goal = value('tm-practice-goal');
      current = await req(path(task) + '/practices', 'POST', { title: value('tm-practice-title'), objective: goal, student_ids: selected(), level: 'basic', questions: [{ objective: goal, kind: value('tm-practice-kind'), prompt: value('tm-practice-prompt'), options: {}, answers: value('tm-practice-answers').split('\n').filter(Boolean), explanation: value('tm-practice-explanation'), hints: [], difficulty: 'basic' }] });
      await teachMateTasks.refresh(true); await show(task, current.id);
    } else if (name === 'practice-targets') {
      // Load the whole allowed class, including students outside an existing group.
      students = await req('/api/v1/students?term_id=' + task.term_id + (task.class_id ? '&class_id=' + task.class_id : ''));
      modalForm('设置任务对象', '<label class="tm-task-field">任务范围<select id="tm-practice-target-type"><option value="class">班级</option><option value="individual">个人</option><option value="group">小组</option></select></label>' + choices(task.student_ids || []) + '<p>个人选择一位，小组可多选；班级范围无需勾选名单。已有练习保留原对象。</p>', 'targets-save');
      document.getElementById('tm-practice-target-type').value = task.target_type || 'class';
    } else if (name === 'practice-targets-save') {
      var type = value('tm-practice-target-type');
      await req(path(task), 'PATCH', { expected_revision: task.revision, target_type: type, student_ids: type === 'class' ? [] : selected() });
      closeModal(); teachMateTasks.close(); await teachMateTasks.refresh(true);
    }
  }
  window.teachMatePractice = { openPlugin: async function () { var id = teachMateTasks.currentTaskId(); if (!id) { await teachMateTasks.startTaskCreation(function (task) { return generationForm(task); }); return; } var task = await req('/api/v1/teaching/tasks/' + id); await generationForm(task); }, workspace: workspace, action: action, releaseSource: releaseSource };
})();
