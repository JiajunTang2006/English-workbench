    // ================= 英语成长森林模块（2026-09-14 新增） =================
    // 独立文件便于应用升级后重新挂载。数据来源：state.dictation / state.exams（自动）
    // + state.forest.logs（老师手动补录），随 saveData() 写入 workspace_states。
    (function () {
      'use strict';

      const FE_STAGES = [
        { min: 0,   name: '种子',     icon: '🌰' },
        { min: 40,  name: '发芽',     icon: '🌱' },
        { min: 100, name: '小树苗',   icon: '🌿' },
        { min: 200, name: '茁壮成长', icon: '🪴' },
        { min: 320, name: '开花',     icon: '🌸' },
        { min: 480, name: '结果',     icon: '🍎' },
        { min: 700, name: '森林之星', icon: '🌳' }
      ];

      // 手动补录的预设按钮
      const FE_QUICK = [
        { pts: 2, note: '课堂主动回答' },
        { pts: 3, note: '作业按时完成' },
        { pts: 5, note: '作业优秀' },
        { pts: 3, note: '英语跟读完成' },
        { pts: 5, note: '阅读任务完成' },
        { pts: 10, note: '连续学习7天' }
      ];

      // 预设扣分项（作业/订正/笔记等未完成情况）
      const FE_DEDUCT = [
        { pts: 5, note: '作业未交' },
        { pts: 3, note: '作业未订正' },
        { pts: 3, note: '无课堂笔记' },
        { pts: 2, note: '默写未订正' },
        { pts: 2, note: '课堂走神/违纪' },
        { pts: 3, note: '早读缺席' }
      ];

      let feCls = '';
      let feSort = 'pts';
      // 批量操作模式：勾选多名学生一次性加分/扣分
      let feSelectMode = false;
      const feSel = new Set();

      function feStage(pts) {
        let s = FE_STAGES[0];
        for (const st of FE_STAGES) if (pts >= st.min) s = st;
        return s;
      }
      function feNext(pts) {
        return FE_STAGES.find(st => st.min > pts) || null;
      }

      // 手绘风 SVG 树（7 个成长阶段，idx: 0-6）
      function feTreeSvg(idx) {
        const wobbly = (x1, y1, x2, y2, bulge) =>
          `M${x1},${y1} Q${(x1 + x2) / 2 + bulge},${(y1 + y2) / 2} ${x2},${y2}`;
        const soil = `<path d="M20,58 Q32,54 44,58" fill="none" stroke="#a08455" stroke-width="2" stroke-linecap="round"/>
          <path d="M16,60 Q20,58.5 24,59.5 M40,59.5 Q44,58.5 48,60" fill="none" stroke="#c4b08a" stroke-width="1.2" stroke-linecap="round"/>`;
        const trunkStroke = `fill="none" stroke="#8a6a43" stroke-width="2.6" stroke-linecap="round"`;
        let body = '';
        if (idx === 0) {
          body = `<ellipse cx="32" cy="52" rx="5" ry="6.5" fill="#b58a5a" transform="rotate(-14 32 52)"/>
            <path d="M29,48 Q27,44 29,41" fill="none" stroke="#d9c39a" stroke-width="1.4" stroke-linecap="round"/>`;
        } else if (idx === 1) {
          body = `<path d="M32,57 Q31,48 32.5,42" ${trunkStroke}/>
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
          const trunkPath = `<path d="M32,58 Q33,${58 - trunkH * 0.5} 32,${lift + 1}" ${trunkStroke}/>
            <path d="M32,${lift + 4} Q28,${lift + 6} 26,${lift + 3}" ${trunkStroke.replace('2.6', '1.6')}/>`;
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
        return `<svg viewBox="0 0 64 64" class="fe-tree-svg" role="img" aria-hidden="true">${soil}${body}</svg>`;
      }

      // 单次成绩 → 营养值（kind: 'dict' | 'exam'）
      function feScorePts(score, full, kind) {
        const f = Number(full) || 100;
        const pct = (Number(score) / f) * 100;
        if (!isFinite(pct)) return 0;
        if (kind === 'exam') {
          if (pct >= 85) return 15;
          if (pct >= 75) return 10;
          return 5;
        }
        if (pct >= 99.5) return 10;
        if (pct >= 90) return 8;
        if (pct >= 80) return 6;
        if (pct >= 60) return 4;
        return 1;
      }

      function feExamScoreOf(rec) {
        if (!rec) return null;
        if (rec['英语'] != null) return Number(rec['英语']);
        for (const k of Object.keys(rec)) {
          const v = Number(rec[k]);
          if (!isNaN(v) && rec[k] != null && typeof rec[k] !== 'object') return v;
        }
        return null;
      }

      function feEnsureState() {
        if (!state.forest || typeof state.forest !== 'object') state.forest = {};
        if (!state.forest.logs || typeof state.forest.logs !== 'object') state.forest.logs = {};
      }

      function feCompute() {
        feEnsureState();
        const dict = (state && state.dictation) || {};
        const exams = (state && state.exams) || [];
        const logs = state.forest.logs;
        const rows = [];
        for (const s of (state.students || [])) {
          let dictPts = 0, dictCount = 0, dictFull = 0;
          const arr = dict[s.id];
          if (Array.isArray(arr)) {
            for (const v of arr) {
              if (v == null || v === '') continue;
              dictPts += feScorePts(v, 100, 'dict');
              dictCount++;
              if (Number(v) >= 99.5) dictFull++;
            }
          }
          let examPts = 0, examCount = 0, examFull = 0;
          for (const ex of exams) {
            const rec = ex.scores && ex.scores[s.id];
            const sc = feExamScoreOf(rec);
            if (sc == null) continue;
            examPts += feScorePts(sc, ex.fullScore, 'exam');
            examCount++;
            const f = Number(ex.fullScore) || 100;
            if (sc / f >= 0.995) examFull++;
          }
          const myLogs = Array.isArray(logs[s.id]) ? logs[s.id] : [];
          const manualAdd = myLogs.reduce((a, l) => a + Math.max(0, Number(l && l.pts) || 0), 0);
          const manualDed = myLogs.reduce((a, l) => a + Math.min(0, Number(l && l.pts) || 0), 0);
          const manual = manualAdd + manualDed;
          rows.push({
            id: s.id, name: s.name, cls: s.class,
            pts: dictPts + examPts + manual,
            dictPts, dictCount, dictFull, examPts, examCount, examFull,
            manual, manualAdd, manualDed, logCount: myLogs.length
          });
        }
        return rows;
      }

      function feSorted(rows) {
        const r = rows.slice();
        if (feSort === 'name') r.sort((a, b) => String(a.name).localeCompare(String(b.name), 'zh'));
        else if (feSort === 'id') {
          // 学号优先按数字比较（"8" < "10"），非纯数字学号回退到字符串比较
          r.sort((a, b) => {
            const na = Number(a.id), nb = Number(b.id);
            if (!isNaN(na) && !isNaN(nb)) return na - nb;
            return String(a.id).localeCompare(String(b.id), 'zh');
          });
        }
        else r.sort((a, b) => b.pts - a.pts || String(a.name).localeCompare(String(b.name), 'zh'));
        return r;
      }

      function feCard(r) {
        const st = feStage(r.pts);
        const nx = feNext(r.pts);
        const base = nx ? nx.min : st.min || 1;
        const from = st.min;
        const pct = nx ? Math.max(0, Math.min(100, Math.round(((r.pts - from) / (base - from)) * 100))) : 100;
        const checkbox = feSelectMode
          ? `<label class="fe-check"><input type="checkbox" data-act="forest-check" data-id="${escapeHtml(r.id)}" ${feSel.has(String(r.id)) ? 'checked' : ''}><span></span></label>`
          : '';
        return `
          <div class="fe-card ${feSel.has(String(r.id)) && feSelectMode ? 'fe-card-sel' : ''}" data-act="${feSelectMode ? 'forest-check-card' : 'forest-student'}" data-id="${escapeHtml(r.id)}" title="${feSelectMode ? '点卡片勾选/取消' : `查看 ${escapeHtml(r.name)} 的成长记录`}">
            ${checkbox}
            <div class="fe-tree">${feTreeSvg(FE_STAGES.indexOf(st))}</div>
            <div class="fe-name">${escapeHtml(r.name)}</div>
            <div class="fe-stage">${st.name} · ${r.pts} 营养</div>
            <div class="fe-bar"><span style="width:${pct}%"></span></div>
            <div class="fe-sub">${nx ? `距离${nx.name}还差 ${nx.min - r.pts}` : '已达最高阶段 🎉'}</div>
            <button type="button" class="fe-add" data-act="forest-add" data-id="${escapeHtml(r.id)}" title="补录营养 / 扣除营养">±补录</button>
          </div>`;
      }

      function renderForest() {
        if (!state) return '<div class="empty">数据尚未加载</div>';
        feEnsureState();
        const all = feCompute();
        const rows = feSorted(all.filter(r => !feCls || r.cls === feCls));
        const total = rows.reduce((a, r) => a + r.pts, 0);
        const avg = rows.length ? Math.round(total / rows.length) : 0;
        const fruit = rows.filter(r => r.pts >= 320).length;
        const chips = [''].concat((state.classes || []).slice())
          .map(c => `<button type="button" class="fe-chip ${feCls === c ? 'active' : ''}" data-act="forest-class" data-cls="${escapeHtml(c)}">${c === '' ? '全部班级' : escapeHtml(c)}</button>`).join('');
        const rules = FE_STAGES.map(s => `${s.icon}${s.min}`).join('　');
        return `
          <div class="fe-wrap">
            <div class="fe-toolbar">
              <div class="fe-chips">${chips}</div>
              <div class="fe-actions">
                <input type="text" class="fe-input fe-search" id="forestSearchInput" placeholder="🔍 学号/姓名查营养" data-act="forest-search-input">
                <button type="button" class="btn" data-act="forest-search">查找</button>
                <select class="fe-select" data-act="forest-sort-sel" id="forestSortSel" aria-label="排序方式">
                  <option value="pts" ${feSort === 'pts' ? 'selected' : ''}>按成长值</option>
                  <option value="id" ${feSort === 'id' ? 'selected' : ''}>按学号</option>
                  <option value="name" ${feSort === 'name' ? 'selected' : ''}>按姓名</option>
                </select>
                <button type="button" class="btn ${feSelectMode ? 'fe-btn-batch-on' : ''}" data-act="forest-batch-toggle">${feSelectMode ? '✓ 完成批量操作' : '☑ 批量补录'}</button>
                <button type="button" class="btn" data-act="forest-export">📄 导出班级森林页</button>
              </div>
            </div>
            <div class="fe-summary">
              <div class="fe-stat"><b>${rows.length}</b><span>棵树</span></div>
              <div class="fe-stat"><b>${total}</b><span>班级总营养</span></div>
              <div class="fe-stat"><b>${avg}</b><span>平均营养</span></div>
              <div class="fe-stat"><b>${fruit}</b><span>已开花以上</span></div>
            </div>
            <div class="fe-rules" title="${escapeHtml(rules)}">🌱 成长阶段：${escapeHtml(rules)}　·　营养来自默写/考试成绩（自动）＋老师补录（含作业未交、未订正等扣分），点击学生可看明细</div>
            ${feSelectMode ? `<div class="fe-batchbar">
              <span>已选 <b>${feSel.size}</b> 人</span>
              <button type="button" class="btn" data-act="forest-sel-all">全选本页</button>
              <button type="button" class="btn" data-act="forest-sel-none">清空选择</button>
              <button type="button" class="btn fe-btn-ded" data-act="forest-batch-open">－ 批量扣分</button>
              <button type="button" class="btn fe-btn-add" data-act="forest-batch-open-add">＋ 批量加分</button>
            </div>` : ''}
            <div class="fe-grid">${rows.map(feCard).join('')}</div>
            ${rows.length === 0 ? '<div class="empty">该班级暂无学生</div>' : ''}
          </div>`;
      }

      function feOpenStudent(id) {
        const s = (state.students || []).find(x => String(x.id) === String(id));
        if (!s) return;
        const r = feCompute().find(x => x.id === s.id);
        if (!r) return;
        const rawLogs = Array.isArray(state.forest.logs[s.id]) ? state.forest.logs[s.id] : [];
        // 倒序展示，但保留原始下标便于删除
        const myLogs = rawLogs.map((l, i) => ({ l: l, i: i })).reverse();
        const quickBtns = FE_QUICK.map(q =>
          `<button type="button" class="btn fe-btn-add" data-act="forest-quick" data-id="${escapeHtml(s.id)}" data-pts="${q.pts}" data-note="${escapeHtml(q.note)}">＋${q.pts} ${escapeHtml(q.note)}</button>`
        ).join('');
        const deductBtns = FE_DEDUCT.map(q =>
          `<button type="button" class="btn fe-btn-ded" data-act="forest-quick" data-id="${escapeHtml(s.id)}" data-pts="-${q.pts}" data-note="${escapeHtml(q.note)}">－${q.pts} ${escapeHtml(q.note)}</button>`
        ).join('');
        const logRows = myLogs.length
          ? myLogs.map(({ l, i }) => {
              const v = Number(l.pts) || 0;
              const cell = v < 0
                ? `<span class="fe-neg">－${Math.abs(v)}</span>`
                : `<span class="fe-pos">＋${v}</span>`;
              return `<tr><td>${escapeHtml(l.date || '')}</td><td>${cell}</td><td>${escapeHtml(l.note || '')}</td>
                <td class="fe-del-cell"><button type="button" class="fe-del" data-act="forest-del" data-id="${escapeHtml(s.id)}" data-idx="${i}" title="删除这条记录">✕</button></td></tr>`;
            }).join('')
          : '<tr><td colspan="4">暂无补录记录</td></tr>';
        const dedText = r.manualDed < 0 ? `　<span class="fe-neg">－${Math.abs(r.manualDed)}</span>` : '';
        const body = `
          <div class="fe-detail">
            <p><b>${escapeHtml(s.name)}</b>（${escapeHtml(s.cls || '')}）　当前阶段：<b>${feStage(r.pts).name}</b>　总营养：<b>${r.pts}</b></p>
            <table class="fe-table">
              <tr><td>默写成绩</td><td>${r.dictCount} 次 · ＋${r.dictPts}（满分 ${r.dictFull} 次）</td></tr>
              <tr><td>考试分数</td><td>${r.examCount} 次 · ＋${r.examPts}（满分 ${r.examFull} 次）</td></tr>
              <tr><td>老师补录</td><td>${r.logCount} 条 · <span class="fe-pos">＋${r.manualAdd}</span>${dedText}</td></tr>
            </table>
            <h4>🌱 快速加分</h4>
            <div class="fe-quick">${quickBtns}</div>
            <h4>🍂 快速扣分</h4>
            <div class="fe-quick">${deductBtns}</div>
            <div class="fe-custom">
              <select id="forestCustomSign" class="fe-input">
                <option value="1">＋ 加分</option>
                <option value="-1">－ 扣分</option>
              </select>
              <input type="number" id="forestCustomPts" min="1" max="50" placeholder="营养值" class="fe-input">
              <input type="text" id="forestCustomNote" placeholder="原因（如：作业未订正）" class="fe-input">
              <button type="button" class="btn" data-act="forest-quick-custom" data-id="${escapeHtml(s.id)}">确认补录</button>
            </div>
            <h4>补录记录</h4>
            <table class="fe-table"><tr><th>日期</th><th>营养</th><th>原因</th><th></th></tr>${logRows}</table>
          </div>`;
        openModal(`${escapeHtml(s.name)} · 我的成长树`, body);
      }

      function feAddManual(id, pts, note) {
        const p = Math.round(Number(pts));
        if (!p || isNaN(p)) { showToast('请输入有效的营养值', 'error'); return; }
        if (Math.abs(p) > 50) { showToast('单次补录不能超过 50 点', 'error'); return; }
        closeModal();
        commitMutation(() => {
          feEnsureState();
          const list = (state.forest.logs[id] = state.forest.logs[id] || []);
          list.push({ date: new Date().toISOString().slice(0, 10), pts: p, note: String(note || '').trim() });
        }, { successMessage: p > 0 ? `已为该学生增加 ${p} 点营养 💧` : `已扣除 ${Math.abs(p)} 点营养 🍂` });
      }

      // 删除一条补录记录（录错时可撤销）
      function feDelLog(id, idx) {
        const list = state.forest && state.forest.logs ? state.forest.logs[id] : null;
        const rec = list && list[idx];
        if (!rec) { showToast('记录已不存在', 'error'); return; }
        const v = Number(rec.pts) || 0;
        commitMutation(() => {
          feEnsureState();
          const l = state.forest.logs[id];
          if (l && l[idx] != null) l.splice(idx, 1);
        }, { successMessage: `已撤销该条记录（${v > 0 ? '＋' : '－'}${Math.abs(v)}）` });
        setTimeout(() => feOpenStudent(id), 0);
      }

      // 批量补录弹窗（mode: 'ded' 扣分 / 'add' 加分）
      function feOpenBatch(mode) {
        if (!feSel.size) { showToast('请先勾选要操作的学生', 'error'); return; }
        const isDed = mode === 'ded';
        const presets = (isDed ? FE_DEDUCT : FE_QUICK).map(q =>
          `<button type="button" class="btn ${isDed ? 'fe-btn-ded' : 'fe-btn-add'}" data-act="forest-batch-apply" data-sign="${isDed ? -1 : 1}" data-pts="${q.pts}" data-note="${escapeHtml(q.note)}">${isDed ? '－' : '＋'}${q.pts} ${escapeHtml(q.note)}</button>`
        ).join('');
        const names = [...feSel].map(id => {
          const s = (state.students || []).find(x => String(x.id) === String(id));
          return escapeHtml(s ? s.name : id);
        }).join('、');
        const body = `
          <div class="fe-detail">
            <p>将对 <b>${feSel.size}</b> 名学生${isDed ? '扣除' : '增加'}营养：<span class="fe-batch-names">${names}</span></p>
            <h4>${isDed ? '🍂 选择扣分项' : '🌱 选择加分项'}</h4>
            <div class="fe-quick">${presets}</div>
            <div class="fe-custom">
              <input type="number" id="forestBatchPts" min="1" max="50" placeholder="营养值" class="fe-input">
              <input type="text" id="forestBatchNote" placeholder="原因" class="fe-input">
              <button type="button" class="btn" data-act="forest-batch-apply" data-sign="${isDed ? -1 : 1}" data-pts="custom">${isDed ? '确认扣除' : '确认增加'}</button>
            </div>
          </div>`;
        openModal(`批量${isDed ? '扣除' : '增加'}营养（${feSel.size} 人）`, body);
      }

      function feBatchApply(sign, ptsRaw, noteRaw) {
        let p = Math.round(Number(ptsRaw));
        if (ptsRaw === 'custom') {
          const el = document.getElementById('forestBatchPts');
          p = Math.round(Math.abs(Number(el ? el.value : 0)));
        }
        if (!p || isNaN(p)) { showToast('请输入有效的营养值', 'error'); return; }
        if (p > 50) { showToast('单次补录不能超过 50 点', 'error'); return; }
        const note = String(noteRaw || '').trim();
        const ids = [...feSel];
        const today = new Date().toISOString().slice(0, 10);
        closeModal();
        commitMutation(() => {
          feEnsureState();
          for (const id of ids) {
            const list = (state.forest.logs[id] = state.forest.logs[id] || []);
            list.push({ date: today, pts: sign * p, note: note });
          }
        }, { successMessage: `已为 ${ids.length} 名学生${sign < 0 ? '扣除' : '增加'} ${p} 点营养` });
        feSel.clear();
      }

      // 按学号/姓名查找 → 打开营养明细（多个匹配时列出候选）
      function feSearch() {
        const el = document.getElementById('forestSearchInput');
        const q = String(el ? el.value : '').trim().toLowerCase();
        if (!q) { showToast('请输入学号或姓名', 'error'); return; }
        const all = feCompute();
        const exact = all.find(r => String(r.id).toLowerCase() === q);
        const hits = exact ? [exact]
          : all.filter(r => String(r.id).toLowerCase().includes(q)
            || String(r.name).toLowerCase().includes(q));
        if (!hits.length) { showToast('没找到该学号/姓名', 'error'); return; }
        if (hits.length === 1) { feOpenStudent(hits[0].id); return; }
        // 多个匹配：按学号排序列出候选供点选
        hits.sort((a, b) => {
          const na = Number(a.id), nb = Number(b.id);
          if (!isNaN(na) && !isNaN(nb)) return na - nb;
          return String(a.id).localeCompare(String(b.id), 'zh');
        });
        const rows = hits.map(r => {
          const st = feStage(r.pts);
          return `<tr class="fe-search-row" data-act="forest-search-open" data-id="${escapeHtml(r.id)}">
            <td>${escapeHtml(r.id)}</td><td>${escapeHtml(r.name)}</td><td>${escapeHtml(r.cls || '')}</td>
            <td>${st.name} · <b>${r.pts}</b> 营养</td></tr>`;
        }).join('');
        openModal(`找到 ${hits.length} 名学生`, `
          <div class="fe-detail"><table class="fe-table">
          <tr><th>学号</th><th>姓名</th><th>班级</th><th>营养值</th></tr>${rows}</table>
          <p style="font-size:14px;opacity:.6;">点击行查看营养明细</p></div>`);
      }

      function feExport() {
        feEnsureState();
        const all = feSorted(feCompute().filter(r => !feCls || r.cls === feCls));
        if (!all.length) { showToast('当前筛选下没有学生', 'error'); return; }
        const clsName = feCls || '全部班级';
        const cards = all.map(r => {
          const st = feStage(r.pts);
          return `<div class="card"><div class="tree">${feTreeSvg(FE_STAGES.indexOf(st))}</div><div class="nm">${escapeHtml(r.name)}</div>
            <div class="stg">${st.name}</div><div class="pts">${r.pts} 营养</div></div>`;
        }).join('');
        const total = all.reduce((a, r) => a + r.pts, 0);
        const html = `<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>英语成长森林 · ${escapeHtml(clsName)}</title><style>
body{font-family:"Microsoft YaHei",sans-serif;background:#f5f0e8;margin:0;padding:24px;}
h1{text-align:center;font-size:22px;margin:8px 0 4px;}
.sub{text-align:center;color:#8a7f6a;font-size:13px;margin-bottom:20px;}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:14px;max-width:1100px;margin:0 auto;}
.card{background:#fff;border:1px solid #e5ddc9;border-radius:12px;padding:16px 10px;text-align:center;}
.tree{height:60px;display:flex;align-items:flex-end;justify-content:center;}
.tree svg{width:60px;height:60px;}
.nm{font-weight:600;margin-top:6px;}
.stg{color:#3b6d11;font-size:12px;margin-top:2px;}
.pts{color:#8a7f6a;font-size:12px;}
</style></head><body>
<h1>🌳 英语成长森林 · ${escapeHtml(clsName)}</h1>
<div class="sub">每天一点营养，让英语之树茁壮成长。　共 ${all.length} 棵树 · 班级总营养 ${total} · 生成于 ${new Date().toLocaleDateString('zh-CN')}</div>
<div class="grid">${cards}</div>
</body></html>`;
        const blob = new Blob([html], { type: 'text/html;charset=utf-8' });
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = `英语成长森林_${clsName}_${new Date().toISOString().slice(0, 10)}.html`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        showToast('森林页已导出，可直接发班级群', 'success');
      }

      // 事件委托（forest-* 动作，避免与既有委托冲突）
      document.addEventListener('click', e => {
        const t = e.target.closest('[data-act]');
        if (!t) return;
        const act = t.dataset.act;
        if (act === 'forest-class') { feCls = t.dataset.cls || ''; render(); }
        else if (act === 'forest-export') feExport();
        else if (act === 'forest-student' || act === 'forest-add') feOpenStudent(t.dataset.id);
        else if (act === 'forest-search') feSearch();
        else if (act === 'forest-search-open') { feOpenStudent(t.dataset.id); }
        else if (act === 'forest-batch-toggle') { feSelectMode = !feSelectMode; if (!feSelectMode) feSel.clear(); render(); }
        else if (act === 'forest-check' || act === 'forest-check-card') {
          const id = String(t.dataset.id);
          if (feSel.has(id)) feSel.delete(id); else feSel.add(id);
          render();
        }
        else if (act === 'forest-sel-all') {
          const all = feSorted(feCompute().filter(r => !feCls || r.cls === feCls));
          all.forEach(r => feSel.add(String(r.id)));
          render();
        }
        else if (act === 'forest-sel-none') { feSel.clear(); render(); }
        else if (act === 'forest-batch-open') feOpenBatch('ded');
        else if (act === 'forest-batch-open-add') feOpenBatch('add');
        else if (act === 'forest-batch-apply') {
          let note = t.dataset.note || '';
          if (t.dataset.pts === 'custom') {
            const el = document.getElementById('forestBatchNote');
            note = el ? el.value : '';
          }
          feBatchApply(Number(t.dataset.sign), t.dataset.pts, note);
        }
        else if (act === 'forest-quick') feAddManual(t.dataset.id, t.dataset.pts, t.dataset.note);
        else if (act === 'forest-del') { e.stopPropagation(); feDelLog(t.dataset.id, Number(t.dataset.idx)); }
        else if (act === 'forest-quick-custom') {
          const pts = document.getElementById('forestCustomPts');
          const note = document.getElementById('forestCustomNote');
          const sign = document.getElementById('forestCustomSign');
          const s = sign && sign.value === '-1' ? -1 : 1;
          feAddManual(t.dataset.id, s * Math.abs(Number(pts ? pts.value : 0)), note ? note.value : '');
        }
      });
      document.addEventListener('change', e => {
        if (e.target && e.target.id === 'forestSortSel') { feSort = e.target.value; render(); }
      });
      // 查找框：回车触发
      document.addEventListener('keydown', e => {
        if (e.key === 'Enter' && e.target && e.target.id === 'forestSearchInput') {
          e.preventDefault();
          feSearch();
        }
      });

      // 样式注入一次
      const feCss = `
        .fe-wrap{padding:4px 2px;}
        .fe-toolbar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;justify-content:space-between;margin-bottom:12px;}
        .fe-chips{display:flex;gap:8px;flex-wrap:wrap;}
        .fe-chip{border:1px solid var(--color-border-secondary,#d3d1c7);background:transparent;border-radius:999px;padding:6px 16px;cursor:pointer;font-size:16px;}
        .fe-chip.active{background:#3b6d11;color:#fff;border-color:#3b6d11;}
        .fe-actions{display:flex;gap:8px;align-items:center;}
        .fe-select{padding:6px 10px;border-radius:8px;border:1px solid var(--color-border-secondary,#d3d1c7);background:transparent;font-size:16px;}
        .fe-summary{display:flex;gap:14px;flex-wrap:wrap;margin-bottom:10px;}
        .fe-stat{background:var(--color-background-secondary,#f1efe8);border-radius:10px;padding:8px 16px;display:flex;flex-direction:column;align-items:center;min-width:88px;}
        .fe-stat b{font-size:22px;}
        .fe-stat span{font-size:15px;opacity:.7;}
        .fe-rules{font-size:15px;opacity:.8;margin-bottom:12px;line-height:1.6;}
        .fe-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(178px,1fr));gap:12px;}
        .fe-card{position:relative;background:var(--color-background-primary,#fff);border:1px solid var(--color-border-tertiary,rgba(0,0,0,.15));border-radius:12px;padding:14px 10px 12px;text-align:center;cursor:pointer;transition:transform .15s, box-shadow .15s;}
        .fe-card:hover{transform:translateY(-2px);box-shadow:0 4px 12px rgba(0,0,0,.08);}
        .fe-tree{height:56px;display:flex;align-items:flex-end;justify-content:center;}
        .fe-tree-svg{width:56px;height:56px;}
        .fe-name{font-weight:600;margin-top:4px;font-size:18px;}
        .fe-stage{font-size:15px;color:#3b6d11;margin-top:2px;}
        .fe-bar{height:5px;background:var(--color-background-tertiary,#eee);border-radius:3px;margin:7px 8px 4px;overflow:hidden;}
        .fe-bar span{display:block;height:100%;background:#639922;border-radius:3px;}
        .fe-sub{font-size:14px;opacity:.75;}
        .fe-add{position:absolute;top:8px;right:8px;border:1px solid var(--color-border-secondary,#d3d1c7);background:transparent;border-radius:8px;font-size:13px;padding:3px 8px;cursor:pointer;}
        .fe-add:hover{background:#eaf3de;}
        .fe-detail table{width:100%;border-collapse:collapse;margin:8px 0 14px;}
        .fe-detail td,.fe-detail th{border-bottom:1px solid var(--color-border-tertiary,rgba(0,0,0,.12));padding:7px 5px;font-size:16px;text-align:left;}
        .fe-quick{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0 14px;}
        .fe-custom{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px;}
        .fe-input{padding:7px 10px;border:1px solid var(--color-border-secondary,#d3d1c7);border-radius:8px;font-size:16px;}
        .fe-detail h4{margin:14px 0 2px;font-size:17px;}
        .btn.fe-btn-add{color:#3b6d11;border-color:#9ec97a;background:#f2f8ea;}
        .btn.fe-btn-add:hover{background:#e4f2d4;}
        .btn.fe-btn-ded{color:#a32b1e;border-color:#e2a49c;background:#fdf1ef;}
        .btn.fe-btn-ded:hover{background:#fbe2de;}
        .fe-pos{color:#3b6d11;font-weight:600;}
        .fe-neg{color:#b3261e;font-weight:600;}
        .fe-del-cell{width:26px;text-align:right;}
        .fe-del{border:none;background:transparent;color:#a8a49a;font-size:15px;cursor:pointer;padding:0 4px;line-height:1;}
        .fe-del:hover{color:#b3261e;}
        .fe-check{position:absolute;top:8px;left:8px;cursor:pointer;}
        .fe-check input{width:17px;height:17px;cursor:pointer;accent-color:#3b6d11;}
        .fe-card-sel{border-color:#3b6d11;box-shadow:0 0 0 2px rgba(99,153,34,.25);}
        .fe-batchbar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;background:#f2f8ea;border:1px dashed #9ec97a;border-radius:10px;padding:8px 14px;margin-bottom:12px;font-size:16px;}
        .fe-batchbar b{font-size:20px;color:#3b6d11;}
        .fe-btn-batch-on{background:#3b6d11;color:#fff;border-color:#3b6d11;}
        .fe-batch-names{color:#8a7f6a;font-size:15px;line-height:1.6;}
        .fe-search{width:180px;}
        .fe-search-row{cursor:pointer;}
        .fe-search-row:hover{background:#f2f8ea;}
      `;
      if (!document.getElementById('fe-style')) {
        const el = document.createElement('style');
        el.id = 'fe-style';
        el.textContent = feCss;
        document.head.appendChild(el);
      }

      window.renderForest = renderForest;
    })();
