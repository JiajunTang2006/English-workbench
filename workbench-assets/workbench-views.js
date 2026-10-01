    // ================= 渲染层 =================
    function getCurrentPageName() {
      if (curModule === 'risk') return '重点关注学生';
      const module = MODULES.find(item => item.key === curModule);
      return module ? moduleLabel(module) : '仪表盘';
    }

    function renderHeaderContext() {
      const teacherName = String(state.teacher?.name || '').trim();
      const pageName = activeTab === 'teachmate' ? '教师助手' : getCurrentPageName();
      // 顶部品牌统一结构：WorkBench 显示「xxx工作台」，TeachMate 显示「xxx教师助手」。
      // 学科名未填写时用当前学科的默认名兜底，避免出现多余的分隔点或"undefined"。
      const subjectBase = subjectBaseTitle();
      const platformTitle = subjectBase.endsWith('工作台') ? subjectBase : `${subjectBase}工作台`;
      const assistantTitle = `${subjectBase}教师助手`;
      const workspaceTitle = teacherName ? `${teacherName} · ${platformTitle}` : platformTitle;
      const teachmateTitle = teacherName ? `${teacherName} · ${assistantTitle}` : assistantTitle;
      document.getElementById('appTitle').textContent = activeTab === 'teachmate' ? teachmateTitle : workspaceTitle;
      document.getElementById('appTitle').setAttribute('title', activeTab === 'teachmate' ? workspaceTitle : '');
      const userNameLabel = document.getElementById('userNameLabel');
      if (userNameLabel) userNameLabel.textContent = teacherName || '使用者';
      document.title = `${pageName} · ${workspaceTitle}`;
      // TeachMate 模式不显示学期/班级切换器（产品名直接置于顶栏左侧）
      const ctx = document.querySelector('.header-context');
      if (ctx) {
        if (activeTab === 'teachmate') {
          ctx.hidden = true;
          ctx.style.display = 'none';
        } else {
          ctx.hidden = false;
          ctx.style.display = '';
        }
      }
      const classSelect = document.getElementById('classSelect');
      if (classSelect) {
        const selectedClass = normalizeClassFilter(dashboardClass);
        classSelect.innerHTML = classSelectOptions(selectedClass);
        classSelect.value = selectedClass;
      }
      renderTermSwitcher();
    }

    function renderNav() {
      const nav = document.getElementById('nav');
      const headerTabSwitcher = document.getElementById('headerTabSwitcher');
      const tabSwitcher = `
        <div class="nav-tab-switcher" role="tablist" aria-label="切换工作区">
          <span class="nav-tab-indicator" aria-hidden="true"></span>
          <button type="button" class="tab-btn ${activeTab==='workbench'?'active':''}" data-act="tab-switch" data-tab="workbench" role="tab">WorkBench</button>
          <button type="button" class="tab-btn ${activeTab==='teachmate'?'active':''}" data-act="tab-switch" data-tab="teachmate" role="tab">TeachMate</button>
        </div>
      `;
      if (headerTabSwitcher) headerTabSwitcher.innerHTML = tabSwitcher;
      if (activeTab === 'teachmate') {
        nav.innerHTML = renderTeachMateNav();
      } else {
        nav.innerHTML = visibleModules().map(m => `
          <button type="button" class="nav-item ${m.parent ? 'nav-item-child' : ''} ${m.key===curModule?'active':''}" data-act="nav" data-key="${m.key}" ${m.key===curModule?'aria-current="page"':''}>
            ${m.key === 'dash'
              ? '<svg class="nav-icon nav-dashboard-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3.5" y="3.5" width="17" height="17" rx="3.5"></rect><path d="M7.5 8h9"></path><path d="M7.5 12h3v5h-3z" fill="currentColor" stroke="none"></path><path d="M12.5 12h3v5h-3z" fill="currentColor" stroke="none"></path></svg>'
              : `<span class="nav-icon material-symbols-rounded" aria-hidden="true">${m.icon}</span>`}<span>${escapeHtml(moduleLabel(m))}</span>
          </button>
        `).join('');
      }
      const sidebar = document.getElementById('sidebar');
      if (sidebar) sidebar.style.display = '';
      renderHeaderContext();
      // B3-23: 渲染后同步滑块位置（无动画，避免初始闪动）
      syncTabIndicator(false);
    }

    /** B3-23: 滑块跟随当前激活 tab（offsetLeft 计算，等宽/不等宽均准确） */
    function syncTabIndicator(animate) {
      const sw = document.querySelector('.nav-tab-switcher');
      if (!sw) return;
      const ind = sw.querySelector('.nav-tab-indicator');
      const btn = sw.querySelector('.tab-btn.active');
      if (!ind || !btn) return;
      if (animate) {
        ind.style.transition = 'transform .32s cubic-bezier(.4, 0, .2, 1), width .32s cubic-bezier(.4, 0, .2, 1)';
      } else {
        ind.style.transition = 'none';
      }
      ind.style.width = btn.offsetWidth + 'px';
      ind.style.transform = 'translateX(' + btn.offsetLeft + 'px)';
      void ind.offsetWidth; // reflow：确保后续 transition 生效
      if (!animate) ind.style.transition = '';
    }

    function render() {
      if (typeof teachMateTasks !== 'undefined') teachMateTasks.captureCanvas();
      disposeScoreCharts();
      renderHeaderContext();
      if (activeTab === 'teachmate') {
        const previousMessages = document.getElementById('tmMessages');
        const previousInput = document.getElementById('tmInput');
        const previousTaskOperations = document.getElementById('tmTaskOperations');
        const taskOperationsKey = previousTaskOperations && previousTaskOperations.dataset.menuKey;
        const taskOperationsOpen = previousTaskOperations && previousTaskOperations.open;
        const previousCanvas = typeof teachMateTasks !== 'undefined' ? teachMateTasks.preservedCanvas() : null;
        const canvasFocus = previousCanvas && previousCanvas.contains(document.activeElement) ? document.activeElement : null;
        // 过程消息行带有持续扫光动画；保留同一 run 的过程块节点，避免
        // 每个 SSE/轮询事件都通过 innerHTML 重建，导致动画和折叠状态重置。
        const previousProcess = document.getElementById('tmProcessBlock');
        const currentSessionKey = String(teachMateState.getSnapshot().currentSessionId || '');
        const sameSession = previousMessages && previousMessages.dataset.sessionId === currentSessionKey;
        const previousScrollTop = previousMessages ? previousMessages.scrollTop : 0;
        const previousScrollBottomGap = previousMessages
          ? Math.max(0, previousMessages.scrollHeight - previousMessages.scrollTop - previousMessages.clientHeight)
          : 0;
        const scrollInteractionEpoch = (typeof tmScrollInteractionEpoch === 'number') ? tmScrollInteractionEpoch : 0;
        // 只有确实贴近底部时才跟随新进度；教师手动上滑后，异步重绘不能把
        // 视口强行拉回底部。24px 足够覆盖滚轮/触控板的舍入误差，也不会吞掉
        // 教师有意向上查看的最后一小段内容。
        const wasNearBottom = sameSession && previousScrollBottomGap < 24;
        const inputWasFocused = previousInput && document.activeElement === previousInput;
        const inputSelectionStart = previousInput ? previousInput.selectionStart : null;
        const inputSelectionEnd = previousInput ? previousInput.selectionEnd : null;
        const workarea = document.getElementById('workarea');
        workarea.innerHTML = renderTeachMate();
        const nextTaskOperations = document.getElementById('tmTaskOperations');
        if (nextTaskOperations && nextTaskOperations.dataset.menuKey === taskOperationsKey) {
          nextTaskOperations.open = !!taskOperationsOpen;
        }
        const nextCanvas = document.getElementById('tmTaskCanvas');
        // Keep long editors intact across background refreshes, including file selections.
        if (previousCanvas && nextCanvas && previousCanvas.dataset.canvasKey === nextCanvas.dataset.canvasKey) {
          previousCanvas.querySelector('.tm-canvas-heading').replaceWith(nextCanvas.querySelector('.tm-canvas-heading'));
          nextCanvas.replaceWith(previousCanvas);
          if (canvasFocus) canvasFocus.focus({ preventScroll: true });
        }
        const nextProcess = document.getElementById('tmProcessBlock');
        if (previousProcess && nextProcess &&
            previousProcess.getAttribute('data-run-id') === nextProcess.getAttribute('data-run-id')) {
          nextProcess.replaceWith(previousProcess);
        }
        document.getElementById('sidebar').style.display = '';
        // P1-5: 恢复输入草稿，防止异步重绘清空教师正在输入的内容
        // 不强制抢焦点——只在输入框已处于焦点时恢复光标位置
        var tmInput = document.getElementById('tmInput');
        if (tmInput && teachMateState) {
          var draft = teachMateState.getDraft();
          if (draft) {
            var wasFocused = document.activeElement === tmInput;
            tmInput.value = draft;
            wasFocused = inputWasFocused || wasFocused;
            if (wasFocused) {
              tmInput.focus();
              var start = inputSelectionStart === null ? tmInput.value.length : inputSelectionStart;
              var end = inputSelectionEnd === null ? start : inputSelectionEnd;
              try { tmInput.setSelectionRange(start, end); } catch (e) {}
            }
          }
        }
        const nextMessages = document.getElementById('tmMessages');
        if (nextMessages && sameSession) {
          // 进度重绘和教师手动滚动可能同时发生。记录 wheel/touch/键盘操作，
          // 避免下一帧的底部修正覆盖刚刚发生的人工滚动。
          const markScrollInteraction = () => { if (typeof tmScrollInteractionEpoch === 'number') tmScrollInteractionEpoch += 1; };
          nextMessages.addEventListener('wheel', markScrollInteraction, { passive: true });
          nextMessages.addEventListener('touchstart', markScrollInteraction, { passive: true });
          nextMessages.addEventListener('keydown', (event) => {
            if (['ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', 'Home', 'End', ' '].includes(event.key)) markScrollInteraction();
          });
          const restoreScroll = () => {
            const maxScrollTop = Math.max(0, nextMessages.scrollHeight - nextMessages.clientHeight);
            const targetScrollTop = wasNearBottom
              ? maxScrollTop
              : Math.min(previousScrollTop, maxScrollTop);
            nextMessages.scrollTop = targetScrollTop;
            return targetScrollTop;
          };
          const restoredScrollTop = restoreScroll();
          if (typeof window.requestAnimationFrame === 'function') {
            window.requestAnimationFrame(() => {
              // DOM 重绘后教师可能已经滚动；此时保留他的操作，不执行旧快照恢复。
              // 若位置仍未变化，才用最新 scrollHeight 修正底部跟随。
              if (scrollInteractionEpoch === tmScrollInteractionEpoch
                  && Math.abs(nextMessages.scrollTop - restoredScrollTop) < 1) restoreScroll();
            });
          }
        }
        return;
      }
      document.getElementById('sidebar').style.display = '';
      document.getElementById('workarea').innerHTML = renderers[curModule]();
      if (curModule === 'settings' && typeof loadProviderStatusUi === 'function') {
        // B3-10：进入设置页时刷新 AI 模型运行状态与已配置信息
        loadProviderStatusUi();
        if (typeof loadMoniConfigUi === 'function') loadMoniConfigUi();
        if (typeof loadGrowthStandardsUi === 'function') loadGrowthStandardsUi();
        if (typeof loadSchoolSourcesUi === 'function') loadSchoolSourcesUi();
      }
      if (curModule === 'settings' && typeof loadPaperDistributionDefaultsUi === 'function') loadPaperDistributionDefaultsUi();
      if (curModule === 'risk') {
        const resetRiskDetailScroll = () => {
          const main = document.querySelector('main');
          if (main) {
            main.scrollTop = 0;
            if (typeof main.scrollTo === 'function') main.scrollTo({ top: 0, left: 0, behavior: 'auto' });
          }
          const scrollingElement = document.scrollingElement || document.documentElement;
          if (scrollingElement) scrollingElement.scrollTop = 0;
          if (!/jsdom/i.test(String(window.navigator?.userAgent || '')) && typeof window.scrollTo === 'function') {
            window.scrollTo(0, 0);
          }
          document.documentElement.scrollTop = 0;
          document.body.scrollTop = 0;
        };
        resetRiskDetailScroll();
        if (typeof window.requestAnimationFrame === 'function') window.requestAnimationFrame(resetRiskDetailScroll);
        setTimeout(resetRiskDetailScroll, 50);
      }
      const selectAll = document.querySelector('#workarea [data-act="stu-select-all"]');
      if (selectAll) selectAll.indeterminate = selectAll.dataset.indeterminate === 'true';
      if (curModule === 'score') {
        bindScoreTabs();
        if (scoreTab === 'compare') setTimeout(renderScoreCharts, 0);
      }
    }

    const renderers = {
      dash: renderDash,
      risk: renderRiskDetail,
      stu: renderStu,
      score: renderScore,
      dictation: renderDictation,
      homework: renderHomework,
      growth: function() { return window.renderGrowth ? window.renderGrowth() : '<div class="empty">成长森林模块未加载</div>'; },
      recite: renderRecite,
      writing: renderWriting,
      errors: renderErrors,
      todo: renderTodo,
      settings: renderSettings,
      teachmate: function() { return renderTeachMate(); }
    };

    function renderRateGauge(label, rate, line, color, key) {
      const value = Math.max(0, Math.min(100, Number(rate) || 0));
      return `<div class="dashboard-gauge" data-gauge="${key}" style="--gauge-color:${color}"><div class="rate-gauge-ring" style="--gauge-rate:${value}" role="img" aria-label="${label}${value.toFixed(1)}%"><strong class="rate-gauge-value">${value.toFixed(1)}%</strong></div><div class="rate-gauge-label">${label}</div><div class="rate-gauge-note">${line}</div></div>`;
    }

    function renderDashboardLevels(distribution) {
      if (!distribution.configured) return '<div class="dashboard-level-empty">本场考试尚未设置 A、B、C 分层线，暂时无法显示层级比例。</div>';
      const colors = { A: '#627a67', B: '#4d3045', C: '#a57c45', D: '#b65f42' }; /* warm-paper theme */
      const meanings = { A: '稳定领先', B: '达到预期', C: '需跟进', D: '优先干预' };
      return `<div class="dashboard-levels">${distribution.levels.map(item => `<div class="dashboard-level-row"><span class="dashboard-level-label" style="--level-color:${colors[item.level]}"><strong>${item.level}</strong><small>${meanings[item.level]}</small></span><div class="dashboard-level-track"><span style="--level-rate:${item.percentage}%;--level-color:${colors[item.level]}"></span></div><span class="dashboard-level-value">${item.count}人 · ${item.percentage}%</span></div>`).join('')}</div>`;
    }

    function renderDashboardLeaderboard(cls, students, fallbackExam) {
      // 排行榜完全跟随考试概览，不再维护独立的考试选择器。
      const selectedExam = fallbackExam || getCurrentExam();
      const scored = selectedExam ? getScoredStudents(selectedExam, students).sort((left, right) => right.score - left.score || String(left.student.name).localeCompare(String(right.student.name))) : [];
      const ranked = scored.map((item, index) => ({ ...item, rank: index > 0 && item.score === scored[index - 1].score ? scored[index - 1].rank : index + 1 })).slice(0, 10);
      const renderPodiumSlot = (item, rank) => item
        ? `<div class="dashboard-podium-slot rank-${rank}"><div class="dashboard-podium-person"><span class="dashboard-podium-medal" aria-label="第${rank}名">${rank === 1 ? '🥇' : rank === 2 ? '🥈' : '🥉'}</span><strong title="${escapeAttr(item.student?.name || '未命名学生')}">${escapeHtml(item.student?.name || '未命名学生')}</strong><small class="dashboard-podium-class">${escapeHtml(formatClassLabel(item.student?.class || '未分班'))}</small><small class="numeric">${fmt(item.score)} 分</small></div><div class="dashboard-podium-step" aria-hidden="true">${rank}</div></div>`
        : `<div class="dashboard-podium-slot rank-${rank}"><div class="dashboard-podium-person"><span class="dashboard-podium-empty">暂无</span></div><div class="dashboard-podium-step">${rank}</div></div>`;
      // 领奖台按“第二名、第一名、第三名”排列，让第一名居中显示。
      const podium = [ranked[1], ranked[0], ranked[2]].map((item, index) => renderPodiumSlot(item, [2, 1, 3][index])).join('');
      const rows = ranked.slice(3).map((item, rowIndex) => {
        const displayRank = Number.isInteger(item?.rank) && item.rank > 0 ? item.rank : rowIndex + 4;
        const studentName = item?.student?.name || '未命名学生';
        const studentClass = item?.student?.class || '未分班';
        return `<div class="dashboard-ranking-row"><span class="dashboard-ranking-rank">${displayRank}</span><div class="dashboard-ranking-name" title="${escapeAttr(studentName)}">${escapeHtml(studentName)}<span class="dashboard-ranking-class">${escapeHtml(formatClassLabel(studentClass))}</span></div><span class="dashboard-ranking-score">${fmt(item.score)}</span></div>`;
      }).join('');
      return `<div class="card dashboard-leaderboard"><div class="card-header"><div><h3 class="card-title">本次考试前十</h3><p style="margin:6px 0 0;color:var(--md-text-secondary);font-size:13px;">${escapeHtml(cls ? `${formatClassLabel(cls)} · ` : '全部班级 · ')}${escapeHtml(selectedExam?.name || '暂无考试')}</p></div></div>${ranked.length ? `<div class="dashboard-podium">${podium}</div><div class="dashboard-ranking-list">${rows || '<div class="dashboard-leaderboard-empty">仅有前三名成绩</div>'}</div>` : '<div class="dashboard-leaderboard-empty">当前考试暂无可排名成绩</div>'}</div>`;
    }

    // ---- Dashboard 趋势折线图（纯 SVG，无外部依赖） ----
    function renderDashboardTrendChart(cls) {
      const metrics = calculateHistoricalMetrics(cls);
      const valid = metrics.filter(item => item.average !== null && item.average !== undefined);
      if (!valid.length) return '<div class="dash-empty-state">暂无考试趋势数据</div>';
      const w = 460, h = 170, padL = 36, padR = 16, padT = 24, padB = 40;
      const cw = w - padL - padR, ch = h - padT - padB;
      const values = valid.map(item => item.average);
      const minV = Math.min(...values) - 5;
      const maxV = Math.max(...values) + 5;
      const range = maxV - minV || 1;
      const stepX = valid.length > 1 ? cw / (valid.length - 1) : 0;
      const points = valid.map((item, i) => {
        const x = padL + (valid.length > 1 ? i * stepX : cw / 2);
        const y = padT + ch - ((item.average - minV) / range) * ch;
        return { x, y, item };
      });
      const pathD = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ');
      const areaD = pathD + ` L${points[points.length - 1].x.toFixed(1)},${(padT + ch).toFixed(1)} L${points[0].x.toFixed(1)},${(padT + ch).toFixed(1)} Z`;
      const yTicks = 4;
      const yLines = Array.from({ length: yTicks + 1 }, (_, i) => {
        const v = minV + (range / yTicks) * i;
        const y = padT + ch - (i / yTicks) * ch;
        return `<line x1="${padL}" y1="${y.toFixed(1)}" x2="${w - padR}" y2="${y.toFixed(1)}" stroke="#EDEBF0" stroke-width="1" stroke-dasharray="3,3"/><text x="${padL - 6}" y="${(y + 3).toFixed(1)}" text-anchor="end" fill="#9B97B0" font-size="10">${v.toFixed(0)}</text>`;
      }).join('');
      const xLabels = points.map((p, i) => {
        const label = String(p.item.label || '');
        const tx = p.x;
        const anchor = i === 0 ? 'start' : i === points.length - 1 ? 'end' : 'middle';
        const truncated = label.length > 12 ? label.slice(0, 12) + '…' : label;
        return `<text x="${tx.toFixed(1)}" y="${(h - 16).toFixed(1)}" text-anchor="${anchor}" fill="#9B97B0" font-size="10">${escapeHtml(truncated)}</text>`;
      }).join('');
      const dots = points.map(p => `<circle cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="4" fill="#4262FF" stroke="#fff" stroke-width="2"><title>${escapeHtml(p.item.exam.name || '')} ${escapeHtml(p.item.exam.date || '')}\n平均分: ${p.item.average}</title></circle>`).join('');
      const valLabels = points.map(p => `<text x="${p.x.toFixed(1)}" y="${(p.y - 10).toFixed(1)}" text-anchor="middle" fill="#050038" font-size="10" font-weight="700">${p.item.average}</text>`).join('');
      return `<svg class="dash-trend-svg" viewBox="0 0 ${w} ${h}" preserveAspectRatio="xMidYMid meet" role="img" aria-label="历次考试平均分趋势"><defs><linearGradient id="dashTrendArea" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#4262FF" stop-opacity="0.12"/><stop offset="100%" stop-color="#4262FF" stop-opacity="0"/></linearGradient></defs>${yLines}${xLabels}<path d="${areaD}" fill="url(#dashTrendArea)"/><path d="${pathD}" fill="none" stroke="#4262FF" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>${dots}${valLabels}</svg>`;
    }

    // ---- Dashboard 层级分布柱状图（纯 SVG / CSS） ----
    function renderDashboardLevelBars(distribution) {
      if (!distribution.configured || !distribution.levels.length) return '<div class="dash-empty-state">本场考试尚未设置分层线，暂无层级分布数据</div>';
      const colors = { A: '#627a67', B: '#4d3045', C: '#a57c45', D: '#b65f42' }; /* warm-paper theme */
      const labels = { A: '稳定领先', B: '达到预期', C: '需跟进', D: '优先干预' };
      const maxCount = Math.max(...distribution.levels.map(l => l.count), 1);
      const bars = distribution.levels.map(item => {
        const hPct = (item.count / maxCount) * 100;
        return `<div class="dash-level-bar-col"><div class="dash-level-bar-wrap"><div class="dash-level-bar" style="--bar-color:${colors[item.level]};height:${hPct}%"><span class="dash-level-bar-count">${item.count}</span></div></div><div class="dash-level-bar-label"><strong style="color:${colors[item.level]}">${item.level}</strong><small>${labels[item.level]}</small></div><div class="dash-level-bar-pct">${item.percentage}%</div></div>`;
      }).join('');
      return `<div class="dash-level-bars">${bars}</div>`;
    }

    function renderDash() {
      const cls = normalizeClassFilter(dashboardClass);
      dashboardClass = cls;
      const list = getFilteredStudents(cls);
      const currentExam = getCurrentExam();
      const scores = currentExam ? getScoredStudents(currentExam, list).map(item => item.score) : [];
      const entranceScores = list.map(s => Number(s.english)).filter(Number.isFinite);
      const avgEntrance = avg(entranceScores);
      const examFullScore = Number(currentExam?.fullScore) || 100;
      const passLine = Number(state.settings.pass) * examFullScore / 100;
      const excellentLine = Number(state.settings.excellent) * examFullScore / 100;
      const passCount = scores.filter(s => s >= passLine).length;
      const excellentCount = scores.filter(s => s >= excellentLine).length;
      const passRate = scores.length ? ((passCount / scores.length) * 100).toFixed(1) : 0;
      const excRate = scores.length ? ((excellentCount / scores.length) * 100).toFixed(1) : 0;
      const levelDistribution = calculateAcademicLevelDistribution(currentExam, list);
      const openTodos = state.todos.filter(t => !t.done);
      const todoPreview = openTodos.slice(0, 5);
      const classCount = cls ? 1 : new Set(list.map(student => student.class)).size;
      const exams = [...state.exams].sort((left, right) => String(right.date || '').localeCompare(String(left.date || '')) || String(right.name || '').localeCompare(String(left.name || '')));
      const examPicker = exams.length
        ? `<select class="jump-select dashboard-overview-exam-select" data-act="dashboard-exam" aria-label="选择考试">${exams.map(exam => `<option value="${escapeAttr(exam.id)}" ${exam.id === currentExam?.id ? 'selected' : ''}>${escapeHtml(exam.name)}${exam.date ? `（${escapeHtml(exam.date)}）` : ''}</option>`).join('')}</select>`
        : '<span class="badge">暂无考试</span>';
      const classPicker = `<select class="jump-select dashboard-overview-class-select" data-act="dashboard-class" aria-label="选择仪表盘班级">${classSelectOptions(cls)}</select>`;
      const hasExamData = scores.length > 0;
      const examName = escapeHtml(currentExam?.name || '无');

      // ---- Row 1: 4 KPI Cards ----
      const kpiHtml = `
        <div class="dash-kpi-grid">
          <div class="dash-kpi dash-kpi-yellow">
            <div class="dash-kpi-icon"><span class="material-symbols-rounded">groups</span></div>
            <div class="dash-kpi-body"><div class="dash-kpi-value">${list.length}</div><div class="dash-kpi-label">班级人数</div><div class="dash-kpi-sub">${classCount || 0} 个班级</div></div>
          </div>
          <div class="dash-kpi dash-kpi-blue">
            <div class="dash-kpi-icon"><span class="material-symbols-rounded">school</span></div>
            <div class="dash-kpi-body"><div class="dash-kpi-value">${avgEntrance}</div><div class="dash-kpi-label">${subjectHtml('entrance_score')}均分</div><div class="dash-kpi-sub">${entranceScores.length ? `已录入 ${entranceScores.length} 人` : '暂无入学成绩'}</div></div>
          </div>
          <div class="dash-kpi dash-kpi-teal">
            <div class="dash-kpi-icon"><span class="material-symbols-rounded">check_circle</span></div>
            <div class="dash-kpi-body"><div class="dash-kpi-value">${hasExamData ? passRate + '%' : '--'}</div><div class="dash-kpi-label">及格率</div><div class="dash-kpi-sub">${hasExamData ? `≥${fmt(passLine)}分 · ${passCount}人` : '暂无考试数据'}</div></div>
          </div>
          <div class="dash-kpi dash-kpi-pink">
            <div class="dash-kpi-icon"><span class="material-symbols-rounded">workspace_premium</span></div>
            <div class="dash-kpi-body"><div class="dash-kpi-value">${hasExamData ? excRate + '%' : '--'}</div><div class="dash-kpi-label">优秀率</div><div class="dash-kpi-sub">${hasExamData ? `≥${fmt(excellentLine)}分 · ${excellentCount}人` : '暂无考试数据'}</div></div>
          </div>
        </div>`;

      // ---- Row 2: Level bars + Trend chart ----
      const chartsHtml = `
        <div class="dash-charts-row">
          <div class="card dash-chart-card">
            <div class="dash-card-head"><h3 class="dash-card-title">学生层级分布</h3><span class="dash-card-sub">${escapeHtml(cls ? formatClassLabel(cls) : '全部班级')} · ${escapeHtml(examName)}</span></div>
            <div class="dash-chart-body">${renderDashboardLevelBars(levelDistribution)}</div>
          </div>
          <div class="card dash-chart-card">
            <div class="dash-card-head"><h3 class="dash-card-title">历次考试趋势</h3><span class="dash-card-sub">平均分变化</span></div>
            <div class="dash-chart-body">${renderDashboardTrendChart(cls)}</div>
          </div>
        </div>`;

      // ---- Row 3: Exam meta (secondary metrics) + pickers ----
      const examMetaHtml = `
        <div class="card dash-exam-meta-card">
          <div class="dash-card-head">
            <div><h3 class="dash-card-title">最近考试概览</h3><span class="dash-card-sub">${examName}${currentExam?.date ? ' · ' + escapeHtml(currentExam.date) : ''}</span></div>
            <div class="dash-exam-pickers">${classPicker}${examPicker}</div>
          </div>
          <div class="dash-meta-row">
            <div class="dash-meta-item"><span class="dash-meta-label">满分</span><strong class="dash-meta-value">${fmt(examFullScore)}</strong></div>
            <div class="dash-meta-item"><span class="dash-meta-label">实考</span><strong class="dash-meta-value">${scores.length}</strong></div>
            <div class="dash-meta-item"><span class="dash-meta-label">缺考</span><strong class="dash-meta-value">${list.length - scores.length}</strong></div>
            <div class="dash-meta-item"><span class="dash-meta-label">最高分</span><strong class="dash-meta-value">${hasExamData ? Math.max(...scores) : '--'}</strong></div>
            <div class="dash-meta-item"><span class="dash-meta-label">最低分</span><strong class="dash-meta-value">${hasExamData ? Math.min(...scores) : '--'}</strong></div>
          </div>
        </div>`;

      // ---- Row 4: Leaderboard (table card) + Todo preview ----
      const leaderboardHtml = renderDashLeaderboard(cls, list, currentExam);

      const todoHtml = `
        <div class="card dash-todo-card">
          <div class="dash-card-head"><h3 class="dash-card-title">待办速览</h3><span class="dash-card-sub">${openTodos.length} 条未完成</span></div>
          <div class="dash-todo-body">
            ${todoPreview.length ? `<ul class="dash-todo-list">${todoPreview.map(t => `<li><span class="dash-todo-title">${escapeHtml(t.title)}</span>${t.deadline ? `<span class="dash-todo-badge">${escapeHtml(t.deadline)}</span>` : ''}</li>`).join('')}</ul>` : '<div class="dash-empty-state">暂无待办</div>'}
          </div>
        </div>`;

      // ---- Row 5: Focus students (risk clouds) ----
      const riskHtml = renderRiskClouds(cls);

      return `
        ${kpiHtml}
        ${chartsHtml}
        ${examMetaHtml}
        <div class="dash-leader-todo-row">${leaderboardHtml}${todoHtml}</div>
        ${riskHtml}
      `;
    }

    // ---- Dashboard Leaderboard (横向 table card) ----
    function renderDashLeaderboard(cls, students, fallbackExam) {
      const selectedExam = fallbackExam || getCurrentExam();
      const scored = selectedExam ? getScoredStudents(selectedExam, students).sort((left, right) => right.score - left.score || String(left.student.name).localeCompare(String(right.student.name))) : [];
      const ranked = scored.map((item, index) => ({ ...item, rank: index > 0 && item.score === scored[index - 1].score ? scored[index - 1].rank : index + 1 })).slice(0, 10);
      const rows = ranked.length ? ranked.map((item, rowIndex) => {
        const displayRank = Number.isInteger(item?.rank) && item.rank > 0 ? item.rank : rowIndex + 1;
        const studentName = item?.student?.name || '未命名学生';
        const studentClass = item?.student?.class || '未分班';
        const rankClass = displayRank <= 3 ? `dash-rank-${displayRank}` : '';
        return `<tr class="${rankClass}"><td class="dash-lb-rank">${displayRank}</td><td class="dash-lb-name">${escapeHtml(studentName)}</td><td class="dash-lb-class">${escapeHtml(formatClassLabel(studentClass))}</td><td class="dash-lb-score">${fmt(item.score)}</td></tr>`;
      }).join('') : '';
      return `<div class="card dash-leaderboard-card">
        <div class="dash-card-head"><h3 class="dash-card-title">本次考试前十</h3><span class="dash-card-sub">${escapeHtml(cls ? cls + '班 · ' : '全部班级 · ')}${escapeHtml(selectedExam?.name || '暂无考试')}</span></div>
        ${ranked.length ? `<table class="dash-lb-table"><thead><tr><th>#</th><th>姓名</th><th>班级</th><th class="dash-lb-score-head">分数</th></tr></thead><tbody>${rows}</tbody></table>` : '<div class="dash-empty-state">当前考试暂无可排名成绩</div>'}
      </div>`;
    }

    function renderDictationCard() {
      const cls = normalizeClassFilter(dictationClass);
      const classesToShow = cls ? [cls] : ((state.classes && state.classes.length) ? state.classes : [...new Set(state.students.map(s => s.class))].sort());
      const names = Array.isArray(state.dictationNames) ? state.dictationNames : [];
      const allRounds = Object.values(state.dictation).flat().filter(v => v !== '' && v != null);
      const avgDict = allRounds.length ? (allRounds.reduce((a,b)=>a+Number(b),0)/allRounds.length).toFixed(1) : '—';
      const recordedStu = Object.values(state.dictation).filter(arr => Array.isArray(arr) && arr.some(v => v !== '' && v != null)).length;
      const roundTh = names.map(nm => `<th class="text-right">${escapeHtml(nm)}</th>`).join('');
      let html = `
        <div class="card"><div class="card-header"><h3 class="card-title">默写成绩（按学号排列）</h3>
          <span class="badge badge-blue">已录 ${recordedStu}/${state.students.length} 人 · ${names.length} 轮均分 ${avgDict}</span></div>
          <div class="card-body">`;
      classesToShow.forEach(c => {
        const list = state.students.filter(s => s.class === c).sort((a,b)=>String(a.id).localeCompare(String(b.id)));
        html += `<div style="margin-bottom:16px;overflow-x:auto;">
          <div style="font-weight:600;margin-bottom:6px;">${escapeHtml(formatClassLabel(c))}（${list.length}人）</div>
          <table><thead><tr><th>学号</th><th>姓名</th><th>班级</th>${roundTh}</tr></thead><tbody>
            ${list.map(s=>`<tr><td>${escapeHtml(s.id)}</td><td>${escapeHtml(s.name)}</td><td>${escapeHtml(s.class)}</td>
              ${names.map((nm, i) => `<td class="text-right" contenteditable="true" data-act="dict-edit" data-sid="${escapeAttr(s.id)}" data-r="${i+1}">${escapeHtml((state.dictation[s.id]||[])[i] ?? '')}</td>`).join('')}</tr>`).join('')}
          </tbody></table>
        </div>`;
      });
      html += `</div></div>`;
      return html;
    }

    function splitAssessmentStudents(list) {
      const groups = { left: [], right: [] };
      (Array.isArray(list) ? list : []).forEach(student => {
        const suffix = Number(String(student.id || '').slice(-2));
        if (Number.isFinite(suffix) && suffix <= 30) groups.left.push(student);
        else groups.right.push(student);
      });
      return groups;
    }

    function renderAssessmentSplitTables(list, renderTable, leftLabel = '', rightLabel = '') {
      const groups = splitAssessmentStudents(list);
      return `<div class="writing-gender-grid"><div class="writing-gender-column">${leftLabel ? `<h4>${escapeHtml(leftLabel)}</h4>` : ''}${renderTable(groups.left)}</div><div class="writing-gender-column">${rightLabel ? `<h4>${escapeHtml(rightLabel)}</h4>` : ''}${renderTable(groups.right)}</div></div>`;
    }

    function renderRecordModeToggle(module) {
      const mode = recordViewModes[module] || 'single';
      const nextMode = mode === 'all' ? 'single' : 'all';
      const label = mode === 'all' ? '返回单次成绩' : '查看全部记录';
      return `<button class="btn btn-secondary record-mode-button" data-act="record-view-mode" data-module="${module}" data-mode="${nextMode}">${label}</button>`;
    }

    function renderAllRecordsTable(module, list) {
      const students = [...(Array.isArray(list) ? list : [])].sort((a, b) => String(a.id).localeCompare(String(b.id)));
      let columns = [];
      let cell = () => '';
      if (module === 'score') {
        columns = (state.exams || []).map(exam => ({ id: exam.id, label: exam.name || '未命名考试', date: exam.date }));
        cell = (student, column) => { const exam = state.exams.find(item => item.id === column.id); const score = getExamScore(exam, student); return score === null ? '—' : String(score); };
      } else if (module === 'dictation') {
        columns = (state.dictationNames || []).map((name, index) => ({ id: index, label: name }));
        cell = (student, column) => { const value = (state.dictation[student.id] || [])[column.id]; return value === '' || value == null ? '—' : String(value); };
      } else if (module === 'recite') {
        columns = (state.recitations || []).map(task => ({ id: task.id, label: task.title || '未命名背诵' }));
        cell = (student, column) => { const status = normalizeReciteStatus((state.recitations.find(task => task.id === column.id) || {}).status?.[student.id]); return status.level || '—'; };
      } else if (module === 'writing') {
        columns = (state.writings || []).map(task => ({ id: task.id, label: task.title || '未命名写作' }));
        cell = (student, column) => { const value = (state.writings.find(task => task.id === column.id) || {}).scores?.[student.id]; return value === '' || value == null ? '—' : String(value); };
      } else if (module === 'homework') {
        columns = (state.homeworkTasks || []).map(task => ({ id: task.id, label: task.name || '未命名作业', date: task.date }));
        cell = (student, column) => ((state.homeworkRecords?.[column.id] || {})[student.id] === true ? '已交' : '未交');
      }
      if (!columns.length) return '<div class="empty">暂无历史记录</div>';
      const gridColumns = `repeat(${columns.length + 3}, minmax(110px, auto))`;
      const header = `<div class="all-records-row all-records-header" role="row" style="grid-template-columns:${gridColumns}"><div role="columnheader">班级</div><div role="columnheader">学号</div><div role="columnheader">姓名</div>${columns.map(column => `<div role="columnheader" title="${escapeAttr(column.label)}${column.date ? ` · ${column.date}` : ''}">${escapeHtml(column.label)}</div>`).join('')}</div>`;
      const rows = students.map(student => `<div class="all-records-row" role="row" style="grid-template-columns:${gridColumns}"><div role="cell">${escapeHtml(formatClassLabel(student.class || ''))}</div><div role="cell" class="numeric">${escapeHtml(student.id)}</div><div role="cell">${escapeHtml(student.name)}</div>${columns.map(column => `<div role="cell" class="text-center">${escapeHtml(cell(student, column))}</div>`).join('')}</div>`).join('');
      return `<div class="all-records-wrap"><div class="all-records-table" role="table" aria-label="${escapeAttr(module)}全部记录">${header}<div role="rowgroup">${rows}</div></div></div>`;
    }

    function renderAllRecordsPanel(module, list, title) {
      return `<div class="card record-history-panel"><div class="card-header"><h3 class="card-title">${escapeHtml(title)}</h3><div style="display:flex;align-items:center;gap:10px;"><span class="badge badge-blue">按学生汇总 · 只读</span><button class="btn btn-sm btn-secondary" data-act="export-records" data-module="${escapeAttr(module)}">导出 Excel</button></div></div><div class="card-body" style="padding:0;overflow:auto;">${renderAllRecordsTable(module, list)}</div></div>`;
    }

    function renderRecordTwoColumn(module, singlePanel, historyPanel) {
      return recordViewModes[module] === 'all' ? historyPanel : singlePanel;
    }

    function renderDictation() {
      const cls = normalizeClassFilter(dictationClass);
      dictationClass = cls;
      const list = getVisibleStudents(cls, dictationSearchText).sort((a,b)=>String(a.id).localeCompare(String(b.id)));
      const names = Array.isArray(state.dictationNames) ? state.dictationNames : [];
      if (!Number.isInteger(dictationRoundIndex) || dictationRoundIndex < 0 || dictationRoundIndex >= names.length) dictationRoundIndex = 0;
      const activeName = names[dictationRoundIndex];
      const activeValues = list.map(s => (state.dictation[s.id] || [])[dictationRoundIndex]).filter(v => v !== '' && v != null && Number.isFinite(Number(v)));
      const activeStats = getDictationRoundStats(dictationRoundIndex, list);
      const average = activeValues.length ? (activeValues.reduce((sum, value) => sum + Number(value), 0) / activeValues.length).toFixed(1) : '—';
      const searchValue = String(dictationSearchText).replace(/"/g, '&quot;');
      const searchBox = `<span class="search-box"><span class="material-symbols-rounded">search</span><input type="search" data-act="dict-search" value="${searchValue}" placeholder="搜索当前班级姓名/学号" aria-label="搜索默写成绩学生姓名或学号">${dictationSearchText ? '<button class="icon-btn" data-act="dict-search-clear" title="清除搜索" aria-label="清除默写搜索"><span class="material-symbols-rounded">close</span></button>' : ''}</span>`;
      const allRoundsSelected = names.length > 0 && names.every((nm, index) => selectedDictationRounds.has(index));
      const toolbar = `<div class="toolbar">${searchBox}<button class="btn btn-primary" data-act="dict-add-round">+ 添加默写轮次</button><button class="btn btn-secondary" data-act="dict-batch-toggle">${dictationBatchMode ? '退出批量管理' : '批量管理'}</button><button class="btn btn-danger-outline" data-act="dict-delete-round" ${names.length === 0 ? 'disabled' : ''}>删除轮次</button><select class="jump-select dict-toolbar-select" data-act="dict-class-select" aria-label="选择默写成绩班级">${classSelectOptions(cls)}</select>${dictationBatchMode ? `<button class="btn btn-sm btn-secondary" data-act="dict-round-select-all">${allRoundsSelected ? '取消全选' : '全选轮次'}</button><button class="btn btn-sm btn-danger-outline" data-act="dict-round-delete-selected" ${selectedDictationRounds.size ? '' : 'disabled'}>删除选中轮次</button><span style="font-size:12px;color:var(--text-secondary);">已选${selectedDictationRounds.size}轮</span>` : ''}</div>`;
      if (!names.length) {
        return `${toolbar}<div class="card"><div class="card-header"><h3 class="card-title">默写轮次</h3><span class="badge badge-blue">0 轮</span></div><div class="card-body"><div class="empty">暂无默写轮次，请点击“+ 添加默写轮次”开始记录。</div></div></div>${renderRecordModeToggle('dictation')}`;
      }
      const roundCards = names.map((name, index) => {
        const values = list.map(s => (state.dictation[s.id] || [])[index]).filter(v => v !== '' && v != null && Number.isFinite(Number(v)));
        const stats = getDictationRoundStats(index, list);
        const selected = index === dictationRoundIndex;
        const rangeSummary = stats.ranges.length ? stats.ranges.map(range => `${escapeHtml(dictationRangeLabel(range))} ${range.count}人`).join(' · ') : '未设置分数段';
        const passCount = values.filter(v => Number(v) >= Number(state.settings.pass || 60)).length;
        const avgScore = values.length ? (values.reduce((sum, value) => sum + Number(value), 0) / values.length).toFixed(1) : '—';
        const batchChecked = selectedDictationRounds.has(index);
        const batchCheckbox = dictationBatchMode ? `<label class="dictation-round-batch-cb" onclick="event.stopPropagation()"><input type="checkbox" data-act="dict-round-select-toggle" data-r="${index}" ${batchChecked ? 'checked' : ''} aria-label="选择${escapeAttr(name)}"></label>` : '';
        const statsBtn = !dictationBatchMode ? `<button class="dictation-round-stats-btn icon-btn" data-act="dict-round-stats" data-r="${index}" title="统计设置" aria-label="${escapeAttr(name)}统计设置"><span class="material-symbols-rounded" aria-hidden="true">tune</span></button>` : '';
        const cardClass = dictationBatchMode ? (batchChecked ? 'batch-selected' : '') : (selected ? 'active' : '');
        const titleAttrs = dictationBatchMode ? '' : ' contenteditable="true" data-act="dict-name" data-r="' + index + '"';
        return `<div class="writing-task-card dictation-round-card ${cardClass}" data-act="dict-round-select" data-r="${index}" tabindex="0" role="button" aria-label="选择默写轮次${escapeAttr(name)}">
          <div class="dictation-round-title-row">
            <strong class="dictation-round-title"${titleAttrs} spellcheck="false">${escapeHtml(name)}</strong>
            ${batchCheckbox}${statsBtn}
          </div>
          <div class="dictation-round-data-row">
            <div class="dictation-round-avg"><strong class="dictation-round-avg-val">${avgScore}</strong><span class="dictation-round-avg-label">平均分</span></div>
            <div class="dictation-round-stats">
              <span class="dictation-round-stat">已录<b>${stats.recorded}</b>人</span>
              <span class="dictation-round-stat">满分<b>${stats.fullScore}</b></span>
              <span class="dictation-round-stat">及格<b>${passCount}</b></span>
            </div>
          </div>
          <div class="dictation-round-range-line">${rangeSummary}</div>
        </div>`;
      }).join('');
      const renderTable = students => students.length ? `<table class="writing-table"><colgroup><col style="width:33.33%"><col style="width:33.33%"><col style="width:33.33%"></colgroup><thead><tr><th>学号</th><th>姓名</th><th class="text-center writing-score-col">默写成绩</th></tr></thead><tbody>${students.map(student => `<tr><td>${escapeHtml(student.id)}</td><td>${escapeHtml(student.name)}</td><td class="text-center writing-score-col" contenteditable="true" data-act="dict-edit" data-sid="${escapeAttr(student.id)}" data-r="${dictationRoundIndex + 1}">${escapeHtml((state.dictation[student.id] || [])[dictationRoundIndex] ?? '')}</td></tr>`).join('')}</tbody></table>` : '<div class="empty">暂无学生</div>';
      const singlePanel = `<div class="card"><div class="card-header"><h3 class="card-title">${escapeHtml(activeName)} · 成绩表</h3><span class="badge badge-blue">平均分 ${average} · 满分 ${activeStats.fullScore} 人 · 已录 ${activeStats.recorded} 人</span></div><div class="card-body" style="padding:0;overflow:auto;">${renderAssessmentSplitTables(list, renderTable)}</div></div>`;
      const recordsBody = `${renderRecordModeToggle('dictation')}${renderRecordTwoColumn('dictation', singlePanel, renderAllRecordsPanel('dictation', list, '全部默写记录'))}`;
      return `${toolbar}<div class="card"><div class="card-header"><h3 class="card-title">默写轮次</h3><span class="badge badge-blue">${names.length} 轮</span></div><div class="card-body"><div class="writing-task-grid">${roundCards}</div></div></div>${recordsBody}`;
    }

    function renderHomework() {
      const cls = normalizeClassFilter(homeworkClass);
      homeworkClass = cls;
      const tasks = Array.isArray(state.homeworkTasks) ? state.homeworkTasks : [];
      const records = state.homeworkRecords || {};
      const list = getVisibleStudents(cls, homeworkSearchText).sort((a, b) => String(a.id).localeCompare(String(b.id)));
      // 确保当前选中的作业有效
      if (!tasks.length || !tasks.find(t => t.id === homeworkActiveTaskId)) {
        homeworkActiveTaskId = tasks.length ? tasks[tasks.length - 1].id : '';
      }
      const activeTask = tasks.find(t => t.id === homeworkActiveTaskId);
      const searchValue = String(homeworkSearchText).replace(/"/g, '&quot;');
      const searchBox = `<span class="search-box"><span class="material-symbols-rounded">search</span><input type="search" data-act="hw-search" value="${searchValue}" placeholder="搜索当前班级姓名/学号" aria-label="搜索日常作业学生姓名或学号">${homeworkSearchText ? '<button class="icon-btn" data-act="hw-search-clear" title="清除搜索" aria-label="清除搜索"><span class="material-symbols-rounded">close</span></button>' : ''}</span>`;
      const allSelected = list.length > 0 && list.every(s => selectedHomeworkStudents.has(s.id));

      // 作业任务卡片
      const taskCards = tasks.length ? tasks.map(task => {
        const taskRecords = records[task.id] || {};
        const submitted = list.filter(s => taskRecords[s.id] === true).length;
        const total = list.length;
        const rate = total ? Math.round(submitted / total * 100) : 0;
        const selected = task.id === homeworkActiveTaskId;
        return `<div class="writing-task-card assessment-task-card homework-task-card ${selected ? 'active' : ''}" data-act="hw-task-select" data-id="${escapeAttr(task.id)}" tabindex="0" role="button" aria-label="选择作业${escapeAttr(task.name)}">
          <div class="assessment-task-head">
            <strong title="${escapeAttr(task.name)}">${escapeHtml(task.name)}</strong>
            <button class="btn btn-sm assessment-task-action" data-act="hw-detail" data-id="${escapeAttr(task.id)}">详情</button>
          </div>
          <div class="assessment-task-meta">
            <span>${escapeHtml(task.date || '未设日期')}</span>
            <span>已交 ${submitted}/${total} 人</span>
          </div>
          <div class="assessment-task-metric"><span>提交率</span><strong>${rate}%</strong></div>
        </div>`;
      }).join('') : '<div class="empty" style="grid-column:1/-1;">暂无作业任务，点击「+ 添加作业」创建</div>';

      // 学生表格
      const renderTable = students => students.length ? `<table class="writing-table"><colgroup><col style="width:30%"><col style="width:30%"><col style="width:40%"></colgroup><thead><tr><th>学号</th><th>姓名</th><th class="text-center writing-score-col">作业状态</th></tr></thead><tbody>${students.map(student => {
        const submitted = activeTask ? (records[activeTask.id] || {})[student.id] === true : false;
        const isSel = selectedHomeworkStudents.has(student.id);
        return `<tr>
          <td>${escapeHtml(student.id)}</td>
          <td>${escapeHtml(student.name)}</td>
          <td class="text-center writing-score-col">
            ${homeworkBatchMode ? `<input type="checkbox" data-act="hw-select" data-id="${escapeAttr(student.id)}" ${isSel ? 'checked' : ''} aria-label="选择${escapeAttr(student.name)}">` : `<button class="hw-toggle ${submitted ? 'hw-done' : 'hw-undone'}" data-act="hw-toggle" data-id="${escapeAttr(student.id)}" aria-label="${submitted ? '已交' : '未交'}">${submitted ? '✓' : '✗'}</button>`}
          </td>
        </tr>`;
      }).join('')}</tbody></table>` : '<div class="empty">暂无学生</div>';

      // 批量工具栏
      const batchTools = homeworkBatchMode ? `<div class="batch-toolbar">
        <strong>已选择 ${selectedHomeworkStudents.size} 人</strong>
        <button class="btn btn-sm" data-act="hw-select-visible" ${list.length ? '' : 'disabled'}>选择本页</button>
        <button class="btn btn-sm btn-text" data-act="hw-clear-selection">取消选择</button>
        <button class="btn btn-sm btn-success" data-act="hw-batch-mark-done" ${selectedHomeworkStudents.size ? '' : 'disabled'}>标记已交</button>
        <button class="btn btn-sm" data-act="hw-batch-mark-undone" ${selectedHomeworkStudents.size ? '' : 'disabled'}>标记未交</button>
        <button class="btn btn-sm btn-text" data-act="hw-batch-exit">退出批量管理</button>
      </div>` : '';

      const activeStats = activeTask ? (() => {
        const rec = records[activeTask.id] || {};
        const submitted = list.filter(s => rec[s.id] === true).length;
        const notSubmitted = list.length - submitted;
        return { submitted, notSubmitted, total: list.length, rate: list.length ? Math.round(submitted / list.length * 100) : 0 };
      })() : null;

      const singlePanel = activeTask ? `<div class="card"><div class="card-header"><h3 class="card-title">${escapeHtml(activeTask.name)} · 提交情况</h3><span class="badge badge-blue">已交 ${activeStats.submitted} 人 · 未交 ${activeStats.notSubmitted} 人 · 提交率 ${activeStats.rate}%</span></div><div class="card-body" style="padding:0;overflow:auto;">${renderAssessmentSplitTables(list, renderTable)}</div></div>` : '<div class="card"><div class="empty">暂无作业任务</div></div>';
      const homeworkRecordsBody = `${renderRecordModeToggle('homework')}${renderRecordTwoColumn('homework', singlePanel, renderAllRecordsPanel('homework', list, '全部作业记录'))}`;
      return `<div class="toolbar">
        ${searchBox}
        <button class="btn btn-primary" data-act="hw-add-task">+ 添加作业</button>
        <button class="btn btn-secondary" data-act="hw-batch-toggle">${homeworkBatchMode ? '退出批量管理' : '批量管理'}</button>
        ${tasks.length ? `<button class="btn btn-danger-outline" data-act="hw-delete-task">删除作业</button>` : ''}
        <select class="jump-select" data-act="hw-class-select" aria-label="选择日常作业班级">${classSelectOptions(cls)}</select>
      </div>
      ${batchTools}
      <div class="card"><div class="card-header"><h3 class="card-title">作业任务</h3><span class="badge badge-blue">${tasks.length} 次作业</span></div><div class="card-body"><div class="writing-task-grid">${taskCards}</div></div></div>
      ${homeworkRecordsBody}`;
    }

    function renderRoster(selectedClass = '', filteredStudents = null) {
      let html = `<div class="card"><div class="card-header"><h3 class="card-title">班级名单（按学号排列）</h3></div><div class="card-body">`;
      const classes = selectedClass ? [selectedClass] : getAvailableClasses();
      classes.forEach(c => {
        const source = Array.isArray(filteredStudents) ? filteredStudents : state.students;
        const list = source.filter(s => s.class === c).sort((a,b)=>String(a.id).localeCompare(String(b.id)));
        html += `<div style="margin-bottom:14px;overflow-x:auto;">
          <div style="font-weight:600;margin-bottom:6px;">${escapeHtml(formatClassLabel(c))}（${list.length}人）</div>
          <table><thead><tr><th>学号</th><th>姓名</th><th>班级</th></tr></thead><tbody>
            ${list.map(s=>`<tr><td>${escapeHtml(s.id)}</td><td>${escapeHtml(s.name)}</td><td>${escapeHtml(s.class)}</td></tr>`).join('')}
          </tbody></table>
        </div>`;
      });
      html += `</div></div>`;
      return html;
    }

    function renderStu() {
      const cls = normalizeClassFilter(studentClass);
      studentClass = cls;
      const { filtered, pageItems: list, totalPages, start } = getStudentPageData();
      const selectedVisible = list.filter(s => selectedStudentIds.has(s.id));
      const allSelected = list.length > 0 && selectedVisible.length === list.length;
      const searchValue = String(stuSearchText).replace(/"/g, '&quot;');
      const searchBox = `<span class="search-box"><span class="material-symbols-rounded">search</span><input type="search" id="stuSearch" data-act="stu-search" placeholder="搜索姓名/学号" value="${searchValue}" aria-label="搜索姓名或学号">${stuSearchText ? '<button class="icon-btn" data-act="stu-search-clear" title="清除搜索" aria-label="清除搜索"><span class="material-symbols-rounded">close</span></button>' : ''}</span>`;
      const batchTools = batchMode ? `<div class="batch-toolbar"><strong>已选择 ${selectedStudentIds.size} 人</strong><button class="btn btn-sm" data-act="stu-select-visible" ${list.length ? '' : 'disabled'}>选择本页</button><button class="btn btn-sm btn-text" data-act="stu-clear-selection">取消选择</button><button class="btn btn-sm" data-act="stu-batch-archive" ${selectedStudentIds.size ? '' : 'disabled'}>批量归档</button><button class="btn btn-sm btn-text" data-act="stu-batch-exit">退出批量管理</button></div>` : '';
      const empty = stuSearchText ? '<div class="empty">未找到学生</div>' : '<div class="empty">暂无学生</div>';
      const pageEnd = Math.min(start + list.length, filtered.length);
      return `
        <div class="toolbar">
          <button class="btn btn-primary" data-act="stu-add">+ 新增学生</button>
          <select class="jump-select" data-act="stu-class" aria-label="选择学生管理班级">${classSelectOptions(cls)}</select>
          ${searchBox}
          <button class="btn btn-secondary" data-act="stu-export">导出Excel</button>
          <button class="btn btn-secondary" data-act="stu-batch-toggle">${batchMode ? '批量管理中' : '批量管理'}</button>
        </div>
        ${batchTools}
        <div class="card student-table-card"><div class="card-body" style="padding:0;overflow:auto;">
          ${list.length ? `<table class="student-table"><thead><tr>${batchMode ? `<th class="student-select-col"><input type="checkbox" data-act="stu-select-all" ${allSelected?'checked':''} ${selectedVisible.length && !allSelected?'data-indeterminate="true"':''} aria-label="选择本页学生"></th>` : ''}<th>学号</th><th class="student-name-col">姓名</th><th>班级</th><th class="text-right">${subjectHtml('entrance_score')}</th><th>家长电话</th><th class="student-actions-col"><span class="sr-only">操作</span></th></tr></thead><tbody>
            ${list.map(s=>{
                const rawPhone = s.phone || '';
                const phoneIsVisible = visiblePhoneStudentIds.has(String(s.id));
                const displayPhone = rawPhone ? (phoneIsVisible ? escapeHtml(rawPhone) : '••••••••') : '—';
                return `<tr data-act="stu-detail" data-id="${escapeAttr(s.id)}" tabindex="0" aria-label="打开${escapeAttr(s.name)}的学生档案" class="student-row ${selectedStudentIds.has(s.id)?'selected-row':''}">${batchMode ? `<td class="student-select-col"><input type="checkbox" data-act="stu-select" data-id="${escapeAttr(s.id)}" ${selectedStudentIds.has(s.id)?'checked':''} aria-label="选择${escapeAttr(s.name)}"></td>` : ''}
                <td class="numeric">${escapeHtml(s.id)}</td><td class="student-name-col"><strong>${escapeHtml(s.name)}</strong></td><td>${escapeHtml(formatClassLabel(s.class))}</td>
                <td class="text-right numeric">${s.english ?? '—'}</td><td class="phone-cell"><span class="phone-content"><span class="phone-value ${phoneIsVisible ? 'revealed' : 'masked'}">${displayPhone}</span>${rawPhone ? `<button class="phone-toggle-btn icon-btn" data-act="stu-toggle-phone" data-id="${escapeAttr(s.id)}" title="${phoneIsVisible ? '隐藏' : '显示'}家长电话" aria-label="${phoneIsVisible ? '隐藏' : '显示'}${escapeAttr(s.name)}的家长电话" aria-pressed="${phoneIsVisible}"><span class="material-symbols-rounded">${phoneIsVisible ? 'visibility_off' : 'visibility'}</span></button>` : ''}</span></td>
                <td class="student-actions-col"><details class="row-actions"><summary data-act="row-menu-toggle" aria-label="打开${escapeAttr(s.name)}的更多操作">⋯</summary><div class="row-actions-menu"><button type="button" data-act="stu-edit" data-id="${escapeAttr(s.id)}">编辑</button><button type="button" data-act="stu-transfer" data-id="${escapeAttr(s.id)}">转班</button><button type="button" data-act="stu-archive" data-id="${escapeAttr(s.id)}">归档</button></div></details></td>
              </tr>`;
              }).join('')}
          </tbody></table>` : empty}
        </div></div>
        <div class="table-pagination" aria-label="学生列表分页"><span>显示 ${filtered.length ? start + 1 : 0}–${pageEnd} / 共 ${filtered.length} 人</span><label>每页 <select data-act="student-page-size" aria-label="每页显示人数"><option value="30" ${studentPageSize===30?'selected':''}>30</option><option value="50" ${studentPageSize===50?'selected':''}>50</option></select></label><button class="btn btn-sm" data-act="student-page-prev" ${studentPage<=1?'disabled':''}>上一页</button><span class="numeric">${studentPage} / ${totalPages}</span><button class="btn btn-sm" data-act="student-page-next" ${studentPage>=totalPages?'disabled':''}>下一页</button></div>`;
    }

    function renderScore() {
      const cls = getScoreClass();
      const clsLabel = cls ? formatClassLabel(cls) : '全部汇总';
      const subtabs = [{ v: '', label: '全部汇总' }, ...state.classes.map(c => ({ v: c, label: formatClassLabel(c) }))];
      return `
        <div class="subtabs">
          ${subtabs.map(s => `<div class="subtab ${scoreClass===s.v?'active':''}" data-act="score-class" data-cls="${s.v}">${s.label}</div>`).join('')}
        </div>
        <div class="toolbar" style="background:#fff;border:1px solid var(--border);border-radius:8px;padding:10px 12px;margin-bottom:12px;">
          <span style="font-weight:600;">${clsLabel} ${subjectHtml('score_column')}：</span>
          <button class="btn btn-sm ${scoreSort==='id'?'btn-secondary':''}" data-act="score-sort" data-sort="id">按学号</button>
          <button class="btn btn-sm ${scoreSort==='scoreDesc'?'btn-secondary':''}" data-act="score-sort" data-sort="scoreDesc">成绩高→低</button>
          <button class="btn btn-sm ${scoreSort==='scoreAsc'?'btn-secondary':''}" data-act="score-sort" data-sort="scoreAsc">成绩低→高</button>
        </div>
        <div class="tabs">
          <div class="tab ${scoreTab==='table'?'active':''}" data-tab="table">成绩表</div>
          <div class="tab ${scoreTab==='compare'?'active':''}" data-tab="compare">历次对比</div>
          <div class="tab ${scoreTab==='tier'?'active':''}" data-tab="tier">分层分析</div>
        </div>
        <div id="scorePanel">${scorePanels[scoreTab]()}</div>`;
    }

    const scorePanels = {
      table: () => {
        const cls = getScoreClass();
        const exam = getCurrentExam();
        const all = getFilteredStudents(cls);
        let list = [...all];
        const scoreOf = student => getExamScore(exam, student);
        const pendingCount = pendingScoreEdits.size;
        const isMoniExam = String(exam?.id || '').startsWith('moni:');
        const hasRecordedScores = exam ? getScoredStudents(exam, state.students).length > 0 : false;
        const sourceWarning = isMoniExam && !hasRecordedScores
          ? `<div class="score-source-warning" role="status"><span class="material-symbols-rounded" aria-hidden="true">info</span><div><strong>名单和考试已同步，${subjectHtml('score_single')}尚未提供</strong><span>MONI 当前返回的${subjectHtml('score_short')}与排名为空；WorkBench 已保留“—”，没有用四科总分替代。</span></div></div>`
          : '';
        if (scoreSort === 'id') {
          list.sort((a,b) => String(a.id).localeCompare(String(b.id)));
        } else if (scoreSort === 'scoreDesc') {
          list.sort((a,b) => (scoreOf(b) ?? -Infinity) - (scoreOf(a) ?? -Infinity));
        } else if (scoreSort === 'scoreAsc') {
          list.sort((a,b) => (scoreOf(a) ?? Infinity) - (scoreOf(b) ?? Infinity));
        }
        const rankMap = calculateClassRanks(exam, state.students);
        return `
          <div class="toolbar">
            <button class="btn btn-primary" data-act="score-add-exam">+ 新增考试</button>
            <label style="display:inline-flex;align-items:center;gap:6px;">当前考试：<select id="score-exam-select" data-act="score-exam" class="jump-select" style="min-width:220px;">${state.exams.map(item => `<option value="${escapeAttr(item.id)}" ${item.id===currentExamId?'selected':''}>${escapeHtml(item.name)}${item.date?'（'+escapeHtml(item.date)+'）':''}</option>`).join('')}</select></label>
            <span>满分：${exam?.fullScore||100}</span>
            <button class="btn btn-secondary" data-act="score-batch-paste" ${exam ? '' : 'disabled'}>批量粘贴</button>
            <button class="btn btn-secondary" data-act="score-item-upload" ${exam ? '' : 'disabled'}>上传小分</button>
            ${DATABASE_MODE ? `<button class="btn btn-secondary" data-act="score-paper-settings" ${exam ? '' : 'disabled'}>考试设置</button>` : ''}
            <button class="btn ${pendingCount ? 'btn-primary' : 'btn-secondary'}" data-act="score-save-pending" ${pendingCount ? '' : 'disabled'}>保存成绩更改${pendingCount ? `（${pendingCount}）` : ''}</button>
            <button class="btn btn-text" data-act="score-undo" ${scoreUndoStack.length ? '' : 'disabled'}>撤销</button>
            <button class="btn score-exam-action" data-act="score-edit-exam" ${exam ? '' : 'disabled'}>考试信息</button>
            <button class="btn score-exam-delete" data-act="score-del-exam" ${exam ? '' : 'disabled'}>删除考试…</button>
            ${pendingCount ? `<span class="unsaved-indicator"><span aria-hidden="true"></span>${pendingCount} 项未保存</span>` : '<span class="saved-indicator">所有更改已保存</span>'}
          </div>
          ${sourceWarning}
          <div class="card score-table-card"><div class="card-body" style="padding:0;overflow:auto;">
            <table class="score-table"><thead><tr><th>学号</th><th class="sticky-name-col">姓名</th><th>班级</th><th class="text-right">${subjectHtml('score_total')}</th><th class="text-right">班级排名</th><th class="text-right">年级排名</th></tr></thead><tbody>
              ${list.map(s => {
                const sc = getExamScore(exam, s);
                const gradeRank = getStudentGradeRank(exam, s);
                const dirty = pendingScoreEdits.has(scoreEditKey(exam?.id, s.id));
                return `<tr><td class="numeric">${escapeHtml(s.id)}</td><td class="sticky-name-col">${escapeHtml(s.name)}</td><td>${escapeHtml(formatClassLabel(s.class))}</td><td class="text-right numeric score-edit-cell ${dirty ? 'score-dirty' : ''}" contenteditable="${exam ? 'true' : 'false'}" data-act="score-edit" data-sid="${escapeAttr(s.id)}" aria-label="${escapeAttr(s.name)}${subjectHtml('score_column')}${dirty ? '，尚未保存' : ''}">${sc === null ? '—' : sc}</td><td class="text-right numeric">${rankMap[s.id] || '—'}</td><td class="text-right numeric">${gradeRank ?? '—'}</td></tr>`;
              }).join('')}
            </tbody></table>
            ${list.length?'':'<div class="empty">该班级暂无学生</div>'}
          </div></div>`;
      },
      dist: () => {
        const cls = getScoreClass();
        const exam = getCurrentExam();
        const scored = getScoredStudents(exam, getFilteredStudents(cls)).sort((a,b) => b.score - a.score);
        const rankMap = calculateClassRanks(exam, state.students);
        return `
          <div class="card"><div class="card-header"><h3 class="card-title">${escapeHtml(exam?.name || '当前考试')}成绩分布（从高到低）</h3></div>
          <div class="card-body" style="padding:0;max-height:500px;overflow:auto;">
            <table><thead><tr><th>班级排名</th><th>年级排名</th><th>姓名</th><th>班级</th><th class="text-right">成绩</th></tr></thead><tbody>
              ${scored.map(item=>`<tr><td>${rankMap[item.student.id] || '—'}</td><td>${getStudentGradeRank(exam, item.student) ?? '—'}</td><td>${escapeHtml(item.student.name)}</td><td>${escapeHtml(item.student.class)}</td><td class="text-right">${item.score}</td></tr>`).join('')}
            </tbody></table>
            ${scored.length ? '' : '<div class="empty">当前考试暂无成绩</div>'}
          </div></div>`;
      },
      compare: () => {
        const cls = getScoreClass();
        const metrics = calculateHistoricalMetrics(cls);
        const hasAnyScore = metrics.some(item => item.average !== null);
        if (!state.exams.length) return '<div class="empty">暂无考试</div>';
        return `<div class="analysis-stack">
          <div class="card analysis-card">
            <div class="analysis-card-head"><div><h3>平均分趋势</h3></div></div>
            ${hasAnyScore ? '<div id="trend-average-chart" class="trend-chart" aria-label="历次平均分趋势图"></div>' : '<div class="trend-empty">历次考试尚未录入成绩</div>'}
          </div>
          <div class="card analysis-card">
            <div class="analysis-card-head"><div><h3>年级排名趋势</h3></div></div>
            ${cls ? '<div id="trend-rank-chart" class="trend-chart" aria-label="班级年级名次趋势图"></div>' : '<div class="trend-empty">请选择班级</div>'}
          </div>
          <div class="card analysis-card">
            <div class="analysis-card-head"><div><h3>分层比例趋势</h3></div></div>
            ${hasAnyScore ? '<div id="trend-rate-chart" class="trend-chart" aria-label="分层率与达标率趋势图"></div>' : '<div class="trend-empty">历次考试尚未录入成绩</div>'}
          </div>
        </div>`;
      },
      tier: () => {
        const cls = getScoreClass();
        const exam = getCurrentExam();
        if (!exam) return '<div class="empty">暂无考试</div>';
        const students = getFilteredStudents(cls);
        const summary = calculateExamSummary(exam, students);
        const gradeRanks = calculateGradeRankDistribution(exam, students);
        const academic = calculateAcademicLevelDistribution(exam, students);
        const classGradeRank = cls ? Number(exam.classGradeRanks?.[cls]) : null;
        const validClassGradeRank = Number.isInteger(classGradeRank) && classGradeRank > 0 ? classGradeRank : null;
        const classRankBlock = cls
          ? `<strong>${validClassGradeRank ?? '—'}</strong><span>年级排名 · ${escapeHtml(formatClassLabel(cls))}</span><button class="btn btn-sm" data-act="score-class-grade-rank">${validClassGradeRank ? '修改' : '录入'}</button>`
          : '<strong>—</strong><span>年级排名 · 切换到具体班级查看</span>';
        const rankRows = gradeRanks.buckets.map(item => `<div class="rank-distribution-row"><span>${item.label}名</span><div class="rank-distribution-bar"><div style="width:${item.percentage}%"></div></div><span class="value">${item.count}人 · ${item.percentage}%</span></div>`).join('');
        const levelColors = { A: '#627a67', B: '#4d3045', C: '#a57c45', D: '#b65f42' }; /* warm-paper theme */
        return `<div class="analysis-stack">
          <div class="card analysis-card">
            <div class="analysis-card-head"><div><h3>本次考试概览</h3><p>${escapeHtml(exam.name)} · 满分${fmt(exam.fullScore || 100)} · 实考${summary.total}人</p></div></div>
            <div class="analysis-kpis">
              <div class="analysis-kpi"><strong>${summary.average ?? '—'}</strong><span>平均分</span></div>
              <div class="analysis-kpi"><strong>${summary.highest ?? '—'}</strong><span>最高分</span></div>
              <div class="analysis-kpi"><strong>${summary.excellentRate}%</strong><span>优秀率 · ≥${fmt(summary.excellentScore)}</span></div>
              <div class="analysis-kpi"><strong>${summary.passRate}%</strong><span>及格率 · ≥${fmt(summary.passScore)}</span></div>
              <div class="analysis-kpi">${classRankBlock}</div>
            </div>
          </div>
          <div class="card analysis-card">
            <div class="analysis-card-head"><div><h3>年级名次分布</h3></div><span class="badge badge-blue">已录 ${gradeRanks.validTotal} 人</span></div>
            <div class="rank-distribution-list">
              ${rankRows || '<div class="trend-empty">当前筛选下尚未录入学生年级排名</div>'}
              ${gradeRanks.missing ? `<div class="rank-distribution-row"><span>未录入</span><div class="rank-distribution-bar"><div style="width:0"></div></div><span class="value">${gradeRanks.missing}人 · 不计比例</span></div>` : ''}
            </div>
          </div>
          <div class="card analysis-card">
            <div class="analysis-card-head"><div><h3>学业等级分布</h3></div>${academic.configured ? '<button class="btn btn-sm" data-act="score-edit-exam">修改分层线</button>' : ''}</div>
            ${academic.configured
              ? `<div class="level-grid">${academic.levels.map(item => { const meaning = { A: '稳定领先', B: '达到预期', C: '需跟进', D: '优先干预' }[item.level]; return `<div class="level-card" style="--level-color:${levelColors[item.level]}"><div class="level-title">${item.level}层 · ${meaning}</div><strong>${item.count}</strong> 人 · ${item.percentage}%<small>${item.range}分</small></div>`; }).join('')}</div>`
              : '<div class="trend-empty">本次考试尚未设置A、B、C分层线<br><button class="btn btn-primary" data-act="score-edit-exam">设置本次考试分层线</button></div>'}
          </div>
        </div>`;
      }
    };

    function bindScoreTabs() {
      document.querySelectorAll('.tab').forEach(t => t.onclick = () => { scoreTab = t.dataset.tab; render(); });
      document.querySelectorAll('[data-act="score-sort"]').forEach(b => b.onclick = () => { scoreSort = b.dataset.sort; render(); });
    }

    function renderRecite() {
      const cls = normalizeClassFilter(reciteClass);
      reciteClass = cls;
      const list = getVisibleStudents(cls, reciteSearchText).sort((a,b)=>String(a.id).localeCompare(String(b.id)));
      const searchValue = String(reciteSearchText).replace(/"/g, '&quot;');
      const searchBox = `<span class="search-box"><span class="material-symbols-rounded">search</span><input type="search" data-act="recite-search" value="${searchValue}" placeholder="搜索当前班级姓名/学号" aria-label="搜索背诵成绩学生姓名或学号">${reciteSearchText ? '<button class="icon-btn" data-act="recite-search-clear" title="清除搜索" aria-label="清除背诵搜索"><span class="material-symbols-rounded">close</span></button>' : ''}</span>`;
      const tasks = Array.isArray(state.recitations) ? state.recitations : [];
      if (!tasks.some(task => task.id === reciteTaskId)) reciteTaskId = tasks[0]?.id || '';
      const activeTask = tasks.find(task => task.id === reciteTaskId) || null;
      const allReciteSelected = tasks.length > 0 && tasks.every(task => selectedReciteTasks.has(task.id));
      const taskCards = tasks.length ? tasks.map(task => {
        const counts = RECITE_LEVELS.reduce((map, level) => { map[level] = list.filter(student => normalizeReciteStatus(task.status?.[student.id]).level === level).length; return map; }, {});
        const recorded = list.filter(student => normalizeReciteStatus(task.status?.[student.id]).level).length;
        const active = task.id === reciteTaskId;
        const batchChecked = selectedReciteTasks.has(task.id);
        const batchCb = reciteBatchMode ? `<label class="dictation-round-batch-cb" onclick="event.stopPropagation()"><input type="checkbox" data-act="recite-task-select-toggle" data-id="${escapeAttr(task.id)}" ${batchChecked ? 'checked' : ''} aria-label="选择${escapeAttr(task.title || '背诵任务')}"></label>` : '';
        const cardClass = reciteBatchMode ? (batchChecked ? 'batch-selected' : '') : (active ? 'active' : '');
        return `<div class="writing-task-card ${cardClass}" data-act="recite-task-select" data-id="${escapeAttr(task.id)}" tabindex="0" role="button" aria-label="选择背诵任务${escapeAttr(task.title)}"><div><strong>${escapeHtml(task.title || '未命名背诵任务')}</strong><div class="writing-task-meta">${escapeHtml(task.scope || '未设置范围')} · 已录${recorded}人</div><div class="recite-stat-summary">${RECITE_LEVELS.map(level => `<span class="recite-stat-${level.toLowerCase()}">${level} ${counts[level]}人</span>`).join('')}</div></div><div class="writing-task-average"><strong>${recorded}</strong><span>已录人数</span></div>${batchCb}</div>`;
      }).join('') : '<div class="empty">暂无背诵任务</div>';
      const renderTable = students => students.length ? `<table class="writing-table"><colgroup><col style="width:33.33%"><col style="width:33.33%"><col style="width:33.33%"></colgroup><thead><tr><th>学号</th><th>姓名</th><th class="text-center">背诵档位</th></tr></thead><tbody>${students.map(student => {
        const status = normalizeReciteStatus(activeTask?.status?.[student.id]);
        const retake = status.level === 'F' ? `<select data-act="recite-edit-retake" data-id="${escapeAttr(activeTask.id)}" data-sid="${escapeAttr(student.id)}" aria-label="${escapeAttr(student.name)}重背结果"><option value="" ${!status.retake ? 'selected' : ''}>未记录</option><option value="passed" ${status.retake === 'passed' ? 'selected' : ''}>通过</option><option value="not_passed" ${status.retake === 'not_passed' ? 'selected' : ''}>未通过</option></select>` : '';
        return `<tr><td>${escapeHtml(student.id)}</td><td>${escapeHtml(student.name)}</td><td class="text-center"><select data-act="recite-edit-status" data-id="${escapeAttr(activeTask.id)}" data-sid="${escapeAttr(student.id)}" aria-label="${escapeAttr(student.name)}背诵档位"><option value="" ${!status.level ? 'selected' : ''}>未记录</option>${RECITE_LEVELS.map(level => `<option value="${level}" ${status.level === level ? 'selected' : ''}>${level}</option>`).join('')}</select>${retake}</td></tr>`;
      }).join('')}</tbody></table>` : '<div class="empty">暂无学生</div>';
      const singlePanel = activeTask && !reciteBatchMode ? `<div class="card"><div class="card-header"><h3 class="card-title">${escapeHtml(activeTask.title || '未命名背诵任务')} · 成绩表</h3><span class="badge badge-blue">${escapeHtml(activeTask.scope || '未设置范围')} · A/B/C/F</span></div><div class="card-body" style="padding:0;overflow:auto;">${renderAssessmentSplitTables(list, renderTable)}</div></div>` : '<div class="card"><div class="empty">请先选择一个背诵任务</div></div>';
      const table = `${renderRecordModeToggle('recite')}${renderRecordTwoColumn('recite', singlePanel, renderAllRecordsPanel('recite', list, '全部背诵记录'))}`;
      return `<div class="toolbar">${searchBox}<button class="btn btn-primary" data-act="recite-add">+ 新建背诵任务</button><button class="btn btn-secondary" data-act="recite-batch-toggle">${reciteBatchMode ? '退出批量管理' : '批量管理'}</button><select class="jump-select" data-act="recite-class" aria-label="选择背诵成绩班级">${classSelectOptions(cls)}</select>${reciteBatchMode ? `<button class="btn btn-sm btn-secondary" data-act="recite-task-select-all">${allReciteSelected ? '取消全选' : '全选轮次'}</button><button class="btn btn-sm btn-danger-outline" data-act="recite-task-delete-selected" ${selectedReciteTasks.size ? '' : 'disabled'}>删除选中轮次</button><span style="font-size:12px;color:var(--text-secondary);">已选${selectedReciteTasks.size}轮</span>` : ''}</div><div class="card"><div class="card-header"><h3 class="card-title">背诵任务</h3><span class="badge badge-blue">${tasks.length} 项</span></div><div class="card-body"><div class="writing-task-grid">${taskCards}</div></div></div>${table}`;
    }

    function renderWriting() {
      const cls = normalizeClassFilter(writingClass);
      writingClass = cls;
      const list = getVisibleStudents(cls, writingSearchText).sort((a,b)=>String(a.id).localeCompare(String(b.id)));
      const searchValue = String(writingSearchText).replace(/"/g, '&quot;');
      const searchBox = `<span class="search-box"><span class="material-symbols-rounded">search</span><input type="search" data-act="writing-search" value="${searchValue}" placeholder="搜索当前班级姓名/学号" aria-label="搜索写作成绩学生姓名或学号">${writingSearchText ? '<button class="icon-btn" data-act="writing-search-clear" title="清除搜索" aria-label="清除写作搜索"><span class="material-symbols-rounded">close</span></button>' : ''}</span>`;
      const tasks = Array.isArray(state.writings) ? state.writings : [];
      if (!tasks.some(task => task.id === writingTaskId)) writingTaskId = tasks[0]?.id || '';
      const activeTask = tasks.find(task => task.id === writingTaskId) || null;
      const average = task => {
        const values = list.map(student => Number(task?.scores?.[student.id])).filter(Number.isFinite);
        return values.length ? (values.reduce((sum, value) => sum + value, 0) / values.length).toFixed(1) : '—';
      };
      const allWritingSelected = tasks.length > 0 && tasks.every(task => selectedWritingTasks.has(task.id));
      const taskCards = tasks.length ? tasks.map(task => {
        const values = list.map(student => Number(task.scores?.[student.id])).filter(Number.isFinite);
        const active = task.id === writingTaskId;
        const batchChecked = selectedWritingTasks.has(task.id);
        const batchCb = writingBatchMode ? `<label class="dictation-round-batch-cb" onclick="event.stopPropagation()"><input type="checkbox" data-act="writing-task-select-toggle" data-id="${escapeAttr(task.id)}" ${batchChecked ? 'checked' : ''} aria-label="选择${escapeAttr(task.title)}"></label>` : '';
        const editBtn = !writingBatchMode ? `<button class="btn btn-sm" data-act="writing-edit-task" data-id="${escapeAttr(task.id)}">编辑</button>` : '';
        const cardClass = writingBatchMode ? (batchChecked ? 'batch-selected' : '') : (active ? 'active' : '');
        return `<div class="writing-task-card assessment-task-card writing-assessment-task-card ${cardClass}" data-act="writing-task-select" data-id="${escapeAttr(task.id)}" tabindex="0" role="button" aria-label="选择写作任务${escapeAttr(task.title)}">
          <div class="assessment-task-head">
            <strong title="${escapeAttr(task.title)}">${escapeHtml(task.title)}</strong>
            ${editBtn}${batchCb}
          </div>
          <div class="assessment-task-meta">
            <span>${escapeHtml(task.date || '未设日期')}</span>
            <span>满分 ${fmt(task.fullScore || 12)}</span>
            <span>已录 ${values.length} 人</span>
          </div>
          <div class="assessment-task-metric"><span>平均分</span><strong>${average(task)}</strong></div>
        </div>`;
      }).join('') : '<div class="empty">暂无写作任务</div>';
      const renderWritingGenderTable = students => students.length
        ? `<table class="writing-table"><colgroup><col style="width:33.33%"><col style="width:33.33%"><col style="width:33.33%"></colgroup><thead><tr><th>学号</th><th>姓名</th><th class="text-center writing-score-col">写作得分</th></tr></thead><tbody>${students.map(student => `<tr><td>${escapeHtml(student.id)}</td><td>${escapeHtml(student.name)}</td><td class="text-center writing-score-col" contenteditable="true" data-act="writing-edit-score" data-id="${escapeAttr(activeTask.id)}" data-sid="${escapeAttr(student.id)}">${activeTask.scores?.[student.id] ?? ''}</td></tr>`).join('')}</tbody></table>`
        : '<div class="empty">暂无学生</div>';
      const writingGenderTables = activeTask ? renderAssessmentSplitTables(list, renderWritingGenderTable) : '<div class="empty">暂无写作任务</div>';
      const singlePanel = writingBatchMode ? '<div class="card"><div class="empty">批量管理模式下暂不显示单次成绩表</div></div>' : `<div class="card"><div class="card-header"><h3 class="card-title">${activeTask ? `${escapeHtml(activeTask.title)} · 成绩表` : '写作成绩表'}</h3><span class="badge badge-blue">${activeTask ? `平均分 ${average(activeTask)} · 满分 ${fmt(activeTask.fullScore || 12)}` : '请先创建任务'}</span></div><div class="card-body" style="padding:0;overflow:auto;">${writingGenderTables}</div></div>`;
      const writingTableCard = `${renderRecordModeToggle('writing')}${renderRecordTwoColumn('writing', singlePanel, renderAllRecordsPanel('writing', list, '全部写作记录'))}`;
      return `
        <div class="toolbar">${searchBox}<button class="btn btn-primary" data-act="writing-add">+ 新建写作任务</button><button class="btn btn-secondary" data-act="writing-batch-toggle">${writingBatchMode ? '退出批量管理' : '批量管理'}</button><select class="jump-select" data-act="writing-class" aria-label="选择写作成绩班级">${classSelectOptions(cls)}</select>${writingBatchMode ? `<button class="btn btn-sm btn-secondary" data-act="writing-task-select-all">${allWritingSelected ? '取消全选' : '全选轮次'}</button><button class="btn btn-sm btn-danger-outline" data-act="writing-task-delete-selected" ${selectedWritingTasks.size ? '' : 'disabled'}>删除选中轮次</button><span style="font-size:12px;color:var(--text-secondary);">已选${selectedWritingTasks.size}轮</span>` : ''}</div>
        <div class="card"><div class="card-header"><h3 class="card-title">写作任务</h3><span class="badge badge-blue">${tasks.length} 项</span></div><div class="card-body"><div class="writing-task-grid">${taskCards}</div></div></div>
        ${writingTableCard}`;
    }

    function formatFileSize(bytes) {
      const size = Number(bytes) || 0;
      if (size < 1024) return `${size} B`;
      if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
      return `${(size / (1024 * 1024)).toFixed(1)} MB`;
    }

    function selectedErrorDocument() {
      const documents = state.paperDocuments || [];
      if (errorSelectedDocumentId && documents.some(item => item.id === errorSelectedDocumentId)) return documents.find(item => item.id === errorSelectedDocumentId);
      errorSelectedDocumentId = documents[0]?.id || '';
      return documents[0] || null;
    }

    function renderErrorDocumentPreview(documentItem) {
      if (!documentItem) return '<div class="empty">请先上传一份原卷资料。</div>';
      const type = String(documentItem.type || '').toLowerCase();
      const contentUrl = documentItem.attachmentId ? attachmentBlobUrls.get(String(documentItem.attachmentId)) : documentItem.content;
      if (documentItem.attachmentId && !contentUrl) {
        attachmentObjectUrl(documentItem.attachmentId).then(() => render()).catch(error => console.warn('附件预览加载失败', error));
        return '<div class="empty">正在加载本地预览…</div>';
      }
      if (type.includes('pdf') || documentItem.name.toLowerCase().endsWith('.pdf')) return `<iframe src="${escapeAttr(contentUrl)}" title="原卷 PDF 预览"></iframe>`;
      if (type.startsWith('image/') || /\.(png|jpe?g|gif|webp)$/i.test(documentItem.name)) return `<img src="${escapeAttr(contentUrl)}" alt="${escapeAttr(documentItem.name)}">`;
      if (documentItem.previewText) return `<div class="error-document-text">${escapeHtml(documentItem.previewText)}</div>`;
      return '<div class="empty">Word 文件已保存。当前浏览器无法直接渲染此格式，请点击“下载原卷”后使用 Word 打开。</div>';
    }

    function renderErrors() {
      const documents = state.paperDocuments || [];
      const selected = selectedErrorDocument();
      const errorRows = state.errors.length ? state.errors.map(item => {
        const source = documents.find(doc => doc.id === item.documentId);
        const sourceCell = source ? `<button type="button" class="btn btn-sm btn-text" data-act="error-doc-select" data-id="${escapeAttr(source.id)}">${escapeHtml(source.name)}</button>` : '未关联';
        return `<tr><td>${escapeHtml(item.qnum || '—')}</td><td>${escapeHtml(item.type || '—')}</td><td>${escapeHtml(item.point || '—')}</td><td>${item.page ? `第${escapeHtml(item.page)}页` : '—'}</td><td>${sourceCell}</td><td>${Number(item.count) || 0}</td><td>${escapeHtml(item.reason || '—')}</td><td><button class="btn btn-sm btn-danger" data-act="error-del" data-id="${escapeAttr(item.id)}">删除</button></td></tr>`;
      }).join('') : '<tr><td colspan="8" class="text-center">暂无错题记录，请从上方上传原卷或新建错题。</td></tr>';
      return `<div class="toolbar"><label class="btn btn-primary">上传原卷<input id="error-doc-file" type="file" accept=".pdf,.doc,.docx,.png,.jpg,.jpeg,.gif,.webp" hidden></label><button class="btn btn-secondary" data-act="error-add">+ 新建错题</button></div>
        <div class="error-workspace"><div class="card"><div class="card-header"><h3 class="card-title">试卷资料库</h3><span class="badge badge-blue">${documents.length} 份</span></div><div class="card-body"><div class="error-document-list">${documents.length ? documents.map(item => `<button type="button" class="error-document-item ${selected?.id === item.id ? 'active' : ''}" data-act="error-doc-select" data-id="${escapeAttr(item.id)}"><span class="error-document-name">${escapeHtml(item.name)}</span><span class="error-document-meta">${escapeHtml(item.extension || '文件')} · ${formatFileSize(item.size)}</span></button>`).join('') : '<div class="empty">还没有原卷资料</div>'}</div></div></div>
          <div class="card"><div class="card-header"><div><h3 class="card-title">原卷预览</h3><p style="margin:6px 0 0;color:var(--md-text-secondary);font-size:13px;">${escapeHtml(selected?.name || '未选择文件')}</p></div>${selected ? (selected.attachmentId ? `<button class="btn btn-sm" data-act="error-doc-download" data-id="${escapeAttr(selected.id)}">下载原卷</button>` : `<a class="btn btn-sm" href="${escapeAttr(selected.content)}" download="${escapeAttr(selected.name)}">下载原卷</a>`) : ''}</div><div class="card-body"><div class="error-document-preview">${renderErrorDocumentPreview(selected)}</div>${selected ? `<div class="error-document-actions"><span class="badge badge-blue">${escapeHtml(selected.extension || '文件')} · ${formatFileSize(selected.size)}</span><button class="btn btn-sm btn-danger" data-act="error-doc-delete" data-id="${escapeAttr(selected.id)}">删除资料</button></div>` : ''}</div></div></div>
        <div class="card"><div class="card-header"><h3 class="card-title">错题库</h3><span class="badge badge-blue">${state.errors.length} 道</span></div><div class="card-body" style="padding:0;overflow:auto;"><table><thead><tr><th>题号</th><th>题型</th><th>考点</th><th>原卷页码</th><th>来源原卷</th><th>错误人数</th><th>典型错因</th><th>操作</th></tr></thead><tbody>${errorRows}</tbody></table></div></div>`;
    }

    async function extractDocxPreview(dataUrl) {
      try {
        await ensureJSZip();
        const buffer = await fetch(dataUrl).then(response => response.arrayBuffer());
        const zip = await JSZip.loadAsync(buffer);
        const file = zip.file('word/document.xml');
        if (!file) return '';
        const xml = await file.async('text');
        return xml.replace(/<w:tab[^>]*\/>/g, '\t').replace(/<w:br[^>]*\/>/g, '\n').replace(/<\/w:p>/g, '\n').replace(/<[^>]+>/g, '').replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>').trim();
      } catch (error) { console.warn('Word 预览提取失败', error); return ''; }
    }

    function handleErrorDocumentUpload(file) {
      if (!file) return;
      if (file.size > 20 * 1024 * 1024) return showToast('单个原卷文件不能超过20MB', 'error');
      const reader = new FileReader();
      reader.onload = async () => {
        const extension = (file.name.match(/\.([^.]+)$/)?.[1] || 'file').toUpperCase();
        const localContent = reader.result;
        try {
          let item;
          if (DATABASE_MODE) {
            const payload = await apiRequest(`/api/v1/attachments?term_id=${currentTermId}`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ title: file.name, original_name: file.name, mime_type: file.type || 'application/octet-stream', content_base64: String(localContent).split(',').pop(), metadata: { source: 'paper_documents' } })
            });
            item = { id: `attachment-${payload.id}`, attachmentId: Number(payload.id), name: payload.original_name, extension, type: payload.mime_type, size: Number(payload.size_bytes) || file.size, content: '', createdAt: payload.created_at || new Date().toISOString() };
          } else {
            item = { id: uid(), name: file.name, extension, type: file.type || '', size: file.size, content: localContent, createdAt: new Date().toISOString() };
          }
          if (/\.docx$/i.test(file.name)) item.previewText = await extractDocxPreview(localContent);
          const result = await commitMutation(() => {
            state.paperDocuments = Array.isArray(state.paperDocuments) ? state.paperDocuments : [];
            state.paperDocuments.push(item);
            errorSelectedDocumentId = item.id;
          }, { successMessage: '原卷资料已保存', renderNavigation: true });
          if (!result.ok && DATABASE_MODE && item.attachmentId) await apiRequest(`/api/v1/attachments/${item.attachmentId}`, { method: 'DELETE' }).catch(() => {});
        } catch (error) {
          showToast(`原卷资料保存失败：${error.message || error}`, 'error');
        }
      };
      reader.onerror = () => showToast('文件读取失败', 'error');
      reader.readAsDataURL(file);
    }

    function renderRiskCloudGroup(level, items, exams, showAll = false) {
      const meaning = level === 'C' ? '需跟进' : '优先干预';
      const label = `${level} 层 · ${meaning}`;
      const average = items.length ? (items.reduce((sum, item) => sum + item.count, 0) / items.length).toFixed(1) : '0.0';
      const colors = level === 'C' /* warm-paper theme */
        ? { high: '#8a6534', mid: '#a57c45', low: '#e9dcc3', dot: '#8a6534' }
        : { high: '#a34c2e', mid: '#c98a70', low: '#f0d5c9', dot: '#a34c2e' };
      const distribution = {
        high: items.filter(item => Number(item.count) >= 3).length,
        mid: items.filter(item => Number(item.count) === 2).length,
        low: items.filter(item => Number(item.count) === 1).length,
      };
      const distributionTotal = items.length || 1;
      const distributionRows = [
        { key: 'high', label: '3次以上', color: colors.high },
        { key: 'mid', label: '2次', color: colors.mid },
        { key: 'low', label: '1次', color: colors.low },
      ];
      const distributionBar = distributionRows.map(row => `<span style="width:${(distribution[row.key] / distributionTotal * 100).toFixed(1)}%;background:${row.color}" title="${row.label} ${distribution[row.key]}人"></span>`).join('');
      const distributionLabels = distributionRows.map(row => `<span><i style="background:${row.color}"></i>${row.label} ${distribution[row.key]}人</span>`).join('');
      const expanded = showAll || Boolean(riskCloudExpanded[level]);
      const visibleItems = expanded ? items : items.slice(0, 8);
      const studentCards = visibleItems.map(item => {
        const count = Math.max(1, Number(item.count) || 1);
        const evidence = (item.evidence || [`近${exams.length}次考试中${count}次处于${level}层`]).map(text => `<li>${escapeHtml(text)}</li>`).join('');
        return `<article class="risk-student-card" data-act="risk-student-detail" data-id="${escapeAttr(item.student.id)}" data-level="${level}" tabindex="0" aria-label="查看${escapeAttr(item.student.name)}的关注证据"><div class="risk-student-main"><div><span class="risk-student-state" aria-hidden="true"></span><strong class="risk-cloud-name">${escapeHtml(item.student.name)}</strong></div><span class="risk-student-count">${level}层 · ${meaning}</span></div><ul class="risk-evidence-list">${evidence}</ul></article>`;
      }).join('');
      const moreButton = items.length > 8 && !showAll ? `<div class="risk-cloud-more"><button type="button" data-act="risk-expand" data-level="${level}">查看全部 ${items.length} 名学生 →</button></div>` : '';
      return `<div class="card risk-cloud-card ${level === 'C' ? 'c-level' : 'd-level'}"><div class="card-header"><div><h3 class="card-title">${label}</h3></div><div class="risk-cloud-stat"><strong>${items.length} 人</strong><span>平均 ${average} 次</span></div></div><div class="risk-cloud"><div class="risk-frequency"><span class="risk-frequency-title">最近${exams.length || 0}次考试</span><div class="risk-frequency-track" aria-label="${label}出现次数分布">${distributionBar}</div><div class="risk-frequency-labels">${distributionLabels}</div></div><div class="risk-student-grid">${studentCards || `<div class="risk-cloud-empty">暂无${level}层学生</div>`}</div>${moreButton}</div></div>`;
    }

    function renderRiskDetail() {
      const availableClasses = state.classes || [];
      if (riskCloudClass && !availableClasses.includes(riskCloudClass)) riskCloudClass = '';
      const selectedClass = normalizeClassFilter(riskCloudClass || dashboardClass);
      const { exams, groups } = calculateRiskClouds(selectedClass, 5);
      const classText = selectedClass ? '当前班级' : '全部班级';
      const level = riskDetailLevel === 'D' ? 'D' : 'C';
      const classOptions = riskClassSelectOptions(selectedClass);
      return `<div class="risk-detail-page"><div class="risk-detail-toolbar"><button class="btn" type="button" data-act="risk-back">← 返回仪表盘</button><div><h2 class="risk-detail-title">重点关注学生 · ${level}层</h2><p class="risk-detail-subtitle">${escapeHtml(classText)} · 最近${exams.length || 0}次考试</p></div><select class="jump-select risk-cloud-select" data-act="risk-class" aria-label="选择重点关注学生班级">${classOptions}</select></div><div class="risk-detail-groups">${renderRiskCloudGroup(level, groups[level], exams, true)}</div></div>`;
    }

    function openRiskStudentDetail(studentId, level) {
      const student = state.students.find(item => item.id === studentId);
      if (!student) return;
      const targetLevel = level === 'D' ? 'D' : 'C';
      const recentExams = getRecentExams(5);
      const evidenceItem = calculateRiskClouds(student.class, 5).groups[targetLevel].find(item => item.student.id === student.id);
      const evidenceBlock = evidenceItem?.evidence?.length ? `<div class="risk-evidence-summary"><strong>关注依据</strong><ul>${evidenceItem.evidence.map(text => `<li>${escapeHtml(text)}</li>`).join('')}</ul></div>` : '';
      const records = recentExams.map(exam => {
        const score = getExamScore(exam, student);
        return { exam, score, tier: getExamTier(exam, score) };
      }).filter(item => item.tier === targetLevel);
      const rows = records.length
        ? records.map(item => `<tr><td>${escapeHtml(item.exam.name || '未命名考试')}</td><td>${escapeHtml(item.exam.date || '—')}</td><td class="text-center"><strong>${item.tier}</strong></td><td class="text-center">${fmt(item.score)}</td></tr>`).join('')
        : `<tr><td colspan="4" class="empty">近${recentExams.length}次考试中暂无${targetLevel}类记录</td></tr>`;
      openModal(`重点关注 · ${student.name}`, `<div class="card" style="margin:0 0 16px;"><div class="card-body"><div style="display:flex;gap:24px;flex-wrap:wrap;"><span><b>姓名：</b>${escapeHtml(student.name)}</span><span><b>学号：</b>${escapeHtml(student.id)}</span><span><b>班级：</b>当前班级</span></div>${evidenceBlock}</div></div><div class="card"><div class="card-header"><h3 class="card-title">${targetLevel}层 · ${targetLevel === 'D' ? '优先干预' : '需跟进'}</h3><span class="badge ${targetLevel === 'D' ? 'badge-red' : 'badge-orange'}">${records.length} 次</span></div><div class="card-body" style="padding:0;overflow:auto;"><table><thead><tr><th>考试</th><th>日期</th><th class="text-center">层级</th><th class="text-center">${subjectHtml('score_short')}</th></tr></thead><tbody>${rows}</tbody></table></div></div>`, '<button class="btn btn-primary" onclick="closeModal()">关闭</button>');
    }

    function renderRiskClouds(cls) {
      const availableClasses = state.classes || [];
      if (riskCloudClass && !availableClasses.includes(riskCloudClass)) riskCloudClass = '';
      const selectedClass = normalizeClassFilter(cls || dashboardClass);
      const { exams, groups } = calculateRiskClouds(selectedClass, 5);
      const examText = exams.length ? `最近${exams.length}次考试` : '暂无考试数据';
      const classOptions = riskClassSelectOptions(selectedClass);
      const classText = selectedClass ? '当前班级' : '全部班级';
      const attentionIds = new Set([...groups.C, ...groups.D].map(item => item.student.id));
      return `<div class="card risk-attention-card"><div class="card-header"><div><h3 class="card-title">重点关注学生</h3><p style="margin:6px 0 0;color:var(--md-text-secondary);font-size:13px;">${escapeHtml(classText)} · ${examText}</p></div><select class="jump-select risk-cloud-select" data-act="risk-class" aria-label="选择重点关注学生班级">${classOptions}</select></div><div class="risk-attention-summary"><div><span class="risk-summary-label">需要关注</span><strong>${attentionIds.size} 人</strong></div><div><span class="risk-summary-label">C层 · 需跟进</span><strong class="c-summary">${groups.C.length} 人</strong></div><div><span class="risk-summary-label">D层 · 优先干预</span><strong class="d-summary">${groups.D.length} 人</strong></div></div><div class="risk-cloud-grid">${renderRiskCloudGroup('C', groups.C, exams)}${renderRiskCloudGroup('D', groups.D, exams)}</div></div>`;
    }

    function renderTodo() {
      ensureTodoDate();
      const list = Array.isArray(state.todos) ? state.todos : [];
      const occurrences = expandTodoOccurrences(list);
      const today = formatLocalDate(new Date());
      const getTodoDate = todo => todo.date || todo.deadline || '';
      const dayTasks = date => occurrences.filter(item => item.date === date).sort((a, b) => String(getTodoStartTime(a.todo) || '99:99').localeCompare(String(getTodoStartTime(b.todo) || '99:99')) || String(a.todo.createdAt || '').localeCompare(String(b.todo.createdAt || '')));
      const dayTaskRows = selectedDate => {
        const selectedTasks = dayTasks(selectedDate);
        return selectedTasks.length ? selectedTasks.map(item => {
          const todo = item.todo;
          const todoDate = item.date;
          const startTime = getTodoStartTime(todo);
          const endTime = getTodoEndTime(todo);
          const deadlineTime = startTime ? new Date(`${todoDate}T${endTime || startTime}:00`) : new Date(`${todoDate}T23:59:59`);
          const overdue = !item.done && todoDate && deadlineTime < new Date();
          const priorityClass = todo.priority === '高' ? 'priority-high' : todo.priority === '低' ? 'priority-low' : 'priority-medium';
          const priorityBadge = todo.priority === '高' ? 'badge-red' : todo.priority === '低' ? 'badge-green' : 'badge-orange';
          const timeText = startTime ? `${startTime}${endTime ? `–${endTime}` : ''}` : '全天';
          const toggleAct = todo.repeatRule ? 'todo-occurrence-toggle' : 'todo-toggle';
          return `<div class="todo-task ${priorityClass} ${overdue ? 'overdue' : ''}"><input type="checkbox" ${item.done ? 'checked' : ''} data-act="${toggleAct}" data-id="${escapeAttr(todo.id)}" data-date="${escapeAttr(todoDate)}" aria-label="标记完成"><div><div class="todo-task-title ${item.done ? 'done' : ''}">${escapeHtml(todo.title)}</div><div class="todo-task-meta"><span class="badge ${priorityBadge}">${escapeHtml(todo.priority)}</span> · ${escapeHtml(timeText)}${todo.repeatRule ? ' · 每周重复' : ''}${todo.notes ? ` · ${escapeHtml(todo.notes)}` : ''}</div></div><div class="todo-task-actions"><button class="btn btn-sm" data-act="todo-edit" data-id="${escapeAttr(todo.id)}">编辑</button><button class="btn btn-sm btn-danger" data-act="todo-del" data-id="${escapeAttr(todo.id)}">删除</button></div></div>`;
        }).join('') : '<div class="todo-empty-state"><span class="material-symbols-rounded" aria-hidden="true">event_available</span><strong>当天还没有安排</strong><p>可以添加临时待办，或从课表批量导入。</p><button class="btn btn-sm btn-primary" data-act="todo-add"><span class="material-symbols-rounded" aria-hidden="true">add</span>添加待办</button></div>';
      };
      const renderDayLabel = date => `<span class="todo-weekday-date">${['周日','周一','周二','周三','周四','周五','周六'][date.getDay()]}</span><strong>${date.getMonth() + 1}/${date.getDate()}</strong>`;
      const renderTimeBlock = (item, date) => {
        const todo = item.todo;
        const start = getTodoStartTime(todo);
        if (!start) return '';
        const [hour, minute] = start.split(':').map(Number);
        const end = getTodoEndTime(todo);
        const endMinutes = end ? Number(end.slice(0, 2)) * 60 + Number(end.slice(3)) : hour * 60 + minute + 56;
        const top = Math.max(0, (hour * 60 + minute) / 60 * 48);
        const height = Math.max(32, (endMinutes - (hour * 60 + minute)) / 60 * 48);
        const doneClass = item.done || todo.done ? 'done' : '';
        return `<button class="todo-week-event ${doneClass}" style="top:${top}px;height:${height}px" data-act="todo-edit" data-id="${escapeAttr(todo.id)}" title="${escapeAttr(todo.title)}"><strong>${escapeHtml(todo.title)}</strong><span>${escapeHtml(start)}${end ? `–${escapeHtml(end)}` : ''}</span></button>`;
      };
      const renderWeek = () => {
        const base = new Date(todoWeekDate);
        const day = base.getDay();
        const monday = new Date(base);
        monday.setDate(base.getDate() - (day === 0 ? 6 : day - 1));
        const weekDays = Array.from({ length: 7 }, (_, index) => { const date = new Date(monday); date.setDate(monday.getDate() + index); return date; });
        const weekStart = formatLocalDate(weekDays[0]);
        const weekEnd = formatLocalDate(weekDays[6]);
        const headers = weekDays.map(date => `<div class="todo-week-header ${formatLocalDate(date) === today ? 'today' : ''}">${renderDayLabel(date)}</div>`).join('');
        const allDay = weekDays.map(date => {
          const dateKey = formatLocalDate(date);
          const items = dayTasks(dateKey).filter(item => !getTodoStartTime(item.todo));
          return `<div class="todo-week-allday-cell" data-act="todo-day" data-date="${dateKey}">${items.map(item => `<button class="todo-week-allday-item ${item.done ? 'done' : ''}" data-act="todo-edit" data-id="${escapeAttr(item.todo.id)}">${escapeHtml(item.todo.title)}</button>`).join('')}</div>`;
        }).join('');
        const hours = Array.from({ length: 24 }, (_, index) => `<div class="todo-time-label ${index === 0 ? 'first' : ''}" style="top:${index * 48}px">${String(index).padStart(2, '0')}:00</div>`).join('');
        const columns = weekDays.map(date => {
          const dateKey = formatLocalDate(date);
          return `<div class="todo-week-column ${dateKey === today ? 'today' : ''}" data-date="${dateKey}">${dayTasks(dateKey).map(item => renderTimeBlock(item, date)).join('')}</div>`;
        }).join('');
        return `<div class="todo-week-view"><div class="todo-week-head"><div class="todo-week-spacer"></div>${headers}</div><div class="todo-week-allday"><div class="todo-week-spacer">全天</div>${allDay}</div><div class="todo-week-scroll"><div class="todo-time-axis">${hours}</div><div class="todo-week-grid">${columns}</div></div><div class="todo-week-range">${weekStart} 至 ${weekEnd}</div></div>`;
      };
      const view = new Date(todoCalendarDate.getFullYear(), todoCalendarDate.getMonth(), 1);
      const year = view.getFullYear();
      const month = view.getMonth();
      const firstOffset = view.getDay();
      const cells = Array.from({ length: 42 }, (_, index) => {
        const day = index - firstOffset + 1;
        const date = new Date(year, month, day);
        const dateKey = formatLocalDate(date);
        const tasks = dayTasks(dateKey);
        const inMonth = date.getMonth() === month;
        const labels = tasks.slice(0, 3).map(item => {
          const todo = item.todo;
          const priorityClass = todo.priority === '高' ? 'priority-high' : todo.priority === '低' ? 'priority-low' : 'priority-medium';
          const time = getTodoStartTime(todo);
          return `<span class="todo-day-item ${priorityClass} ${item.done ? 'done' : ''}">${time ? `${escapeHtml(time)} ` : ''}${escapeHtml(todo.title)}</span>`;
        }).join('');
        return `<button class="todo-day-cell ${inMonth ? '' : 'other-month'} ${dateKey === todoSelectedDate ? 'selected' : ''} ${dateKey === today ? 'today' : ''}" data-act="todo-day" data-date="${dateKey}"><span class="todo-day-number">${date.getDate()}</span><span class="todo-day-items">${labels}${tasks.length > 3 ? `<span class="todo-day-item">还有${tasks.length - 3}项</span>` : ''}</span></button>`;
      }).join('');
      const selectedTasks = dayTasks(todoSelectedDate);
      const taskRows = dayTaskRows(todoSelectedDate);
      const monthTitle = `${year}年${month + 1}月`;
      const body = todoViewMode === 'week' ? renderWeek() : `<div class="todo-calendar-grid"><div class="todo-weekday">周日</div><div class="todo-weekday">周一</div><div class="todo-weekday">周二</div><div class="todo-weekday">周三</div><div class="todo-weekday">周四</div><div class="todo-weekday">周五</div><div class="todo-weekday">周六</div>${cells}</div>`;
      const selectedDateObject = parseLocalDate(todoSelectedDate);
      const selectedWeekday = ['周日','周一','周二','周三','周四','周五','周六'][selectedDateObject.getDay()];
      const calendarTitle = todoViewMode === 'week' ? `${todoWeekDate.getFullYear()}年${todoWeekDate.getMonth() + 1}月` : monthTitle;
      return `<div class="todo-page"><div class="todo-calendar-head"><div class="todo-heading-block"><div class="todo-title-row"><h2 class="todo-calendar-title">${calendarTitle}</h2><div class="todo-view-switch" role="group" aria-label="待办视图"><button class="todo-segment-btn ${todoViewMode === 'week' ? 'active' : ''}" data-act="todo-view-mode" data-mode="week">周视图</button><button class="todo-segment-btn ${todoViewMode === 'month' ? 'active' : ''}" data-act="todo-view-mode" data-mode="month">月视图</button></div></div><p class="todo-calendar-subtitle">${todoViewMode === 'week' ? '查看一周的全天与分时日程' : '查看整月安排与待办分布'}</p></div><div class="todo-calendar-actions"><div class="todo-nav-group" role="group" aria-label="日期导航"><button class="todo-nav-btn" data-act="todo-calendar-prev" aria-label="上一个${todoViewMode === 'week' ? '周' : '月'}"><span class="material-symbols-rounded" aria-hidden="true">chevron_left</span></button><button class="todo-nav-today" data-act="todo-calendar-today">今天</button><button class="todo-nav-btn" data-act="todo-calendar-next" aria-label="下一个${todoViewMode === 'week' ? '周' : '月'}"><span class="material-symbols-rounded" aria-hidden="true">chevron_right</span></button></div><div class="todo-action-group"><button class="btn btn-sm btn-secondary" data-act="todo-import"><span class="material-symbols-rounded" aria-hidden="true">calendar_add_on</span>导入课表</button><button class="btn btn-sm btn-primary" data-act="todo-add"><span class="material-symbols-rounded" aria-hidden="true">add</span>添加待办</button></div></div></div><div class="todo-calendar-layout"><div class="card todo-calendar-card"><div class="card-body">${body}</div></div><div class="card todo-detail-card"><div class="card-header"><div class="todo-detail-heading"><span class="material-symbols-rounded" aria-hidden="true">event_note</span><div><h3 class="card-title">${formatTodoDate(todoSelectedDate)}</h3><p>${selectedWeekday} · ${selectedTasks.length ? `共 ${selectedTasks.length} 项安排` : '暂无安排'}</p></div></div></div><div class="card-body"><div class="todo-task-list">${taskRows}</div></div></div></div></div>`;
    }

    function renderSettings() {
      const currentTerm = availableTerms.find(term => Number(term.id) === Number(currentTermId));
      const currentSubject = subjectCatalog.find(item => item.key === subjectKey) || subjectCatalog[0] || {};
      const termPeriod = currentTerm ? [currentTerm.starts_on, currentTerm.ends_on].filter(Boolean).join(' 至 ') : '';
      return `
        <div class="card"><div class="card-header"><h3 class="card-title">教师信息与评分标准</h3></div>
        <div class="card-body">
          ${DATABASE_MODE ? `<div class="term-settings-row"><div><strong>${escapeHtml(currentTerm?.name || '未识别')}</strong>${termPeriod ? `<div class="subtle">${escapeHtml(termPeriod)}</div>` : ''}</div><div><button class="btn btn-secondary" data-act="term-manage">管理学期</button><button class="btn btn-primary" data-act="term-add">新建学期</button></div></div><hr style="margin:20px 0;border:0;border-top:1px solid var(--border);">` : ''}
          <div class="form-row">
            <div class="form-group"><label>教师姓名</label><input id="sett-name" value="${escapeAttr(state.teacher.name)}"></div>
            <div class="form-group"><label for="sett-subject-key">任教学科</label><select id="sett-subject-key">${subjectCatalog.map(item => `<option value="${escapeAttr(item.key)}" ${item.key === subjectKey ? 'selected' : ''}>${escapeHtml(item.label)}</option>`).join('')}</select></div>
          </div>
          <div class="form-row">
            <div class="form-group"><label>学科名称（显示在顶栏）</label><input id="sett-subject" value="${escapeAttr(state.teacher.subject)}" placeholder="例如：七年级语文"></div>
          </div>
          <div class="form-row">
            <div class="form-group"><label>优秀线（满分百分比）</label><input type="number" min="0" max="100" step="0.5" id="sett-excellent" value="${state.settings.excellent}"></div>
            <div class="form-group"><label>及格线（满分百分比）</label><input type="number" min="0" max="100" step="0.5" id="sett-pass" value="${state.settings.pass}"></div>
          </div>
          <div class="form-row">
            <div class="form-group"><label>班级列表（逗号分隔）</label><input id="sett-classes" value="${state.classes.join(',')}"></div>
          </div>
          <button class="btn btn-primary" data-act="settings-save">保存设置</button>
        </div></div>
        ${DATABASE_MODE ? `<div class="card" id="paper-default-card"><div class="card-header"><h3 class="card-title">试卷设置</h3></div><div class="card-body">


          <div id="paper-default-rows" class="paper-distribution-rows">正在读取试卷设置…</div>
          <div class="paper-distribution-actions"><button class="btn btn-primary" data-act="paper-default-save" disabled>保存设置</button><span id="paper-default-status" role="status" class="subtle"></span></div>
        </div></div>` : ''}
        ${DATABASE_MODE ? `<div class="card"><div class="card-header"><h3 class="card-title">成长加分标准</h3></div>
        <div class="card-body">

          <div class="form-row">
            <div class="form-group"><label for="growth-teacher-select">当前教师</label><select id="growth-teacher-select" data-act="growth-teacher-select"></select></div>
            <div class="form-group"><label for="growth-teacher-new">新增教师</label><div style="display:flex;gap:8px;"><input id="growth-teacher-new" placeholder="教师姓名" maxlength="32" autocomplete="off"><button class="btn btn-secondary" data-act="growth-teacher-add">添加</button></div></div>
          </div>
          <div id="growth-preset-conflict" class="growth-preset-conflict" role="status"></div>
          <div id="growth-preset-rows" class="growth-preset-editor">正在读取加分标准…</div>
          <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:12px;align-items:center;"><button class="btn btn-primary" data-act="growth-preset-save">保存加分标准</button><button class="btn btn-secondary" data-act="growth-preset-reset">恢复默认分值</button><button class="btn btn-text danger-text" data-act="growth-teacher-remove">删除当前教师</button><span id="growth-preset-status" class="subtle">正在读取配置…</span></div>
        </div></div>` : ''}
        ${subjectKey === 'english' ? `<div class="card"><div class="card-header"><h3 class="card-title">MONI 数据接口（MCP）</h3></div>
        <div class="card-body">

          <div class="form-group"><label for="moni-api-key">MONI API Key</label><input id="moni-api-key" type="password" autocomplete="off" placeholder="输入新的 API Key（已配置时可留空）" style="font-family:ui-monospace,monospace;"><div id="moni-key-state" class="subtle" style="margin-top:8px;">正在检查 API Key 状态…</div></div>
          <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:12px;align-items:center;"><button class="btn btn-secondary" data-act="moni-open-tutorial">使用教程</button><button class="btn btn-primary" data-act="moni-save-config">保存接口配置</button><button class="btn btn-secondary" data-act="moni-test-config">测试连接</button><button class="btn btn-secondary" data-act="moni-sync-now">测试并同步学生数据</button><span id="moni-config-status" class="subtle">正在读取配置…</span></div>
        </div></div>` : `<div class="card"><div class="card-header"><h3 class="card-title">MONI 数据接口（MCP）</h3></div><div class="card-body"><p style="margin:0;color:var(--md-text-secondary);">内置 MONI 目前只提供英语单科同步。当前是${escapeHtml(subjectName())}工作区，请在下方配置对应学科的数据源。</p></div></div>`}
        ${DATABASE_MODE ? `<div class="card"><div class="card-header"><h3 class="card-title">其他学校数据源（自定义 MCP）</h3></div>
        <div class="card-body">

          <div id="school-source-list" class="school-source-list">正在读取数据源…</div>
          <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:12px;align-items:center;"><button class="btn btn-secondary" data-act="school-source-new">新建数据源</button><button class="btn btn-text" data-act="school-source-refresh">刷新列表</button><span id="school-source-status" class="subtle">正在读取配置…</span></div>
          <div id="school-source-editor" class="school-source-editor" hidden>
            <h4 id="school-source-editor-title">新建数据源</h4>
            <div class="form-row">
              <div class="form-group"><label for="school-source-key">数据源标识</label><input id="school-source-key" maxlength="64" autocomplete="off" placeholder="小写字母、数字、点、下划线或连字符"></div>
              <div class="form-group"><label for="school-source-name">显示名称</label><input id="school-source-name" maxlength="150" autocomplete="off" placeholder="例如：某校${escapeAttr(subjectName())}数据"></div>
            </div>
            <div class="form-group"><label for="school-source-endpoint">MCP 端点（http/https）</label><input id="school-source-endpoint" autocomplete="off" placeholder="https://mcp.example.edu/api/mcp" style="font-family:ui-monospace,monospace;"></div>
            <div class="form-row">
              <div class="form-group"><label for="school-source-auth">鉴权方式</label><select id="school-source-auth"><option value="none">不需要令牌</option><option value="bearer">Bearer 令牌</option></select></div>
              <div class="form-group"><label for="school-source-token">Bearer 令牌</label><input id="school-source-token" type="password" autocomplete="off" placeholder="已配置时可留空"><div id="school-source-token-state" class="subtle" style="margin-top:8px;">令牌状态未知</div></div>
            </div>
            <div class="form-row">
              <div class="form-group"><label for="school-source-term-id">学期标识</label><input id="school-source-term-id" autocomplete="off" placeholder="例如 2026-S1"></div>
              <div class="form-group"><label for="school-source-term-name">学期名称</label><input id="school-source-term-name" autocomplete="off" placeholder="例如 2026 学年第一学期"></div>
              <div class="form-group"><label for="school-source-full-score">满分（兜底）</label><input id="school-source-full-score" type="number" min="0" max="1000" step="0.5" placeholder="例如 100"></div>
            </div>
            <div class="form-row">
              <div class="form-group"><label for="school-source-subject-field">科目字段名</label><input id="school-source-subject-field" autocomplete="off" placeholder="例如 subjectName"></div>
              <div class="form-group"><label for="school-source-subject-values">只同步的科目取值（逗号分隔）</label><input id="school-source-subject-values" autocomplete="off" placeholder="英语,English"></div>
            </div>
            <h4>数据路径模板</h4>
            <p class="subtle" style="margin:0 0 8px;">用 <code>{class_id}</code> / <code>{exam_id}</code> 作为占位符，只填学校实际提供的路径。</p>
            <div class="school-source-grid">
              <label><span>学期</span><input id="school-source-path-term" autocomplete="off" placeholder="/school/current-term.json"></label>
              <label><span>班级列表</span><input id="school-source-path-classes" autocomplete="off" placeholder="/classes/.list.jsonl"></label>
              <label><span>学生名册</span><input id="school-source-path-roster" autocomplete="off" placeholder="/classes/{class_id}/students/.list.jsonl"></label>
              <label><span>考试列表</span><input id="school-source-path-exams" autocomplete="off" placeholder="/classes/{class_id}/exams/.list.jsonl"></label>
              <label><span>题目</span><input id="school-source-path-questions" autocomplete="off" placeholder="/classes/{class_id}/exams/{exam_id}/questions/.list.jsonl"></label>
              <label><span>成绩</span><input id="school-source-path-students" autocomplete="off" placeholder="/classes/{class_id}/exams/{exam_id}/students/.list.jsonl"></label>
            </div>
            <h4>字段映射</h4>
            <p class="subtle" style="margin:0 0 8px;">左边是 WorkBench 认的逻辑字段，右边写学校返回的字段名；可以写候选列表，按顺序取第一个有值的。取不到的字段留空，不会被当成 0。</p>
            <textarea id="school-source-field-map" rows="12" spellcheck="false" aria-label="字段映射 JSON"></textarea>
            <div id="school-source-missing" class="school-source-missing" role="status"></div>
            <h4>分类取值对照（可选）</h4>
            <p class="subtle" style="margin:0 0 8px;">字段名对上了，取值口径还可能对不上：同一类东西，上游可能叫「甲等」，WorkBench 只认 A。每行写一条「逻辑字段: 统一叫法 = 上游叫法1, 上游叫法2」，多个上游叫法用逗号、顿号或分号分隔；以 <code>#</code> 开头的行会被忽略。没有写进对照表的上游取值会按原样保留，不会被猜成别的东西。</p>
            <details class="school-source-alias-help"><summary>可以填哪些字段</summary><ul id="school-source-alias-reference"></ul></details>
            <textarea id="school-source-alias-map" rows="5" spellcheck="false" aria-label="分类取值对照表" placeholder="question.knowledge: 宾语从句 = 从句, Object Clause"></textarea>
            <h4>学生分层别名（可选）</h4>
            <p class="subtle" style="margin:0 0 8px;">学校的「优秀 / 良好 / 合格 / 待提高」这类分层叫法，每行写「上游叫法 = A」。只有填了才知道哪一层是 A；填不出来就留空，分层线不会被猜。</p>
            <textarea id="school-source-tier-aliases" rows="3" spellcheck="false" aria-label="学生分层别名" placeholder="优秀 = A"></textarea>
            <div id="school-source-alias-status" class="school-source-missing" role="status"></div>
            <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:12px;align-items:center;">
              <button class="btn btn-primary" data-act="school-source-save">保存配置</button>
              <button class="btn btn-secondary" data-act="school-source-test">测试连接</button>
              <button class="btn btn-secondary" data-act="school-source-sync">立即同步</button>
              <button class="btn btn-text danger-text" data-act="school-source-delete">删除数据源</button>
              <button class="btn btn-text" data-act="school-source-cancel">取消</button>
              <span id="school-source-editor-status" class="subtle"></span>
            </div>
          </div>
        </div></div>` : ''}
        <div class="card"><div class="card-header"><h3 class="card-title">班级归档与合并</h3></div>
        <div class="card-body">

          ${state.classes.length >= 2 ? `<div class="form-row"><div class="form-group"><label>来源班级</label><select id="class-merge-source">${state.classes.map(c => `<option value="${escapeAttr(c)}">${escapeHtml(formatClassLabel(c))}</option>`).join('')}</select></div><div class="form-group"><label>目标班级</label><select id="class-merge-target">${state.classes.map(c => `<option value="${escapeAttr(c)}">${escapeHtml(formatClassLabel(c))}</option>`).join('')}</select></div></div><button class="btn btn-secondary" data-act="class-merge">归档并合并</button>` : '<div style="color:var(--md-text-secondary);">至少需要两个班级才能进行手动合并。</div>'}
          ${Object.keys(state.classAliases || {}).length ? `<div style="margin-top:16px;"><strong>已归档的班级写法</strong><div style="display:grid;gap:8px;margin-top:8px;">${Object.entries(state.classAliases).map(([source, target]) => `<div class="tag-manage-row"><span>${escapeHtml(formatClassLabel(source))} → ${escapeHtml(formatClassLabel(resolveClassName(target)))}</span><button class="btn btn-sm" data-act="class-unarchive" data-id="${escapeAttr(source)}">解除映射</button></div>`).join('')}</div><small style="color:var(--md-text-secondary);">解除映射不会拆分已有学生，只影响之后导入时的自动归类。</small></div>` : ''}
        </div></div>
        <div class="card"><div class="card-header"><h3 class="card-title">数据安全与维护</h3></div>
        <div class="card-body">
          <h4>数据备份</h4>
          <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:20px;">
            <button class="btn btn-secondary" data-act="export-json">导出当前学期 JSON</button>
            <button class="btn btn-secondary" data-act="import-json">导入 JSON 备份</button>
          </div>
          <p class="subtle" style="margin:-8px 0 20px;">JSON 包含当前学期的工作台内容；成长树补录、附件和其他学期保存在数据库备份中。</p>
          <h4>数据维护</h4>
          <p style="margin:0 0 14px;color:var(--md-text-secondary);">已归档学生 ${state.archivedStudents?.length || 0} 人 · 已归档考试 ${state.archivedExams?.length || 0} 场</p>
          <div style="display:flex;gap:8px;flex-wrap:wrap;">
            <button class="btn btn-secondary" data-act="archived-manage">管理已归档学生</button>
            <button class="btn btn-secondary" data-act="archived-exams-manage">管理已归档考试</button>
            <button class="btn btn-text danger-text" data-act="reset-data">重置全部数据…</button>
          </div>
        </div></div>`;
    }

    // NOTE: renderTeachMateNav 和 renderTeachMate 已迁移至 teachmate-views.js
