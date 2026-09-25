// ================= TeachMate 证据检查器 =================
    // 职责：证据明细渲染（来源、计算公式、分子/分母、规则版本），
    //       单个证据详情弹层（供证据 ID 引用跳转）。
    // 依赖：teachMateApi.getRunEvidence、全局 openModal / showToast。

    const teachMateEvidence = (function () {
      function _escape(v) {
        return (typeof escapeHtml === 'function') ? escapeHtml(v) : String(v == null ? '' : v);
      }

      /**
       * 渲染单个证据卡（右栏迷你列表用）。
       * @param {object} e - EvidenceRead
       * @returns {string} HTML
       */
      function renderEvidenceMini(e) {
        if (!e) return '';
        var typeLabel = e.evidence_type || '查询';
        var strength = _strengthLabel(e);
        var sourceLabel = _sourceLabel(e.source_entity, e.source_field, e.source_page, e.source_question_no);
        return '<div class="tm-evidence-mini tm-evidence-mini-' + _escape(strength.cls) + '" ' +
          'data-act="tm-view-evidence-item" data-evidence-id="' + _escape(e.evidence_id || '') + '" ' +
          'data-run-id="' + _escape(String(e.run_id || e.analysis_run_id || '')) + '" tabindex="0" role="button" ' +
          'aria-label="查看证据详情">' +
          '<div class="tm-evidence-mini-head">' +
          '<span class="tm-evidence-mini-type">' + _escape(typeLabel) + '</span>' +
          '<span class="tm-evidence-mini-badge" title="' + _escape(strength.title) + '">' + _escape(strength.label) + '</span>' +
          '</div>' +
          '<div class="tm-evidence-mini-query">' + _escape(e.display_summary || '本次分析使用的事实') + '</div>' +
          (sourceLabel ? '<div class="tm-evidence-mini-source"><span class="material-symbols-rounded" aria-hidden="true">database</span>' + _escape(sourceLabel) + '</div>' : '') +
          '</div>';
      }

      function _sourceLabel(entity, field, page, questionNo) {
        var source = String(entity || '').trim();
        if (!source && !field && !page && !questionNo) return '';
        if (source.indexOf('attachment:') === 0) source = '本次上传资料';
        else if (source === 'exam_scores' || source === 'exam_score') source = '考试成绩数据';
        else if (source === 'question_statistics') source = '考试题目统计';
        else if (source === 'student_profile') source = '学生历史成绩';
        else if (source.indexOf('_') >= 0) source = source.replace(/_/g, ' ');
        var suffix = [];
        if (field) suffix.push(String(field));
        if (page) suffix.push('第' + page + '页');
        if (questionNo) suffix.push('第' + questionNo + '题');
        return source + (suffix.length ? ' · ' + suffix.join(' · ') : '');
      }

      function _strengthLabel(e) {
        // 计算类证据给出分子/分母与比率；查询类给出来源描述
        var hasCalc = (e.numerator !== null && e.numerator !== undefined) || (e.denominator !== null && e.denominator !== undefined) || e.calculation_formula;
        var hasSource = !!e.source_entity || !!e.source_field || !!e.source_page || !!e.source_question_no;
        if (hasCalc) return { cls: 'calc', label: '计算', title: '包含计算公式' };
        if (hasSource) return { cls: 'sourced', label: '有来源', title: '包含数据来源' };
        return { cls: 'plain', label: '查询', title: '查询类证据' };
      }

      /**
       * 证据明细 HTML（弹层内容）。
       * @param {object} e - EvidenceRead
       * @returns {string} HTML
       */
      function renderEvidenceDetail(e) {
        if (!e) return '<p class="tm-evidence-empty">未找到该证据</p>';

        var rows = '';
        if (e.evidence_id) rows += _row('证据 ID', e.evidence_id);
        if (e.evidence_type) rows += _row('证据类型', e.evidence_type);
        if (e.display_summary) rows += _row('摘要', e.display_summary);
        if (e.source_entity) rows += _row('来源实体', e.source_entity);
        if (e.source_field) rows += _row('来源字段', e.source_field);
        if (e.source_page) rows += _row('来源页码', String(e.source_page));
        if (e.source_question_no) rows += _row('题号', e.source_question_no);

        // 计算口径：公式 + 分子/分母
        if (e.calculation_formula || (e.numerator !== null && e.numerator !== undefined) || (e.denominator !== null && e.denominator !== undefined)) {
          var ratio = '';
          var num = e.numerator, den = e.denominator;
          if (num !== null && num !== undefined && den !== null && den !== undefined && den !== 0) {
            ratio = '<div class="tm-evidence-ratio"><strong>' + _escape(String(num)) + '</strong>' +
              '<span class="tm-evidence-ratio-bar" aria-hidden="true"></span>' +
              '<strong>' + _escape(String(den)) + '</strong>' +
              '<span class="tm-evidence-ratio-value">' + _escape((num / den * 100).toFixed(1)) + '%</span></div>';
          }
          rows += '<div class="tm-evidence-calc" data-testid="evidence-calculation">' +
            '<div class="tm-evidence-calc-title">计算口径</div>' +
            (e.calculation_formula
              ? '<pre class="tm-evidence-formula">' + _escape(e.calculation_formula) + '</pre>'
              : '') +
            ratio +
            '</div>';
        }

        // 规则版本
        if (e.rule_id || e.rule_version) {
          rows += '<div class="tm-evidence-rule">' +
            '<div class="tm-evidence-calc-title">规则版本</div>' +
            (e.rule_id ? '<div class="tm-evidence-rule-row"><span>规则 ID</span><code>' + _escape(e.rule_id) + '</code></div>' : '') +
            (e.rule_version ? '<div class="tm-evidence-rule-row"><span>版本</span><code>' + _escape(e.rule_version) + '</code></div>' : '') +
            '</div>';
        }

        return '<div class="tm-evidence-detail" data-testid="evidence-detail">' + rows + '</div>';
      }

      function _row(label, value) {
        return '<div class="tm-evidence-detail-row"><span class="tm-evidence-detail-label">' + _escape(label) + '</span>' +
          '<span class="tm-evidence-detail-value">' + _escape(value) + '</span></div>';
      }

      /**
       * 打开证据检查器弹层。
       * @param {object} e - EvidenceRead
       */
      async function openEvidenceInspector(e) {
        if (!e) return;
        var header = '<div class="tm-evidence-inspector-head"><span class="material-symbols-rounded" aria-hidden="true">fact_check</span>' +
          '<span>证据详情</span></div>';
        var body = renderEvidenceDetail(e);
        if (typeof openModal === 'function') {
          openModal('证据检查器', header + body);
        }
      }

      /**
       * 从运行加载全部证据并打开检查器定位到指定 evidence_id。
       * @param {number} runId
       * @param {string} evidenceId
       */
      async function openEvidenceById(runId, evidenceId) {
        if (!runId || !evidenceId) { showToast('缺少运行或证据信息'); return; }
        try {
          var list = await teachMateApi.getRunEvidence(runId);
          var found = (list || []).find(function (e) { return String(e.evidence_id) === String(evidenceId); });
          if (!found) { showToast('未找到证据 ' + evidenceId); return; }
          await openEvidenceInspector(found);
        } catch (e) {
          showToast('加载证据失败: ' + (e.message || e));
        }
      }

      return {
        renderEvidenceMini: renderEvidenceMini,
        renderEvidenceDetail: renderEvidenceDetail,
        openEvidenceInspector: openEvidenceInspector,
        openEvidenceById: openEvidenceById,
      };
    })();

    if (typeof window !== 'undefined') window.teachMateEvidence = teachMateEvidence;
