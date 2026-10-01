    // ================= TeachMate 报告画布与导出 =================
    // 职责：结构化报告渲染、教师确认（AI 原稿 vs 教师修改）、导出（打印 / JSON）。
    // 依赖：teachMateState（getSnapshot）、teachMateApi（getRunEvidence）、全局 escapeHtml。
    // 所有函数挂到 window.teachMateReport，供 teachmate-views.js 与 teachmate-interactions.js 调用。

    const teachMateReport = (function () {
      // 只记住本页各份报告的展开状态，避免后续消息刷新时打断阅读。
      var materialOpenStates = new Map();
      var materialEdits = new Map();
      var materialDrafts = new Map();
      var materialModes = new Map();
      var materialTypes = {
        lesson_flow: { label: '课堂讲评', icon: 'co_present', hint: '上课时使用', tone: 'lesson' },
        student_handout: { label: '学生练习', icon: 'edit_note', hint: '发给学生 · 不含答案', tone: 'student' },
        teacher_key: { label: '教师答案', icon: 'key', hint: '备课参考 · 教师使用', tone: 'teacher' },
        followup_assessment: { label: '课后复测', icon: 'fact_check', hint: '复测草稿 · 使用前核对', tone: 'assessment' },
      };

      function rememberMaterialState(card) {
        var key = card.dataset.materialKey;
        if (!key) return;
        materialOpenStates.delete(key);
        materialOpenStates.set(key, card.open);
        if (materialOpenStates.size > 200) materialOpenStates.delete(materialOpenStates.keys().next().value);
      }

      function materialKey(runId, sectionIndex) {
        return runId ? String(runId) + ':' + Number(sectionIndex) : '';
      }

      function trimMaterialMap(map) {
        while (map.size > 200) map.delete(map.keys().next().value);
      }

      function updateMaterialDraft(field) {
        var card = field && field.closest('.tm-material-card');
        if (!card) return;
        var key = materialKey(card.dataset.materialRunId, card.dataset.sectionIndex);
        if (!key) return;
        var draft = materialDrafts.get(key) || { body: '', items: '' };
        if (field.dataset.materialField === 'body') draft.body = field.value;
        if (field.dataset.materialField === 'items') draft.items = field.value;
        materialDrafts.set(key, draft);
        trimMaterialMap(materialDrafts);
      }

      function modifyMaterial(action, button, answer) {
        var card = button && button.closest('.tm-material-card');
        var runId = card && card.dataset.materialRunId;
        var sectionIndex = card && Number(card.dataset.sectionIndex);
        var key = materialKey(runId, sectionIndex);
        var original = answer && Array.isArray(answer.sections) && answer.sections[sectionIndex];
        if (!key || !original) return false;

        if (action === 'edit') {
          var existing = materialEdits.get(key);
          materialDrafts.set(key, existing
            ? { body: existing.body, items: existing.items.join('\n') }
            : { body: String(original.body || ''), items: (Array.isArray(original.items) ? original.items : []).join('\n') });
          materialModes.set(key, 'edit');
        } else if (action === 'compare') {
          materialModes.set(key, 'compare');
        } else if (action === 'back') {
          materialModes.set(key, 'view');
        } else if (action === 'cancel') {
          materialDrafts.delete(key);
          materialModes.set(key, 'view');
        } else {
          return false;
        }
        trimMaterialMap(materialModes);
        return true;
      }

      function getMaterialDraft(runId, sectionIndex) {
        var draft = materialDrafts.get(materialKey(runId, sectionIndex));
        if (!draft) return null;
        return {
          body: String(draft.body || ''),
          items: String(draft.items || '').split(/\r?\n/).map(function (item) { return item.trim(); }).filter(Boolean),
        };
      }

      function commitSavedMaterialEdit(runId, sectionIndex, edit) {
        var key = materialKey(runId, sectionIndex);
        if (!key || !edit) return;
        materialEdits.set(key, { body: String(edit.body || ''), items: Array.isArray(edit.items) ? edit.items.slice() : [] });
        materialDrafts.delete(key);
        materialModes.set(key, 'view');
        trimMaterialMap(materialEdits);
      }

      function hydrateMaterialEdits(messages) {
        (messages || []).forEach(function (message) {
          if (!message || !message.run_id || !message.material_edits) return;
          Object.entries(message.material_edits).forEach(function (entry) {
            if (!/^\d+$/.test(entry[0])) return;
            commitSavedMaterialEdit(message.run_id, Number(entry[0]), entry[1]);
          });
        });
      }

      function applyMaterialEdits(answer, runId) {
        if (!answer || !Array.isArray(answer.sections) || !runId) return answer;
        var changed = false;
        var sections = answer.sections.map(function (section, index) {
          var edit = materialEdits.get(materialKey(runId, index));
          if (!edit) return section;
          changed = true;
          return Object.assign({}, section, { body: edit.body, items: edit.items.slice() });
        });
        return changed ? Object.assign({}, answer, { sections: sections }) : answer;
      }

      document.addEventListener('input', function (event) {
        if (event.target && event.target.matches && event.target.matches('[data-material-field]')) updateMaterialDraft(event.target);
      }, true);

      document.addEventListener('toggle', function (event) {
        var card = event.target;
        if (card && card.isConnected && card.matches && card.matches('.tm-material-card')) rememberMaterialState(card);
      }, true);

      document.addEventListener('click', function (event) {
        var summary = event.target && event.target.closest && event.target.closest('.tm-material-card > summary');
        var card = summary && summary.parentElement;
        if (!card || !card.isConnected) return;
        var key = card.dataset.materialKey;
        if (key) {
          materialOpenStates.delete(key);
          materialOpenStates.set(key, !card.open);
          trimMaterialMap(materialOpenStates);
        }
      }, true);

      function setMaterialsExpanded(button, expanded) {
        var materials = button && button.closest('.tm-materials');
        if (!materials) return;
        materials.querySelectorAll('.tm-material-card').forEach(function (card) {
          card.open = expanded;
          rememberMaterialState(card);
        });
      }

      function renderMaterials(sections, runId) {
        var html = '<section class="tm-materials" aria-label="教学材料">' +
          '<div class="tm-materials-head"><div><span class="tm-report-kicker">备课材料</span><h4>把分析带进课堂 <span>' + sections.length + ' 份材料</span></h4></div>' +
          '<div class="tm-materials-controls" role="group" aria-label="材料阅读方式"><button type="button" data-act="tm-materials-expand">全部展开</button><button type="button" data-act="tm-materials-collapse">全部收起</button></div></div>';
        sections.forEach(function (section, index) {
          if (!section || typeof section !== 'object') return;
          var type = materialTypes[section.kind] || { label: '教学材料', icon: 'description', hint: '教学草稿', tone: 'lesson' };
          var key = runId ? String(runId) + ':' + index + ':' + (section.kind || '') : '';
          var editKey = materialKey(runId, index);
          var edit = editKey ? materialEdits.get(editKey) : null;
          var draft = editKey ? materialDrafts.get(editKey) : null;
          var mode = editKey ? (materialModes.get(editKey) || 'view') : 'view';
          var visibleBody = edit ? edit.body : String(section.body || '');
          var visibleItems = edit ? edit.items : (Array.isArray(section.items) ? section.items : []);
          var open = key && materialOpenStates.has(key) ? materialOpenStates.get(key) : index === 0 && section.kind !== 'teacher_key';
          var cardRun = runId ? String(runId) : '';
          html += '<details class="tm-material-card tm-material-' + type.tone + '" data-testid="report-section" data-material-key="' + _escape(key) + '" data-material-run-id="' + _escape(cardRun) + '" data-section-index="' + index + '"' + (open ? ' open' : '') + '>' +
            '<summary><span class="tm-material-icon material-symbols-rounded" aria-hidden="true">' + type.icon + '</span>' +
            '<span class="tm-material-heading"><small>' + type.label + (edit ? ' · 教师已改' : '') + '</small><strong>' + _escape(section.title || type.label) + '</strong><span>' + type.hint + '</span></span>' +
            '<span class="tm-material-chevron material-symbols-rounded" aria-hidden="true">expand_more</span></summary>' +
            '<div class="tm-material-content"><div class="tm-material-toolbar">';
          if (mode === 'edit') {
            html += '<span>编辑草稿 · 导出会包含已保存修改</span><div><button type="button" data-act="tm-material-save">保存修改</button><button type="button" data-act="tm-material-cancel">取消</button></div></div>' +
              '<div class="tm-material-editor"><label>材料说明<textarea class="tm-material-field" data-material-field="body" rows="3">' + _escape(draft ? draft.body : visibleBody) + '</textarea></label>' +
              '<label>步骤或题目 <small>每行一项；留空可移除全部项目</small><textarea class="tm-material-field" data-material-field="items" rows="5">' + _escape(draft ? draft.items : visibleItems.join('\n')) + '</textarea></label></div>' +
              '<p class="tm-material-edit-note">编辑中的草稿尚未保存；点击“保存修改”后会写入本机数据库，可在刷新后继续使用。</p>';
          } else if (mode === 'compare' && edit) {
            html += '<span>原稿与教师修改</span><div><button type="button" data-act="tm-material-back">返回材料</button><button type="button" data-act="tm-material-edit">继续修改</button></div></div>' +
              '<div class="tm-material-compare"><section><h5>AI 原稿</h5>' + _renderMaterialText(section.body, section.items) + '</section><section><h5>教师修改版</h5>' + _renderMaterialText(edit.body, edit.items) + '</section></div>';
          } else {
            html += '<span>' + (edit ? '已保存 · 导出将使用教师修改稿' : (cardRun ? '待教师核对' : '只读历史材料')) + '</span><div>' +
              (edit && cardRun ? '<button type="button" data-act="tm-material-compare">查看修改对比</button>' : '') +
              (cardRun ? '<button type="button" data-act="tm-material-edit">编辑材料</button>' : '') + '</div></div>';
            if (visibleBody) html += '<p class="tm-report-body">' + _escape(visibleBody) + '</p>';
            if (visibleItems.length) html += '<ul class="tm-struct-list">' + visibleItems.map(function (item) { return '<li>' + _escape(item) + '</li>'; }).join('') + '</ul>';
          }
          html += '</div></details>';
        });
        return html + '</section>';
      }

      function _renderMaterialText(body, items) {
        var html = body ? '<p>' + _escape(body) + '</p>' : '<p class="tm-material-empty">暂无说明</p>';
        if (Array.isArray(items) && items.length) html += '<ul>' + items.map(function (item) { return '<li>' + _escape(item) + '</li>'; }).join('') + '</ul>';
        else html += '<p class="tm-material-empty">暂无步骤或题目</p>';
        return html;
      }

      function _escape(v) {
        return (typeof escapeHtml === 'function') ? escapeHtml(v) : String(v == null ? '' : v);
      }

      function _sentence(v, fallback) {
        var text = String(v == null ? '' : v).trim() || String(fallback == null ? '' : fallback).trim();
        if (!text) return '';
        return /[。！？；.!?;]$/.test(text) ? text : text + '。';
      }

      // 确认弹窗面向教师使用自然语言；JSON 仍保留在后台用于审计和导出。
      function _naturalReport(value) {
        if (value && typeof value === 'object') {
          var parts = [];
          if (value.summary) parts.push('总体情况\n' + _sentence(value.summary));
          var findings = Array.isArray(value.findings) ? value.findings : [];
          if (findings.length) {
            parts.push('主要发现\n' + findings.map(function (item, index) {
              var title = item && (item.title || item.name) || ('发现 ' + (index + 1));
              var detail = item && (item.description || item.detail || item.claim || item.text) || '';
              return (index + 1) + '、' + title + (detail ? '：' + _sentence(detail) : '。');
            }).join('\n'));
          }
          var recommendations = Array.isArray(value.recommendations) ? value.recommendations : [];
          if (recommendations.length) {
            parts.push('行动建议\n' + recommendations.map(function (item, index) {
              var action = item && (item.action || item.title) || ('建议 ' + (index + 1));
              var reason = item && (item.rationale || item.description || item.detail || '') || '';
              return (index + 1) + '、' + action + (reason ? '：' + _sentence(reason) : '。');
            }).join('\n'));
          }
          if (value.timeline) parts.push('复习安排\n' + _sentence(value.timeline));
          var sections = Array.isArray(value.sections) ? value.sections : [];
          if (sections.length) {
            parts.push('教学材料\n' + sections.map(function (section) {
              var heading = section && section.title || '教学材料';
              var lines = [];
              if (section && section.body) lines.push(section.body);
              if (section && Array.isArray(section.items)) lines = lines.concat(section.items);
              return heading + (lines.length ? '\n' + lines.map(function (line) { return '• ' + line; }).join('\n') : '');
            }).join('\n\n'));
          }
          var limitations = Array.isArray(value.limitations) ? value.limitations.filter(Boolean) : [];
          if (limitations.length) parts.push('需要注意\n' + limitations.map(function (item) { return '• ' + _sentence(item); }).join('\n'));
          return parts.join('\n\n') || '暂无可展示的分析内容。';
        }
        var text = String(value == null ? '' : value).trim();
        if (!text) return '';
        // 兼容历史消息中的 ```json ...``` 文本，解析成功后转为自然语言。
        var candidate = text.replace(/^```(?:json)?\s*/i, '').replace(/\s*```$/, '').trim();
        if (candidate.charAt(0) === '{') {
          try { return _naturalReport(JSON.parse(candidate)); } catch (e) { /* 保留原文 */ }
        }
        return text;
      }

      function _humanProfileValue(value) {
        if (Array.isArray(value)) return value.map(_humanProfileValue).join('、');
        if (value && typeof value === 'object') {
          var action = value.action || value.title || '';
          var result = value.result || value.rationale || '';
          var status = value.status || '';
          return action + (result ? '：' + result : '') + (status ? '（' + status + '）' : '');
        }
        return String(value == null ? '' : value);
      }

      /**
       * 报告画布渲染（升级版结构化回答）。
       * @param {object|null} answer - StructuredAnswer（含 summary / findings / recommendations / limitations / sections）
       * @param {object} [options] - 主视图展示选项；导出和审计数据不受影响
       * @param {boolean} [options.hideEvidence=false] - 隐藏主视图中的证据引用与证据统计
       * @param {boolean} [options.hideLimitations=false] - 隐藏主视图中的局限段落
       * @returns {string} HTML
       */
      function renderReportCanvas(answer, options) {
        if (!answer || typeof answer !== 'object') return '';
        options = options || {};
        var hideEvidence = options.hideEvidence === true;
        var hideLimitations = options.hideLimitations === true;
        var reportRunId = options.runId || '';
        var html = '<div class="tm-report-canvas" data-testid="report-canvas">';
        var findingCount = Array.isArray(answer.findings) ? answer.findings.length : 0;
        var recommendationCount = Array.isArray(answer.recommendations) ? answer.recommendations.length : 0;
        var evidenceCount = 0;
        (Array.isArray(answer.findings) ? answer.findings : []).forEach(function (finding) {
          evidenceCount += Array.isArray(finding.evidence_ids) ? finding.evidence_ids.length : 0;
        });

        // 摘要
        if (answer.summary) {
          html += '<section class="tm-report-section tm-report-summary" data-testid="report-summary">' +
            '<div class="tm-report-kicker">核心结论</div>' +
            '<h4 class="tm-report-title"><span class="material-symbols-rounded" aria-hidden="true">summarize</span>核心结论</h4>' +
            '<p class="tm-report-body">' + _escape(answer.summary) + '</p></section>';
        }
        if (answer.timeline) {
          html += '<section class="tm-report-section"><div class="tm-report-kicker">安排</div><h4 class="tm-report-title"><span class="material-symbols-rounded" aria-hidden="true">calendar_month</span>复习时间表</h4><p class="tm-report-body">' + _escape(answer.timeline) + '</p></section>';
        }
        html += '<div class="tm-report-stat-grid' + (hideEvidence ? ' tm-report-stat-grid-compact' : '') + '" aria-label="报告概览指标">' +
          '<div class="tm-report-stat"><strong>' + findingCount + '</strong><span>主要发现</span></div>' +
          '<div class="tm-report-stat"><strong>' + recommendationCount + '</strong><span>行动建议</span></div>' +
          (hideEvidence ? '' : '<div class="tm-report-stat"><strong>' + evidenceCount + '</strong><span>可核验证据</span></div>') +
          '</div>';

        // 主要发现
        if (Array.isArray(answer.findings) && answer.findings.length) {
          html += '<section class="tm-report-section" data-testid="report-findings">' +
            '<div class="tm-report-kicker">优先处理</div>' +
            '<h4 class="tm-report-title"><span class="material-symbols-rounded" aria-hidden="true">search</span>主要发现 <span class="tm-report-count">' + answer.findings.length + '</span></h4>' +
            '<div class="tm-finding-list">';
          answer.findings.forEach(function (finding, idx) {
            var confidence = finding.confidence ? '<span class="tm-struct-badge">' + _escape(finding.confidence) + '</span>' : '';
            // 后端正式契约使用 description，兼容历史数据中的 claim/detail/text。
            var findingTitle = finding.title || '发现 ' + (idx + 1);
            var claim = finding.description || finding.claim || finding.detail || finding.text || finding.summary || '';
            var claimText = _sentence(claim, findingTitle);
            html += '<article class="tm-finding" data-testid="report-finding">' +
              '<div class="tm-finding-head"><span class="tm-finding-index">' + (idx + 1) + '</span><strong>' + _escape(findingTitle) + '</strong>' + confidence + '</div>' +
              '<p class="tm-finding-claim">' + _escape(claimText) + '</p>';
            if (!hideEvidence && Array.isArray(finding.evidence_ids) && finding.evidence_ids.length) {
              html += '<div class="tm-evidence-refs">' +
                '<span class="material-symbols-rounded" aria-hidden="true">fact_check</span>' +
                '<span>证据：</span>' +
                finding.evidence_ids.map(function (eid, evidenceIndex) {
                  return '<button type="button" class="tm-evidence-ref-btn" data-act="tm-view-evidence-id" data-evidence-id="' + _escape(eid) + '" data-run-id="' + _escape(String((finding.run_id) || reportRunId || '')) + '" aria-label="查看第' + (evidenceIndex + 1) + '条来源">来源' + (evidenceIndex + 1) + '</button>';
                }).join('、') +
                '</div>';
            }
            if (reportRunId && window.teachMateTasks && teachMateTasks.currentTaskId()) {
              html += '<button type="button" class="tm-task-btn" data-act="tm-task-feedback" data-run-id="' + _escape(String(reportRunId)) + '" data-finding="' + _escape(findingTitle) + '">记录对这一判断的修正</button>';
            }
            html += '</article>';
          });
          html += '</div></section>';
        }

        // 行动建议
        if (Array.isArray(answer.recommendations) && answer.recommendations.length) {
          html += '<section class="tm-report-section" data-testid="report-recommendations">' +
            '<div class="tm-report-kicker">下一步行动</div>' +
            '<h4 class="tm-report-title"><span class="material-symbols-rounded" aria-hidden="true">lightbulb</span>行动建议 <span class="tm-report-count">' + answer.recommendations.length + '</span></h4>' +
            '<ol class="tm-recommendation-list">';
          answer.recommendations.slice().sort(function (a, b) { return (a.priority || 99) - (b.priority || 99); }).forEach(function (rec) {
            var recommendationText = rec.rationale || rec.description || rec.detail || rec.reason || '';
            html += '<li class="tm-recommendation-item">' +
              '<div class="tm-rec-priority">P' + (rec.priority || 3) + '</div>' +
              '<div class="tm-rec-copy"><strong>' + _escape(rec.action || '') + '</strong>';
            if (recommendationText || rec.action) html += '<p>' + _escape(_sentence(recommendationText, rec.action)) + '</p>';
            if (!hideEvidence && Array.isArray(rec.supports) && rec.supports.length) html += '<small>依据：' + rec.supports.map(_escape).join('、') + '</small>';
            html += '</div></li>';
          });
          html += '</ol></section>';
        }

        // 局限与注意事项
        if (!hideLimitations && Array.isArray(answer.limitations) && answer.limitations.length) {
          html += '<section class="tm-report-section tm-report-limitations" data-testid="report-limitations">' +
            '<h4 class="tm-report-title"><span class="material-symbols-rounded" aria-hidden="true">warning</span>局限与注意事项</h4>' +
            '<ul class="tm-struct-list">';
          answer.limitations.forEach(function (item) { html += '<li>' + _escape(item) + '</li>'; });
          html += '</ul></section>';
        }

        // 附加分节（通用）
        if (answer.answer_type === 'review_plan' && Array.isArray(answer.sections) && answer.sections.length) {
          html += renderMaterials(answer.sections.filter(function (section) { return section && typeof section === 'object'; }), reportRunId);
        } else if (Array.isArray(answer.sections)) {
          answer.sections.forEach(function (sec) {
            html += '<section class="tm-report-section" data-testid="report-section">';
            if (sec.title) html += '<h4 class="tm-report-title">' + _escape(sec.title) + '</h4>';
            if (sec.body) html += '<p class="tm-report-body">' + _escape(sec.body) + '</p>';
            if (Array.isArray(sec.items)) {
              html += '<ul class="tm-struct-list">';
              sec.items.forEach(function (item) { html += '<li>' + _escape(item) + '</li>'; });
              html += '</ul>';
            }
            html += '</section>';
          });
        }

        if (reportRunId && window.teachMateTasks && !teachMateTasks.currentTaskId()) {
          html += '<div class="tm-task-actions"><button type="button" class="tm-task-btn" data-act="tm-task-from-report" data-run-id="' + _escape(String(reportRunId)) + '">围绕本报告创建教学任务</button></div>';
        }
        html += '</div>';
        return html;
      }

      /**
       * 教师确认面板：AI 原稿与教师修改差异，确认后保存。
       * @param {object} ctx - { runId, answer, evaluationId, aiOriginal, teacherText, status }
       * @returns {string} HTML
       */
      function renderConfirmPanel(ctx) {
        ctx = ctx || {};
        var aiOriginal = _naturalReport(ctx.aiOriginal || ctx.answer || '');
        var teacherText = (typeof ctx.teacherText === 'string') ? _naturalReport(ctx.teacherText) : aiOriginal;
        var status = ctx.status || 'draft';
        var statusLabel = status === 'confirmed' ? '已确认'
          : status === 'archived' ? '已归档'
          : '草稿';

        var statusBadge = '<span class="tm-confirm-status tm-confirm-status-' + _escape(status) + '">' + _escape(statusLabel) + '</span>';

        return '<section class="tm-confirm-panel" data-testid="confirm-panel" aria-label="教师确认">' +
          '<div class="tm-confirm-head"><span class="material-symbols-rounded" aria-hidden="true">verified</span>' +
          '<div><strong>教师确认</strong><small>AI 原稿与你的修改会分别保存，确认后写入正式评价</small></div>' + statusBadge + '</div>' +
          '<div class="tm-confirm-grid">' +
          '<div class="tm-confirm-col">' +
          '<label class="tm-confirm-label" for="tmAiOriginalText">AI 原稿</label>' +
          '<textarea class="tm-confirm-textarea" id="tmAiOriginalText" rows="6" readonly aria-readonly="true">' + _escape(aiOriginal) + '</textarea></div>' +
          '<div class="tm-confirm-col">' +
          '<label class="tm-confirm-label" for="tmTeacherText">教师修改</label>' +
          '<textarea class="tm-confirm-textarea" id="tmTeacherText" rows="6" data-act="tm-confirm-edit" placeholder="在 AI 原稿基础上修改，或直接重写…"' +
          (status === 'confirmed' ? ' disabled' : '') + '>' + _escape(teacherText) + '</textarea></div>' +
          '</div>' +
          '<div class="tm-confirm-actions">' +
          (ctx.evaluationId ? '<button type="button" class="tm-confirm-btn" data-act="tm-evaluation-view" data-evaluation-id="' + _escape(String(ctx.evaluationId)) + '">查看审计记录</button>' : '') +
          (status === 'confirmed'
            ? '<span class="tm-confirm-done"><span class="material-symbols-rounded" aria-hidden="true">check_circle</span>该评价已确认，不可再修改</span>'
            : '<button type="button" class="tm-confirm-btn" data-act="tm-evaluation-save">保存草稿</button>' +
              '<button type="button" class="tm-confirm-btn tm-confirm-primary" data-act="tm-evaluation-confirm">确认并保存</button>') +
          '</div>' +
          '<div class="tm-confirm-help">' +
          '<span class="material-symbols-rounded" aria-hidden="true">lock</span>' +
          '<span>确认后评价会写入学生档案，修改内容会与 AI 原稿分别保留并审计。</span></div>' +
          '</section>';
      }

      /**
       * 导出：打印版本（新窗口 / 打印样式由 CSS 控制）。
       * @param {object} payload - { sessionTitle, answer, evidence }
       */
      function exportForPrint(payload) {
        payload = payload || {};
        var snapshot = (typeof teachMateState !== 'undefined') ? teachMateState.getSnapshot() : {};
        var title = payload.sessionTitle || (snapshot.currentSession && snapshot.currentSession.title) || 'TeachMate 分析报告';
        var answer = payload.answer;
        var evidence = payload.evidence || [];
        var runId = payload.runId || snapshot.currentRunId || '';

        var html = '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">' +
          '<title>' + _escape(title) + '</title>' +
          '<style>' +
          'body{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;color:#0b1233;line-height:1.6;max-width:760px;margin:32px auto;padding:0 24px}' +
          'h1{font-size:22px;margin-bottom:4px}h2{font-size:15px;margin:20px 0 8px;border-bottom:1px solid #e5e8f1;padding-bottom:4px}' +
          '.meta{color:#66708f;font-size:12px;margin-bottom:24px}' +
          '.finding{margin-bottom:10px}.finding strong{display:block}' +
          '.rec{margin-bottom:8px}ul{padding-left:20px}' +
          '.limitations li{color:#66708f}' +
          '.ev{margin:4px 0;font-size:12px;color:#66708f}' +
          '@media print{body{margin:12mm auto}}' +
          '</style></head><body>' +
          '<h1>' + _escape(title) + '</h1>' +
          '<div class="meta">生成时间：' + new Date().toLocaleString('zh-CN') + (runId ? ' · 运行 #' + runId : '') + ' · TeachMate AI 教学助手</div>';

        if (answer) {
          if (answer.summary) html += '<h2>摘要</h2><p>' + _escape(answer.summary) + '</p>';
          if (Array.isArray(answer.findings) && answer.findings.length) {
            html += '<h2>主要发现</h2>';
            answer.findings.forEach(function (f) {
              html += '<div class="finding"><strong>' + _escape(f.title || '') + '</strong>' + _escape(f.claim || f.description || '') + '</div>';
            });
          }
          if (Array.isArray(answer.recommendations) && answer.recommendations.length) {
            html += '<h2>行动建议</h2>';
            answer.recommendations.forEach(function (r) {
              html += '<div class="rec"><strong>' + _escape(r.action || '') + '</strong> ' + _escape(r.rationale || '') + '</div>';
            });
          }
          if (answer.timeline) html += '<h2>复习时间表</h2><p>' + _escape(answer.timeline) + '</p>';
          if (Array.isArray(answer.sections)) {
            answer.sections.forEach(function (section) {
              html += '<h2>' + _escape(section.title || '教学材料') + '</h2>';
              if (section.body) html += '<p>' + _escape(section.body) + '</p>';
              if (Array.isArray(section.items) && section.items.length) {
                html += '<ul>' + section.items.map(function (item) { return '<li>' + _escape(item) + '</li>'; }).join('') + '</ul>';
              }
            });
          }
          if (Array.isArray(answer.limitations) && answer.limitations.length) {
            html += '<h2>局限与注意事项</h2><ul class="limitations">' + answer.limitations.map(function (l) { return '<li>' + _escape(l) + '</li>'; }).join('') + '</ul>';
          }
        }

        if (evidence && evidence.length) {
          html += '<h2>证据清单（' + evidence.length + '）</h2>';
          evidence.forEach(function (e) {
            html += '<div class="ev">' + _escape(e.evidence_id || '') + ' · ' + _escape(e.evidence_type || '') +
              (e.display_summary ? ' · ' + _escape(e.display_summary) : '') +
              (e.source_entity ? ' · 来源 ' + _escape(e.source_entity) : '') + '</div>';
          });
        }

        html += '</body></html>';

        var win = window.open('', '_blank');
        if (!win) { showToast('浏览器拦截了打印窗口，请允许弹出窗口后重试。'); return; }
        win.document.write(html);
        win.document.close();
        win.focus();
        setTimeout(function () { try { win.print(); } catch (e) { /* 用户手动打印 */ } }, 200);
      }

      /**
       * 导出：结构化 JSON 下载。
       * 应用版本优先读取服务端 /api/v1/runtime（S0-02），失败时回退内置常量。
       * @param {object} payload - { answer, evidence, runId }
       */
      async function exportJSON(payload) {
        payload = payload || {};
        var snapshot = (typeof teachMateState !== 'undefined') ? teachMateState.getSnapshot() : {};
        var version = '0.9.0-beta.2';
        try {
          if (typeof teachMateApi !== 'undefined' && typeof teachMateApi.getRuntime === 'function') {
            var runtime = await teachMateApi.getRuntime();
            if (runtime && runtime.version) version = runtime.version;
          }
        } catch (e) { /* 离线场景回退内置版本 */ }
        var doc = {
          exported_at: new Date().toISOString(),
          app: 'TeachMate',
          version: version,
          run_id: payload.runId || snapshot.currentRunId || null,
          session_id: snapshot.currentSessionId || null,
          report: payload.answer || null,
          evidence: payload.evidence || [],
        };
        var blob = new Blob([JSON.stringify(doc, null, 2)], { type: 'application/json;charset=utf-8' });
        var url = URL.createObjectURL(blob);
        var a = document.createElement('a');
        a.href = url;
        a.download = 'teachmate-report-' + (payload.runId || 'run') + '.json';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
      }

      return {
        modifyMaterial: modifyMaterial,
        getMaterialDraft: getMaterialDraft,
        commitSavedMaterialEdit: commitSavedMaterialEdit,
        hydrateMaterialEdits: hydrateMaterialEdits,
        applyMaterialEdits: applyMaterialEdits,
        setMaterialsExpanded: setMaterialsExpanded,
        renderReportCanvas: renderReportCanvas,
        renderConfirmPanel: renderConfirmPanel,
        exportForPrint: exportForPrint,
        exportJSON: exportJSON,
      };
    })();

    if (typeof window !== 'undefined') window.teachMateReport = teachMateReport;
