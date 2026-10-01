    // 使用产品图标作为助手身份标识，避免在欢迎页和长任务状态中出现不一致的二次元头像。
    // 版本参数用于刷新浏览器对旧头像资源的缓存。
    const TEACHMATE_ICON_SRC = './workbench-assets/app-icon-256.png?v=202608241930';

    function _teachMateIconMarkup(className, altText) {
      var safeClass = className || 'tm-brand-icon';
      var size = safeClass.indexOf('welcome') >= 0 ? '78' : safeClass.indexOf('avatar') >= 0 ? '38' : safeClass.indexOf('toolbar') >= 0 ? '30' : safeClass.indexOf('nav') >= 0 ? '22' : safeClass.indexOf('header') >= 0 ? '28' : '18';
      return '<img class="' + safeClass + '" width="' + size + '" height="' + size + '" src="' + TEACHMATE_ICON_SRC + '" alt="' + escapeAttr(altText || 'TeachMate') + '" loading="eager" decoding="async">';
    }

    // ================= TeachMate 视图层 =================
    // 替换旧的 renderTeachMateNav 和 renderTeachMate 空壳函数。
    // 所有视图使用 teachMateState 的真实状态，不含硬编码演示会话。

    function _formatSessionTime(dt) {
      if (!dt) return '';
      let d;
      try {
        const s = String(dt).trim();
        // 后端 created_at 经 SQLite(timezone=True)+utcnow 存储/读出后，序列化为「无时区后缀的 UTC 裸串」
        // (如 2026-08-20T06:30:00，其真实语义是 UTC 06:30 = 北京时间 14:30)。
        // 处理规则：
        //  - 已带时区标记(Z / ±HH:MM)：直接解析，浏览器会正确换算到本地。
        //  - 无时区后缀：强制补 Z 当作 UTC 解析(new Date(s+'Z'))，即可得到真实本地时刻。
        //  注意：切勿再对无时区串做 ±getTimezoneOffset() 的“纠正”——那会把真实时刻反向拨回 8 小时，
        //  正好制造“多 8 小时”的 bug（上一版就是这里算反了）。
        if (/[zZ]$|[+-]\d{2}:?\d{2}$/.test(s)) {
          d = new Date(s);
        } else {
          d = new Date(s + 'Z');
        }
        if (isNaN(d.getTime())) d = new Date(dt);
        const now = new Date();
        const diff = (now - d) / 1000;
        if (diff < 60) return '刚刚';
        if (diff < 3600) return Math.floor(diff / 60) + '分钟前';
        if (diff < 86400) return Math.floor(diff / 3600) + '小时前';
        if (diff < 604800) return Math.floor(diff / 86400) + '天前';
        return (d.getMonth() + 1) + '月' + d.getDate() + '日';
      } catch (e) { return ''; }
    }

    // --- 覆盖旧函数：renderTeachMateNav ---
    function _renderTeachMateSearchInput() {
      const snapshot = teachMateState.getSnapshot();
      const searchTerm = snapshot.searchTerm || '';
      return '<div class="tm-session-search"><span class="material-symbols-rounded">search</span><input data-act="tm-session-search" placeholder="搜索对话" aria-label="搜索对话" value="' + escapeAttr(searchTerm) + '"></div>';
    }

    function _renderTeachMateSearchPanel() {
      var searchTerm = teachMateState.getSnapshot().searchTerm || '';
      var open = (typeof tmSearchPanelOpen !== 'undefined' && tmSearchPanelOpen) || !!searchTerm;
      return '<div id="tmSearchPanel" class="tm-search-panel"' + (open ? '' : ' hidden') + '>' + (open ? _renderTeachMateSearchInput() : '') + '</div>';
    }

    // 全局：单个会话条目（供 renderTeachMateNav 与搜索刷新共用，避免重建时重复定义）
    function renderTeachMateSessionItem(s) {
      var snapshot = teachMateState.getSnapshot();
      var pinnedIds = (snapshot.pinnedSessionIds || []).map(String);
      var folders = snapshot.sessionFolders || [];
      var isActive = s.id === snapshot.currentSessionId;
      var preview = s.summary && s.summary !== s.title ? s.summary : '';
      var time = _formatSessionTime(s.updated_at || s.created_at);
      var isPinned = pinnedIds.indexOf(String(s.id)) >= 0;
      var inFolder = folders.find(function (f) { return (f.sessionIds || []).map(String).indexOf(String(s.id)) >= 0; });
      var menuOpen = (typeof tmConvMenuSessionId !== 'undefined') && tmConvMenuSessionId === String(s.id);
      var popover = '';
      if (menuOpen) {
        var folderItems = '<button class="tm-conv-menu-item' + (inFolder ? '' : ' is-current') + '" data-act="tm-move-to-folder" data-id="' + s.id + '" data-folder-id="" role="menuitem"><span class="material-symbols-rounded">drive_file_move_outline</span>不移动</button>' +
          folders.map(function (f) {
            var inThis = (f.sessionIds || []).map(String).indexOf(String(s.id)) >= 0;
            return '<button class="tm-conv-menu-item' + (inThis ? ' is-current' : '') + '" data-act="tm-move-to-folder" data-id="' + s.id + '" data-folder-id="' + escapeAttr(f.id) + '" role="menuitem"><span class="material-symbols-rounded">folder</span>' + escapeHtml(f.name) + (inThis ? '<em>当前</em>' : '') + '</button>';
          }).join('');
        popover = '<div class="tm-conv-popover" role="menu" aria-label="对话操作">' +
          '<div class="tm-conv-popover-label">移动到文件夹</div>' + folderItems +
          '<button class="tm-conv-menu-item" data-act="tm-move-new-folder" data-id="' + s.id + '" role="menuitem"><span class="material-symbols-rounded">create_new_folder</span>新建文件夹并移入</button>' +
          '<div class="tm-conv-popover-divider"></div>' +
          '<button class="tm-conv-menu-item" data-act="tm-rename-session" data-id="' + s.id + '" role="menuitem"><span class="material-symbols-rounded">edit</span>重命名</button>' +
          '<button class="tm-conv-menu-item tm-conv-menu-danger" data-act="tm-delete-session" data-id="' + s.id + '" role="menuitem"><span class="material-symbols-rounded">delete</span>删除对话</button>' +
          '</div>';
      }
      var itemHtml = '<div class="nav-item tm-conv-item' + (isActive ? ' active' : '') + '" data-act="tm-conversation-select" data-id="' + s.id + '">' +
        '<span class="nav-icon material-symbols-rounded tm-conv-icon">chat_bubble</span>' +
        '<div class="tm-conv-info"><div class="tm-conv-title">' + escapeHtml(s.title || '新对话') + '</div><div class="tm-conv-preview">' + escapeHtml(preview) + '</div></div>' +
        '<div class="tm-conv-time">' + escapeHtml(time) + '</div>' +
        '<div class="tm-conv-actions" role="group" aria-label="对话操作">' +
        '<button class="tm-conv-more" data-act="tm-conv-more" data-id="' + s.id + '" title="更多操作" aria-label="更多操作" aria-expanded="' + String(menuOpen) + '"><span class="material-symbols-rounded">more_horiz</span></button>' +
        '<button data-act="tm-archive-session" data-id="' + s.id + '" title="归档" aria-label="归档对话"><span class="material-symbols-rounded">archive</span></button>' +
        '<button data-act="tm-pin-session" data-id="' + s.id + '" title="' + (isPinned ? '取消置顶' : '置顶') + '" aria-label="' + (isPinned ? '取消置顶对话' : '置顶对话') + '"><span class="material-symbols-rounded">' + (isPinned ? 'keep_off' : 'push_pin') + '</span></button>' +
        '</div></div>';
      return '<div class="tm-conv-wrap' + (menuOpen ? ' is-menu-open' : '') + '">' + itemHtml + popover + '</div>';
    }

    function renderTeachMateFolderSection(folder) {
      var snapshot = teachMateState.getSnapshot();
      var sessions = (snapshot.sessions || []).slice();
      var folderIds = (folder.sessionIds || []).map(String);
      var children = sessions.filter(function (s) { return folderIds.indexOf(String(s.id)) >= 0; });
      return '<section class="tm-folder' + (folder.collapsed ? ' is-collapsed' : '') + '"><button class="tm-folder-head" data-act="tm-folder-toggle" data-folder-id="' + escapeAttr(folder.id) + '" aria-expanded="' + (!folder.collapsed) + '"><span class="material-symbols-rounded">folder</span><span class="tm-folder-name">' + escapeHtml(folder.name) + '</span><span class="tm-folder-count">' + children.length + '</span><span class="material-symbols-rounded tm-folder-chevron">expand_more</span></button>' + (folder.collapsed ? '' : (children.length ? children.map(renderTeachMateSessionItem).join('') : '<div class="tm-folder-empty">暂无对话</div>')) + '</section>';
    }

    // 全局：历史对话列表区。搜索刷新时只重建此 section，不触碰搜索框，避免输入焦点丢失。
    function renderTeachMateHistorySection() {
      var snapshot = teachMateState.getSnapshot();
      var sessions = (snapshot.sessions || []).slice();
      var searchTerm = snapshot.searchTerm || '';
      var pinnedIds = (snapshot.pinnedSessionIds || []).map(String);
      var pinned = sessions.filter(function (s) { return pinnedIds.indexOf(String(s.id)) >= 0; });
      var regular = sessions.filter(function (s) { return pinnedIds.indexOf(String(s.id)) < 0; });
      var inner = (pinned.length ? '<div class="tm-side-subhead">置顶</div>' + pinned.map(renderTeachMateSessionItem).join('') : '') +
        (regular.length ? regular.map(renderTeachMateSessionItem).join('') : (!pinned.length ? '<div class="tm-nav-empty">' + (searchTerm ? '未找到匹配的对话' : '暂无对话') + '</div>' : ''));
      return '<section class="tm-side-history" aria-label="历史对话"><div class="tm-side-section-head"><span>历史对话</span></div>' + inner + '</section>';
    }

    function renderTeachMateFilesSection() {
      var folders = teachMateState.getSnapshot().sessionFolders || [];
      return '<section class="tm-side-files" aria-label="文件"><div class="tm-side-section-head"><span>文件</span></div>' +
        '<button class="nav-item tm-side-action" data-act="tm-library"><span class="nav-icon material-symbols-rounded">folder_open</span><span>资料库</span></button>' +
        '<button class="nav-item tm-side-action" data-act="tm-folder-create"><span class="nav-icon material-symbols-rounded">create_new_folder</span><span>新建文件夹</span></button>' +
        (folders.length ? '<div class="tm-side-subhead tm-side-folder-label">文件夹</div>' + folders.map(renderTeachMateFolderSection).join('') : '') +
        '</section>';
    }

    function renderTeachMateNav() {
      const snapshot = teachMateState.getSnapshot();
      const sessions = (snapshot.sessions || []).slice();
      const searchTerm = snapshot.searchTerm || '';
      const pinnedIds = (snapshot.pinnedSessionIds || []).map(String);
      // 用户卡：根据 workbench 教师信息动态生成；空时回落"教师"
      const _teacherName = String((typeof state !== 'undefined' && state.teacher && state.teacher.name) || '').trim();
      const _displayName = _teacherName || '教师';
      const _avatarChar = _displayName.charAt(0).toUpperCase();
      return '<div class="tm-side-shell"><button class="nav-item tm-new-chat" data-act="tm-new-chat"><span class="nav-icon material-symbols-rounded">add</span><span>新建对话</span></button>' +
        _renderTeachMateSearchPanel() +
        '<section class="tm-side-primary" aria-label="功能"><div class="tm-side-section-head"><span>功能</span></div>' +
        '<button class="nav-item tm-side-action" data-act="tm-toggle-search"><span class="nav-icon material-symbols-rounded">search</span><span>查找对话</span></button>' +
        '<button class="nav-item tm-side-action" data-act="tm-plugin-placeholder"><span class="nav-icon material-symbols-rounded">extension</span><span>插件</span></button>' +
        '</section>' + renderTeachMateFilesSection() +
        renderTeachMateHistorySection() +
        '<button class="tm-side-user" data-act="tm-open-agent-settings" aria-label="打开 Agent 设置"><span class="tm-side-avatar">' + escapeHtml(_avatarChar) + '</span><span class="tm-side-user-copy"><strong>' + escapeHtml(_displayName) + '</strong></span><span class="material-symbols-rounded">chevron_right</span></button></div>';
    }

    // --- 覆盖旧函数：renderTeachMate ---
    /** B3-18: 与 header 标题同构的移动端工具栏文案 */
    function _tmHeaderTitle() {
      var el = document.getElementById('appTitle');
      if (el && el.textContent) return el.textContent;
      var t = String(state.teacher && state.teacher.name || '').trim();
      var s = String(state.teacher && state.teacher.subject || '').trim();
      return (t ? t + ' · ' : '') + (s ? s + '教师助手' : subjectBaseTitle() + '教师助手');
    }
    function _tmHeaderSubtitle() { return 'AI 教学助手'; }

    function _renderBatchTaskCard(group, snapshot) {
      if (!group || typeof group !== 'object') return '';
      var tasks = Array.isArray(group.tasks) ? group.tasks : [];
      var counts = { completed: 0, running: 0, queued: 0, failed: 0, cancelled: 0, other: 0 };
      tasks.forEach(function (task) {
        var status = String(task && task.status || 'queued');
        if (status === 'degraded') status = 'completed';
        if (Object.prototype.hasOwnProperty.call(counts, status)) counts[status] += 1;
        else counts.other += 1;
      });
      var total = Number(group.requested_student_count || tasks.length || 0);
      var finished = counts.completed + counts.failed + counts.cancelled;
      var unfinished = counts.running + counts.queued + counts.other;
      var progress = total ? Math.min(100, Math.round((finished / total) * 100)) : 0;
      var status = String(group.status || 'queued');
      var isPlan = status === 'waiting_confirmation';
      var statusLabel = {
        queued: '排队中', running: '进行中', waiting_confirmation: '等待确认',
        completed: '已完成', partially_completed: '部分完成', failed: '未完成', cancelled: '已停止',
      }[status] || '处理中';
      var statusClass = ['completed'].includes(status) ? 'is-done'
        : ['failed', 'cancelled'].includes(status) ? 'is-error'
          : status === 'waiting_confirmation' ? 'is-waiting' : 'is-running';
      var scope = group.scope_snapshot_json || {
        class_id: group.class_id,
        exam_id: group.exam_id,
      };
      var contextNames = (snapshot && snapshot.contextNames) || {};
      var className = contextNames.class && contextNames.class[scope.class_id];
      var examName = contextNames.exam && contextNames.exam[scope.exam_id];
      var scopeText = [className || (scope.class_id ? '班级 #' + scope.class_id : ''), examName || (scope.exam_id ? '考试 #' + scope.exam_id : '')].filter(Boolean).join(' · ');
      var studentName = function (task) {
        var id = task && Array.isArray(task.student_ids) && task.student_ids.length ? task.student_ids[0] : null;
        return (id != null && contextNames.student && contextNames.student[id]) || (id != null ? '学生 #' + id : (task.task_role === 'exam_agent' ? '考试整体分析' : '分析任务'));
      };
      var collapsedLimit = isPlan ? 8 : 5;
      var visibleTasks = snapshot && snapshot.analysisGroupDetailsExpanded ? tasks : tasks.slice(0, collapsedLimit);
      var taskRows = visibleTasks.map(function (task) {
        var taskStatus = String(task.status || 'queued');
        var taskLabel = isPlan && taskStatus === 'queued' ? '待执行' : ({ completed: '完成', degraded: '完成', running: '分析中', queued: '排队中', failed: Number(task.retry_count || 0) > 0 ? '失败（已自动重试）' : '失败', cancelled: '已停止' }[taskStatus] || '等待中');
        var taskClass = ['completed', 'degraded'].includes(taskStatus) ? 'is-done' : ['failed', 'cancelled'].includes(taskStatus) ? 'is-error' : taskStatus === 'running' ? 'is-running' : 'is-queued';
        return '<div class="tm-batch-task-row"><span class="tm-batch-task-dot ' + taskClass + '" aria-hidden="true"></span><span class="tm-batch-task-name">' + escapeHtml(studentName(task)) + '</span><span class="tm-batch-task-status ' + taskClass + '">' + escapeHtml(taskLabel) + '</span></div>';
      }).join('');
      var more = tasks.length > visibleTasks.length ? '<button type="button" class="tm-batch-more" data-act="tm-batch-toggle" aria-expanded="false">查看全部 ' + tasks.length + ' 名学生<span class="material-symbols-rounded" aria-hidden="true">expand_more</span></button>' : (tasks.length > collapsedLimit ? '<button type="button" class="tm-batch-more" data-act="tm-batch-toggle" aria-expanded="true">收起名单<span class="material-symbols-rounded" aria-hidden="true">expand_less</span></button>' : '');
      var retryableStatuses = ['failed', 'cancelled', 'queued', 'running', 'waiting_confirmation'];
      var retryableCount = tasks.filter(function (task) {
        return task && task.task_role !== 'exam_agent' && retryableStatuses.indexOf(String(task.status || '')) >= 0 && Array.isArray(task.student_ids) && task.student_ids.length;
      }).length;
      var action = ['queued', 'running'].includes(status)
        ? '<button type="button" class="tm-batch-action tm-batch-action-danger" data-act="tm-batch-cancel" data-group-id="' + escapeAttr(String(group.id || '')) + '">停止任务</button>'
        : '';
      if (!isPlan && retryableCount && ['completed', 'partially_completed', 'failed', 'cancelled'].indexOf(status) >= 0) {
        action += '<button type="button" class="tm-batch-action tm-batch-action-retry" data-act="tm-batch-retry" data-group-id="' + escapeAttr(String(group.id || '')) + '">重新生成未完成学生（' + retryableCount + '）</button>';
      }
      var overviewHtml = '<div class="tm-batch-overview" role="status" aria-label="批量诊断结果概览">' +
        '<span class="tm-batch-overview-item is-done"><strong>' + counts.completed + '</strong> 已完成</span>' +
        '<span class="tm-batch-overview-item is-pending"><strong>' + unfinished + '</strong> 未完成</span>' +
        '<span class="tm-batch-overview-item is-error"><strong>' + counts.failed + '</strong> 失败</span>' +
        (counts.cancelled ? '<span class="tm-batch-overview-item is-cancelled"><strong>' + counts.cancelled + '</strong> 已取消</span>' : '') +
        '</div>';
      var summaryHtml = isPlan
        ? '<div class="tm-batch-summary tm-batch-plan-summary"><strong>' + total + '</strong><span>名学生</span><span class="tm-batch-summary-divider"></span><span>确认后并行 ' + Number(group.max_concurrency || 1) + ' 路执行</span></div>'
        : '<div class="tm-batch-summary"><strong>' + finished + '<small>/' + total + '</small></strong><span>已处理学生</span><span class="tm-batch-summary-divider"></span><span>并行 ' + Number(group.max_concurrency || 1) + ' 路</span></div>' + overviewHtml + '<div class="tm-batch-progress" role="progressbar" aria-valuenow="' + progress + '" aria-valuemin="0" aria-valuemax="100" aria-label="批量画像进度"><span style="width:' + progress + '%"></span></div><div class="tm-batch-progress-meta"><span>' + progress + '% 完成</span><span>' + counts.running + ' 项进行中 · ' + counts.queued + ' 项排队</span></div>';
      return '<section class="tm-batch-card' + (isPlan ? ' is-plan' : '') + '" data-group-id="' + escapeAttr(String(group.id || '')) + '" aria-label="' + (isPlan ? '学生诊断执行方案' : '批量画像任务') + '">' +
        '<div class="tm-batch-head"><div class="tm-batch-title-wrap"><span class="tm-batch-icon material-symbols-rounded" aria-hidden="true">groups</span><div><div class="tm-batch-kicker">' + (isPlan ? '学生诊断执行方案' : '批量画像任务') + '</div><h3>' + escapeHtml(scopeText || '当前班级范围') + '</h3></div></div><span class="tm-batch-status ' + statusClass + '">' + escapeHtml(statusLabel) + '</span></div>' + summaryHtml +
        '<div class="tm-batch-task-list">' + (taskRows || '<div class="tm-batch-empty">任务已创建，等待运行状态更新</div>') + '</div>' + more +
        '<div class="tm-batch-foot"><span>' + (group.estimated_tokens != null ? '预计约 ' + Number(group.estimated_tokens).toLocaleString('zh-CN') + ' tokens' : 'Token 用量将在执行后统计') + '</span><div class="tm-batch-actions">' + action + '</div></div>' +
        '</section>';
    }

    function _renderScopePrompt(prompt) {
      if (!prompt || !prompt.text) return '';
      var kind = String(prompt.kind || 'individual');
      var title = kind === 'unresolved' ? '需要确认学生' : '学生诊断范围';
      var reference = prompt.studentName ? '<span class="tm-scope-prompt-reference">' + escapeHtml(prompt.studentName) + '</span>' : '';
      return '<div class="tm-scope-prompt tm-message tm-message-ai" role="status" aria-live="polite">' +
        '<div class="tm-message-avatar">' + _teachMateIconMarkup('tm-brand-icon tm-brand-icon-avatar', 'TeachMate') + '</div>' +
        '<div class="tm-message-body"><div class="tm-scope-prompt-title">' + escapeHtml(title) + reference + '</div>' +
        '<div class="tm-message-content">' + escapeHtml(prompt.text) + '</div></div></div>';
    }

    function renderTeachMate() {
      _ensureProcDelegation();
      const snapshot = teachMateState.getSnapshot();
      const hasSession = !!snapshot.currentSessionId;
      const hasMessages = Array.isArray(snapshot.messages) && snapshot.messages.length > 0;
      const scopePromptHtml = _renderScopePrompt(snapshot.scopePrompt);
      // 新建会话虽然已经有后端 ID，但在教师发送第一条消息前仍应保持欢迎页。
      // 运行或等待确认时切回对话态，避免把任务进度藏在欢迎内容里。
      const showWelcome = !snapshot.scopePrompt && (!hasSession || (!hasMessages && !snapshot.isRunning && snapshot.runState !== 'waiting_confirmation'));
      const messagesHtml = showWelcome ? _renderWelcomePage() : _renderMessages(snapshot);
      const runningHtml = snapshot.isRunning && snapshot.runState !== 'waiting_confirmation' ? _renderRunningIndicator(snapshot) : '';
      const confirmationHtml = snapshot.runState === 'waiting_confirmation' ? _renderBudgetConfirmation(snapshot) : '';
      const errorHtml = snapshot.error ? _renderErrorBar(snapshot.error) : '';
      // 运行中时间线已内嵌在聊天进度卡；终态保留完整时间线供回看。
      const timelineHtml = hasSession && snapshot.timeline && snapshot.timeline.length && !snapshot.isRunning ? _renderTimeline(snapshot) : '';
      const batchHtml = snapshot.analysisGroup ? _renderBatchTaskCard(snapshot.analysisGroup, snapshot) : '';
      const inputHtml = _renderInputArea(snapshot);
      // 过程块行节点是 keyed 缓存（_procRows），rAF 里增量填充/更新，
      // 避免整片 innerHTML 重绘把扫光动画打断重启。
      _scheduleProcMount();
      const layoutClass = showWelcome ? ' tm-layout-welcome' : ' tm-layout-active';

      return '<div class="tm-layout' + layoutClass + '">' +
        '<div class="tm-center">' +
        '<div class="tm-mobile-toolbar"><div><strong>' + escapeHtml(_tmHeaderTitle()) + '</strong><span>' + escapeHtml(_tmHeaderSubtitle()) + '</span></div></div>' +
        '<div class="tm-messages" id="tmMessages" data-session-id="' + escapeAttr(String(snapshot.currentSessionId || '')) + '" role="log" aria-live="polite" aria-atomic="false" aria-label="对话消息">' + messagesHtml + scopePromptHtml + batchHtml + runningHtml + confirmationHtml + timelineHtml + '<div id="tmReportMount"></div></div>' +
        errorHtml + inputHtml + '</div>' + '</div>';
    }

    // --- 过程消息（Harness 风格）：keyed 行缓存 + 原地更新 ---
    // 参考 deepseek-harness ui-chat 的 TurnProcessNodeView / GenericCommandCard /
    // ReasoningRow：整 Turn 一个折叠控件，行身份稳定（data-step-id），事件增量
    // 到达时只改变化的行，扫光动画不随整片 innerHTML 重绘而重启。
    var _procRows = new Map();   // runKey:stepId -> { el, sig }
    var _procRunKey = '';
    var _procOpen = null;        // null = 未手动切换，跟随运行态默认（运行中展开，终态折叠）
    var _procBound = false;
    var _procMountScheduled = false;

    function _procIconFor(it) {
      if (it.kind === 'error') return 'error';
      if (it.kind === 'validate') return 'rule';
      if (it.kind === 'plan') return 'flag';
      if (it.kind === 'tool') return 'handyman';
      return 'psychology';
    }

    function _fmtChars(n) {
      var v = Number(n);
      if (!isFinite(v) || v <= 0) return '';
      if (v < 1000) return v + ' 字';
      return (Math.round(v / 100) / 10).toFixed(1) + 'k 字';
    }

    // 折叠态摘要（GenericCommandCard/ReasoningRow 的 summary 语义）：
    // running 显示"当前正在做什么"，done 显示"做完了什么"。
    function _procSummaryOf(it) {
      var running = it.state === 'running';
      if (it.kind === 'error') return it.detail || '需要修正后重试';
      if (it.role === 'thinking') {
        if (running) return it.detail || '正在整理分析思路';
        var chars = _fmtChars(it.thinkingChars);
        return chars ? '思考完成 · ' + chars : (it.detail || '思考完成');
      }
      if (running) return it.detail || it.label || '';
      return it.detail || '已完成';
    }

    function _procSig(it) {
      return [it.state, it.text || '', it.detail || '', it.nextAction || '', it.thinkingChars || 0].join('|');
    }

    // 行身份与 mount 的 keyed 缓存共用同一规则；stepId 缺失时退化为序号。
    function _procRowId(it, i) {
      return String((it && (it.stepId || it.ts)) || ('idx' + i));
    }

    // 行内部结构：静态首绘（_procRowHtml）与增量构建（_buildProcRow）共用，
    // 保证两条路径产出的 DOM 可互换收编。
    function _procRowInner(it) {
      return '<span class="tm-proc-ico material-symbols-rounded" aria-hidden="true">' + _procIconFor(it) + '</span>' +
        '<span class="tm-proc-title"></span>' +
        '<span class="tm-proc-sep" aria-hidden="true"></span>' +
        '<span class="tm-proc-summary"></span>' +
        '<span class="tm-proc-tail"></span>';
    }

    function _buildProcRow(it) {
      var el = document.createElement('div');
      el.className = 'tm-proc-row';
      el.setAttribute('role', 'listitem');
      el.innerHTML = _procRowInner(it);
      _syncProcRow(el, it);
      return el;
    }

    function _procRowHtml(it) {
      // 与 _syncProcRow 输出保持同一状态字段与文本，静态首绘即所见即所得。
      var running = it.state === 'running';
      var stateAttr = it.kind === 'error' ? 'error' : (running ? 'running' : 'done');
      var mode = running ? 'dots' : (it.state === 'done' ? 'check' : 'none');
      var tail = mode === 'dots'
        ? '<span class="tm-proc-dots" aria-label="进行中"><i></i><i></i><i></i></span>'
        : (mode === 'check' ? '<span class="material-symbols-rounded tm-proc-check" aria-label="完成">check</span>' : '');
      return '<div class="tm-proc-row' + (it.role === 'thinking' ? ' is-thinking' : '') + '"' +
          ' role="listitem" data-state="' + stateAttr + '">' +
        '<span class="tm-proc-ico material-symbols-rounded" aria-hidden="true">' + _procIconFor(it) + '</span>' +
        '<span class="tm-proc-title">' + escapeHtml(it.text || it.label || '') + '</span>' +
        '<span class="tm-proc-sep" aria-hidden="true"></span>' +
        '<span class="tm-proc-summary">' + escapeHtml(_procSummaryOf(it)) + '</span>' +
        '<span class="tm-proc-tail" data-mode="' + mode + '">' + tail + '</span>' +
      '</div>';
    }

    function _syncProcRow(el, it) {
      var running = it.state === 'running';
      var prev = el.getAttribute('data-state');
      el.setAttribute('data-state', it.kind === 'error' ? 'error' : (running ? 'running' : 'done'));
      el.classList.toggle('is-thinking', it.role === 'thinking');
      var titleEl = el.querySelector('.tm-proc-title');
      var title = it.text || it.label || '';
      if (titleEl.textContent !== title) titleEl.textContent = title;
      var sumEl = el.querySelector('.tm-proc-summary');
      var summary = _procSummaryOf(it);
      if (sumEl.textContent !== summary) sumEl.textContent = summary;
      var tail = el.querySelector('.tm-proc-tail');
      var mode = running ? 'dots' : (it.state === 'done' ? 'check' : 'none');
      if (tail.getAttribute('data-mode') !== mode) {
        tail.setAttribute('data-mode', mode);
        tail.innerHTML = mode === 'dots'
          ? '<span class="tm-proc-dots" aria-label="进行中"><i></i><i></i><i></i></span>'
          : (mode === 'check' ? '<span class="material-symbols-rounded tm-proc-check" aria-label="完成">check</span>' : '');
      }
      // running → done 的瞬间给一次绿色余晖，避免硬切
      if (prev === 'running' && it.state === 'done') {
        el.classList.add('is-just-done');
        setTimeout(function () { el.classList.remove('is-just-done'); }, 600);
      }
    }

    function _procMetaHtml(timeline) {
      var tools = 0, thinks = 0, live = 0;
      for (var i = 0; i < timeline.length; i++) {
        var it = timeline[i] || {};
        if (it.state === 'running') live++;
        if (it.state === 'done' && it.kind === 'tool') tools++;
        else if (it.state === 'done' && (it.role === 'thinking' || it.kind === 'model')) thinks++;
      }
      var parts = [];
      if (tools) parts.push('<span><b>' + tools + '</b> 个工具</span>');
      if (thinks) parts.push('<span><b>' + thinks + '</b> 次思考</span>');
      if (live) parts.push('<span><b>' + live + '</b> 进行中</span>');
      return parts.join('<i></i>');
    }

    function _processBlockHtml(timeline, running, runId) {
      if (!Array.isArray(timeline) || !timeline.length) return '';
      var open = _procOpen !== null ? _procOpen : !!running;
      // 首绘静态生成行（与 _mountProcBlock 的 keyed 增量共用同一行结构），
      // 后续重绘由 mount 收编为缓存行做原地更新，动画跨重绘不重启。
      var rowsHtml = '';
      for (var i = 0; i < timeline.length; i++) rowsHtml += _procRowHtml(timeline[i] || {});
      return '<div class="tm-proc" id="tmProcessBlock" data-run-id="' + escapeAttr(String(runId || '')) + '" data-open="' + open + '">' +
        '<button type="button" class="tm-proc-toggle" data-proc-toggle aria-expanded="' + open + '">' +
          '<span class="tm-proc-toggle-label">过程消息</span>' +
          '<span class="tm-proc-meta" id="tmProcMeta">' + _procMetaHtml(timeline) + '</span>' +
          '<span class="material-symbols-rounded tm-proc-chev" aria-hidden="true">expand_more</span>' +
        '</button>' +
        '<div class="tm-proc-list" id="tmProcessList" role="list" aria-label="分析过程"' + (open ? '' : ' hidden') + '>' + rowsHtml + '</div>' +
      '</div>';
    }

    // 行 DOM 由 mount 阶段填充：renderTeachMate 是字符串拼接 + 全量 innerHTML，
    // 行节点缓存在这里跨重绘复用，只有 sig 变化的行才原地更新。
    function _scheduleProcMount() {
      if (_procMountScheduled) return;
      _procMountScheduled = true;
      requestAnimationFrame(function () {
        _procMountScheduled = false;
        _mountProcBlock();
      });
    }

    function _mountProcBlock() {
      var list = document.getElementById('tmProcessList');
      if (!list) return;
      var block = document.getElementById('tmProcessBlock');
      var runKey = block ? String(block.getAttribute('data-run-id') || '') : '';
      if (runKey !== _procRunKey) { _procRows.clear(); _procRunKey = runKey; }
      var snapshot = teachMateState.getSnapshot();
      var timeline = Array.isArray(snapshot.timeline) ? snapshot.timeline : [];
      var seen = {};
      for (var i = 0; i < timeline.length; i++) {
        var it = timeline[i] || {};
        var sid = _procRowId(it, i);
        var key = runKey + ':' + sid;
        seen[key] = true;
        var entry = _procRows.get(key);
        if (!entry) {
          // 静态首绘的行按位置收编进 keyed 缓存，补写行身份；
          // 找不到对应节点才现场构建。
          var el = null;
          var child = list.children[i];
          if (child && child.classList.contains('tm-proc-row') && !child.getAttribute('data-step-id')) {
            el = child;
            el.setAttribute('data-step-id', sid);
          } else {
            for (var ci = 0; ci < list.children.length; ci++) {
              if (list.children[ci].getAttribute('data-step-id') === sid) { el = list.children[ci]; break; }
            }
            if (!el) { el = _buildProcRow(it); el.setAttribute('data-step-id', sid); list.appendChild(el); }
          }
          _syncProcRow(el, it);
          _procRows.set(key, { el: el, sig: _procSig(it) });
        } else {
          if (entry.sig !== _procSig(it)) {
            _syncProcRow(entry.el, it);
            entry.sig = _procSig(it);
          }
          list.appendChild(entry.el); // 已在容器内则是移动，保证与 timeline 顺序一致
        }
      }
      _procRows.forEach(function (row, key) {
        if (!seen[key]) { row.el.remove(); _procRows.delete(key); }
      });
      var meta = document.getElementById('tmProcMeta');
      if (meta) meta.innerHTML = _procMetaHtml(timeline);
    }

    function _ensureProcDelegation() {
      if (_procBound) return;
      _procBound = true;
      document.addEventListener('click', function (ev) {
        var btn = ev.target && ev.target.closest ? ev.target.closest('[data-proc-toggle]') : null;
        if (!btn) return;
        var block = btn.closest('.tm-proc');
        if (!block) return;
        var open = block.getAttribute('data-open') === 'true';
        _procOpen = !open;
        block.setAttribute('data-open', String(!open));
        btn.setAttribute('aria-expanded', String(!open));
        var list = block.querySelector('.tm-proc-list');
        if (list) {
          if (_procOpen) list.removeAttribute('hidden');
          else list.setAttribute('hidden', '');
        }
      });
    }

    // --- U5-03: 运行时间线（读取数据、调用工具、生成报告、校验、完成） ---
    // 终态回看与运行中共用同一套过程块；行内容由 _mountProcBlock 增量填充。
    function _renderTimeline(snapshot) {
      return '<div class="tm-timeline" data-testid="run-timeline">' +
        _processBlockHtml(snapshot.timeline, false, snapshot.currentRunId) +
        '</div>';
    }

    function _renderRightPanel(snapshot) {
      var isRunning = !!snapshot.isRunning;
      var isWaiting = snapshot.runState === 'waiting_confirmation';
      var isFailed = ['failed', 'cancelled', 'degraded'].indexOf(snapshot.runState) >= 0;
      var answer = snapshot.reportAnswer;
      if (!answer && Array.isArray(snapshot.messages)) {
        for (var i = snapshot.messages.length - 1; i >= 0; i--) {
          var message = snapshot.messages[i];
          if (!message || message.role !== 'assistant' || !message.structured_answer) continue;
          var parsed = message.structured_answer;
          if (typeof parsed === 'string') {
            try { parsed = JSON.parse(parsed); } catch (e) { parsed = null; }
          }
          if (parsed && typeof parsed === 'object') { answer = parsed; break; }
        }
      }

      var session = (snapshot.sessions || []).find(function (item) { return item.id === snapshot.currentSessionId; });
      var contextNames = snapshot.contextNames || {};
      var className = '';
      var examName = '';
      if (session && session.class_id) className = (contextNames.class && contextNames.class[session.class_id]) || ('班级 #' + session.class_id);
      if (session && session.exam_id) {
        examName = (contextNames.exam && contextNames.exam[session.exam_id]) || '';
        if (!examName) {
          var exam = (state.exams || []).find(function (item) { return String(item.id) === String(session.exam_id); });
          if (exam) examName = exam.name;
        }
        examName = examName || ('考试 #' + session.exam_id);
      }
      var contextLabel = [className, examName].filter(Boolean).join(' · ') || '当前教学范围';
      var title = isRunning ? '正在分析' : (isWaiting ? '准备继续分析' : (isFailed ? '任务未完成' : (answer && answer.answer_type === 'review_plan' ? '教学包草稿' : '分析结果')));
      var status = isRunning ? '进行中' : (isWaiting ? '等待确认' : (isFailed ? '需要处理' : (answer ? '已完成' : '等待结果')));
      var statusClass = isRunning ? 'tm-right-status-running' : (isFailed ? 'tm-right-status-error' : 'tm-right-status-done');
      var body = '';

      if (isRunning || isWaiting) {
        body = _renderRightProgress(snapshot, isWaiting);
      } else if (isFailed) {
        body = '<div class="tm-right-empty tm-right-empty-error"><span class="material-symbols-rounded">error_outline</span><strong>这次任务没有完成</strong><p>' + escapeHtml(snapshot.error || '可以在左侧查看错误信息并重试。') + '</p>' + (snapshot.canRetry ? '<button class="tm-right-retry" data-act="tm-retry">重新运行</button>' : '') + '</div>';
      } else if (answer && typeof teachMateReport !== 'undefined') {
        body = '<div class="tm-right-summary"><div class="tm-right-summary-kicker">已生成成果</div><strong>' + (answer.answer_type === 'review_plan' ? '复习安排和课堂材料已整理成草稿' : '这份报告可以继续转成教学行动') + '</strong><span>核对证据与数据局限，调整后再用于课堂。</span></div>' +
          _renderRightDataQuality(snapshot) +
          '<div class="tm-right-report-tabs" role="tablist" aria-label="结果内容"><button type="button" class="tm-right-tab is-active" data-act="tm-right-tab" data-tab="report" aria-selected="true">' + (answer.answer_type === 'review_plan' ? '教学计划与材料' : '报告') + '</button><button type="button" class="tm-right-tab" data-act="tm-right-tab" data-tab="evidence" aria-selected="false">证据' + (snapshot.currentEvidence && snapshot.currentEvidence.length ? ' (' + snapshot.currentEvidence.length + ')' : '') + '</button></div>' +
          '<div class="tm-right-tab-panel" data-right-tab-panel="report">' + teachMateReport.renderReportCanvas(answer, { hideEvidence: true, runId: snapshot.currentRunId }) + _renderRightNextActions(snapshot) + '</div>' +
          '<div class="tm-right-tab-panel" data-right-tab-panel="evidence" hidden>' + _renderEvidenceList(snapshot) + '</div>' +
          '<div class="tm-right-tab-panel" data-right-tab-panel="quality" hidden>' + _renderRightQualityDetail(snapshot) + '</div>';
      } else {
        body = '<div class="tm-right-empty"><span class="material-symbols-rounded">auto_awesome</span><strong>分析结果会显示在这里</strong><p>任务完成后，完整报告、证据和下一步操作会集中出现在右侧。</p></div>';
      }

      return '<aside class="tm-right-panel" id="tmRightPanel" aria-label="TeachMate 成果工作区" data-testid="tm-right-panel">' +
        '<div class="tm-right-header"><div><span class="tm-right-eyebrow">TEACHMATE WORKSPACE</span><strong>' + escapeHtml(title) + '</strong><small>' + escapeHtml(contextLabel) + '</small></div><div class="tm-right-header-meta"><span class="tm-right-status ' + statusClass + '">' + escapeHtml(status) + '</span><button type="button" class="tm-right-close" data-act="tm-drawer-close" aria-label="关闭成果区"><span class="material-symbols-rounded">close</span></button></div></div>' +
        '<div class="tm-right-scroll">' + body + '</div></aside>';
    }

    function _renderRightProgress(snapshot, waiting) {
      var timeline = Array.isArray(snapshot.timeline) ? snapshot.timeline : [];
      var items = timeline.length ? timeline.slice(-8).map(function (item) {
        var done = item.state === 'done';
        var error = item.kind === 'error';
        var text = item.text || item.label || '正在处理';
        return '<div class="tm-right-progress-item' + (done ? ' is-done' : '') + (error ? ' is-error' : '') + '"><span class="tm-right-progress-icon material-symbols-rounded">' + (error ? 'error' : (done ? 'check' : 'progress_activity')) + '</span><span>' + escapeHtml(text) + '</span>' + (item.detail ? '<small>' + escapeHtml(item.detail) + '</small>' : '') + '</div>';
      }).join('') : '<div class="tm-right-progress-item is-current"><span class="tm-right-progress-icon material-symbols-rounded">progress_activity</span><span>' + escapeHtml(waiting ? '等待教师确认后继续' : _analysisActivityLabel(snapshot)) + '</span></div>';
      return '<div class="tm-right-running-card"><div class="tm-right-running-intro"><span class="tm-right-running-mark material-symbols-rounded">' + (waiting ? 'pan_tool_alt' : 'auto_awesome') + '</span><div><strong>' + escapeHtml(waiting ? '任务已准备好' : 'TeachMate 正在完成分析') + '</strong><p>' + escapeHtml(waiting ? '确认后会继续执行当前任务，分析过程和数据范围不会改变。' : '你可以在这里看到当前阶段；完成后右侧会自动变成完整报告。') + '</p></div></div><div class="tm-right-progress-list" role="list" aria-label="分析进度">' + items + '</div></div>';
    }

    function _renderRightDataQuality(snapshot) {
      var ready = snapshot.dataReady || {};
      var issues = Array.isArray(ready.issues) ? ready.issues : [];
      var label = ready.ready === false ? '数据需要处理' : (issues.length ? '数据可用，有提醒' : '数据检查已完成');
      var detail = issues.length ? ('发现 ' + issues.length + ' 项需要注意') : '当前任务使用的数据已通过基础检查';
      var cls = ready.ready === false ? ' tm-right-quality-error' : (issues.length ? ' tm-right-quality-warning' : ' tm-right-quality-ok');
      return '<div class="tm-right-quality' + cls + '" data-testid="right-data-quality"><span class="material-symbols-rounded">' + (ready.ready === false ? 'error' : (issues.length ? 'warning' : 'check_circle')) + '</span><div><strong>' + escapeHtml(label) + '</strong><small>' + escapeHtml(detail) + '</small></div><button type="button" data-act="tm-right-tab" data-tab="quality">查看</button></div>';
    }

    function _renderRightQualityDetail(snapshot) {
      var ready = snapshot.dataReady || {};
      var issues = Array.isArray(ready.issues) ? ready.issues : [];
      var rows = issues.length ? issues.map(function (issue) {
        var message = typeof issue === 'string' ? issue : (issue.message || issue.detail || issue.title || '需要关注的数据问题');
        return '<div class="tm-right-quality-row"><span class="material-symbols-rounded">warning</span><span>' + escapeHtml(message) + '</span></div>';
      }).join('') : '<div class="tm-right-quality-row is-ok"><span class="material-symbols-rounded">check_circle</span><span>当前任务使用的数据已通过基础检查。</span></div>';
      return '<div class="tm-right-quality-detail"><div class="tm-right-detail-title">数据质量详情</div><p>这些提示来自数据导入和分析前检查，不由模型生成。</p>' + rows + '</div>';
    }

    function _renderRightNextActions(snapshot) {
      var runId = escapeAttr(String(snapshot.currentRunId || ''));
      var session = (snapshot.sessions || []).find(function (item) { return String(item.id) === String(snapshot.currentSessionId); });
      var hasExam = !!(session && session.exam_id);
      var answer = snapshot.reportAnswer;
      if (!answer && Array.isArray(snapshot.messages)) {
        for (var i = snapshot.messages.length - 1; i >= 0; i--) {
          var candidate = snapshot.messages[i] && snapshot.messages[i].structured_answer;
          if (typeof candidate === 'string') { try { candidate = JSON.parse(candidate); } catch (e) { candidate = null; } }
          if (candidate && typeof candidate === 'object') { answer = candidate; break; }
        }
      }
      var isTeachingPackage = !!(answer && answer.answer_type === 'review_plan');
      var actions = (isTeachingPackage ? '' : '<button type="button" data-act="tm-report-confirm" data-run-id="' + runId + '">教师确认</button>') +
        '<button type="button" data-act="tm-followup-task" data-capability="' + (isTeachingPackage ? 'review_plan' : 'general_chat') + '" data-prompt="' + escapeAttr(isTeachingPackage
          ? '请根据我接下来补充的调整要求，修订当前教学包；保留未涉及的材料，并继续把学生练习与教师答案分开展示。调整要求：'
          : '请基于当前会话中有证据支持的教学发现，准备一份可直接使用的讲评草稿：包括 20 分钟流程、课堂提问、分层练习、教师答案与讲解。请区分数据事实和待验证解释；不要把推测写成学生结论。') + '">' + (isTeachingPackage ? '调整教学包' : '准备讲评材料') + '</button>';
      if (hasExam) {
        if (!isTeachingPackage) {
          actions += '<button type="button" data-act="tm-followup-task" data-capability="review_plan" data-prompt="' + escapeAttr('请基于当前考试的已核验分析，制定复习安排和练习建议。注明数据局限；不得把推测写成学生结论。') + '">生成复习计划</button>';
        }
      }
      if (answer && answer.answer_type === 'review_plan') {
        actions += '<button type="button" data-act="tm-export-doc" data-format="pdf" data-run-id="' + runId + '">导出教师版 PDF</button>' +
          '<button type="button" data-act="tm-export-doc" data-format="docx" data-run-id="' + runId + '">导出教师版 Word</button>';
        if (Array.isArray(answer.sections) && answer.sections.some(function (section) { return section && section.kind === 'student_handout'; })) {
          actions += '<button type="button" data-act="tm-export-doc" data-export-variant="student-handout" data-format="pdf" data-run-id="' + runId + '">单独导出学生练习单</button>';
        }
      }
      return '<div class="tm-right-next"><div class="tm-right-next-title">继续完成教学任务</div><div class="tm-right-next-actions">' + actions + '</div><small class="tm-right-next-hint">选择后会填入输入框；检查范围和要求后再发送。</small></div>';
    }

    function _renderContextCard(snapshot) {
      var session = snapshot.sessions.find(function(s) { return s.id === snapshot.currentSessionId; });
      var rows = '';
      var ctxNames = snapshot.contextNames || {};
      if (session) {
        // P1-12: 从 AgentSessionRead 真实字段构建上下文，而非不存在的 scope_context
        if (session.term_id) {
          var termName = '';
          var term = (availableTerms || []).find(function(t) { return t.id === session.term_id; });
          if (term) termName = term.name;
          rows += '<div class="tm-context-row"><span class="tm-context-label">学期</span><span class="tm-context-value">' + escapeHtml(termName || ('#' + session.term_id)) + '</span></div>';
        }
        if (session.class_id) {
          // P1-F: 使用会话固定的 class_id 查找名称，不依赖全局 dashboardClass
          var className = (ctxNames.class && ctxNames.class[session.class_id]) || ('班级 #' + session.class_id);
          rows += '<div class="tm-context-row"><span class="tm-context-label">班级</span><span class="tm-context-value">' + escapeHtml(className) + '</span></div>';
        }
        if (session.exam_id) {
          // P1-F: 使用会话固定的 exam_id 查找名称，不依赖全局 currentExamId
          var examName = (ctxNames.exam && ctxNames.exam[session.exam_id]) || '';
          if (!examName) {
            var exam = (state.exams || []).find(function(e) { return String(e.id) === String(session.exam_id); });
            if (exam) examName = exam.name;
          }
          rows += '<div class="tm-context-row"><span class="tm-context-label">考试</span><span class="tm-context-value">' + escapeHtml(examName || ('考试 #' + session.exam_id)) + '</span></div>';
        }
        if (session.student_id) {
          // P1-F: 使用缓存的名称，不展示裸学生 ID
          var studentName = (ctxNames.student && ctxNames.student[session.student_id]) || ('学生 #' + session.student_id);
          rows += '<div class="tm-context-row"><span class="tm-context-label">学生</span><span class="tm-context-value">' + escapeHtml(studentName) + '</span></div>';
        }
      }
      if (!rows) {
        rows = '<div class="tm-context-row"><span class="tm-context-label">未选择会话</span></div>';
      }
      return '<div class="tm-context-card">' +
        '<div class="tm-context-card-title">当前上下文</div>' +
        rows + '</div>';
    }

    function _renderEvidenceList(snapshot) {
      var evidence = snapshot.currentEvidence || [];
      if (!evidence.length) {
        return '<div class="tm-context-card">' +
          '<div class="tm-context-card-title">证据</div>' +
          '<div style="font-size:13px;color:var(--tm-text-muted);padding:8px 0;">暂无证据数据</div></div>';
      }
      var items = evidence.map(function(e) {
        return (typeof teachMateEvidence !== 'undefined')
          ? teachMateEvidence.renderEvidenceMini(e)
          : '<div class="tm-evidence-mini" data-act="tm-view-evidence-item" data-evidence-id="' + escapeAttr(String(e.evidence_id || '')) + '" data-run-id="' + escapeAttr(String(snapshot.currentRunId || '')) + '">' +
            '<div class="tm-evidence-mini-type">' + escapeHtml(e.evidence_type || '查询') + '</div>' +
            '<div class="tm-evidence-mini-query">' + escapeHtml(e.display_summary || e.evidence_id || '') + '</div></div>';
      }).join('');
      var run = snapshot.currentRun || {};
      return '<div class="tm-context-card">' +
        '<div class="tm-context-card-title">证据 (' + evidence.length + ')</div>' +
        '<div class="tm-evidence-list">' + items + '</div></div>';
    }

    function _renderWelcomePage() {
      var snapshot = teachMateState.getSnapshot();
      // P1-12/P1-13: 只展示已实现的能力快捷卡，携带 data-quick-task 路由
      // 已实现能力: exam_analysis（考试分析）、student_diagnosis（学生诊断）、review_plan（复习计划）
      var provider = snapshot.providerInfo;
      // providerInfo 尚未返回时只表示“未知”，不能把快捷入口误判为不可用。
      var available = !provider || (provider.agent_enabled && provider.text_agent_enabled && provider.api_key_configured);
      var unavailableReason = _teachMateUnavailableReason(provider);
      var unavailable = (provider && unavailableReason) ? ' data-provider-unavailable="true" title="' + escapeAttr(unavailableReason) + '"' : '';
      var quickUnavailable = function (pluginId) {
        if (typeof tmIsPluginEnabled === 'function' && !tmIsPluginEnabled(pluginId)) {
          return ' disabled aria-disabled="true" title="该插件已停用，请先在设置 → 插件管理中启用。"';
        }
        return unavailable;
      };
      var teacherName = String(state.teacher && state.teacher.name || '').trim();
      var greeting = teacherName ? '你好，' + escapeHtml(teacherName) : '你好，我是 TeachMate';
      return '<div class="tm-welcome">' +
        '<div class="tm-welcome-hero">' +
          '<div class="tm-welcome-copy">' +
            '<div class="tm-welcome-brand" aria-hidden="true"><span class="tm-spark tm-spark-one">✦</span><div class="tm-welcome-icon">' + _teachMateIconMarkup('tm-brand-icon tm-brand-icon-welcome', 'TeachMate') + '</div><span class="tm-spark tm-spark-two">✦</span></div>' +
            '<div class="tm-welcome-eyebrow">TEACHMATE · AI 教学助手</div>' +
            '<h2>' + greeting + '</h2>' +
            '<img class="tm-welcome-underline" src="./workbench-assets/illustrations/teachmate-doodle-underline.svg" alt="" aria-hidden="true" draggable="false">' +
          '</div>' +
        '</div>' +
        '<div class="tm-quick-prompts-title">你可以这样问我</div>' +
        '<div class="tm-suggestions">' +
        '<button class="tm-suggestion-card tm-suggestion-analytics" data-act="tm-suggestion" data-quick-task="exam_analysis" data-prompt="帮我分析这次考试的整体成绩情况，包括均分、分布、分层等"' + quickUnavailable('exam_analysis') + '>' +
        '<span class="tm-suggestion-icon material-symbols-rounded">analytics</span><span class="tm-suggestion-copy"><strong>分析考试成绩</strong><small>可基于文字、附件，或选择数据库考试增强分析</small></span><span class="tm-card-arrow material-symbols-rounded">arrow_forward</span></button>' +
        '<button class="tm-suggestion-card tm-suggestion-student" data-act="tm-suggestion" data-quick-task="student_diagnosis" data-prompt="给当前班级所有学生做学生画像，并给出个性化提升建议"' + quickUnavailable('student_diagnosis') + '>' +
        '<span class="tm-suggestion-icon material-symbols-rounded">person_search</span><span class="tm-suggestion-copy"><strong>学生诊断</strong><small>支持全班批量，也可点名单人</small></span><span class="tm-card-arrow material-symbols-rounded">arrow_forward</span></button>' +
        '<button class="tm-suggestion-card tm-suggestion-plan" data-act="tm-suggestion" data-quick-task="review_plan" data-prompt="帮我根据这次考试成绩生成针对性的复习计划"' + quickUnavailable('review_plan') + '>' +
        '<span class="tm-suggestion-icon material-symbols-rounded">edit_note</span><span class="tm-suggestion-copy"><strong>生成复习计划</strong><small>把成绩分析转成可执行的安排</small></span><span class="tm-card-arrow material-symbols-rounded">arrow_forward</span></button>' +
        '</div>' +
        '<img class="tm-welcome-books" src="./workbench-assets/illustrations/teachmate-doodle-books.svg" alt="" aria-hidden="true" draggable="false">' +
        '</div>';
    }

    function _teachMateUnavailableReason(provider) {
      if (!provider) return '';
      if (!provider.api_key_configured) return 'AI 分析尚未配置 API Key，请先打开“设置 → 模型”填写。';
      if (!provider.agent_enabled || !provider.text_agent_enabled) {
        return 'API Key 已配置，但 Agent 能力尚未启用；请点击“保存并应用”，然后重启工作台。';
      }
      return '';
    }

    function _renderMessages(snapshot) {
      if (!snapshot.messages || !snapshot.messages.length) {
        if (snapshot.scopePrompt) return '';
        return '<div class="tm-empty-chat"><div class="tm-empty-icon"><span class="material-symbols-rounded">forum</span></div>' +
          '<strong>这个对话还是空的</strong><p>输入教学问题，TeachMate 会结合当前上下文进行分析。</p></div>';
      }
      var msgs = snapshot.messages;
      // 长会话性能优化：超过 100 条时只渲染最近 80 条 + 折叠提示
      if (!teachMateState._renderAllMessages && msgs.length > 100) {
        var skipped = msgs.length - 80;
        var recent = msgs.slice(-80);
        return '<div class="tm-virtual-sep" data-act="tm-load-more" role="button" tabindex="0" aria-label="加载更多消息">↑ ' + skipped + ' 条更早的消息已折叠，点击展开</div>' +
          recent.map(m => _renderMessage(m)).join('');
      }
      return msgs.map(m => _renderMessage(m)).join('');
    }

    /** 消息附件 chip：图标 + 文件名 + 大小（用户消息气泡内）。 */
    function _userFileMeta(att) {
      var name = att.original_name || att.title || ('附件 #' + (att.id || ''));
      var ext = String(name).split('.').pop().toLowerCase();
      var icon = ext === 'pdf' ? 'picture_as_pdf'
        : (ext === 'xlsx' || ext === 'xls' || ext === 'csv') ? 'table'
        : (ext === 'docx' || ext === 'doc') ? 'article'
        : (ext === 'txt' || ext === 'md') ? 'text_snippet'
        : 'description';
      var sizeText = '';
      var size = att.size_bytes;
      if (typeof size === 'number' && isFinite(size) && size >= 0) {
        sizeText = size >= 1048576 ? (size / 1048576).toFixed(1) + ' MB'
          : size >= 1024 ? Math.round(size / 1024) + ' KB'
          : size + ' B';
      }
      return { name: name, icon: icon, sizeText: sizeText };
    }

    function _renderUserFileChip(att) {
      var meta = _userFileMeta(att);
      return '<span class="tm-message-user-file-chip" role="listitem" title="' + escapeAttr(meta.name) + '">' +
        '<span class="material-symbols-rounded tm-message-user-file-icon" aria-hidden="true">' + meta.icon + '</span>' +
        '<span class="tm-message-user-file-name">' + escapeHtml(meta.name) + '</span>' +
        (meta.sizeText ? '<span class="tm-message-user-file-size">' + meta.sizeText + '</span>' : '') +
        '</span>';
    }

    // 普通聊天仍然按原文展示；只有明显是分析类长文本时，才把标题、数据行和列表
    // 拆成报告式层级。这样可以兼容尚未返回 structured_answer 的追问，也不会改变普通对话。
    function _plainAnalysisHeading(line) {
      var value = String(line || '').trim().replace(/^#{1,6}\s+/, '').replace(/[：:]$/, '');
      if (/^(结论摘要|主要发现|关键发现|建议|行动建议|下一步行动|数据限制|局限|证据与说明)$/.test(value)) return value;
      if (/^(关于.+|薄弱层.+)$/.test(value)) return value;
      return '';
    }

    function _isPlainAnalysisReport(content) {
      var text = String(content || '');
      if (!text.trim()) return false;
      var headings = (text.match(/结论摘要|主要发现|关键发现|关于.+?的可点名情况|薄弱层|数据限制|局限|行动建议/g) || []).length;
      var hasAnalysisContext = /分析|报告|诊断|分层|成绩|考试/.test(text);
      var hasPackedData = /层级分数范围人数占比|分数段人数|指标数值/.test(text);
      return hasAnalysisContext && (headings >= 2 || hasPackedData);
    }

    function _renderPlainAnalysisMetrics(line) {
      var value = String(line || '');
      if (/层级分数范围人数占比/.test(value)) {
        var labels = ['优秀 (A)', '良好 (B)', '中等 (C)', '待提升 (D)'];
        var rows = [];
        labels.forEach(function (label, index) {
          var start = value.indexOf(label);
          if (start < 0) return;
          var end = value.length;
          labels.slice(index + 1).forEach(function (nextLabel) {
            var next = value.indexOf(nextLabel, start + label.length);
            if (next >= 0 && next < end) end = next;
          });
          var tail = value.slice(start + label.length, end);
          var percentMatch = tail.match(/\.(\d+)\s*%/);
          var parsed = null;
          if (percentMatch) {
            var beforePercent = tail.slice(0, percentMatch.index).replace(/\s+/g, '');
            var percentDigits = beforePercent.match(/(\d+)$/);
            if (beforePercent.charAt(0) === '<' && percentDigits) {
              var lessDigits = percentDigits[1];
              // 依次尝试最长的分数上限，剩余数字再拆成人数和占比。
              for (var lessLength = Math.min(3, lessDigits.length - 2); lessLength >= 1; lessLength--) {
                var lessLimit = Number(lessDigits.slice(0, lessLength));
                if (lessLimit <= 100) {
                  for (var lessPercentLength = Math.min(3, lessDigits.length - lessLength - 1); lessPercentLength >= 1; lessPercentLength--) {
                    var lessPercentInteger = lessDigits.slice(-lessPercentLength);
                    var lessCount = lessDigits.slice(lessLength, -lessPercentLength);
                    if (lessCount && Number(lessPercentInteger) <= 100) {
                      parsed = { range: '<' + lessLimit, count: lessCount, percent: lessPercentInteger + '.' + percentMatch[1] };
                      break;
                    }
                  }
                  if (parsed) break;
                }
              }
            } else {
              var rangeParts = beforePercent.match(/^(\d+)[–—-](\d+)$/);
              if (rangeParts) {
                var lower = Number(rangeParts[1]);
                var upperAndCountAndPercent = rangeParts[2];
                // 占比可能和人数连写（如 2351.1%）。从占比整数部分的最长合法后缀开始，
                // 再寻找满足上下限关系的分数上限。
                for (var percentLength = Math.min(3, upperAndCountAndPercent.length - 1); percentLength >= 1; percentLength--) {
                  var percentInteger = upperAndCountAndPercent.slice(-percentLength);
                  var upperAndCount = upperAndCountAndPercent.slice(0, -percentLength);
                  if (Number(percentInteger) > 100 || !upperAndCount) continue;
                  for (var upperLength = 1; upperLength <= Math.min(3, upperAndCount.length - 1); upperLength++) {
                    var upper = Number(upperAndCount.slice(0, upperLength));
                    var count = upperAndCount.slice(upperLength);
                    if (upper >= lower && upper <= 100 && /^\d+$/.test(count)) {
                      parsed = { range: rangeParts[1] + '–' + upper, count: count, percent: percentInteger + '.' + percentMatch[1] };
                      break;
                    }
                  }
                  if (parsed) break;
                }
              }
            }
          }
          if (parsed) {
            rows.push({ label: label, range: parsed.range, count: parsed.count, percent: parsed.percent, index: index });
          }
        });
        if (rows.length) {
          return '<div class="tm-text-report-data-block tm-text-report-tier-grid" role="table" aria-label="学生层级分布">' +
            rows.map(function (row) {
              return '<div class="tm-text-report-tier-card tier-' + row.index + '" role="row">' +
                '<div class="tm-text-report-tier-label" role="cell">' + escapeHtml(row.label) + '</div>' +
                '<div class="tm-text-report-tier-range" role="cell">' + escapeHtml(row.range) + ' 分</div>' +
                '<strong role="cell">' + escapeHtml(row.count) + '<small> 人</small></strong>' +
                '<span role="cell">' + escapeHtml(row.percent) + '%</span>' +
                '</div>';
            }).join('') + '</div>';
        }
      }

      if (/分数段人数/.test(value)) {
        var bands = [];
        var bandRe = /(\d+)\s*[–—-]\s*(\d+)\s*（([^）]+)）/g;
        var matches = [];
        var match;
        while ((match = bandRe.exec(value))) matches.push({ match: match, start: match.index, end: bandRe.lastIndex });
        var normalizedBands = matches.map(function (item) {
          var match = item.match;
          var lowerText = match[1];
          var upper = Number(match[2]);
          var lower = Number(lowerText);
          var prefixCount = '';
          // “150–60”实际是“1 人，50–60 分”被连写；把前置人数与分数范围拆开。
          if (lower > upper) {
            for (var lowerLength = Math.min(3, lowerText.length); lowerLength >= 1; lowerLength--) {
              var candidateLower = Number(lowerText.slice(-lowerLength));
              var candidatePrefix = lowerText.slice(0, -lowerLength);
              if (candidateLower <= upper && candidateLower >= 0 && candidatePrefix) {
                lower = candidateLower;
                prefixCount = candidatePrefix;
                break;
              }
            }
          }
          return { item: item, lower: lower, upper: upper, prefixCount: prefixCount };
        });
        normalizedBands.forEach(function (band, index) {
          var item = band.item;
          var match = item.match;
          var count = '';
          // 下一段范围前的前置数字其实是当前段人数，例如“40）150–60”中的 1。
          if (index + 1 < normalizedBands.length && normalizedBands[index + 1].prefixCount) {
            count = normalizedBands[index + 1].prefixCount;
          }
          if (!count) {
            var nextStart = index + 1 < normalizedBands.length ? normalizedBands[index + 1].item.start : value.length;
            var between = value.slice(item.end, nextStart).match(/^\s*(\d+)/);
            if (between) count = between[1];
          }
          if (count) bands.push({ range: band.lower + '–' + band.upper, label: match[3], count: count });
        });
        if (bands.length) {
          return '<div class="tm-text-report-data-block tm-text-report-band-grid" role="table" aria-label="分数段人数">' +
            bands.map(function (band) {
              return '<div class="tm-text-report-band-card" role="row"><span class="tm-text-report-band-range" role="cell">' + escapeHtml(band.range) + ' 分</span><span role="cell">' + escapeHtml(band.label) + '</span><strong role="cell">' + escapeHtml(band.count) + '<small> 人</small></strong></div>';
            }).join('') + '</div>';
        }
      }

      if (/指标数值/.test(value)) {
        var metricKeys = ['参与人数', '平均分', '合格率', '优秀率', '最高 / 最低', '最高', '最低', '标准差'];
        var positions = [];
        metricKeys.forEach(function (key) {
          if ((key === '最高' || key === '最低') && value.indexOf('最高 / 最低') >= 0) return;
          var at = value.indexOf(key);
          if (at >= 0) positions.push({ key: key, at: at });
        });
        positions.sort(function (a, b) { return a.at - b.at; });
        if (positions.length) {
          return '<div class="tm-text-report-data-block tm-text-report-metric-grid" role="table" aria-label="考试核心指标">' +
            positions.map(function (item, index) {
              var end = index + 1 < positions.length ? positions[index + 1].at : value.length;
              var metricValue = value.slice(item.at + item.key.length, end).replace(/[：:\s，,。]+/g, ' ').trim();
              return '<div class="tm-text-report-metric-card" role="row"><span role="cell">' + escapeHtml(item.key) + '</span><strong role="cell">' + escapeHtml(metricValue) + '</strong></div>';
            }).join('') + '</div>';
        }
      }
      return '';
    }

    function _renderPlainAnalysisBody(lines) {
      var html = '';
      var list = [];
      function renderMarkdown(text) {
        if (typeof window !== 'undefined' && window.WBEnhancements && typeof window.WBEnhancements.renderMarkdown === 'function') {
          return window.WBEnhancements.renderMarkdown(text);
        }
        // teachmate-views 在增强脚本完成初始化前也可能先渲染一次，
        // 此时不能把 Markdown 源码直接显示给教师，至少先完成安全的行内格式化。
        return escapeHtml(text)
          .replace(/`([^`\n]+)`/g, '<code class="md-code">$1</code>')
          .replace(/\*\*([^*]+)\*\*/g, '<strong class="md-strong">$1</strong>')
          .replace(/(^|[^*])\*([^*]+)\*(?!\*)/g, '$1<em class="md-em">$2</em>');
      }
      function renderInline(text) {
        var rendered = renderMarkdown(text);
        return rendered.replace(/^<p class="md-p">([\s\S]*)<\/p>$/, '$1');
      }
      function flushList() {
        if (!list.length) return;
        html += '<ul class="tm-text-report-list">' + list.map(function (item) { return '<li>' + renderInline(item) + '</li>'; }).join('') + '</ul>';
        list = [];
      }
      for (var lineIndex = 0; lineIndex < lines.length; lineIndex++) {
        var rawLine = lines[lineIndex];
        var line = String(rawLine || '').trim();
        if (!line) continue;

        // 连续的 Markdown 表格交给统一渲染器，避免逐行显示竖线和分隔线。
        var nextLine = lineIndex + 1 < lines.length ? String(lines[lineIndex + 1] || '').trim() : '';
        if (line.indexOf('|') >= 0 && /^\|?\s*:?-{3,}/.test(nextLine)) {
          flushList();
          var tableLines = [line, nextLine];
          lineIndex += 2;
          while (lineIndex < lines.length && String(lines[lineIndex] || '').indexOf('|') >= 0) {
            tableLines.push(String(lines[lineIndex]).trim());
            lineIndex++;
          }
          lineIndex--;
          html += renderMarkdown(tableLines.join('\n'));
          continue;
        }
        var dataBlock = _renderPlainAnalysisMetrics(line);
        if (dataBlock) { flushList(); html += dataBlock; continue; }

        // AI 有时会把多个项目压在同一行，用 • 分隔；拆开后每一项独立成行。
        var bulletParts = line.split(/\s*•\s*/).filter(function (part) { return part.trim(); });
        var isBullet = /^[-*]\s+/.test(line) || /^\d+[.、]\s+/.test(line) || line.charAt(0) === '•';
        if (isBullet || bulletParts.length > 1) {
          bulletParts.forEach(function (part) {
            var item = part.replace(/^[-*]\s+/, '').replace(/^\d+[.、]\s+/, '').trim();
            if (item) list.push(item);
          });
          continue;
        }
        flushList();
        html += renderMarkdown(line);
      }
      flushList();
      return html;
    }

    function _renderPlainAnalysisReport(content) {
      var lines = String(content || '').replace(/\r\n?/g, '\n').split('\n').map(function (line) { return line.trim(); }).filter(Boolean);
      if (!lines.length) return '';
      var title = '';
      var sections = [];
      var current = null;
      lines.forEach(function (line, index) {
        var heading = _plainAnalysisHeading(line);
        if (!title && index === 0 && !heading && line.length <= 36 && /分析|报告|诊断|分层/.test(line)) {
          title = line;
          return;
        }
        if (heading) {
          current = { title: heading, lines: [] };
          sections.push(current);
          return;
        }
        if (!current) {
          current = { title: title ? '分析结果' : '', lines: [] };
          sections.push(current);
        }
        current.lines.push(line);
      });
      if (!sections.length) sections.push({ title: '', lines: lines });
      var sectionHtml = sections.map(function (section) {
        var headingHtml = section.title ? '<h3 class="tm-text-report-section-title">' + escapeHtml(section.title) + '</h3>' : '';
        return '<section class="tm-text-report-section">' + headingHtml + _renderPlainAnalysisBody(section.lines) + '</section>';
      }).join('');
      return '<div class="tm-plain-analysis" aria-label="分析回复">' +
        (title ? '<div class="tm-text-report-title"><span class="material-symbols-rounded" aria-hidden="true">analytics</span><strong>' + escapeHtml(title) + '</strong><span>分析结果</span></div>' : '') +
        sectionHtml + '</div>';
    }

    function _renderMessage(msg) {
      if (msg.role === 'user') {
        var userFiles = '';
        var atts = msg.attachments || [];
        if (atts.length) {
          userFiles = '<div class="tm-message-user-files" role="list" aria-label="消息附件">' + atts.map(function (att) {
            return _renderUserFileChip(att);
          }).join('') + '</div>';
        }
        var messagePlugin = msg.plugin || null;
        if (!messagePlugin && msg.capability && msg.capability !== 'general_chat' && typeof tmPluginDefinitions === 'function') {
          var capabilityId = String(msg.capability);
          var matchedPlugin = tmPluginDefinitions().find(function (plugin) {
            return plugin.id === capabilityId || (plugin.capability_ids || []).includes(capabilityId);
          });
          if (matchedPlugin) messagePlugin = { name: matchedPlugin.name, icon: matchedPlugin.icon_asset };
        }
        var pluginBadge = messagePlugin ? '<div class="tm-message-plugin-chip"><img src="' + escapeAttr(messagePlugin.icon || '/workbench-assets/plugin-icons/spark.svg') + '" alt=""><span>' + escapeHtml(messagePlugin.name || '插件') + '</span></div>' : '';
        return '<div class="tm-message tm-message-user">' +
          '<div class="tm-message-user-stack">' + pluginBadge +
            '<div class="tm-message-content">' + userFiles +
              '<div class="tm-message-text">' + escapeHtml(msg.content_text || msg.content || '') + '</div>' +
            '</div>' +
          '</div></div>';
      }
      // assistant
      var content = msg.content_text || msg.content || '';
      var structured = '';
      var hasStructuredReport = false;
      if (msg.structured_answer) {
        var parsedAnswer = msg.structured_answer;
        if (typeof parsedAnswer === 'string') {
          try { parsedAnswer = JSON.parse(parsedAnswer); } catch (e) { parsedAnswer = null; }
        }
        hasStructuredReport = !!(parsedAnswer && typeof parsedAnswer === 'object');
        // 报告直接嵌入消息时间线：后续继续追问时，之前的报告仍保留在上方。
        structured = hasStructuredReport ? _renderInlineReportSummary(parsedAnswer, msg) : '';
      }
      var evidenceBtn = '';
      var evIds = msg.evidence_ids;
      if (evIds) {
        if (typeof evIds === 'string') { try { evIds = JSON.parse(evIds); } catch (e) { evIds = null; } }
        if (evIds && evIds.length && !hasStructuredReport && msg.run_id) evidenceBtn = '<button class="tm-evidence-btn" data-act="tm-view-evidence" data-run-id="' + escapeAttr(String(msg.run_id)) + '"><span class="material-symbols-rounded">fact_check</span>查看证据 (' + evIds.length + ')</button>';
      }
      var pending = msg._pending ? ' tm-message-pending' : '';
      var isPlainAnalysis = !msg._pending && !hasStructuredReport && _isPlainAnalysisReport(content);
      var pendingBody = '';
      if (msg._pending) {
        // B3-17: AI 回复中的打字指示器
        pendingBody = '<div class="tm-message-content tm-typing" aria-label="TeachMate 正在思考">' +
          (content ? '<span class="tm-pending-copy">' + escapeHtml(content) + '</span>' : '') +
          '<span class="tm-typing-dots" aria-hidden="true"><i></i><i></i><i></i></span></div>';
      } else if (hasStructuredReport) {
        // 结构化分析的长内容属于报告产物；聊天时间线只保留可快速阅读的结论摘要。
        var summaryText = parsedAnswer && parsedAnswer.summary
          ? parsedAnswer.summary
          : '分析已完成，详细结果已整理到考试分析报告。';
        pendingBody = '<div class="tm-message-content tm-structured-summary"><span class="tm-structured-summary-label">结论摘要</span><p>' + escapeHtml(summaryText) + '</p></div>';
      } else {
        var renderedContent = isPlainAnalysis ? _renderPlainAnalysisReport(content) : ((typeof window !== 'undefined' && window.WBEnhancements && typeof window.WBEnhancements.renderMarkdown === 'function')
          ? window.WBEnhancements.renderMarkdown(content)
          : escapeHtml(content));
        pendingBody = '<div class="tm-message-content' + (isPlainAnalysis ? ' tm-plain-analysis-content' : '') + '">' + renderedContent + '</div>';
      }
      // U5-03: 保留打印、教师确认和诊断操作，仅对有结构报告的 assistant 消息显示
      var exportActions = '';
      if (hasStructuredReport && !msg._pending) {
        var _runIdAttr = escapeAttr(String(msg.run_id || ''));
        var showEvaluationConfirm = parsedAnswer.answer_type !== 'review_plan';
        exportActions = '<div class="tm-message-actions" role="group" aria-label="报告操作">' +
          '<button type="button" class="tm-action-btn" data-act="tm-export-print" data-run-id="' + _runIdAttr + '" title="打印或另存为 PDF"><span class="material-symbols-rounded" aria-hidden="true">print</span>' + (parsedAnswer.answer_type === 'review_plan' ? '打印教师版' : '打印') + '</button>' +
          '<button type="button" class="tm-action-btn" data-act="tm-copy-summary" data-copy-text="' + escapeAttr(String(summaryText || parsedAnswer.summary || '')) + '" title="复制本报告结论"><span class="material-symbols-rounded" aria-hidden="true">content_copy</span>复制结论</button>' +
          (showEvaluationConfirm ? '<button type="button" class="tm-action-btn" data-act="tm-report-confirm" data-run-id="' + escapeAttr(String(msg.run_id || '')) + '" title="教师确认"><span class="material-symbols-rounded" aria-hidden="true">verified</span>教师确认</button>' : '') +
          '<button type="button" class="tm-action-btn tm-action-copy" data-act="tm-copy-diagnostics" data-run-id="' + escapeAttr(String(msg.run_id || '')) + '" title="复制诊断信息"><span class="material-symbols-rounded" aria-hidden="true">content_copy</span>诊断</button>' +
          '</div>';
      }
      return '<div class="tm-message tm-message-ai' + pending + (isPlainAnalysis ? ' tm-plain-analysis-message' : '') + '">' +
        '<div class="tm-message-body">' +
        pendingBody +
        structured +
        evidenceBtn +
        exportActions +
        '</div></div>';
    }

    function _messageElapsedMs(msg, snapshot) {
      msg = msg || {};
      var started = msg.started_at || msg.startedAt;
      var completed = msg.completed_at || msg.completedAt;
      if (typeof msg.duration_ms === 'number') return msg.duration_ms;
      if (started && completed) {
        var startMs = Date.parse(started);
        var endMs = Date.parse(completed);
        if (isFinite(startMs) && isFinite(endMs)) return Math.max(0, endMs - startMs);
      }
      if (snapshot && msg.run_id && snapshot.currentRunId && String(msg.run_id) === String(snapshot.currentRunId)) {
        return snapshot.elapsedMs;
      }
      return null;
    }

    function _formatDurationLabel(ms) {
      if (ms == null || !isFinite(ms)) return '';
      var totalSec = Math.max(0, Math.floor(ms / 1000));
      var minutes = Math.floor(totalSec / 60);
      var seconds = totalSec % 60;
      return minutes + '分' + (seconds < 10 ? '0' : '') + seconds + '秒';
    }

    function _renderInlineReportSummary(answer, msg) {
      var snapshot = teachMateState.getSnapshot();
      var isTeachingPackage = answer.answer_type === 'review_plan';
      var materialCount = Array.isArray(answer.sections) ? answer.sections.filter(Boolean).length : 0;
      var findings = Array.isArray(answer.findings) ? answer.findings.length : 0;
      var recommendations = Array.isArray(answer.recommendations) ? answer.recommendations.length : 0;
      var duration = _formatDurationLabel(_messageElapsedMs(msg, snapshot));
      var reportHtml = (typeof teachMateReport !== 'undefined' && teachMateReport.renderReportCanvas)
        ? teachMateReport.renderReportCanvas(answer, { hideEvidence: true, hideLimitations: !isTeachingPackage, runId: msg && msg.run_id })
        : _renderStructuredAnswer(answer, { hideEvidence: true, hideLimitations: !isTeachingPackage });
      // 数据质量属于某次分析的范围；历史报告不能复用当前底部选择器的状态。
      var isCurrentReport = !msg || !msg.run_id || !snapshot.currentRunId
        || String(msg.run_id) === String(snapshot.currentRunId);
      var qualityHtml = snapshot.dataReady && isCurrentReport
        ? _renderInlineReportQuality(snapshot)
        : '';
      var packageActions = '';
      if (isTeachingPackage && msg && msg.run_id) {
        var runId = escapeAttr(String(msg.run_id));
        packageActions = '<div class="tm-material-actions" role="group" aria-label="教学包操作">' +
          '<button type="button" class="tm-action-btn" data-act="tm-export-doc" data-format="docx" data-run-id="' + runId + '"><span class="material-symbols-rounded" aria-hidden="true">description</span>教师版 Word</button>' +
          '<button type="button" class="tm-action-btn" data-act="tm-export-doc" data-format="pdf" data-run-id="' + runId + '">教师版 PDF</button>';
        if (Array.isArray(answer.sections) && answer.sections.some(function (section) { return section && section.kind === 'student_handout'; })) {
          packageActions += '<button type="button" class="tm-action-btn tm-material-export-student" data-act="tm-export-doc" data-format="pdf" data-export-variant="student-handout" data-run-id="' + runId + '"><span class="material-symbols-rounded" aria-hidden="true">download</span>单独导出学生练习单</button>';
        }
        var session = (snapshot.sessions || []).find(function (item) { return String(item.id) === String(snapshot.currentSessionId); });
        if (isCurrentReport && session && session.exam_id) {
          packageActions += '<button type="button" class="tm-action-btn" data-act="tm-followup-task" data-capability="review_plan" data-prompt="请修订当前教学包，保留未涉及的材料，并把学生练习与教师答案分开展示。调整要求："><span class="material-symbols-rounded" aria-hidden="true">edit</span>调整教学包</button>';
        }
        packageActions += '</div>';
      }
      return '<section class="tm-inline-report-summary tm-inline-artifact" data-testid="inline-report-summary" data-testid-artifact="inline-artifact">' +
        '<div class="tm-inline-report-summary-head"><div class="tm-inline-report-kicker">ARTIFACT · 教学成果</div><div class="tm-inline-report-title-row"><div class="tm-inline-report-title"><span class="material-symbols-rounded" aria-hidden="true">' + (isTeachingPackage ? 'auto_stories' : 'summarize') + '</span><strong>' + (isTeachingPackage ? '复习计划' : '考试分析报告') + '</strong></div><span class="tm-inline-report-summary-status">' + (isTeachingPackage ? '草稿 · 待核对' : '已完成') + '</span></div><div class="tm-inline-report-subtitle">' + (isTeachingPackage ? '按用途展开材料，核对后用于课堂 · 学生练习可单独导出' : '基于本次分析使用的班级、考试和资料生成 · 可继续核验或导出') + '</div></div>' +
        '<div class="tm-inline-report-meta">' + (isTeachingPackage ? '<span>教学材料 ' + materialCount + ' 份</span>' : '<span>主要发现 ' + findings + ' 条</span>') + '<span>行动建议 ' + recommendations + ' 条</span>' + (duration ? '<span>本次用时 ' + escapeHtml(duration) + '</span>' : '') + '</div>' +
        reportHtml + qualityHtml + packageActions +
        '</section>';
    }

    function _renderInlineReportQuality(snapshot) {
      var ready = snapshot.dataReady || {};
      var issues = Array.isArray(ready.issues) ? ready.issues : [];
      var quality = ready.quality || {};
      var label = ready.ready === false ? '数据需要处理' : (issues.length ? '数据可用，有 ' + issues.length + ' 项提醒' : '数据检查已完成');
      var qualityFacts = [];
      if (quality.scored_count != null) qualityFacts.push('统计基于 ' + quality.scored_count + ' 名有效成绩');
      if (quality.absent_count) qualityFacts.push(quality.absent_count + ' 名学生缺考');
      if (quality.missing_scores) qualityFacts.push(quality.missing_scores + ' 条到场记录缺少成绩');
      var cls = ready.ready === false ? ' is-error' : (issues.length ? ' is-warning' : ' is-ok');
      var detail = qualityFacts.concat(issues.length
        ? issues.map(function (issue) {
          var text = typeof issue === 'string' ? issue : (issue.message || issue.detail || issue.title || '需要关注的数据问题');
          return text;
        }) : []).map(function (text) { return '<li>' + escapeHtml(text) + '</li>'; }).join('');
      if (!detail) detail = '<li>当前任务使用的数据已通过基础检查。</li>';
      return '<div class="tm-inline-report-quality' + cls + '"><div class="tm-inline-report-quality-head"><span class="material-symbols-rounded" aria-hidden="true">' + (ready.ready === false ? 'error' : (issues.length ? 'warning' : 'check_circle')) + '</span><span>' + escapeHtml(label) + '</span><button type="button" data-act="tm-toggle-inline-quality" aria-expanded="false">查看详情</button></div><ul class="tm-inline-report-quality-detail" hidden>' + detail + '</ul></div>';
    }

    function _renderStructuredAnswer(answer, options) {
      if (!answer || typeof answer !== 'object') return '';
      options = options || {};
      var hideEvidence = options.hideEvidence === true;
      var hideLimitations = options.hideLimitations === true;
      var sentence = function (value, fallback) {
        var text = String(value == null ? '' : value).trim() || String(fallback == null ? '' : fallback).trim();
        if (!text) return '';
        return /[。！？；.!?;]$/.test(text) ? text : text + '。';
      };
      var html = '<div class="tm-structured">';
      if (answer.summary) html += '<div class="tm-struct-summary">' + escapeHtml(answer.summary) + '</div>';
      if (Array.isArray(answer.findings) && answer.findings.length) {
        html += '<div class="tm-struct-section"><h4 class="tm-struct-title">主要发现</h4><div class="tm-finding-list">';
        answer.findings.forEach(function (finding) {
          var confidence = finding.confidence ? '<span class="tm-struct-badge">' + escapeHtml(finding.confidence) + '</span>' : '';
          var findingTitle = finding.title || '发现';
          var findingDetail = finding.description || finding.claim || finding.detail || finding.text || finding.summary || '';
          html += '<article class="tm-finding"><div class="tm-finding-head"><strong>' + escapeHtml(finding.title || '发现') + '</strong>' + confidence + '</div>' +
            '<p>' + escapeHtml(sentence(findingDetail, findingTitle)) + '</p>';
          if (!hideEvidence && Array.isArray(finding.evidence_ids) && finding.evidence_ids.length) {
            html += '<div class="tm-evidence-refs">证据：' + finding.evidence_ids.map(escapeHtml).join('、') + '</div>';
          }
          html += '</article>';
        });
        html += '</div></div>';
      }
      if (Array.isArray(answer.recommendations) && answer.recommendations.length) {
        html += '<div class="tm-struct-section"><h4 class="tm-struct-title">行动建议</h4><ol class="tm-recommendation-list">';
        answer.recommendations.slice().sort(function (a, b) { return (a.priority || 99) - (b.priority || 99); }).forEach(function (rec) {
          html += '<li><strong>' + escapeHtml(rec.action || '') + '</strong>';
          var recommendationText = rec.rationale || rec.description || rec.detail || rec.reason || '';
          if (recommendationText || rec.action) html += '<p>' + escapeHtml(sentence(recommendationText, rec.action)) + '</p>';
          if (!hideEvidence && Array.isArray(rec.supports) && rec.supports.length) html += '<small>依据：' + rec.supports.map(escapeHtml).join('、') + '</small>';
          html += '</li>';
        });
        html += '</ol></div>';
      }
      if (!hideLimitations && Array.isArray(answer.limitations) && answer.limitations.length) {
        html += '<div class="tm-struct-section tm-limitations"><h4 class="tm-struct-title">局限与注意事项</h4><ul class="tm-struct-list">';
        answer.limitations.forEach(function (item) { html += '<li>' + escapeHtml(item) + '</li>'; });
        html += '</ul></div>';
      }
      if (Array.isArray(answer.sections)) {
        answer.sections.forEach(function (sec) {
          html += '<div class="tm-struct-section">';
          if (sec.title) html += '<h4 class="tm-struct-title">' + escapeHtml(sec.title) + '</h4>';
          if (sec.body) html += '<p class="tm-struct-body">' + escapeHtml(sec.body) + '</p>';
          if (Array.isArray(sec.items)) {
            html += '<ul class="tm-struct-list">';
            sec.items.forEach(function (item) { html += '<li>' + escapeHtml(item) + '</li>'; });
            html += '</ul>';
          }
          html += '</div>';
        });
      }
      html += '</div>';
      return html;
    }

    function _formatElapsed(ms) {
      var totalSec = Math.max(0, Math.floor((ms || 0) / 1000));
      var m = Math.floor(totalSec / 60);
      var s = totalSec % 60;
      return m + '分' + (s < 10 ? '0' : '') + s + '秒';
    }

    function _latestUserPrompt(snapshot) {
      var messages = snapshot && Array.isArray(snapshot.messages) ? snapshot.messages : [];
      for (var i = messages.length - 1; i >= 0; i--) {
        var msg = messages[i];
        if (msg && msg.role === 'user') return String(msg.content_text || msg.content || '').trim();
      }
      return '';
    }

    function _selectedModel(snapshot) {
      var models = snapshot && Array.isArray(snapshot.savedModels) ? snapshot.savedModels : [];
      var currentId = String(snapshot && snapshot.currentModelId || '');
      return models.find(function (model) { return String(model.id || '') === currentId; }) || null;
    }

    function _isThinkingModeEnabled(snapshot) {
      var model = _selectedModel(snapshot);
      if (!model || model.thinking_enabled !== true) return false;
      // DeepSeek 原生模型自带 thinking；兼容接口必须同时声明支持推理。
      return model.provider === 'deepseek' || model.supports_reasoning === true;
    }

    function _analysisActivityLabel(snapshot) {
      var state = snapshot && snapshot.runState;
      if (state === 'queued' || state === 'submitting') return state === 'queued' ? '分析任务排队中' : '正在提交分析任务';
      if (state === 'waiting_confirmation') return '等待教师确认';
      var timeline = snapshot && Array.isArray(snapshot.timeline) ? snapshot.timeline : [];
      for (var i = timeline.length - 1; i >= 0; i--) {
        var item = timeline[i];
        if (!item || item.state === 'done') continue;
        if (item.kind === 'tool') return '正在读取数据 · ' + (item.label || '调用分析工具');
        if (item.kind === 'validate') return '正在校验分析结果';
        if (item.kind === 'model') return '正在分析 · 生成回复中';
      }
      return '正在分析 · 生成回复中';
    }

    // 分析中只保留一个低干扰的等待提示，避免大面积多行骨架喧宾夺主。
    function _renderThinkingWait() {
      return '<div class="tm-thinking-wait" aria-hidden="true">' +
        '<span class="tm-thinking-wait-dots"><i></i><i></i><i></i></span>' +
        '<span>正在组织回答</span>' +
      '</div>';
    }

    // 状态胶囊：阶段与计时拆成两个节点，计时器只原地更新 #tmRunningElapsed，
    // 阶段标签（读取数据/校验/生成回复）不会被覆盖丢失。
    function _renderStatusPill(stage, elapsedLabel) {
      return '<div class="tm-thinking-status">' +
        '<span class="tm-running-dot" aria-hidden="true"></span>' +
        '<span class="tm-running-stage" id="tmRunningStage">' + escapeHtml(stage) + '</span>' +
        '<span class="tm-running-sep" aria-hidden="true">·</span>' +
        '<span class="tm-running-elapsed" id="tmRunningElapsed">' + escapeHtml(elapsedLabel) + '</span>' +
      '</div>';
    }

    function _renderRunningIndicator(snapshot) {
      var state = snapshot.runState;
      // 普通聊天不展示结构化分析步骤，保持轻量的回复中提示。
      if ((snapshot.progressMode || 'light') !== 'full') {
        return '<div class="tm-running-indicator" id="tmRunning" role="status" aria-live="polite" aria-atomic="true">' +
          '<span class="tm-running-dot" aria-hidden="true"></span><span>正在回复</span></div>';
      }
      var elapsedLabel = '已用时 ' + _formatElapsed(snapshot.elapsedMs || 0);
      var timeline = Array.isArray(snapshot.timeline) ? snapshot.timeline : [];
      var current = null;
      for (var i = timeline.length - 1; i >= 0; i--) {
        if (timeline[i] && timeline[i].state === 'running') { current = timeline[i]; break; }
      }
      var connection = snapshot.progressConnection || 'connected';
      var connectionText = connection === 'polling'
        ? '实时连接暂时中断，已切换备用通道'
        : (connection === 'slow' ? '响应较慢，仍在等待模型返回' : '连接正常，分析仍在进行');

      // 没有真实步骤事件时只显示“分析中”。run.started 仅表示任务生命周期
      // 开始，不能被渲染成一条虚构的工作流；等后端发来真实 tool/model/step
      // 事件后，再显示过程消息卡。
      if (!timeline.length) {
        return '<article class="tm-analysis-progress tm-generating-process tm-thinking-process tm-analysis-only" id="tmRunning" data-testid="tm-thinking" role="status" aria-live="polite" aria-atomic="false">' +
          '<div class="tm-analysis-progress-head"><div class="tm-thinking-agent">' +
            _teachMateIconMarkup('tm-brand-icon tm-brand-icon-avatar', 'TeachMate AI') +
            '<strong>TeachMate 正在分析</strong><span class="tm-progress-badge">RUNNING</span></div>' +
            (snapshot.currentRunId
              ? '<button type="button" class="tm-progress-cancel" data-act="tm-cancel">取消任务</button>'
              : '') +
          '</div>' +
          '<div class="tm-analysis-only-status"><span class="tm-proc-halo" aria-hidden="true"></span><strong>分析中</strong></div>' +
          '<div class="tm-proc-foot"><span></span><span class="tm-proc-elapsed" id="tmRunningElapsed">' + escapeHtml(elapsedLabel) + '</span></div>' +
        '</article>';
      }

      // 运行中与终态共用过程块（_processBlockHtml + _mountProcBlock），
      // 行节点跨重绘复用，扫光/三态点动画不随重绘重启。
      // 保留 tmRunningElapsed / tmRunningConnection：计时器与连接状态由
      // state 层原地更新这两个节点，不经过全量重绘。
      var currentNext = current && current.nextAction ? current.nextAction : '';
      return '<article class="tm-analysis-progress tm-generating-process tm-thinking-process" id="tmRunning" data-testid="tm-thinking" role="status" aria-live="polite" aria-atomic="false">' +
        '<div class="tm-analysis-progress-head"><div class="tm-thinking-agent">' +
          _teachMateIconMarkup('tm-brand-icon tm-brand-icon-avatar', 'TeachMate AI') +
          '<strong>TeachMate 正在分析</strong><span class="tm-progress-badge">RUNNING</span></div>' +
          (snapshot.currentRunId
            ? '<button type="button" class="tm-progress-cancel" data-act="tm-cancel">取消任务</button>'
            : state === 'submitting'
              ? '<button type="button" class="tm-progress-cancel" disabled title="任务正在创建">准备中…</button>'
              : '') +
        '</div>' +
        _processBlockHtml(timeline, true, snapshot.currentRunId || snapshot.currentSessionId) +
        (currentNext ? '<div class="tm-proc-next">下一步：' + escapeHtml(currentNext) + '</div>' : '') +
        '<div class="tm-proc-foot"><span class="tm-proc-running-meta"><span class="tm-proc-halo" aria-hidden="true"></span><span id="tmRunningStage">' + escapeHtml(current ? (current.text || current.label || '正在处理') : '正在回复') + '</span><span class="tm-running-sep" aria-hidden="true">·</span><span id="tmRunningConnection" class="tm-proc-connection' + (connection !== 'connected' ? ' is-slow' : '') + '">' + escapeHtml(connectionText) + '</span></span><span class="tm-proc-elapsed" id="tmRunningElapsed">' + escapeHtml(elapsedLabel) + '</span></div>' +
      '</article>';
    }

    function _renderBudgetConfirmation(snapshot) {
      return '<section class="tm-budget-card tm-confirm-continue-card" role="alert" aria-live="assertive">' +
        '<div class="tm-budget-icon"><span class="material-symbols-rounded">play_circle</span></div>' +
        '<div class="tm-budget-content"><strong>任务已准备好</strong>' +
        '<p>确认后会继续执行当前分析，你也可以取消本次任务。</p>' +
        '<div class="tm-budget-actions"><button data-act="tm-cancel">取消任务</button>' +
        '<button class="tm-budget-confirm" data-act="tm-confirm-budget">确认并继续</button></div></div></section>';
    }

    function _renderErrorBar(error) {
      return '<div class="tm-error-bar" id="tmErrorBar" role="alert" aria-live="assertive">' +
        '<span class="material-symbols-rounded">error</span>' +
        '<span>' + escapeHtml(error) + '</span>' +
        (teachMateState.getSnapshot().canRetry ? '<button class="tm-retry-btn" data-act="tm-retry">重试</button>' : '') +
        '</div>';
    }

    function _renderInputArea(snapshot) {
      var unavailableReason = _teachMateUnavailableReason(snapshot.providerInfo);
      var unavailable = !!unavailableReason && !!snapshot.providerInfo;
      var pendingGroup = snapshot.analysisGroup && String(snapshot.analysisGroup.status || '') === 'waiting_confirmation'
        ? snapshot.analysisGroup : null;
      var composerLocked = snapshot.isRunning || !!pendingGroup;
      var disabled = composerLocked ? ' disabled' : '';
      // 模型列表完全来自“班级与设置 → 模型”返回的已保存档案，不在聊天区写死示例模型。
      var savedModels = Array.isArray(snapshot.savedModels) ? snapshot.savedModels : [];
      var currentModelId = String(snapshot.currentModelId || (snapshot.providerInfo && snapshot.providerInfo.profile_id) || '');
      var currentModel = currentModelId
        ? (savedModels.find(function (model) { return String(model.id) === currentModelId; }) || null)
        : null;
      var modelRows = savedModels;
      var modelMenuItems = modelRows.map(function (model) {
        var selected = currentModel && String(model.id) === String(currentModel.id) ? ' is-selected' : '';
        var displayName = model.display_name || model.model_name || '未命名模型';
        var thinkingMode = model.thinking_enabled === true && (model.provider === 'deepseek' || model.supports_reasoning === true);
        var secondary = (model.model_name || model.provider || '') + (thinkingMode ? ' · 深度思考' : ' · 普通回复');
        return '<button type="button" class="tm-model-option' + selected + '" data-act="tm-model-option" data-model-id="' + escapeAttr(String(model.id)) + '" data-model-name="' + escapeAttr(displayName) + '"><span class="tm-model-option-name">' + escapeHtml(displayName) + '</span><span class="tm-model-option-rate">' + escapeHtml(secondary) + '</span></button>';
      }).join('');
      if (!modelMenuItems) modelMenuItems = '<div class="tm-model-empty-state">暂无已配置模型，请先在设置中添加</div>';
      var currentModelName = currentModel ? (currentModel.display_name || currentModel.model_name || '未命名模型') : '未配置模型';
      var modelPicker = '<div class="tm-model-picker' + (composerLocked ? ' is-disabled' : '') + '">' +
        '<button type="button" class="tm-model-trigger" data-act="tm-model-toggle" aria-expanded="false" aria-haspopup="listbox" aria-label="选择模型"' + (composerLocked ? ' disabled' : '') + '>' +
          '<span class="tm-model-label">模型</span><span class="tm-model-current">' + escapeHtml(currentModelName) + '</span><span class="material-symbols-rounded tm-model-chevron" aria-hidden="true">expand_more</span>' +
        '</button>' +
        '<div class="tm-model-menu" id="tmModelMenu" role="listbox" hidden>' + modelMenuItems + '<div class="tm-model-menu-divider"></div><button type="button" class="tm-model-custom" data-act="tm-model-custom"><span class="material-symbols-rounded" aria-hidden="true">edit</span><span>配置自定义模型</span></button></div>' +
      '</div>';
      var sendBtn = snapshot.isRunning
        ? '<button class="tm-send-btn tm-cancel-btn" data-act="tm-cancel" title="' + (snapshot.currentRunId ? '取消任务' : '任務正在创建') + '" aria-label="' + (snapshot.currentRunId ? '取消任务' : '任务正在创建') + '"' + (snapshot.currentRunId ? '' : ' disabled') + '><span class="material-symbols-rounded">stop</span></button>'
        : pendingGroup
          ? '<button class="tm-send-btn" title="请先确认或修改执行方案" aria-label="等待确认执行方案" disabled><span class="material-symbols-rounded">schedule</span></button>'
        : unavailable
          ? '<button class="tm-send-btn" title="' + escapeAttr(unavailableReason) + '" aria-label="AI 分析不可用" disabled><span class="material-symbols-rounded">send</span></button>'
          : '<button class="tm-send-btn" data-act="tm-send" title="发送" aria-label="发送"><span class="material-symbols-rounded">send</span></button>';
      var attachChips = '';
      var pending = snapshot.pendingAttachments || [];
      if (pending.length) {
        attachChips = '<div class="tm-composer-files"><div class="tm-file-section-label"><span class="material-symbols-rounded" aria-hidden="true">attach_file</span><span>已添加文件</span><span class="tm-file-section-count">' + pending.length + '</span></div><div class="tm-attach-chips" role="list" aria-label="待发送附件">' + pending.map(function (a) {
          var status = a.status || 'ready';
          var statusLabel, statusIcon;
          if (status === 'uploading') { statusLabel = '上传中 ' + (a.progress || 0) + '%'; statusIcon = 'upload'; }
          else if (status === 'validating') { statusLabel = '校验中'; statusIcon = 'fact_check'; }
          else if (status === 'pending_ocr') { statusLabel = '待 OCR'; statusIcon = 'document_scanner'; }
          else if (status === 'parsing') { statusLabel = '解析中'; statusIcon = 'progress_activity'; }
          else if (status === 'error') { statusLabel = '处理失败'; statusIcon = 'error'; }
          else { statusLabel = '可发送'; statusIcon = 'check_circle'; }
          var fileName = a.originalName || a.title || ('附件 #' + a.attachmentId);
          var ext = String(fileName).split('.').pop().toLowerCase();
          var fileIcon = ext === 'pdf' ? 'picture_as_pdf' : (ext === 'xlsx' || ext === 'xls' || ext === 'csv') ? 'table' : (ext === 'docx' || ext === 'doc') ? 'article' : (ext === 'png' || ext === 'jpg' || ext === 'jpeg') ? 'image' : 'description';
          var removeTitle = status === 'uploading' ? '取消上传' : '移除附件';
          var progressBar = status === 'uploading'
            ? '<span class="tm-attach-progress" role="progressbar" aria-valuenow="' + (a.progress || 0) + '" aria-valuemin="0" aria-valuemax="100"><span class="tm-attach-progress-bar" style="width:' + (a.progress || 0) + '%"></span></span>'
            : '';
          return '<span class="tm-attach-chip tm-file-chip-' + escapeAttr(ext) + ' tm-attach-chip-' + escapeAttr(status) + '" role="listitem"><span class="tm-file-type-icon material-symbols-rounded" aria-hidden="true">' + fileIcon + '</span><span class="tm-attach-chip-title">' + escapeHtml(fileName) + '</span>' + progressBar + '<span class="tm-attach-chip-status"><span class="material-symbols-rounded" aria-hidden="true">' + statusIcon + '</span>' + escapeHtml(statusLabel) + '</span><button type="button" class="tm-attach-chip-remove" data-act="tm-attach-remove" data-attachment-id="' + escapeAttr(String(a.attachmentId)) + '" aria-label="' + escapeAttr(removeTitle) + '" title="' + escapeAttr(removeTitle) + '"><span class="material-symbols-rounded" aria-hidden="true">close</span></button></span>';
        }).join('') + '</div></div>';
      }
      var currentSession = (snapshot.sessions || []).find(function (session) { return session.id === snapshot.currentSessionId; });
      var currentClassName = '';
      if (snapshot.selectedClassName !== null && snapshot.selectedClassName !== undefined) {
        currentClassName = typeof normalizeClassFilter === 'function'
          ? normalizeClassFilter(snapshot.selectedClassName)
          : String(snapshot.selectedClassName || '');
      } else if (currentSession && currentSession.class_id) {
        currentClassName = (snapshot.contextNames && snapshot.contextNames.class && snapshot.contextNames.class[currentSession.class_id]) || ('班级 #' + currentSession.class_id);
      } else if (typeof normalizeClassFilter === 'function') {
        currentClassName = normalizeClassFilter(dashboardClass);
      }
      var classes = typeof getAvailableClasses === 'function' ? getAvailableClasses() : ((state && Array.isArray(state.classes)) ? state.classes : []);
      var classOptionValue = String(currentClassName || '').replace(/班$/, '');
      var classOptions = '<button type="button" class="tm-class-option' + (!currentClassName ? ' is-selected' : '') + '" data-act="tm-class-option" data-class-name=""><span>全部班级</span>' + (!currentClassName ? '<span class="material-symbols-rounded">check</span>' : '') + '</button>';
      classOptions += classes.map(function (className) {
        var selected = String(className) === classOptionValue ? ' is-selected' : '';
        return '<button type="button" class="tm-class-option' + selected + '" data-act="tm-class-option" data-class-name="' + escapeAttr(String(className)) + '"><span>' + escapeHtml(formatClassLabel(className)) + '</span>' + (selected ? '<span class="material-symbols-rounded">check</span>' : '') + '</button>';
      }).join('');
      // 空字符串是教师明确选择“全部班级”，null 才表示尚未选择；两者
      // 在查询范围上相同，但按钮文案必须区分，避免选择后仍显示占位词。
      var classExplicitlySelected = snapshot.selectedClassName !== null && snapshot.selectedClassName !== undefined;
      var classLabel = currentClassName ? formatClassLabel(currentClassName) : (classExplicitlySelected ? '全部班级' : '选择班级');
      var classPicker = '<div class="tm-scope-picker-wrap tm-class-picker-wrap"><button type="button" class="tm-scope-picker" data-act="tm-class-toggle" aria-expanded="false" aria-haspopup="listbox" title="选择班级"' + (composerLocked ? ' disabled' : '') + '><span class="material-symbols-rounded" aria-hidden="true">groups</span><span class="tm-scope-picker-label">' + escapeHtml(classLabel) + '</span><span class="material-symbols-rounded tm-scope-picker-chevron" aria-hidden="true">expand_more</span></button><div class="tm-class-menu" id="tmClassMenu" role="listbox" hidden>' + classOptions + '</div></div>';
      var databaseMode = typeof DATABASE_MODE !== 'undefined' && DATABASE_MODE;
      var currentTermMatches = snapshot.availableExamsTermId == null || Number(snapshot.availableExamsTermId) === Number(currentTermId);
      var databaseExams = Array.isArray(snapshot.availableExams) && currentTermMatches ? snapshot.availableExams : [];
      var workspaceExams = Array.isArray(state.exams) ? state.exams : [];
      // 当前工作区状态是用户实际正在使用的考试上下文；数据库考试表可能保留历史导入记录。
      // 这里优先展示 workspace state，发送时仍通过 source_key 映射回数据库数字 ID。
      var exams = (databaseMode ? (workspaceExams.length ? workspaceExams : databaseExams) : (databaseExams.length ? databaseExams : workspaceExams)).slice();
      var examKey = function (exam) { return String(exam.source_key || exam.sourceKey || exam.id || ''); };
      var seenExamKeys = Object.create(null);
      exams = exams.filter(function (exam) {
        if (!exam || !examKey(exam)) return false;
        var key = examKey(exam);
        if (seenExamKeys[key]) return false;
        seenExamKeys[key] = true;
        return !exam.status || exam.status === 'active';
      });
      var selectedExamId = snapshot.examSelectionTouched
        ? String(snapshot.selectedExamId || '')
        : String(snapshot.selectedExamId || (currentSession && currentSession.exam_id ? currentSession.exam_id : ''));
      var selectedExam = exams.find(function (exam) { return examKey(exam) === selectedExamId || String(exam.id) === selectedExamId; }) || null;
      var examMenuItems = '<button type="button" class="tm-exam-option' + (!selectedExam ? ' is-selected' : '') + '" data-act="tm-exam-option" data-exam-id=""><span class="tm-exam-option-name">不选择考试</span><span class="tm-exam-option-hint">仅使用文字或文件</span></button>';
      if (exams.length) {
        examMenuItems += exams.sort(function (left, right) {
          return String(right.date || '').localeCompare(String(left.date || '')) || String(right.name || '').localeCompare(String(left.name || ''));
        }).map(function (exam) {
          var label = exam.name || '未命名考试';
          var examKind = exam.exam_kind || exam.examKind || '';
          var meta = exam.date || exam.exam_date || (examKind === 'entrance' ? '入学考试' : '数据库考试');
          var optionKey = examKey(exam);
          var selected = selectedExam && (optionKey === selectedExamId || String(exam.id) === selectedExamId) ? ' is-selected' : '';
          return '<button type="button" class="tm-exam-option' + selected + '" data-act="tm-exam-option" data-exam-id="' + escapeAttr(optionKey) + '"><span class="tm-exam-option-name">' + escapeHtml(label) + '</span><span class="tm-exam-option-hint">' + escapeHtml(meta) + '</span></button>';
        }).join('');
      } else {
        examMenuItems += '<div class="tm-exam-empty-state">当前学期暂无数据库考试</div>';
      }
      var examLabel = selectedExam ? (selectedExam.name || '已选择考试') : '选择考试';
      if (!selectedExam && currentSession && currentSession.exam_id && !snapshot.examSelectionTouched) examLabel = (snapshot.contextNames && snapshot.contextNames.exam && snapshot.contextNames.exam[currentSession.exam_id]) || ('考试 #' + currentSession.exam_id);
      var examMenu = '<div class="tm-exam-menu" id="tmExamMenu" role="listbox" hidden>' + examMenuItems + '</div>';
      var selectedPluginId = String(snapshot.selectedPluginId || '');
      var availablePlugins = typeof tmPluginDefinitions === 'function' ? tmPluginDefinitions().filter(function (plugin) { return plugin.enabled && plugin.available; }) : [];
      var selectedPlugin = availablePlugins.find(function (plugin) { return plugin.id === selectedPluginId; }) || null;
      var pluginItems = availablePlugins.length
        ? availablePlugins.map(function (plugin) {
          var icon = typeof tmPluginIconPath === 'function' ? tmPluginIconPath(plugin) : '/workbench-assets/plugin-icons/spark.svg';
          return '<button type="button" class="tm-plugin-option' + (plugin.id === selectedPluginId ? ' is-selected' : '') + '" data-act="tm-plugin-option" data-plugin-id="' + escapeAttr(plugin.id) + '"><img src="' + escapeAttr(icon) + '" alt=""><span><strong>' + escapeHtml(plugin.name) + '</strong><small>' + escapeHtml(plugin.desc || '调用该插件能力') + '</small></span></button>';
        }).join('')
        : '<div class="tm-plugin-empty">暂无已启用插件</div>';
      var pluginMenu = '<div class="tm-plugin-menu tm-tool-submenu" id="tmPluginMenu" hidden>' + pluginItems + '</div>';
      var toolMenu = '<div class="tm-import-menu" id="tmImportMenu" hidden><button data-act="tm-attach"><span class="material-symbols-rounded">upload_file</span><span>导入文件</span></button><button data-act="tm-library"><span class="material-symbols-rounded">folder_open</span><span>从资料库导入</span></button><button data-act="tm-plus-select-plugin"><span class="material-symbols-rounded">extension</span><span>选择调用插件</span></button></div>';
      var toolPicker = '<div class="tm-import-group"><button class="tm-composer-plus" data-act="tm-composer-plus" title="工具" aria-label="打开工具菜单" aria-expanded="false"' + (composerLocked ? ' disabled' : '') + '><span class="material-symbols-rounded">add</span><span>工具</span><span class="material-symbols-rounded tm-import-chevron">expand_more</span></button>' + toolMenu + pluginMenu + '</div>';
      var examPicker = '<div class="tm-scope-picker-wrap tm-exam-picker-wrap"><button type="button" class="tm-scope-picker" data-act="tm-exam-toggle" aria-expanded="false" aria-haspopup="listbox" title="选择考试"' + (composerLocked ? ' disabled' : '') + '><span class="material-symbols-rounded" aria-hidden="true">assignment</span><span class="tm-scope-picker-label">' + escapeHtml(examLabel) + '</span><span class="material-symbols-rounded tm-scope-picker-chevron" aria-hidden="true">expand_more</span></button>' + examMenu + '</div>';
      var selectedPluginChip = selectedPlugin
        ? '<div class="tm-composer-selection-row"><button type="button" class="tm-plugin-chip" data-act="tm-plugin-clear" title="取消插件选择"><img src="' + escapeAttr(typeof tmPluginIconPath === 'function' ? tmPluginIconPath(selectedPlugin) : '/workbench-assets/plugin-icons/spark.svg') + '" alt=""><span>调用 ' + escapeHtml(selectedPlugin.name) + '</span><span class="material-symbols-rounded" aria-hidden="true">close</span></button></div>'
        : '';
      var planConfirmation = pendingGroup
        ? '<div class="tm-plan-confirm" role="dialog" aria-label="确认学生诊断执行方案" aria-live="polite"><div class="tm-plan-confirm-icon"><span class="material-symbols-rounded" aria-hidden="true">fact_check</span></div><div class="tm-plan-confirm-copy"><strong>执行方案已准备好，是否开始？</strong><span>将为 ' + Number(pendingGroup.requested_student_count || 0) + ' 名学生生成画像' + (pendingGroup.estimated_tokens != null ? '，预计约 ' + Number(pendingGroup.estimated_tokens).toLocaleString('zh-CN') + ' tokens' : '') + '。确认前不会运行。</span></div><div class="tm-plan-confirm-actions"><button type="button" class="tm-plan-confirm-secondary" data-act="tm-batch-edit" data-group-id="' + escapeAttr(String(pendingGroup.id || '')) + '">修改范围</button><button type="button" class="tm-plan-confirm-secondary is-cancel" data-act="tm-batch-cancel" data-group-id="' + escapeAttr(String(pendingGroup.id || '')) + '">取消</button><button type="button" class="tm-plan-confirm-primary" data-act="tm-batch-confirm" data-group-id="' + escapeAttr(String(pendingGroup.id || '')) + '">确认执行</button></div></div>'
        : '';
      var scopeControls = classPicker + examPicker;
      return '<div class="tm-input-area">' + planConfirmation + '<div class="tm-composer">' + attachChips +
        selectedPluginChip + '<div class="tm-composer-input-row"><textarea class="tm-input" id="tmInput" placeholder="给 TeachMate 发送消息…" rows="1" data-act="tm-input" aria-label="消息输入框"' + disabled + '></textarea></div>' +
        '<div class="tm-composer-toolbar"><div class="tm-composer-leading">' + toolPicker + scopeControls + '</div><div class="tm-composer-actions">' + modelPicker + sendBtn + '</div></div></div>' +
        '<div class="tm-input-hint">' + (unavailable ? '<span>模型暂不可用，可以先编辑问题。</span><button type="button" class="tm-settings-quiet" data-act="tm-model-custom">配置模型</button>' : '<span class="material-symbols-rounded">verified_user</span>TeachMate 的结论仅作教学参考，请核实重要信息') + '</div></div>';
    }
