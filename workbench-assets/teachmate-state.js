    // ================= TeachMate 状态管理 =================
    // 独立状态机，不依赖 workbench-core 的 state。
    // 运行状态：idle → submitting → queued → running → waiting_confirmation → completed/failed/cancelled/degraded
    // 事件传输：优先 Fetch SSE，SSE 不可用时回退轮询。

    const teachMateState = (function () {
      // --- 事件类型常量（与后端 event_types.py 对齐） ---
      const EVENT_TYPES = {
        // 运行生命周期
        RUN_REGISTERED: 'run.registered',
        RUN_STARTED: 'run.started',
        RUN_COMPLETED: 'run.completed',
        RUN_FAILED: 'run.failed',
        RUN_CANCELLED: 'run.cancelled',
        RUN_WAITING_CONFIRMATION: 'run.waiting_confirmation',
        RUN_IDLE: 'run.idle',
        RUN_DEGRADED: 'run.degraded',
        // 模型事件
        MODEL_STARTED: 'model.started',
        MODEL_DELTA: 'model.delta',
        MODEL_THINKING: 'model.thinking',
        MODEL_COMPLETED: 'model.completed',
        // 工具事件
        TOOL_STARTED: 'tool.started',
        TOOL_COMPLETED: 'tool.completed',
        // 用量事件
        USAGE_UPDATED: 'usage.updated',
        // 验证事件
        VALIDATION_STARTED: 'validation.started',
        VALIDATION_FAILED: 'validation.failed',
        // 可选的公开进度事件：由运行时提供短标题/摘要/下一步，不暴露原始思维链。
        PROGRESS_STEP_STARTED: 'progress.step_started',
        PROGRESS_STEP_UPDATED: 'progress.step_updated',
        PROGRESS_STEP_COMPLETED: 'progress.step_completed',
        PROGRESS_HEARTBEAT: 'progress.heartbeat',
      };
      const TERMINAL_STATUSES = new Set(['completed', 'failed', 'cancelled', 'degraded']);
      const TERMINAL_EVENT_TYPES = new Set([
        EVENT_TYPES.RUN_COMPLETED,
        EVENT_TYPES.RUN_FAILED,
        EVENT_TYPES.RUN_CANCELLED,
        EVENT_TYPES.RUN_DEGRADED,
      ]);

      // --- 内部状态 ---
      let _sessions = [];
      let _currentSessionId = null;
      let _messages = [];
      let _runState = 'idle';
      let _currentRunId = null;
      let _currentRun = null;
      let _events = [];
      let _eventCursor = 0;  // after 游标，用于增量轮询
      let _evidence = {};
      let _drafts = {};  // 按会话 ID 保存草稿
      let _searchTerm = '';  // P1-5: 会话搜索词持久化
      let _error = null;
      let _sseClient = null;       // Fetch SSE 客户端实例
      let _pollTimer = null;       // 回退轮询定时器
      let _sseFailed = false;      // SSE 是否不可用（用于决定是否直接回退轮询）
      // 运行进度：只保存公开的阶段摘要，不保存模型原始思维链。
      let _thinkingContent = '';
      let _thinkingActive = false;
      // 面向教师的连接/进度状态；不保存模型原始思维链。
      let _progressConnection = 'connected';
      let _lastProgressAt = null;
      let _runStartedAt = null;    // RUN_STARTED 时间戳（ms）
      let _runCompletedAt = null;  // 终态事件时间戳（ms）
      let _finalElapsedMs = null;  // 终态时冻结的时长（ms）
      let _elapsedTimer = null;    // 运行计时器（1s 节流）
      let _capabilities = [];
      let _plugins = [];
      let _selectedPluginId = '';
      let _personalization = {
        user_address: '老师',
        tone: 'rigorous',
        custom_prompt: '',
      };
      let _providerInfo = null;
      let _savedModels = [];
      let _currentModelId = '';
      let _selectedExamId = '';
      // 底部选择器对当前会话的“下一轮分析”覆盖值。
      // null 表示用户尚未在当前会话中重新选择班级；空字符串表示明确选择“全部班级”。
      let _selectedClassName = null;
      let _examSelectionTouched = false;
      let _availableExams = [];
      let _availableExamsTermId = null;
      let _availableExamsLoaded = false;
      let _bindCurrentExam = false; // 新会话是否显式绑定 WorkBench 当前考试
      let _contextNames = {};  // 缓存: {class:{id:name}, exam:{id:name}, student:{id:name}}
      // U5: 运行时间线 + 数据就绪 + 评价
      let _timeline = [];       // 当前 run 的时间线条目（工具/模型/校验阶段投影）
      let _progressMode = 'light'; // 普通聊天 light；结构化分析/附件任务 full
      let _runCapability = 'general_chat';
      let _dataReady = null;    // 数据就绪检查结果 {ready, issues}
      let _budgetLimit = null;  // 预算上限（来自 estimate）
      let _evaluations = {};    // 按 run_id 缓存评价列表 [{EvaluationRead}]
      let _reportAnswer = null; // 当前报告的 StructuredAnswer（结构化消息解析）
      let _pendingAttachments = []; // 待随下一条消息发送的附件 [{attachmentId, title}]
      // 批量画像任务组：由批量分析入口写入；独立于当前单次 run 的状态。
      let _analysisGroup = null;
      let _analysisGroupDetailsExpanded = false;
      // 发送前需要补充范围时显示在消息区的轻量提示，不写入历史消息。
      let _scopePrompt = null;
      // Chat UI：置顶与考试文件夹同时保留本地缓存，并异步同步到后端。
      let _pinnedSessionIds = _readLocalJson('teachmate:pinned-sessions', []);
      let _sessionFolders = _readLocalJson('teachmate:session-folders', []);

      function _readLocalJson(key, fallback) {
        try {
          var raw = window.localStorage.getItem(key);
          var parsed = raw ? JSON.parse(raw) : fallback;
          return parsed == null ? fallback : parsed;
        } catch (e) { return fallback; }
      }
      function _writeLocalJson(key, value) {
        try { window.localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* 非关键 UI 状态 */ }
      }
      function _persistNavigationPreferences() {
        var api = (typeof window !== 'undefined' && window.teachMateApi) || null;
        var term = (typeof currentTermId !== 'undefined') ? currentTermId : null;
        if (!api || typeof api.saveSessionPreferences !== 'function' || !term) return;
        api.saveSessionPreferences({ term_id: Number(term), pinned_session_ids: _pinnedSessionIds.map(Number).filter(Number.isFinite), folders: _sessionFolders }).catch(function () {});
      }
      function _togglePinnedSession(sessionId) {
        var id = String(sessionId);
        var ids = _pinnedSessionIds.map(String);
        var index = ids.indexOf(id);
        if (index >= 0) ids.splice(index, 1); else ids.unshift(id);
        _pinnedSessionIds = ids;
        _writeLocalJson('teachmate:pinned-sessions', ids);
        _persistNavigationPreferences();
        _notify();
      }
      function _setSessionFolders(folders) {
        _sessionFolders = Array.isArray(folders) ? folders : [];
        _writeLocalJson('teachmate:session-folders', _sessionFolders);
        _persistNavigationPreferences();
        _notify();
      }
      function _setSessionPreferences(preferences) {
        if (!preferences) return;
        _pinnedSessionIds = Array.isArray(preferences.pinned_session_ids) ? preferences.pinned_session_ids.map(String) : [];
        _sessionFolders = Array.isArray(preferences.folders) ? preferences.folders : [];
        _writeLocalJson('teachmate:pinned-sessions', _pinnedSessionIds);
        _writeLocalJson('teachmate:session-folders', _sessionFolders);
        _notify();
      }
      function _createSessionFolder(name) {
        var label = String(name || '').trim();
        if (!label) return null;
        var folder = { id: 'folder-' + Date.now(), name: label, sessionIds: [], collapsed: false, createdAt: new Date().toISOString() };
        _setSessionFolders(_sessionFolders.concat(folder));
        return folder;
      }
      function _toggleSessionFolder(folderId) {
        _setSessionFolders(_sessionFolders.map(function (folder) {
          return folder.id === folderId ? Object.assign({}, folder, { collapsed: !folder.collapsed }) : folder;
        }));
      }
      function _moveSessionToFolder(sessionId, folderId) {
        var id = String(sessionId);
        _setSessionFolders(_sessionFolders.map(function (folder) {
          var ids = (folder.sessionIds || []).map(String).filter(function (item) { return item !== id; });
          if (folder.id === folderId) ids.push(id);
          return Object.assign({}, folder, { sessionIds: ids });
        }));
      }

      // --- 订阅 ---
      const _subscribers = new Set();
      function subscribe(fn) {
        _subscribers.add(fn);
        return () => _subscribers.delete(fn);
      }
      function _notify() {
        const snapshot = getSnapshot();
        _subscribers.forEach(fn => {
          try { fn(snapshot); } catch (e) { console.error('TeachMate subscriber error:', e); }
        });
      }

      function getSnapshot() {
        // 当前 run 的证据（右栏读取这个字段）
        var currentEvidence = (_currentRunId && _evidence[_currentRunId]) ? _evidence[_currentRunId] : [];
        return {
          sessions: _sessions,
          currentSessionId: _currentSessionId,
          messages: _messages,
          runState: _runState,
          currentRunId: _currentRunId,
          currentRun: _currentRun,
          analysisGroup: _analysisGroup,
          analysisGroupDetailsExpanded: _analysisGroupDetailsExpanded,
          scopePrompt: _scopePrompt,
          events: _events,
          evidence: _evidence,
          currentEvidence: currentEvidence,
          draft: _drafts[_currentSessionId] || '',
          searchTerm: _searchTerm,
          error: _error,
          capabilities: _capabilities,
          plugins: _plugins,
          selectedPluginId: _selectedPluginId,
          personalization: _personalization,
          providerInfo: _providerInfo,
          savedModels: _savedModels,
          currentModelId: _currentModelId || (_providerInfo && _providerInfo.profile_id) || '',
          selectedExamId: _selectedExamId,
          selectedClassName: _selectedClassName,
          examSelectionTouched: _examSelectionTouched,
          availableExams: _availableExams,
          availableExamsTermId: _availableExamsTermId,
          availableExamsLoaded: _availableExamsLoaded,
          bindCurrentExam: _bindCurrentExam,
          contextNames: _contextNames,
          timeline: _timeline,
          progressMode: _progressMode,
          runCapability: _runCapability,
          dataReady: _dataReady,
          budgetLimit: _budgetLimit,
          evaluations: _evaluations,
          reportAnswer: _reportAnswer,
          pendingAttachments: _pendingAttachments,
          pinnedSessionIds: _pinnedSessionIds,
          sessionFolders: _sessionFolders,
          isRunning: _runState === 'submitting' || _runState === 'queued' || _runState === 'running' || _runState === 'waiting_confirmation',
          canCancel: _runState === 'queued' || _runState === 'running' || _runState === 'waiting_confirmation',
          canRetry: _runState === 'failed' || _runState === 'cancelled' || _runState === 'degraded',
          // 深度思考进度（运行指示器展示思考时间 + 思考内容）
          elapsedMs: (_finalElapsedMs != null)
            ? _finalElapsedMs
            : (_runStartedAt != null ? (Date.now() - _runStartedAt) : 0),
          thinkingContent: _thinkingContent,
          thinkingActive: _thinkingActive,
          progressConnection: _progressConnection,
          lastProgressAt: _lastProgressAt,
        };
      }

      function _setRunState(s) {
        // 状态未变化时不触发通知，避免工具调用轮次（run.idle → running）无谓全量重渲染闪烁
        if (_runState === s) return false;
        _runState = s;
        _notify();
        return true;
      }
      function _setError(msg) { _error = msg; _notify(); }
      function _clearError() { if (_error !== null) { _error = null; _notify(); } }
      function _setProgressConnection(value, notify) {
        var next = value || 'connected';
        if (_progressConnection === next) return;
        _progressConnection = next;
        if (notify !== false) _notify();
      }

      // --- 深度思考计时器（1s 原地更新计时文本，不触发全量重渲染避免闪烁） ---
      function _ensureElapsedTimer() {
        if (_elapsedTimer) return;
        _elapsedTimer = setInterval(function () {
          // 只有非终态才持续刷新；终态后由视图展示最终时长。
          if (TERMINAL_STATUSES.has(_runState)) { _stopElapsedTimer(); return; }
          // 页面销毁或切换后不保留孤儿定时器（也避免后台标签页占用资源）。
          if (typeof document === 'undefined' || !document.getElementById('tmRunning')) {
            _stopElapsedTimer();
            return;
          }
          var el = document.getElementById('tmRunningElapsed');
          if (el) {
            // 阶段标签在 #tmRunningStage 节点，由视图随 snapshot 更新；计时节点只写计时，
            // 避免 1s 定时器把"正在读取数据/正在校验"等阶段覆盖成固定文案。
            var ms = _runStartedAt != null ? (Date.now() - _runStartedAt) : 0;
            el.textContent = '已用时 ' + _formatElapsedMs(ms);
          }
          if (!TERMINAL_STATUSES.has(_runState) && _lastProgressAt != null) {
            var silenceMs = Date.now() - _lastProgressAt;
            var connection = _progressConnection === 'polling'
              ? 'polling'
              : (silenceMs >= 30000 ? 'slow' : 'connected');
            if (_progressConnection !== connection) _setProgressConnection(connection, false);
            var connectionEl = document.getElementById('tmRunningConnection');
            if (connectionEl) {
              connectionEl.textContent = connection === 'polling'
                ? '实时连接暂时中断，已切换备用通道'
                : (connection === 'slow' ? '响应较慢，仍在等待模型返回' : '连接正常，分析仍在进行');
              connectionEl.classList.toggle('is-slow', connection !== 'connected');
            }
          }
        }, 1000);
      }
      function _formatElapsedMs(ms) {
        var totalSec = Math.max(0, Math.floor((ms || 0) / 1000));
        var m = Math.floor(totalSec / 60);
        var s = totalSec % 60;
        return m + '分' + (s < 10 ? '0' : '') + s + '秒';
      }
      function _timestampMs(value) {
        if (value == null || value === '') return null;
        var ms = value instanceof Date ? value.getTime() : (typeof value === 'number' ? value : Date.parse(value));
        return isFinite(ms) ? ms : null;
      }
      function _syncRunTiming(run) {
        if (!run) return;
        var started = _timestampMs(run.started_at || run.startedAt);
        var completed = _timestampMs(run.completed_at || run.completedAt);
        if (started != null) _runStartedAt = started;
        if (completed != null) _runCompletedAt = completed;
        if (_runStartedAt != null && _runCompletedAt != null) {
          _finalElapsedMs = Math.max(0, _runCompletedAt - _runStartedAt);
        }
      }
      function _stopElapsedTimer() {
        if (_elapsedTimer) { clearInterval(_elapsedTimer); _elapsedTimer = null; }
      }
      /**
       * 在"运行已开始 + 完整进度模式 + 非终态"三个条件同时满足时确保计时器在跑。
       * 用于补齐 progressMode 由 light 升到 full 的滞后场景 —— RUN_STARTED 到达时
       * 模式可能还是 light（还没产生 timeline），当时不会启动计时器，之后必须补启。
       */
      function _ensureElapsedTimerIfNeeded() {
        if (_progressMode !== 'full') return;
        if (_runStartedAt == null) return;
        if (TERMINAL_STATUSES.has(_runState)) return;
        _ensureElapsedTimer();
      }
      function _freezeElapsed(completedAt) {
        var completedMs = _timestampMs(completedAt);
        if (completedMs != null) _runCompletedAt = completedMs;
        if (_finalElapsedMs == null && _runStartedAt != null) {
          _finalElapsedMs = Math.max(0, (_runCompletedAt != null ? _runCompletedAt : Date.now()) - _runStartedAt);
        }
        _thinkingActive = false;
        _stopElapsedTimer();
      }
      // --- 深度思考增量：DOM 级追加（只更新已展开的思考区，不触发全量重渲染） ---
      function _appendThinkingDom(delta) {
        var box = document.getElementById('tmThinkingBox');
        var pre = box && !box.hidden ? box.querySelector('.tm-thinking-content') : null;
        if (pre) {
          pre.textContent += delta;
          // 内容增长时保持滚动跟随最新思考
          pre.scrollTop = pre.scrollHeight;
        }
      }

      /**
       * 深度思考行的真实计量文案：已思考 N 字。
       * 后端逐批上报 thinking_chars，这里只做展示格式化。它取代了此前
       * “AI 正在整理证据和结论”这类永远不变的占位话——数字每批都在涨，
       * 教师能直接看出模型还在产出，而不是一张静止的贴图。
       */
      function _thinkingMetricLabel(chars) {
        var n = Number(chars);
        if (!isFinite(n) || n <= 0) return '正在整理分析思路';
        if (n < 1000) return '思考中 · ' + n + ' 字';
        return '思考中 · ' + (Math.round(n / 100) / 10).toFixed(1) + 'k 字';
      }

      // 与 backend/app/agent/tools/ 注册的 ToolDefinition 一一对应。
      // 这些是真实的业务工具，所以展示出来的就是 AI 真正在做的事。
      var TOOL_LABELS = {
        get_at_risk_students: '识别高风险学生',
        get_class_ranking: '读取班级排名',
        get_common_mistakes: '汇总常见错误',
        get_error_causes: '分析错误成因',
        get_exam_paper_image: '调取试卷图像',
        get_exam_statistics: '读取考试统计',
        get_knowledge_coverage: '检查知识点覆盖',
        get_question_difficulty: '计算题目难度',
        get_question_list: '读取题目列表',
        get_risk_factors: '分析风险因素',
        get_score_distribution: '统计分数分布',
        get_student_answer_image: '调取学生答卷',
        get_student_list: '读取学生名单',
        get_student_profile: '读取学生画像',
        get_student_scores: '读取学生成绩',
        propose_student_profile_update: '提出画像更新',
        submit_teaching_report: '生成教学报告',
      };

      /**
       * 工具条目的展示名。已知工具给中文友好名；未知工具原样呈现工具名，
       * 绝不编造——真实优先于好看，教师看到的必须是模型真正调用的东西。
       */
      function _toolDisplayLabel(toolName) {
        var name = String(toolName || '').trim();
        if (!name) return '调用分析工具';
        return TOOL_LABELS[name] || name;
      }

      /**
       * 收尾仍在进行的思考段。模型一旦开始调工具或生成新回复，上一段思考
       * 就已落地；不收尾会让时间线出现"思考在跑 + 工具在跑"并存的假象。
       */
      function _closeOpenThinking(summary) {
        var changed = false;
        _timeline = _timeline.map(function (it) {
          if (!it || it.role !== 'thinking' || it.state !== 'running') return it;
          changed = true;
          return Object.assign({}, it, {
            state: 'done',
            detail: summary || it.detail || '思考完成',
          });
        });
        return changed;
      }

      // --- U5: 运行时间线投影 ---
      // 从事件流投影出时间线阶段：工具调用 / 模型生成 / 校验 / 报告。
      // 只收集有展示价值的条目，不逐 Token 刷屏。
      function _appendTimelineFromEvent(event) {
        var eventType = event && event.event;
        var data = (event && event.data) || {};
        var entry = null;
        var changed = false;
        var toolName = String(data.tool_name || data.tool || data.name || '').trim();

        // 运行时可以随每个事件提供短标题、摘要和下一步；固定文案仅作为
        // 兼容旧运行时/旧事件的兜底，避免把用户看到的过程写死。
        function publicText(keys, fallback) {
          for (var pi = 0; pi < keys.length; pi++) {
            var value = data[keys[pi]];
            if (typeof value === 'string' && value.trim()) return value.trim().slice(0, 240);
          }
          return fallback || '';
        }
        function publicNext(fallback) {
          return publicText(['next_action', 'nextAction', 'next'], fallback);
        }
        function promoteToFullProgress() {
          // 普通聊天也会收到 model.started；只有结构化能力、明确的公开进度
          // 字段或工具/校验事件才升级为完整进度卡。
          if (_runCapability !== 'general_chat' || data.progress_mode === 'full' ||
              data.progressMode === 'full' || data.title || data.label ||
              data.summary || data.detail || data.next_action || data.nextAction) {
            _progressMode = 'full';
            if (_runState === 'running') _ensureElapsedTimer();
          }
        }

        if (eventType === EVENT_TYPES.PROGRESS_STEP_STARTED ||
            eventType === EVENT_TYPES.PROGRESS_STEP_UPDATED ||
            eventType === EVENT_TYPES.PROGRESS_STEP_COMPLETED) {
          _progressMode = 'full';
          if (_runState === 'running') _ensureElapsedTimer();
          var stepId = String(data.step_id || data.stepId || data.id || '').trim();
          var progressState = eventType === EVENT_TYPES.PROGRESS_STEP_COMPLETED ? 'done' : (data.status || 'running');
          var progressTitle = publicText(['title', 'label', 'message'], '正在处理当前步骤');
          var progressEntry = {
            kind: 'progress', state: progressState, stepId: stepId,
            text: progressTitle,
            detail: publicText(['summary', 'detail'], ''),
            nextAction: publicNext('继续处理当前任务'),
            order: Number(data.order || 0), data: Object.assign({}, data),
          };
          var existing = -1;
          if (stepId) {
            for (var pei = _timeline.length - 1; pei >= 0; pei--) {
              if (_timeline[pei] && _timeline[pei].stepId === stepId) { existing = pei; break; }
            }
          }
          if (existing >= 0) {
            _timeline = _timeline.map(function (it, index) {
              return index === existing ? Object.assign({}, it, progressEntry, { ts: event.timestamp || it.ts }) : it;
            });
            return true;
          }
          entry = progressEntry;
        } else if (eventType === EVENT_TYPES.PROGRESS_HEARTBEAT) {
          // 心跳只更新连接活性，不在聊天里制造新的“步骤”。
          return false;
        } else if (eventType === EVENT_TYPES.RUN_STARTED) {
          // run.started 只代表生命周期开始，不代表模型已经完成了某个
          // 可公开的工作步骤。这里不再把它伪装成“已接收分析任务/准备数据”
          // 的时间线行；只有后端真实发来的 step/tool/model 事件才进入过程消息。
          return false;
        } else if (eventType === EVENT_TYPES.TOOL_STARTED) {
          promoteToFullProgress();
          // 模型开始调用工具 = 上一段思考已经落地，先把它收尾，
          // 否则时间线里会出现"思考还在跑"和"工具在跑"并存的假象。
          changed = _closeOpenThinking('') || changed;
          entry = {
            kind: 'tool', state: 'running',
            // 真实工具名优先：后端给什么就显示什么，不做编造；仅对已知内置
            // 工具补一个中文友好名，未知工具原样呈现（真实 > 好看）。
            text: _toolDisplayLabel(toolName),
            label: toolName || '分析工具',
            detail: publicText(['summary', 'detail', 'message'], ''),
            nextAction: publicNext(''),
            data: Object.assign({}, data, { tool: toolName, tool_name: toolName }),
          };
        } else if (eventType === EVENT_TYPES.TOOL_COMPLETED) {
          promoteToFullProgress();
          // 找到对应 tool.started 并标记完成；结果摘要优先使用运行时提供的公开说明。
          changed = _markTimelineDone('tool', function (it) {
            var completedName = String(data.tool_name || data.tool || data.name || '').trim();
            var callId = String(data.call_id || data.callId || '').trim();
            return it && ((!callId || String(it.data && (it.data.call_id || it.data.callId) || '') === callId) &&
              (!completedName || String(it.data && (it.data.tool_name || it.data.tool) || '').trim() === completedName));
          }, publicText(['summary', 'detail', 'message'], '已完成')) || changed;
        } else if (eventType === EVENT_TYPES.MODEL_STARTED) {
          promoteToFullProgress();
          changed = _closeOpenThinking('') || changed;
          entry = { kind: 'model', state: 'running', text: publicText(['title', 'label', 'message'], '正在组织分析结论'), detail: publicText(['summary', 'detail'], '正在比较已读取的事实和证据'), nextAction: publicNext('归纳主要问题并形成教学建议') };
        } else if (eventType === EVENT_TYPES.MODEL_COMPLETED) {
          promoteToFullProgress();
          changed = _markTimelineDone('model', null, publicText(['summary', 'detail', 'message'], '分析结论已形成')) || changed;
        } else if (eventType === EVENT_TYPES.VALIDATION_STARTED) {
          promoteToFullProgress();
          changed = _markTimelineDone('model', null, publicText(['summary', 'detail'], '分析结论已形成')) || changed;
          entry = { kind: 'validate', state: 'running', text: publicText(['title', 'label', 'message'], '正在校验分析结果'), detail: publicText(['summary', 'detail'], '核对数字、证据引用和数据范围'), nextAction: publicNext('确认报告可以安全展示给教师') };
        } else if (eventType === EVENT_TYPES.VALIDATION_FAILED) {
          promoteToFullProgress();
          changed = _markTimelineDone('validate', null, publicText(['summary', 'detail'], '校验需要修正')) || changed;
          entry = { kind: 'error', state: 'done', text: publicText(['title', 'label', 'message'], '发现一处需要修正的结论'), detail: publicText(['error', 'summary', 'detail'], '正在重新整理报告'), nextAction: publicNext('重新核对证据后再提交报告') };
        }
        else if (eventType === EVENT_TYPES.USAGE_UPDATED) {
          // 用量事件不作为主进度步骤，避免将技术指标变成用户负担。
          entry = null;
        }

        if (entry) {
          entry.ts = event.timestamp || new Date().toISOString();
          _timeline = [..._timeline, entry];
          changed = true;
        }
        // 如果当前过程卡已经挂载，先原地同步行节点，再等待外层视图重绘。
        // 这样 tool.completed/model.completed 到达时不会短暂停留在旧状态，
        // 也不依赖订阅者是否恰好在同一帧触发全量 render。
        if (changed && typeof document !== 'undefined' && document.getElementById('tmProcessList')) {
          _mountProcBlock();
        }
        return changed;
      }

      /** 将某一类已完成的条目标记为完成，或追加一条完成记录。 */
      function _markTimelineDone(kind, matcher, fallbackLabel) {
        var idx = -1;
        for (var i = _timeline.length - 1; i >= 0; i--) {
          var it = _timeline[i];
          if (it.kind === kind && it.state === 'running') {
            if (matcher && !matcher(it)) continue;
            idx = i;
            break;
          }
        }
        if (idx >= 0) {
          _timeline = _timeline.map(function (it, i) {
            return i === idx ? Object.assign({}, it, {
              state: 'done',
              detail: fallbackLabel || it.detail || '',
            }) : it;
          });
          return true;
        }
        return false;
      }

      function _completeOpenTimeline() {
        _timeline = _timeline.map(function (it) {
          return it && it.state === 'running' ? Object.assign({}, it, { state: 'done' }) : it;
        });
      }

      // --- U5: 状态 setter ---
      function setTimeline(timeline) {
        _timeline = timeline || [];
        // 外部恢复历史事件或测试/回放注入了结构化步骤时，自动切换完整模式。
        // 普通聊天不会产生 timeline，因此不会被误升级为分析视图。
        if (_timeline.length) _progressMode = 'full';
        // 补齐计时器：后端可能先推 run.started（当时 progressMode 仍是 light，
        // RUN_STARTED 分支的 _ensureElapsedTimer() 不会执行），之后才由 progress 事件
        // 产生 timeline 把模式升到 full。此时必须回头启动计时器，否则"已用时"永久停摆。
        _ensureElapsedTimerIfNeeded();
        _notify();
      }
      function clearTimeline() { if (_timeline.length) { _timeline = []; _notify(); } }
      function setDataReady(result) { _dataReady = result; _notify(); }
      function setBudgetLimit(yuan) { _budgetLimit = yuan; _notify(); }
      function setEvaluations(runId, list) { if (runId) _evaluations = Object.assign({}, _evaluations, { [runId]: list || [] }); _notify(); }
      function getEvaluationsForRun(runId) { return _evaluations[runId] || []; }
      function setReportAnswer(answer) { _reportAnswer = answer; _notify(); }
      function getTimeline() { return _timeline; }

      // --- U5: 待发送附件 ---
      function addPendingAttachment(attachment) {
        var attachmentId = Number(attachment && attachment.attachmentId);
        if (!attachmentId || _pendingAttachments.some(function (item) { return Number(item.attachmentId) === attachmentId; })) return;
        _pendingAttachments = [..._pendingAttachments, Object.assign({}, attachment, { attachmentId: attachmentId })];
        _notify();
      }
      function updatePendingAttachment(attachmentId, patch) {
        attachmentId = Number(attachmentId);
        _pendingAttachments = _pendingAttachments.map(function (item) {
          return Number(item.attachmentId) === attachmentId ? Object.assign({}, item, patch || {}) : item;
        });
        _notify();
      }
      function removePendingAttachment(attachmentId) {
        _pendingAttachments = _pendingAttachments.filter(function (a) { return a.attachmentId !== attachmentId; });
        _notify();
      }
      function clearPendingAttachments() {
        if (_pendingAttachments.length) { _pendingAttachments = []; _notify(); }
      }
      function getPendingAttachments() { return _pendingAttachments; }
      function setDraft(text) {
        var key = _currentSessionId || '__default__';
        if (text) {
          _drafts[key] = text;
        } else {
          delete _drafts[key];
        }
      }
      function getDraft() {
        var key = _currentSessionId || '__default__';
        return _drafts[key] || '';
      }
      function clearDraft() {
        var key = _currentSessionId || '__default__';
        delete _drafts[key];
      }

      // --- 会话 ---
      function setSessions(sessions) { _sessions = sessions; _notify(); }

      function setCurrentSession(sessionId) {
        // 统一为数字 ID（后端返回数字，DOM 属性是字符串）
        _currentSessionId = typeof sessionId === 'string' ? parseInt(sessionId, 10) : sessionId;
        if (isNaN(_currentSessionId)) _currentSessionId = null;
        _messages = [];
        _events = [];
        _eventCursor = 0;
        _evidence = {};
        _timeline = [];
        _progressMode = 'light';
        _runCapability = 'general_chat';
        _reportAnswer = null;
        _dataReady = null;
        _runState = 'idle';
        _currentRunId = null;
        _currentRun = null;
        _error = null;
        _stopStreaming();
        _stopElapsedTimer();
        _thinkingContent = '';
        _thinkingActive = false;
        _progressConnection = 'connected';
        _lastProgressAt = null;
        _runStartedAt = null;
        _runCompletedAt = null;
        _finalElapsedMs = null;
        _selectedClassName = null;
        _selectedExamId = '';
        _examSelectionTouched = false;
        _notify();
      }

      function setMessages(messages) { _messages = messages; _notify(); }

      function setCurrentRun(run) {
        _currentRun = run || null;
        _currentRunId = run && (run.id || run.run_id) ? (run.id || run.run_id) : null;
        if (run && run.capability) {
          _runCapability = String(run.capability);
          _progressMode = run.progress_mode || (_runCapability === 'general_chat' ? 'light' : 'full');
        }
        _syncRunTiming(run);
        // _syncRunTiming 会补齐 _runStartedAt，之后才能判断是否需要启动计时器。
        _ensureElapsedTimerIfNeeded();
        if (run && TERMINAL_STATUSES.has(run.status)) {
          _runState = run.status;
          _stopElapsedTimer();
        }
        _notify();
      }

      function appendMessage(message) {
        _messages = [..._messages, message];
        _notify();
      }

      function updateLastAssistantMessage(updates) {
        const idx = _messages.findIndex(m => m.role === 'assistant' && m._pending);
        if (idx >= 0) {
          _messages = _messages.map((m, i) => i === idx ? { ...m, ...updates, _pending: false } : m);
          _notify();
        }
      }

      // --- 运行状态机 ---
      function startSubmitting() {
        _clearError();
        _events = [];
        _timeline = [];
        _progressMode = 'light';
        _runCapability = 'general_chat';
        _thinkingContent = '';
        _thinkingActive = false;
        _progressConnection = 'connected';
        _lastProgressAt = Date.now();
        _runStartedAt = null;
        _runCompletedAt = null;
        _finalElapsedMs = null;
        _stopElapsedTimer();
        _setRunState('submitting');
      }

      function failSubmitting(message) {
        _error = String(message || '发送失败');
        _runState = 'failed';
        _stopStreaming();
        _stopElapsedTimer();
        _notify();
      }

      async function handleSendResponse(response) {
        var responseSessionId = _currentSessionId;
        _currentRunId = response.run_id;
        _runCapability = String(response.capability || 'general_chat');
        _progressMode = response.progress_mode || (_runCapability === 'general_chat' ? 'light' : 'full');
        _currentRun = { id: response.run_id, status: response.status || 'queued', capability: _runCapability };
        _syncRunTiming(response);
        _eventCursor = 0;
        _setRunState(response.status || 'queued');
        if (response.status === 'waiting_confirmation') {
          _stopStreaming();
          try {
            var confirmedRun = await teachMateApi.getRun(response.run_id);
            if (_currentSessionId !== responseSessionId || String(_currentRunId) !== String(response.run_id)) return;
            _currentRun = confirmedRun;
            _notify();
          } catch (e) {
            _setError('读取预算信息失败: ' + (e.message || e));
          }
          return;
        }
        _startStreaming(response.run_id);
      }

      function handleRunEvent(event) {
        // SSE 重连与轮询切换可能短暂收到同一个持久化事件；seq 是后端
        // 的运行内唯一游标，前端必须去重，否则时间线和终态提示会重复。
        if (event && typeof event.seq === 'number' && _events.some(function (item) {
          return item && item.seq === event.seq;
        })) {
          return;
        }
        _events = [..._events, event];
        _lastProgressAt = Date.now();
        _setProgressConnection('connected', false);
        var eventType = event.event;
        var data = event.data || {};

        // U5: 运行时间线投影（读取数据、调用工具、生成报告、校验、完成）
        // 时间线投影与运行状态是两条独立变化路径。首个 run.started 会顺带
        // 刷新界面，但后续 tool/model/validation 事件通常不会改变 runState；
        // 因此必须在时间线实际变化后主动通知视图，否则步骤虽已进入状态却只
        // 能在任务结束后一次性看到。
        var timelineChanged = _appendTimelineFromEvent(event);
        var runStateChanged = false;

        switch (eventType) {
          case EVENT_TYPES.RUN_STARTED:
            runStateChanged = _setRunState('running');
            _runStartedAt = _timestampMs(event.timestamp) || Date.now();
            _runCompletedAt = null;
            _finalElapsedMs = null;
            if (_progressMode === 'full') _ensureElapsedTimer();
            break;
          case EVENT_TYPES.MODEL_THINKING:
            // 不在教师端展示原始思维链；只更新公开的工作状态。
            // 原始 reasoning 可能冗长、会自我修正，也不应成为隐私或提示词泄漏面。
            // 因此这里取的是后端给的公开信号：thinking_chars（真实累计字数）
            // 与可选的 thinking_tail（显式开启时的脱敏末行），不再回退到固定文案。
            _thinkingActive = true;
            if (_runCapability !== 'general_chat' || data.public_summary || data.summary ||
                data.title || data.next_action || data.thinking_chars != null) {
              _progressMode = 'full';
              if (_runState === 'running') _ensureElapsedTimer();
            }
            var modelItem = null;
            for (var mi = _timeline.length - 1; mi >= 0; mi--) {
              if (_timeline[mi] && _timeline[mi].kind === 'model' && _timeline[mi].state === 'running') {
                modelItem = _timeline[mi];
                break;
              }
            }
            // 真实累计思考字数：后端逐批上报，前端累加。这是"深度思考"行的
            // 实时计量来源，取代此前永远不变的占位文案。
            var deltaChars = Number(data.thinking_chars);
            if (!isFinite(deltaChars) || deltaChars < 0) deltaChars = 0;
            var prevChars = modelItem && Number(modelItem.thinkingChars) ? Number(modelItem.thinkingChars) : 0;
            var totalChars = prevChars + deltaChars;

            var tail = (typeof data.thinking_tail === 'string' && data.thinking_tail.trim())
              ? data.thinking_tail.trim().slice(0, 160)
              : '';
            var summary = (typeof data.public_summary === 'string' && data.public_summary.trim())
              ? data.public_summary.trim().slice(0, 240)
              : ((typeof data.summary === 'string' && data.summary.trim())
                ? data.summary.trim().slice(0, 240)
                : '');
            // 展示优先级：真实末行 > 运行时公开摘要 > 真实计量（都没有时才用计量）
            var publicSummary = tail || summary || _thinkingMetricLabel(totalChars);
            var publicNextAction = (typeof data.next_action === 'string' && data.next_action.trim())
              ? data.next_action.trim().slice(0, 240)
              : (typeof data.nextAction === 'string' && data.nextAction.trim())
                ? data.nextAction.trim().slice(0, 240)
                : '';
            if (!modelItem) {
              _timeline = [..._timeline, {
                kind: 'model', role: 'thinking', state: 'running',
                text: (typeof data.title === 'string' && data.title.trim()) ? data.title.trim().slice(0, 240) : '深度思考',
                detail: publicSummary, nextAction: publicNextAction,
                thinkingChars: totalChars,
              }];
              _notify();
            } else {
              _timeline = _timeline.map(function (it) {
                return it === modelItem ? Object.assign({}, it, {
                  role: 'thinking',
                  detail: publicSummary,
                  nextAction: publicNextAction || it.nextAction,
                  thinkingChars: totalChars,
                }) : it;
              });
              _notify();
            }
            break;
          case EVENT_TYPES.RUN_IDLE:
            // 模型完成本轮，等待下一步指令
            if (_runState === 'running') _setRunState('running');
            break;
          case EVENT_TYPES.RUN_WAITING_CONFIRMATION:
            _setRunState('waiting_confirmation');
            break;
          case EVENT_TYPES.RUN_COMPLETED:
            _completeOpenTimeline();
            _currentRun = Object.assign({}, _currentRun || {}, { status: data.status === 'degraded' ? 'degraded' : 'completed', completed_at: data.completed_at || event.timestamp });
            if (data.status === 'degraded') {
              _setRunState('degraded');
            } else {
              _setRunState('completed');
            }
            _stopStreaming();
            _freezeElapsed(data.completed_at || event.timestamp);
            _loadFinalResults();
            break;
          case EVENT_TYPES.RUN_DEGRADED:
            _completeOpenTimeline();
            _currentRun = Object.assign({}, _currentRun || {}, { status: 'degraded', completed_at: data.completed_at || event.timestamp });
            _setRunState('degraded');
            _stopStreaming();
            _freezeElapsed(data.completed_at || event.timestamp);
            _loadFinalResults();
            break;
          case EVENT_TYPES.RUN_FAILED:
            _completeOpenTimeline();
            _currentRun = Object.assign({}, _currentRun || {}, { status: 'failed', completed_at: data.completed_at || event.timestamp });
            _setRunState('failed');
            _setError(data.error || '运行失败');
            _stopStreaming();
            _freezeElapsed(data.completed_at || event.timestamp);
            break;
          case EVENT_TYPES.RUN_CANCELLED:
            _completeOpenTimeline();
            _currentRun = Object.assign({}, _currentRun || {}, { status: 'cancelled', completed_at: data.completed_at || event.timestamp });
            _setRunState('cancelled');
            _stopStreaming();
            _freezeElapsed(data.completed_at || event.timestamp);
            break;
          case EVENT_TYPES.MODEL_COMPLETED:
            // 模型生成完成，但运行可能继续（工具调用等），不驱动状态机
            break;
          case EVENT_TYPES.USAGE_UPDATED:
            // Token 用量更新，仅记录
            break;
          default:
            // 其他事件（tool.started, model.delta 等）仅记录，不驱动状态机
            break;
        }

        // 避免 run.started 的状态通知造成重复渲染；其余时间线变化需要单独
        // 刷新，保证新增步骤和 running/done 状态能实时显示。
        if (timelineChanged && !runStateChanged) _notify();
      }

      function _loadFinalResults() {
        if (!_currentSessionId) return;
        // 所有异步结果都绑定到启动时的 session/run，避免切换会话后旧请求覆盖新内容。
        var sessionId = _currentSessionId;
        var runId = _currentRunId;
        if (runId && teachMateApi && typeof teachMateApi.getRun === 'function') {
          teachMateApi.getRun(runId).then(function (run) {
            if (_currentSessionId !== sessionId || String(_currentRunId) !== String(runId)) return;
            if (!run || String(run.id) !== String(runId)) return;
            _currentRun = run;
            _syncRunTiming(run);
            _notify();
          }).catch(function () {});
        }
        teachMateApi.listMessages(sessionId).then(msgs => {
          if (_currentSessionId !== sessionId || String(_currentRunId || '') !== String(runId || '')) return;
          setMessages(msgs);
          // U5: 从最新 assistant 消息解析结构化报告
          var answer = null;
          for (var i = msgs.length - 1; i >= 0; i--) {
            if (msgs[i].role === 'assistant' && msgs[i].structured_answer) {
              var a = msgs[i].structured_answer;
              if (typeof a === 'string') { try { a = JSON.parse(a); } catch (e) { a = null; } }
              if (a && typeof a === 'object') { answer = a; break; }
            }
          }
          _reportAnswer = answer;
          _notify();
        }).catch(e => console.error('Failed to reload messages:', e));
        if (runId) {
          teachMateApi.getRunEvidence(runId).then(ev => {
            if (_currentSessionId !== sessionId || String(_currentRunId) !== String(runId)) return;
            _evidence = { ..._evidence, [runId]: ev }; _notify();
          }).catch(e => console.error('Failed to load evidence:', e));
          // U5: 加载该运行的教师评价
          teachMateApi.listEvaluations({}).then(function (evals) {
            if (_currentSessionId !== sessionId || String(_currentRunId) !== String(runId)) return;
            var related = (evals || []).filter(function (ev) { return String(ev.analysis_run_id) === String(runId); });
            setEvaluations(runId, related);
          }).catch(function () {});
        }
      }

      // --- 事件流（SSE 优先，回退轮询） ---

      function _startStreaming(runId) {
        _stopStreaming();
        function isCurrentRun() { return String(_currentRunId || '') === String(runId); }
        _setProgressConnection('connected', false);

        // 如果 SSE 之前已判定不可用，直接走轮询
        if (_sseFailed) {
          _startPolling(runId);
          return;
        }

        _sseClient = teachMateApi.createSSEClient(
          runId,
          function (eventObj) {
            if (!isCurrentRun()) return;
            // onEvent —— 处理每个事件
            if (typeof eventObj.seq === 'number' && eventObj.seq > _eventCursor) {
              _eventCursor = eventObj.seq;
            }
            handleRunEvent(eventObj);
          },
          function (err) {
            if (!isCurrentRun()) return;
            // onError —— SSE 不可用，回退轮询
            console.warn('SSE unavailable, falling back to polling:', err);
            _sseFailed = true;
            _sseClient = null;
            _setProgressConnection('polling');
            if (TERMINAL_STATUSES.has(_runState)) return;
            _startPolling(runId);
          },
          function (eventObj) {
            if (!isCurrentRun()) return;
            // onTerminal —— 终态事件已由 handleRunEvent 处理，此处仅清理
            _sseClient = null;
          }
        );

        _sseClient.start();
      }

      function _stopStreaming() {
        if (_sseClient) {
          _sseClient.close();
          _sseClient = null;
        }
        _stopPolling();
      }

      // --- 回退轮询（SSE 不可用时使用） ---
      function _startPolling(runId) {
        _stopPolling();
        let pollCount = 0;
        const MAX_POLLS = 600;

        async function poll() {
          if (String(_currentRunId || '') !== String(runId)) return;
          if (pollCount >= MAX_POLLS) {
            _setError('运行超时');
            _setRunState('failed');
            _freezeElapsed();
            return;
          }
          pollCount++;
          try {
            var data = await teachMateApi.getRunEvents(runId, _eventCursor);
            if (String(_currentRunId || '') !== String(runId)) return;
            if (data && data.events && data.events.length) {
              for (var i = 0; i < data.events.length; i++) handleRunEvent(data.events[i]);
            }
            if (data && typeof data.next_after === 'number') {
              _eventCursor = data.next_after;
            } else if (data && typeof data.total_events === 'number') {
              _eventCursor = data.total_events;
            }

            var status = data && data.status;
            if (status && TERMINAL_STATUSES.has(status) && status !== _runState) {
              if (status === 'completed') { _setRunState('completed'); _stopPolling(); _freezeElapsed(data.completed_at); _loadFinalResults(); }
              else if (status === 'degraded') { _setRunState('degraded'); _stopPolling(); _freezeElapsed(data.completed_at); _loadFinalResults(); }
              else if (status === 'failed') { _setRunState('failed'); _setError(data.error || '运行失败'); _stopPolling(); _freezeElapsed(data.completed_at); }
              else if (status === 'cancelled') { _setRunState('cancelled'); _stopPolling(); _freezeElapsed(data.completed_at); }
              return;
            }
          } catch (e) {
            console.error('Poll error:', e);
          }
          if (_runState === 'queued' || _runState === 'running' || _runState === 'waiting_confirmation' || _runState === 'submitting') {
            _pollTimer = setTimeout(poll, 1000);
          }
        }
        poll();
      }

      function _stopPolling() { if (_pollTimer) { clearTimeout(_pollTimer); _pollTimer = null; } }

      // --- 取消 ---
      async function cancelCurrentRun() {
        if (!_currentRunId) return;
        try { await teachMateApi.cancelRun(_currentRunId); _setRunState('cancelled'); _stopStreaming(); }
        catch (e) { _setError('取消失败: ' + e.message); }
      }

      // --- 预算确认 ---
      async function confirmCurrentRun() {
        if (!_currentRunId || _runState !== 'waiting_confirmation') return;
        try {
          _clearError();
          const response = await teachMateApi.confirmRun(_currentRunId);
          _currentRun = { ...(_currentRun || {}), status: response.status || 'queued' };
          _setRunState(response.status || 'queued');
          _startStreaming(_currentRunId);
        } catch (e) {
          _setError('确认预算失败: ' + (e.message || e));
        }
      }

      // --- 重试 ---
      async function retryCurrentRun() {
        if (!_currentRunId) return;
        try {
          _clearError(); _setRunState('submitting');
          const response = await teachMateApi.retryRun(_currentRunId);
          _currentRunId = response.new_run_id;
          _currentRun = { id: response.new_run_id, status: response.status || 'queued' };
          _setRunState(response.status || 'queued');
          _startStreaming(response.new_run_id);
        } catch (e) { _setError('重试失败: ' + e.message); _setRunState('failed'); }
      }

      // --- 证据 ---
      function getEvidenceForRun(runId) { return _evidence[runId] || []; }

      // --- 能力与 Provider ---
      function setCapabilities(caps) { _capabilities = caps; _notify(); }
      function setPlugins(payload) {
        _plugins = Array.isArray(payload) ? payload : ((payload && payload.plugins) || []);
        _notify();
      }
      function setSelectedPluginId(pluginId) {
        _selectedPluginId = pluginId == null ? '' : String(pluginId);
        _notify();
      }
      function setPersonalization(payload) {
        var value = payload && typeof payload === 'object' ? payload : {};
        _personalization = {
          user_address: String(value.user_address || '老师'),
          tone: ['rigorous', 'friendly', 'custom'].includes(String(value.tone)) ? String(value.tone) : 'rigorous',
          custom_prompt: String(value.custom_prompt || ''),
        };
        _notify();
      }
      function setProviderInfo(info) { _providerInfo = info; _notify(); }
      function setSavedModels(payload) {
        if (Array.isArray(payload)) {
          _savedModels = payload;
        } else {
          _savedModels = (payload && payload.models) || [];
          _currentModelId = (payload && payload.current_id) || '';
        }
        _notify();
      }
      function setCurrentModelId(id) { _currentModelId = id || ''; _notify(); }
      function setSelectedExamId(id) { _selectedExamId = id == null ? '' : String(id); _examSelectionTouched = true; _notify(); }
      function setSelectedClassName(name) { _selectedClassName = name == null ? '' : String(name); _notify(); }
      function setAvailableExams(exams, termId) {
        _availableExams = Array.isArray(exams) ? exams : [];
        _availableExamsTermId = termId == null ? null : Number(termId);
        _availableExamsLoaded = true;
        _notify();
      }
      function setBindCurrentExam(enabled) { _bindCurrentExam = !!enabled; _notify(); }
      function setContextNames(names) { _contextNames = names; _notify(); }
      function setAnalysisGroup(group) {
        var sameGroup = _analysisGroup && group && String(_analysisGroup.id || '') === String(group.id || '');
        _analysisGroup = group && typeof group === 'object' ? group : null;
        if (!sameGroup) _analysisGroupDetailsExpanded = false;
        _notify();
      }
      function clearAnalysisGroup() {
        _analysisGroup = null;
        _analysisGroupDetailsExpanded = false;
        _notify();
      }
      function toggleAnalysisGroupDetails() {
        _analysisGroupDetailsExpanded = !_analysisGroupDetailsExpanded;
        _notify();
      }

      function setScopePrompt(prompt) {
        _scopePrompt = prompt && typeof prompt === 'object'
          ? Object.assign({}, prompt)
          : (prompt ? { text: String(prompt) } : null);
        _notify();
      }
      function clearScopePrompt() {
        if (_scopePrompt == null) return;
        _scopePrompt = null;
        _notify();
      }

      // --- 搜索词（P1-5: 搜索词持久化到状态，重绘后恢复） ---
      function setSearchTerm(term) { _searchTerm = term || ''; }
      function getSearchTerm() { return _searchTerm; }

      // --- 清理 ---
      function reset() {
        _stopStreaming();
        _stopElapsedTimer();
        _thinkingContent = '';
        _thinkingActive = false;
        _runStartedAt = null;
        _finalElapsedMs = null;
        _sessions = [];
        _currentSessionId = null;
        _messages = [];
        _events = [];
        _eventCursor = 0;
        _evidence = {};
        _runState = 'idle';
        _progressMode = 'light';
        _runCapability = 'general_chat';
        _currentRunId = null;
        _currentRun = null;
        _savedModels = [];
        _currentModelId = '';
        _plugins = [];
        _selectedPluginId = '';
        _personalization = { user_address: '老师', tone: 'rigorous', custom_prompt: '' };
        _selectedExamId = '';
        _selectedClassName = null;
        _examSelectionTouched = false;
        _availableExams = [];
        _availableExamsTermId = null;
        _availableExamsLoaded = false;
        _bindCurrentExam = false;
        _error = null;
        _drafts = {};
        _searchTerm = '';
        _pendingAttachments = [];
        _analysisGroup = null;
        _analysisGroupDetailsExpanded = false;
        _scopePrompt = null;
        _notify();
      }

      return {
        subscribe,
        getSnapshot,
        setSessions,
        setCurrentSession,
        setMessages,
        setCurrentRun,
        appendMessage,
        updateLastAssistantMessage,
        setDraft,
        getDraft,
        clearDraft,
        startSubmitting,
        failSubmitting,
        handleSendResponse,
        handleRunEvent,
        cancelCurrentRun,
        confirmCurrentRun,
        retryCurrentRun,
        getEvidenceForRun,
        setCapabilities,
        setPlugins,
        setSelectedPluginId,
        setPersonalization,
        setProviderInfo,
        setSavedModels,
        setCurrentModelId,
        setSelectedExamId,
        setSelectedClassName,
        setAvailableExams,
        setBindCurrentExam,
        setContextNames,
        setAnalysisGroup,
        clearAnalysisGroup,
        toggleAnalysisGroupDetails,
        setScopePrompt,
        clearScopePrompt,
        setSearchTerm,
        getSearchTerm,
        setError: _setError,
        clearError: _clearError,
        /** P1-12: 将运行状态重置为 idle（用于发送前门禁拒绝时） */
        setIdle: function() { _setRunState('idle'); },
        // U5: 时间线 / 数据就绪 / 预算 / 评价 / 报告
        setTimeline,
        clearTimeline,
        getTimeline,
        setDataReady,
        setBudgetLimit,
        setEvaluations,
        getEvaluationsForRun,
        setReportAnswer,
        addPendingAttachment,
        updatePendingAttachment,
        removePendingAttachment,
        clearPendingAttachments,
        getPendingAttachments,
        togglePinnedSession: _togglePinnedSession,
        setSessionPreferences: _setSessionPreferences,
        createSessionFolder: _createSessionFolder,
        toggleSessionFolder: _toggleSessionFolder,
        moveSessionToFolder: _moveSessionToFolder,
        reset,
        get currentSessionId() { return _currentSessionId; },
        get runState() { return _runState; },
        get error() { return _error; },
        EVENT_TYPES: EVENT_TYPES,
        TERMINAL_STATUSES: TERMINAL_STATUSES,
        TERMINAL_EVENT_TYPES: TERMINAL_EVENT_TYPES,
      };
    })();

    // 暴露到 window 供测试访问
    if (typeof window !== 'undefined') window.teachMateState = teachMateState;

    // 页面可见性变化 —— 页面隐藏时关闭 SSE 连接，页面恢复时重连
    if (typeof document !== 'undefined') {
      document.addEventListener('visibilitychange', function () {
        if (document.hidden) {
          // 页面切到后台 → 关闭 SSE 连接（节省资源）
          if (window.teachMateState && window.teachMateState.runState &&
              !window.teachMateState.TERMINAL_STATUSES.has(window.teachMateState.runState)) {
            // SSEClient.close() 由 _stopStreaming 内部调用
            // 这里通过临时存储 runId，在页面恢复时重连
          }
        }
        // 页面恢复时，SSE 会在下次 _startStreaming 时自动重连
      });
    }
