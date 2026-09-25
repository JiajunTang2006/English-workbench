// ================= TeachMate 首次设置与数据就绪检查 =================
    // 职责：
    //   1. 模型状态概要（Provider、API Key 是否配置、能力开关）
    //   2. 数据就绪检查（缺少考试/成绩/题目/附件时给出可执行修复入口）
    //   3. 预算上限提示
    // 依赖：teachMateApi、teachMateState。

    const teachMateOnboarding = (function () {
      function _escape(v) {
        return (typeof escapeHtml === 'function') ? escapeHtml(v) : String(v == null ? '' : v);
      }

      /** 连接测试：读取 provider 配置成功即判定可达（不触碰真实 API Key）。 */
      async function runConnectionTest() {
        try {
          var info = await teachMateApi.getProviderInfo();
          return { ok: true, info: info };
        } catch (e) {
          return { ok: false, error: (e && e.message) || String(e) };
        }
      }

      /**
       * 渲染首次设置卡片（欢迎页顶部 / 独立弹层均可复用）。
       * @param {object} snapshot - teachMateState snapshot
       * @returns {string} HTML
       */
      function renderSetupCard(snapshot) {
        snapshot = snapshot || (typeof teachMateState !== 'undefined' ? teachMateState.getSnapshot() : {});
        var provider = snapshot.providerInfo;

        // Provider 状态：未配置 / 已配置但关闭 / 就绪
        var statusCls = 'error', statusLabel = '未配置', statusIcon = 'error';
        if (provider) {
          if (!provider.api_key_configured) {
            statusCls = 'error'; statusLabel = '缺少 API Key'; statusIcon = 'key_off';
          } else if (!provider.agent_enabled || !provider.text_agent_enabled) {
            statusCls = 'warning'; statusLabel = 'Agent 能力未开启'; statusIcon = 'pause_circle';
          } else {
            statusCls = 'ok'; statusLabel = '已就绪'; statusIcon = 'check_circle';
          }
        }

        var budgetNote = '';
        if (snapshot.budgetLimit) {
          budgetNote = '<div class="tm-setup-row"><span class="tm-setup-label">预算上限</span>' +
            '<span class="tm-setup-value">¥' + _escape(Number(snapshot.budgetLimit).toFixed(2)) + ' / 分析</span></div>';
        }

        var modelRow = provider
          ? '<div class="tm-setup-row"><span class="tm-setup-label">模型</span>' +
            '<span class="tm-setup-value">' + _escape(provider.display_name || provider.provider || '') +
            (provider.model_name ? ' · ' + _escape(provider.model_name) : '') + '</span></div>'
          : '';

        var readiness = snapshot.dataReady;
        var readinessLabel = !readiness
          ? '待检查'
          : readiness.ready
            ? '数据已就绪'
            : (readiness.issues || []).map(function (issue) { return issue.message; }).join('、') || '数据未就绪';

        return '<section class="tm-setup-card tm-setup-' + statusCls + '" data-testid="setup-card" aria-label="AI 分析配置状态">' +
          '<div class="tm-setup-head">' +
          '<span class="tm-setup-icon material-symbols-rounded" aria-hidden="true">' + statusIcon + '</span>' +
          '<div><strong>AI 分析配置</strong>' +
          '<span class="tm-setup-status">' + _escape(statusLabel) + '</span></div>' +
          '<button type="button" class="tm-setup-test-btn" data-act="tm-setup-test" title="检查后端配置是否可达">连接测试</button>' +
          '</div>' +
          '<div class="tm-setup-body">' +
          (modelRow || '') +
          (budgetNote) +
          '<div class="tm-setup-row"><span class="tm-setup-label">数据依赖</span>' +
          '<span class="tm-setup-value">' + _escape(readinessLabel) + '</span></div>' +
          '</div>' +
          '<div class="tm-setup-help">' +
          '<span class="material-symbols-rounded" aria-hidden="true">info</span>' +
          '<span>API Key 通过环境变量管理，不会存入本地数据库；不存在时 TeachMate 仍然可以浏览数据。</span></div>' +
          '</section>';
      }

      /**
       * 数据就绪检查：返回 { ready, issues: [{key, message, action}] }。
       * @param {object} snapshot
       * @returns {Promise<object>}
       */
      async function checkDataReadiness(snapshot) {
        snapshot = snapshot || (typeof teachMateState !== 'undefined' ? teachMateState.getSnapshot() : {});
        var issues = [];
        var quality = null;
        var termId = (typeof currentTermId !== 'undefined' && currentTermId != null) ? currentTermId : (snapshot.termId || null);

        // 1. 是否有班级
        try {
          var classes = await teachMateApi.listClasses(termId);
          if (!classes || !classes.length) {
            issues.push({ key: 'classes', blocking: true, message: '当前学期还没有班级', action: '导入名单', actionAct: 'tm-import-classes' });
          }
        } catch (e) { issues.push({ key: 'classes', blocking: true, message: '无法读取班级数据', action: '重试', actionAct: 'tm-data-retry' }); }

        // 2. 是否有考试
        try {
          var exams = await teachMateApi.listExams(termId);
          if (!exams || !exams.length) {
            issues.push({ key: 'exams', blocking: true, message: '当前学期还没有考试记录', action: '去成绩面板', actionAct: 'tm-open-exams' });
          }

          // 有明确选择时，继续检查“这场考试 + 这个班级”的实际数据，
          // 避免只因为存在考试记录就显示“数据已就绪”。
          var currentSession = (snapshot.sessions || []).find(function (item) {
            return String(item && item.id) === String(snapshot.currentSessionId);
          }) || {};
          var selectedExamKey = snapshot.examSelectionTouched
            ? snapshot.selectedExamId
            : (currentSession.exam_id || '');
          if (selectedExamKey && exams && exams.length && typeof teachMateApi.getExamSummary === 'function') {
            var matchedExam = exams.find(function (exam) {
              return String(exam.source_key || exam.sourceKey || exam.id || '') === String(selectedExamKey)
                || String(exam.id || '') === String(selectedExamKey);
            });
            if (!matchedExam) {
              issues.push({ key: 'selected_exam', blocking: true, message: '当前选择的考试不属于本学期，无法开始分析', action: '重新选择', actionAct: 'tm-plus-select-exam' });
            } else {
              var classId = null;
              if (snapshot.selectedClassName) {
                if (typeof teachMateApi.findClassIdByName === 'function') {
                  classId = await teachMateApi.findClassIdByName(snapshot.selectedClassName, termId);
                }
                if (!classId) {
                  issues.push({ key: 'selected_class', blocking: true, message: '当前选择的班级不存在或不属于本学期', action: '重新选择', actionAct: 'tm-plus-select-class' });
                }
              }
              if (!issues.some(function (issue) { return issue.key === 'selected_class'; })) {
                try {
                  var summary = await teachMateApi.getExamSummary(matchedExam.id, termId, classId);
                  var absent = Number(summary && summary.absent_count || 0);
                  var missing = Number(summary && summary.missing_score_count || 0);
                  var scored = Number(summary && summary.present_count || 0);
                  quality = { scored_count: scored, absent_count: absent, missing_scores: missing };
                  if (!scored && !absent) {
                    issues.push({ key: 'scores', blocking: true, message: '当前选择范围还没有可用成绩', action: '去成绩面板', actionAct: 'tm-open-exams' });
                  }
                  if (absent > 0) issues.push({ key: 'absent', blocking: false, message: '本次考试有 ' + absent + ' 名学生缺考，统计将按有效成绩计算', action: '查看详情', actionAct: 'tm-open-exams' });
                  if (missing > 0) issues.push({ key: 'missing_scores', blocking: false, message: '有 ' + missing + ' 条到场记录缺少成绩，统计将按有效成绩计算', action: '查看详情', actionAct: 'tm-open-exams' });
                } catch (summaryError) {
                  issues.push({ key: 'score_summary', blocking: true, message: '无法读取当前考试范围的数据质量', action: '重试', actionAct: 'tm-data-retry' });
                }
              }
            }
          }
        } catch (e) { issues.push({ key: 'exams', blocking: true, message: '无法读取考试数据', action: '重试', actionAct: 'tm-data-retry' }); }

        return { ready: !issues.some(function (issue) { return issue.blocking !== false; }), issues: issues, quality: quality };
      }

      /** 渲染数据就绪检查结果。 */
      function renderReadinessResult(result) {
        if (!result) return '';
        if (result.ready) {
          return '<div class="tm-ready-banner" data-testid="readiness-ok" role="status">' +
            '<span class="material-symbols-rounded" aria-hidden="true">verified</span>' +
            '<span>班级与考试数据已就绪，可以开始分析。</span></div>';
        }
        var items = result.issues.map(function (issue) {
          return '<div class="tm-issue-row">' +
            '<span class="material-symbols-rounded" aria-hidden="true">error</span>' +
            '<span class="tm-issue-message">' + _escape(issue.message) + '</span>' +
            '<button type="button" class="tm-issue-action" data-act="' + _escape(issue.actionAct || 'tm-data-retry') + '">' + _escape(issue.action || '处理') + '</button>' +
            '</div>';
        }).join('');
        return '<div class="tm-readiness-card" data-testid="readiness-issues" role="alert">' +
          '<div class="tm-readiness-title"><span class="material-symbols-rounded" aria-hidden="true">rule</span>数据就绪检查</div>' + items + '</div>';
      }

      return {
        renderSetupCard: renderSetupCard,
        checkDataReadiness: checkDataReadiness,
        renderReadinessResult: renderReadinessResult,
        runConnectionTest: runConnectionTest,
      };
    })();

    if (typeof window !== 'undefined') window.teachMateOnboarding = teachMateOnboarding;
