    function handleMutationResult(result, onSuccess, onFailure) {
      const finish = value => {
        const callback = value?.ok ? onSuccess : onFailure;
        if (!callback) return value;
        const callbackResult = callback(value || { ok: false });
        return callbackResult === undefined ? value : callbackResult;
      };
      return result && typeof result.then === 'function'
        ? result.then(finish)
        : finish(result);
    }

    const ESCAPE_EDITABLE_ACTS = new Set(['score-edit', 'dict-edit', 'dict-name', 'writing-edit-score']);
    // 同一个弹层内打开二级页面时保留上一层视图，关闭/按 Esc 时逐级返回。
    // 只保存视图数据，不保存 DOM 引用，避免重绘后引用失效。
    const modalViewStack = [];

    function consumeEscape(event) {
      event.preventDefault();
      event.stopPropagation();
    }

    function focusAfterRender(selector) {
      setTimeout(() => document.querySelector(selector)?.focus(), 0);
    }

    function serializeModalFormState() {
      const modal = document.getElementById('modal');
      if (!modal) return '[]';
      const fields = [...modal.querySelectorAll('.modal-body input, .modal-body textarea, .modal-body select, .modal-body [contenteditable="true"]')];
      return JSON.stringify(fields.map((field, index) => {
        if (field.matches('input[type="checkbox"], input[type="radio"]')) return [index, field.checked];
        if (field.matches('select[multiple]')) return [index, [...field.selectedOptions].map(option => option.value)];
        if (field.isContentEditable || field.getAttribute('contenteditable') === 'true') return [index, field.textContent];
        return [index, field.value];
      }));
    }

    function captureModalFormState() {
      const modal = document.getElementById('modal');
      if (!modal) return;
      const trackedFields = modal.querySelectorAll('.modal-body input, .modal-body textarea, .modal-body select, .modal-body [contenteditable="true"]');
      modal.dataset.formTracked = trackedFields.length ? 'true' : 'false';
      modal.dataset.initialFormState = serializeModalFormState();
    }

    function modalHasUnsavedChanges() {
      const modal = document.getElementById('modal');
      return modal?.dataset.formTracked === 'true'
        && modal.dataset.initialFormState !== serializeModalFormState();
    }

    function requestModalClose(returnToStudent = true) {
      if (modalHasUnsavedChanges() && !window.confirm('内容尚未保存，确定放弃吗？')) return false;
      closeModal(returnToStudent);
      return true;
    }

    function closeOpenRowMenu() {
      const menus = [...document.querySelectorAll('details.row-actions[open]')];
      if (!menus.length) return false;
      const focusedMenu = document.activeElement?.closest?.('details.row-actions[open]');
      const menuToRefocus = focusedMenu || menus[menus.length - 1];
      menus.forEach(menu => { menu.open = false; });
      menuToRefocus?.querySelector('summary')?.focus();
      return true;
    }

    function cancelActiveCellEdit(target) {
      const isEditable = target?.isContentEditable || target?.getAttribute?.('contenteditable') === 'true';
      if (!isEditable || !ESCAPE_EDITABLE_ACTS.has(target.dataset?.act)) return false;
      target.__cancelEditOnBlur = true;
      target.textContent = target.__escapeOriginalText ?? target.textContent;
      target.blur();
      return true;
    }

    function exitActiveBatchMode() {
      let focusSelector = '';
      if (curModule === 'stu' && batchMode) {
        batchMode = false;
        selectedStudentIds.clear();
        focusSelector = '[data-act="stu-batch-toggle"]';
      } else if (curModule === 'dictation' && dictationBatchMode) {
        dictationBatchMode = false;
        selectedDictationRounds.clear();
        focusSelector = '[data-act="dict-batch-toggle"]';
      } else if (curModule === 'recite' && reciteBatchMode) {
        reciteBatchMode = false;
        selectedReciteTasks.clear();
        focusSelector = '[data-act="recite-batch-toggle"]';
      } else if (curModule === 'writing' && writingBatchMode) {
        writingBatchMode = false;
        selectedWritingTasks.clear();
        focusSelector = '[data-act="writing-batch-toggle"]';
      } else if (curModule === 'homework' && homeworkBatchMode) {
        homeworkBatchMode = false;
        selectedHomeworkStudents.clear();
        focusSelector = '[data-act="hw-batch-toggle"]';
      }
      if (!focusSelector) return false;
      render();
      focusAfterRender(focusSelector);
      return true;
    }

    function clearFocusedSearch(target) {
      const act = target?.dataset?.act;
      let cleared = false;
      if (act === 'stu-search' && stuSearchText) { stuSearchText = ''; cleared = true; }
      else if (act === 'dict-search' && dictationSearchText) { dictationSearchText = ''; cleared = true; }
      else if (act === 'recite-search' && reciteSearchText) { reciteSearchText = ''; cleared = true; }
      else if (act === 'writing-search' && writingSearchText) { writingSearchText = ''; cleared = true; }
      else if (act === 'hw-search' && homeworkSearchText) { homeworkSearchText = ''; cleared = true; }
      if (!cleared) return false;
      render();
      focusAfterRender(`[data-act="${act}"]`);
      return true;
    }

    function handleEscape(event) {
      if (event.isComposing || event.keyCode === 229 || event.target?.__searchComposing) return false;

      const modal = document.getElementById('modal');
      if (modal?.classList.contains('show')) {
        consumeEscape(event);
        if (typeof window.tmHandleSettingsBack === 'function' && window.tmHandleSettingsBack()) return true;
        requestModalClose();
        return true;
      }
      // TeachMate 确认对话框
      var tmOverlay = document.querySelector('.tm-confirm-overlay');
      if (tmOverlay) {
        consumeEscape(event);
        tmOverlay.remove();
        return true;
      }
      // TeachMate 右栏抽屉
      var tmPanel = document.getElementById('tmRightPanel');
      if (tmPanel && (tmPanel.classList.contains('tm-drawer-open') || document.body.classList.contains('tm-drawer-veil'))) {
        consumeEscape(event);
        if (typeof tmCloseRightPanel === 'function') tmCloseRightPanel();
        else tmPanel.classList.remove('tm-drawer-open');
        return true;
      }
      if (closeOpenRowMenu()) {
        consumeEscape(event);
        return true;
      }
      if (cancelActiveCellEdit(event.target)) {
        consumeEscape(event);
        return true;
      }
      if (document.body.classList.contains('navopen')) {
        consumeEscape(event);
        closeDrawer();
        document.getElementById('menuBtn')?.focus();
        return true;
      }
      if (exitActiveBatchMode()) {
        consumeEscape(event);
        return true;
      }
      if (clearFocusedSearch(event.target)) {
        consumeEscape(event);
        return true;
      }
      if (curModule === 'risk') {
        consumeEscape(event);
        curModule = 'dash';
        renderNav();
        render();
        focusAfterRender('.nav-item[data-key="dash"]');
        return true;
      }
      return false;
    }

    // ================= 事件分发 =================
    function handleTermAction(act, target, id) {
      if (act === 'term-add') openTermModal();
      else if (act === 'term-manage') openTermManagerModal();
      else if (act === 'term-manage-switch') { closeModal(); switchTerm(id); }
      else if (act === 'term-manage-edit') openTermEditModal(id);
      else if (act === 'term-manage-archive') openTermArchiveModal(id);
      else if (act === 'term-manage-archive-confirm') archiveManagedTerm(id);
      else if (act === 'term-manage-delete-permanent') openTermPermanentDeleteModal(id);
      else if (act === 'term-manage-delete-permanent-confirm') permanentlyDeleteManagedTerm(id);
      else if (act === 'term-manage-restore') restoreManagedTerm(id);
      else if (act === 'term-manage-cache') openTermCacheModal(id);
      else if (act === 'term-manage-cache-confirm') clearManagedTermCache(id);
    }

    function initEvents() {
      document.addEventListener('click', e => {
        const t = e.target.closest('[data-act]');
        const act = t ? t.dataset.act : null;
        const id = t ? t.dataset.id : null;
        if (!act) return;
        if (act === 'nav') { visiblePhoneStudentIds.clear(); curModule = t.dataset.key; renderNav(); render(); closeDrawer(); }
        else if (act === 'tab-switch') { animateTabSwitch(t.dataset.tab); }
        else if (act.startsWith('tm-')) { handleTeachMateAction(act, t); }
        else if (act.startsWith('term-')) handleTermAction(act, t, id);
        else if (act === 'about-close') closeModal();
        else if (act === 'modal-close') closeModal();
        else if (act === 'subject-choose') openSubjectPicker();
        else if (act === 'subject-confirm') void confirmSubjectChoice();
        else if (act === 'stu-add') openStuModal();
        else if (act === 'stu-edit') openStuModal(id);
        else if (act === 'stu-transfer') openStudentTransferModal(id);
        else if (act === 'stu-archive') openArchiveStudentModal(id);
        else if (act === 'stu-detail') openStudentDetail(id);
        else if (act === 'risk-student-detail') openRiskStudentDetail(id, t.dataset.level);
        else if (act === 'tag-add') openTagCreateModal(id);
        else if (act === 'tag-batch-delete') openTagBatchDeleteModal(id);
        else if (act === 'tag-lock') toggleEvaluationTag(t.dataset.tagId, id);
        else if (act === 'tag-lock-inline') toggleEvaluationTagInline(t.dataset.tagId, id);
        else if (act === 'tag-delete-inline') deleteEvaluationTagInline(t.dataset.tagId, id);
        else if (act === 'tag-batch-confirm') confirmTagBatchDelete(id);
        else if (act === 'student-profile-retry') reloadStudentLearningProfile(id);
        else if (act === 'student-profile-confirm') confirmStudentProfileRevision(id);
        else if (act === 'student-profile-reject') rejectStudentProfileRevision(id);
        else if (act === 'student-profile-edit') openStudentProfileEditModal(id);
        else if (act === 'student-profile-save') saveStudentProfileEdit(id, t);
        else if (act === 'class-merge') mergeClassAlias();
        else if (act === 'class-unarchive') unarchiveClassAlias(id);
        else if (act === 'risk-expand') {
          riskDetailLevel = t.dataset.level === 'D' ? 'D' : 'C';
          riskCloudClass = riskCloudClass || dashboardClass || '';
          curModule = 'risk';
          renderNav();
          render();
        }
        else if (act === 'risk-detail-level') { riskDetailLevel = t.dataset.level === 'D' ? 'D' : 'C'; render(); }
        else if (act === 'risk-back') { curModule = 'dash'; renderNav(); render(); }
        else if (act === 'stu-export') void runLocalActionOnce('export-students', () => exportStudents(), t);
        else if (act === 'stu-search-clear' || act === 'dict-search-clear' || act === 'recite-search-clear' || act === 'writing-search-clear' || act === 'hw-search-clear') {
          const searchState = {
            'stu-search-clear': ['stu-search', 'stu'],
            'dict-search-clear': ['dict-search', 'dict'],
            'recite-search-clear': ['recite-search', 'recite'],
            'writing-search-clear': ['writing-search', 'writing'],
            'hw-search-clear': ['hw-search', 'hw']
          }[act];
          if (searchState[1] === 'stu') stuSearchText = '';
          else if (searchState[1] === 'dict') dictationSearchText = '';
          else if (searchState[1] === 'recite') reciteSearchText = '';
          else if (searchState[1] === 'writing') writingSearchText = '';
          else homeworkSearchText = '';
          render();
          setTimeout(() => document.querySelector(`[data-act="${searchState[0]}"]`)?.focus(), 0);
        }
        else if (act === 'stu-batch-toggle') { batchMode = !batchMode; if (!batchMode) selectedStudentIds.clear(); render(); }
        else if (act === 'stu-toggle-phone') {
          const studentId = String(id || '');
          if (visiblePhoneStudentIds.has(studentId)) visiblePhoneStudentIds.delete(studentId);
          else { visiblePhoneStudentIds.clear(); visiblePhoneStudentIds.add(studentId); }
          render();
          setTimeout(() => document.querySelector(`[data-act="stu-toggle-phone"][data-id="${CSS.escape(studentId)}"]`)?.focus(), 0);
        }
        else if (act === 'stu-batch-exit') { batchMode = false; selectedStudentIds.clear(); render(); }
        else if (act === 'stu-clear-selection') { selectedStudentIds.clear(); render(); }
        else if (act === 'stu-select-visible') { getStudentPageData().pageItems.forEach(s => selectedStudentIds.add(s.id)); render(); }
        else if (act === 'stu-select-all') {
          const visible = getStudentPageData().pageItems;
          const allSelected = visible.length > 0 && visible.every(s => selectedStudentIds.has(s.id));
          visible.forEach(s => allSelected ? selectedStudentIds.delete(s.id) : selectedStudentIds.add(s.id));
          render();
        }
        else if (act === 'stu-select') {
          if (selectedStudentIds.has(id)) selectedStudentIds.delete(id); else selectedStudentIds.add(id);
          render();
        }
        else if (act === 'stu-batch-archive') openBatchArchiveModal();
        else if (act === 'student-page-prev') { visiblePhoneStudentIds.clear(); studentPage = Math.max(1, studentPage - 1); render(); }
        else if (act === 'student-page-next') { visiblePhoneStudentIds.clear(); studentPage += 1; render(); }
        else if (act === 'delete-cancel') { closeModal(); setTimeout(() => batchDeleteButton?.focus(), 0); }
        else if (act === 'archive-confirm') {
          const count = archiveStudents([id]);
          closeModal(); render(); showToast(count ? `已归档 ${count} 名学生` : '归档失败', count ? 'success' : 'error');
        }
        else if (act === 'archive-confirm-batch') {
          if (batchDeletePending) return;
          batchDeletePending = true;
          t.disabled = true;
          const count = archiveStudents([...selectedStudentIds]);
          batchDeletePending = false;
          closeModal(); render(); showToast(count ? `已归档 ${count} 名学生` : '归档失败', count ? 'success' : 'error');
          setTimeout(() => batchDeleteButton?.focus(), 0);
        }
        else if (act === 'archived-manage') openArchivedStudentsModal();
        else if (act === 'archived-restore') { const restored = restoreArchivedStudent(id); closeModal(); renderNav(); render(); showToast(restored ? '学生已恢复' : '恢复失败', restored ? 'success' : 'error'); }
        else if (act === 'archived-purge') openPurgeArchivedStudentModal(id);
        else if (act === 'archived-purge-confirm') { const purged = purgeArchivedStudent(id); closeModal(); render(); showToast(purged ? '归档记录已永久清理' : '清理失败', purged ? 'success' : 'error'); }
        else if (act === 'archived-exams-manage') openArchivedExamsModal();
        else if (act === 'archived-exam-restore') restoreArchivedExam(id);
        else if (act === 'archived-exam-purge') openPurgeArchivedExamModal(id);
        else if (act === 'archived-exam-purge-confirm') purgeArchivedExam(id);
        else if (act === 'score-add-exam') openExamModal();
        else if (act === 'score-batch-paste') openBatchScorePasteModal();
        else if (act === 'score-item-upload') openItemScoreUploadModal();
        else if (act === 'score-paper-settings') openExamPaperSettingsModal();
        else if (act === 'score-save-pending') {
          savePendingScoreEdits().then(saved => { render(); showToast(saved ? '成绩更改已保存' : '保存失败', saved ? 'success' : 'error'); });
        }
        else if (act === 'score-undo') { const undone = undoPendingScoreEdit(); render(); showToast(undone ? '已撤销上一次成绩修改' : '没有可撤销的修改', undone ? 'success' : 'error'); }
        else if (act === 'score-edit-exam') openExamModal(currentExamId);
        else if (act === 'score-class-grade-rank') openClassGradeRankModal();
        else if (act === 'score-del-exam') openDeleteExamModal();
        else if (act === 'score-delete-cancel') closeModal();
        else if (act === 'score-delete-confirm') {
          if (t.disabled) return;
          t.disabled = true;
          deleteCurrentExam().then(deleted => {
            if (deleted) closeModal();
            showToast(deleted ? '考试已归档' : '归档失败', deleted ? 'success' : 'error');
          });
        }
        else if (act === 'score-class') { setGlobalClassFilter(t.dataset.cls); render(); }
        else if (act === 'score-exam') {
          const previous = currentExamId;
          handleMutationResult(
            commitMutation(() => { currentExamId = t.value; state.currentExamId = currentExamId; }),
            null,
            () => { currentExamId = previous; }
          );
        }
        else if (act === 'dict-class') { setGlobalClassFilter(t.dataset.cls); render(); }
        else if (act === 'dict-round-select') {
          if (dictationBatchMode) {
            const idx = Number(t.dataset.r) || 0;
            if (selectedDictationRounds.has(idx)) selectedDictationRounds.delete(idx); else selectedDictationRounds.add(idx);
            render();
          } else { dictationRoundIndex = Number(t.dataset.r) || 0; render(); }
        }
        else if (act === 'dict-add-round') {
          commitMutation(() => {
            if (!Array.isArray(state.dictationNames)) state.dictationNames = [];
            const n = state.dictationNames.length + 1;
            state.dictationNames.push('自定义' + n);
            if (!Array.isArray(state.dictationRanges)) state.dictationRanges = [];
            state.dictationRanges.push([]);
            state.students.forEach(s => { if (!state.dictation[s.id]) state.dictation[s.id] = []; state.dictation[s.id].push(''); });
          });
        }
        else if (act === 'dict-delete-round') openDeleteDictationRoundModal();
        else if (act === 'dict-round-stats') openDictationRangeModal(Number(t.dataset.r));
        else if (act === 'dict-batch-toggle') { dictationBatchMode = !dictationBatchMode; if (!dictationBatchMode) selectedDictationRounds.clear(); render(); }
        else if (act === 'dict-round-select-all') {
          const names = state.dictationNames || [];
          const allSelected = names.length > 0 && names.every((nm, i) => selectedDictationRounds.has(i));
          if (allSelected) selectedDictationRounds.clear(); else names.forEach((nm, i) => selectedDictationRounds.add(i));
          render();
        }
        else if (act === 'dict-round-delete-selected') openBatchDeleteDictationRoundsModal();
        else if (act === 'dict-round-delete-cancel') closeModal(false);
        else if (act === 'dict-round-delete-confirm') {
          Promise.resolve(removeDictationRounds([Number(document.getElementById('m-dict-round-delete')?.value)])).then(result => {
            if (result.count) closeModal(false);
            showToast(result.message, result.count ? 'success' : 'error');
          });
        }
        else if (act === 'dict-round-delete-confirm-batch') {
          Promise.resolve(removeDictationRounds([...selectedDictationRounds])).then(result => {
            if (result.count) closeModal(false);
            showToast(result.message, result.count ? 'success' : 'error');
          });
        }
        else if (act === 'recite-task-delete-cancel') closeModal(false);
        else if (act === 'recite-task-delete-confirm-batch') {
          Promise.resolve(removeReciteTasks([...selectedReciteTasks])).then(result => {
            if (result.count) closeModal(false);
            showToast(result.message, result.count ? 'success' : 'error');
          });
        }
        else if (act === 'writing-task-delete-cancel') closeModal(false);
        else if (act === 'writing-task-delete-confirm-batch') {
          Promise.resolve(removeWritingTasks([...selectedWritingTasks])).then(result => {
            if (result.count) closeModal(false);
            showToast(result.message, result.count ? 'success' : 'error');
          });
        }
        // ── 日常作业 ──
        else if (act === 'hw-add-task') openHomeworkAddModal();
        else if (act === 'hw-task-select') { homeworkActiveTaskId = id; render(); }
        else if (act === 'hw-toggle') {
          if (!homeworkActiveTaskId) return;
          const taskId = homeworkActiveTaskId;
          const currentVal = (state.homeworkRecords[taskId] || {})[id] === true;
          handleMutationResult(commitMutation(() => {
            if (!state.homeworkRecords[taskId]) state.homeworkRecords[taskId] = {};
            state.homeworkRecords[taskId][id] = !currentVal;
          }, { successMessage: currentVal ? '已标记为未交' : '已标记为已交' }));
        }
        else if (act === 'hw-batch-toggle') { homeworkBatchMode = !homeworkBatchMode; if (!homeworkBatchMode) selectedHomeworkStudents.clear(); render(); }
        else if (act === 'hw-batch-exit') { homeworkBatchMode = false; selectedHomeworkStudents.clear(); render(); }
        else if (act === 'hw-clear-selection') { selectedHomeworkStudents.clear(); render(); }
        else if (act === 'hw-select-visible') { getVisibleStudents(normalizeClassFilter(homeworkClass), homeworkSearchText).forEach(s => selectedHomeworkStudents.add(s.id)); render(); }
        else if (act === 'hw-select') {
          if (selectedHomeworkStudents.has(id)) selectedHomeworkStudents.delete(id); else selectedHomeworkStudents.add(id);
          render();
        }
        else if (act === 'hw-batch-mark-done') {
          if (!homeworkActiveTaskId || !selectedHomeworkStudents.size) return;
          const taskId = homeworkActiveTaskId;
          handleMutationResult(commitMutation(() => {
            if (!state.homeworkRecords[taskId]) state.homeworkRecords[taskId] = {};
            selectedHomeworkStudents.forEach(sid => { state.homeworkRecords[taskId][sid] = true; });
          }, { successMessage: `已将 ${selectedHomeworkStudents.size} 名学生标记为已交` }));
        }
        else if (act === 'hw-batch-mark-undone') {
          if (!homeworkActiveTaskId || !selectedHomeworkStudents.size) return;
          const taskId = homeworkActiveTaskId;
          handleMutationResult(commitMutation(() => {
            if (!state.homeworkRecords[taskId]) state.homeworkRecords[taskId] = {};
            selectedHomeworkStudents.forEach(sid => { state.homeworkRecords[taskId][sid] = false; });
          }, { successMessage: `已将 ${selectedHomeworkStudents.size} 名学生标记为未交` }));
        }
        else if (act === 'hw-detail') openHomeworkDetailModal(id);
        else if (act === 'hw-delete-task') openHomeworkDeleteModal();
        else if (act === 'hw-delete-cancel') closeModal(false);
        else if (act === 'hw-delete-confirm') {
          const selectEl = document.getElementById('m-hw-delete');
          const taskId = selectEl ? selectEl.value : homeworkActiveTaskId;
          handleMutationResult(commitMutation(() => {
            state.homeworkTasks = (state.homeworkTasks || []).filter(t => t.id !== taskId);
            delete state.homeworkRecords[taskId];
            if (homeworkActiveTaskId === taskId) homeworkActiveTaskId = '';
          }, { successMessage: '作业已删除' }));
          closeModal(false);
        }
        else if (act === 'hw-add-confirm') {
          const nameInput = document.getElementById('m-hw-name');
          const dateInput = document.getElementById('m-hw-date');
          const name = (nameInput?.value || '').trim();
          if (!name) return showToast('请输入作业名称', 'error');
          const hwDate = (dateInput?.value || '').trim();
          const taskId = 'hw_' + Date.now() + '_' + Math.random().toString(36).slice(2, 8);
          handleMutationResult(commitMutation(() => {
            if (!Array.isArray(state.homeworkTasks)) state.homeworkTasks = [];
            state.homeworkTasks.push({ id: taskId, name, date: hwDate, createdAt: new Date().toISOString() });
            if (!state.homeworkRecords) state.homeworkRecords = {};
            state.homeworkRecords[taskId] = {};
            homeworkActiveTaskId = taskId;
          }, { successMessage: '作业已创建' }));
          closeModal(false);
        }
        else if (act === 'hw-add-cancel') closeModal(false);
        else if (act === 'recite-add') openReciteModal();
        else if (act === 'recite-task-select') {
          if (reciteBatchMode) {
            if (selectedReciteTasks.has(id)) selectedReciteTasks.delete(id); else selectedReciteTasks.add(id);
            render();
          } else { reciteTaskId = id; render(); }
        }
        else if (act === 'recite-batch-toggle') { reciteBatchMode = !reciteBatchMode; if (!reciteBatchMode) selectedReciteTasks.clear(); render(); }
        else if (act === 'recite-task-select-all') {
          const tasks = state.recitations || [];
          const allSelected = tasks.length > 0 && tasks.every(task => selectedReciteTasks.has(task.id));
          if (allSelected) selectedReciteTasks.clear(); else tasks.forEach(task => selectedReciteTasks.add(task.id));
          render();
        }
        else if (act === 'recite-task-delete-selected') openBatchDeleteReciteTasksModal();
        else if (act === 'writing-add') openWritingModal();
        else if (act === 'writing-task-select') {
          if (writingBatchMode) {
            if (selectedWritingTasks.has(id)) selectedWritingTasks.delete(id); else selectedWritingTasks.add(id);
            render();
          } else { writingTaskId = id; render(); }
        }
        else if (act === 'writing-batch-toggle') { writingBatchMode = !writingBatchMode; if (!writingBatchMode) selectedWritingTasks.clear(); render(); }
        else if (act === 'writing-task-select-all') {
          const tasks = state.writings || [];
          const allSelected = tasks.length > 0 && tasks.every(task => selectedWritingTasks.has(task.id));
          if (allSelected) selectedWritingTasks.clear(); else tasks.forEach(task => selectedWritingTasks.add(task.id));
          render();
        }
        else if (act === 'writing-task-delete-selected') openBatchDeleteWritingTasksModal();
        else if (act === 'writing-edit-task') { writingTaskId = id; openWritingEditModal(id); }
        else if (act === 'error-add') openErrorModal();
        else if (act === 'error-del') { commitMutation(() => { state.errors = state.errors.filter(x=>x.id!==id); }); }
        else if (act === 'error-doc-select') { errorSelectedDocumentId = id; render(); }
        else if (act === 'error-doc-archive') {
          const documentItem = (state.paperDocuments || []).find(item => item.id === id);
          if (!documentItem) return showToast('未找到要归档的文件', 'error');
          commitMutation(() => {
            state.archivedDocuments = Array.isArray(state.archivedDocuments) ? state.archivedDocuments : [];
            state.archivedDocuments = state.archivedDocuments.filter(item => item.id !== id);
            state.archivedDocuments.push({ ...documentItem, archivedAt: new Date().toISOString() });
            state.paperDocuments = (state.paperDocuments || []).filter(item => item.id !== id);
            state.errors.forEach(item => { if (item.documentId === id) item.documentId = ''; });
            errorSelectedDocumentId = '';
          }, { successMessage: '资料已归档，可在教师设置 → 已归档文件中管理', renderNavigation: true });
        }
        else if (act === 'error-doc-download') {
          const documentItem = (state.paperDocuments || []).find(item => item.id === id);
          if (!documentItem?.attachmentId) return;
          attachmentObjectUrl(documentItem.attachmentId).then(url => {
            const link = document.createElement('a');
            link.href = url;
            link.download = documentItem.name || '原卷资料';
            link.click();
          }).catch(error => showToast(`原卷下载失败：${error.message || error}`, 'error'));
        }
        else if (act === 'error-doc-delete') {
          const documentItem = (state.paperDocuments || []).find(item => item.id === id);
          if (!documentItem) return showToast('未找到要删除的文件', 'error');
          openModal('确认删除原卷资料？', `<p>将直接删除「${escapeHtml(documentItem.name || '未命名文件')}」。</p><p class="danger-note">文件删除后不可恢复，关联的错题来源也会被清除。</p>`, `<button class="btn btn-text" data-act="error-doc-delete-cancel">取消</button><button class="btn btn-danger" data-act="error-doc-delete-confirm" data-id="${escapeAttr(id)}">确认删除</button>`);
        }
        else if (act === 'error-doc-delete-cancel') {
          closeModal();
        }
        else if (act === 'error-doc-delete-confirm') {
          const documentItem = (state.paperDocuments || []).find(item => item.id === id);
          if (!documentItem) { closeModal(); return showToast('未找到要删除的文件', 'error'); }
          const removeDocument = () => {
            state.paperDocuments = (state.paperDocuments || []).filter(item => item.id !== id);
            state.errors.forEach(item => { if (item.documentId === id) item.documentId = ''; });
            errorSelectedDocumentId = '';
          };
          const deleteResult = DATABASE_MODE && documentItem.attachmentId
            ? commitMutation(async () => {
              const response = await apiRequest(`/api/v1/attachments/${documentItem.attachmentId}/delete-with-workspace`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${workbenchToken}` },
                body: JSON.stringify({ term_id: currentTermId, expected_revision: databaseRevision, document_id: id, error_ids: state.errors.filter(item => item.documentId === id).map(item => item.id) })
              });
              databaseRevision = response.revision;
              state = response.state && typeof response.state === 'object' ? response.state : state;
              if (!response.state) removeDocument();
              errorSelectedDocumentId = '';
              return response;
            }, { successMessage: '原卷资料已删除', renderNavigation: true, skipSave: true })
            : commitMutation(removeDocument, { successMessage: '原卷资料已删除', renderNavigation: true });
          Promise.resolve(deleteResult).then(result => {
            if (result && result.ok !== false) closeModal();
          });
        }
        else if (act === 'todo-add') openTodoModal();
        else if (act === 'todo-edit') openTodoModal(id);
        else if (act === 'todo-view-mode') { todoViewMode = t.dataset.mode === 'week' ? 'week' : 'month'; try { localStorage.setItem('workbench_todo_view', todoViewMode); } catch (error) {} render(); }
        else if (act === 'todo-day') { todoSelectedDate = t.dataset.date; const selected = parseLocalDate(todoSelectedDate); if (selected) { todoCalendarDate = new Date(selected.getFullYear(), selected.getMonth(), 1); todoWeekDate = selected; } render(); }
        else if (act === 'todo-calendar-prev') { if (todoViewMode === 'week') { todoWeekDate = new Date(todoWeekDate); todoWeekDate.setDate(todoWeekDate.getDate() - 7); } else todoCalendarDate = new Date(todoCalendarDate.getFullYear(), todoCalendarDate.getMonth() - 1, 1); render(); }
        else if (act === 'todo-calendar-next') { if (todoViewMode === 'week') { todoWeekDate = new Date(todoWeekDate); todoWeekDate.setDate(todoWeekDate.getDate() + 7); } else todoCalendarDate = new Date(todoCalendarDate.getFullYear(), todoCalendarDate.getMonth() + 1, 1); render(); }
        else if (act === 'todo-calendar-today') { const today = new Date(); todoSelectedDate = formatLocalDate(today); todoCalendarDate = new Date(today.getFullYear(), today.getMonth(), 1); todoWeekDate = today; render(); }
        else if (act === 'todo-toggle' || act === 'todo-occurrence-toggle') { const item=state.todos.find(x=>x.id===id); if(item){ commitMutation(() => { if (act === 'todo-occurrence-toggle' && item.repeatRule) { item.completedDates = item.completedDates || {}; item.completedDates[t.dataset.date] = !item.completedDates[t.dataset.date]; } else item.done=!item.done; }); } }
        else if (act === 'todo-del') { commitMutation(() => { state.todos = state.todos.filter(x=>x.id!==id); }); }
        else if (act === 'todo-import') openTodoImportModal();
        else if (act === 'todo-import-add-row') addTodoImportRow();
        else if (act === 'todo-import-duplicate-row') duplicateTodoImportRow(t.closest('.todo-import-row'));
        else if (act === 'todo-import-remove-row') removeTodoImportRow(t.closest('.todo-import-row'));
        else if (act === 'todo-import-apply-text') applyTodoImportText();
        else if (act === 'todo-import-preview') previewTodoImport();
        else if (act === 'todo-import-confirm') confirmTodoImport();
        else if (act === 'record-view-mode') { recordViewModes[t.dataset.module] = t.dataset.mode === 'all' ? 'all' : 'single'; render(); }
        else if (act === 'settings-save') saveSettings();
        else if (act === 'paper-default-save') savePaperDistributionDefaultsUi();
        else if (act === 'moni-save-config') saveMoniConfigUi();
        else if (act === 'moni-open-tutorial') { if (typeof window.tmOpenTutorial === 'function') window.tmOpenTutorial('moni'); }
        else if (act === 'moni-test-config') testMoniConfigUi();
        else if (act === 'moni-sync-now') syncMoniNowUi();
        else if (act === 'growth-preset-save') saveGrowthPresetsUi();
        else if (act === 'growth-preset-reset') resetGrowthPresetsUi();
        else if (act === 'growth-teacher-add') addGrowthTeacherUi();
        else if (act === 'growth-teacher-remove') removeGrowthTeacherUi();
        else if (act === 'growth-teacher-remove-cancel') closeModal();
        else if (act === 'growth-teacher-remove-confirm') confirmRemoveGrowthTeacherUi();
        else if (act === 'school-source-new') openSchoolSourceEditor(null);
        else if (act === 'school-source-refresh') loadSchoolSourcesUi();
        else if (act === 'school-source-edit') openSchoolSourceEditor(t.dataset.key);
        else if (act === 'school-source-test') testSchoolSourceUi(t.dataset.key);
        else if (act === 'school-source-sync') syncSchoolSourceUi(t.dataset.key);
        else if (act === 'school-source-delete') deleteSchoolSourceUi(t.dataset.key);
        else if (act === 'school-source-delete-cancel') closeModal();
        else if (act === 'school-source-delete-confirm') confirmDeleteSchoolSourceUi(t.dataset.key);
        else if (act === 'school-source-save') saveSchoolSourceUi();
        else if (act === 'school-source-cancel') closeSchoolSourceEditor();
        else if (act === 'model-add') openModelProfileModal();
        else if (act === 'model-edit') openModelProfileModal(id);
        else if (act === 'model-activate') activateModelProfileUi(id);
        else if (act === 'model-delete') deleteModelProfileUi(id);
        else if (act === 'model-save') saveModelProfileUi();
        else if (act === 'model-test') testModelProfileUi();
        else if (act === 'provider-test') testProviderConnectionUi();
        else if (act === 'provider-switch') switchProviderUi();
        else if (act === 'export-json') exportJSON();
        else if (act === 'import-json') importJSON();
        else if (act === 'reset-data') resetData();
      });

      document.addEventListener('change', e => {
        if (e.target.id === 'sett-subject-key') {
          void applySubjectFromSettings(e.target.value);
        } else if (e.target.dataset.act === 'tm-chat-model' && typeof window.tmSwitchModel === 'function') {
          window.tmSwitchModel(e.target.value);
        } else if (e.target.id === 'model-profile-provider') {
          syncModelProfileProviderFields();
        } else if (e.target.id === 'termSelect') {
          switchTerm(e.target.value);
        } else if (e.target.dataset.act === 'growth-teacher-select') {
          selectGrowthTeacherUi(e.target.value);
        } else if (e.target.id === 'school-source-field-map' || e.target.id === 'school-source-path-exams') {
          renderSchoolSourceMissing();
          renderSchoolSourceAliasStatus();
        } else if (e.target.id === 'school-source-alias-map' || e.target.id === 'school-source-tier-aliases') {
          renderSchoolSourceAliasStatus();
        } else if (e.target.id === 'error-doc-file') {
          handleErrorDocumentUpload(e.target.files?.[0]);
          e.target.value = '';
        } else if (e.target.id === 'todo-import-file') {
          const file = e.target.files?.[0];
          if (file && !isAllowedTodoImportFile(file)) {
            e.target.value = '';
            showToast('日程导入仅支持 CSV 或 TXT 文件', 'error');
          } else if (file) {
            file.text().then(text => {
              const input = document.getElementById('m-todo-import-text');
              if (input) input.value = text;
              applyTodoImportText();
            }).catch(() => showToast('课表文件读取失败', 'error'));
          }
        } else if (e.target.closest?.('.todo-import-editor')) {
          previewTodoImport(true);
        } else if (e.target.dataset.act === 'risk-class') {
          setGlobalClassFilter(e.target.value);
          render();
        } else if (e.target.dataset.act === 'dashboard-class') {
          setGlobalClassFilter(e.target.value);
          render();
        } else if (e.target.dataset.act === 'stu-class') {
          visiblePhoneStudentIds.clear();
          setGlobalClassFilter(e.target.value);
          resetStudentViewState();
          render();
        } else if (e.target.dataset.act === 'student-page-size') {
          visiblePhoneStudentIds.clear();
          studentPageSize = Number(e.target.value) === 50 ? 50 : 30;
          studentPage = 1;
          render();
        } else if (e.target.dataset.act === 'recite-class') {
          setGlobalClassFilter(e.target.value);
          render();
        } else if (e.target.dataset.act === 'dict-class-select') {
          setGlobalClassFilter(e.target.value);
          render();
        } else if (e.target.dataset.act === 'hw-class-select') {
          setGlobalClassFilter(e.target.value);
          render();
        } else if (e.target.dataset.act === 'writing-class') {
          setGlobalClassFilter(e.target.value);
          render();
        } else if (e.target.dataset.act === 'dashboard-exam') {
          const exam = state.exams.find(item => item.id === e.target.value);
          if (!exam) return;
          commitMutation(() => { currentExamId = exam.id; state.currentExamId = exam.id; });
        } else if (e.target.dataset.act === 'score-exam') {
          commitMutation(() => { currentExamId = e.target.value; state.currentExamId = currentExamId; });
        } else if (e.target.dataset.act === 'tag-assign') {
          const student = state.students.find(item => item.id === e.target.dataset.id);
          if (!student) return;
          const tagId = e.target.dataset.tagId;
          commitMutation(() => {
            student.evaluationTags = Array.isArray(student.evaluationTags) ? student.evaluationTags : [];
            if (e.target.checked && !student.evaluationTags.includes(tagId)) student.evaluationTags.push(tagId);
            if (!e.target.checked) student.evaluationTags = student.evaluationTags.filter(item => item !== tagId);
          });
        } else if (e.target.dataset.act === 'tag-select') {
          const tagId = e.target.dataset.tagId;
          if (e.target.checked) selectedTagIds.add(tagId); else selectedTagIds.delete(tagId);
        } else if (e.target.dataset.act === 'tag-select-all') {
          const tags = state.studentTags || [];
          if (e.target.checked) tags.forEach(tag => selectedTagIds.add(tag.id));
          else selectedTagIds.clear();
          openTagBatchDeleteModal(e.target.dataset.id, false);
        } else if (e.target.dataset.act === 'tag-name-change') {
          const tag = (state.studentTags || []).find(item => item.id === e.target.dataset.tagId);
          const name = e.target.value.trim();
          if (!tag || tag.locked) return;
          if (!name) { e.target.value = tag.name; return showToast('标签名称不能为空', 'error'); }
          if ((state.studentTags || []).some(item => item.id !== tag.id && item.name === name)) { e.target.value = tag.name; return showToast('标签名称已存在', 'error'); }
          const previousName = tag.name;
          handleMutationResult(
            commitMutation(() => { tag.name = name; }, { successMessage: '标签名称已更新' }),
            null,
            () => { e.target.value = previousName; }
          );
        } else if (e.target.dataset.act === 'student-note') {
          const student = state.students.find(item => item.id === e.target.dataset.id);
          if (!student || student.evaluationNote === e.target.value) return;
          commitMutation(() => { student.evaluationNote = e.target.value; });
        } else if (e.target.dataset.act === 'dict-round-select') {
          const index = Number(e.target.dataset.r);
          if (e.target.checked) selectedDictationRounds.add(index); else selectedDictationRounds.delete(index);
          render();
        } else if (e.target.dataset.act === 'dict-round-select-toggle') {
          const index = Number(e.target.dataset.r);
          if (e.target.checked) selectedDictationRounds.add(index); else selectedDictationRounds.delete(index);
          render();
        } else if (e.target.dataset.act === 'recite-task-select-toggle') {
          const id = e.target.dataset.id;
          if (e.target.checked) selectedReciteTasks.add(id); else selectedReciteTasks.delete(id);
          render();
        } else if (e.target.dataset.act === 'writing-task-select-toggle') {
          const id = e.target.dataset.id;
          if (e.target.checked) selectedWritingTasks.add(id); else selectedWritingTasks.delete(id);
          render();
        } else if (e.target.dataset.act === 'recite-edit-status') {
          const task = state.recitations.find(item => item.id === e.target.dataset.id);
          if (!task) return;
          commitMutation(() => {
            task.status = task.status || {};
            const current = normalizeReciteStatus(task.status[e.target.dataset.sid]);
            const level = String(e.target.value || '').toUpperCase();
            task.status[e.target.dataset.sid] = { level, retake: level === 'F' ? current.retake : '' };
          });
        } else if (e.target.dataset.act === 'recite-edit-retake') {
          const task = state.recitations.find(item => item.id === e.target.dataset.id);
          if (!task) return;
          commitMutation(() => {
            task.status = task.status || {};
            task.status[e.target.dataset.sid] = { level: 'F', retake: e.target.value || '' };
          });
        }
      });
      const searchActs = new Set(['stu-search', 'dict-search', 'recite-search', 'writing-search', 'hw-search']);
      const updateSearchState = (target, restoreFocus = true) => {
        const a = target.dataset.act;
        const value = target.value;
        const selectionStart = target.selectionStart;
        if (a === 'stu-search') { visiblePhoneStudentIds.clear(); stuSearchText = value; studentPage = 1; }
        else if (a === 'dict-search') dictationSearchText = value;
        else if (a === 'recite-search') reciteSearchText = value;
        else if (a === 'writing-search') writingSearchText = value;
        else if (a === 'hw-search') homeworkSearchText = value;
        render();
        if (restoreFocus) {
          const input = document.querySelector(`[data-act="${a}"]`);
          if (input) { input.focus(); input.setSelectionRange(selectionStart, selectionStart); }
        }
      };
      document.addEventListener('compositionstart', e => {
        if (searchActs.has(e.target.dataset?.act)) e.target.__searchComposing = true;
      });
      document.addEventListener('compositionend', e => {
        if (!searchActs.has(e.target.dataset?.act)) return;
        e.target.__searchComposing = false;
        updateSearchState(e.target);
      });
      document.addEventListener('input', e => {
        const a = e.target.dataset.act;
        if (searchActs.has(a)) {
          // 中文输入法组合期间不要重绘输入框，否则会把拼音残留到最终文字中（如"胡huh"）。
          if (e.isComposing || e.target.__searchComposing) return;
          updateSearchState(e.target);
        } else if (e.target.dataset.act === 'student-note') {
          return;
        } else if (e.target.dataset.act === 'tm-session-search') {
          tmSearchSessions(e.target.value || '');
        } else if (e.target.dataset.act === 'tm-input') {
          // P1-E: 保存输入草稿，防止异步重绘丢失
          if (teachMateState) teachMateState.setDraft(e.target.value);
          // 输入区随内容增长，超过上限后只在 textarea 内滚动，不撑开聊天页面。
          e.target.style.height = 'auto';
          var inputMaxHeight = 180;
          var inputHeight = Math.min(e.target.scrollHeight || 0, inputMaxHeight);
          e.target.style.height = Math.max(40, inputHeight) + 'px';
          e.target.style.overflowY = (e.target.scrollHeight || 0) > inputMaxHeight ? 'auto' : 'hidden';
        } else if (e.target.closest?.('.todo-import-editor')) {
          previewTodoImport(true);
        }
      });
      document.addEventListener('focusin', e => {
        const isEditable = e.target.isContentEditable || e.target.getAttribute?.('contenteditable') === 'true';
        if (!isEditable || !ESCAPE_EDITABLE_ACTS.has(e.target.dataset?.act)) return;
        e.target.__escapeOriginalText = e.target.textContent;
      });
      document.addEventListener('keydown', e => {
        // TeachMate 输入框：Enter 发送，Shift+Enter 换行
        if (e.key === 'Enter' && !e.shiftKey && e.target.dataset?.act === 'tm-input') {
          e.preventDefault();
          tmSendMessage();
          return;
        }
        if ((e.key === 'Enter' || e.key === ' ') && e.target.matches?.('tr[data-act="stu-detail"]')) {
          e.preventDefault();
          openStudentDetail(e.target.dataset.id);
          return;
        }
        if ((e.key === 'Enter' || e.key === ' ') && e.target.matches?.('.risk-student-card[data-act="risk-student-detail"]')) {
          e.preventDefault();
          openRiskStudentDetail(e.target.dataset.id, e.target.dataset.level);
          return;
        }
        if (e.key === 'Enter' && e.target.dataset?.act === 'dict-round-stats') {
          openDictationRangeModal(Number(e.target.dataset.r));
          return;
        }
        if (e.key === 'Escape') handleEscape(e);
      });

      document.getElementById('menuBtn').onclick = () => document.body.classList.toggle('navopen');
      document.getElementById('sidebarToggle').onclick = toggleSidebar;
      window.addEventListener('resize', applySidebarState);
      applySidebarState();
      document.getElementById('overlay').onclick = closeDrawer;
      const headerClassSelect = document.getElementById('classSelect');
      if (headerClassSelect) headerClassSelect.onchange = () => {
        setGlobalClassFilter(headerClassSelect.value);
        render();
      };
      document.getElementById('workarea').addEventListener('blur', e => {
        if (e.target.__cancelEditOnBlur) {
          delete e.target.__cancelEditOnBlur;
          delete e.target.__escapeOriginalText;
          return;
        }
        delete e.target.__escapeOriginalText;
        if (e.target.dataset.act === 'score-edit') {
          const sid = e.target.dataset.sid;
          const raw = e.target.textContent.trim();
          const val = raw === '' || raw === '—' ? null : Number(raw);
          const exam = getCurrentExam();
          if (!exam) return showToast('请先创建考试', 'error');
          const fullScore = Number(exam.fullScore) || 100;
          if (val !== null && (!Number.isFinite(val) || val < 0 || val > fullScore)) {
            e.target.classList.add('score-invalid');
            e.target.setAttribute('aria-invalid', 'true');
            e.target.setAttribute('title', `请输入0-${fullScore}之间的数字，留空表示未录入`);
            return showToast(`成绩无效：请输入0-${fullScore}之间的数字`, 'error');
          }
          const student = state.students.find(item => item.id === sid);
          if (!student || !stageScoreEdit(exam, student, val)) return;
          render();
          showToast('成绩已暂存');
        } else if (e.target.dataset.act === 'dict-edit') {
          const sid = e.target.dataset.sid;
          const r = parseInt(e.target.dataset.r || '1', 10);
          const raw = e.target.textContent.trim();
          const val = raw === '' ? '' : (isNaN(parseFloat(raw)) ? raw : parseFloat(raw));
          handleMutationResult(commitMutation(() => {
            if (!state.dictation[sid]) state.dictation[sid] = [];
            while (state.dictation[sid].length < (state.dictationNames || []).length) state.dictation[sid].push('');
            state.dictation[sid][r - 1] = val;
          }, { successMessage: '默写成绩已保存' }));
        } else if (e.target.dataset.act === 'dict-name') {
          const r = parseInt(e.target.dataset.r, 10);
          if (!Array.isArray(state.dictationNames)) state.dictationNames = [];
          const val = e.target.textContent.trim() || ('自定义' + (r + 1));
          handleMutationResult(commitMutation(() => { state.dictationNames[r] = val; }, { successMessage: '轮次名称已更新' }));
        } else if (e.target.dataset.act === 'writing-edit-score') {
          const task = state.writings.find(item => item.id === e.target.dataset.id);
          const student = state.students.find(item => item.id === e.target.dataset.sid);
          if (!task || !student) return;
          const raw = e.target.textContent.trim();
          const value = raw === '' ? '' : Number(raw);
          if (value !== '' && (!Number.isFinite(value) || value < 0 || value > Number(task.fullScore || 12))) {
            render();
            return showToast(`成绩应在0-${task.fullScore || 12}分之间`, 'error');
          }
          task.scores = task.scores || {};
          handleMutationResult(commitMutation(() => { task.scores[student.id] = value; }, { successMessage: '写作成绩已保存' }));
        }
      }, true);
      document.getElementById('importBtn').onclick = () => { document.getElementById('fileInput').click(); };
      document.getElementById('fileInput').onchange = handleImport;
      document.getElementById('exportBtn').onclick = openExportCenter;
      document.getElementById('backupBtn').onclick = () => { curModule='settings'; renderNav(); render(); };
      const userMenuBtn = document.getElementById('userMenuBtn');
      if (userMenuBtn) userMenuBtn.onclick = openAboutDiagnostics;
      document.getElementById('modalClose').onclick = closeModal;
    }

    function closeDrawer() { document.body.classList.remove('navopen'); }

    function applySidebarState() {
      const isDesktop = window.innerWidth > 860;
      const collapsed = isDesktop && sidebarCollapsed;
      document.body.classList.toggle('sidebar-collapsed', collapsed);
      const button = document.getElementById('sidebarToggle');
      if (!button) return;
      const label = collapsed ? '展开左侧菜单' : '收起左侧菜单';
      button.setAttribute('aria-label', label);
      button.setAttribute('title', label);
      button.setAttribute('aria-expanded', String(!collapsed));
    }

    function toggleSidebar() {
      if (window.innerWidth <= 860) {
        document.body.classList.toggle('navopen');
        return;
      }
      sidebarCollapsed = !sidebarCollapsed;
      try { localStorage.setItem(SIDEBAR_PREF_KEY, sidebarCollapsed ? '1' : '0'); } catch (error) {}
      applySidebarState();
    }

    // ================= Tab 切换过渡 =================
    // B3-16: tab 切换时给 #workarea 添加淡入过渡动画，衔接更流畅。
    // 若用户系统要求减少动效（prefers-reduced-motion），则跳过动画。
    function animateTabEnter() {
      const wa = document.getElementById('workarea');
      if (!wa) return;
      if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
      wa.classList.remove('tm-view-enter');
      void wa.offsetWidth; // 强制 reflow 以重启动画
      wa.classList.add('tm-view-enter');
    }

    // B3-23: 滑块式 tab 切换。点击后先让滑块滑向目标按钮，动画结束再切换内容。
    // 这样两个 tab 之间的切换有"滑块滑动"的连贯感，而非原地变色。
    let _tabSwitchTimer = null;
    function animateTabSwitch(targetTab) {
      // Every click supersedes the pending transition, including a return to the current tab.
      if (_tabSwitchTimer) { clearTimeout(_tabSwitchTimer); _tabSwitchTimer = null; }
      const sw = document.querySelector('.nav-tab-switcher');
      if (targetTab === activeTab) {
        if (sw) sw.querySelectorAll('.tab-btn').forEach(b => b.classList.toggle('active', b.dataset.tab === activeTab));
        syncTabIndicator(false);
        return;
      }
      const btn = sw && sw.querySelector('.tab-btn[data-tab="' + targetTab + '"]');
      const ind = sw && sw.querySelector('.nav-tab-indicator');
      const reduced = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      if (!sw || !btn || !ind || reduced) { finishTabSwitch(targetTab); return; }
      // 先让滑块滑到目标按钮（GPU transform，丝滑）
      ind.style.transition = 'transform .32s cubic-bezier(.4, 0, .2, 1), width .32s cubic-bezier(.4, 0, .2, 1)';
      ind.style.width = btn.offsetWidth + 'px';
      ind.style.transform = 'translateX(' + btn.offsetLeft + 'px)';
      // 文字颜色同步过渡（先加 active 以触发 color transition）
      sw.querySelectorAll('.tab-btn').forEach(b => b.classList.toggle('active', b.dataset.tab === targetTab));
      _tabSwitchTimer = setTimeout(function () { _tabSwitchTimer = null; finishTabSwitch(targetTab); }, 330);
    }
    function finishTabSwitch(targetTab) {
      setActiveTab(targetTab);
      renderNav(); // 内部会 syncTabIndicator(false) 将滑块定位到最终位置
      render();
      if (targetTab === 'teachmate') initTeachMate();
      animateTabEnter();
    }

    // ================= Modal =================
    function openModal(title, body, footer='') {
      const modal = document.getElementById('modal');
      const content = modal?.querySelector('.modal-content');
      // TeachMate 设置及其弹出的二级弹层开启视图栈；普通弹层仍保持原有的
      // “更新当前弹层”语义，避免影响学期管理等已有流程。
      const preservePrevious = content?.classList.contains('tm-settings-modal-content')
        || modal?.dataset.tmModalStackEnabled === 'true';
      if (modal?.classList.contains('show') && preservePrevious) {
        const currentView = captureModalView();
        if (currentView) modalViewStack.push(currentView);
      }
      // 所有普通弹窗都从标准尺寸开始；学生明细打开后再单独加宽。
      content?.classList.remove('student-detail-modal', 'growth-detail-modal', 'todo-import-modal', 'todo-edit-modal', 'tm-settings-modal-content', 'tm-teacher-confirm-modal');
      document.getElementById('modalTitle').textContent = title;
      document.getElementById('modalBody').innerHTML = body;
      document.getElementById('modalFooter').innerHTML = footer;
      modal.classList.add('show');
      modal.dataset.tmModalStackEnabled = preservePrevious ? 'true' : 'false';
      captureModalFormState();
    }
    function openAboutDiagnostics() {
      const teacherName = String(state.teacher?.name || '').trim() || '未设置';
      const subject = String(state.teacher?.subject || '').trim() || '未设置';
      const termName = availableTerms.find(term => Number(term.id) === Number(currentTermId))?.name || (DATABASE_MODE ? '未识别' : '浏览器存储版');
      const className = normalizeClassFilter(dashboardClass);
      const storageName = DATABASE_MODE ? 'SQLite 本地数据库' : '浏览器本地存储';
      openModal('关于与诊断', `
        <div class="diagnostics-user"><span class="material-symbols-rounded" aria-hidden="true">account_circle</span><div><strong>${escapeHtml(teacherName)}</strong><span>${escapeHtml(subject)}</span></div></div>
        <dl class="diagnostics-list">
          <div><dt>当前学期</dt><dd>${escapeHtml(termName)}</dd></div>
          <div><dt>当前班级</dt><dd>${escapeHtml(className ? formatClassLabel(className) : '全部班级')}</dd></div>
          <div><dt>当前页面</dt><dd>${escapeHtml(getCurrentPageName())}</dd></div>
          <div><dt>应用版本</dt><dd>${escapeHtml(appRuntimeVersion)}</dd></div>
          <div><dt>数据存储</dt><dd>${escapeHtml(storageName)}</dd></div>
          <div><dt>数据库结构</dt><dd>${escapeHtml(appRuntimeSchema)}</dd></div>
        </dl>
      `, '<button class="btn btn-primary" data-act="about-close">关闭</button>');
    }
    function disposeStudentCharts() {
      const charts = Array.isArray(studentCharts) ? studentCharts.splice(0) : [];
      charts.forEach(chart => {
        try { chart.__workbenchResizeObserver?.disconnect(); } catch (error) {}
        try { chart.dispose(); } catch (error) {}
      });
      // 兼容图表初始化失败或热更新后未被数组记录的实例，避免旧画布遮住新弹窗。
      document.querySelectorAll('.student-history-chart').forEach(node => {
        try { if (typeof echarts !== 'undefined') echarts.dispose(node); } catch (error) {}
        node.innerHTML = '';
      });
      studentCharts = [];
    }
    function prepareModalForSubdialog() {
      disposeStudentCharts();
      modalViewStack.length = 0;
      const modal = document.getElementById('modal');
      modal?.classList.remove('show');
      modal?.querySelector('.modal-content')?.classList.remove('student-detail-modal');
    }
    function captureModalView() {
      const modal = document.getElementById('modal');
      const content = modal?.querySelector('.modal-content');
      if (!modal || !content) return null;
      return {
        title: document.getElementById('modalTitle')?.textContent || '',
        body: document.getElementById('modalBody')?.innerHTML || '',
        footer: document.getElementById('modalFooter')?.innerHTML || '',
        contentClassName: content.className,
        dataset: { ...modal.dataset },
      };
    }
    function restoreModalView(view) {
      if (!view) return false;
      const modal = document.getElementById('modal');
      const content = modal?.querySelector('.modal-content');
      if (!modal || !content) return false;
      document.getElementById('modalTitle').textContent = view.title;
      document.getElementById('modalBody').innerHTML = view.body;
      document.getElementById('modalFooter').innerHTML = view.footer;
      content.className = view.contentClassName;
      Object.keys(modal.dataset).forEach(key => delete modal.dataset[key]);
      Object.assign(modal.dataset, view.dataset || {});
      modal.classList.add('show');
      return true;
    }
    function closeModal(returnToStudent = true, options = {}) {
      const closeOptions = returnToStudent && typeof returnToStudent === 'object' ? returnToStudent : options;
      const shouldReturnToStudent = typeof returnToStudent === 'boolean' ? returnToStudent : true;
      const restorePrevious = closeOptions.restorePrevious !== false;
      if (restorePrevious && typeof window.tmHandleSettingsBack === 'function' && window.tmHandleSettingsBack()) return;
      if (restorePrevious && modalViewStack.length) {
        disposeStudentCharts();
        const previousView = modalViewStack.pop();
        restoreModalView(previousView);
        return;
      }
      disposeStudentCharts();
      document.querySelector('#modal .modal-content')?.classList.remove('student-detail-modal');
      const modal = document.getElementById('modal');
      modal.classList.remove('show');
      delete modal.dataset.formTracked;
      delete modal.dataset.initialFormState;
      modal.querySelector('.modal-content')?.classList.remove('tm-settings-modal-content');
      delete modal.dataset.tmModalStackEnabled;
      modalViewStack.length = 0;
      const studentId = shouldReturnToStudent ? modalReturnStudentId : '';
      modalReturnStudentId = '';
      if (studentId) openStudentDetail(studentId);
    }

    function openArchiveStudentModal(id) {
      const student = state.students.find(s => s.id === id);
      if (!student) return;
      openModal('归档学生', `<p>将学生「${escapeHtml(student.name)}」（${escapeHtml(student.id)}）移出当前名单？</p><p style="color:var(--md-text-secondary);">考试、默写、背诵和写作记录都会保留，可在“班级与设置 → 数据维护”中恢复。</p>`, `<button class="btn btn-text" data-act="delete-cancel">取消</button><button class="btn btn-primary" data-act="archive-confirm" data-id="${escapeAttr(id)}">确认归档</button>`);
    }

    function openBatchArchiveModal() {
      const ids = [...selectedStudentIds].filter(id => state.students.some(s => s.id === id));
      if (!ids.length) return;
      batchDeleteButton = document.querySelector('[data-act="stu-batch-archive"]');
      openModal('批量归档学生', `<p>将所选的 ${ids.length} 名学生移出当前名单？</p><p style="color:var(--md-text-secondary);">相关教学和成绩记录会继续保留，可在数据维护中恢复。</p>`, `<button class="btn btn-text" data-act="delete-cancel">取消</button><button class="btn btn-primary" data-act="archive-confirm-batch">确认归档</button>`);
    }

    function openStudentTransferModal(id) {
      const student = state.students.find(item => item.id === id);
      if (!student) return;
      const options = state.classes.map(cls => `<option value="${escapeAttr(cls)}" ${cls === student.class ? 'selected' : ''}>${escapeHtml(formatClassLabel(cls))}</option>`).join('');
      openModal('学生转班', `<div class="form-group"><label>${escapeHtml(student.name)} · 目标班级</label><select id="m-transfer-class">${options}</select></div>`, '<button class="btn btn-text" onclick="closeModal()">取消</button><button class="btn btn-primary" id="m-transfer-save">确认转班</button>');
      document.getElementById('m-transfer-save').onclick = async () => {
        const targetClass = document.getElementById('m-transfer-class').value;
        if (!targetClass || targetClass === student.class) return showToast('请选择不同的目标班级', 'error');
        const result = await commitMutation(() => { student.class = targetClass; }, { successMessage: `已将${student.name}转入${formatClassLabel(targetClass)}` });
        if (result.ok) closeModal();
      };
    }

    function openArchivedStudentsModal() {
      const archived = [...(state.archivedStudents || [])].sort((a, b) => String(b.archivedAt || '').localeCompare(String(a.archivedAt || '')));
      const rows = archived.length ? archived.map(student => `<tr><td class="numeric">${escapeHtml(student.id)}</td><td>${escapeHtml(student.name)}</td><td>${escapeHtml(formatClassLabel(student.class))}</td><td>${escapeHtml(String(student.archivedAt || '').slice(0, 10) || '—')}</td><td class="text-right"><button class="btn btn-sm" data-act="archived-restore" data-id="${escapeAttr(student.id)}">恢复</button><button class="btn btn-sm btn-text danger-text" data-act="archived-purge" data-id="${escapeAttr(student.id)}">永久清理</button></td></tr>`).join('') : '<tr><td colspan="5" class="empty">暂无已归档学生</td></tr>';
      openModal('已归档学生', `<p style="color:var(--md-text-secondary);">归档不会删除历史成绩。永久清理会移除该学生及其关联记录，请仅在确认数据不再需要时使用。</p><div class="archived-table-wrap"><table><thead><tr><th>学号</th><th>姓名</th><th>原班级</th><th>归档日期</th><th class="text-right">操作</th></tr></thead><tbody>${rows}</tbody></table></div>`, '<button class="btn btn-primary" data-act="about-close">关闭</button>');
    }

    function openPurgeArchivedStudentModal(id) {
      const student = (state.archivedStudents || []).find(item => item.id === id);
      if (!student) return;
      openModal('永久清理归档记录？', `<p>即将永久清理「${escapeHtml(student.name)}」（${escapeHtml(student.id)}）及其关联成绩和教学记录。</p><p class="danger-note">此操作无法通过工作台恢复。</p>`, `<button class="btn btn-text" data-act="delete-cancel">取消</button><button class="btn btn-danger" data-act="archived-purge-confirm" data-id="${escapeAttr(id)}">永久清理</button>`);
    }

    function openDeleteExamModal() {
      const exam = getCurrentExam();
      if (!exam) return showToast('当前没有可归档的考试', 'error');
      openModal('归档考试？', `<p>将考试「${escapeHtml(exam.name)}」移出当前列表？</p><p style="color:var(--md-text-secondary);">历史成绩会保留，可在“班级与设置 → 数据维护”中恢复。</p>`, `<button class="btn" data-act="score-delete-cancel">取消</button><button class="btn btn-primary" data-act="score-delete-confirm">确认归档</button>`);
    }

    function openBatchScorePasteModal() {
      const exam = getCurrentExam();
      if (!exam) return showToast('请先创建考试', 'error');
        const classLabel = scoreClass ? formatClassLabel(scoreClass) : '全部班级';
      openModal('批量粘贴成绩', `<div class="form-group"><label>${escapeHtml(classLabel)} · 满分 ${fmt(exam.fullScore || 100)} · 学号/姓名 + 成绩</label><textarea id="m-score-paste" rows="10" placeholder="20260101\t86.5\n张同学\t92"></textarea></div><div id="batch-paste-errors" class="batch-paste-errors" role="alert"></div>`, '<button class="btn btn-text" onclick="closeModal()">取消</button><button class="btn btn-primary" id="m-score-paste-stage">校验并暂存</button>');
      document.getElementById('m-score-paste-stage').onclick = () => {
        const lines = document.getElementById('m-score-paste').value.split(/\r?\n/).map(line => line.trim()).filter(Boolean);
        const students = getFilteredStudents(scoreClass);
        const fullScore = Number(exam.fullScore) || 100;
        const staged = [];
        const errors = [];
        lines.forEach((line, index) => {
          const columns = line.split(/\t|,|，/).map(value => value.trim()).filter(value => value !== '');
          if (columns.length < 2) {
            const whitespaceColumns = line.split(/\s+/).filter(Boolean);
            columns.splice(0, columns.length, ...whitespaceColumns);
          }
          const identity = columns[0] || '';
          const scoreRaw = columns[1] || '';
          const student = students.find(item => item.id === identity) || students.find(item => item.name === identity);
          const score = Number(scoreRaw);
          if (!student) errors.push(`第${index + 1}行：未找到“${identity}”`);
          else if (!Number.isFinite(score) || score < 0 || score > fullScore) errors.push(`第${index + 1}行：成绩“${scoreRaw}”应在0-${fullScore}之间`);
          else staged.push({ student, score });
        });
        const errorBox = document.getElementById('batch-paste-errors');
        if (!lines.length) {
          errorBox.innerHTML = '<p>请先粘贴成绩数据。</p>';
          return;
        }
        if (errors.length) {
          errorBox.innerHTML = `<strong>发现 ${errors.length} 个问题，尚未暂存：</strong><ul>${errors.slice(0, 12).map(error => `<li>${escapeHtml(error)}</li>`).join('')}</ul>${errors.length > 12 ? `<p>另有 ${errors.length - 12} 个问题未显示。</p>` : ''}`;
          return;
        }
        let changed = 0;
        staged.forEach(item => { if (stageScoreEdit(exam, item.student, item.score)) changed += 1; });
        closeModal();
        render();
        showToast(`已暂存 ${changed} 项成绩，请检查后保存`);
      };
    }

    // ================= 默认试卷分布与单场考试设置 =================
    function paperDistributionRanges(text) {
      const groups = new Map();
      let unsupported = false;
      String(text || '').split(/[,，、;；\s]+/).filter(Boolean).forEach(part => {
        const grouped = part.match(/^(\d+)-(\d+)[~～至–—](\d+)-(\d+)$/);
        const flat = part.match(/^(\d+)[~～至–—-](\d+)$/);
        const one = part.match(/^\d+$/);
        let prefix = '', start, end;
        if (grouped && grouped[1] === grouped[3]) {
          prefix = grouped[1]; start = Number(grouped[2]); end = Number(grouped[4]);
        } else if (flat) { start = Number(flat[1]); end = Number(flat[2]); }
        else if (one) { start = end = Number(part); }
        else { unsupported = true; return; }
        if (start < 1 || end < start || end > 1000) { unsupported = true; return; }
        if (!groups.has(prefix)) groups.set(prefix, new Set());
        for (let n = start; n <= end; n++) groups.get(prefix).add(n);
      });
      const ranges = [];
      for (const [prefix, values] of groups) {
        for (const n of [...values].sort((a, b) => a - b)) {
          const last = ranges[ranges.length - 1];
          if (last && last.prefix === prefix && last.end + 1 === n) last.end = n;
          else ranges.push({ prefix, start: n, end: n });
        }
      }
      return { ranges: ranges.length ? ranges : [{ prefix: '', start: '', end: '' }], unsupported };
    }

    function paperDistributionRowsHtml(rows) {
      return rows.map(row => {
        const parsed = paperDistributionRanges(row.question_numbers);
        const kind = escapeAttr(row.question_type);
        const fields = parsed.ranges.map(range => `<div class="paper-distribution-range" data-range-prefix="${escapeAttr(range.prefix)}">${range.prefix ? `<small>第${escapeHtml(range.prefix)}大题</small>` : ''}<span>第</span><input type="number" min="1" max="1000" step="1" data-range-start aria-label="${kind}起始题号" value="${range.start}" placeholder="起始"><span>题，到第</span><input type="number" min="1" max="1000" step="1" data-range-end aria-label="${kind}结束题号" value="${range.end}" placeholder="结束"><span>题</span></div>`).join('');
        return `<div class="paper-distribution-row" data-paper-type="${kind}" data-range-unsupported="${parsed.unsupported}"><span>${escapeHtml(row.question_type)}</span><div>${fields}${parsed.unsupported ? `<small class="paper-distribution-warning">无法识别题号：${escapeHtml(row.question_numbers)}。请重新设置范围。</small>` : ''}</div></div>`;
      }).join('');
    }

    function paperDistributionPayload(container, metadata) {
      return {
        subject_key: metadata.subject_key,
        expected_revision: metadata.revision,
        expected_paper_version_id: metadata.paper_version_id || null,
        rows: [...container.querySelectorAll('[data-paper-type]')].map(row => {
          const kind = row.dataset.paperType;
          const ranges = [...row.querySelectorAll('.paper-distribution-range')].map(range => {
            const startText = range.querySelector('[data-range-start]').value.trim();
            const endText = range.querySelector('[data-range-end]').value.trim();
            if (!startText && !endText) return '';
            if (!startText || !endText) throw new Error(`${kind}请填写起止两个题号；没有该题型时都留空`);
            const start = Number(startText), end = Number(endText);
            if (!Number.isInteger(start) || !Number.isInteger(end) || start < 1 || end < start || end > 1000) throw new Error(`${kind}的起始题号不能大于结束题号，请填1到1000的整数`);
            const prefix = range.dataset.rangePrefix;
            return prefix ? `${prefix}-${start}～${prefix}-${end}` : `${start}～${end}`;
          }).filter(Boolean);
          if (!ranges.length && row.dataset.rangeUnsupported === 'true') throw new Error(`${kind}请重新设置范围`);
          return { question_type: kind, question_numbers: ranges.join('、') };
        }),
      };
    }

    async function loadPaperDistributionDefaultsUi() {
      const rows = document.getElementById('paper-default-rows');
      if (!rows || !DATABASE_MODE) return;
      const button = document.querySelector('[data-act="paper-default-save"]');
      const status = document.getElementById('paper-default-status');
      try {
        const data = await apiRequest('/api/v1/exams/paper-distribution/default');
        if (!rows.isConnected) return;
        rows.innerHTML = paperDistributionRowsHtml(data.rows);
        rows._paperDistribution = data;
        button.disabled = false;
        status.textContent = data.revision ? '' : '未设置';
      } catch (error) {
        if (rows.isConnected) { rows.textContent = '试卷设置读取失败'; status.textContent = error.message || '请重新进入设置页'; }
      }
    }

    async function savePaperDistributionDefaultsUi() {
      const rows = document.getElementById('paper-default-rows');
      const metadata = rows?._paperDistribution;
      const button = document.querySelector('[data-act="paper-default-save"]');
      const status = document.getElementById('paper-default-status');
      if (!metadata || button.disabled) return;
      button.disabled = true;
      status.textContent = '正在保存…';
      try {
        const data = await apiRequest('/api/v1/exams/paper-distribution/default', {
          method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(paperDistributionPayload(rows, metadata)),
        });
        if (!rows.isConnected) return;
        rows._paperDistribution = data;
        status.textContent = '已保存';
        showToast('试卷设置已保存');
      } catch (error) {
        if (rows.isConnected) status.textContent = error.message || '保存失败，请重试';
      } finally { if (button.isConnected) button.disabled = false; }
    }

    async function openExamPaperSettingsModal() {
      const exam = getCurrentExam();
      if (!exam || !DATABASE_MODE) return showToast('请先选择考试', 'error');
      const termId = currentTermId;
      openModal('考试设置', `<p><strong>${escapeHtml(exam.name)}</strong></p><div id="exam-paper-rows" class="paper-distribution-rows">正在读取考试设置…</div><div id="exam-paper-status" class="paper-distribution-status" role="status"></div>`, '<button class="btn btn-text" onclick="closeModal()">取消</button><button class="btn btn-secondary" id="exam-paper-use-default" disabled>使用默认分布</button><button class="btn btn-primary" id="exam-paper-save" disabled>保存考试设置</button>');
      const rows = document.getElementById('exam-paper-rows');
      const status = document.getElementById('exam-paper-status');
      const save = document.getElementById('exam-paper-save');
      const useDefault = document.getElementById('exam-paper-use-default');
      try {
        const examId = await resolveItemScoreExamId(exam);
        if (!rows.isConnected) return;
        if (!examId) throw new Error('没有找到这场考试的数据库记录');
        const url = `/api/v1/exams/${examId}/paper-distribution?term_id=${encodeURIComponent(termId)}`;
        const data = await apiRequest(url);
        if (!rows.isConnected) return;
        rows.innerHTML = paperDistributionRowsHtml(data.rows);
        status.textContent = data.unassigned_questions?.length
          ? `未分配题型：${data.unassigned_questions.join('、')}`
          : data.source === 'default' ? '使用默认分布' : '';
        save.disabled = false;
        useDefault.disabled = !data.default_rows.some(row => row.question_numbers);
        useDefault.onclick = () => {
          rows.innerHTML = paperDistributionRowsHtml(data.default_rows);
          status.textContent = '已填入默认分布，待保存';
        };
        save.onclick = async () => {
          if (save.disabled) return;
          save.disabled = true;
          useDefault.disabled = true;
          status.textContent = '正在保存…';
          try {
            await apiRequest(url, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(paperDistributionPayload(rows, data)) });
            if (!rows.isConnected) return;
            closeModal();
            showToast('考试设置已保存');
          } catch (error) {
            if (rows.isConnected) status.textContent = error.message || '保存失败，请重试';
          } finally {
            if (save.isConnected) { save.disabled = false; useDefault.disabled = !data.default_rows.some(row => row.question_numbers); }
          }
        };
      } catch (error) {
        if (rows.isConnected) { rows.textContent = '考试设置读取失败'; status.textContent = error.message || '请重试'; }
      }
    }

    // ================= 逐题小分上传（按题型） =================
    // 前端 state.exams[].id 是后端 Exam.source_key，而小分接口要的是数字 exam_id，
    // 所以上传前先做一次映射；映射不到就不发请求，避免把错数据写到别的考试上。
    async function resolveItemScoreExamId(exam) {
      if (!DATABASE_MODE || !exam) return null;
      try {
        return await teachMateApi.findExamIdByKey(exam.id, currentTermId);
      } catch (error) {
        return null;
      }
    }

    const ITEM_SCORE_HEADER_RE = /^(题型|板块|题目类型|question_?type|section|题号|question_?no)$/i;
    const ITEM_SCORE_LINE_HELP = '每行一条：题号 · 学号或姓名 · 得分；也兼容题型 · 题号 · 学号 · 得分（有小题号时为 5 列）';

    function matchItemScoreStudent(students, identity) {
      const key = String(identity == null ? '' : identity).trim();
      if (!key) return { error: '学号不能为空' };
      const byNumber = students.filter(student => String(student.id) === key);
      if (byNumber.length === 1) return { student: byNumber[0] };
      // 学号认不出来时才回退到姓名；同名歧义直接报错，不替教师选人。
      const byName = students.filter(student => String(student.name) === key);
      if (byName.length === 1) return { student: byName[0] };
      if (byName.length > 1) return { error: `名单里有 ${byName.length} 名学生都叫“${key}”，请改写学号` };
      return { error: `名单里没有学号“${key}”对应的学生` };
    }

    /**
     * 解析小分粘贴文本。返回 { rows:[{row, name}], errors:[string] }，不抛异常——
     * 教师的输入问题要逐行列出来，而不是让整个界面报错。
     */
    function parseItemScorePaste(text, students) {
      const rows = [];
      const errors = [];
      const seen = new Set();
      String(text || '').split(/\r?\n/).forEach((rawLine, index) => {
        const line = rawLine.trim();
        if (!line) return;
        const lineNo = index + 1;
        // 有制表符/逗号就按它们切并保留空列（教师可用空列表示「这题没有小题号」）；
        // 只写了空格的行才退回按空白切。
        const cells = (line.indexOf('\t') >= 0 || /[,，]/.test(line) ? line.split(/\t|,|，/) : line.split(/\s+/))
          .map(value => String(value == null ? '' : value).trim());
        if (!rows.length && !errors.length && ITEM_SCORE_HEADER_RE.test(cells[0] || '')) return;
        const simple = cells.length === 3 && /^\d/.test(cells[0]);
        if ((!simple && cells.length < 4) || cells.length > 5) {
          errors.push(`第${lineNo}行：${ITEM_SCORE_LINE_HELP}`);
          return;
        }
        const five = cells.length === 5;
        const questionType = simple ? '' : cells[0];
        const questionNo = simple ? cells[0] : cells[1];
        const subNo = five ? cells[2] : '';
        const identity = simple ? cells[1] : five ? cells[3] : cells[2];
        const scoreRaw = simple ? cells[2] : five ? cells[4] : cells[3];
        if ((!simple && !questionType) || !questionNo) { errors.push(`第${lineNo}行：题型和题号都不能为空`); return; }
        const matched = matchItemScoreStudent(students, identity);
        if (matched.error) { errors.push(`第${lineNo}行：${matched.error}`); return; }
        const score = Number(scoreRaw);
        if (scoreRaw === '' || !Number.isFinite(score) || score < 0) {
          errors.push(`第${lineNo}行：得分“${scoreRaw}”要填 0 或正数`);
          return;
        }
        const row = { student_no: String(matched.student.id), question_no: questionNo, score };
        if (questionType) row.question_type = questionType;
        if (subNo) row.sub_question_no = subNo;
        const key = [row.student_no, questionType, questionNo, subNo].join('\u0000');
        if (seen.has(key)) {
          errors.push(`第${lineNo}行：${matched.student.name} 的「${questionType} ${questionNo}${subNo ? '-' + subNo : ''}」在一批里重复提交`);
          return;
        }
        seen.add(key);
        rows.push({ row: row, name: matched.student.name });
      });
      return { rows: rows, errors: errors };
    }

    function renderItemScoreProblems(detail, errorBox) {
      const problems = detail && Array.isArray(detail.problems) ? detail.problems : [];
      if (!problems.length) {
        errorBox.innerHTML = `<strong>上传未完成：</strong><p>${escapeHtml((detail && detail.message) || '接口拒绝了这批小分')}</p>`;
        return;
      }
      const lines = problems.map(problem => {
        const where = [problem.question_type, problem.question_no].filter(Boolean).join(' ');
        const who = problem.student_no || '（未填学号）';
        const lineNo = Number.isInteger(problem.index) ? `第${problem.index + 1}行 ` : '';
        return `${lineNo}${who} · ${where || '（未填题型）'}：${problem.message || problem.reason || '无法写入'}`;
      });
      errorBox.innerHTML = `<strong>${escapeHtml((detail && detail.message) || `${problems.length} 条小分无法写入`)}</strong>`
        + `<ul>${lines.slice(0, 20).map(line => `<li>${escapeHtml(line)}</li>`).join('')}</ul>`
        + (lines.length > 20 ? `<p>另有 ${lines.length - 20} 条问题未显示。</p>` : '');
    }

    function renderItemScoreResult(result, errorBox, exam) {
      const sections = Array.isArray(result.sections) ? result.sections : [];
      const sectionRows = sections.map(item => {
        const average = item.average_score == null ? '样本较少' : fmt(item.average_score);
        return `<tr><td>${escapeHtml(item.section_name)}</td><td class="numeric">${item.max_score > 0 ? fmt(item.max_score) : '未提供'}</td>`
          + `<td class="numeric">${item.scored_items}/${item.expected_items}</td>`
          + `<td class="numeric">${average}</td><td class="numeric">${item.complete_student_count}</td></tr>`;
      }).join('');
      const skipped = Array.isArray(result.skipped) ? result.skipped : [];
      const skippedBlock = skipped.length
        ? `<p class="item-score-result-note">另有 ${skipped.length} 条被跳过（教师已修正或上次上传过的小分默认保留）：</p>`
          + `<ul>${skipped.slice(0, 10).map(item => {
            const message = item.reason === 'previous_upload'
              ? '与上次上传相同，未重复写入'
              : item.reason === 'score_changed'
                ? `本次为 ${fmt(item.incoming_score)} 分，已有 ${fmt(item.existing_score)} 分，默认保留已有成绩`
                : '小分已被单独修正，未覆盖';
            return `<li>${escapeHtml(`${item.student_no} · ${item.question_type} ${item.question_no}：${message}`)}</li>`;
          }).join('')}</ul>`
        : '';
      const missingBlock = result.total_score_missing_count
        ? `<p class="item-score-result-note">其中 ${result.total_score_missing_count} 名学生这场考试还没有总分，${escapeHtml(subjectText('score_total'))}与题型合计暂不一致；小分已按原样保存，不用重传。</p>`
        : '';
      errorBox.className = 'item-score-result';
      errorBox.innerHTML = `<strong>已写入 ${result.written} 条小分，覆盖 ${result.student_count} 名学生（试卷版本 ${result.paper_version}）</strong>`
        + (sectionRows
          ? `<table class="item-score-result-table"><thead><tr><th>题型</th><th class="numeric">满分</th><th class="numeric">已录/应有</th><th class="numeric">平均分</th><th class="numeric">完整人数</th></tr></thead><tbody>${sectionRows}</tbody></table>`
          : '')
        + missingBlock + skippedBlock
        + `<p class="item-score-result-note">总分未被本次上传改写；${escapeHtml(subjectText('score_total'))}仍以「${escapeHtml(exam.name)}」成绩表里的值为准。</p>`;
    }

    function openItemScoreUploadModal() {
      const exam = getCurrentExam();
      if (!exam) return showToast('请先创建考试', 'error');
      if (!DATABASE_MODE) return showToast('上传小分需要在连接数据库的版本里使用', 'error');
      const classLabel = scoreClass ? formatClassLabel(scoreClass) : '全部班级';
      const roster = Array.isArray(state.students) ? state.students : [];
      openModal('上传逐题小分', `
        <p class="hint" style="margin-bottom:12px;">写入 ${escapeHtml(exam.name)} 的逐题小分（${escapeHtml(classLabel)}）。只需题号、学号或姓名、得分，程序按试卷设置匹配题型。请先在“考试设置”检查分布；没有单独结构时会使用默认分布。</p>
        <div class="form-group">
          <label>${escapeHtml(ITEM_SCORE_LINE_HELP)}</label>
          <textarea id="m-item-score-paste" rows="10" placeholder="题号\t学号\t得分\n1\t01\t2\n2\t01\t1.5\n3\t张三\t0"></textarea>
        </div>
        <details class="item-score-skeleton">
          <summary>查看这场考试可用的题型 / 题号 / 满分</summary>
          <div id="item-score-skeleton-body" class="item-score-skeleton-body">正在读取试卷结构…</div>
        </details>
        <label class="item-score-overwrite"><input type="checkbox" id="m-item-score-overwrite"> 覆盖我已经上传过或单独改过的小分</label>
        <div id="item-score-errors" class="batch-paste-errors" role="alert"></div>
      `, '<button class="btn btn-text" onclick="closeModal()">取消</button><button class="btn btn-primary" id="m-item-score-submit">校验并写入</button>');

      const errorBox = document.getElementById('item-score-errors');
      const submit = document.getElementById('m-item-score-submit');
      // exam_id 映射是异步的，但按钮必须立刻可用：先存下 Promise，点击时再 await。
      const examIdPromise = resolveItemScoreExamId(exam);

      // 试卷骨架：只做提示，读不到也不阻塞上传（真正的判定在后端）。
      const skeleton = document.getElementById('item-score-skeleton-body');
      if (skeleton) {
        examIdPromise.then(examId => {
          if (!document.body.contains(skeleton)) return;
          if (examId == null) {
            skeleton.textContent = '没找到这场考试在后端对应的考试记录，暂时无法列出题型。';
            return;
          }
          teachMateApi.getQuestionMetrics(examId, currentTermId).then(payload => {
            const questions = (payload && Array.isArray(payload.questions)) ? payload.questions : [];
            if (!questions.length) {
              skeleton.innerHTML = '<p>这场考试还没有已确认的试卷结构。请在成绩管理的“考试设置”填写题号分布，或先在“班级与设置 → 试卷设置”保存默认分布，首次上传时会自动应用。</p>';
              return;
            }
            const rows = questions.map(item => {
              const type = item.section_name || item.question_type || '其他';
              const no = `${item.question_no}${item.sub_question_no ? '-' + item.sub_question_no : ''}`;
              return `<tr><td>${escapeHtml(type)}</td><td>${escapeHtml(no)}</td><td class="numeric">${item.max_score > 0 ? fmt(item.max_score) : '未提供'}</td></tr>`;
            }).join('');
            skeleton.innerHTML = `<table class="item-score-skeleton-table"><thead><tr><th>题型</th><th>题号</th><th class="numeric">满分</th></tr></thead><tbody>${rows}</tbody></table>`;
          }).catch(() => {
            skeleton.textContent = '试卷结构读取失败，可直接粘贴上传，错误会逐行报出。';
          });
        });
      }

      submit.onclick = async () => {
        const parsed = parseItemScorePaste(document.getElementById('m-item-score-paste').value, roster);
        if (!parsed.rows.length && !parsed.errors.length) {
          errorBox.className = 'batch-paste-errors';
          errorBox.innerHTML = '<p>请先粘贴小分数据。</p>';
          return;
        }
        if (parsed.errors.length) {
          errorBox.className = 'batch-paste-errors';
          errorBox.innerHTML = `<strong>发现 ${parsed.errors.length} 个问题，尚未上传：</strong><ul>${parsed.errors.slice(0, 20).map(item => `<li>${escapeHtml(item)}</li>`).join('')}</ul>${parsed.errors.length > 20 ? `<p>另有 ${parsed.errors.length - 20} 个问题未显示。</p>` : ''}`;
          return;
        }
        const examId = await examIdPromise;
        if (examId == null) {
          errorBox.className = 'batch-paste-errors';
          errorBox.innerHTML = '<p>没找到这场考试在后端对应的考试记录，无法写入。请先保存一次成绩，让考试同步到数据库后重试。</p>';
          return;
        }
        submit.disabled = true;
        submit.textContent = '正在写入…';
        teachMateApi.putItemScores(examId, {
          rows: parsed.rows.map(item => item.row),
          note: '成绩表上传小分',
          overwrite_teacher_override: Boolean(document.getElementById('m-item-score-overwrite')?.checked),
        }, currentTermId).then(result => {
          submit.disabled = false;
          submit.textContent = '校验并写入';
          errorBox.className = 'batch-paste-errors';
          renderItemScoreResult(result || {}, errorBox, exam);
          render();
          showToast(`已写入 ${(result && result.written) || 0} 条小分`);
        }).catch(error => {
          submit.disabled = false;
          submit.textContent = '校验并写入';
          errorBox.className = 'batch-paste-errors';
          const detail = error && error.detail;
          if (detail && typeof detail === 'object' && !Array.isArray(detail) && detail.code === 'item_score_rows_invalid') {
            renderItemScoreProblems(detail, errorBox);
            return;
          }
          const status = error && error.status;
          const prefix = status === 404
            ? '这场考试还没有已确认的试卷结构，无法按题型写入小分。'
            : '上传失败：';
          errorBox.innerHTML = `<strong>${escapeHtml(prefix)}</strong><p>${escapeHtml((error && error.message) || '请稍后重试')}</p>`;
        });
      };
    }

    function removeDictationRounds(indices) {
      const names = Array.isArray(state.dictationNames) ? state.dictationNames : [];
      const valid = [...new Set(indices.map(Number))].filter(i => Number.isInteger(i) && i >= 0 && i < names.length).sort((a, b) => b - a);
      if (!valid.length) return { count: 0, message: '请先选择要删除的轮次' };
      const message = `已删除 ${valid.length} 个轮次`;
      const result = commitMutation(() => {
        valid.forEach(index => {
          state.dictationNames.splice(index, 1);
          if (Array.isArray(state.dictationRanges)) state.dictationRanges.splice(index, 1);
          Object.values(state.dictation || {}).forEach(values => {
            if (Array.isArray(values)) values.splice(index, 1);
          });
        });
        selectedDictationRounds.clear();
      });
      if (result && typeof result.then === 'function') {
        return result.then(value => ({ count: value.ok ? valid.length : 0, message: value.ok ? message : '删除失败' }));
      }
      return { count: result.ok ? valid.length : 0, message: result.ok ? message : '删除失败' };
    }

    function openDeleteDictationRoundModal() {
      const names = state.dictationNames || [];
      if (!names.length) return showToast('暂无可删除的默写轮次', 'error');
      const options = names.map((name, index) => `<option value="${index}" ${index === dictationRoundIndex ? 'selected' : ''}>${escapeHtml(name)}</option>`).join('');
      openModal('删除默写轮次', `<p>删除后该轮所有学生成绩也会一并删除，请谨慎操作。</p><div class="form-group"><label>选择要删除的轮次</label><select id="m-dict-round-delete">${options}</select></div>`, `<button class="btn" data-act="dict-round-delete-cancel">取消</button><button class="btn btn-danger" data-act="dict-round-delete-confirm">确认删除</button>`);
    }

    function openBatchDeleteDictationRoundsModal() {
      const indexes = [...selectedDictationRounds].sort((a, b) => a - b);
      if (!indexes.length) return showToast('请先勾选要删除的轮次', 'error');
      const names = state.dictationNames || [];
      const selectedNames = indexes.map(i => names[i]).filter(Boolean).map(escapeHtml).join('、');
      openModal('批量删除默写轮次', `<p>即将删除 ${indexes.length} 个轮次：${selectedNames}</p><p style="color:var(--md-text-secondary);">对应轮次的所有学生成绩也会一并删除，删除后可能无法恢复。</p>`, `<button class="btn" data-act="dict-round-delete-cancel">取消</button><button class="btn btn-danger" data-act="dict-round-delete-confirm-batch">确认删除</button>`);
    }

    function removeReciteTasks(ids) {
      const tasks = Array.isArray(state.recitations) ? state.recitations : [];
      const idSet = new Set(ids);
      const valid = tasks.filter(task => idSet.has(task.id));
      if (!valid.length) return { count: 0, message: '请先选择要删除的任务' };
      if (tasks.length - valid.length < 1) return { count: 0, message: '至少保留一项背诵任务' };
      const message = `已删除 ${valid.length} 个背诵任务`;
      const result = commitMutation(() => {
        state.recitations = tasks.filter(task => !idSet.has(task.id));
        if (reciteTaskId && idSet.has(reciteTaskId)) reciteTaskId = state.recitations[0]?.id || '';
        selectedReciteTasks.clear();
      });
      if (result && typeof result.then === 'function') {
        return result.then(value => ({ count: value.ok ? valid.length : 0, message: value.ok ? message : '删除失败' }));
      }
      return { count: result.ok ? valid.length : 0, message: result.ok ? message : '删除失败' };
    }

    function openBatchDeleteReciteTasksModal() {
      const ids = [...selectedReciteTasks];
      const tasks = state.recitations || [];
      const selected = tasks.filter(task => selectedReciteTasks.has(task.id));
      if (!selected.length) return showToast('请先勾选要删除的任务', 'error');
      if (tasks.length - selected.length < 1) return showToast('至少保留一项背诵任务', 'error');
      const selectedNames = selected.map(task => escapeHtml(task.title || '未命名背诵任务')).join('、');
      openModal('批量删除背诵任务', `<p>即将删除 ${selected.length} 个任务：${selectedNames}</p><p style="color:var(--md-text-secondary);">对应任务的所有学生背诵记录也会一并删除，删除后可能无法恢复。</p>`, `<button class="btn" data-act="recite-task-delete-cancel">取消</button><button class="btn btn-danger" data-act="recite-task-delete-confirm-batch">确认删除</button>`);
    }

    function removeWritingTasks(ids) {
      const tasks = Array.isArray(state.writings) ? state.writings : [];
      const idSet = new Set(ids);
      const valid = tasks.filter(task => idSet.has(task.id));
      if (!valid.length) return { count: 0, message: '请先选择要删除的任务' };
      if (tasks.length - valid.length < 1) return { count: 0, message: '至少保留一项写作任务' };
      const message = `已删除 ${valid.length} 个写作任务`;
      const result = commitMutation(() => {
        state.writings = tasks.filter(task => !idSet.has(task.id));
        if (writingTaskId && idSet.has(writingTaskId)) writingTaskId = state.writings[0]?.id || '';
        selectedWritingTasks.clear();
      });
      if (result && typeof result.then === 'function') {
        return result.then(value => ({ count: value.ok ? valid.length : 0, message: value.ok ? message : '删除失败' }));
      }
      return { count: result.ok ? valid.length : 0, message: result.ok ? message : '删除失败' };
    }

    function openBatchDeleteWritingTasksModal() {
      const tasks = state.writings || [];
      const selected = tasks.filter(task => selectedWritingTasks.has(task.id));
      if (!selected.length) return showToast('请先勾选要删除的任务', 'error');
      if (tasks.length - selected.length < 1) return showToast('至少保留一项写作任务', 'error');
      const selectedNames = selected.map(task => escapeHtml(task.title || '未命名写作任务')).join('、');
      openModal('批量删除写作任务', `<p>即将删除 ${selected.length} 个任务：${selectedNames}</p><p style="color:var(--md-text-secondary);">对应任务的所有学生写作成绩也会一并删除，删除后可能无法恢复。</p>`, `<button class="btn" data-act="writing-task-delete-cancel">取消</button><button class="btn btn-danger" data-act="writing-task-delete-confirm-batch">确认删除</button>`);
    }

    function openDictationRangeModal(roundIndex) {
      const names = state.dictationNames || [];
      const name = names[roundIndex];
      if (!name) return;
      const configured = normalizeDictationRanges((state.dictationRanges || [])[roundIndex]);
      const rows = Array.from({ length: 10 }, (_, index) => {
        const range = configured[index] || {};
        const min = range.min == null ? '' : range.min;
        const max = range.max == null ? '' : range.max;
        return `<tr><td>${index + 1}</td><td><input type="text" id="m-dict-range-label-${index}" value="${escapeAttr(range.label || '')}" maxlength="30" placeholder="可选"></td><td><input type="number" id="m-dict-range-min-${index}" value="${escapeAttr(min)}" min="0" max="100" step="0.5" placeholder="不限"></td><td><input type="number" id="m-dict-range-max-${index}" value="${escapeAttr(max)}" min="0" max="100" step="0.5" placeholder="不限"></td></tr>`;
      }).join('');
      openModal(`设置${escapeHtml(name)}统计`, `<div style="overflow:auto;"><table><thead><tr><th>序号</th><th>分数段名称（可选）</th><th>最低分</th><th>最高分</th></tr></thead><tbody>${rows}</tbody></table></div>`, `<button class="btn" onclick="closeModal()">取消</button><button class="btn btn-primary" id="m-dict-range-save">保存统计设置</button>`);
      document.getElementById('m-dict-range-save').onclick = () => {
        const ranges = [];
        for (let index = 0; index < 10; index += 1) {
          const label = document.getElementById(`m-dict-range-label-${index}`)?.value.trim() || '';
          const minRaw = document.getElementById(`m-dict-range-min-${index}`)?.value.trim() || '';
          const maxRaw = document.getElementById(`m-dict-range-max-${index}`)?.value.trim() || '';
          if (!label && !minRaw && !maxRaw) continue;
          const min = minRaw === '' ? null : Number(minRaw);
          const max = maxRaw === '' ? null : Number(maxRaw);
          if ((min == null && max == null) || (min != null && (!Number.isFinite(min) || min < 0 || min > DICTATION_FULL_SCORE)) || (max != null && (!Number.isFinite(max) || max < 0 || max > DICTATION_FULL_SCORE)) || (min != null && max != null && min > max)) {
            return showToast(`第${index + 1}个分数段设置无效，请检查0-100分范围和上下限`, 'error');
          }
          ranges.push({ label, min, max });
        }
        const result = commitMutation(() => {
          if (!Array.isArray(state.dictationRanges)) state.dictationRanges = [];
          while (state.dictationRanges.length < names.length) state.dictationRanges.push([]);
          state.dictationRanges[roundIndex] = normalizeDictationRanges(ranges);
        }, { successMessage: `${name}统计设置已保存` });
        const finish = value => { if (value.ok) closeModal(); };
        if (result && typeof result.then === 'function') result.then(finish); else finish(result);
      };
    }

    async function mutateDatabaseExam(exam, action) {
      const records = await apiRequest(`/api/v1/exams?term_id=${currentTermId}&include_archived=true`);
      const record = records.find(item => String(item.source_key) === String(exam.id));
      if (!record) throw new Error('未找到考试的数据库记录，请刷新后重试');
      if (action === 'purge') await createDatabaseBackup();
      const payload = await apiRequest(`/api/v1/exams/${record.id}/${action}?term_id=${currentTermId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ source_key: String(exam.id), expected_revision: databaseRevision })
      });
      state = migrate(payload.state);
      databaseRevision = payload.revision;
      currentExamId = state.currentExamId || state.exams[0]?.id || '';
      state.currentExamId = currentExamId;
      return payload;
    }

    async function deleteCurrentExam() {
      const exam = getCurrentExam();
      if (!exam) return false;
      const result = await commitMutation(async () => {
        if (DATABASE_MODE) return mutateDatabaseExam(exam, 'archive');
        state.archivedExams = Array.isArray(state.archivedExams) ? state.archivedExams : [];
        state.exams = state.exams.filter(item => item.id !== exam.id);
        state.archivedExams.push({ ...exam, status: 'archived', archivedAt: new Date().toISOString() });
        const nextExam = state.exams[0] || null;
        currentExamId = nextExam?.id || '';
        state.currentExamId = currentExamId;
        return true;
      }, { skipSave: DATABASE_MODE, successMessage: '考试已归档' });
      return result.ok;
    }

    function openArchivedExamsModal() {
      const archived = [...(state.archivedExams || [])];
      const rows = archived.length
        ? archived.map(exam => `<tr><td>${escapeHtml(exam.name)}</td><td>${escapeHtml(String(exam.date || exam.examDate || '').slice(0, 10) || '—')}</td><td class="text-right"><button class="btn btn-sm" data-act="archived-exam-restore" data-id="${escapeAttr(exam.id)}">恢复</button><button class="btn btn-sm btn-text danger-text" data-act="archived-exam-purge" data-id="${escapeAttr(exam.id)}">永久清理</button></td></tr>`).join('')
        : '<tr><td colspan="3" class="empty">暂无已归档考试</td></tr>';
      openModal('已归档考试', '<p style="color:var(--md-text-secondary);">归档不会删除历史成绩；永久清理会删除考试及其关联成绩，且无法恢复。</p><div class="archived-table-wrap"><table><thead><tr><th>考试</th><th>日期</th><th class="text-right">操作</th></tr></thead><tbody>' + rows + '</tbody></table></div>', '<button class="btn btn-primary" data-act="about-close">关闭</button>');
    }

    function openPurgeArchivedExamModal(id) {
      const exam = (state.archivedExams || []).find(item => item.id === id);
      if (!exam) return;
      openModal('永久清理考试？', `<p>即将永久清理「${escapeHtml(exam.name)}」及其全部成绩、题型成绩和班级指标。</p><p class="danger-note">此操作不可恢复，请确认已不再需要这些数据。</p>`, '<button class="btn" data-act="delete-cancel">取消</button><button class="btn btn-danger" data-act="archived-exam-purge-confirm" data-id="' + escapeAttr(id) + '">永久清理</button>');
    }

    async function restoreArchivedExam(id) {
      const exam = (state.archivedExams || []).find(item => item.id === id);
      if (!exam) return false;
      const result = await commitMutation(async () => {
        if (DATABASE_MODE) return mutateDatabaseExam(exam, 'restore');
        state.archivedExams = state.archivedExams.filter(item => item.id !== id);
        state.exams.push({ ...exam, status: 'active', archivedAt: undefined });
        currentExamId = id;
        state.currentExamId = id;
        return true;
      }, { skipSave: DATABASE_MODE, successMessage: '考试已恢复' });
      if (result.ok) { closeModal(); renderNav(); render(); }
      return result.ok;
    }

    async function purgeArchivedExam(id) {
      const exam = (state.archivedExams || []).find(item => item.id === id);
      if (!exam) return false;
      const result = await commitMutation(async () => {
        if (DATABASE_MODE) return mutateDatabaseExam(exam, 'purge');
        state.archivedExams = state.archivedExams.filter(item => item.id !== id);
        return true;
      }, { skipSave: DATABASE_MODE, successMessage: '考试已永久清理' });
      if (result.ok) { closeModal(); renderNav(); render(); }
      return result.ok;
    }

    // ================= 学生管理弹窗 =================
    function openStuModal(id) {
      const s = id ? state.students.find(x=>x.id===id) : {};
      const isEdit = !!id;
      openModal(isEdit?'编辑学生':'新增学生', `
        <div class="form-row">
          <div class="form-group"><label>学号</label><input id="m-stu-id" value="${escapeAttr(s.id||'')}" ${isEdit?'disabled':''}></div>
          <div class="form-group"><label>姓名</label><input id="m-stu-name" value="${escapeAttr(s.name||'')}"></div>
        </div>
        <div class="form-row">
          <div class="form-group"><label>班级</label><select id="m-stu-class">${state.classes.map(c=>`<option value="${escapeAttr(c)}" ${s.class===c?'selected':''}>${escapeHtml(c)}</option>`).join('')}</select></div>
        </div>
        <div class="form-row">
          <div class="form-group"><label>${subjectHtml('entrance_score')}</label><input type="number" min="0" step="0.5" id="m-stu-english" value="${s.english ?? ''}"></div>
          <div class="form-group"><label>家长电话</label><input id="m-stu-phone" value="${escapeAttr(s.phone||'')}"></div>
        </div>
      `, `<button class="btn" onclick="closeModal()">取消</button><button class="btn btn-primary" id="m-stu-save">保存</button>`);
      document.getElementById('m-stu-save').onclick = async () => {
        const sid = document.getElementById('m-stu-id').value.trim();
        const name = document.getElementById('m-stu-name').value.trim();
        if (!sid || !name) return showToast('学号和姓名必填', 'error');
        const englishRaw = document.getElementById('m-stu-english').value.trim();
        const english = englishRaw === '' ? 0 : Number(englishRaw);
        if (!Number.isFinite(english) || english < 0) {
          return showToast(`${subjectText('entrance_score')}必须是非负数`, 'error');
        }
        const obj = {
          id: sid, name,
          class: document.getElementById('m-stu-class').value,
          english,
          target: s.target ?? '',
          weakTags: s.weakTags ?? '',
          phone: document.getElementById('m-stu-phone').value,
          seat: s.seat ?? '',
          evaluationTags: Array.isArray(s.evaluationTags) ? s.evaluationTags : [],
          evaluationNote: s.evaluationNote ?? ''
        };
        if (!isEdit && state.students.some(x=>x.id===sid)) return showToast('学号已存在', 'error');
        if (!isEdit && (state.archivedStudents || []).some(x=>x.id===sid)) return showToast('该学号已归档，请先在数据维护中恢复', 'error');
        const result = await commitMutation(() => {
          if (isEdit) { const idx = state.students.findIndex(x=>x.id===id); state.students[idx] = obj; }
          else state.students.push(obj);
        }, { successMessage: '保存成功', renderNavigation: true });
        if (result.ok) closeModal();
      };
    }

    function delStudent(id) {
      openArchiveStudentModal(id);
    }

    function getExamTier(exam, score) {
      const lines = normalizeTierLines(exam?.tierLines, exam?.fullScore);
      if (score === null || score === undefined || score === '' || !lines) return '—';
      if (score >= lines.a) return 'A';
      if (score >= lines.b) return 'B';
      if (score >= lines.c) return 'C';
      return 'D';
    }

    function pruneEvaluationTags(tags) {
      const result = [...(Array.isArray(tags) ? tags : [])].sort((a, b) => String(a.createdAt).localeCompare(String(b.createdAt)));
      while (result.length > 100) {
        const index = result.findIndex(tag => !tag.locked);
        if (index < 0) break;
        const [removed] = result.splice(index, 1);
        state?.students?.forEach(student => { student.evaluationTags = (student.evaluationTags || []).filter(tagId => tagId !== removed.id); });
      }
      return result;
    }

    async function renderStudentComparisonChart(history) {
      const chartDefs = [
        { id: 'student-chart-score', title: subjectText('score_trend'), name: subjectText('score_short'), color: '#0b57d0', yName: '分数', data: item => item.score },
        { id: 'student-chart-grade-rank', title: '年级排名趋势', name: '年级排名', color: '#f29900', yName: '名次', inverse: true, data: item => item.gradeRank },
        { id: 'student-chart-class-rank', title: '班级排名趋势', name: '班级排名', color: '#0f9d58', yName: '名次', inverse: true, data: item => item.classRank },
        { id: 'student-chart-tier', title: '层级变化趋势', name: '层级', color: '#7c4dff', yName: '层级', tier: true, data: item => ({ A: 4, B: 3, C: 2, D: 1 }[item.tier] || null) },
      ];
      const nodes = chartDefs.map(def => document.getElementById(def.id)).filter(Boolean);
      if (!nodes.length) return;
      if (!history.length) { nodes.forEach(node => { node.innerHTML = '<div class="trend-empty">暂无足够的考试记录</div>'; }); return; }
      if (typeof echarts === 'undefined' && typeof navigator !== 'undefined' && /jsdom/i.test(navigator.userAgent || '')) {
        nodes.forEach(node => { node.innerHTML = '<div class="trend-empty">当前环境无法绘制图表，请使用浏览器打开</div>'; });
        return;
      }
      try {
        await ensureEcharts();
      } catch (error) {
        nodes.forEach(node => { node.innerHTML = '<div class="trend-empty">图表组件加载失败，请重试。</div>'; });
        return;
      }
      if (typeof echarts === 'undefined') {
        nodes.forEach(node => { node.innerHTML = '<div class="trend-empty">当前环境无法绘制图表，请重试。</div>'; });
        return;
      }
      try {
        const canvas = document.createElement('canvas');
        const context = canvas.getContext && canvas.getContext('2d');
        if (!context) throw new Error('Canvas unavailable');
      } catch (error) {
        nodes.forEach(node => { node.innerHTML = '<div class="trend-empty">当前环境无法绘制图表，请使用浏览器打开</div>'; });
        return;
      }
      const chartHistory = [...history].sort((a, b) => String(a.exam.date || '').localeCompare(String(b.exam.date || '')));
      const labels = chartHistory.map(item => item.exam.name);
      chartDefs.forEach(def => {
        const node = document.getElementById(def.id);
        if (!node) return;
        let chart;
        try { chart = echarts.init(node); chart.setOption({ animationDuration: 300, tooltip: { trigger: 'axis', confine: true }, grid: { left: 70, right: 24, top: 28, bottom: 48 }, xAxis: { type: 'category', data: labels, axisLabel: { rotate: labels.length > 4 ? 25 : 0, hideOverlap: true } }, yAxis: { type: 'value', name: def.yName, nameTextStyle: { color: '#667085' }, axisLabel: { show: true, color: '#667085', formatter: def.tier ? (value => ({ 1: 'D', 2: 'C', 3: 'B', 4: 'A' }[value] || '')) : (value => fmt(value)) }, inverse: Boolean(def.inverse), min: def.tier ? 1 : undefined, max: def.tier ? 4 : undefined, interval: def.tier ? 1 : undefined, scale: !def.tier }, series: [{ name: def.name, type: 'line', smooth: !def.tier, step: def.tier ? 'middle' : undefined, connectNulls: false, symbolSize: 8, data: chartHistory.map(def.data), lineStyle: { width: 3, color: def.color }, itemStyle: { color: def.color } }] }); studentCharts.push(chart); } catch (error) { try { chart?.dispose(); } catch (disposeError) {} node.innerHTML = '<div class="trend-empty">当前环境无法绘制图表，请使用浏览器打开</div>'; }
      });
    }

    function renderStudentReciteHistory(student) {
      const tasks = Array.isArray(state.recitations) ? state.recitations : [];
      const rows = tasks.map(task => {
        const status = normalizeReciteStatus(task.status?.[student.id]);
        const retake = status.level === 'F' && status.retake ? (status.retake === 'passed' ? '通过' : '未通过') : '—';
        return `<tr><td>${escapeHtml(task.title || '未命名任务')}</td><td>${escapeHtml(task.scope || '—')}</td><td class="text-center"><strong>${escapeHtml(status.level || '—')}</strong></td><td class="text-center">${escapeHtml(retake)}</td></tr>`;
      }).join('');
      return `<div class="card student-detail-practice-card"><div class="card-header"><h3 class="card-title">背诵成绩</h3><span class="badge badge-blue">${tasks.length} 个任务</span></div><div class="card-body" style="padding:0;overflow:auto;">${tasks.length ? `<table><thead><tr><th>任务</th><th>范围</th><th>档位</th><th>重背结果</th></tr></thead><tbody>${rows}</tbody></table>` : '<div class="empty">暂无背诵任务记录</div>'}</div></div>`;
    }

    function renderStudentDictationHistory(student) {
      const names = Array.isArray(state.dictationNames) ? state.dictationNames : [];
      const scores = Array.isArray(state.dictation?.[student.id]) ? state.dictation[student.id] : [];
      const rows = names.map((name, index) => {
        const value = scores[index];
        return `<tr><td>${escapeHtml(name)}</td><td class="text-center">${value === '' || value == null ? '—' : escapeHtml(value)}</td></tr>`;
      }).join('');
      return `<div class="card student-detail-practice-card"><div class="card-header"><h3 class="card-title">默写成绩</h3><span class="badge badge-blue">${names.length} 轮</span></div><div class="card-body" style="padding:0;overflow:auto;">${names.length ? `<table><thead><tr><th>轮次</th><th>成绩</th></tr></thead><tbody>${rows}</tbody></table>` : '<div class="empty">暂无默写轮次记录</div>'}</div></div>`;
    }

    function renderStudentHomeworkHistory(student) {
      const tasks = Array.isArray(state.homeworkTasks) ? state.homeworkTasks : [];
      const records = state.homeworkRecords || {};
      const rows = tasks.map(task => {
        const submitted = (records[task.id] || {})[student.id] === true;
        return `<tr><td>${escapeHtml(task.name)}</td><td>${escapeHtml(task.date || '—')}</td><td class="text-center">${submitted ? '<span style="color:var(--md-success);font-weight:700;">✓ 已交</span>' : '<span style="color:var(--danger);font-weight:700;">✗ 未交</span>'}</td></tr>`;
      }).join('');
      const submittedCount = tasks.filter(t => (records[t.id] || {})[student.id] === true).length;
      return `<div class="card student-detail-practice-card"><div class="card-header"><h3 class="card-title">日常作业</h3><span class="badge badge-blue">${submittedCount}/${tasks.length} 已交</span></div><div class="card-body" style="padding:0;overflow:auto;">${tasks.length ? `<table><thead><tr><th>作业</th><th>日期</th><th>状态</th></tr></thead><tbody>${rows}</tbody></table>` : '<div class="empty">暂无作业记录</div>'}</div></div>`;
    }

    function renderStudentWritingHistory(student) {
      const tasks = Array.isArray(state.writings) ? state.writings : [];
      const rows = tasks.map(task => {
        const score = task.scores?.[student.id];
        return `<tr><td>${escapeHtml(task.title || '未命名写作任务')}</td><td>${escapeHtml(task.date || '—')}</td><td class="text-center">${score === '' || score == null ? '—' : escapeHtml(score)}</td><td class="text-center">${fmt(task.fullScore || 12)}</td></tr>`;
      }).join('');
      return `<div class="card student-detail-practice-card"><div class="card-header"><h3 class="card-title">写作成绩</h3><span class="badge badge-blue">${tasks.length} 个任务</span></div><div class="card-body" style="padding:0;overflow:auto;">${tasks.length ? `<table><thead><tr><th>任务</th><th>日期</th><th>成绩</th><th>满分</th></tr></thead><tbody>${rows}</tbody></table>` : '<div class="empty">暂无写作任务记录</div>'}</div></div>`;
    }

    function renderStudentLearningProfile(payload, studentId) {
      const profile = payload?.learning_profile || {};
      const meta = payload?.learning_profile_meta || {};
      const tracking = payload?.question_type_tracking || {};
      const trackingAverages = Array.isArray(tracking.averages) ? tracking.averages : [];
      const hasRate = value => value !== null && value !== undefined && String(value).trim() !== '' && Number.isFinite(Number(value));
      const measuredTypeCount = trackingAverages.filter(item => item && hasRate(item[tracking.metric_key || 'score_rate'])).length;
      let summary = String(profile.summary || '').trim();
      // 兼容旧版只有列表字段的档案；新版本只展示一段连贯摘要。
      if (!summary) {
        const legacy = [];
        [['strengths', '目前表现出'], ['weaknesses', '仍需关注'], ['watch_items', '后续建议继续观察']].forEach(([key, label]) => {
          const values = Array.isArray(profile[key]) ? profile[key].map(item => item && typeof item === 'object' ? (item.action || '') : String(item)).filter(Boolean) : [];
          if (values.length) legacy.push(`${label}${values.slice(0, 2).join('、')}`);
        });
        if (legacy.length) summary = `根据已有记录，${legacy.join('；')}。后续结合新的学习表现持续更新。`;
      }
      const summaryHtml = payload?.loading
        ? '<div class="student-profile-skeleton" role="status"><strong>正在加载学生画像</strong><span class="wb-skeleton wb-skeleton-line"></span><span class="wb-skeleton wb-skeleton-line sm"></span></div>'
        : payload?.profile_error
          ? `<div class="student-profile-load-error" role="alert"><span>${escapeHtml(payload.profile_error.message || '学生画像暂时无法加载')}</span><button class="btn btn-sm" data-act="student-profile-retry" data-id="${escapeAttr(studentId)}">重新加载</button></div>`
          : summary
            ? `<p class="student-profile-summary">${escapeHtml(summary)}</p>`
            : '<p class="student-profile-summary">暂无画像摘要。使用 TeachMate 的“学生诊断”后，画像会自动更新。</p>';
      const pending = Array.isArray(payload?.pending_profile_revisions) ? payload.pending_profile_revisions : [];
      const pendingHtml = pending.length ? pending.map(revision => {
        const patch = revision.patch || {};
        const proposed = String(patch.summary || '').trim()
          .replace(/\bstudent[ _-]?\d+\b/gi, '该生')
          .replace(/学生[ _-]?\d+/g, '该生');
        const changed = proposed ? `<li>${escapeHtml(proposed)}</li>` : '';
        return `<div class="student-profile-pending"><div><strong>待确认画像变更</strong><span class="subtle">新建议</span></div><ul>${changed || '<li>本次建议将更新学生画像，确认后可在学生管理中查看新的画像摘要。</li>'}</ul><div class="student-profile-actions"><button class="btn btn-sm btn-primary" data-act="student-profile-confirm" data-id="${escapeAttr(revision.id)}">确认写入画像</button><button class="btn btn-sm btn-text" data-act="student-profile-reject" data-id="${escapeAttr(revision.id)}">拒绝</button></div></div>`;
      }).join('') : '';
      const updated = payload?.loading ? '正在同步' : (meta.updated_at ? String(meta.updated_at).slice(0, 10) : '尚未建立');
      const editButton = (typeof DATABASE_MODE !== 'undefined' && DATABASE_MODE)
        ? `<button class="btn btn-sm" data-act="student-profile-edit" data-id="${escapeAttr(studentId)}">编辑画像</button>` : '';
      const trackingCountLabel = payload?.loading ? '正在读取' : (tracking.exam_count ? `${tracking.exam_count} 场考试均值` : '等待小题成绩');
      const sourceLabel = tracking.data_source || '已录入的小题成绩';
      return `<div id="student-learning-profile-${escapeAttr(studentId)}" class="card student-profile-card" style="margin:0 0 16px;"><div class="card-header"><h3 class="card-title">学生画像</h3><div style="display:flex;align-items:center;gap:8px;">${editButton}</div></div><div class="card-body"><div class="student-profile-layout"><div class="student-profile-copy" role="region" aria-label="学生画像内容" tabindex="0">${summaryHtml}<div class="subtle" style="margin-top:12px;">最近确认：${escapeHtml(updated)}${meta.updated_by ? ` · ${escapeHtml(String(meta.updated_by))}` : ''}</div>${pendingHtml}</div><aside class="student-profile-radar-panel" aria-label="题型能力图"><div class="student-profile-radar-head"><div><strong>题型能力图</strong><small>${escapeHtml(tracking.metric_label || '各场考试题型得分率的平均值')}</small></div><span class="student-profile-radar-count">${escapeHtml(trackingCountLabel)}</span></div><div id="student-ability-radar-${escapeAttr(studentId)}" class="student-ability-radar" data-measured-count="${measuredTypeCount}"><div class="trend-empty">正在读取逐题成绩…</div></div><div class="student-profile-radar-foot"><span>${escapeHtml(sourceLabel)}</span><span>缺失或未录全的题型不计入均值</span></div></aside></div></div></div>`;
    }

    async function renderStudentAbilityRadar(tracking, studentId) {
      const node = document.getElementById(`student-ability-radar-${studentId}`);
      if (!node) return;
      if (tracking?.error) {
        node.innerHTML = `<div class="student-ability-empty" role="alert"><span>${escapeHtml(tracking.error)}</span><button class="btn btn-sm" data-act="student-profile-retry" data-id="${escapeAttr(studentId)}">重新加载</button></div>`;
        return;
      }
      if (tracking?.loading) {
        node.innerHTML = '<div class="student-ability-empty" role="status"><span class="wb-skeleton wb-skeleton-block" aria-hidden="true"></span><span>正在读取能力数据</span></div>';
        return;
      }
      const allCategories = Array.isArray(tracking?.categories) ? tracking.categories : [];
      const averages = Array.isArray(tracking?.averages) ? tracking.averages : [];
      const averageMap = new Map(averages.map(item => [String(item?.type || ''), item]));
      const metric = tracking?.metric_key || 'score_rate';
      const unit = tracking?.unit || '%';
      const categories = allCategories.filter(name => {
        const value = averageMap.get(String(name))?.[metric];
        return value !== null && value !== undefined && String(value).trim() !== '' && Number.isFinite(Number(value));
      });
      const missing = allCategories.filter(name => !categories.includes(name));
      const panel = node.closest('.student-profile-radar-panel');
      panel?.querySelector('.student-ability-missing')?.remove();
      if (missing.length || metric === 'mean_score') {
        const note = document.createElement('small');
        note.className = 'student-ability-missing';
        note.textContent = (missing.length ? `暂无完整数据：${missing.join('、')}。` : '')
          + (metric === 'mean_score' ? '显示实际每题平均得分，题型间分值可能不同，不作为掌握率。' : '');
        panel?.appendChild(note);
      }
      if (!categories.length) {
        node.innerHTML = '<div class="student-ability-empty"><span class="material-symbols-rounded" aria-hidden="true">insights</span><span>设置试卷分布并录入完整题型的小分后，这里会形成能力图</span></div>';
        return;
      }
      if (typeof echarts === 'undefined' && typeof navigator !== 'undefined' && /jsdom/i.test(navigator.userAgent || '')) {
        node.innerHTML = '<div class="trend-empty">当前环境无法绘制能力图，请使用浏览器打开</div>';
        return;
      }
      try { await ensureEcharts(); } catch (error) {
        node.innerHTML = '<div class="trend-empty">图表组件加载失败，请重试。</div>';
        return;
      }
      if (typeof echarts === 'undefined') {
        node.innerHTML = '<div class="trend-empty">当前环境无法绘制能力图，请重试。</div>';
        return;
      }
      try {
        const canvas = document.createElement('canvas');
        const context = canvas.getContext && canvas.getContext('2d');
        if (!context) throw new Error('Canvas unavailable');
      } catch (error) {
        node.innerHTML = '<div class="trend-empty">当前环境无法绘制能力图，请使用浏览器打开</div>';
        return;
      }
      try {
        const values = categories.map(name => {
          const rate = averageMap.get(String(name))?.[metric];
          return rate === null || rate === undefined || String(rate).trim() === '' || !Number.isFinite(Number(rate)) ? null : Number(rate);
        });
        const chart = echarts.init(node);
        if (typeof ResizeObserver === 'function') {
          chart.__workbenchResizeObserver = new ResizeObserver(() => {
            if (node.isConnected) chart.resize();
          });
          chart.__workbenchResizeObserver.observe(node);
        }
        const scaleMaximum = metric === 'score_rate' ? 100 : Math.max(1, Math.ceil(Math.max(...values) * 1.15));
        const caption = metric === 'score_rate' ? '题型得分率' : '每题平均得分';
        const option = {
          animationDuration: 320,
          tooltip: {
            trigger: 'item',
            confine: true,
            formatter: () => categories.map((name, index) => `${name}：${values[index] == null ? '暂无数据' : `${fmt(values[index])}${unit}`}`).join('<br>'),
          },
          radar: {
            center: ['50%', '51%'],
            // Leave a small, fixed label gutter inside the locked panel so
            // long subject names never get clipped at the canvas edge.
            radius: '64%',
            startAngle: 90,
            splitNumber: 4,
            indicator: categories.map(name => ({ name, max: scaleMaximum })),
            axisName: { color: '#667085', fontSize: 13 },
            axisLine: { lineStyle: { color: '#d0d5dd' } },
            splitLine: { lineStyle: { color: '#d0d5dd' } },
            splitArea: { areaStyle: { color: ['rgba(67,97,238,.035)', 'rgba(67,97,238,.075)'] } },
          },
          series: [{
            name: caption,
            type: 'radar',
            symbol: 'circle',
            symbolSize: 9,
            data: [{
              value: values,
              name: '多场考试平均',
              lineStyle: { width: 2.5, color: '#4361ee' },
              itemStyle: { color: '#fff', borderColor: '#4361ee', borderWidth: 2 },
              areaStyle: { color: 'rgba(67,97,238,.22)' },
            }],
          }],
        };
        if (categories.length < 3) {
          delete option.radar;
          option.grid = { left: 100, right: 32, top: 35, bottom: 35 };
          option.xAxis = { type: 'value', max: scaleMaximum };
          option.yAxis = { type: 'category', data: categories };
          option.series = [{ name: caption, type: 'bar', data: values, barMaxWidth: 34, itemStyle: { color: '#4361ee' }, label: { show: true, position: 'right', formatter: item => `${fmt(item.value)}${unit}` } }];
        }
        chart.setOption(option);
        studentCharts.push(chart);
      } catch (error) {
        node.innerHTML = '<div class="trend-empty">当前环境无法绘制能力图，请使用浏览器打开</div>';
      }
    }

    async function loadStudentLearningProfile(student) {
      if (!DATABASE_MODE || !student || !student.id || !currentTermId) return {};
      try {
        const [profile, revisions] = await Promise.all([
          apiRequest(`/api/v1/students/${encodeURIComponent(student.id)}/profile?term_id=${encodeURIComponent(currentTermId)}`),
          apiRequest(`/api/v1/agent/profile-revisions?student_id=${encodeURIComponent(student.id)}&term_id=${encodeURIComponent(currentTermId)}&status=draft`).catch(() => []),
        ]);
        return { ...profile, pending_profile_revisions: revisions || [] };
      } catch (error) {
        return { learning_profile: {}, learning_profile_meta: {}, profile_error: error };
      }
    }

    async function reloadStudentLearningProfile(studentId) {
      const loadingNode = document.getElementById(`student-learning-profile-${studentId}`);
      if (!loadingNode) return;
      loadingNode.outerHTML = renderStudentLearningProfile({ loading: true }, studentId);
      const student = state.students.find(item => String(item.id) === String(studentId));
      const payload = await loadStudentLearningProfile(student);
      const profileNode = document.getElementById(`student-learning-profile-${studentId}`);
      if (!profileNode || !payload) return;
      profileNode.outerHTML = renderStudentLearningProfile(payload, studentId);
      renderStudentAbilityRadar(payload.profile_error ? { error: payload.profile_error.message } : payload.question_type_tracking, studentId);
    }

    async function confirmStudentProfileRevision(revisionId) {
      if (!revisionId) return;
      try {
        await apiRequest(`/api/v1/agent/profile-revisions/${encodeURIComponent(revisionId)}/confirm`, { method: 'POST' });
        showToast('学生画像已确认写入', 'success');
        closeModal(false);
      } catch (error) {
        showToast('画像写入失败：' + (error.message || error), 'error');
      }
    }

    async function rejectStudentProfileRevision(revisionId) {
      if (!revisionId) return;
      try {
        await apiRequest(`/api/v1/agent/profile-revisions/${encodeURIComponent(revisionId)}/reject`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ reason: '教师拒绝本次画像变更' }) });
        showToast('已拒绝本次画像变更');
        closeModal(false);
      } catch (error) {
        showToast('拒绝画像变更失败：' + (error.message || error), 'error');
      }
    }

    async function openStudentProfileEditModal(studentId) {
      const student = state.students.find(item => item.id === studentId);
      if (!student) return;
      try {
        const payload = await apiRequest(`/api/v1/students/${encodeURIComponent(studentId)}/profile?term_id=${encodeURIComponent(currentTermId)}`);
        const profile = payload?.learning_profile || {};
        const body = `<p class="subtle">画像是一段会随诊断持续更新的自然语言记录。可直接修改整段内容，保存后会保留版本记录。</p><div class="form-group"><label for="tm-profile-summary">画像摘要</label><textarea id="tm-profile-summary" rows="8" maxlength="2000" placeholder="例如：该生目前……，相比上次……，后续重点关注……。">${escapeHtml(profile.summary || '')}</textarea></div>`;
        openModal(`编辑学生画像 · ${student.name}`, body, '<button class="btn btn-text" data-act="modal-close">取消</button><button class="btn btn-primary" data-act="student-profile-save" data-id="' + escapeAttr(studentId) + '">保存画像</button>');
        document.querySelector('#modal .modal-content')?.classList.add('student-detail-modal');
        const saveButton = document.querySelector('[data-act="student-profile-save"]');
        if (saveButton) saveButton.dataset.expectedVersion = String(payload?.learning_profile_meta?.version || 0);
      } catch (error) {
        showToast('读取学生画像失败：' + (error.message || error), 'error');
      }
    }

    async function saveStudentProfileEdit(studentId, button) {
      if (!studentId) return;
      const patch = { summary: document.getElementById('tm-profile-summary')?.value || '' };
      try {
        await apiRequest(`/api/v1/students/${encodeURIComponent(studentId)}/profile?term_id=${encodeURIComponent(currentTermId)}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ patch, expected_version: Number(button?.dataset.expectedVersion || 0) }),
        });
        showToast('学生画像已更新', 'success');
        closeModal(false);
        openStudentDetail(studentId);
      } catch (error) {
        showToast('保存学生画像失败：' + (error.message || error), 'error');
      }
    }

    async function openStudentDetail(id) {
      const student = state.students.find(item => item.id === id);
      if (!student) return;
      // 先打开详情，画像接口异步加载，避免网络较慢时阻塞学生评价等已有操作。
      const learningProfilePromise = loadStudentLearningProfile(student);
      const learningProfile = { loading: true };
      student.evaluationTags = Array.isArray(student.evaluationTags) ? student.evaluationTags : [];
      student.evaluationNote = String(student.evaluationNote || '');
      // 学生明细默认保留全部考试；缺考或未录入的考试以空值显示，趋势图保留对应时间点。
      const history = state.exams.map(exam => {
        const score = getExamScore(exam, student);
        const rankMap = calculateClassRanks(exam, state.students);
        return { exam, score, gradeRank: getStudentGradeRank(exam, student), classRank: rankMap[student.id] || null, tier: getExamTier(exam, score) };
      });
      const rows = history.length ? history.map(item => `<tr><td>${escapeHtml(item.exam.name)}${item.exam.examKind === 'entrance' ? ' <span class="badge badge-blue">入学基线</span>' : ''}</td><td>${escapeHtml(item.exam.date || '—')}</td><td class="text-right">${item.score ?? '缺考'}</td><td class="text-center">${item.gradeRank ?? '—'}</td><td class="text-center">${item.classRank ?? '—'}</td><td class="text-center"><strong>${item.tier}</strong></td></tr>`).join('') : '<tr><td colspan="6" class="empty">暂时没有' + escapeHtml(subjectText('exam_default')) + '记录</td></tr>';
      const tags = state.studentTags || [];
      const assignedTags = tags.filter(tag => student.evaluationTags.includes(tag.id));
      const tagRows = assignedTags.length ? assignedTags.map(tag => `<div class="student-evaluation-row"><input class="student-evaluation-input" type="text" data-act="tag-name-change" data-tag-id="${escapeAttr(tag.id)}" value="${escapeAttr(tag.name)}" maxlength="100" ${tag.locked ? 'readonly' : ''} aria-label="文字评价"><span class="student-evaluation-lock">${tag.locked ? '已锁定' : ''}</span><button class="btn btn-sm" data-act="tag-lock-inline" data-id="${escapeAttr(student.id)}" data-tag-id="${escapeAttr(tag.id)}">${tag.locked ? '解锁' : '锁定'}</button><button class="btn btn-sm btn-danger" data-act="tag-delete-inline" data-id="${escapeAttr(student.id)}" data-tag-id="${escapeAttr(tag.id)}">删除</button></div>`).join('') : '<div style="color:var(--md-text-secondary);">还没有文字评价，请点击“新建评价”。</div>';
      const chartCards = '<div class="student-chart-grid"><div class="student-chart-card"><h4>' + escapeHtml(subjectText('score_trend')) + '</h4><div id="student-chart-score" class="student-history-chart"></div></div><div class="student-chart-card"><h4>年级排名趋势</h4><div id="student-chart-grade-rank" class="student-history-chart"></div></div><div class="student-chart-card"><h4>班级排名趋势</h4><div id="student-chart-class-rank" class="student-history-chart"></div></div><div class="student-chart-card"><h4>层级变化趋势</h4><div id="student-chart-tier" class="student-history-chart"></div></div></div>';
      openModal(`学生明细 · ${student.name}`, `<div class="card" style="margin:0 0 16px;"><div class="card-body"><div style="display:flex;gap:28px;flex-wrap:wrap;"><span><b>学号：</b>${escapeHtml(student.id)}</span><span><b>班级：</b>${escapeHtml(student.class)}</span><span><b>当前${subjectHtml('entrance_score')}：</b>${student.english ?? '—'}</span></div></div></div>${renderStudentLearningProfile(learningProfile, student.id)}<div class="card" style="margin:0 0 16px;"><div class="card-header"><h3 class="card-title">历次考试横向对比</h3></div><div class="card-body">${chartCards}</div></div><div class="card" style="margin:0 0 16px;"><div class="card-header"><h3 class="card-title">${subjectHtml('exam_default')}历史</h3></div><div class="card-body" style="padding:0;overflow:auto;"><table><thead><tr><th>考试</th><th>日期</th><th>${subjectHtml('score_short')}</th><th>年级排名</th><th>班级排名</th><th>层级</th></tr></thead><tbody>${rows}</tbody></table></div></div>${renderStudentReciteHistory(student)}${renderStudentDictationHistory(student)}${renderStudentHomeworkHistory(student)}${renderStudentWritingHistory(student)}<div class="card"><div class="card-header"><h3 class="card-title">文字评价</h3><div style="display:flex;gap:8px;"><button class="btn btn-sm btn-primary" data-act="tag-add" data-id="${escapeAttr(student.id)}">新建评价</button><button class="btn btn-sm" data-act="tag-batch-delete" data-id="${escapeAttr(student.id)}">批量管理</button></div></div><div class="card-body"><div class="student-evaluation-list">${tagRows}</div></div></div>`, '<button class="btn btn-primary" onclick="closeModal()">关闭</button>');
      document.querySelector('#modal .modal-content')?.classList.add('student-detail-modal');
      renderStudentComparisonChart(history);
      renderStudentAbilityRadar({ loading: true }, student.id);
      learningProfilePromise.then(function (payload) {
        const profileNode = document.getElementById(`student-learning-profile-${student.id}`);
        if (profileNode && payload) {
          profileNode.outerHTML = renderStudentLearningProfile(payload, student.id);
          renderStudentAbilityRadar(payload.profile_error ? { error: payload.profile_error.message } : payload.question_type_tracking, student.id);
        }
      }).catch(function () {});
    }

    function openTagCreateModal(studentId) {
      prepareModalForSubdialog();
      openModal('新建学生评价标签', '<div class="form-group"><label>标签名称</label><input id="m-tag-name" maxlength="40" placeholder="例如：作业认真、需要关注"></div>', '<button class="btn" onclick="closeModal()">取消</button><button class="btn btn-primary" id="m-tag-save">保存</button>');
      document.getElementById('m-tag-save').onclick = () => {
        const name = document.getElementById('m-tag-name').value.trim();
        if (!name) return showToast('请输入标签名称', 'error');
        if ((state.studentTags || []).some(tag => tag.name === name)) return showToast('标签名称已存在', 'error');
        const next = [...(state.studentTags || []), { id: uid(), name, locked: false, createdAt: new Date().toISOString() }];
        const pruned = pruneEvaluationTags(next);
        const createdTagId = next[next.length - 1].id;
        if (!pruned.some(tag => tag.id === createdTagId)) return showToast('现有100个标签全部已锁定，无法创建新标签', 'error');
        const result = commitMutation(() => {
          state.studentTags = pruned;
          const student = state.students.find(item => item.id === studentId);
          if (student) {
            student.evaluationTags = Array.isArray(student.evaluationTags) ? student.evaluationTags : [];
            if (!student.evaluationTags.includes(createdTagId)) student.evaluationTags.push(createdTagId);
          }
        }, { successMessage: '评价标签已创建' });
        const finish = value => {
          if (!value.ok) return;
          closeModal();
          openStudentDetail(studentId);
        };
        if (result && typeof result.then === 'function') result.then(finish);
        else finish(result);
      };
    }

    function openTagBatchDeleteModal(studentId, resetSelection = true) {
      prepareModalForSubdialog();
      modalReturnStudentId = studentId;
      if (resetSelection) selectedTagIds.clear();
      const tags = state.studentTags || [];
      const allSelected = tags.length > 0 && tags.every(tag => selectedTagIds.has(tag.id));
      const body = tags.length ? `<div class="tag-batch-toolbar"><label><input type="checkbox" data-act="tag-select-all" data-id="${escapeAttr(studentId)}" ${allSelected ? 'checked' : ''}> 全选</label><span style="color:var(--md-text-secondary);font-size:13px;">${selectedTagIds.size ? `已选择 ${selectedTagIds.size} 项` : `共 ${tags.length} 项`}</span></div><p style="color:var(--md-text-secondary);margin-top:0;">勾选后批量删除或锁定；锁定项不会被删除。</p><div class="tag-batch-list">${tags.map(tag => `<div class="tag-batch-row"><input type="checkbox" data-act="tag-select" data-id="${escapeAttr(studentId)}" data-tag-id="${escapeAttr(tag.id)}" ${selectedTagIds.has(tag.id) ? 'checked' : ''} aria-label="选择评价"><input class="tag-batch-name" type="text" value="${escapeAttr(tag.name)}" readonly aria-label="评价文字">${tag.locked ? '<span class="badge badge-blue">已锁定</span>' : '<span></span>'}<button type="button" class="btn btn-sm" data-act="tag-lock" data-id="${escapeAttr(studentId)}" data-tag-id="${escapeAttr(tag.id)}">${tag.locked ? '解锁' : '锁定'}</button></div>`).join('')}</div>` : '<div class="empty">暂无标签</div>';
      openModal('管理学生评价标签', body, `<button class="btn" onclick="closeModal()">取消</button><button class="btn btn-danger" data-act="tag-batch-confirm" data-id="${escapeAttr(studentId)}">删除选中标签</button>`);
    }

    function toggleEvaluationTag(tagId, studentId) {
      const tag = (state.studentTags || []).find(item => item.id === tagId);
      if (!tag) return;
      const result = commitMutation(() => { tag.locked = !tag.locked; });
      const finish = value => { if (value.ok) openTagBatchDeleteModal(studentId, false); };
      if (result && typeof result.then === 'function') result.then(finish); else finish(result);
    }

    function toggleEvaluationTagInline(tagId, studentId) {
      const tag = (state.studentTags || []).find(item => item.id === tagId);
      if (!tag) return;
      const result = commitMutation(() => { tag.locked = !tag.locked; });
      const finish = value => { if (value.ok) { closeModal(); openStudentDetail(studentId); } };
      if (result && typeof result.then === 'function') result.then(finish); else finish(result);
    }

    function deleteEvaluationTagInline(tagId, studentId) {
      const student = state.students.find(item => item.id === studentId);
      const tag = (state.studentTags || []).find(item => item.id === tagId);
      if (!student || !tag) return;
      if (tag.locked) return showToast('已锁定的评价不能删除，请先解锁', 'error');
      const result = commitMutation(() => {
        student.evaluationTags = (student.evaluationTags || []).filter(item => item !== tagId);
      }, { successMessage: '文字评价已删除' });
      const finish = value => { if (value.ok) { closeModal(false); openStudentDetail(studentId); } };
      if (result && typeof result.then === 'function') result.then(finish); else finish(result);
    }

    function confirmTagBatchDelete(studentId) {
      const selected = new Set(selectedTagIds);
      const removable = new Set((state.studentTags || []).filter(tag => selected.has(tag.id) && !tag.locked).map(tag => tag.id));
      if (!removable.size) return showToast('没有可删除的未锁定标签', 'error');
      const result = commitMutation(() => {
        state.studentTags = (state.studentTags || []).filter(tag => !removable.has(tag.id));
        state.students.forEach(student => { student.evaluationTags = (student.evaluationTags || []).filter(tagId => !removable.has(tagId)); });
        selectedTagIds.clear();
      }, { successMessage: `已批量删除${removable.size}个标签` });
      const finish = value => { if (value.ok) { closeModal(false); openStudentDetail(studentId); } };
      if (result && typeof result.then === 'function') result.then(finish); else finish(result);
    }

    function mergeClassAlias() {
      const sourceInput = document.getElementById('class-merge-source');
      const targetInput = document.getElementById('class-merge-target');
      const source = normalizeClassName(sourceInput?.value);
      const target = resolveClassName(targetInput?.value);
      if (!source || !target) return showToast('请选择来源班级和目标班级', 'error');
      if (source === target) return showToast('来源班级和目标班级不能相同', 'error');
      if (!state.classes.includes(source) || !state.classes.includes(target)) return showToast('班级不存在或已被归档', 'error');

      const result = commitMutation(() => {
        const aliases = normalizeClassAliases(state.classAliases);
        aliases[source] = target;
        state.classAliases = normalizeClassAliases(aliases);
        state.students.forEach(student => { if (normalizeClassName(student.class) === source) student.class = target; });
        state.classes = [...new Set(state.classes.map(cls => resolveClassName(cls)).filter(Boolean))];
        state.exams.forEach(exam => {
          const ranks = exam.classGradeRanks && typeof exam.classGradeRanks === 'object' ? exam.classGradeRanks : {};
          const merged = {};
          Object.entries(ranks).forEach(([className, rank]) => {
            const resolved = resolveClassName(className, state.classAliases);
            if (resolved && merged[resolved] == null) merged[resolved] = rank;
          });
          exam.classGradeRanks = merged;
        });
        if (dictationClass === source) dictationClass = target;
        if (scoreClass === source) scoreClass = target;
      }, { successMessage: `${formatClassLabel(source)}已归档并合并到${formatClassLabel(target)}`, renderNavigation: true });
      return result;
    }

    function unarchiveClassAlias(source) {
      const key = normalizeClassName(source);
      if (!key || !state.classAliases?.[key]) return;
      const result = commitMutation(() => {
        delete state.classAliases[key];
      }, { successMessage: '已解除班级写法映射；已有学生不会被拆分', renderNavigation: true });
      return result;
    }

    // ================= 考试弹窗 =================
    function openExamModal(examId = '') {
      const exam = examId ? state.exams.find(item => item.id === examId) : null;
      const isEdit = Boolean(exam);
      const tierLines = exam?.tierLines || {};
      const fullScore = Number(exam?.fullScore) || 100;
      openModal(isEdit ? '修改考试信息与分层线' : '新增考试', `
        <div class="form-row">
          <div class="form-group"><label>考试名称</label><input id="m-exam-name" value="${escapeAttr(exam?.name || '')}" placeholder="如：9月月考"></div>
          <div class="form-group"><label>考试日期</label><input type="date" id="m-exam-date" value="${escapeAttr(exam?.date || '')}"></div>
        </div>
        <div class="form-row">
          <div class="form-group"><label>满分</label><input type="number" min="1" step="0.5" id="m-exam-full" value="${fullScore}"></div>
          <div class="form-group"><label>考试类型</label><select id="m-exam-type"><option value="english_total" ${exam?.type!=='question_type'?'selected':''}>${subjectHtml('score_total')}</option><option value="question_type" ${exam?.type==='question_type'?'selected':''}>按题型</option></select></div>
        </div>
        <hr style="margin:18px 0;border:0;border-top:1px solid var(--border);">
        <h4 style="margin:0 0 6px;">本次考试分层线</h4>
        <div class="form-row tier-row">
          <div class="form-group"><label>A层线（前25%最低分）</label><input type="number" min="0" max="${fullScore}" step="0.5" id="m-exam-tier-a" value="${tierLines.a ?? ''}" required></div>
          <div class="form-group"><label>B层线（前60%最低分）</label><input type="number" min="0" max="${fullScore}" step="0.5" id="m-exam-tier-b" value="${tierLines.b ?? ''}" required></div>
          <div class="form-group"><label>C层线（前80%最低分）</label><input type="number" min="0" max="${fullScore}" step="0.5" id="m-exam-tier-c" value="${tierLines.c ?? ''}" required></div>
        </div>
      `, `<button class="btn" onclick="closeModal()">取消</button><button class="btn btn-primary" id="m-exam-save">${isEdit ? '保存修改' : '保存'}</button>`);
      document.getElementById('m-exam-full').oninput = event => {
        const value = Number(event.target.value);
        ['m-exam-tier-a', 'm-exam-tier-b', 'm-exam-tier-c'].forEach(id => {
          document.getElementById(id).max = Number.isFinite(value) && value > 0 ? String(value) : '';
        });
      };
      document.getElementById('m-exam-save').onclick = async () => {
        const name = document.getElementById('m-exam-name').value.trim();
        const date = document.getElementById('m-exam-date').value;
        if (!name) return showToast('请输入考试名称', 'error');
        const nextFullScore = Number(document.getElementById('m-exam-full').value);
        if (!Number.isFinite(nextFullScore) || nextFullScore <= 0) return showToast('考试满分必须大于0', 'error');
        const tierValues = ['m-exam-tier-a', 'm-exam-tier-b', 'm-exam-tier-c'].map(id => document.getElementById(id).value.trim());
        if (tierValues.some(value => value === '')) return showToast('请完整填写A、B、C三条分层线', 'error');
        const [a, b, c] = tierValues.map(Number);
        if (![a, b, c].every(Number.isFinite)) return showToast('A、B、C分层线必须是有效数字', 'error');
        if (c < 0 || a > nextFullScore) return showToast(`分层线应在0到${fmt(nextFullScore)}分之间`, 'error');
        if (!(a >= b && b >= c)) return showToast('A层线应不低于B层线，B层线应不低于C层线', 'error');
        const nextTierLines = { a, b, c };
        if (isEdit) {
          const existingScores = Object.values(exam.scores || {}).map(value => Number(value?.英语 ?? value)).filter(Number.isFinite);
          if (existingScores.some(score => score > nextFullScore)) return showToast('新满分不能低于已经录入的学生成绩', 'error');
        }
        const result = await commitMutation(() => {
          if (isEdit) {
            exam.name = name;
            exam.date = date;
            exam.fullScore = nextFullScore;
            exam.type = document.getElementById('m-exam-type').value;
            exam.tierLines = nextTierLines;
            currentExamId = exam.id;
          } else {
            state.exams.unshift({ id: uid(), name, date, fullScore: nextFullScore, type: document.getElementById('m-exam-type').value, examKind: 'regular', scores: {}, tierLines: nextTierLines, classGradeRanks: {} });
            currentExamId = state.exams[0].id;
          }
          state.currentExamId = currentExamId;
        }, { successMessage: isEdit ? '考试信息已更新' : '考试已创建', renderNavigation: true });
        if (result.ok) closeModal();
      };
    }

    function openClassGradeRankModal() {
      const cls = getScoreClass();
      const exam = getCurrentExam();
      if (!exam) return showToast('请先创建考试', 'error');
      if (!cls) return showToast('请先切换到一个具体班级', 'error');
      const current = Number(exam.classGradeRanks?.[resolveClassName(cls)]);
      const currentValue = Number.isInteger(current) && current > 0 ? current : '';
      openModal('设置年级排名', `
        <p style="margin:0 0 18px;color:var(--md-text-secondary);">${escapeHtml(exam.name)} · ${escapeHtml(formatClassLabel(cls))}<br><small>填写这个班本次${escapeHtml(subjectText('score_column'))}在全年级各班中的名次。</small></p>
        <div class="form-group"><label>班级年级名次</label><input id="m-class-grade-rank" type="number" min="1" step="1" inputmode="numeric" value="${currentValue}" placeholder="例如：3"></div>
      `, '<button class="btn" onclick="closeModal()">取消</button><button class="btn btn-primary" id="m-class-grade-rank-save">保存</button>');
      const input = document.getElementById('m-class-grade-rank');
      setTimeout(() => input?.focus(), 0);
      document.getElementById('m-class-grade-rank-save').onclick = () => {
        const value = Number(input.value);
        if (!Number.isInteger(value) || value < 1) return showToast('班级年级名次必须是大于0的整数', 'error');
        const result = commitMutation(() => {
          exam.classGradeRanks = exam.classGradeRanks || {};
          exam.classGradeRanks[resolveClassName(cls)] = value;
        }, { successMessage: `${formatClassLabel(cls)}年级名次已保存` });
        const finish = saved => { if (saved.ok) closeModal(); };
        if (result && typeof result.then === 'function') result.then(finish); else finish(result);
      };
    }

    // ================= 背诵弹窗 =================
    function openReciteModal() {
      const cls = normalizeClassFilter(reciteClass);
      const list = getFilteredStudents(cls);
      openModal('新建背诵任务', `
        <div class="form-row"><div class="form-group"><label>任务名称</label><input id="m-rec-title"></div><div class="form-group"><label>范围</label><input id="m-rec-scope" placeholder="如：Unit 1 课文"></div></div>
        <div style="max-height:400px;overflow:auto;">
          <table><thead><tr><th>姓名</th><th>档位</th><th>重背结果</th></tr></thead><tbody>
            ${list.map(s=>`<tr><td>${escapeHtml(s.name)}</td><td><select class="m-rec-level" data-sid="${escapeAttr(s.id)}" aria-label="${escapeAttr(s.name)}背诵档位"><option value="A">A</option><option value="B">B</option><option value="C">C</option><option value="F">F</option></select></td><td><select class="m-rec-retake" data-sid="${escapeAttr(s.id)}" aria-label="${escapeAttr(s.name)}重背结果" hidden><option value="">请选择</option><option value="passed">通过</option><option value="not_passed">未通过</option></select><span class="m-rec-retake-placeholder" data-sid="${escapeAttr(s.id)}">—</span></td></tr>`).join('')}
          </tbody></table>
        </div>
      `, `<button class="btn" onclick="closeModal()">取消</button><button class="btn btn-primary" id="m-rec-save">保存</button>`);
      const syncRetake = levelSelect => {
        const sid = levelSelect.dataset.sid;
        const retake = [...document.querySelectorAll('.m-rec-retake')].find(el => el.dataset.sid === sid);
        const placeholder = [...document.querySelectorAll('.m-rec-retake-placeholder')].find(el => el.dataset.sid === sid);
        const isF = levelSelect.value === 'F';
        if (retake) retake.hidden = !isF;
        if (placeholder) placeholder.hidden = isF;
        if (!isF && retake) retake.value = '';
      };
      document.querySelectorAll('.m-rec-level').forEach(select => {
        select.addEventListener('change', () => syncRetake(select));
        syncRetake(select);
      });
      document.getElementById('m-rec-save').onclick = async () => {
        const status = {};
        document.querySelectorAll('.m-rec-level').forEach(el => {
          const retake = [...document.querySelectorAll('.m-rec-retake')].find(item => item.dataset.sid === el.dataset.sid);
          status[el.dataset.sid] = { level: el.value, retake: el.value === 'F' ? (retake?.value || '') : '' };
        });
        const task = { id: uid(), title: document.getElementById('m-rec-title').value, scope: document.getElementById('m-rec-scope').value, status };
        const result = await commitMutation(() => {
          state.recitations.push(task);
          reciteTaskId = task.id;
        }, { successMessage: '任务已保存', renderNavigation: true });
        if (result.ok) closeModal();
      };
    }

    // ================= 写作弹窗 =================
    function openWritingModal() {
      openModal('新建写作任务', `
        <div class="form-row"><div class="form-group"><label>任务名称</label><input id="m-wri-title" placeholder="例如：第一单元写作"></div><div class="form-group"><label>日期</label><input type="date" id="m-wri-date"></div><div class="form-group"><label>满分</label><input type="number" min="0.5" step="0.5" id="m-wri-full" value="12"></div></div>
      `, `<button class="btn" onclick="closeModal()">取消</button><button class="btn btn-primary" id="m-wri-save">保存</button>`);
      document.getElementById('m-wri-save').onclick = async () => {
        const title = document.getElementById('m-wri-title').value.trim();
        const date = document.getElementById('m-wri-date').value;
        const fullScore = Number(document.getElementById('m-wri-full').value);
        if (!title) return showToast('请输入任务名称', 'error');
        if (!Number.isFinite(fullScore) || fullScore <= 0) return showToast('满分必须是大于0的数字', 'error');
        const task = { id: uid(), title, date, fullScore, scores: {} };
        const result = await commitMutation(() => {
          state.writings.push(task);
          writingTaskId = task.id;
        }, { successMessage: '空写作任务已创建，请导入或录入成绩', renderNavigation: true });
        if (result.ok) closeModal();
      };
    }

    function openWritingEditModal(id) {
      const task = state.writings.find(item => item.id === id);
      if (!task) return;
      openModal('编辑写作任务', `<div class="form-row"><div class="form-group"><label>任务名称</label><input id="m-wri-title" value="${escapeAttr(task.title)}"></div><div class="form-group"><label>日期</label><input type="date" id="m-wri-date" value="${escapeAttr(task.date || '')}"></div><div class="form-group"><label>满分</label><input type="number" min="0.5" step="0.5" id="m-wri-full" value="${escapeAttr(task.fullScore || 12)}"></div></div>`, `<button class="btn" onclick="closeModal()">取消</button><button class="btn btn-primary" id="m-wri-save">保存</button>`);
      document.getElementById('m-wri-save').onclick = async () => {
        const title = document.getElementById('m-wri-title').value.trim();
        const fullScore = Number(document.getElementById('m-wri-full').value);
        if (!title) return showToast('请输入任务名称', 'error');
        if (!Number.isFinite(fullScore) || fullScore <= 0) return showToast('满分必须是大于0的数字', 'error');
        const result = await commitMutation(() => {
          task.title = title;
          task.date = document.getElementById('m-wri-date').value;
          task.fullScore = fullScore;
        }, { successMessage: '写作任务信息已更新', renderNavigation: true });
        if (result.ok) closeModal();
      };
    }

    // ================= 错题弹窗 =================
    function openErrorModal() {
      openModal('录入高频错题', `
        <div class="form-row">
          <div class="form-group"><label>题号</label><input id="m-err-qnum"></div>
          <div class="form-group"><label>题型</label><select id="m-err-type">${subjectQuestionTypes().map(t=>`<option value="${escapeAttr(t)}">${escapeHtml(t)}</option>`).join('')}</select></div>
        </div>
        <div class="form-row"><div class="form-group"><label>考点</label><input id="m-err-point"></div><div class="form-group"><label>错误人数</label><input type="number" id="m-err-count"></div></div>
        <div class="form-row"><div class="form-group"><label>关联原卷</label><select id="m-err-document"><option value="">不关联原卷</option>${(state.paperDocuments || []).map(item => `<option value="${escapeAttr(item.id)}" ${item.id === errorSelectedDocumentId ? 'selected' : ''}>${escapeHtml(item.name)}</option>`).join('')}</select></div><div class="form-group"><label>原卷页码</label><input type="number" min="1" id="m-err-page" placeholder="例如：3"></div></div>
        <div class="form-group"><label>典型错因</label><textarea id="m-err-reason" rows="2"></textarea></div>
        <div class="form-group"><label>讲评要点</label><textarea id="m-err-key" rows="2"></textarea></div>
      `, `<button class="btn" onclick="closeModal()">取消</button><button class="btn btn-primary" id="m-err-save">保存</button>`);
      document.getElementById('m-err-save').onclick = async () => {
        const errorItem = { id: uid(), qnum: document.getElementById('m-err-qnum').value.trim(), type: document.getElementById('m-err-type').value, point: document.getElementById('m-err-point').value.trim(), count: parseInt(document.getElementById('m-err-count').value, 10) || 0, documentId: document.getElementById('m-err-document').value, page: parseInt(document.getElementById('m-err-page').value, 10) || '', reason: document.getElementById('m-err-reason').value.trim(), key: document.getElementById('m-err-key').value.trim() };
        const result = await commitMutation(() => state.errors.push(errorItem), { successMessage: '错题已录入', renderNavigation: true });
        if (result.ok) closeModal();
      };
    }

    // ================= 待办弹窗 =================
    function openTodoModal(todoId = '') {
      ensureTodoDate();
      const existing = todoId ? state.todos.find(item => item.id === todoId) : null;
      const date = existing?.date || existing?.deadline || todoSelectedDate;
      const repeat = existing?.repeatRule || {};
      const weekdays = Array.isArray(repeat.weekdays) ? repeat.weekdays : [];
      openModal(existing ? '编辑待办' : '新增待办', `
        <div class="form-row"><div class="form-group"><label>事项标题</label><input id="m-todo-title" maxlength="100" value="${escapeAttr(existing?.title || '')}" placeholder="例如：批改月考试卷"></div><div class="form-group"><label>优先级</label><select id="m-todo-priority"><option value="高" ${existing?.priority === '高' ? 'selected' : ''}>高</option><option value="中" ${!existing || existing?.priority === '中' ? 'selected' : ''}>中</option><option value="低" ${existing?.priority === '低' ? 'selected' : ''}>低</option></select></div></div>
        <div class="form-row"><div class="form-group"><label>日期</label><input type="date" id="m-todo-date" value="${escapeAttr(date)}"></div><div class="form-group"><label>开始时间（可不填，留空表示全天）</label><input type="time" id="m-todo-time" value="${escapeAttr(existing?.startTime || existing?.time || '')}"></div><div class="form-group"><label>结束时间</label><input type="time" id="m-todo-end-time" value="${escapeAttr(existing?.endTime || '')}"></div></div>
        <div class="form-group"><label><input type="checkbox" id="m-todo-weekly" ${repeat.frequency === 'weekly' ? 'checked' : ''}> 每周重复</label><div class="todo-repeat-fields"><span>星期：</span>${['日','一','二','三','四','五','六'].map((day, index) => `<label><input type="checkbox" class="m-todo-weekday" value="${index}" ${weekdays.includes(index) ? 'checked' : ''}>${day}</label>`).join('')}<input type="date" id="m-todo-repeat-start" value="${escapeAttr(repeat.startDate || date)}"><input type="date" id="m-todo-repeat-end" value="${escapeAttr(repeat.endDate || date)}"></div></div>
        <div class="form-group"><label>备注</label><textarea id="m-todo-notes" rows="3" maxlength="300" placeholder="记录班级、材料或其他提醒">${escapeHtml(existing?.notes || '')}</textarea></div>
      `, `<button class="btn" onclick="closeModal()">取消</button><button class="btn btn-primary" id="m-todo-save">保存</button>`);
      document.querySelector('#modal .modal-content')?.classList.add('todo-edit-modal');
      document.getElementById('m-todo-save').onclick = () => {
        const title = document.getElementById('m-todo-title').value.trim();
        const todoDate = document.getElementById('m-todo-date').value;
        const time = document.getElementById('m-todo-time').value;
        const endTime = document.getElementById('m-todo-end-time').value;
        const weekly = document.getElementById('m-todo-weekly').checked;
        const repeatWeekdays = [...document.querySelectorAll('.m-todo-weekday:checked')].map(input => Number(input.value));
        const repeatStart = document.getElementById('m-todo-repeat-start').value || todoDate;
        const repeatEnd = document.getElementById('m-todo-repeat-end').value || repeatStart;
        if (!title) return showToast('请填写事项标题', 'error');
        if (!/^\d{4}-\d{2}-\d{2}$/.test(todoDate)) return showToast('请选择有效日期', 'error');
        if (time && !/^\d{2}:\d{2}$/.test(time)) return showToast('请填写有效时间', 'error');
        if (endTime && !time) return showToast('填写结束时间前请先填写开始时间', 'error');
        if (time && endTime && endTime <= time) return showToast('结束时间必须晚于开始时间', 'error');
        if (weekly && (!repeatWeekdays.length || !/^\d{4}-\d{2}-\d{2}$/.test(repeatStart) || !/^\d{4}-\d{2}-\d{2}$/.test(repeatEnd) || repeatEnd < repeatStart)) return showToast('请完整设置每周重复的星期和日期范围', 'error');
        const payload = { title, priority: document.getElementById('m-todo-priority').value, date: todoDate, deadline: todoDate, time, startTime: time, endTime, allDay: !time, repeatRule: weekly ? { frequency: 'weekly', weekdays: repeatWeekdays, startDate: repeatStart, endDate: repeatEnd } : null, notes: document.getElementById('m-todo-notes').value.trim() };
        const result = commitMutation(() => {
          if (existing) Object.assign(existing, payload);
          else state.todos.push({ id: uid(), ...payload, done: false, createdAt: new Date().toISOString() });
          todoSelectedDate = todoDate;
          const selected = parseLocalDate(todoDate);
          if (selected) todoCalendarDate = new Date(selected.getFullYear(), selected.getMonth(), 1);
        }, { successMessage: existing ? '待办已更新' : '待办已添加', renderNavigation: true });
        if (result && typeof result.then === 'function') result.then(saved => { if (saved.ok) closeModal(); });
        else if (result.ok) closeModal();
      };
    }

    function normalizeTodoImportDate(value) {
      const raw = String(value || '').trim();
      const match = raw.match(/^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$/);
      if (!match) return '';
      const date = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
      if (date.getFullYear() !== Number(match[1]) || date.getMonth() !== Number(match[2]) - 1 || date.getDate() !== Number(match[3])) return '';
      return `${match[1]}-${pad2(match[2])}-${pad2(match[3])}`;
    }

    function isAllowedTodoImportFile(file) {
      return Boolean(file && /\.(csv|txt)$/i.test(String(file.name || '')));
    }

    function parseTodoImportRows(text) {
      const lines = String(text || '').split(/\r?\n/).map(line => line.trim()).filter(Boolean);
      if (lines.length && /名称|日程|weekday|星期/i.test(lines[0])) lines.shift();
      return lines.map((line, index) => {
        const parts = line.split(/[\t,，|｜]/).map(value => value.trim());
        const [title, weekdayRaw, startTime = '', endTime = '', className = '', startDate = '', endDate = '', notes = ''] = parts;
        const weekdayText = String(weekdayRaw || '').replace(/^星期/, '').replace(/^周/, '');
        const weekdayMap = { 日: 0, 天: 0, 一: 1, 二: 2, 三: 3, 四: 4, 五: 5, 六: 6 };
        const weekday = Object.prototype.hasOwnProperty.call(weekdayMap, weekdayText) ? weekdayMap[weekdayText] : (/^[0-6]$/.test(weekdayText) ? Number(weekdayText) : NaN);
        const normalizedStartDate = normalizeTodoImportDate(startDate);
        const normalizedEndDate = normalizeTodoImportDate(endDate);
        const hasExplicitDate = Boolean(startDate || endDate);
        const errors = [];
        if (!title) errors.push('缺少名称');
        if (!Number.isInteger(weekday) || weekday < 0 || weekday > 6) {
          if (!(normalizedStartDate && normalizedEndDate && normalizedStartDate === normalizedEndDate)) errors.push('星期无效');
        }
        if (startTime && !/^\d{2}:\d{2}$/.test(startTime)) errors.push('开始时间无效');
        if (endTime && !startTime) errors.push('填写结束时间前请填写开始时间');
        if (endTime && (!/^\d{2}:\d{2}$/.test(endTime) || endTime <= startTime)) errors.push('结束时间无效');
        if (hasExplicitDate && (!normalizedStartDate || !normalizedEndDate || normalizedEndDate < normalizedStartDate)) errors.push('日期范围无效');
        return { title, weekday, startTime, endTime, className, startDate: normalizedStartDate, endDate: normalizedEndDate, notes, errors, line: index + 1 };
      });
    }

    function todoImportRowHtml(row = {}) {
      const weekdayOptions = ['周日','周一','周二','周三','周四','周五','周六'].map((label, index) => `<option value="${index}" ${Number(row.weekday) === index ? 'selected' : ''}>${label}</option>`).join('');
      return `<div class="todo-import-row" data-class-name="${escapeAttr(row.className || '全部班级')}">
        <div class="todo-import-row-number" aria-hidden="true">1</div>
        <label><span class="sr-only">课程或日程名称</span><input class="todo-import-title" maxlength="100" value="${escapeAttr(row.title || '')}" placeholder="例如：英语早读"></label>
        <label><span class="sr-only">星期</span><select class="todo-import-weekday"><option value="">选择星期</option>${weekdayOptions}</select></label>
        <label><span class="sr-only">开始时间</span><input class="todo-import-start" type="time" value="${escapeAttr(row.startTime || '')}"></label>
        <label><span class="sr-only">结束时间</span><input class="todo-import-end" type="time" value="${escapeAttr(row.endTime || '')}"></label>
        <label class="todo-import-date-range"><span class="todo-import-date-field"><span>开始日期</span><input class="todo-import-start-date" type="date" value="${escapeAttr(row.startDate || '')}"></span><span class="todo-import-date-field"><span>结束日期</span><input class="todo-import-end-date" type="date" value="${escapeAttr(row.endDate || '')}"></span></label>
        <label><span class="sr-only">备注</span><input class="todo-import-notes" maxlength="200" value="${escapeAttr(row.notes || '')}" placeholder="班级/教室（可选）"></label>
        <div class="todo-import-row-actions"><button class="icon-btn" data-act="todo-import-duplicate-row" type="button" aria-label="复制这一行" title="复制"><span class="material-symbols-rounded" aria-hidden="true">content_copy</span></button><button class="icon-btn danger-text" data-act="todo-import-remove-row" type="button" aria-label="删除这一行" title="删除"><span class="material-symbols-rounded" aria-hidden="true">delete</span></button></div>
        <div class="todo-import-row-error" aria-live="polite"></div>
      </div>`;
    }

    function renumberTodoImportRows() {
      document.querySelectorAll('.todo-import-row').forEach((row, index) => {
        const number = row.querySelector('.todo-import-row-number');
        if (number) number.textContent = String(index + 1);
      });
    }

    function addTodoImportRow(row = {}) {
      const editor = document.getElementById('todo-import-editor');
      if (!editor) return;
      editor.insertAdjacentHTML('beforeend', todoImportRowHtml(row));
      renumberTodoImportRows();
      editor.querySelector('.todo-import-row:last-child .todo-import-title')?.focus();
      previewTodoImport(true);
    }

    function duplicateTodoImportRow(source) {
      if (!source) return;
      const weekdayValue = source.querySelector('.todo-import-weekday')?.value || '';
      addTodoImportRow({
        title: source.querySelector('.todo-import-title')?.value || '',
        weekday: /^[0-6]$/.test(weekdayValue) ? Number(weekdayValue) : '',
        startTime: source.querySelector('.todo-import-start')?.value || '',
        endTime: source.querySelector('.todo-import-end')?.value || '',
        startDate: source.querySelector('.todo-import-start-date')?.value || '',
        endDate: source.querySelector('.todo-import-end-date')?.value || '',
        notes: source.querySelector('.todo-import-notes')?.value || ''
      });
    }

    function removeTodoImportRow(source) {
      if (!source) return;
      const editor = document.getElementById('todo-import-editor');
      if (editor?.children.length === 1) {
        source.querySelectorAll('input, select').forEach(field => { field.value = ''; });
      } else source.remove();
      renumberTodoImportRows();
      previewTodoImport(true);
    }

    function openTodoImportModal() {
      todoImportDraft = null;
      openModal('批量导入日程', `<div class="todo-import-flow">
        <section class="todo-import-section"><div class="todo-import-section-head"><span class="todo-step">1</span><div><h4>填写日程</h4></div></div><div class="todo-import-grid-scroll"><div class="todo-import-table-head"><span>#</span><span>课程/日程名称</span><span>星期</span><span>开始</span><span>结束</span><span>每行日期范围</span><span>备注</span><span>操作</span></div><div id="todo-import-editor" class="todo-import-editor">${todoImportRowHtml()}</div></div><button class="todo-import-add-row" data-act="todo-import-add-row" type="button"><span class="material-symbols-rounded" aria-hidden="true">add</span>添加一节课</button></section>
        <details class="todo-import-advanced"><summary><span class="material-symbols-rounded" aria-hidden="true">upload_file</span>从 CSV 或文本快速填充</summary><div class="todo-import-advanced-body"><p>仅支持 CSV 或 TXT 文件。列顺序：名称、星期、开始、结束、班级、开始日期、结束日期、备注。开始日期和结束日期相同，就是单日事项；不同则按星期重复。</p><div class="todo-import-file-row"><input type="file" id="todo-import-file" accept=".csv,.txt"><button class="btn btn-sm btn-secondary" data-act="todo-import-apply-text" type="button">应用粘贴内容</button></div><textarea id="m-todo-import-text" rows="5" placeholder="英语早读｜周一｜07:20｜07:50｜任教班级｜2026-08-17｜2026-12-31｜教学楼A201"></textarea></div></details>
        <div id="todo-import-preview" class="todo-import-preview" aria-live="polite"></div>
      </div>`, '<button class="btn" onclick="closeModal()">取消</button><button class="btn btn-primary" data-act="todo-import-confirm" disabled><span class="material-symbols-rounded" aria-hidden="true">playlist_add_check</span>确认导入</button>');
      document.querySelector('#modal .modal-content')?.classList.add('todo-import-modal');
      renumberTodoImportRows();
      previewTodoImport(true);
    }

    function collectTodoImportDraft() {
      return [...document.querySelectorAll('.todo-import-row')].map((element, index) => {
        const title = element.querySelector('.todo-import-title')?.value.trim() || '';
        const weekdayRaw = element.querySelector('.todo-import-weekday')?.value || '';
        const startTime = element.querySelector('.todo-import-start')?.value || '';
        const endTime = element.querySelector('.todo-import-end')?.value || '';
        const rowStartDate = element.querySelector('.todo-import-start-date')?.value || '';
        const rowEndDate = element.querySelector('.todo-import-end-date')?.value || '';
        const notes = element.querySelector('.todo-import-notes')?.value.trim() || '';
        const weekday = /^[0-6]$/.test(weekdayRaw) ? Number(weekdayRaw) : NaN;
        const completelyBlank = !title && !weekdayRaw && !startTime && !endTime && !rowStartDate && !rowEndDate && !notes;
        const hasExplicitDate = Boolean(rowStartDate || rowEndDate);
        const resolvedStartDate = rowStartDate;
        const resolvedEndDate = rowEndDate;
        const className = element.dataset.className || '全部班级';
        const errors = [];
        if (!completelyBlank) {
          if (!title) errors.push('请填写名称');
          if (!Number.isInteger(weekday) && !(hasExplicitDate && rowStartDate === rowEndDate)) errors.push('请选择星期');
          if (startTime && !/^\d{2}:\d{2}$/.test(startTime)) errors.push('开始时间无效');
          if (endTime && !startTime) errors.push('填写结束时间前请填写开始时间');
          if (endTime && endTime <= startTime) errors.push('结束时间须晚于开始时间');
          if (!/^\d{4}-\d{2}-\d{2}$/.test(resolvedStartDate) || !/^\d{4}-\d{2}-\d{2}$/.test(resolvedEndDate) || resolvedEndDate < resolvedStartDate) errors.push('请填写有效的每行日期范围');
        }
        return { title, weekday, startTime, endTime, className, startDate: resolvedStartDate, endDate: resolvedEndDate, rowStartDate, rowEndDate, notes, errors, line: index + 1, element, completelyBlank, hasExplicitDate };
      }).filter(row => !row.completelyBlank);
    }

    function previewTodoImport(silent = false) {
      const draft = collectTodoImportDraft();
      todoImportDraft = draft;
      const preview = document.getElementById('todo-import-preview');
      if (!preview) return;
      document.querySelectorAll('.todo-import-row').forEach(row => { row.classList.remove('invalid'); const error = row.querySelector('.todo-import-row-error'); if (error) error.textContent = ''; });
      draft.forEach(row => {
        row.element.classList.toggle('invalid', Boolean(row.errors.length));
        const error = row.element.querySelector('.todo-import-row-error');
        if (error) error.textContent = row.errors.join(' · ');
      });
      const invalidCount = draft.filter(row => row.errors.length).length;
      const oneOffCount = draft.filter(row => row.startDate === row.endDate).length;
      const repeatCount = draft.length - oneOffCount;
      const modeText = [oneOffCount ? `${oneOffCount} 项单日事项` : '', repeatCount ? `${repeatCount} 项每周重复` : ''].filter(Boolean).join(' · ');
      preview.innerHTML = draft.length ? `<div class="todo-import-summary ${invalidCount ? 'has-errors' : 'ready'}"><span class="material-symbols-rounded" aria-hidden="true">${invalidCount ? 'error' : 'check_circle'}</span><div><strong>${invalidCount ? `还有 ${invalidCount} 行需要修改` : `已准备好 ${draft.length} 项日程`}</strong><p>${invalidCount ? '请查看上方红色提示，修正后即可导入。' : `${modeText} · ${escapeHtml(draft[0].className)}`}</p></div></div>` : '<div class="todo-import-summary"><span class="material-symbols-rounded" aria-hidden="true">edit_calendar</span><div><strong>请至少填写一项日程</strong><p>从第一行开始填写，或使用上方的文件/文本导入。</p></div></div>';
      document.querySelector('[data-act="todo-import-confirm"]')?.toggleAttribute('disabled', !draft.length || Boolean(invalidCount));
      if (!silent && invalidCount) showToast(`请先修改 ${invalidCount} 行错误`, 'error');
    }

    function applyTodoImportText() {
      const selectedFile = document.getElementById('todo-import-file')?.files?.[0];
      if (selectedFile && !isAllowedTodoImportFile(selectedFile)) {
        const fileInput = document.getElementById('todo-import-file');
        if (fileInput) fileInput.value = '';
        return showToast('日程导入仅支持 CSV 或 TXT 文件', 'error');
      }
      const parsed = parseTodoImportRows(document.getElementById('m-todo-import-text')?.value || '');
      if (!parsed.length) return showToast('请先选择文件或粘贴课表内容', 'error');
      const editor = document.getElementById('todo-import-editor');
      if (!editor) return;
      editor.innerHTML = parsed.map(row => todoImportRowHtml(row)).join('');
      renumberTodoImportRows();
      previewTodoImport(true);
      showToast(`已填充 ${parsed.length} 行，请确认后导入`, 'success');
    }

    function confirmTodoImport() {
      previewTodoImport(true);
      if (!todoImportDraft?.length || todoImportDraft.some(row => row.errors.length)) return previewTodoImport();
      const result = commitMutation(() => {
        todoImportDraft.forEach(row => {
          const isOneOff = row.startDate === row.endDate;
          // 课表导出常用 00:00–23:59 表示全天；保留其他时间段的精确时分。
          const allDay = !row.startTime || (row.startTime === '00:00' && row.endTime === '23:59');
          const startTime = allDay ? '' : row.startTime;
          const endTime = allDay ? '' : row.endTime;
          state.todos.push({ id: uid(), title: row.title, priority: '中', date: row.startDate, deadline: row.startDate, time: startTime, startTime, endTime, allDay, notes: row.notes, done: false, createdAt: new Date().toISOString(), repeatRule: isOneOff ? null : { frequency: 'weekly', weekdays: [row.weekday], startDate: row.startDate, endDate: row.endDate, className: row.className } });
        });
      }, { successMessage: `已导入 ${todoImportDraft.length} 条日程（${todoImportDraft.filter(row => row.startDate === row.endDate).length} 条单日，${todoImportDraft.filter(row => row.startDate !== row.endDate).length} 条每周重复）` });
      if (result && typeof result.then === 'function') result.then(saved => { if (saved.ok) closeModal(); });
      else if (result.ok) closeModal();
    }

    // --- B3-10：AI 模型配置（Provider 设置页） ---

    let savedModelProfilesCache = [];

    function aiStatusEl() {
      if (typeof document === 'undefined' || !document.getElementById) return null;
      return document.getElementById('ai-status');
    }
    function withApiTimeout(promise, ms) {
      return Promise.race([promise, new Promise(resolve => setTimeout(() => resolve(null), ms || 1500))]);
    }

    function aiStatus(text, isError) {
      const el = aiStatusEl();
      if (el) {
        el.textContent = text;
        el.style.color = isError ? 'var(--danger, #c5221f)' : 'var(--md-text-secondary)';
      }
    }

    function syncTeachMateModelSettings(modelsPayload, providerInfo) {
      if (!window.teachMateState) return;
      if (modelsPayload && typeof window.teachMateState.setSavedModels === 'function') {
        window.teachMateState.setSavedModels(modelsPayload);
      }
      if (providerInfo && typeof window.teachMateState.setProviderInfo === 'function') {
        window.teachMateState.setProviderInfo(providerInfo);
      }
      // 设置弹窗不会随着主页面 render 重建，模型接口操作完成后主动刷新当前模型页。
      if (typeof tmRenderAgentSettingsTab === 'function' && document.querySelector('.tm-settings-shell') && typeof tmSettingsTab !== 'undefined' && tmSettingsTab === 'models') {
        tmRenderAgentSettingsTab('models');
      }
    }

    async function loadProviderStatusUi() {
      try {
        const info = await withApiTimeout(teachMateApi.getProviderInfo());
        let modelsPayload = null;
        try { modelsPayload = await withApiTimeout(teachMateApi.listModelProfiles()); } catch (e) {}
        if (modelsPayload) {
          savedModelProfilesCache = Array.isArray(modelsPayload) ? modelsPayload : (modelsPayload.models || []);
          renderSavedModelsUi(modelsPayload);
          syncTeachMateModelSettings(modelsPayload, info);
        } else if (info) {
          syncTeachMateModelSettings(null, info);
        }
        let runtime = null;
        try { runtime = await withApiTimeout(teachMateApi.getProviderRuntimeStatus()); } catch (e) { /* 旧后端无该接口时忽略 */ }
        if (runtime) {
          const parts = [
            (runtime.key_configured ? 'API Key 已配置' : 'API Key 未配置'),
            `模式 ${runtime.mode}`,
            `模型 ${runtime.model_name || '-'}`,
          ];
          if (runtime.configured) parts.push(`配置 v${runtime.config_version}`);
          parts.push(runtime.running ? `运行中 PID ${runtime.pid ?? '-'}` : (runtime.configured ? '未运行' : '未启动'));
          if (runtime.running && runtime.restart_count > 0) parts.push(`重启 ${runtime.restart_count} 次`);
          if (runtime.last_error) parts.push('最近错误: ' + runtime.last_error.slice(0, 60));
          aiStatus(parts.join(' · '), !!runtime.last_error);
        } else {
          aiStatus('运行状态不可用（可能为旧版后端）');
        }
      } catch (e) {
        if (aiStatusEl()) aiStatus('读取运行状态失败：' + (e?.message || e), true);
      }
    }

    function renderSavedModelsUi(payload) {
      const el = document.getElementById('ai-saved-models');
      if (!el) return;
      const models = payload?.models || [];
      const currentId = payload?.current_id || '';
      if (!models.length) {
        el.innerHTML = '<div style="padding:18px;border:1px dashed var(--border);border-radius:10px;color:var(--md-text-secondary);">还没有保存模型，点击右上角“添加模型”。</div>';
        return;
      }
      el.innerHTML = models.map(model => `
        <div style="display:flex;align-items:center;gap:14px;padding:14px 4px;border-bottom:1px solid var(--border);">
          <span class="material-symbols-rounded" style="font-size:28px;color:var(--md-primary);">add_circle_outline</span>
          <div style="min-width:0;flex:1;"><strong>${escapeHtml(model.display_name)}</strong>${model.id === currentId ? '<span style="margin-left:8px;color:var(--md-primary);font-size:12px;">使用中</span>' : ''}<div style="font-size:13px;color:var(--md-text-secondary);margin-top:4px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${escapeHtml(model.model_name)} · ${escapeHtml(model.provider)} · ${model.api_key_configured ? 'API Key 已配置' : '未配置 API Key'}</div></div>
          <button class="btn btn-secondary" data-act="model-activate" data-id="${escapeAttr(model.id)}" ${model.id === currentId ? 'disabled' : ''}>${model.id === currentId ? '当前使用' : '使用'}</button>
          <button class="btn btn-secondary" data-act="model-edit" data-id="${escapeAttr(model.id)}">编辑</button>
          <button class="btn btn-secondary" data-act="model-delete" data-id="${escapeAttr(model.id)}">删除</button>
        </div>`).join('');
    }

    function openModelProfileModal(profileId) {
      const stateModels = window.teachMateState && typeof window.teachMateState.getSnapshot === 'function'
        ? window.teachMateState.getSnapshot().savedModels
        : [];
      const models = savedModelProfilesCache.length ? savedModelProfilesCache : (Array.isArray(stateModels) ? stateModels : []);
      const model = models.find(item => String(item.id) === String(profileId)) || {};
      const provider = model.provider || 'deepseek';
      const endpoint = model.endpoint || model.base_url || (provider === 'zhipu' ? 'https://open.bigmodel.cn/api/paas/v4/chat/completions' : '');
      const builtin = !!model.builtin;
      const deepseekModels = ['deepseek-v4-pro', 'deepseek-v4-flash', 'deepseek-chat', 'deepseek-reasoner'];
      if (provider === 'deepseek' && model.model_name && !deepseekModels.includes(model.model_name)) deepseekModels.unshift(model.model_name);
      const deepseekOptions = deepseekModels.map(name => `<option value="${escapeAttr(name)}" ${name === model.model_name ? 'selected' : ''}>${escapeHtml(name)}</option>`).join('');
      const effort = model.reasoning_effort || (model.supports_reasoning ? 'high' : 'disabled');
      const effortOptions = reasoningEffortOptions(provider, effort);
      const body = `<div class="form-group"><label>显示名称</label><input id="model-profile-display-name" value="${escapeAttr(model.display_name || '')}" placeholder="例如：智谱 GLM Flash"></div>
        <div class="form-group"><label>提供商</label><select id="model-profile-provider" ${builtin ? 'disabled' : ''}><option value="deepseek" ${provider === 'deepseek' ? 'selected' : ''}>DeepSeek</option><option value="zhipu" ${provider === 'zhipu' ? 'selected' : ''}>智谱 GLM</option><option value="openai_compat" ${provider === 'openai_compat' ? 'selected' : ''}>自定义 / OpenAI 兼容</option></select></div>
        <div class="form-group" id="model-profile-endpoint-group" style="${provider === 'deepseek' ? 'display:none;' : ''}"><label>接口地址</label><input id="model-profile-endpoint" value="${escapeAttr(endpoint)}" placeholder="https://host/v1/chat/completions" ${builtin ? 'readonly' : ''}></div>
        <div class="form-group"><label>API Key <span style="color:var(--md-text-secondary);font-size:12px;">${model.api_key_configured ? '已配置，留空则保留' : '尚未配置，填写后保存在本机'}</span></label><input type="password" id="model-profile-api-key" autocomplete="new-password" placeholder="${model.api_key_configured ? '已配置（不会回显），如需更换请重新输入' : '请输入智谱 API Key'}"></div>
        <div class="form-group" id="model-profile-deepseek-model-group" style="${provider === 'deepseek' ? '' : 'display:none;'}"><label>模型</label><select id="model-profile-deepseek-model">${deepseekOptions}</select></div>
        <div class="form-group" id="model-profile-custom-model-group" style="${provider !== 'deepseek' ? '' : 'display:none;'}"><label>模型 ID</label><input id="model-profile-model-name" value="${escapeAttr(model.model_name || '')}" placeholder="${provider === 'zhipu' ? '例如：glm-5.3-flash' : '例如：kimi-k3'}" ${builtin ? 'readonly' : ''}></div>
        <div class="form-group"><label>思考深度</label><select id="model-profile-reasoning-effort">${effortOptions}</select><div id="model-profile-effort-hint" style="color:var(--md-text-secondary);font-size:12px;margin-top:5px;">${provider === 'deepseek' ? 'DeepSeek 将真实发送 thinking 和 reasoning_effort 参数。' : provider === 'zhipu' ? 'GLM-5.3 将真实发送 thinking 和 reasoning_effort 参数。' : '中转站将按 OpenAI 兼容协议真实发送 reasoning_effort 参数。'}</div></div>
        <div style="display:flex;gap:18px;margin-top:14px;font-size:13px;"><label><input type="checkbox" id="model-profile-tools" ${model.supports_tool_calls !== false ? 'checked' : ''}> 工具调用</label><label><input type="checkbox" id="model-profile-vision" ${model.supports_vision ? 'checked' : ''}> 图片输入</label><label><input type="checkbox" id="model-profile-reasoning" ${provider === 'deepseek' || provider === 'zhipu' || model.supports_reasoning ? 'checked' : ''}> 推理模式</label></div>
        <p id="model-profile-provider-hint" style="color:var(--md-text-secondary);font-size:12px;margin:14px 0 0;">DeepSeek 使用官方接口，无需填写 URL；模型选择会作为实际 model ID 发送。</p>`;
      const footer = `<button class="btn btn-secondary" data-act="modal-close">取消</button><button class="btn btn-secondary" data-act="model-test">测试连接</button><button class="btn btn-primary" data-act="model-save" data-id="${escapeAttr(model.id || '')}">保存并应用</button>`;
      openModal(model.id ? '编辑模型' : '添加模型', body, footer);
      const modal = document.getElementById('modal');
      if (modal) modal.dataset.modelProfileId = model.id || '';
    }

    function reasoningEffortOptions(provider, selected) {
      const options = provider === 'deepseek'
        ? [['disabled', '关闭思考'], ['high', '高'], ['max', '最大']]
        : provider === 'zhipu'
          ? [['disabled', '关闭思考'], ['low', '低'], ['high', '高'], ['max', '最大']]
        : [['disabled', '关闭思考'], ['low', '低'], ['medium', '中'], ['high', '高']];
      const current = options.some(item => item[0] === selected) ? selected : 'high';
      return options.map(item => `<option value="${item[0]}" ${item[0] === current ? 'selected' : ''}>${item[1]}</option>`).join('');
    }

    function syncModelProfileProviderFields() {
      const provider = document.getElementById('model-profile-provider')?.value || 'deepseek';
      const endpointGroup = document.getElementById('model-profile-endpoint-group');
      const deepseekGroup = document.getElementById('model-profile-deepseek-model-group');
      const customGroup = document.getElementById('model-profile-custom-model-group');
      const hint = document.getElementById('model-profile-provider-hint');
      const effortSelect = document.getElementById('model-profile-reasoning-effort');
      const effortHint = document.getElementById('model-profile-effort-hint');
      if (endpointGroup) endpointGroup.style.display = provider === 'deepseek' ? 'none' : '';
      if (deepseekGroup) deepseekGroup.style.display = provider === 'deepseek' ? '' : 'none';
      if (customGroup) customGroup.style.display = provider === 'deepseek' ? 'none' : '';
      const endpointInput = document.getElementById('model-profile-endpoint');
      if (provider === 'zhipu' && endpointInput && !endpointInput.value.trim()) endpointInput.value = 'https://open.bigmodel.cn/api/paas/v4/chat/completions';
      const modelInput = document.getElementById('model-profile-model-name');
      if (provider === 'zhipu' && modelInput && !modelInput.value.trim()) modelInput.value = 'glm-5.3-flash';
      if (hint) hint.textContent = provider === 'deepseek'
        ? 'DeepSeek 使用官方接口，无需填写 URL；模型选择会作为实际 model ID 发送。'
        : provider === 'zhipu'
          ? '智谱使用官方 /api/paas/v4/chat/completions 接口，支持 image_url 和 thinking。'
          : '自定义接入使用 OpenAI 兼容协议，请填写完整接口地址和服务商提供的模型 ID。';
      if (effortSelect) {
        const current = effortSelect.value;
        effortSelect.innerHTML = reasoningEffortOptions(provider, current);
      }
      if (effortHint) effortHint.textContent = provider === 'deepseek'
        ? 'DeepSeek 将真实发送 thinking 和 reasoning_effort 参数。'
        : provider === 'zhipu'
          ? 'GLM-5.3 将真实发送 thinking 和 reasoning_effort 参数。'
          : '中转站将按 OpenAI 兼容协议真实发送 reasoning_effort 参数。';
    }

    function modelProfilePayload(apply) {
      const modal = document.getElementById('modal');
      const provider = document.getElementById('model-profile-provider')?.value || 'deepseek';
      return {
        id: modal?.dataset.modelProfileId || undefined,
        display_name: (document.getElementById('model-profile-display-name')?.value || '').trim(),
        provider,
        endpoint: (document.getElementById('model-profile-endpoint')?.value || '').trim(),
        model_name: (provider === 'deepseek'
          ? (document.getElementById('model-profile-deepseek-model')?.value || '')
          : (document.getElementById('model-profile-model-name')?.value || '')).trim(),
        api_key: (document.getElementById('model-profile-api-key')?.value || '').trim() || undefined,
        supports_tool_calls: !!document.getElementById('model-profile-tools')?.checked,
        supports_reasoning: provider === 'deepseek' || provider === 'zhipu' ? true : !!document.getElementById('model-profile-reasoning')?.checked,
        supports_vision: !!document.getElementById('model-profile-vision')?.checked,
        thinking_enabled: (document.getElementById('model-profile-reasoning-effort')?.value || 'high') !== 'disabled',
        reasoning_effort: document.getElementById('model-profile-reasoning-effort')?.value || 'high',
        apply: apply !== false,
      };
    }

    async function testModelProfileUi() {
      const p = modelProfilePayload(false);
      if (!p.model_name || (p.provider !== 'deepseek' && !p.endpoint)) return showToast(p.provider === 'deepseek' ? '请先选择模型' : '请先填写接口地址和模型 ID', 'error');
      try {
        const result = await teachMateApi.testProviderConnection({ provider: p.provider, base_url: p.endpoint, model_name: p.model_name, api_key: p.api_key, profile_id: p.id, thinking_enabled: p.thinking_enabled, reasoning_effort: p.reasoning_effort });
        showToast(result.ok ? '连接成功' : ('连接失败：' + (result.message || '未知错误')), result.ok ? 'success' : 'error');
      } catch (e) { showToast('连接失败：' + (e.message || e), 'error'); }
    }

    async function saveModelProfileUi() {
      const p = modelProfilePayload(true);
      if (!p.display_name || !p.model_name || (p.provider !== 'deepseek' && !p.endpoint)) return showToast(p.provider === 'deepseek' ? '请填写显示名称并选择模型' : '请完整填写显示名称、接口地址和模型 ID', 'error');
      try {
        await teachMateApi.saveModelProfile(p);
        closeModal();
        showToast('模型已保存并应用', 'success');
        loadProviderStatusUi();
      } catch (e) { showToast('保存失败：' + (e.message || e), 'error'); }
    }

    async function activateModelProfileUi(id) {
      if (!id) return;
      try {
        const result = await teachMateApi.activateModelProfile(id);
        if (!result.success) throw new Error(result.message || '切换失败');
        showToast('已切换模型', 'success');
        loadProviderStatusUi();
        if (window.teachMateState && teachMateApi.listModelProfiles) {
          teachMateApi.listModelProfiles().then(p => teachMateState.setSavedModels(p)).catch(() => {});
          teachMateApi.getProviderInfo().then(p => teachMateState.setProviderInfo(p)).catch(() => {});
        }
      } catch (e) { showToast('切换失败：' + (e.message || e), 'error'); }
    }

    async function deleteModelProfileUi(id) {
      if (!id || !window.confirm('确定删除这个模型配置吗？')) return;
      try { await teachMateApi.deleteModelProfile(id); showToast('模型已删除', 'success'); loadProviderStatusUi(); }
      catch (e) { showToast('删除失败：' + (e.message || e), 'error'); }
    }

    async function testProviderConnectionUi() {
      const payload = {
        provider: (document.getElementById('ai-provider') || {}).value || undefined,
        model_name: (document.getElementById('ai-model') || {}).value || undefined,
        base_url: (document.getElementById('ai-base-url') || {}).value || undefined,
        api_key: ((document.getElementById('ai-api-key') || {}).value || '').trim() || undefined,
      };
      aiStatus('正在测试连接…');
      try {
        const result = await teachMateApi.testProviderConnection(payload);
        if (result && result.ok) {
          aiStatus('连接成功（测试消息未保存）· 延迟 ' + result.latency_ms + 'ms');
          showToast('连接成功', 'success');
        } else {
          aiStatus('连接失败：' + (result?.message || '未知错误'), true);
          showToast('连接失败', 'error');
        }
      } catch (e) {
        aiStatus('连接失败：' + (e?.message || e), true);
      }
    }

    async function switchProviderUi() {
      const apiKeyInput = document.getElementById('ai-api-key');
      const keyValue = apiKeyInput ? apiKeyInput.value.trim() : '';
      const payload = {
        provider: (document.getElementById('ai-provider') || {}).value || 'deepseek',
        model_name: (document.getElementById('ai-model') || {}).value || undefined,
        base_url: (document.getElementById('ai-base-url') || {}).value || undefined,
      };
      if (keyValue) payload.api_key = keyValue;
      aiStatus('正在保存并应用…');
      try {
        const result = await teachMateApi.switchProvider(payload);
        if (result && result.success) {
          if (apiKeyInput) apiKeyInput.value = '';
          const keyState = document.getElementById('ai-key-state');
          if (keyState) keyState.textContent = '（已配置）';
          aiStatus(result.message || '已应用');
          showToast('模型配置已应用', 'success');
        } else {
          aiStatus('保存失败：' + (result?.message || '未知错误'), true);
          showToast('保存失败', 'error');
        }
      } catch (e) {
        aiStatus('保存失败：' + (e?.message || e), true);
      }
    }

    // 供视图层在进入设置页时刷新状态
    window.loadProviderStatusUi = loadProviderStatusUi;

    async function loadMoniConfigUi() {
      const status = document.getElementById('moni-config-status');
      const keyState = document.getElementById('moni-key-state');
      try {
        const payload = await apiRequest('/api/v1/school-sync/moni/config');
        if (status) status.textContent = payload?.configured ? `已配置 · ${payload?.health?.health === 'ready' ? '连接正常' : '待测试'}` : '尚未配置';
        if (keyState) keyState.textContent = payload?.configured ? `API Key 已保存（${payload?.key_hint || '已隐藏'}）` : 'API Key 尚未保存';
      } catch (error) {
        if (status) status.textContent = `读取配置失败：${error.message || error}`;
        if (keyState) keyState.textContent = 'API Key 状态读取失败';
      }
    }

    async function saveMoniConfigUi() {
      const input = document.getElementById('moni-api-key');
      const status = document.getElementById('moni-config-status');
      const keyState = document.getElementById('moni-key-state');
      if (!input) return;
      const apiKey = String(input.value || '').trim().replace(/^Bearer\s+/i, '');
      const value = { mcpServers: { moni: { type: 'http', url: 'https://t-mcp.fufenxi.com/api/t-mcp/mcp', headers: { Authorization: apiKey ? `Bearer ${apiKey}` : 'Bearer ********' } } } };
      if (!apiKey && status && !/已配置/.test(status.textContent || '')) { showToast('请输入 MONI API Key', 'error'); return; }
      try {
        const result = await apiRequest('/api/v1/school-sync/moni/config', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value) });
        input.value = '';
        if (status) status.textContent = result.token_configured ? '配置已保存，令牌已安全保存' : '配置已保存，但尚未填写 Bearer 令牌';
        if (keyState) keyState.textContent = result.token_configured ? `API Key 已保存（${result.key_hint || '已隐藏'}）` : 'API Key 尚未保存';
        showToast('MONI 接口配置已保存', 'success');
      } catch (error) {
        const message = /Failed to fetch|NetworkError|网络连接失败/i.test(String(error?.message || error))
          ? '本地 WorkBench 服务未启动，请完全退出后重新打开应用'
          : (error?.message || error);
        if (status) status.textContent = `保存失败：${message}`;
        showToast('MONI 配置保存失败', 'error');
      }
    }

    async function testMoniConfigUi() {
      const status = document.getElementById('moni-config-status');
      const keyState = document.getElementById('moni-key-state');
      if (status) status.textContent = '正在测试连接…';
      try {
        const result = await apiRequest('/api/v1/school-sync/moni/test', { method: 'POST', timeoutMs: 30000 });
        if (status) status.textContent = result.status === 'ok' ? `连接正常 · ${result.tool_count || 0} 个工具` : `连接失败：${result.error || result.health || '未知错误'}`;
        if (keyState && result.key_hint) keyState.textContent = `API Key 已保存（${result.key_hint}）`;
        showToast(result.status === 'ok' ? 'MONI 连接测试成功' : 'MONI 连接测试失败', result.status === 'ok' ? 'success' : 'error');
      } catch (error) { if (status) status.textContent = `测试失败：${error.message || error}`; showToast('MONI 连接测试失败', 'error'); }
    }

    async function syncMoniNowUi() {
      const status = document.getElementById('moni-config-status');
      if (subjectKey !== 'english') {
        const message = `MONI 当前同步器读取英语单科数据；切到${subjectName()}时请使用该学科对应的数据源。`;
        if (status) status.textContent = message;
        showToast(message, 'warning');
        return;
      }
      if (status) status.textContent = '正在读取并同步学生数据，请稍候…';
      try {
        const result = await apiRequest('/api/v1/school-sync/moni/sync', { method: 'POST', timeoutMs: 120000 });
        const summary = result.summary || {};
        const students = Number(summary.roster_students ?? summary.students ?? 0);
        const classes = Number(summary.classes || 0);
        const exams = Number(summary.exams || 0);
        const scoreStudents = Number(summary.score_students ?? 0);
        const tieredExams = Number(summary.tiered_exams ?? 0);
        const gradeRankStudents = Number(summary.grade_rank_students ?? 0);
        const rankScope = summary.grade_rank_scope === 'authorized_classes'
          ? '授权班级未覆盖全年级，未补算'
          : `年级排名 ${gradeRankStudents} 人`;
        const detail = `分层线 ${tieredExams} 场 · ${rankScope}`;
        if (students === 0 && exams === 0) {
          if (status) status.textContent = '同步完成，但 MONI 当前学期没有返回可导入的学生或考试数据';
          showToast('同步完成，但没有发现可导入数据', 'warning');
        } else {
          if (scoreStudents === 0) {
            if (status) status.textContent = `同步完成 · 学生 ${students} 人 · 班级 ${classes} 个 · 考试 ${exams} 场 · ${detail} · MONI 未返回${subjectText('score_single')}`;
            showToast(`同步完成，但未返回${subjectText('score_single')}（未使用全科总分）`, 'warning');
          } else {
            if (status) status.textContent = `同步完成 · 学生 ${students} 人 · 班级 ${classes} 个 · 考试 ${exams} 场 · ${detail}`;
            showToast('MONI 学生数据同步完成', 'success');
          }
        }
        if (typeof loadData === 'function') await loadData();
        render();
      } catch (error) { if (status) status.textContent = `同步失败：${error.message || error}`; showToast('MONI 学生数据同步失败', 'error'); }
    }

    window.loadMoniConfigUi = loadMoniConfigUi;

    // ---------- 成长加分标准（每位老师各自一套） ----------
    // 设置页只维护「本机有哪几位老师」与「每位老师的快捷补录分值」。分值不进入计分
    // 引擎：已入账的历史记录仍按当时确认的分值保留，所以改标准不必新建规则版本。
    let growthStandardsState = null;
    let growthStandardsBusy = false;

    function growthStandardStatus(text, isError) {
      const el = document.getElementById('growth-preset-status');
      if (!el) return;
      el.textContent = text;
      el.classList.toggle('growth-status-error', Boolean(isError));
    }

    function growthStandardSelectedId() {
      const select = document.getElementById('growth-teacher-select');
      const picked = Number(select && select.value);
      if (Number.isSafeInteger(picked) && picked > 0) return picked;
      return Number(growthStandardsState && growthStandardsState.active_teacher
        && growthStandardsState.active_teacher.id) || null;
    }

    function growthStandardOwnerName() {
      const active = growthStandardsState && growthStandardsState.active_teacher;
      const name = active && active.name ? String(active.name).trim() : '';
      return name || '当前教师';
    }

    function renderGrowthTeacherOptions(payload) {
      const select = document.getElementById('growth-teacher-select');
      if (!select) return;
      const rows = Array.isArray(payload && payload.teachers) ? payload.teachers : [];
      const activeId = Number(payload && payload.active_teacher && payload.active_teacher.id) || null;
      select.innerHTML = rows.map(teacher =>
        `<option value="${teacher.id}" ${teacher.id === activeId ? 'selected' : ''}>${escapeHtml(teacher.name)}${teacher.id === activeId ? '（当前）' : ''}</option>`
      ).join('');
      // 至少保留一位教师：只剩一人时不允许删除，也不需要用下拉切换。
      const onlyOne = rows.length <= 1;
      select.disabled = onlyOne;
      const removeBtn = document.querySelector('[data-act="growth-teacher-remove"]');
      if (removeBtn) {
        removeBtn.disabled = onlyOne;
        removeBtn.title = onlyOne ? '至少保留一位教师' : '删除当前选中的教师档案';
      }
    }

    function renderGrowthPresetRows(payload) {
      const box = document.getElementById('growth-preset-rows');
      if (!box) return;
      const presets = Array.isArray(payload && payload.presets) ? payload.presets : [];
      const maxPoints = Number(payload && payload.max_points) || 10;
      if (!presets.length) {
        box.innerHTML = '<div class="growth-inline-warn">当前学期规则版本没有可手工补录的类别，或后台尚未返回加分标准。请重启工作台服务后重试。</div>';
        return;
      }
      box.innerHTML = presets.map(preset => {
        const label = preset.label || preset.type;
        const flag = preset.customized
          ? `<span class="growth-preset-flag">已自定义 · 出厂 ${Number(preset.default_points) || 1}</span>` : '';
        const hidden = preset.visible === false ? '<span class="growth-preset-flag">快捷区已隐藏</span>' : '';
        return `<div class="growth-preset-row" data-growth-preset="${escapeAttr(preset.type)}">
          <span class="growth-preset-name">${escapeHtml(label)}</span>
          <label class="growth-preset-points">分值<input type="number" data-role="points" min="1" max="${maxPoints}" step="1" value="${Number(preset.points) || 1}" aria-label="${escapeAttr(label)}分值"></label>
          <label class="growth-preset-visible"><input type="checkbox" data-role="visible" ${preset.visible !== false ? 'checked' : ''}>快捷显示</label>
          <span class="growth-preset-note">${flag}${hidden}</span>
        </div>`;
      }).join('');
    }

    function renderGrowthStandardConflict(payload) {
      const notice = document.getElementById('growth-preset-conflict');
      if (!notice) return;
      const conflict = (payload && payload.conflict) || {};
      if (!conflict.conflict) { notice.innerHTML = ''; return; }
      const presets = (payload && payload.presets) || [];
      const labels = (conflict.divergent_types || []).map(type => {
        const preset = presets.find(item => item.type === type);
        return preset ? preset.label : type;
      });
      const detail = labels.length ? `（${labels.map(escapeHtml).join('、')}）` : '';
      notice.innerHTML = `<div class="growth-inline-warn" role="status">本机 ${Number(conflict.teachers) || 2} 位老师使用了不同的加分标准${detail}。学生之间的营养值不宜直接横向比较；阶段与「森林之星」只表示学习活动记录量，${escapeHtml(subjectText('ability_disclaimer'))}。</div>`;
    }

    function applyGrowthStandardsPayload(payload) {
      growthStandardsState = payload;
      renderGrowthTeacherOptions(payload);
      renderGrowthPresetRows(payload);
      renderGrowthStandardConflict(payload);
    }

    async function loadGrowthStandardsUi() {
      const box = document.getElementById('growth-preset-rows');
      if (!box) return;
      growthStandardStatus('正在读取配置…');
      try {
        const payload = await apiRequest(`/api/v1/growth/teacher-presets?term_id=${encodeURIComponent(currentTermId)}`);
        applyGrowthStandardsPayload(payload);
        const count = (payload && payload.presets ? payload.presets.length : 0);
        growthStandardStatus(`当前生效：${growthStandardOwnerName()} · ${count} 个补录类别`);
      } catch (error) {
        box.innerHTML = `<div class="growth-inline-warn">读取加分标准失败：${escapeHtml(error && error.message ? error.message : error)}</div>`;
        growthStandardStatus('读取失败', true);
      }
    }

    async function saveGrowthPresetsUi() {
      if (growthStandardsBusy) return;
      if (!DATABASE_MODE) { growthStandardStatus('浏览器存储版不支持加分标准', true); return; }
      const box = document.getElementById('growth-preset-rows');
      const teacherId = growthStandardSelectedId();
      if (!box || !teacherId) { growthStandardStatus('请先选择教师', true); return; }
      const maxPoints = Number(growthStandardsState && growthStandardsState.max_points) || 10;
      const presets = {};
      const problems = [];
      box.querySelectorAll('[data-growth-preset]').forEach(row => {
        const type = row.dataset.growthPreset;
        const pointsInput = row.querySelector('[data-role="points"]');
        const visibleInput = row.querySelector('[data-role="visible"]');
        const raw = Number(String(pointsInput ? pointsInput.value : '').trim());
        const label = (row.querySelector('.growth-preset-name') || {}).textContent || type;
        if (!Number.isSafeInteger(raw) || raw < 1 || raw > maxPoints) {
          problems.push(`「${label}」的分值需为 1–${maxPoints} 的整数`);
          return;
        }
        presets[type] = { points: raw, visible: visibleInput ? visibleInput.checked : true };
      });
      if (problems.length) {
        growthStandardStatus(problems[0], true);
        showToast(problems[0], 'error');
        return;
      }
      growthStandardsBusy = true;
      growthStandardStatus('正在保存…');
      try {
        const payload = await apiRequest(`/api/v1/growth/teacher-presets?teacher_id=${encodeURIComponent(teacherId)}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ term_id: Number(currentTermId), presets })
        });
        applyGrowthStandardsPayload(payload);
        growthStandardStatus(`已保存 · ${growthStandardOwnerName()} · ${Object.keys(presets).length} 个补录类别`);
        showToast('加分标准已保存', 'success');
        // 森林页缓存的预设已过期，作废以便下次进入时按新标准渲染。
        if (typeof window.growthInvalidate === 'function') window.growthInvalidate();
      } catch (error) {
        const message = error && error.message ? error.message : String(error);
        growthStandardStatus(`保存失败：${message}`, true);
        showToast('加分标准保存失败', 'error');
      } finally {
        growthStandardsBusy = false;
      }
    }

    async function resetGrowthPresetsUi() {
      if (growthStandardsBusy) return;
      const teacherId = growthStandardSelectedId();
      if (!teacherId) { growthStandardStatus('请先选择教师', true); return; }
      growthStandardsBusy = true;
      growthStandardStatus('正在恢复默认分值…');
      try {
        const payload = await apiRequest(`/api/v1/growth/teacher-presets?teacher_id=${encodeURIComponent(teacherId)}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ term_id: Number(currentTermId), presets: {} })
        });
        applyGrowthStandardsPayload(payload);
        growthStandardStatus(`已恢复出厂默认 · ${growthStandardOwnerName()}`);
        showToast('已恢复默认分值', 'success');
        if (typeof window.growthInvalidate === 'function') window.growthInvalidate();
      } catch (error) {
        growthStandardStatus(`恢复失败：${error && error.message ? error.message : error}`, true);
        showToast('恢复默认分值失败', 'error');
      } finally {
        growthStandardsBusy = false;
      }
    }

    async function selectGrowthTeacherUi(teacherId) {
      if (!teacherId) return;
      growthStandardStatus('正在切换教师…');
      try {
        await apiRequest(`/api/v1/growth/teachers/${encodeURIComponent(teacherId)}/activate`, { method: 'POST' });
        await loadGrowthStandardsUi();
        if (typeof window.growthInvalidate === 'function') window.growthInvalidate();
      } catch (error) {
        growthStandardStatus(`切换失败：${error && error.message ? error.message : error}`, true);
        showToast('切换教师失败', 'error');
      }
    }

    async function addGrowthTeacherUi() {
      const input = document.getElementById('growth-teacher-new');
      const name = input ? String(input.value || '').trim() : '';
      if (!name) { showToast('请输入教师姓名', 'error'); return; }
      try {
        const payload = await apiRequest('/api/v1/growth/teachers', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name, activate: true })
        });
        if (input) input.value = '';
        applyGrowthStandardsPayload(payload);
        await loadGrowthStandardsUi();
        if (typeof window.growthInvalidate === 'function') window.growthInvalidate();
        showToast(`已新增教师「${name}」并切换为当前`, 'success');
      } catch (error) {
        showToast(error && error.message ? error.message : '新增教师失败', 'error');
      }
    }

    async function removeGrowthTeacherUi() {
      const teacherId = growthStandardSelectedId();
      if (!teacherId) return;
      const name = growthStandardOwnerName();
      openModal('删除教师档案？', `<p>将删除「${escapeHtml(name)}」的加分标准档案。</p><p style="color:var(--md-text-secondary);">已入账的成长记录与署名不受影响，仍保留在事件账本中；至少需保留一位教师。</p>`, `<button class="btn btn-text" data-act="growth-teacher-remove-cancel">取消</button><button class="btn btn-danger" data-act="growth-teacher-remove-confirm">确认删除</button>`);
    }

    async function confirmRemoveGrowthTeacherUi() {
      const teacherId = growthStandardSelectedId();
      closeModal();
      if (!teacherId) return;
      try {
        const payload = await apiRequest(`/api/v1/growth/teachers/${encodeURIComponent(teacherId)}`, { method: 'DELETE' });
        applyGrowthStandardsPayload(payload);
        await loadGrowthStandardsUi();
        if (typeof window.growthInvalidate === 'function') window.growthInvalidate();
        showToast('教师档案已删除', 'success');
      } catch (error) {
        showToast(error && error.message ? error.message : '删除教师失败', 'error');
      }
    }

    window.loadGrowthStandardsUi = loadGrowthStandardsUi;

    // ---------- 其他学校数据源（自定义 MCP） ----------
    // 内置 MONI 的取数逻辑不动；换学校只是换一份配置：端点、鉴权、路径模板、字段映射。
    // 保存时后端会先校验、再把配置物化成插件清单，因此这里不做第二份规则判断——
    // 只做「表单能不能拼成一份 JSON」与「关键映射是否填了」的即时提示。
    // 字段映射模板：左边是 WorkBench 认的逻辑字段，右边是学校可能用的列名（按顺序取
    // 第一个有值的）。这里的候选名只是「同名写法」的起点，不是对学校字段的断言——
    // 取不到就留空，不会被当成 0；用不到的行可以直接删掉。
    const SCHOOL_SOURCE_FIELD_TEMPLATE = {
      'term.external_id': ['termId', 'term_id'],
      'term.name': ['termName', 'term_name'],
      'term.code': ['termCode', 'term_code'],
      'class.external_id': ['classId', 'class_id', 'id'],
      'class.name': ['className', 'class_name', 'name'],
      'class.grade': ['grade', 'gradeName'],
      'exam.external_id': ['examId', 'exam_id', 'id'],
      'exam.name': ['examName', 'exam_name', 'name'],
      'exam.date': ['examDate', 'exam_date', 'date'],
      'exam.full_score': ['fullScore', 'full_score'],
      'exam.kind': ['examKind', 'exam_kind'],
      'exam.paper_revision': ['paperRevision', 'paper_revision'],
      'exam.tier_a_cutoff': ['tierACutoff'],
      'exam.tier_b_cutoff': ['tierBCutoff'],
      'exam.tier_c_cutoff': ['tierCCutoff'],
      'question.external_id': ['questionId', 'question_id', 'id'],
      'question.no': ['questionNo', 'number'],
      'question.sub_no': ['subNo', 'sub_no'],
      'question.section': ['sectionName', 'section'],
      'question.type': ['questionType', 'question_type'],
      'question.content': ['contentText', 'content'],
      'question.answer': ['correctAnswer', 'answer'],
      'question.max_score': ['maxScore', 'fullScore'],
      'question.difficulty': ['difficultyLevel', 'difficulty'],
      'question.cognitive': ['cognitiveLevel', 'cognitive'],
      'question.knowledge': ['knowledgeNodes', 'knowledge'],
      'question.ability': ['abilityNodes', 'ability'],
      'question.pitfall': ['pitfallTags', 'pitfall'],
      'question.teaching_block': ['teachingBlocks', 'teachingBlock'],
      'item.list': ['items', 'itemScores'],
      'item.question_external_id': ['questionId', 'question_id'],
      'item.score': ['score'],
      'item.score_rate': ['scoreRate', 'score_rate'],
      'item.answer': ['studentAnswer', 'answer'],
      'item.correct': ['isCorrect', 'correct'],
      'item.selected_option': ['selectedOption', 'selected_option'],
      'item.time_spent_ms': ['timeSpentMs', 'time_spent_ms'],
      'item.modify_count': ['modifyCount', 'modify_count'],
      'item.hesitation_time_ms': ['hesitationTimeMs', 'hesitation_time_ms'],
      'item.pitfall': ['pitfallTags', 'pitfall'],
      'item.teaching_block': ['teachingBlocks', 'teachingBlock'],
      'student.external_id': ['studentId', 'student_id', 'id'],
      'student.name': ['name', 'studentName'],
      'student.student_no': ['studentNo', 'student_no'],
      'student.gender': ['gender', 'sex'],
      'student.status': ['status'],
      'student.total_score': ['totalScore', 'score'],
      'student.class_rank': ['classRank'],
      'student.grade_rank': ['gradeRank'],
      'student.global_rank': ['globalRank', 'global_rank'],
      'student.tier': ['tier', 'tierLevel']
    };
    const SCHOOL_SOURCE_REQUIRED_BASE = ['class.external_id', 'class.name', 'student.external_id', 'student.name'];
    const SCHOOL_SOURCE_REQUIRED_EXAM = ['exam.external_id', 'exam.name'];
    const SCHOOL_SOURCE_PATH_FIELDS = ['term', 'classes', 'roster', 'exams', 'questions', 'students'];

    // 可以填「上游叫法 → 统一叫法」对照表的字段。与后端 KNOWN_FIELDS 里的分类字段
    // 保持同一份命名：字段名写错时后端会直接报错，所以这里也提前挡一道。
    const SCHOOL_SOURCE_ALIAS_FIELDS = [
      ['question.difficulty', '题目难度'],
      ['question.cognitive', '认知层级'],
      ['question.knowledge', '知识点（多值）'],
      ['question.ability', '能力点（多值）'],
      ['question.pitfall', '易错点（多值）'],
      ['question.teaching_block', '教学模块（多值）'],
      ['item.pitfall', '逐题易错点（多值）'],
      ['item.teaching_block', '逐题教学模块（多值）'],
      ['exam.kind', '考试类型（entrance / regular）'],
      ['student.tier', '学生分层（A / B / C / D）']
    ];
    const SCHOOL_SOURCE_ALIAS_FIELD_NAMES = new Set(SCHOOL_SOURCE_ALIAS_FIELDS.map(item => item[0]));
    const SCHOOL_SOURCE_TIER_LEVELS = ['A', 'B', 'C', 'D'];
    // 多值用逗号、顿号或分号分隔；与后端 as_str_list 的分隔约定一致。
    const SCHOOL_SOURCE_ALIAS_SEPARATOR = /[,，、;；|]/;

    let schoolSourceState = { rows: [], editing: null, busy: false };

    function schoolSourceStatus(text, isError) {
      const el = document.getElementById('school-source-status');
      if (!el) return;
      el.textContent = text;
      el.classList.toggle('growth-status-error', Boolean(isError));
    }

    function schoolSourceEditorStatus(text, isError) {
      const el = document.getElementById('school-source-editor-status');
      if (!el) return;
      el.textContent = text;
      el.classList.toggle('growth-status-error', Boolean(isError));
    }

    function schoolSourceValue(id) {
      const el = document.getElementById(id);
      return el ? String(el.value || '').trim() : '';
    }

    function schoolSourceSetValue(id, value) {
      const el = document.getElementById(id);
      if (el) el.value = value === null || value === undefined ? '' : String(value);
    }

    function schoolSourceRequiredMissing(fieldMap, hasExams) {
      const required = SCHOOL_SOURCE_REQUIRED_BASE.concat(hasExams ? SCHOOL_SOURCE_REQUIRED_EXAM : []);
      return required.filter(key => !Object.prototype.hasOwnProperty.call(fieldMap, key));
    }

    function renderSchoolSourceMissing() {
      const box = document.getElementById('school-source-missing');
      if (!box) return;
      let parsed = null;
      const raw = schoolSourceValue('school-source-field-map');
      try {
        parsed = raw ? JSON.parse(raw) : {};
      } catch (error) {
        box.className = 'school-source-missing is-error';
        box.textContent = `字段映射不是合法 JSON：${error && error.message ? error.message : error}`;
        return;
      }
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        box.className = 'school-source-missing is-error';
        box.textContent = '字段映射需要是一个 JSON 对象。';
        return;
      }
      const missing = schoolSourceRequiredMissing(parsed, Boolean(schoolSourceValue('school-source-path-exams')));
      if (missing.length) {
        box.className = 'school-source-missing is-error';
        box.textContent = `还缺关键字段映射：${missing.join('、')}。补齐后才能保存。`;
        return;
      }
      box.className = 'school-source-missing';
      box.textContent = '关键字段映射已齐全。';
    }

    // ---------- 分类取值对照表 ----------
    // 字段「名」靠字段映射解决，字段「取值」还得靠这张表：同一个知识点，上游可能叫
    // 「宾语从句」也可能叫「从句」。教师填「统一叫法 → 上游叫法」，保存时后端反转成
    // 归一表。这里只做「能不能解析成一份配置」的即时提示，不做模糊自动归一。

    function schoolSourceAliasKey(value) {
      return String(value === null || value === undefined ? '' : value)
        .trim().normalize('NFKC').toLowerCase();
    }

    function splitSchoolSourceAliasNames(raw) {
      return String(raw || '')
        .split(SCHOOL_SOURCE_ALIAS_SEPARATOR)
        .map(item => item.trim())
        .filter(Boolean);
    }

    function schoolSourceConfigLines(raw) {
      return String(raw || '').split(/\r?\n/).map((text, index) => ({
        lineNo: index + 1, text: text.trim()
      })).filter(line => line.text && !line.text.startsWith('#'));
    }

    function parseSchoolSourceAliasMap(raw) {
      const valueAliases = {};
      const errors = [];
      schoolSourceConfigLines(raw).forEach(line => {
        const colon = line.text.indexOf(':');
        const equals = line.text.indexOf('=');
        if (colon < 0 || equals < 0 || equals < colon) {
          errors.push(`第 ${line.lineNo} 行：请写成「逻辑字段: 统一叫法 = 上游叫法1, 上游叫法2」`);
          return;
        }
        const field = line.text.slice(0, colon).trim();
        const canonical = line.text.slice(colon + 1, equals).trim();
        const upstream = splitSchoolSourceAliasNames(line.text.slice(equals + 1));
        if (!SCHOOL_SOURCE_ALIAS_FIELD_NAMES.has(field)) {
          errors.push(`第 ${line.lineNo} 行：「${field}」不是可以归一的分类字段`);
          return;
        }
        if (!canonical) {
          errors.push(`第 ${line.lineNo} 行：统一叫法不能为空`);
          return;
        }
        if (!upstream.length) {
          errors.push(`第 ${line.lineNo} 行：至少写一个上游叫法`);
          return;
        }
        const groups = valueAliases[field] || (valueAliases[field] = {});
        const target = groups[canonical] || (groups[canonical] = []);
        upstream.forEach(name => {
          // 同一个上游叫法被映射成两个统一值时直接报错，不做「谁先谁赢」的静默取舍。
          const conflict = Object.keys(groups).find(other =>
            other !== canonical &&
            groups[other].some(item => schoolSourceAliasKey(item) === schoolSourceAliasKey(name)));
          if (conflict) {
            errors.push(`第 ${line.lineNo} 行：「${name}」已经被映射成「${conflict}」，请只保留一个`);
            return;
          }
          if (!target.some(item => schoolSourceAliasKey(item) === schoolSourceAliasKey(name))) {
            target.push(name);
          }
        });
      });
      return { value_aliases: valueAliases, errors };
    }

    function parseSchoolSourceTierAliases(raw) {
      const tierAliases = {};
      const errors = [];
      schoolSourceConfigLines(raw).forEach(line => {
        const equals = line.text.indexOf('=');
        if (equals < 0) {
          errors.push(`第 ${line.lineNo} 行：请写成「上游叫法 = A」`);
          return;
        }
        const label = line.text.slice(0, equals).trim();
        // 与后端 normalize_tier 同口径：忽略大小写与空格，「A 层」也算 A。
        const level = line.text.slice(equals + 1).trim().toUpperCase().replace(/\s+/g, '').replace(/层$/, '');
        if (!label) {
          errors.push(`第 ${line.lineNo} 行：上游叫法不能为空`);
          return;
        }
        if (SCHOOL_SOURCE_TIER_LEVELS.indexOf(level) < 0) {
          errors.push(`第 ${line.lineNo} 行：目标层只能是 A/B/C/D，收到「${level}」`);
          return;
        }
        tierAliases[label] = level;
      });
      return { tier_aliases: tierAliases, errors };
    }

    function formatSchoolSourceAliasMap(aliases) {
      if (!aliases || typeof aliases !== 'object') return '';
      const lines = [];
      Object.keys(aliases).forEach(field => {
        const groups = aliases[field];
        if (!groups || typeof groups !== 'object') return;
        Object.keys(groups).forEach(canonical => {
          const names = Array.isArray(groups[canonical]) ? groups[canonical] : [];
          if (!names.length) return;
          lines.push(`${field}: ${canonical} = ${names.join(', ')}`);
        });
      });
      return lines.join('\n');
    }

    function formatSchoolSourceTierAliases(aliases) {
      if (!aliases || typeof aliases !== 'object') return '';
      return Object.keys(aliases)
        .map(label => `${label} = ${aliases[label]}`)
        .join('\n');
    }

    function schoolSourceAliasFieldsNotMapped(aliasMap) {
      // 对照表里的字段必须先说明读哪一列，否则后端会拒绝整份配置。
      let fieldMap = {};
      try {
        const parsed = JSON.parse(schoolSourceValue('school-source-field-map') || '{}');
        if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) fieldMap = parsed;
      } catch (error) {
        return [];
      }
      return Object.keys(aliasMap).filter(field => !Object.prototype.hasOwnProperty.call(fieldMap, field));
    }

    function renderSchoolSourceAliasStatus() {
      const box = document.getElementById('school-source-alias-status');
      if (!box) return { ok: true };
      const aliasResult = parseSchoolSourceAliasMap(schoolSourceValue('school-source-alias-map'));
      const tierResult = parseSchoolSourceTierAliases(schoolSourceValue('school-source-tier-aliases'));
      const errors = aliasResult.errors.concat(tierResult.errors);
      if (errors.length) {
        box.className = 'school-source-missing is-error';
        box.textContent = errors.slice(0, 3).join('；') + (errors.length > 3 ? `（共 ${errors.length} 处）` : '');
        return { ok: false };
      }
      const unmapped = schoolSourceAliasFieldsNotMapped(aliasResult.value_aliases);
      if (unmapped.length) {
        box.className = 'school-source-missing is-error';
        box.textContent = `对照表里的 ${unmapped.join('、')} 还没有在字段映射里说明读哪一列，保存会被拒绝。`;
        return { ok: false };
      }
      const rules = Object.keys(aliasResult.value_aliases).reduce(
        (total, field) => total + Object.keys(aliasResult.value_aliases[field]).length, 0);
      const tierRules = Object.keys(tierResult.tier_aliases).length;
      box.className = 'school-source-missing';
      if (!rules && !tierRules) {
        box.textContent = '没有填对照表：上游取值会按原样保留，不会猜。';
      } else {
        box.textContent = `已配置 ${rules} 条取值归一${tierRules ? `、${tierRules} 条分层别名` : ''}。`
          + '没有写进对照表的上游取值会按原样保留。';
      }
      return { ok: true };
    }

    function renderSchoolSourceAliasReference() {
      const box = document.getElementById('school-source-alias-reference');
      if (!box) return;
      box.innerHTML = SCHOOL_SOURCE_ALIAS_FIELDS.map(([field, label]) =>
        `<li><code>${escapeHtml(field)}</code><span>${escapeHtml(label)}</span></li>`).join('');
    }

    function renderSchoolSourceList(rows) {
      const box = document.getElementById('school-source-list');
      if (!box) return;
      if (!rows.length) {
        box.innerHTML = '<div class="subtle">还没有登记任何数据源。</div>';
        return;
      }
      box.innerHTML = rows.map(row => {
        const key = String(row.source_key || '');
        const builtin = Boolean(row.builtin);
        const configurable = row.configurable !== false;
        const config = row.config || {};
        const endpoint = config.endpoint ? escapeHtml(config.endpoint) : '（内置地址）';
        const tokenState = row.token_configured
          ? `令牌已保存${row.key_hint ? `（${escapeHtml(row.key_hint)}）` : ''}`
          : '未配置令牌';
        const note = row.config_error
          ? `<div class="school-source-note is-error">配置有问题：${escapeHtml(row.config_error)}</div>`
          : `<div class="school-source-note">${escapeHtml(config.name || '')}${config.name ? ' · ' : ''}${endpoint} · ${tokenState}</div>`;
        let actions;
        if (builtin) {
          actions = '<span class="school-source-badge">内置</span>';
        } else if (!configurable) {
          // 导入过程自动登记的来源没有可编辑的字段映射配置，只列出不提供编辑入口。
          actions = '<span class="school-source-badge">自动登记</span>';
        } else {
          actions = `<button class="btn btn-sm btn-secondary" data-act="school-source-edit" data-key="${escapeAttr(key)}">编辑</button>
             <button class="btn btn-sm btn-secondary" data-act="school-source-test" data-key="${escapeAttr(key)}">测试连接</button>
             <button class="btn btn-sm btn-secondary" data-act="school-source-sync" data-key="${escapeAttr(key)}">立即同步</button>
             <button class="btn btn-sm btn-text danger-text" data-act="school-source-delete" data-key="${escapeAttr(key)}">删除</button>`;
        }
        return `<div class="school-source-row"><div class="school-source-head"><div><strong>${escapeHtml(row.name || key)}</strong><span class="school-source-key">${escapeHtml(key)}</span></div><div class="school-source-row-actions">${actions}</div></div>${note}</div>`;
      }).join('');
    }

    async function loadSchoolSourcesUi() {
      const box = document.getElementById('school-source-list');
      if (!box) return;
      schoolSourceStatus('正在读取配置…');
      try {
        const rows = await apiRequest('/api/v1/school-sync/sources');
        schoolSourceState.rows = Array.isArray(rows) ? rows : [];
        renderSchoolSourceList(schoolSourceState.rows);
        const custom = schoolSourceState.rows.filter(row => !row.builtin).length;
        schoolSourceStatus(`共 ${schoolSourceState.rows.length} 个数据源 · 自定义 ${custom} 个`);
      } catch (error) {
        box.innerHTML = `<div class="growth-inline-warn">读取数据源失败：${escapeHtml(error && error.message ? error.message : error)}</div>`;
        schoolSourceStatus('读取失败', true);
      }
    }

    function schoolSourceRow(sourceKey) {
      return schoolSourceState.rows.find(row => String(row.source_key) === String(sourceKey)) || null;
    }

    function openSchoolSourceEditor(sourceKey) {
      const editor = document.getElementById('school-source-editor');
      if (!editor) return;
      const row = sourceKey ? schoolSourceRow(sourceKey) : null;
      const config = (row && row.config) || {};
      const paths = config.paths || {};
      const auth = config.auth || {};
      const filter = config.subject_filter || {};
      const term = config.term || {};
      schoolSourceState.editing = row ? String(row.source_key) : null;

      const title = document.getElementById('school-source-editor-title');
      if (title) title.textContent = row ? `编辑「${row.name || row.source_key}」` : '新建数据源';
      schoolSourceSetValue('school-source-key', row ? row.source_key : '');
      const keyInput = document.getElementById('school-source-key');
      if (keyInput) {
        keyInput.readOnly = Boolean(row);
        keyInput.classList.toggle('is-readonly', Boolean(row));
      }
      schoolSourceSetValue('school-source-name', row ? row.name : '');
      schoolSourceSetValue('school-source-endpoint', config.endpoint || '');
      schoolSourceSetValue('school-source-auth', auth.type || 'none');
      schoolSourceSetValue('school-source-token', '');
      schoolSourceSetValue('school-source-term-id', term.external_id || '');
      schoolSourceSetValue('school-source-term-name', term.name || '');
      schoolSourceSetValue('school-source-full-score', config.full_score === null || config.full_score === undefined ? '' : config.full_score);
      schoolSourceSetValue('school-source-subject-field', filter.field || '');
      schoolSourceSetValue('school-source-subject-values', Array.isArray(filter.any_of) ? filter.any_of.join(',') : '');
      SCHOOL_SOURCE_PATH_FIELDS.forEach(name => schoolSourceSetValue(`school-source-path-${name}`, paths[name] || ''));
      const map = document.getElementById('school-source-field-map');
      if (map) {
        const spec = config.field_map && Object.keys(config.field_map).length
          ? config.field_map
          : SCHOOL_SOURCE_FIELD_TEMPLATE;
        map.value = JSON.stringify(spec, null, 2);
      }
      schoolSourceSetValue('school-source-alias-map', formatSchoolSourceAliasMap(config.value_aliases));
      schoolSourceSetValue('school-source-tier-aliases', formatSchoolSourceTierAliases(config.tier_aliases));
      const tokenState = document.getElementById('school-source-token-state');
      if (tokenState) {
        tokenState.textContent = row
          ? (row.token_configured ? `令牌已保存${row.key_hint ? `（${row.key_hint}）` : ''}，留空表示不修改` : '尚未保存令牌')
          : '尚未保存令牌';
      }
      schoolSourceEditorStatus('');
      renderSchoolSourceAliasReference();
      renderSchoolSourceMissing();
      renderSchoolSourceAliasStatus();
      editor.hidden = false;
      if (typeof editor.scrollIntoView === 'function') editor.scrollIntoView({ block: 'nearest' });
    }

    function closeSchoolSourceEditor() {
      const editor = document.getElementById('school-source-editor');
      if (editor) editor.hidden = true;
      schoolSourceState.editing = null;
      schoolSourceEditorStatus('');
    }

    function collectSchoolSourceForm() {
      const sourceKey = schoolSourceValue('school-source-key').toLowerCase();
      const name = schoolSourceValue('school-source-name');
      const endpoint = schoolSourceValue('school-source-endpoint');
      if (!sourceKey) { schoolSourceEditorStatus('请填写数据源标识', true); return null; }
      if (!name) { schoolSourceEditorStatus('请填写显示名称', true); return null; }
      if (!endpoint) { schoolSourceEditorStatus('请填写 MCP 端点', true); return null; }

      let fieldMap = null;
      try {
        fieldMap = JSON.parse(schoolSourceValue('school-source-field-map') || '{}');
      } catch (error) {
        schoolSourceEditorStatus(`字段映射不是合法 JSON：${error && error.message ? error.message : error}`, true);
        return null;
      }
      if (!fieldMap || typeof fieldMap !== 'object' || Array.isArray(fieldMap)) {
        schoolSourceEditorStatus('字段映射需要是一个 JSON 对象', true);
        return null;
      }

      const paths = {};
      SCHOOL_SOURCE_PATH_FIELDS.forEach(key => {
        const value = schoolSourceValue(`school-source-path-${key}`);
        if (value) paths[key] = value;
      });
      if (!paths.classes) { schoolSourceEditorStatus('至少要填写班级列表路径', true); return null; }
      if (!paths.exams && !paths.roster) {
        schoolSourceEditorStatus('考试列表与学生名册至少填写一个，否则没有可同步的内容', true);
        return null;
      }
      const missing = schoolSourceRequiredMissing(fieldMap, Boolean(paths.exams));
      if (missing.length) {
        schoolSourceEditorStatus(`还缺关键字段映射：${missing.join('、')}`, true);
        return null;
      }

      // 分类取值对照表：解析不了就整份配置不发出去，避免后端 422 后教师不知道改哪里。
      const aliasResult = parseSchoolSourceAliasMap(schoolSourceValue('school-source-alias-map'));
      const tierResult = parseSchoolSourceTierAliases(schoolSourceValue('school-source-tier-aliases'));
      const aliasErrors = aliasResult.errors.concat(tierResult.errors);
      if (aliasErrors.length) {
        schoolSourceEditorStatus(`分类取值对照表有问题：${aliasErrors[0]}`, true);
        renderSchoolSourceAliasStatus();
        return null;
      }
      const unmapped = schoolSourceAliasFieldsNotMapped(aliasResult.value_aliases);
      if (unmapped.length) {
        schoolSourceEditorStatus(
          `对照表里的 ${unmapped.join('、')} 还没有在字段映射里说明读哪一列，请先补上`, true);
        renderSchoolSourceAliasStatus();
        return null;
      }

      const authType = schoolSourceValue('school-source-auth') || 'none';
      const config = {
        transport: 'http',
        endpoint,
        headers: {},
        auth: authType === 'bearer'
          ? { type: 'bearer', token_profile: `school-${sourceKey}` }
          : { type: 'none' },
        paths,
        field_map: fieldMap
      };
      const termId = schoolSourceValue('school-source-term-id');
      if (termId) {
        const termName = schoolSourceValue('school-source-term-name') || termId;
        config.term = { external_id: termId, name: termName, code: termId };
      }
      const fullScore = schoolSourceValue('school-source-full-score');
      if (fullScore) {
        const parsedScore = Number(fullScore);
        if (!Number.isFinite(parsedScore) || parsedScore <= 0 || parsedScore > 1000) {
          schoolSourceEditorStatus('满分需要是 0 到 1000 之间的数字', true);
          return null;
        }
        config.full_score = parsedScore;
      }
      const filterField = schoolSourceValue('school-source-subject-field');
      const filterValues = schoolSourceValue('school-source-subject-values')
        .split(',').map(item => item.trim()).filter(Boolean);
      if (filterField && filterValues.length) {
        config.subject_filter = { field: filterField, any_of: filterValues };
      } else if (filterField || filterValues.length) {
        schoolSourceEditorStatus(`科目过滤需要同时填写字段名与取值，否则无法安全判断哪一列是${subjectText('score_column')}`, true);
        return null;
      }
      if (Object.keys(aliasResult.value_aliases).length) {
        config.value_aliases = aliasResult.value_aliases;
      }
      if (Object.keys(tierResult.tier_aliases).length) {
        config.tier_aliases = tierResult.tier_aliases;
      }
      return { source_key: sourceKey, name, config, bearer_token: schoolSourceValue('school-source-token') || null };
    }

    async function saveSchoolSourceUi() {
      if (schoolSourceState.busy) return;
      const form = collectSchoolSourceForm();
      if (!form) return;
      schoolSourceState.busy = true;
      schoolSourceEditorStatus('正在保存…');
      try {
        if (schoolSourceState.editing) {
          await apiRequest(`/api/v1/school-sync/sources/${encodeURIComponent(schoolSourceState.editing)}/config`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: form.name, config: form.config, bearer_token: form.bearer_token })
          });
        } else {
          await apiRequest('/api/v1/school-sync/sources', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(form)
          });
        }
        await loadSchoolSourcesUi();
        openSchoolSourceEditor(form.source_key);
        schoolSourceEditorStatus('已保存。可以点「测试连接」确认对方接口是否可达。');
        showToast('学校数据源已保存', 'success');
      } catch (error) {
        schoolSourceEditorStatus(`保存失败：${error && error.message ? error.message : error}`, true);
        showToast('学校数据源保存失败', 'error');
      } finally {
        schoolSourceState.busy = false;
      }
    }

    async function testSchoolSourceUi(sourceKey) {
      if (!sourceKey) return;
      schoolSourceStatus(`正在测试「${sourceKey}」…`);
      try {
        const result = await apiRequest(`/api/v1/school-sync/sources/${encodeURIComponent(sourceKey)}/test`, { method: 'POST', timeoutMs: 30000 });
        const ok = result.status === 'ok';
        schoolSourceStatus(ok
          ? `「${sourceKey}」连接正常 · ${Number(result.tool_count) || 0} 个工具`
          : `「${sourceKey}」连接失败：${result.error || result.health || '未知错误'}`, !ok);
        showToast(ok ? '连接测试成功' : '连接测试失败', ok ? 'success' : 'error');
      } catch (error) {
        schoolSourceStatus(`「${sourceKey}」测试失败：${error && error.message ? error.message : error}`, true);
        showToast('连接测试失败', 'error');
      }
    }

    async function syncSchoolSourceUi(sourceKey) {
      if (!sourceKey) return;
      schoolSourceStatus(`正在同步「${sourceKey}」，请稍候…`);
      try {
        const result = await apiRequest(`/api/v1/school-sync/sources/${encodeURIComponent(sourceKey)}/sync`, { method: 'POST', timeoutMs: 180000 });
        const summary = result.summary || {};
        const warnings = Array.isArray(summary.warnings) ? summary.warnings : [];
        const warnNote = warnings.length ? ` · ${warnings.length} 条提示：${warnings[0]}` : '';
        const students = Number(summary.roster_students ?? summary.students ?? 0);
        const classes = Number(summary.classes || 0);
        const exams = Number(summary.exams || 0);
        const message = (students === 0 && exams === 0)
          ? `同步完成，但没有取到可导入的数据${warnNote}`
          : `同步完成 · 学生 ${students} 人 · 班级 ${classes} 个 · 考试 ${exams} 场${warnNote}`;
        if (typeof loadData === 'function') await loadData();
        // 不重新渲染设置页：状态栏就是这次同步的结果，重渲染会被列表刷新覆盖掉。
        await loadSchoolSourcesUi();
        schoolSourceStatus(message, students === 0 && exams === 0 ? true : warnings.length > 0);
        showToast(students === 0 && exams === 0 ? '同步完成，但没有发现可导入数据' : '学校数据同步完成',
          students === 0 && exams === 0 ? 'warning' : 'success');
      } catch (error) {
        schoolSourceStatus(`「${sourceKey}」同步失败：${error && error.message ? error.message : error}`, true);
        showToast('学校数据同步失败', 'error');
      }
    }

    function deleteSchoolSourceUi(sourceKey) {
      if (!sourceKey) return;
      openModal('删除数据源？', `<p>将删除「${escapeHtml(sourceKey)}」的配置、插件清单与本机保存的令牌。</p><p style="color:var(--md-text-secondary);">已导入的成绩与学生记录不会被删除，仍保留在数据中。</p>`, `<button class="btn btn-text" data-act="school-source-delete-cancel">取消</button><button class="btn btn-danger" data-act="school-source-delete-confirm" data-key="${escapeAttr(sourceKey)}">确认删除</button>`);
    }

    async function confirmDeleteSchoolSourceUi(sourceKey) {
      closeModal();
      if (!sourceKey) return;
      try {
        await apiRequest(`/api/v1/school-sync/sources/${encodeURIComponent(sourceKey)}`, { method: 'DELETE' });
        if (schoolSourceState.editing === String(sourceKey)) closeSchoolSourceEditor();
        await loadSchoolSourcesUi();
        showToast('数据源已删除', 'success');
      } catch (error) {
        showToast(error && error.message ? error.message : '删除数据源失败', 'error');
      }
    }

    window.loadSchoolSourcesUi = loadSchoolSourcesUi;

    async function saveSettings() {
      const teacherName = document.getElementById('sett-name').value.trim();
      const subject = document.getElementById('sett-subject').value.trim();
      const excellent = parseFloat(document.getElementById('sett-excellent').value);
      const passing = parseFloat(document.getElementById('sett-pass').value);
      if (!teacherName || !subject) return showToast('教师姓名和学科不能为空', 'error');
      if (![excellent, passing].every(Number.isFinite)) return showToast('请完整填写优秀线和及格线', 'error');
      if ([excellent, passing].some(value => value < 0 || value > 100)) return showToast('优秀线和及格线比例应在0%到100%之间', 'error');
      if (!(excellent > passing)) return showToast('优秀线比例必须高于及格线比例', 'error');
      const newClasses = [...new Set((document.getElementById('sett-classes').value || '').split(',').map(cls => resolveClassName(cls)).filter(Boolean))];
      const orphan = state.students.filter(student => student.class && !newClasses.includes(student.class));
      if (orphan.length) return showToast(`无法删除仍有学生的班级：${[...new Set(orphan.map(student => student.class))].join('、')}`, 'error');
      const result = await commitMutation(() => {
        state.teacher.name = teacherName;
        state.teacher.subject = subject;
        state.settings.excellent = excellent;
        state.settings.pass = passing;
        state.classes = newClasses;
        if (!state.classes.includes(dictationClass)) dictationClass = state.classes[0] || '';
        if (scoreClass && !state.classes.includes(scoreClass)) scoreClass = '';
      }, { successMessage: '设置已保存', renderNavigation: true });
      return result.ok;
    }

    async function resetData() {
      const warning = DATABASE_MODE
        ? '⚠️ 此操作将清空当前数据库内容。系统会先自动备份，是否继续？'
        : '⚠️ 此操作非常危险，将清空所有本地数据且不可恢复！是否继续？';
      if (!confirm(warning)) return;
      try {
        await createDatabaseBackup();
      } catch (error) {
        showToast(`清空已取消：${error.message}`, 'error');
        return;
      }
      const result = await commitMutation(() => {
        state = createDefaultState();
        currentExamId = '';
      }, { successMessage: '数据已重置为初始状态', renderNavigation: true });
      return result.ok;
    }

    // ================= 导入导出 =================
    function handleImport(e) {
      const files = [...(e.target.files || [])];
      if (!files.length) return;
      const input = e.target;
      if (input.dataset.importBusy === '1') return;
      input.dataset.importBusy = '1';
      input.disabled = true;
      const isJson = files[0].name.toLowerCase().endsWith('.json');
      const readOne = file => new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = () => reject(reader.error || new Error('文件读取失败'));
        if (file.name.toLowerCase().endsWith('.json')) reader.readAsText(file, 'utf-8');
        else reader.readAsBinaryString(file);
      });
      (async () => {
        try {
          if (isJson) {
            const data = JSON.parse(await readOne(files[0]));
            await createDatabaseBackup();
            const importedState = migrate(data);
            const result = await commitMutation(() => {
              state = importedState;
              currentExamId = state.currentExamId || state.exams[0]?.id || '';
              state.currentExamId = currentExamId;
            }, { successMessage: 'JSON 导入成功，旧版数据已保留', renderNavigation: true });
            if (!result.ok) throw new Error('数据库未能保存导入内容');
            closeModal();
            return;
          }
          await ensureXlsx();
          const workbooks = [];
          for (const file of files) workbooks.push({ name: file.name, workbook: XLSX.read(await readOne(file), { type: 'binary', cellStyles: true }) });
          if (curModule === 'writing') {
            importWritingFromWorkbooks(workbooks);
            e.target.value = '';
            return;
          }
          const roster = workbooks.length === 1 && isRosterWorkbook(workbooks[0].workbook);
          if (roster) importRosterFromWorkbook(workbooks[0].workbook);
          else importFromWorkbooks(workbooks);
        } catch (err) { showToast('导入失败：' + err.message, 'error'); }
      })().finally(() => {
        input.value = '';
        input.disabled = false;
        delete input.dataset.importBusy;
      });
    }

    function isRosterWorkbook(wb) {
      return wb.SheetNames.some(sheetName => {
        const rows = XLSX.utils.sheet_to_json(wb.Sheets[sheetName], { header: 1, defval: '' });
        if (!rows.length) return false;
        const headerRow = findHeaderRow(rows);
        if (headerRow < 0) return false;
        const headers = rows[headerRow].map(h => String(h).trim().toLowerCase());
        const hasId = headers.some(h => ['学号', 'id', 'studentid', '学生编号'].includes(h));
        const hasName = headers.some(h => ['姓名', 'name', '学生姓名'].includes(h));
        const hasClass = headers.some(h => ['班级', 'class', '班级名称'].includes(h));
        const hasScore = headers.some(h => subjectScoreImportAliases().includes(h));
        return hasId && hasName && hasClass && !hasScore;
      });
    }

    function importRosterFromWorkbook(wb) {
      const parsed = [];
      const errors = [];
      wb.SheetNames.forEach(sheetName => {
        const rows = XLSX.utils.sheet_to_json(wb.Sheets[sheetName], { header: 1, defval: '' });
        if (rows.length < 2) return;
        const headerRow = findHeaderRow(rows);
        if (headerRow < 0) return;
        const headers = rows[headerRow].map(h => String(h).trim().toLowerCase());
        const indexOf = names => names.map(n => headers.indexOf(n)).find(i => i >= 0);
        const idIdx = indexOf(['学号', 'id', 'studentid', '学生编号']);
        const nameIdx = indexOf(['姓名', 'name', '学生姓名']);
        const classIdx = indexOf(['班级', 'class', '班级名称']);
        for (let i = headerRow + 1; i < rows.length; i++) {
          const row = rows[i];
          const id = String(row[idIdx] ?? '').trim();
          const name = String(row[nameIdx] ?? '').trim();
          const cls = resolveClassName(row[classIdx]);
          if (!id || !name || !cls) { errors.push(`第${i + 1}行缺少学号、姓名或班级`); continue; }
          parsed.push({ id, name, class: cls });
        }
      });
      const seenIds = new Set();
      const seenNames = new Set();
      parsed.forEach(item => {
        const nameKey = `${item.class}\u0000${item.name}`;
        if (seenIds.has(item.id)) errors.push(`学号重复：${item.id}`);
        else if (seenNames.has(nameKey)) errors.push(`${formatClassLabel(item.class)}姓名重复：${item.name}`);
        seenIds.add(item.id); seenNames.add(nameKey);
      });
      if (errors.length) return showToast(`名单导入失败：${errors.slice(0, 3).join('；')}`, 'error');
      const rollbackSnapshot = cloneStateSnapshot();
      const rollbackContext = { currentExamId };
      const byId = new Map(state.students.map(student => [student.id, student]));
      parsed.forEach(item => {
        const old = byId.get(item.id);
        if (old) { old.name = item.name; old.class = item.class; }
        else byId.set(item.id, { id: item.id, name: item.name, class: item.class, english: 0, target: '', weakTags: '', phone: '', seat: '', evaluationTags: [], evaluationNote: '' });
      });
      state.students = [...byId.values()];
      state.classes = [...new Set([...state.classes, ...parsed.map(item => item.class)])];
      if (!state.classes.includes(dictationClass)) dictationClass = state.classes[0] || '';
      scoreClass = scoreClass && state.classes.includes(scoreClass) ? scoreClass : '';
      const result = commitMutation(() => {}, {
        rollbackSnapshot,
        rollbackContext,
        successMessage: `名单导入成功，共导入 ${parsed.length} 名学生`,
        renderNavigation: true
      });
      return handleMutationResult(result);
    }

    function findHeaderRow(rows) {
      const writingHints = enabledModuleKeys().has('writing') ? ['写作', '写作成绩', '写作分数', '作文'] : [];
      const wanted = ['学号', '姓名', subjectName(), subjectText('score_ranking'), '年级排名', '班级', '得分', ...writingHints];
      return rows.slice(0, 12).findIndex(row => {
        const headers = row.map(value => String(value ?? '').trim().toLowerCase());
        return headers.some(value => wanted.includes(value)) && headers.some(value => ['学号', 'id', 'studentid', '学生编号'].includes(value));
      });
    }

    function importWritingFromWorkbooks(workbooks) {
      const task = state.writings.find(item => item.id === writingTaskId) || state.writings[0];
      if (!task) return showToast('请先新建一个写作任务，再导入成绩', 'error');
      writingTaskId = task.id;
      const rollbackSnapshot = cloneStateSnapshot();
      const rollbackContext = { currentExamId };
      const byId = new Map(state.students.map(student => [student.id, student]));
      const seen = new Set();
      let imported = 0;
      const errors = [];
      workbooks.forEach(({ name: sourceName, workbook }) => workbook.SheetNames.forEach(sheetName => {
        const rows = XLSX.utils.sheet_to_json(workbook.Sheets[sheetName], { header: 1, defval: '' });
        const headerRow = findHeaderRow(rows);
        if (headerRow < 0) { errors.push(`${sourceName}：找不到包含学号、姓名和写作成绩的表头`); return; }
        const headers = rows[headerRow].map(value => String(value ?? '').trim().toLowerCase());
        const indexOf = names => names.map(name => headers.indexOf(name)).find(index => index >= 0);
        const idIdx = indexOf(['学号', 'id', 'studentid', '学生编号']);
        const nameIdx = indexOf(['姓名', 'name', '学生姓名']);
        const classIdx = indexOf(['班级', 'class', '班级名称']);
        const scoreIdx = indexOf(['写作成绩', '写作分数', '写作', '作文成绩', '作文分数', '作文', '成绩', '分数', 'score', 'writing_score']);
        if (idIdx === undefined || nameIdx === undefined || scoreIdx === undefined) { errors.push(`${sourceName}：缺少学号、姓名或写作成绩列`); return; }
        const className = state.classes.find(cls => String(sheetName).includes(cls)) || findClassNameInText(sheetName) || '';
        for (let i = headerRow + 1; i < rows.length; i++) {
          const row = rows[i];
          const id = String(row[idIdx] ?? '').trim();
          const name = String(row[nameIdx] ?? '').trim();
          if (!id || !name || seen.has(id)) continue;
          const rawScore = row[scoreIdx];
          const score = rawScore === '' || rawScore == null ? null : Number(rawScore);
          if (!Number.isFinite(score) || score < 0 || score > Number(task.fullScore || 12)) continue;
          seen.add(id);
          const cls = resolveClassName((classIdx !== undefined ? row[classIdx] : '') || className || '');
          const student = byId.get(id) || { id, name, class: cls, english: 0, target: '', weakTags: '', phone: '', seat: '', evaluationTags: [], evaluationNote: '' };
          student.name = name;
          if (cls) student.class = cls;
          byId.set(id, student);
          if (cls && !state.classes.includes(cls)) state.classes.push(cls);
          task.scores = task.scores || {};
          task.scores[id] = score;
          imported++;
        }
      }));
      state.students = [...byId.values()];
      const result = commitMutation(() => {}, { rollbackSnapshot, rollbackContext, successMessage: `已导入${task.title}：${imported}人${errors.length ? `；${errors[0]}` : ''}`, renderNavigation: true });
      return handleMutationResult(result, () => ({ task, imported, errors }));
    }

    function inferImportedExamMeta(workbooks) {
      const sourceNames = (workbooks || []).map(item => String(item?.name || '')).join(' ');
      const today = new Date();
      const fallbackDate = today.toISOString().slice(0, 10);
      const exactDate = sourceNames.match(/(20\d{2})[-年\/.](\d{1,2})[-月\/.](\d{1,2})/);
      const monthOnly = sourceNames.match(/(?:^|[^0-9])((?:1[0-2]|[1-9])月)(?:月考|考试)?/);
      const date = exactDate
        ? `${exactDate[1]}-${String(exactDate[2]).padStart(2, '0')}-${String(exactDate[3]).padStart(2, '0')}`
        : (monthOnly ? `${today.getFullYear()}-${String(Number(monthOnly[1].replace('月', ''))).padStart(2, '0')}-01` : fallbackDate);
      let name = '';
      if (monthOnly) {
        const year = sourceNames.match(/20\d{2}/)?.[0] || String(today.getFullYear());
        name = `${year}年${monthOnly[1]}月考`;
      } else {
        const firstStem = String(workbooks?.[0]?.name || '').replace(/\.[^.]+$/, '').trim();
        name = firstStem
          .replace(/[-_ ]?(?:七年级)?\d{1,2}班(?:级)?(?:[-_ ]|$).*$/i, '')
          .replace(/[-_ ]?(?:class)\s*\d{1,2}(?:[-_ ]|$).*$/i, '')
          .trim();
      }
      if (!name || name === '导入成绩表') name = `导入考试 ${date}`;
      return { name, date };
    }

    function importFromWorkbook(wb) {
      // 兼容旧版单表调用，统一填充当前选中的考试。
      const current = getCurrentExam();
      return importFromWorkbooks([{ name: '导入成绩表.xlsx', workbook: wb }], { targetExamId: current?.id || '' });
    }

    function inferFullScoreFromWorkbooks(workbooks) {
      const totals = new Set();
      (workbooks || []).forEach(({ workbook }) => {
        (workbook?.SheetNames || []).forEach(sheetName => {
          const rows = XLSX.utils.sheet_to_json(workbook.Sheets[sheetName], { header: 1, defval: '' });
          rows.slice(0, 12).forEach(row => row.forEach(value => {
            const text = String(value ?? '');
            const match = text.match(/满分\s*([0-9]+(?:\.[0-9]+)?)/);
            if (match) totals.add(Number(match[1]));
          }));
        });
      });
      const inferred = [...totals].reduce((sum, value) => sum + value, 0);
      return inferred > 0 && inferred <= 1000 ? inferred : null;
    }

    function importedClassName(rowValue, sheetName, sourceName) {
      const raw = String(rowValue ?? '').trim();
      const fromSource = findClassNameInText(sourceName) || findClassNameInText(sheetName) || '';
      // 真实成绩表常把班级列写成“5班”，但文件名携带“九年级5班”。
      // 优先使用文件名推断出的完整年级班级编号（如 95），避免页面再次拼接“班”。
      if (fromSource && /^[一二三四五六七八九]?年级?\s*[0-9]{1,2}班$/.test(raw.replace(/[\s　]/g, ''))) {
        return resolveClassName(fromSource);
      }
      if (fromSource && /^[0-9]{1,2}班$/.test(raw.replace(/[\s　]/g, '')) && /[年级级第]/.test(String(sourceName))) {
        return resolveClassName(fromSource);
      }
      return resolveClassName(raw || fromSource || '');
    }

    function importFromWorkbooks(workbooks, options = {}) {
      const rollbackSnapshot = cloneStateSnapshot();
      const rollbackContext = { currentExamId };
      const entrance = state.exams.find(item => item.examKind === 'entrance');
      const selectedExam = options.targetExamId ? state.exams.find(item => item.id === options.targetExamId) : getCurrentExam();
      const firstImport = !state.exams.length && !entrance && !selectedExam;
      const importedMeta = inferImportedExamMeta(workbooks);
      // 正常流程：老师先在成绩管理中新建并选中考试，智能导入只填充这张考试表。
      // 只有空数据库首次导入时，才自动创建“入学考试”基线。
      const inferredFullScore = inferFullScoreFromWorkbooks(workbooks);
      const exam = selectedExam || (firstImport ? {
        id: uid(), name: '入学考试', date: importedMeta.date, fullScore: inferredFullScore || 100,
        type: 'english_total', examKind: 'entrance', scores: {}, tierLines: null, classGradeRanks: {}
      } : entrance || state.exams[0]);
      if (!state.exams.includes(exam)) state.exams.unshift(exam);
      const byId = new Map(state.students.map(student => [student.id, student]));
      const seen = new Set(); let imported = 0; let importedRanks = 0; let absent = 0; const errors = [];
      workbooks.forEach(({ name: sourceName, workbook }) => workbook.SheetNames.forEach(sheetName => {
        const rows = XLSX.utils.sheet_to_json(workbook.Sheets[sheetName], { header: 1, defval: '' });
        const headerRow = findHeaderRow(rows);
        if (headerRow < 0) { errors.push(`${sourceName}：找不到包含学号、姓名和${subjectName()}列的表头`); return; }
        const headers = rows[headerRow].map(value => String(value ?? '').trim().toLowerCase());
        const indexOf = names => names.map(name => headers.indexOf(name)).find(index => index >= 0);
        const idIdx = indexOf(['学号', 'id', 'studentid', '学生编号']);
        const nameIdx = indexOf(['姓名', 'name', '学生姓名']);
        const classIdx = indexOf(['班级', 'class', '班级名称']);
        const scoreIdx = indexOf(subjectScoreImportAliases());
        const gradeRankIdx = indexOf(subjectRankImportAliases());
        const absentIdx = indexOf(['缺考', '出勤状态', 'attendance_status', 'status']);
        const className = state.classes.find(c => String(sheetName).includes(c)) || findClassNameInText(sheetName) || '';
        for (let i = headerRow + 1; i < rows.length; i++) {
          const row = rows[i]; const id = String(row[idIdx] ?? '').trim(); const name = String(row[nameIdx] ?? '').trim();
          if (!id || !name || seen.has(id)) continue;
          seen.add(id);
          const cls = importedClassName((classIdx !== undefined ? row[classIdx] : '') || className || '', sheetName, sourceName);
          const rawScore = scoreIdx === undefined ? '' : row[scoreIdx];
          const statusText = absentIdx === undefined ? '' : String(row[absentIdx] ?? '').trim();
          const isAbsent = /缺考|未考|absent/i.test(statusText) || (rawScore !== '' && /缺考|未考|absent/i.test(String(rawScore)));
          const numericScore = rawScore !== '' && rawScore != null && Number.isFinite(Number(rawScore)) ? Number(rawScore) : null;
          const rawRank = gradeRankIdx === undefined ? '' : row[gradeRankIdx];
          const numericRank = rawRank === '' || rawRank == null ? null : Number(rawRank);
          const gradeRank = Number.isInteger(numericRank) && numericRank > 0 ? numericRank : null;
          const student = byId.get(id) || { id, name, class: cls, english: 0, target: '', weakTags: '', phone: '', seat: '', evaluationTags: [], evaluationNote: '' };
          student.name = name; if (cls) student.class = cls; byId.set(id, student);
          if (cls && !state.classes.includes(cls)) state.classes.push(cls);
          if (isAbsent || numericScore === null) { exam.scores[id] = { 英语: '', gradeRank, attendanceStatus: 'absent', classAtExam: cls || student.class || null }; absent++; }
          else if (numericScore >= 0 && numericScore <= exam.fullScore) { exam.scores[id] = { 英语: numericScore, gradeRank, attendanceStatus: 'present', classAtExam: cls || student.class || null }; student.english = numericScore; }
          if (gradeRank !== null) importedRanks++;
          imported++;
        }
      }));
      state.students = [...byId.values()]; state.currentExamId = currentExamId = exam.id;
      const result = commitMutation(() => {}, { rollbackSnapshot, rollbackContext, successMessage: `已导入${exam.name}：${imported}人，年级排名${importedRanks}人${absent ? `，缺考${absent}人` : ''}${errors.length ? `；${errors[0]}` : ''}`, renderNavigation: true });
      return handleMutationResult(result, () => ({ exam, imported, importedRanks, absent, errors }));
    }

    function openExportCenter() {
      openModal('导出中心', `
        <div style="display:grid;gap:12px;">
          <button class="btn btn-primary" data-act="export-students">导出 学生名单 Excel</button>
          <button class="btn btn-primary" data-act="export-scores">导出 成绩分析 Excel</button>
          <button class="btn btn-primary" data-act="export-report">导出 班级薄弱项 Word</button>
          <button class="btn" data-act="export-json">导出当前学期 JSON</button>
        </div>
      `);
    }

    document.addEventListener('click', e => {
      const t = e.target.closest('[data-act]');
      const act = t ? t.dataset.act : null;
      if (act === 'export-students') void runLocalActionOnce('export-students', () => exportStudents(), t);
      else if (act === 'export-scores') void runLocalActionOnce('export-scores', () => exportScores(), t);
      else if (act === 'export-records') void runLocalActionOnce(`export-records:${t.dataset.module || ''}`, () => exportAllRecords(t.dataset.module), t);
      else if (act === 'export-report') void runLocalActionOnce('export-report', () => exportReport(), t);
    });

    async function exportStudents() {
      try { await ensureXlsx(); } catch (error) { showToast('本地 Excel 组件加载失败，请重试', 'error'); return; }
      const ws = XLSX.utils.json_to_sheet(state.students.map(s=>({学号:s.id,姓名:s.name,班级:s.class,[subjectText('entrance_score')]:s.english,家长电话:s.phone})));
      const wb = XLSX.utils.book_new(); XLSX.utils.book_append_sheet(wb, ws, '学生名单');
      XLSX.writeFile(wb, `学生名单_${new Date().toISOString().slice(0,10)}.xlsx`);
      closeModal();
    }

    async function exportScores() {
      try { await ensureXlsx(); } catch (error) { showToast('本地 Excel 组件加载失败，请重试', 'error'); return; }
      const exam = getCurrentExam();
      if (!exam) return showToast('请先创建考试', 'error');
      const scored = getScoredStudents(exam, state.students);
      const classRanks = calculateClassRanks(exam, state.students);
      const detail = state.students.map(s => ({ 学号:s.id, 姓名:s.name, 班级:formatClassLabel(s.class), [subjectText('score_total')]: getExamScore(exam, s) ?? '', 班级排名: classRanks[s.id] ?? '', 年级排名: getStudentGradeRank(exam, s) ?? '' }));
      const ws1 = XLSX.utils.json_to_sheet(detail);
      const values = scored.map(item=>item.score);
      const ws2 = XLSX.utils.json_to_sheet([{指标:'考试名称',数值:exam.name},{指标:'平均分',数值:avg(values)},{指标:'最高分',数值:values.length ? Math.max(...values) : ''},{指标:'最低分',数值:values.length ? Math.min(...values) : ''},{指标:'实考人数',数值:values.length},{指标:'缺考人数',数值:state.students.length-values.length}]);
      const wb = XLSX.utils.book_new();
      XLSX.utils.book_append_sheet(wb, ws1, '成绩明细');
      XLSX.utils.book_append_sheet(wb, ws2, '统计汇总');
      XLSX.writeFile(wb, `成绩分析_${exam.name}_${new Date().toISOString().slice(0,10)}.xlsx`);
      closeModal();
    }

    async function exportAllRecords(module) {
      try { await ensureXlsx(); } catch (error) { showToast('本地 Excel 组件加载失败，请重试', 'error'); return; }
      const definitions = {
        dictation: { title: '默写全部记录', columns: (state.dictationNames || []).map((label, index) => ({ id: index, label })), value: student => column => (state.dictation?.[student.id]?.[column.id] ?? '') },
        recite: { title: '背诵全部记录', columns: (state.recitations || []).map(task => ({ id: task.id, label: task.title || '未命名背诵' })), value: student => column => normalizeReciteStatus((state.recitations || []).find(task => task.id === column.id)?.status?.[student.id]).level || '' },
        writing: { title: '写作全部记录', columns: (state.writings || []).map(task => ({ id: task.id, label: task.title || '未命名写作' })), value: student => column => (state.writings || []).find(task => task.id === column.id)?.scores?.[student.id] ?? '' },
        homework: { title: '作业全部记录', columns: (state.homeworkTasks || []).map(task => ({ id: task.id, label: task.name || '未命名作业' })), value: student => column => ((state.homeworkRecords?.[column.id] || {})[student.id] === true ? '已交' : '未交') },
        score: { title: '考试全部记录', columns: (state.exams || []).map(exam => ({ id: exam.id, label: exam.name || '未命名考试' })), value: student => column => { const exam = (state.exams || []).find(item => item.id === column.id); return exam ? (getExamScore(exam, student) ?? '') : ''; } }
      };
      const definition = definitions[module];
      if (!definition) return;
      const className = module === 'dictation' ? normalizeClassFilter(dictationClass) : module === 'recite' ? normalizeClassFilter(reciteClass) : module === 'writing' ? normalizeClassFilter(writingClass) : module === 'homework' ? normalizeClassFilter(homeworkClass) : getScoreClass();
      const students = getVisibleStudents(className).sort((a, b) => String(a.id).localeCompare(String(b.id)));
      if (!definition.columns.length) return showToast('暂无可导出的历史记录', 'error');
      const rows = students.map(student => {
        const row = { 班级: formatClassLabel(student.class || ''), 学号: student.id, 姓名: student.name };
        definition.columns.forEach(column => { row[column.label] = definition.value(student)(column); });
        return row;
      });
      const ws = XLSX.utils.json_to_sheet(rows);
      const wb = XLSX.utils.book_new();
      XLSX.utils.book_append_sheet(wb, ws, '全部记录');
      XLSX.writeFile(wb, `${definition.title}_${new Date().toISOString().slice(0, 10)}.xlsx`);
      showToast('全部记录已导出', 'success');
    }

    async function exportReport() {
      try { await ensureJSZip(); } catch (error) { showToast('本地 ZIP 组件加载失败，请重试', 'error'); return; }
      const zip = new JSZip();
      const xml = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
        <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
          <w:body>
            <w:p><w:pPr><w:pStyle w:val="Title"/></w:pPr><w:r><w:t>班级薄弱项报告</w:t></w:r></w:p>
            <w:p><w:r><w:t>生成时间：${new Date().toLocaleString()}</w:t></w:r></w:p>
            <w:p><w:r><w:t>班级均分：${avg(state.students.map(s=>s.english))}</w:t></w:r></w:p>
            ${state.errors.map(e=>`<w:p><w:r><w:t>${escapeXml(`${e.qnum} ${e.type} ${e.point}：错误${e.count}人，${e.reason}`)}</w:t></w:r></w:p>`).join('')}
            <w:sectPr><w:pgSz w:w="11906" w:h="16838"/></w:sectPr>
          </w:body>
        </w:document>`;
      zip.file('word/document.xml', xml);
      zip.file('[Content_Types].xml', '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>');
      zip.generateAsync({ type: 'blob' }).then(blob => {
        const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = `班级薄弱项报告_${new Date().toISOString().slice(0,10)}.docx`; a.click();
      });
      closeModal();
    }

    async function exportJSON() {
      try {
        let exportState = state;
        if (DATABASE_MODE) {
          // 先等当前页面的异步写入结束，再从数据库读取已提交的版本。
          // 直接序列化内存 state 会在刚编辑完数据时导出旧值或未保存值。
          if (databaseSaveChain) await databaseSaveChain;
          const payload = await apiRequest(`/api/v1/terms/${currentTermId}/workspace-state`);
          if (!payload || !payload.state || typeof payload.state !== 'object') {
            throw new Error('当前学期没有可导出的工作台数据');
          }
          exportState = payload.state;
        }
        const blob = new Blob([JSON.stringify(exportState, null, 2)], { type: 'application/json;charset=utf-8' });
        const termCode = availableTerms.find(term => Number(term.id) === Number(currentTermId))?.code || 'local';
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = `workbench_term_${termCode}_${new Date().toISOString().slice(0, 10)}.json`;
        document.body.appendChild(link);
        link.click();
        link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 60000);
        closeModal();
        showToast('当前学期工作台数据已导出', 'success');
      } catch (error) {
        showToast(`JSON 导出失败：${error.message || error}`, 'error');
      }
    }

    function importJSON() {
      document.getElementById('fileInput').accept = '.json';
      document.getElementById('fileInput').click();
      setTimeout(() => { document.getElementById('fileInput').accept = '.xlsx,.xls,.csv,.json'; }, 1000);
    }

    function formatTermPeriod(term) {
      if (!term?.starts_on && !term?.ends_on) return '未设置日期';
      return `${term.starts_on || '—'} 至 ${term.ends_on || '—'}`;
    }

    async function openTermManagerModal() {
      if (!DATABASE_MODE) return;
      try {
        managedTerms = await apiRequest('/api/v1/terms?include_archived=true');
        const rows = managedTerms.map(term => {
          const current = Number(term.id) === Number(currentTermId);
          const archived = term.status === 'archived';
          const status = current ? '<span class="badge badge-blue">当前</span>' : archived ? '<span class="badge badge-gray">已删除（可恢复）</span>' : '<span class="badge badge-green">可用</span>';
          const permanentDelete = current ? '' : `<button class="btn btn-sm btn-danger" data-act="term-manage-delete-permanent" data-id="${term.id}">永久删除</button>`;
          const cacheButton = `<button class="btn btn-sm btn-secondary" data-act="term-manage-cache" data-id="${term.id}">清理缓存</button>`;
          const actions = archived
            ? `${cacheButton}<button class="btn btn-sm" data-act="term-manage-restore" data-id="${term.id}">恢复</button>${permanentDelete}`
            : `${current ? '' : `<button class="btn btn-sm" data-act="term-manage-switch" data-id="${term.id}">切换</button>`}<button class="btn btn-sm" data-act="term-manage-edit" data-id="${term.id}">编辑</button>${cacheButton}${current ? '' : `<button class="btn btn-sm" data-act="term-manage-archive" data-id="${term.id}">删除</button>`}${permanentDelete}`;
          return `<tr><td><strong>${escapeHtml(term.name)}</strong><div class="subtle">${escapeHtml(term.code)}</div></td><td>${escapeHtml(formatTermPeriod(term))}</td><td>${status}</td><td class="text-right">${actions}</td></tr>`;
        }).join('');
        openModal('学期管理', `<div class="archived-table-wrap"><table><thead><tr><th>学期</th><th>日期</th><th>状态</th><th class="text-right">操作</th></tr></thead><tbody>${rows}</tbody></table></div>`, '<button class="btn" data-act="about-close">关闭</button><button class="btn btn-primary" data-act="term-add">新建学期</button>');
      } catch (error) {
        showToast(error.message || '学期列表加载失败', 'error');
      }
    }

    function cacheCountItems(summary) {
      const labels = {
        exam_paper_memories: '试卷 AI 记忆', sessions: 'TeachMate 会话',
        messages: '对话消息', analysis_runs: '分析运行记录',
        analysis_evidence: '分析证据缓存', analysis_events: '运行过程记录',
        llm_usage_records: '模型用量记录', student_profile_drafts: '学生画像草稿',
        evaluation_drafts: '学生评价草稿', error_cause_candidates: '错因候选',
        message_attachments: '临时消息附件关联'
      };
      return Object.keys(labels).filter(key => Number(summary[key] || 0) > 0)
        .map(key => `<li>${labels[key]}：${Number(summary[key])}</li>`).join('')
        || '<li>当前没有可清理的 AI 缓存</li>';
    }

    async function openTermCacheModal(id) {
      const term = managedTerms.find(item => Number(item.id) === Number(id));
      if (!term) return;
      try {
        const summary = await apiRequest(`/api/v1/terms/${Number(id)}/cache`);
        const hasCache = Object.keys(summary).some(key => [
          'exam_paper_memories', 'sessions', 'messages', 'analysis_runs',
          'analysis_evidence', 'analysis_events', 'llm_usage_records',
          'student_profile_drafts', 'evaluation_drafts', 'error_cause_candidates',
          'message_attachments'
        ].includes(key) && Number(summary[key] || 0) > 0);
        openModal('清理 TeachMate 缓存', `
          <p>将清理“${escapeHtml(term.name)}”中的 AI 临时数据：</p>
          <ul class="subtle" style="margin:10px 0 14px;padding-left:20px;">${cacheCountItems(summary)}</ul>
          <p class="subtle">不会删除成绩、考试结构、正式原卷附件、系统知识库，或教师已确认的学生画像和评价。清理后该学期的 TeachMate 对话记录将无法恢复。</p>
          ${summary.active_runs ? '<p class="danger-note">当前仍有分析正在运行，完成或取消后才能清理。</p>' : ''}
        `, `<button class="btn" data-act="term-manage">取消</button><button class="btn btn-danger" data-act="term-manage-cache-confirm" data-id="${term.id}" ${hasCache && !summary.active_runs ? '' : 'disabled'}>确认清理</button>`);
      } catch (error) {
        showToast(error.message || '缓存信息加载失败', 'error');
      }
    }

    async function clearManagedTermCache(id) {
      const term = managedTerms.find(item => Number(item.id) === Number(id));
      const button = document.querySelector('[data-act="term-manage-cache-confirm"]');
      if (!term || !button || button.disabled) return;
      button.disabled = true;
      try {
        const result = await apiRequest(`/api/v1/terms/${Number(id)}/cache/clear`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ confirm: true })
        });
        closeModal();
        await openTermManagerModal();
        const removed = Object.values(result.removed || {}).reduce((sum, value) => sum + Number(value || 0), 0);
        showToast(`已清理“${term.name}”的 AI 缓存${removed ? `（${removed} 项）` : ''}`);
      } catch (error) {
        button.disabled = false;
        showToast(error.message || '缓存清理失败', 'error');
      }
    }

    function openTermEditModal(id) {
      const term = managedTerms.find(item => Number(item.id) === Number(id));
      if (!term || term.status !== 'active') return;
      openModal('编辑学期', `
        <div class="form-row">
          <div class="form-group"><label>学期名称</label><input id="m-term-edit-name" maxlength="100" value="${escapeAttr(term.name)}"></div>
          <div class="form-group"><label>学期代码</label><input id="m-term-edit-code" maxlength="50" value="${escapeAttr(term.code)}"></div>
        </div>
        <div class="form-row">
          <div class="form-group"><label>开始日期</label><input type="date" id="m-term-edit-start" value="${escapeAttr(term.starts_on || '')}"></div>
          <div class="form-group"><label>结束日期</label><input type="date" id="m-term-edit-end" value="${escapeAttr(term.ends_on || '')}"></div>
        </div>
      `, '<button class="btn" data-act="term-manage">返回</button><button class="btn btn-primary" id="m-term-edit-save">保存</button>');
      document.getElementById('m-term-edit-save').onclick = async event => {
        const name = document.getElementById('m-term-edit-name').value.trim();
        const code = document.getElementById('m-term-edit-code').value.trim();
        const startsOn = document.getElementById('m-term-edit-start').value || null;
        const endsOn = document.getElementById('m-term-edit-end').value || null;
        if (!name) return showToast('请填写学期名称', 'error');
        if (!/^[a-zA-Z0-9_-]+$/.test(code)) return showToast('学期代码格式不正确', 'error');
        if (startsOn && endsOn && startsOn > endsOn) return showToast('开始日期不能晚于结束日期', 'error');
        event.currentTarget.disabled = true;
        try {
          const updated = await apiRequest(`/api/v1/terms/${term.id}`, {
            method: 'PATCH', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, code, starts_on: startsOn, ends_on: endsOn })
          });
          availableTerms = availableTerms.map(item => Number(item.id) === Number(updated.id) ? updated : item);
          renderTermSwitcher();
          render();
          await openTermManagerModal();
          showToast('学期信息已保存');
        } catch (error) {
          event.currentTarget.disabled = false;
          showToast(error.message || '保存失败', 'error');
        }
      };
    }

    function openTermArchiveModal(id) {
      const term = managedTerms.find(item => Number(item.id) === Number(id));
      if (!term || Number(term.id) === Number(currentTermId)) return;
      openModal('删除学期', `<p>删除“${escapeHtml(term.name)}”？</p><p class="subtle">为保护数据，删除后会进入归档状态，数据不会丢失；之后可在“学期管理”中恢复。当前学期需要先切换到其他学期后才能删除。</p>`, `<button class="btn" data-act="term-manage">取消</button><button class="btn btn-danger" data-act="term-manage-archive-confirm" data-id="${term.id}">确认删除</button>`);
    }

    async function archiveManagedTerm(id) {
      try {
        await apiRequest(`/api/v1/terms/${Number(id)}/archive`, { method: 'POST' });
        availableTerms = await apiRequest('/api/v1/terms');
        renderTermSwitcher();
        await openTermManagerModal();
        showToast('学期已删除（可恢复）');
      } catch (error) {
        showToast(error.message || '归档失败', 'error');
      }
    }

    function openTermPermanentDeleteModal(id) {
      const term = managedTerms.find(item => Number(item.id) === Number(id));
      if (!term || Number(term.id) === Number(currentTermId)) return;
      openModal('永久删除学期', `<p>即将永久删除“${escapeHtml(term.name)}”及其班级、考试、成绩、待办和本学期教学记录。</p><p class="danger-note">此操作不可恢复。请先导出备份，并在下方输入学期代码 <strong>${escapeHtml(term.code)}</strong> 进行确认。</p><div class="form-group"><label for="m-term-delete-code">学期代码</label><input id="m-term-delete-code" autocomplete="off" placeholder="输入学期代码确认"></div>`, `<button class="btn" data-act="term-manage">取消</button><button class="btn btn-danger" data-act="term-manage-delete-permanent-confirm" data-id="${term.id}">永久删除</button>`);
      setTimeout(() => document.getElementById('m-term-delete-code')?.focus(), 0);
    }

    async function permanentlyDeleteManagedTerm(id) {
      const term = managedTerms.find(item => Number(item.id) === Number(id));
      const input = document.getElementById('m-term-delete-code');
      const button = document.querySelector('[data-act="term-manage-delete-permanent-confirm"]');
      if (!term || !input || input.value.trim() !== term.code) {
        showToast('请输入正确的学期代码确认删除', 'error');
        input?.focus();
        return;
      }
      if (button) button.disabled = true;
      try {
        await apiRequest(`/api/v1/terms/${Number(id)}`, {
          method: 'DELETE',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ confirmation_code: input.value.trim() })
        });
        availableTerms = await apiRequest('/api/v1/terms');
        renderTermSwitcher();
        await openTermManagerModal();
        showToast('学期及其数据已永久删除');
      } catch (error) {
        if (button) button.disabled = false;
        showToast(error.message || '永久删除失败', 'error');
      }
    }

    async function restoreManagedTerm(id) {
      try {
        await apiRequest(`/api/v1/terms/${Number(id)}/restore`, { method: 'POST' });
        availableTerms = await apiRequest('/api/v1/terms');
        renderTermSwitcher();
        await openTermManagerModal();
        showToast('学期已恢复');
      } catch (error) {
        showToast(error.message || '恢复失败', 'error');
      }
    }

    function openTermModal() {
      if (!DATABASE_MODE) return;
      const current = availableTerms.find(term => term.id === currentTermId);
      openModal('新建学期', `
        <div class="form-row">
          <div class="form-group"><label>学期名称</label><input id="m-term-name" maxlength="100" placeholder="例如：2026年第一学期"></div>
          <div class="form-group"><label>学期代码</label><input id="m-term-code" maxlength="50" placeholder="例如：2026-S1"></div>
        </div>
        <div class="form-row">
          <div class="form-group"><label>开始日期</label><input type="date" id="m-term-start"></div>
          <div class="form-group"><label>结束日期</label><input type="date" id="m-term-end"></div>
        </div>
        <label class="check-row"><input type="checkbox" id="m-term-clone" ${current ? 'checked' : ''}> 复制当前学期的班级和学生名单</label>
        <p style="color:var(--md-text-secondary);font-size:13px;">考试、成绩、待办、默写、背诵、写作和原卷不会复制。</p>
      `, '<button class="btn" onclick="closeModal()">取消</button><button class="btn btn-primary" id="m-term-save">创建并切换</button>');
      document.getElementById('m-term-save').onclick = async event => {
        const button = event.currentTarget;
        const name = document.getElementById('m-term-name').value.trim();
        const code = document.getElementById('m-term-code').value.trim();
        const startsOn = document.getElementById('m-term-start').value || null;
        const endsOn = document.getElementById('m-term-end').value || null;
        const clone = document.getElementById('m-term-clone').checked;
        if (!name) return showToast('请填写学期名称', 'error');
        if (!/^[a-zA-Z0-9_-]+$/.test(code)) return showToast('学期代码只能包含字母、数字、短横线和下划线', 'error');
        if (startsOn && endsOn && startsOn > endsOn) return showToast('开始日期不能晚于结束日期', 'error');
        button.disabled = true;
        try {
          const created = await apiRequest('/api/v1/terms', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              code,
              name,
              starts_on: startsOn,
              ends_on: endsOn,
              clone_from_term_id: clone ? currentTermId : null,
              clone_classes: clone,
              clone_enrollments: clone
            })
          });
          closeModal();
          await switchTerm(created.id);
        } catch (error) {
          button.disabled = false;
          showToast(error.message || '学期创建失败', 'error');
        }
      };
    }

    // ================= Toast =================
    function showToast(msg, type='success') {
      const t = document.getElementById('toast');
      t.textContent = msg; t.className = 'toast ' + type + ' show';
      setTimeout(() => t.classList.remove('show'), 2500);
    }

    // ================= 日常作业弹窗 =================
    function openHomeworkAddModal() {
      const today = new Date().toISOString().slice(0, 10);
      openModal('添加作业', `
        <div class="form-row">
          <div class="form-group"><label>作业名称</label><input id="m-hw-name" placeholder="如：8月12日 Unit 3 词汇抄写" autofocus></div>
          <div class="form-group"><label>日期</label><input type="date" id="m-hw-date" value="${today}"></div>
        </div>
        <p style="color:var(--md-text-secondary);font-size:13px;margin-top:8px;">创建后所有学生默认为"未交"，可在表格中逐个或批量标记。</p>
      `, '<button class="btn" data-act="hw-add-cancel">取消</button><button class="btn btn-primary" data-act="hw-add-confirm">创建</button>');
      const nameInput = document.getElementById('m-hw-name');
      if (nameInput) nameInput.focus();
      // 回车确认
      nameInput?.addEventListener('keydown', e => {
        if (e.key === 'Enter') {
          e.preventDefault();
          document.querySelector('[data-act="hw-add-confirm"]')?.click();
        }
      });
    }

    function openHomeworkDetailModal(taskId) {
      const task = (state.homeworkTasks || []).find(t => t.id === taskId);
      if (!task) return;
      const records = state.homeworkRecords[taskId] || {};
      const allStudents = getFilteredStudents('');
      const submitted = allStudents.filter(s => records[s.id] === true);
      const notSubmitted = allStudents.filter(s => records[s.id] !== true);
      const rate = allStudents.length ? Math.round(submitted.length / allStudents.length * 100) : 0;
      openModal(`${escapeHtml(task.name)} — 提交详情`, `
        <div class="homework-detail-stats">
          <div class="hw-stat-card hw-stat-done"><div class="hw-stat-num">${submitted.length}</div><div class="hw-stat-label">已交</div></div>
          <div class="hw-stat-card hw-stat-undone"><div class="hw-stat-num">${notSubmitted.length}</div><div class="hw-stat-label">未交</div></div>
          <div class="hw-stat-card hw-stat-rate"><div class="hw-stat-num">${rate}%</div><div class="hw-stat-label">提交率</div></div>
          <div class="hw-stat-card hw-stat-total"><div class="hw-stat-num">${allStudents.length}</div><div class="hw-stat-label">总人数</div></div>
        </div>
        ${task.date ? `<p style="color:var(--md-text-secondary);margin-bottom:12px;">日期：${escapeHtml(task.date)}</p>` : ''}
        <div class="homework-detail-section">
          <h4 style="margin:0 0 8px;color:var(--md-text-secondary);">未交名单（${notSubmitted.length} 人）</h4>
          ${notSubmitted.length ? `<table class="homework-detail-table"><thead><tr><th>学号</th><th>姓名</th><th>班级</th></tr></thead><tbody>${notSubmitted.map(s => `<tr><td>${escapeHtml(s.id)}</td><td>${escapeHtml(s.name)}</td><td>${escapeHtml(s.class || '')}</td></tr>`).join('')}</tbody></table>` : '<div class="empty" style="padding:16px;text-align:center;color:var(--md-text-secondary);">全部已交</div>'}
        </div>
        <details style="margin-top:12px;">
          <summary style="cursor:pointer;color:var(--md-text-secondary);font-size:13px;">已交名单（${submitted.length} 人）</summary>
          ${submitted.length ? `<table class="homework-detail-table" style="margin-top:8px;"><thead><tr><th>学号</th><th>姓名</th><th>班级</th></tr></thead><tbody>${submitted.map(s => `<tr><td>${escapeHtml(s.id)}</td><td>${escapeHtml(s.name)}</td><td>${escapeHtml(s.class || '')}</td></tr>`).join('')}</tbody></table>` : '<div class="empty" style="padding:12px;text-align:center;color:var(--md-text-secondary);">暂无</div>'}
        </details>
      `, '<button class="btn btn-primary" data-act="hw-delete-cancel">关闭</button>');
    }

    function openHomeworkDeleteModal() {
      const tasks = state.homeworkTasks || [];
      if (!tasks.length) return showToast('没有可删除的作业', 'error');
      openModal('删除作业', `
        <p style="margin-bottom:12px;">选择要删除的作业：</p>
        <select id="m-hw-delete" class="form-control" style="width:100%;padding:8px;margin-bottom:12px;">
          ${tasks.map(t => `<option value="${escapeAttr(t.id)}">${escapeHtml(t.name)}${t.date ? ' (' + escapeHtml(t.date) + ')' : ''}</option>`).join('')}
        </select>
        <p style="color:var(--md-text-secondary);font-size:13px;">删除后无法恢复，该作业的所有提交记录也会一并删除。</p>
      `, '<button class="btn" data-act="hw-delete-cancel">取消</button><button class="btn btn-primary" data-act="hw-delete-confirm" style="background:var(--md-sys-color-error,#dc3545);">删除</button>');
      // 默认选中当前作业
      if (homeworkActiveTaskId) {
        const sel = document.getElementById('m-hw-delete');
        if (sel) sel.value = homeworkActiveTaskId;
      }
    }

    // ================= 初始化 =================
    function scheduleMoniAutoRefresh() {
      // 当前 MONI 适配器读取英语单科字段；切到其他学科时不把英语成绩混入当前工作台。
      if (!DATABASE_MODE || subjectKey !== 'english') return;
      let attempts = 0;
      const refresh = async () => {
        attempts += 1;
        if (attempts > 3 || subjectKey !== 'english' || pendingScoreEdits.size || databaseWriteBlocked) return;
        const beforeRevision = databaseRevision;
        try {
          await loadData();
          if (databaseRevision !== beforeRevision) {
            resetTermViewState();
            renderNav();
            render();
            showToast('已刷新启动时自动同步的 MONI 数据', 'success');
            return;
          }
        } catch (error) {
          // 自动刷新失败不阻塞正常使用，下一次启动仍会重试。
        }
        window.setTimeout(refresh, 5000);
      };
      window.setTimeout(refresh, 5000);
    }

    // ================= 学科选择 =================
    // 首次进入时选一次（必须点确定才会落库），之后在设置页的「任教学科」下拉里改。
    function openSubjectPicker() {
      const choices = subjectCatalog.map(item => `
        <label class="subject-choice">
          <input type="radio" name="subject-choice" value="${escapeAttr(item.key)}" ${item.key === subjectKey ? 'checked' : ''}>
          <span class="subject-choice-text"><strong>${escapeHtml(item.label)}</strong><small>${escapeHtml(item.teacher_subject_default)}</small></span>
        </label>`).join('');
      openModal('选择任教学科', `
        <div class="subject-picker">
          <p class="subject-picker-intro">学科决定界面用语与功能模块：默写、背诵、写作只对语文与英语开放。录入考试、成绩或画像后，为保护已有数据，本工作区会固定学科；另一学科请使用独立工作区。</p>
          <div class="subject-choice-grid" role="radiogroup" aria-label="任教学科">${choices}</div>
        </div>
      `, '<button class="btn btn-primary" data-act="subject-confirm">确定</button>');
      setTimeout(() => document.querySelector('#modal input[name="subject-choice"]:checked')?.focus(), 0);
    }

    async function confirmSubjectChoice() {
      const picked = document.querySelector('#modal input[name="subject-choice"]:checked');
      if (!picked) return showToast('请选择任教学科', 'error');
      const button = document.querySelector('#modal [data-act="subject-confirm"]');
      if (button) button.disabled = true;
      try {
        // 即使选的还是默认学科也要落库，否则下次进入还会再弹一次。
        await setSubjectKey(picked.value, { persist: true });
      } catch (error) {
        if (button) button.disabled = false;
        showToast(error && error.message ? error.message : '学科保存失败', 'error');
        return false;
      }
      renderNav();
      render();
      showToast(`已设为${subjectName()}老师`, 'success');
      closeModal();
      return true;
    }

    // 设置页的「任教学科」下拉：改完立即生效，不需要再点保存设置。
    async function applySubjectFromSettings(nextKey) {
      try {
        await setSubjectKey(nextKey, { persist: true });
      } catch (error) {
        showToast(error && error.message ? error.message : '学科保存失败', 'error');
        render();
        return false;
      }
      renderNav();
      showToast(`已切换到${subjectName()}`, 'success');
      // 当前模块可能已被新学科裁掉，回到仪表盘而不是停在空页面。
      if (!enabledModuleKeys().has(curModule)) {
        curModule = 'dash';
        renderNav();
      }
      render();
      return true;
    }

    async function init() {
      try {
        setActiveTab(activeTab);
        await loadData();
        // 学科必须在首次渲染前取到：导航模块裁剪与界面用语都依赖它。
        await loadSubjectCatalog();
        renderNav();
        initEvents();
        render();
        if (activeTab === 'teachmate') initTeachMate();
        scheduleMoniAutoRefresh();
        // 老师还没选过学科时先选一次；选过就不再打扰。
        if (!subjectChosen) openSubjectPicker();
      } catch (error) {
        console.error(error);
        state = createDefaultState();
        setActiveTab(activeTab);
        renderNav();
        initEvents();
        render();
        if (error.code === 'STALE_SESSION' || error.status === 401) {
          openModal('页面已失效', '<p>这是上一次启动保留的旧页面。</p><p>请关闭此标签页，并使用本次启动自动打开的新页面。</p>');
        } else {
          openModal('数据库连接失败', `<p>${escapeHtml(error.message)}</p><p>请关闭此页面，然后重新启动工作台。</p>`);
        }
      }
    }
    init();
