    // ================= TeachMate API 层 =================
    // 封装所有与 /api/v1/agent 的交互，供 teachmate-state 和 teachmate-interactions 调用。

    const teachMateApi = (function () {
      const BASE = '/api/v1/agent';

      /** @returns {string} 当前 workbenchToken */
      function token() {
        return workbenchToken || '';
      }

      /**
       * 发送 API 请求（复用全局 apiRequest）。
       * @param {string} path - 相对路径（如 /sessions）
       * @param {object} [opts] - fetch options
       * @param {number} [opts.timeoutMs] - 超时毫秒
       */
      async function request(path, opts) {
        return apiRequest(`${BASE}${path}`, opts);
      }

      // --- 会话 ---

      async function listSessions(termId, search) {
        const params = new URLSearchParams();
        if (termId != null) params.set('term_id', termId);
        if (search) params.set('search', search);
        const q = params.toString() ? `?${params.toString()}` : '';
        return request(`/sessions${q}`);
      }

      async function listArchivedSessions(termId) {
        const params = new URLSearchParams({ include_archived: 'true' });
        if (termId != null) params.set('term_id', termId);
        const sessions = await request(`/sessions?${params.toString()}`);
        return (sessions || []).filter(item => item && item.status === 'archived');
      }

      async function getSessionPreferences(termId) {
        const params = new URLSearchParams({ term_id: String(termId) });
        return request(`/session-preferences?${params.toString()}`);
      }

      async function saveSessionPreferences(preferences) {
        return request('/session-preferences', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(preferences || {}),
        });
      }

      async function listTrashSessions() {
        return request('/sessions/trash');
      }

      async function restoreSession(sessionId) {
        return request(`/sessions/${sessionId}/restore`, { method: 'POST' });
      }

      async function createSession(payload) {
        return request('/sessions', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
      }

      async function getSession(sessionId) {
        return request(`/sessions/${sessionId}`);
      }

      async function updateSession(sessionId, patch) {
        return request(`/sessions/${sessionId}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(patch),
        });
      }

      async function deleteSession(sessionId) {
        return request(`/sessions/${sessionId}`, { method: 'DELETE' });
      }

      async function listMessages(sessionId) {
        return request(`/sessions/${sessionId}/messages`);
      }

      // --- 消息发送（异步运行） ---

      /**
       * 发送消息，返回 { message_id, run_id, status }
       * @param {number} sessionId
       * @param {string} content
       * @param {string} [quickTask]
       * @param {number[]} [attachmentIds]
       * @param {string} [modelId] - 本次运行绑定的模型档案 ID
       */
      async function sendMessage(sessionId, content, quickTask, attachmentIds, modelId) {
        const body = { content };
        if (quickTask) body.quick_task = quickTask;
        if (attachmentIds && attachmentIds.length) body.attachment_ids = attachmentIds;
        if (modelId) body.model_id = modelId;
        return request(`/sessions/${sessionId}/messages`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
          timeoutMs: 10000,
        });
      }

      // --- 运行管理 ---

      async function getRun(runId) {
        return request(`/runs/${runId}`);
      }

      /**
       * 轮询运行事件（非 SSE 模式 / SSE 不可用时回退）。
       * @param {number} runId
       * @param {number} [after=0] - 事件游标，从此位置开始返回增量事件
       * @returns {Promise<object>} { events, status, next_after, ... }
       */
      async function getRunEvents(runId, after) {
        var q = (typeof after === 'number' && after > 0) ? '?after=' + after : '';
        return request('/runs/' + runId + '/events' + q);
      }

      /**
       * 创建 SSE 客户端（Fetch SSE 模式）。
       * 调用方负责 start()/close() 生命周期管理。
       * @param {number} runId
       * @param {function} onEvent - 事件回调 (eventObj) => void
       * @param {function} onError - SSE 不可用回调 (err) => void
       * @param {function} onTerminal - 终态事件回调 (eventObj) => void
       * @returns {SSEClient}
       */
      function createSSEClient(runId, onEvent, onError, onTerminal) {
        return new teachMateEvents.SSEClient(runId, onEvent, onError, onTerminal);
      }

      async function cancelRun(runId) {
        return request(`/runs/${runId}/cancel`, { method: 'POST' });
      }

      async function retryRun(runId) {
        return request(`/runs/${runId}/retry`, { method: 'POST' });
      }

      async function confirmRun(runId) {
        return request(`/runs/${runId}/confirm`, { method: 'POST' });
      }

      // --- 多 Agent 任务组 ---

      async function createAnalysisGroup(payload) {
        var result = await request('/groups', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload || {}),
          timeoutMs: 10000,
        });
        // 批量任务返回后直接交给 TeachMate 任务卡；非 TeachMate 调用方不受影响。
        if (typeof window !== 'undefined' && window.teachMateState && typeof window.teachMateState.setAnalysisGroup === 'function' && result) {
          window.teachMateState.setAnalysisGroup(result);
          if (result.id && typeof window.tmRefreshAnalysisGroup === 'function') window.tmRefreshAnalysisGroup(result.id, true);
        }
        return result;
      }

      async function getAnalysisGroup(groupId) {
        return request(`/groups/${groupId}`);
      }

      async function getLatestAnalysisGroupForSession(sessionId) {
        return request(`/groups/session/${sessionId}/latest`);
      }

      async function confirmAnalysisGroup(groupId, confirmedBudgetYuan) {
        return request(`/groups/${groupId}/confirm`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ confirmed_budget_yuan: confirmedBudgetYuan }),
        });
      }

      async function cancelAnalysisGroup(groupId) {
        return request(`/groups/${groupId}/cancel`, { method: 'POST' });
      }

      async function retryAnalysisGroup(groupId, studentIds) {
        return request(`/groups/${groupId}/retry`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ student_ids: Array.isArray(studentIds) ? studentIds : [] }),
        });
      }

      // --- 证据 ---

      async function getRunEvidence(runId) {
        return request(`/runs/${runId}/evidence`);
      }

      // --- 能力与估算 ---

      async function listCapabilities() {
        return request('/capabilities');
      }

      // 插件宿主 API 不走 /agent 前缀，供设置页读取 bundled/installed 插件。
      async function listPlugins() {
        return apiRequest('/api/v1/plugins');
      }
      async function getSettings() {
        return apiRequest('/api/v1/settings');
      }
      async function updateSettings(payload) {
        return apiRequest('/api/v1/settings', {
          method: 'PATCH', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload || {}),
        });
      }
      async function getMockSchoolSyncPayload() {
        return apiRequest('/api/v1/school-sync/mock/payload');
      }
      async function previewSchoolSync(payload) {
        return apiRequest('/api/v1/school-sync/preview', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload || {}),
        });
      }
      async function applySchoolSync(payload) {
        return apiRequest('/api/v1/school-sync/apply', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload || {}), timeoutMs: 60000,
        });
      }
      async function queueSchoolSync(payload) {
        return apiRequest('/api/v1/school-sync/jobs', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload || {}), timeoutMs: 10000,
        });
      }
      async function getSchoolSyncJob(jobId) {
        return apiRequest('/api/v1/school-sync/jobs/' + encodeURIComponent(jobId));
      }
      async function retrySchoolSyncJob(jobId) {
        return apiRequest('/api/v1/school-sync/jobs/' + encodeURIComponent(jobId) + '/retry', { method: 'POST' });
      }
      async function getSchoolSyncStatus() {
        return apiRequest('/api/v1/school-sync/status');
      }
      async function listSchoolSyncRuns(limit) {
        return apiRequest('/api/v1/school-sync/runs?limit=' + encodeURIComponent(limit || 10));
      }
      async function getQuestionMetrics(examId, termId, classId) {
        var params = new URLSearchParams();
        if (termId != null) params.set('term_id', termId);
        if (classId != null) params.set('class_id', classId);
        var query = params.toString();
        return apiRequest('/api/v1/exams/' + encodeURIComponent(examId) + '/question-metrics' + (query ? '?' + query : ''));
      }
      async function getStudentItemResults(examId, studentId, termId) {
        var query = termId != null ? '?term_id=' + encodeURIComponent(termId) : '';
        return apiRequest('/api/v1/exams/' + encodeURIComponent(examId) + '/students/' + encodeURIComponent(studentId) + '/item-results' + query);
      }
      async function getStudentScoreDetails(examId, studentId, termId) {
        var query = termId != null ? '?term_id=' + encodeURIComponent(termId) : '';
        return apiRequest('/api/v1/exams/' + encodeURIComponent(examId) + '/students/' + encodeURIComponent(studentId) + '/score-details' + query);
      }
      async function overrideStudentItemResult(examId, studentId, questionId, payload, termId) {
        var query = termId != null ? '?term_id=' + encodeURIComponent(termId) : '';
        return apiRequest('/api/v1/exams/' + encodeURIComponent(examId) + '/students/' + encodeURIComponent(studentId) + '/item-results/' + encodeURIComponent(questionId) + query, {
          method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload || {}),
        });
      }
      async function enablePlugin(pluginId) {
        return apiRequest('/api/v1/plugins/' + encodeURIComponent(pluginId) + '/enable', { method: 'POST' });
      }
      async function disablePlugin(pluginId) {
        return apiRequest('/api/v1/plugins/' + encodeURIComponent(pluginId) + '/disable', { method: 'POST' });
      }
      async function connectWorkBuddy() {
        return apiRequest('/api/v1/plugin/auth/connect', { method: 'POST' });
      }
      async function listWorkBuddyConnections() {
        return apiRequest('/api/v1/plugin/auth/tokens');
      }
      async function revokeWorkBuddyConnection(tokenId) {
        return apiRequest('/api/v1/plugin/auth/revoke', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ token_id: tokenId }),
        });
      }
      async function checkPluginHealth(pluginId) {
        return apiRequest('/api/v1/plugins/' + encodeURIComponent(pluginId) + '/health');
      }
      async function callPlugin(pluginId, tool, argumentsPayload) {
        return apiRequest('/api/v1/plugins/' + encodeURIComponent(pluginId) + '/call', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ tool: tool, arguments: argumentsPayload || {} }),
        });
      }
      async function installPlugin(path) {
        return apiRequest('/api/v1/plugins/install', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path: path }),
        });
      }
      async function installPluginArchive(file) {
        const response = await fetch('/api/v1/plugins/install', {
          method: 'POST',
          headers: {
            Authorization: 'Bearer ' + token(),
            'Content-Type': 'application/zip',
            'X-Plugin-Filename': file && file.name ? file.name : 'plugin.zip',
          },
          body: file,
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
          const error = new Error((payload.detail && payload.detail.message) || '插件安装失败');
          error.status = response.status;
          throw error;
        }
        return payload;
      }
      async function listPluginCatalog() {
        return apiRequest('/api/v1/plugins/catalog/list');
      }
      async function installPluginFromCatalog(pluginId, version) {
        return apiRequest('/api/v1/plugins/install-from-catalog', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ id: pluginId, version: version || null }),
        });
      }
      async function uninstallPlugin(pluginId) {
        return apiRequest('/api/v1/plugins/' + encodeURIComponent(pluginId), { method: 'DELETE' });
      }

      async function estimate(payload) {
        return request('/estimate', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
      }

      // --- 教师评价确认（Evaluation） ---

      async function listEvaluations(params) {
        var qs = new URLSearchParams();
        if (params) {
          if (params.term_id != null) qs.set('term_id', params.term_id);
          if (params.student_id != null) qs.set('student_id', params.student_id);
          if (params.status) qs.set('status', params.status);
        }
        var q = qs.toString() ? '?' + qs.toString() : '';
        return request('/evaluations' + q);
      }

      async function getEvaluation(evaluationId) {
        return request('/evaluations/' + evaluationId);
      }

      async function createEvaluation(payload) {
        return request('/evaluations', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
      }

      async function updateEvaluation(evaluationId, patch) {
        return request('/evaluations/' + evaluationId, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(patch),
        });
      }

      async function confirmEvaluation(evaluationId) {
        return request('/evaluations/' + evaluationId + '/confirm', { method: 'POST' });
      }

      async function getEvaluationAudit(evaluationId) {
        return request('/evaluations/' + evaluationId + '/audit');
      }

      async function listProfileRevisions(params) {
        var qs = new URLSearchParams();
        params = params || {};
        if (params.student_id != null) qs.set('student_id', params.student_id);
        if (params.term_id != null) qs.set('term_id', params.term_id);
        if (params.run_id != null) qs.set('run_id', params.run_id);
        if (params.status) qs.set('status', params.status);
        var q = qs.toString() ? '?' + qs.toString() : '';
        return request('/profile-revisions' + q);
      }

      async function confirmProfileRevision(revisionId) {
        return request('/profile-revisions/' + revisionId + '/confirm', { method: 'POST' });
      }

      async function rejectProfileRevision(revisionId, reason) {
        return request('/profile-revisions/' + revisionId + '/reject', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ reason: reason || '' }),
        });
      }

      // --- 附件上传与解析（U4 管线前台入口） ---

      /**
       * 上传附件（base64 传输）。
       * @param {object} payload - { title, original_name, mime_type, content_base64 }
       * @returns {Promise<object>} AttachmentRead
       */
      function createAttachment(payload, termId) {
        var q = termId != null ? '?term_id=' + encodeURIComponent(termId) : '';
        return apiRequest('/api/v1/attachments' + q, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
      }

      /**
       * 多部件流式上传附件（L1 / AttachmentUploadAPI v1）。
       * 使用 XMLHttpRequest + FormData，支持上传进度回调与取消（fetch 无进度）。
       * 不手动设置 Content-Type，由浏览器生成 multipart/form-data 边界。
       * @param {File} file - 待上传文件
       * @param {object} [opts]
       * @param {number} [opts.termId]
       * @param {string} [opts.title]
       * @param {string} [opts.source] - 来源标记（TeachMate 外部附件不进入 WorkBench 资料库）
       * @param {number} [opts.timeoutMs] - 0 表示不限时（大文件）
       * @param {function} [opts.onProgress] - (percent, loaded, total) => void
       * @param {AbortSignal} [opts.signal] - 用于取消上传
       * @returns {Promise<object>} AttachmentRead
       */
      function uploadAttachment(file, opts) {
        opts = opts || {};
        var termId = opts.termId;
        var title = opts.title || (file && file.name ? file.name.replace(/\.[^.]+$/, '') : '');
        var controller = null;
        var aborted = false;
        return new Promise(function (resolve, reject) {
          var xhr = new XMLHttpRequest();
          var url = '/api/v1/attachments/upload' +
            (termId != null ? '?term_id=' + encodeURIComponent(termId) : '');
          xhr.open('POST', url, true);
          xhr.setRequestHeader('Authorization', 'Bearer ' + (workbenchToken || ''));
          xhr.timeout = opts.timeoutMs || 0;

          if (xhr.upload && opts.onProgress) {
            xhr.upload.onprogress = function (event) {
              if (event.lengthComputable && event.total) {
                var pct = Math.round((event.loaded / event.total) * 100);
                try { opts.onProgress(pct, event.loaded, event.total); } catch (cbError) {}
              }
            };
          }
          xhr.onload = function () {
            var payload = null;
            try { payload = JSON.parse(xhr.responseText); } catch (parseError) {}
            if (xhr.status >= 200 && xhr.status < 300) {
              resolve(payload);
              return;
            }
            if (aborted) return;  // 取消优先
            var detail = payload && payload.detail;
            var err = new Error(_uploadErrorMessage(payload, xhr.status));
            err.status = xhr.status;
            err.payload = payload;
            err.detail = detail;
            if (detail && typeof detail === 'object' && !Array.isArray(detail) && detail.code) {
              err.code = String(detail.code);
            }
            reject(err);
          };
          xhr.onerror = function () {
            if (aborted) return;
            var err = new Error('网络错误，附件上传失败');
            err.code = 'NETWORK_ERROR';
            reject(err);
          };
          xhr.ontimeout = function () {
            if (aborted) return;
            var err = new Error('上传超时，请检查网络后重试');
            err.code = 'REQUEST_TIMEOUT';
            reject(err);
          };
          xhr.onabort = function () {
            aborted = true;
            var err = new Error('已取消上传');
            err.code = 'UPLOAD_CANCELLED';
            reject(err);
          };

          if (opts.signal) {
            if (opts.signal.aborted) { xhr.abort(); }
            else {
              opts.signal.addEventListener('abort', function () { xhr.abort(); });
            }
          }

          var form = new FormData();
          // 不要手动设置 Content-Type；浏览器会加 boundary
          form.append('file', file, file && file.name ? file.name : 'upload.bin');
          if (title) form.append('title', title);
          if (opts.source) form.append('source', opts.source);
          xhr.send(form);
        });
      }

      /** 从附件接口错误体提取可读文案（与 apiErrorMessage 一致）。 */
      function _uploadErrorMessage(payload, status) {
        var detail = payload && payload.detail;
        if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
          if (detail.message && detail.code) return String(detail.message) + '（' + String(detail.code) + '）';
          if (detail.message) return String(detail.message);
          if (detail.code) return '附件上传失败（' + String(detail.code) + '）';
        }
        return '附件上传失败（' + status + '）';
      }

      /** 列表附件（当前学期）。 */
      function listAttachments(termId) {
        var q = termId != null ? '?term_id=' + encodeURIComponent(termId) : '';
        return apiRequest('/api/v1/attachments' + q);
      }

      /** 提交附件解析任务。 */
      function parseAttachment(attachmentId, purpose) {
        return request('/attachments/' + attachmentId + '/parse', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ attachment_id: attachmentId, purpose: purpose || 'chat_context' }),
        });
      }

      /** 查询附件解析结果。 */
      function getAttachmentParseResult(attachmentId) {
        return request('/attachments/' + attachmentId + '/parse-result');
      }

      /** 查询后台任务状态。 */
      function getJob(jobId) {
        return request('/jobs/' + jobId);
      }

      /** 教师确认后晋升附件为正式资料。 */
      function promoteAttachment(attachmentId, payload) {
        return request('/attachments/' + attachmentId + '/promote', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload || { purpose: 'chat_context' }),
        });
      }

      // --- 作用域实体查找（P1-12: 获取真实的数字 class_id / exam_id） ---

      /**
       * 获取班级列表（含数字 id）。
       * 后端 GET /api/v1/classes 返回 ClassRead [{id, name, ...}]
       * @param {number} [termId]
       * @returns {Promise<Array<{id:number,name:string,...}>>}
       */
      async function listClasses(termId) {
        var q = termId != null ? '?term_id=' + encodeURIComponent(termId) : '';
        return apiRequest('/api/v1/classes' + q);
      }

      /**
       * 根据班级名称查找数字 class_id。
       * @param {string} className - 班级名称（如 "1"）
       * @param {number} [termId]
       * @returns {Promise<number|null>}
       */
      async function findClassIdByName(className, termId) {
        if (!className) return null;
        var classes = await listClasses(termId);
        // MONI 保留学校端的正式名称（如“七年级11班”），WorkBench
        // 为了简洁展示会将它归一为“711”。查找真实 class_id 时必须复用
        // 同一套归一逻辑，否则 MONI 同步后还要教师额外点一次“保存设置”。
        // 匹配仍然是归一后的完整值精确相等，不会把相近班级混在一起。
        var canonical = function (value) {
          var text = String(value == null ? '' : value).trim();
          if (typeof normalizeClassName === 'function') {
            var normalized = normalizeClassName(text);
            if (normalized) text = String(normalized);
          }
          return text.trim().replace(/班\s*$/, '').replace(/\s+/g, '');
        };
        var target = canonical(className);
        var found = classes.find(function (c) { return canonical(c && c.name) === target; });
        return found ? found.id : null;
      }

      /**
       * 获取考试列表（含数字 id 和 source_key）。
       * 后端 GET /api/v1/exams 返回 ExamRead [{id, source_key, name, ...}]
       * @param {number} [termId]
       * @returns {Promise<Array<{id:number,source_key:string|null,name:string,...}>>}
       */
      async function listExams(termId) {
        var q = termId != null ? '?term_id=' + encodeURIComponent(termId) : '';
        return apiRequest('/api/v1/exams' + q);
      }

      /** 获取所选考试和班级范围的确定性汇总，用于分析前的数据质量提示。 */
      async function getExamSummary(examId, termId, classId) {
        var params = new URLSearchParams();
        if (termId != null) params.set('term_id', termId);
        if (classId != null) params.set('class_id', classId);
        var q = params.toString() ? '?' + params.toString() : '';
        return apiRequest('/api/v1/exams/' + encodeURIComponent(examId) + '/summary' + q);
      }

      /**
       * 根据前端 exam ID（对应后端 source_key）查找数字 exam_id。
       * @param {string|number} examKey - 前端 exam ID（对应 Exam.source_key）
       * @param {number} [termId]
       * @returns {Promise<number|null>}
       */
      async function findExamIdByKey(examKey, termId) {
        if (!examKey) return null;
        var exams = await listExams(termId);
        var keyStr = String(examKey);
        // 先精确匹配 source_key；兼容工作台状态中的 camelCase 字段。
        var found = exams.find(function (e) {
          return String(e.source_key || e.sourceKey || '') === keyStr;
        });
        // 回退：匹配 id（如果 examKey 本身就是数字 id）。
        if (!found) {
          var numId = Number(examKey);
          if (!isNaN(numId) && numId > 0) {
            found = exams.find(function (e) { return Number(e.id) === numId; });
          }
        }
        // 兼容旧数据：工作台中的入学考试可能只有 examKind="entrance"，
        // 而数据库迁移后 source_key 不一定仍然叫 entrance。
        if (!found && (keyStr === 'entrance' || keyStr.indexOf('entrance') === 0)) {
          found = exams.find(function (e) {
            return String(e.exam_kind || e.examKind || '').toLowerCase() === 'entrance';
          });
        }
        return found ? found.id : null;
      }

      async function listStudents(termId, classId) {
        var params = new URLSearchParams();
        if (termId != null) params.set('term_id', termId);
        if (classId != null) params.set('class_id', classId);
        // 学生列表由 core router 直接注册在 /api/v1/students；不要拼接不存在的 /core 前缀。
        return apiRequest('/api/v1/students?' + params.toString());
      }

      async function findStudentId(studentKey, termId, classId) {
        if (studentKey == null) return null;
        var students = await listStudents(termId, classId);
        var key = String(studentKey);
        var found = students.find(function (s) {
          return String(s.id) === key || String(s.student_no) === key || String(s.name) === key;
        });
        return found ? found.id : null;
      }

      // --- Provider ---

      async function getProviderInfo() {
        return request('/provider');
      }

      async function listProviders() {
        return request('/providers');
      }

      async function switchProvider(payload) {
        return request('/provider/switch', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
      }

      // --- Provider 测试与运行时状态（B3-10） ---

      async function testProviderConnection(payload) {
        return request('/provider/test', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
      }

      async function getProviderRuntimeStatus() {
        return request('/provider/status');
      }

      async function listModelProfiles() {
        return request('/models');
      }

      async function saveModelProfile(payload) {
        return request('/models', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
      }

      async function activateModelProfile(id) {
        return request('/models/' + encodeURIComponent(id) + '/activate', { method: 'POST' });
      }

      async function deleteModelProfile(id) {
        return request('/models/' + encodeURIComponent(id), { method: 'DELETE' });
      }

      // --- 运行时信息（S0-02：版本与迁移事实统一） ---
      // 应用版本与数据库迁移号以服务端 /api/v1/runtime 为唯一事实源，
      // 前端导出等场景优先读取，失败时回退内置常量。

      let _runtimeCache = null;

      async function getRuntime() {
        if (_runtimeCache) return _runtimeCache;
        _runtimeCache = apiRequest('/api/v1/runtime')
          .finally(function () { _runtimeCache = null; });
        return _runtimeCache;
      }

      /** 设置页内置 Token 用量面板。 */
      async function getTokenUsage(rangeKey) {
        var key = ['today', '7d', '30d', 'all'].includes(String(rangeKey || 'today')) ? String(rangeKey || 'today') : 'today';
        return apiRequest('/api/v1/token-usage?range=' + encodeURIComponent(key));
      }

      /**
       * L4-D：提交文档导出任务（PDF/DOCX）。
       * @param {object} spec - DocumentSpec v1（含 format / title / inline_content / source 等）
       * @returns {Promise<object>} { job_id, status, artifact, reused, format_hint }
       */
      async function exportDocument(spec) {
        return apiRequest('/api/v1/documents/export', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(spec),
          timeoutMs: 60000,
        });
      }

      /**
       * L4-D：下载已生成的文档产物（PDF/DOCX/HTML），返回 Blob。
       * @param {string|number} artifactId
       * @returns {Promise<Blob>}
       */
      async function downloadDocumentArtifact(artifactId) {
        const response = await fetch(
          '/api/v1/documents/download/' + encodeURIComponent(artifactId),
          { headers: { Authorization: 'Bearer ' + token() } }
        );
        if (!response.ok) {
          let detail = null;
          try { detail = await response.json(); } catch (e) { /* ignore */ }
          const err = new Error('文档下载失败（' + response.status + '）');
          err.status = response.status;
          err.payload = detail;
          throw err;
        }
        return response.blob();
      }

      return {
        listSessions,
        listArchivedSessions,
        getSessionPreferences,
        saveSessionPreferences,
        listTrashSessions,
        restoreSession,
        createSession,
        getSession,
        updateSession,
        deleteSession,
        listMessages,
        sendMessage,
        getRun,
        getRunEvents,
        createSSEClient,
        cancelRun,
        retryRun,
        confirmRun,
        createAnalysisGroup,
        getAnalysisGroup,
        getLatestAnalysisGroupForSession,
        confirmAnalysisGroup,
        cancelAnalysisGroup,
        retryAnalysisGroup,
        getRunEvidence,
        listCapabilities,
        listPlugins,
        getSettings,
        updateSettings,
        getMockSchoolSyncPayload,
        previewSchoolSync,
        applySchoolSync,
        queueSchoolSync,
        getSchoolSyncJob,
        retrySchoolSyncJob,
        getSchoolSyncStatus,
        listSchoolSyncRuns,
        getQuestionMetrics,
        getStudentItemResults,
        getStudentScoreDetails,
        overrideStudentItemResult,
        enablePlugin,
        disablePlugin,
        connectWorkBuddy,
        listWorkBuddyConnections,
        revokeWorkBuddyConnection,
        checkPluginHealth,
        callPlugin,
        installPlugin,
        installPluginArchive,
        listPluginCatalog,
        installPluginFromCatalog,
        uninstallPlugin,
        estimate,
        listClasses,
        findClassIdByName,
        listExams,
        getExamSummary,
        findExamIdByKey,
        listStudents,
        findStudentId,
        getProviderInfo,
        listProviders,
        switchProvider,
        testProviderConnection,
        getProviderRuntimeStatus,
        listModelProfiles,
        saveModelProfile,
        activateModelProfile,
        deleteModelProfile,
        listEvaluations,
        getEvaluation,
        createEvaluation,
        updateEvaluation,
        confirmEvaluation,
        getEvaluationAudit,
        listProfileRevisions,
        confirmProfileRevision,
        rejectProfileRevision,
        createAttachment,
        uploadAttachment,
        listAttachments,
        parseAttachment,
        getAttachmentParseResult,
        getJob,
        promoteAttachment,
        getRuntime,
        getTokenUsage,
        exportDocument,
        downloadDocumentArtifact,
      };
    })();

    // 暴露到 window 供测试与调试访问
    if (typeof window !== 'undefined') window.teachMateApi = teachMateApi;
