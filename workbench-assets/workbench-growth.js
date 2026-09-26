// ================= 英语成长森林（成长树）模块 =================
// 数据全部来自后端确定性接口 /api/v1/growth/*：前端不实现第二份计分规则，
// 只负责展示阶段、本周新增、记录覆盖、五个分支、待订正与历史年轮。
// 与参考包的关键差异：不提供扣分（纪律扣分不混入能力值），补录必须填事由，
// 撤销走事件账本（引用原记录）而不是删除数组元素。
(function () {
  'use strict';

  const GROWTH_DIMENSION_LABELS = {
    vocabulary: '词汇', grammar: '语法', reading: '阅读',
    listening: '听力', writing: '写作'
  };
  const GROWTH_DIMENSION_ORDER = ['vocabulary', 'grammar', 'reading', 'listening', 'writing'];

  // 补录预设：只加分，不提供扣分（方案 §3.2）。
  const GROWTH_PRESETS = [
    { type: 'task_completed', label: '完成学习任务', pts: 2 },
    { type: 'correction_verified', label: '完成订正并确认', pts: 2 },
    { type: 'spaced_review', label: '间隔复习/达标复测', pts: 3 },
    { type: 'teacher_observation', label: '课堂/阅读表现', pts: 1 },
    { type: 'teacher_bonus', label: '教师手工加分', pts: 1 },
    { type: 'weekly_goal', label: '达成个人周目标', pts: 2 }
  ];

  function growthPresetsForTerm() {
    return growthData && growthData.rule_version === 'growth-v3'
      ? GROWTH_PRESETS.filter(item => item.type !== 'teacher_bonus')
      : GROWTH_PRESETS;
  }

  let growthLoaded = false;
  let growthLoading = false;
  let growthError = '';
  let growthData = null;
  let growthClass = '';
  let growthSort = 'id';
  let growthSelectMode = false;
  let growthRequestVersion = 0;
  let growthSubmitting = false;
  let growthPendingSubmission = null;
  let growthLegacySubmitting = false;
  let growthPendingLegacy = null;
  let growthModalTermId = null;
  const growthSelectedIds = new Set();
  const growthDetailCache = {};

  function growthResetSelection() {
    growthSelectMode = false;
    growthSelectedIds.clear();
  }

  // 与其它模块一致：全局班级范围变化时同步本模块的班级过滤，并清空批量勾选，
  // 避免保留被隐藏的选择（方案 §1.2）。
  function growthSetClass(value) {
    const next = String(value || '');
    growthClass = getAvailableClasses().includes(next) ? next : '';
    growthResetSelection();
  }

  function growthInvalidate() {
    growthRequestVersion += 1;
    growthLoaded = false;
    growthLoading = false;
    growthError = '';
    growthData = null;
    for (const key of Object.keys(growthDetailCache)) delete growthDetailCache[key];
  }

  // ---------- 手绘风 SVG 树（7 个成长阶段，端口自参考包，保留自然配色） ----------
  function growthTreeSvg(index) {
    const idx = Math.max(0, Math.min(6, Number(index) || 0));
    const wobblyStroke = 'fill="none" stroke="#8a6a43" stroke-width="2.6" stroke-linecap="round"';
    const soil = `<path d="M20,58 Q32,54 44,58" fill="none" stroke="#a08455" stroke-width="2" stroke-linecap="round"/>
      <path d="M16,60 Q20,58.5 24,59.5 M40,59.5 Q44,58.5 48,60" fill="none" stroke="#c4b08a" stroke-width="1.2" stroke-linecap="round"/>`;
    let body = '';
    if (idx === 0) {
      body = `<ellipse cx="32" cy="52" rx="5" ry="6.5" fill="#b58a5a" transform="rotate(-14 32 52)"/>
        <path d="M29,48 Q27,44 29,41" fill="none" stroke="#d9c39a" stroke-width="1.4" stroke-linecap="round"/>`;
    } else if (idx === 1) {
      body = `<path d="M32,57 Q31,48 32.5,42" ${wobblyStroke}/>
        <path d="M32.5,46 Q24,44 22,37 Q30,36 32.5,42" fill="#7fb069"/>
        <path d="M32.5,44 Q40,42 43,35 Q35,34 32.5,40" fill="#95c17d"/>`;
    } else {
      const size = [0, 0, 6, 9, 12, 14, 15][idx];
      const lift = [0, 0, 30, 26, 22, 21, 19][idx];
      const canopy = `<circle cx="32" cy="${lift - size * 0.55}" r="${size}" fill="#7fb069"/>
        <circle cx="${32 - size * 0.7}" cy="${lift - size * 0.1}" r="${size * 0.72}" fill="#95c17d"/>
        <circle cx="${32 + size * 0.7}" cy="${lift - size * 0.15}" r="${size * 0.75}" fill="#6a9c55"/>
        <circle cx="${32 - size * 0.25}" cy="${lift - size * 0.75}" r="${size * 0.6}" fill="#a8cf92"/>`;
      const trunkH = Math.max(6, 58 - (lift + 2));
      const trunkPath = `<path d="M32,58 Q33,${58 - trunkH * 0.5} 32,${lift + 1}" ${wobblyStroke}/>
        <path d="M32,${lift + 4} Q28,${lift + 6} 26,${lift + 3}" ${wobblyStroke.replace('2.6', '1.6')}/>`;
      let deco = '';
      if (idx >= 4) {
        const flowers = idx === 4 ? 5 : 3;
        const fp = [[27, 12], [37, 9], [31, 17], [40, 14], [24, 16]];
        for (let i = 0; i < flowers; i++) {
          const fx = fp[i][0], fy = fp[i][1] + lift - 18;
          deco += `<circle cx="${fx}" cy="${fy}" r="2.1" fill="#f2a2c0"/><circle cx="${fx}" cy="${fy}" r="0.8" fill="#fde3ef"/>`;
        }
      }
      if (idx >= 5) {
        const rp = [[25, 15], [36, 12], [31, 20], [41, 17]];
        const apples = idx === 5 ? 3 : 4;
        for (let i = 0; i < apples; i++) {
          const rx = rp[i][0], ry = rp[i][1] + lift - 14;
          deco += `<circle cx="${rx}" cy="${ry}" r="2.3" fill="#e05d4b"/><path d="M${rx},${ry - 2.3} q1,-1.6 2.4,-1.8" fill="none" stroke="#5d7a3a" stroke-width="1" stroke-linecap="round"/>`;
        }
      }
      if (idx === 6) {
        deco += `<path d="M32,${lift - 26} l1.6,3.4 3.7,0.5 -2.7,2.6 0.7,3.7 -3.3,-1.8 -3.3,1.8 0.7,-3.7 -2.7,-2.6 3.7,-0.5 z" fill="#f5c542"/>
          <circle cx="46" cy="8" r="1.2" fill="#f5c542"/><circle cx="18" cy="12" r="1" fill="#f5c542"/>`;
      }
      body = trunkPath + canopy + deco;
    }
    return `<svg viewBox="0 0 64 64" class="growth-tree-svg" role="img" aria-hidden="true">${soil}${body}</svg>`;
  }

  // ---------- 数据加载 ----------
  async function loadGrowthForest() {
    if (growthLoading) return;
    if (!DATABASE_MODE) { growthError = '成长森林需要连接本地数据库'; growthLoaded = true; render(); return; }
    growthLoading = true;
    const requestVersion = ++growthRequestVersion;
    const termId = currentTermId;
    growthError = '';
    render();
    try {
      const data = await apiRequest(`/api/v1/growth/forest?term_id=${encodeURIComponent(termId)}`);
      if (requestVersion !== growthRequestVersion || termId !== currentTermId) return;
      growthData = data;
      growthLoaded = true;
    } catch (error) {
      if (requestVersion !== growthRequestVersion || termId !== currentTermId) return;
      growthError = error && error.message ? error.message : '成长数据加载失败';
      growthLoaded = true;
    } finally {
      if (requestVersion !== growthRequestVersion) return;
      growthLoading = false;
      render();
    }
  }

  function growthVisibleStudents() {
    if (!growthData || !Array.isArray(growthData.students)) return [];
    const rows = growthData.students.filter(row => !growthClass || row.class_name === growthClass);
    const sorted = rows.slice();
    if (growthSort === 'name') {
      sorted.sort((a, b) => String(a.name || '').localeCompare(String(b.name || ''), 'zh'));
    } else if (growthSort === 'pts') {
      sorted.sort((a, b) => (b.term_points - a.term_points) || String(a.name || '').localeCompare(String(b.name || ''), 'zh'));
    } else {
      sorted.sort((a, b) => {
        const na = Number(a.student_no), nb = Number(b.student_no);
        if (!isNaN(na) && !isNaN(nb)) return na - nb;
        return String(a.student_no || '').localeCompare(String(b.student_no || ''), 'zh');
      });
    }
    return sorted;
  }

  function growthDimensionBadge(status) {
    if (status === 'ok') return '<span class="growth-branch-dot ok" title="有可比测评记录"></span>';
    if (status === 'insufficient_comparable_history') return '<span class="growth-branch-dot few" title="样本较少"></span>';
    return '<span class="growth-branch-dot none" title="待记录"></span>';
  }

  // ---------- 主渲染 ----------
  function renderGrowth() {
    if (!growthLoaded && !growthLoading) setTimeout(loadGrowthForest, 0);
    if (growthLoading && !growthData) return '<div class="empty">正在加载成长森林…</div>';
    if (growthError && !growthData) return `<div class="empty">成长数据加载失败：${escapeHtml(growthError)}</div>`;
    if (!growthData) return '<div class="empty">成长森林暂无数据</div>';

    const rows = growthVisibleStudents();
    const stages = Array.isArray(growthData.stages) ? growthData.stages : [];
    const chips = [''].concat(getAvailableClasses())
      .map(cls => `<button type="button" class="growth-chip ${growthClass === cls ? 'active' : ''}" data-act="growth-class" data-cls="${escapeAttr(cls)}">${cls === '' ? '全部班级' : escapeHtml(formatClassLabel(cls))}</button>`)
      .join('');
    const rules = stages.map(stage => `${stage.icon}${stage.min}`).join('　');
    const cards = rows.map(growthCard).join('');
    const visibleSummary = growthClass ? {
      student_count: rows.length,
      total_points: rows.reduce((sum, row) => sum + Number(row.term_points || 0), 0),
      active_this_week: rows.filter(row => Number(row.week_points || 0) > 0).length,
      blossomed_count: rows.filter(row => Number(row.stage_index || 0) >= 4).length
    } : growthData.summary;
    visibleSummary.average_points = visibleSummary.student_count
      ? Math.round(visibleSummary.total_points / visibleSummary.student_count * 10) / 10 : 0;

    return `
      <div class="growth-wrap">
        <div class="growth-heading"><div><span class="growth-eyebrow">学生管理 / 成长森林</span><h2>成长森林</h2><p>每棵树记录一名学生本学期可核对的学习投入。</p></div><span class="growth-heading-art" aria-hidden="true">🌱</span></div>
        <div class="growth-toolbar">
          <div class="growth-chips">${chips}</div>
          <div class="growth-actions">
            <select class="growth-select" data-act="growth-sort" aria-label="排序方式">
              <option value="id" ${growthSort === 'id' ? 'selected' : ''}>按学号</option>
              <option value="name" ${growthSort === 'name' ? 'selected' : ''}>按姓名</option>
              <option value="pts" ${growthSort === 'pts' ? 'selected' : ''}>按成长值</option>
            </select>
            <button type="button" class="btn ${growthSelectMode ? 'growth-btn-batch-on' : ''}" data-act="growth-batch-toggle">${growthSelectMode ? '✓ 完成批量操作' : '☑ 批量补录'}</button>
            <button type="button" class="btn btn-secondary" data-act="growth-legacy-open">导入旧版记录</button>
            <button type="button" class="btn btn-secondary" data-act="growth-refresh">刷新</button>
          </div>
        </div>
        <div class="growth-summary">
          <div class="growth-stat"><b>${visibleSummary.student_count}</b><span>棵树</span></div>
          <div class="growth-stat"><b>${visibleSummary.total_points}</b><span>班级总营养</span></div>
          <div class="growth-stat"><b>${visibleSummary.average_points}</b><span>平均营养</span></div>
          <div class="growth-stat"><b>${visibleSummary.active_this_week}</b><span>本周有记录</span></div>
          <div class="growth-stat"><b>${visibleSummary.blossomed_count}</b><span>已开花以上</span></div>
        </div>
        <div class="growth-rules"><span class="growth-rules-label">成长阶段：</span><span>${escapeHtml(rules)}</span><small>营养来自可核对的学习活动及教师补录，不是英语能力等级。点击学生查看记录明细。</small></div>
        ${growthSelectMode ? `<div class="growth-batchbar">
          <span>已选 <b>${growthSelectedIds.size}</b> 人</span>
          <button type="button" class="btn" data-act="growth-sel-all">全选本页</button>
          <button type="button" class="btn" data-act="growth-sel-none">清空选择</button>
          <button type="button" class="btn btn-primary" data-act="growth-batch-open">＋ 批量补录</button>
        </div>` : ''}
        ${growthError ? `<div class="growth-inline-warn">${escapeHtml(growthError)}</div>` : ''}
        ${growthData.manual_entry_policy !== 'teacher_confirmed_v1' ? '<div class="growth-inline-warn" role="status">后台尚未加载新版补录规则。请重启工作台服务后再补录，仅刷新浏览器无法更新计分逻辑。</div>' : ''}
        <div class="growth-grid">${cards}</div>
        ${rows.length === 0 ? '<div class="empty">该范围暂无学生</div>' : ''}
      </div>`;
  }

  function growthCard(row) {
    const stages = Array.isArray(growthData.stages) ? growthData.stages : [];
    const next = row.next_stage;
    const pct = Math.round(Math.max(0, Math.min(1, Number(row.stage_progress) || 0)) * 100);
    const selected = growthSelectedIds.has(String(row.student_id));
    const checkbox = growthSelectMode
      ? `<label class="growth-check"><input type="checkbox" aria-label="选择${escapeAttr(row.name)}" data-act="growth-check" data-id="${row.student_id}" ${selected ? 'checked' : ''}></label>`
      : '';
    const dims = GROWTH_DIMENSION_ORDER
      .map(key => growthDimensionBadge((row.dimensions || {})[key] && row.dimensions[key].status))
      .join('');
    return `
      <div tabindex="0" role="button" aria-label="${escapeAttr(row.name)}的成长记录" ${growthSelectMode ? `aria-pressed="${selected}"` : ''} class="growth-card ${selected && growthSelectMode ? 'growth-card-sel' : ''}" data-act="${growthSelectMode ? 'growth-check-card' : 'growth-student'}" data-id="${row.student_id}" title="${growthSelectMode ? '点卡片勾选/取消' : `查看 ${escapeAttr(row.name)} 的成长记录`}">
        ${checkbox}
        <div class="growth-tree">${growthTreeSvg(row.stage_index)}</div>
        <div class="growth-name">${escapeHtml(row.name)}</div>
        <div class="growth-stage">${escapeHtml(row.stage_name)} · ${row.term_points} 营养</div>
        <div class="growth-bar" role="progressbar" aria-label="${escapeAttr(row.name)}当前阶段进度" aria-valuenow="${pct}" aria-valuemin="0" aria-valuemax="100"><span style="width:${pct}%"></span></div>
        <div class="growth-sub">${next ? `距离${escapeHtml(next.name)}还差 ${Math.max(0, next.min - row.term_points)}` : '已达最高阶段 🎉'}</div>
        <div class="growth-branches" aria-label="五个能力分支记录状态">${dims}</div>
        <div class="growth-week">本周 +${row.week_points}　记录 ${row.coverage ? row.coverage.recorded_events : 0} 条</div>
        <button type="button" class="growth-add" data-act="growth-quick-open" data-id="${row.student_id}" title="手工加分">＋营养</button>
      </div>`;
  }

  // ---------- 单人明细 ----------
  async function growthOpenStudent(id) {
    if (!DATABASE_MODE) { showToast('成长森林需要连接本地数据库', 'error'); return; }
    const termId = currentTermId;
    const requestVersion = growthRequestVersion;
    growthModalTermId = termId;
    openModal('成长明细', '<div class="empty">正在加载…</div>');
    try {
      const detail = await apiRequest(`/api/v1/growth/students/${encodeURIComponent(id)}?term_id=${encodeURIComponent(termId)}`);
      if (requestVersion !== growthRequestVersion || termId !== currentTermId) return;
      growthDetailCache[`${termId}:${id}`] = detail;
      renderGrowthDetail(detail);
    } catch (error) {
      if (requestVersion !== growthRequestVersion || termId !== currentTermId) return;
      openModal('成长明细', `<div class="empty">加载失败：${escapeHtml(error && error.message ? error.message : '未知错误')}</div>`);
    }
  }

  function renderGrowthDetail(detail) {
    const snapshot = detail.snapshot || {};
    const records = Array.isArray(detail.records) ? detail.records : [];
    const cappedReasons = new Set(['capped', 'category_daily_awards', 'category_weekly_awards']);
    const historicalCapped = records.filter(record => record.source_type === 'teacher'
      && record.scoring_mode !== 'teacher_confirmed_v1' && cappedReasons.has(record.cap_reason)).length;
    const next = snapshot.next_stage;
    const pct = Math.round(Math.max(0, Math.min(1, Number(snapshot.stage_progress) || 0)) * 100);
    const dimRows = GROWTH_DIMENSION_ORDER.map(key => {
      const dim = (snapshot.dimensions || {})[key] || {};
      const label = GROWTH_DIMENSION_LABELS[key];
      let text = '待记录';
      if (dim.status === 'ok') {
        // 后端字段是 value（指数平滑后的得分率 0~1），缺失时不显示 0% 以免误导。
        const rate = Number(dim.value);
        text = Number.isFinite(rate)
          ? `平滑得分率 ${(rate * 100).toFixed(1)}%　样本 ${dim.observations || 0} 次`
          : `样本 ${dim.observations || 0} 次`;
      } else if (dim.status === 'insufficient_comparable_history') text = `样本较少（${dim.observations || 0} 次，需 ≥3 个不同日期）`;
      const trend = dim.trend === 'up' ? ' · 近两窗上升' : dim.trend === 'down' ? ' · 近两窗下降' : '';
      return `<tr><td>${label}</td><td>${escapeHtml(text)}${trend}</td></tr>`;
    }).join('');

    const quickButtons = growthPresetsForTerm().map(preset =>
      `<button type="button" class="btn growth-btn-add" data-act="growth-quick" data-id="${detail.student_id}" data-type="${preset.type}" data-pts="${preset.pts}" data-note="${escapeAttr(preset.label)}">＋${preset.pts} ${escapeHtml(preset.label)}</button>`
    ).join('');

    const recordRows = records.length ? records.map(record => {
      const reversible = record.reversible;
      const oldCapped = record.source_type === 'teacher' && record.scoring_mode !== 'teacher_confirmed_v1'
        && cappedReasons.has(record.cap_reason);
      const capNote = record.cap_reason && record.cap_reason !== 'none'
        ? `<span class="growth-cap" title="计分说明">${oldCapped ? '旧规则 · ' : ''}${escapeHtml(growthCapReasonLabel(record.cap_reason))}</span>` : '';
      const legacyNote = record.legacy_points != null ? `<span class="growth-legacy-tag">历史营养</span>` : '';
      const progressNote = growthProgressTag(record);
      const reverseBtn = reversible
        ? `<button type="button" class="growth-del" data-act="growth-reverse" data-id="${record.event_id}" title="撤销这条误录记录">撤销</button>` : '';
      const pointsText = record.legacy_points != null
        ? `${record.legacy_points >= 0 ? '＋' : '－'}${Math.abs(record.legacy_points)}`
        : `${record.applied_points >= 0 ? '＋' : '－'}${Math.abs(record.applied_points)}`;
      return `<tr data-record-kind="${oldCapped ? 'limited' : Number(record.applied_points) > 0 ? 'credited' : 'other'}">
        <td>${escapeHtml(record.business_date || '')}</td>
        <td><span class="${record.applied_points < 0 ? 'growth-neg' : record.applied_points > 0 ? 'growth-pos' : 'growth-zero'}">${pointsText}</span>${capNote}${legacyNote}${progressNote}</td>
        <td><b>${escapeHtml(record.event_label || record.event_type)}</b>${record.note && record.note !== record.event_label ? `<div class="growth-record-note">${escapeHtml(record.note)}</div>` : ''}</td>
        <td class="growth-del-cell">${reverseBtn}</td>
      </tr>`;
    }).join('') : '<tr><td colspan="4">暂无补录记录</td></tr>';

    const history = Array.isArray(detail.history) && detail.history.length
      ? detail.history.map(item => `<li>${escapeHtml(item.term_name || '历史学期')}：${escapeHtml(item.stage_name || '')} · ${item.term_points} 营养</li>`).join('')
      : '<li>暂无历史年轮</li>';

    const pending = Array.isArray(detail.pending_corrections) && detail.pending_corrections.length
      ? detail.pending_corrections.map(item => `<li>${escapeHtml(item.title || item.note || '待订正')}</li>`).join('')
      : '<li>暂无待订正任务</li>';

    const body = `
      <div class="growth-detail">
        <div class="growth-detail-overview">
          <div class="growth-detail-tree" aria-hidden="true">${growthTreeSvg(snapshot.stage_index || 0)}</div>
          <div class="growth-detail-progress">
            <p class="growth-detail-head"><span>${escapeHtml(formatClassLabel(detail.class_name || '未分班'))}</span><span class="growth-stage-pill">${escapeHtml(snapshot.stage_name || '种子')}</span><span class="growth-week-pill">本周 +${snapshot.week_points || 0}</span></p>
            <div class="growth-detail-total"><strong>${snapshot.term_points || 0}</strong><span>本学期营养</span></div>
            <div class="growth-bar growth-bar-detail" role="progressbar" aria-label="当前阶段进度" aria-valuenow="${pct}" aria-valuemin="0" aria-valuemax="100"><span style="width:${pct}%"></span></div>
            <div class="growth-progress-caption"><span>${next ? `距离${escapeHtml(next.name)}还差 ${Math.max(0, next.min - (snapshot.term_points || 0))} 营养` : '已达最高阶段 🎉'}</span><span>${pct}%</span></div>
            ${snapshot.legacy_points ? `<span class="growth-legacy-tag">历史营养（旧规则）${snapshot.legacy_points}</span>` : ''}
          </div>
        </div>
        <div class="growth-detail-grid">
          <div class="growth-detail-col">
            <h4 class="growth-section-title"><span>🌱 快速补录</span><small>点选即补录 · 只加分</small></h4>
            <p class="growth-subtle">新补录按确认分值完整计入，不受每日、每周或类别上限限制。</p>
            <div class="growth-quick">${quickButtons}</div>
            <div class="growth-custom">
              <select id="growthCustomType" class="growth-input" aria-label="补录类别">
                ${growthPresetsForTerm().map(preset => `<option value="${preset.type}">${escapeHtml(preset.label)}</option>`).join('')}
              </select>
              <input type="text" aria-label="补录事由（必填）" id="growthCustomNote" placeholder="事由（必填，如：单元复习达标）" class="growth-input">
              <label class="growth-points-label">营养分值<input type="number" id="growthCustomPoints" min="1" step="1" value="2" class="growth-input" aria-label="营养分值"></label>
              <button type="button" class="btn btn-primary" data-act="growth-custom-add" data-id="${detail.student_id}">确认补录</button>
            </div>
            <h4>📘 五个能力分支</h4><p class="growth-subtle">来自可比测评，无证据显示「待记录」。</p>
            <table class="growth-table">${dimRows}</table>
            <h4>📋 待订正任务</h4>
            <ul class="growth-list">${pending}</ul>
            <h4>🪵 历史年轮</h4>
            <ul class="growth-list">${history}</ul>
          </div>
          <div class="growth-detail-col">
            <h4 class="growth-section-title"><span>补录记录 <span class="growth-count">${records.length}</span></span><small>误录可撤销，保留原记录</small></h4>
            ${historicalCapped ? `<div class="growth-history-notice"><b>${historicalCapped} 条旧补录曾受限额影响</b><span>下方「旧规则」记录保留当时计分，刷新不会补发。需要补足时，请核对事由后新增补录。</span></div>` : ''}
            <div class="growth-record-filters" role="group" aria-label="筛选补录记录">
              <button type="button" data-act="growth-record-filter" data-filter="all" aria-pressed="true">全部</button>
              <button type="button" data-act="growth-record-filter" data-filter="credited" aria-pressed="false">已计入</button>
              ${historicalCapped ? '<button type="button" data-act="growth-record-filter" data-filter="limited" aria-pressed="false">旧规则受限</button>' : ''}
              <span class="growth-filter-count" role="status">${records.length} 条记录</span>
            </div>
            <div class="growth-record-scroll"><table class="growth-table growth-record-table">
              <thead><tr><th>日期</th><th>营养 / 说明</th><th>事由</th><th>操作</th></tr></thead><tbody>
              ${recordRows}
            </tbody></table><p class="growth-filter-empty" hidden>暂无符合条件的记录</p></div>
          </div>
        </div>
        <p class="growth-detail-footnote growth-subtle">记录覆盖：${snapshot.coverage ? `${snapshot.coverage.recorded_events} 条 · ${snapshot.coverage.active_days} 个活动日` : '—'}　规则版本：${escapeHtml(snapshot.rule_version || '')}</p>
      </div>`;
    openModal(`${escapeHtml(detail.name || '')} · 我的成长树`, body);
    const content = document.querySelector('#modal .modal-content');
    content?.classList.add('growth-detail-modal');
    // Shared modals may retain the scroll position from an earlier entry form.
    if (content) content.scrollTop = 0;
    const modalBody = document.getElementById('modalBody');
    if (modalBody) modalBody.scrollTop = 0;
  }

  function growthCapReasonLabel(reason) {
    return {
      capped: '已达当日/当周上限',
      category_daily_awards: '该类别当天次数已达上限',
      category_weekly_awards: '该类别本周次数已达上限',
      legacy: '历史营养（不计入本学期）',
      reversed: '已撤销，不计入当前营养',
      reversal: '撤销原记录',
      reversal_target_missing: '原记录缺失，未计入'
    }[reason] || reason;
  }

  // 进步分徽标：只在真正拿到进步分时显示，并标出相对自己的基线，
  // 避免把「参加考试的基础分」误读成「进步」。
  function growthProgressTag(record) {
    const proposed = Number(record && record.progress_points);
    const awarded = Number(record && record.applied_points);
    const points = Math.min(proposed, Math.max(0, awarded - 2));
    if (!Number.isFinite(points) || points <= 0) return '';
    const baseline = record && record.baseline_rate != null
      ? Number(record.baseline_rate) : NaN;
    const baselineLabel = growthData && growthData.rule_version === 'growth-v3'
      ? '相对自己上一次测评'
      : '相对自己最近三次同类型、同满分测评的较高成绩';
    const tip = Number.isFinite(baseline)
      ? `${baselineLabel}（基线 ${(baseline * 100).toFixed(1)}%）`
      : baselineLabel;
    return `<span class="growth-progress-tag" title="${escapeAttr(tip)}">进步 +${points}</span>`;
  }

  // ---------- 补录 ----------
  async function growthRecordItems(studentIds, eventType, note, points) {
    if (!DATABASE_MODE) { showToast('成长森林需要连接本地数据库', 'error'); return; }
    if (!note || !note.trim()) {
      const input = document.getElementById?.('growthCustomNote');
      input?.setAttribute('aria-invalid', 'true');
      input?.focus();
      showToast('补录必须填写事由', 'error'); return;
    }
    const ids = Array.isArray(studentIds) ? studentIds : [studentIds];
    if (!ids.length) { showToast('请先选择学生', 'error'); return; }
    if (growthSubmitting) return;
    if (growthModalTermId !== null && growthModalTermId !== currentTermId) {
      showToast('学期已切换，请重新打开补录窗口', 'error'); return;
    }
    const confirmedPoints = points == null
      ? (growthPresetsForTerm().find(preset => preset.type === eventType)?.pts || 1)
      : Number(points);
    if (!Number.isSafeInteger(confirmedPoints) || confirmedPoints < 1) {
      const input = document.getElementById?.('growthCustomPoints');
      input?.setAttribute('aria-invalid', 'true');
      input?.focus();
      showToast('营养分值必须为正整数', 'error'); return;
    }
    const items = ids.map(studentId => {
      const item = { student_id: Number(studentId), event_type: eventType, note: note.trim() };
      item.points = confirmedPoints;
      return item;
    });
    const termId = currentTermId;
    const signature = JSON.stringify({ termId, items });
    if (!growthPendingSubmission || growthPendingSubmission.signature !== signature) {
      growthPendingSubmission = {
        signature,
        requestId: `ui-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
      };
    }
    growthSubmitting = true;
    const controls = Array.from(document.querySelectorAll?.('#modal .growth-detail button, #modal .growth-detail input, #modal .growth-detail select') || []);
    const previousControls = controls.map(control => ({ control, disabled: control.disabled }));
    controls.forEach(control => { control.disabled = true; });
    const submit = controls.find(control => control.dataset.act === 'growth-custom-add'
      || (control.dataset.act === 'growth-batch-apply' && control.dataset.type === 'custom'));
    const submitText = submit?.textContent;
    if (submit) { submit.textContent = '正在补录…'; submit.setAttribute('aria-busy', 'true'); }
    try {
      const result = await apiRequest('/api/v1/growth/activities', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ term_id: termId,
          request_id: growthPendingSubmission.requestId, items })
      });
      growthPendingSubmission = null;
      const createdCount = (result.created || []).length;
      const skipped = (result.skipped || []).length;
      const awarded = (result.created || []).reduce((sum, item) => sum + Number(item.applied_points || 0), 0);
      const hasAwardResult = createdCount && result.created.every(item => Number.isFinite(item.applied_points));
      const pointsMessage = hasAwardResult ? `，营养合计 +${awarded}` : '';
      const skippedReason = (result.skipped || []).map(item => item.reason).filter(Boolean)[0];
      showToast(createdCount ? `已记录 ${createdCount} 条成长活动${pointsMessage}${skipped ? `（${skipped} 条跳过：${skippedReason || '请核查'}）` : ''}` : (skippedReason || '没有可记录的条目'), createdCount ? 'success' : 'error');
      growthInvalidate();
      growthResetSelection();
      closeModal();
      await loadGrowthForest();
    } catch (error) {
      showToast(error && error.message ? error.message : '补录失败', 'error');
    } finally {
      growthSubmitting = false;
      previousControls.forEach(({ control, disabled }) => { control.disabled = disabled; });
      if (submit) { submit.textContent = submitText; submit.removeAttribute('aria-busy'); }
    }
  }

  function growthOpenQuick(id) {
    growthModalTermId = currentTermId;
    growthPendingSubmission = null;
    const row = (growthData && growthData.students || []).find(item => String(item.student_id) === String(id));
    const name = row ? row.name : '';
    const buttons = growthPresetsForTerm().map(preset =>
      `<button type="button" class="btn growth-btn-add" data-act="growth-quick" data-id="${id}" data-type="${preset.type}" data-pts="${preset.pts}" data-note="${escapeAttr(preset.label)}">＋${preset.pts} ${escapeHtml(preset.label)}</button>`
    ).join('');
    openModal(`为 ${escapeHtml(name)} 补录营养`, `
      <div class="growth-detail">
        <p class="growth-subtle">教师补录按确认分值完整计入，不受每日、每周或类别上限限制；误录可撤销。</p>
        <div class="growth-quick">${buttons}</div>
        <div class="growth-custom">
          <select id="growthCustomType" class="growth-input" aria-label="补录类别">
            ${growthPresetsForTerm().map(preset => `<option value="${preset.type}">${escapeHtml(preset.label)}</option>`).join('')}
          </select>
          <input type="text" aria-label="补录事由（必填）" id="growthCustomNote" placeholder="事由（必填）" class="growth-input">
          <label class="growth-points-label">营养分值<input type="number" id="growthCustomPoints" min="1" step="1" value="2" class="growth-input" aria-label="营养分值"></label>
          <button type="button" class="btn btn-primary" data-act="growth-custom-add" data-id="${id}">确认补录</button>
        </div>
      </div>`);
  }

  function growthOpenBatch() {
    if (!growthSelectedIds.size) { showToast('请先勾选要操作的学生', 'error'); return; }
    growthModalTermId = currentTermId;
    growthPendingSubmission = null;
    const buttons = growthPresetsForTerm().map(preset =>
      `<button type="button" class="btn growth-btn-add" data-act="growth-batch-apply" data-type="${preset.type}" data-pts="${preset.pts}" data-note="${escapeAttr(preset.label)}">＋${preset.pts} ${escapeHtml(preset.label)}</button>`
    ).join('');
    openModal(`批量补录（${growthSelectedIds.size} 人）`, `
      <div class="growth-detail">
        <p class="growth-subtle">将对 ${growthSelectedIds.size} 名学生补录同一条学习活动；每位学生按确认分值完整计入，不受每日、每周或类别上限限制。</p>
        <div class="growth-quick">${buttons}</div>
        <div class="growth-custom">
          <select id="growthCustomType" class="growth-input" aria-label="补录类别">
            ${growthPresetsForTerm().map(preset => `<option value="${preset.type}">${escapeHtml(preset.label)}</option>`).join('')}
          </select>
          <input type="text" aria-label="补录事由（必填）" id="growthCustomNote" placeholder="事由（必填）" class="growth-input">
          <label class="growth-points-label">营养分值<input type="number" id="growthCustomPoints" min="1" step="1" value="2" class="growth-input" aria-label="营养分值"></label>
          <button type="button" class="btn btn-primary" data-act="growth-batch-apply" data-type="custom">确认补录</button>
        </div>
      </div>`);
  }

  async function growthReverse(eventId) {
    if (!DATABASE_MODE) return;
    if (growthModalTermId !== null && growthModalTermId !== currentTermId) {
      showToast('学期已切换，请重新打开成长明细', 'error'); return;
    }
    try {
      const termId = currentTermId;
      await apiRequest(`/api/v1/growth/events/${encodeURIComponent(eventId)}/reverse`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ term_id: termId, reason: '误录撤销' })
      });
      showToast('已撤销该条记录（原记录保留在账本中）');
      growthInvalidate();
      closeModal();
      await loadGrowthForest();
    } catch (error) {
      showToast(error && error.message ? error.message : '撤销失败', 'error');
    }
  }

  // ---------- 旧版导入 ----------
  function growthOpenLegacyImport() {
    growthModalTermId = currentTermId;
    growthPendingLegacy = null;
    openModal('导入旧版成长记录', `
      <div class="growth-detail">
        <p class="growth-subtle">粘贴 Windows 参考包的 <code>forest.logs</code> JSON，或选择导出的 JSON 文件。先预览学生映射与异常，再确认导入。</p>
        <div class="growth-custom">
          <input type="file" id="growthLegacyFile" accept=".json,application/json" class="growth-input">
          <input type="date" id="growthLegacyCarry" class="growth-input" title="缺日期记录使用的结转日（可留空）">
          <button type="button" class="btn btn-secondary" data-act="growth-legacy-preview">预览</button>
          <button type="button" class="btn btn-primary" data-act="growth-legacy-confirm">确认导入</button>
        </div>
        <textarea id="growthLegacyText" class="growth-input growth-textarea" placeholder='{"logs": {"01": [{"date": "2025-09-01", "pts": 3, "note": "作业按时完成"}]}}'></textarea>
        <div id="growthLegacyPreview" class="growth-legacy-preview"></div>
      </div>`);
  }

  function growthReadLegacyPayload() {
    const text = document.getElementById('growthLegacyText');
    const raw = text ? text.value.trim() : '';
    if (!raw) return null;
    try {
      return JSON.parse(raw);
    } catch (error) {
      showToast('JSON 解析失败，请检查格式', 'error');
      return null;
    }
  }

  async function growthLegacyPreview() {
    const payload = growthReadLegacyPayload();
    if (!payload) { showToast('请先粘贴或选择 JSON', 'error'); return; }
    try {
      const termId = currentTermId;
      const preview = await apiRequest('/api/v1/growth/legacy/preview', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ term_id: termId, payload })
      });
      const box = document.getElementById('growthLegacyPreview');
      if (!box) return;
      const totals = preview.totals || {};
      const anomalies = Array.isArray(preview.anomalies) ? preview.anomalies : [];
      box.innerHTML = `
        <p><b>映射学生 ${totals.students_mapped}</b> · 未映射 ${totals.students_unmapped} · 记录 ${totals.record_count} 条 · 缺日期 ${totals.undated_records} 条 · 手工合计 ${totals.manual_total}</p>
        ${anomalies.length ? `<ul class="growth-list">${anomalies.slice(0, 12).map(item => `<li>${escapeHtml(item.type)}：${escapeHtml(item.detail || '')}${item.student_key ? `（${escapeHtml(item.student_key)}）` : ''}</li>`).join('')}</ul>` : '<p class="growth-subtle">未发现异常。</p>'}
        ${preview.unmapped_students && preview.unmapped_students.length ? `<p class="growth-inline-warn">无法映射的学号（不会按姓名猜）：${preview.unmapped_students.map(item => escapeHtml(item.student_key)).join('、')}</p>` : ''}`;
    } catch (error) {
      showToast(error && error.message ? error.message : '预览失败', 'error');
    }
  }

  async function growthLegacyConfirm() {
    if (growthLegacySubmitting) return;
    if (growthModalTermId !== null && growthModalTermId !== currentTermId) {
      showToast('学期已切换，请重新打开导入窗口', 'error'); return;
    }
    const payload = growthReadLegacyPayload();
    if (!payload) { showToast('请先粘贴或选择 JSON', 'error'); return; }
    const carry = document.getElementById('growthLegacyCarry');
    const termId = currentTermId;
    const carryOverDate = carry && carry.value ? carry.value : null;
    const signature = JSON.stringify({ termId, payload, carryOverDate });
    if (!growthPendingLegacy || growthPendingLegacy.signature !== signature) {
      growthPendingLegacy = { signature, batchId: `ui-legacy-${Date.now()}-${Math.random().toString(36).slice(2, 8)}` };
    }
    growthLegacySubmitting = true;
    try {
      const result = await apiRequest('/api/v1/growth/legacy/confirm', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          term_id: termId,
          payload,
          batch_id: growthPendingLegacy.batchId,
          carry_over_date: carryOverDate
        })
      });
      growthPendingLegacy = null;
      showToast(`已导入 ${(result.created || []).length} 条历史记录`);
      growthInvalidate();
      closeModal();
      await loadGrowthForest();
    } catch (error) {
      showToast(error && error.message ? error.message : '导入失败', 'error');
    } finally {
      growthLegacySubmitting = false;
    }
  }

  // ---------- 事件委托（growth-* 动作，独立委托避免与既有委托冲突） ----------
  document.addEventListener('click', e => {
    const target = e.target.closest('[data-act]');
    if (!target) return;
    const act = target.dataset.act;
    if (!act || !act.startsWith('growth-')) return;
    const id = target.dataset.id;
    if (act === 'growth-record-filter') {
      const detail = target.closest('.growth-detail');
      const filter = target.dataset.filter;
      let count = 0;
      detail.querySelectorAll('[data-record-kind]').forEach(row => {
        row.hidden = filter !== 'all' && row.dataset.recordKind !== filter;
        if (!row.hidden) count++;
      });
      detail.querySelectorAll('[data-act="growth-record-filter"]').forEach(button => {
        button.setAttribute('aria-pressed', String(button === target));
      });
      detail.querySelector('.growth-filter-count').textContent = `${count} 条记录`;
      detail.querySelector('.growth-filter-empty').hidden = count > 0;
    } else if (act === 'growth-class') {
      // 与其它模块一致：班级 chips 走全局范围分发（setGlobalClassFilter 会回调
      // growthSetClass 并清空批量勾选），避免出现两个互相不一致的班级过滤状态。
      setGlobalClassFilter(target.dataset.cls || '');
      render();
    } else if (act === 'growth-refresh') {
      growthInvalidate();
      loadGrowthForest();
    } else if (act === 'growth-student' || act === 'growth-quick-open') {
      if (act === 'growth-quick-open') growthOpenQuick(id); else growthOpenStudent(id);
    } else if (act === 'growth-batch-toggle') {
      growthSelectMode = !growthSelectMode;
      if (!growthSelectMode) growthSelectedIds.clear();
      render();
    } else if (act === 'growth-check' || act === 'growth-check-card') {
      const key = String(id);
      if (growthSelectedIds.has(key)) growthSelectedIds.delete(key); else growthSelectedIds.add(key);
      render();
    } else if (act === 'growth-sel-all') {
      growthVisibleStudents().forEach(row => growthSelectedIds.add(String(row.student_id)));
      render();
    } else if (act === 'growth-sel-none') {
      growthSelectedIds.clear();
      render();
    } else if (act === 'growth-batch-open') {
      growthOpenBatch();
    } else if (act === 'growth-batch-apply') {
      const type = target.dataset.type;
      if (type === 'custom') {
        const sel = document.getElementById('growthCustomType');
        const note = document.getElementById('growthCustomNote');
        const chosen = sel ? sel.value : 'task_completed';
        const bonus = document.getElementById('growthCustomPoints');
        growthRecordItems([...growthSelectedIds], chosen, note ? note.value : '',
          bonus ? bonus.value : null);
      } else {
        growthRecordItems([...growthSelectedIds], type, target.dataset.note || '', target.dataset.pts);
      }
    } else if (act === 'growth-quick') {
      growthRecordItems([id], target.dataset.type, target.dataset.note || '', target.dataset.pts);
    } else if (act === 'growth-custom-add') {
      const sel = document.getElementById('growthCustomType');
      const note = document.getElementById('growthCustomNote');
      const bonus = document.getElementById('growthCustomPoints');
      const chosen = sel ? sel.value : 'task_completed';
      growthRecordItems([id], chosen, note ? note.value : '',
        bonus ? bonus.value : null);
    } else if (act === 'growth-reverse') {
      growthReverse(id);
    } else if (act === 'growth-legacy-open') {
      growthOpenLegacyImport();
    } else if (act === 'growth-legacy-preview') {
      growthLegacyPreview();
    } else if (act === 'growth-legacy-confirm') {
      growthLegacyConfirm();
    }
  });

  document.addEventListener('keydown', e => {
    if ((e.key !== 'Enter' && e.key !== ' ') || !e.target.matches('.growth-card')) return;
    e.preventDefault();
    const id = e.target.dataset.id;
    e.target.click();
    // Selection re-renders the card; keep keyboard focus on the same student.
    document.querySelector(`.growth-card[data-id="${CSS.escape(id)}"]`)?.focus();
  });

  document.addEventListener('input', e => {
    if (e.target.id === 'growthCustomNote' || e.target.id === 'growthCustomPoints') {
      e.target.removeAttribute('aria-invalid');
    }
  });

  document.addEventListener('change', e => {
    const target = e.target;
    if (!target) return;
    if (target.dataset && target.dataset.act === 'growth-sort') {
      growthSort = target.value;
      render();
    } else if (target.id === 'growthCustomType') {
      const input = document.getElementById('growthCustomPoints');
      const preset = growthPresetsForTerm().find(item => item.type === target.value);
      if (input && preset) input.value = preset.pts;
    } else if (target.id === 'growthLegacyFile') {
      const file = target.files && target.files[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = () => {
        const box = document.getElementById('growthLegacyText');
        if (box) box.value = String(reader.result || '');
      };
      reader.readAsText(file);
    }
  });

  window.renderGrowth = renderGrowth;
  window.loadGrowthForest = loadGrowthForest;
  window.growthResetSelection = growthResetSelection;
  window.growthSetClass = growthSetClass;
  window.growthInvalidate = growthInvalidate;
})();
