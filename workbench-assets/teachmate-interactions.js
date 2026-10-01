    // ================= TeachMate 交互层 =================
    // 替换旧的 sendTeachMateMessage 空壳。
    // 实现：会话列表加载、新建对话、消息发送、运行轮询、取消、重试、证据查看。

    var tmViewUnsub = null;
    var tmSearchPanelOpen = false;
    var tmConvMenuSessionId = null;  // 当前打开「更多操作」菜单的会话 id
    var tmArchivedSessions = [];
    var tmArchiveLoaded = false;
    var tmArchiveDeleteFromInline = false;
    var tmArchivePendingDeleteIds = [];
    var tmPersonalizationBound = false;
    var tmFileDropBound = false;
    var tmPendingPluginArchiveFile = null;
    var tmSessionLoadToken = 0;
    // 消息区被教师主动滚动时递增；视图重绘据此避免覆盖人工位置。
    var tmScrollInteractionEpoch = 0;

    function tmScrollMessagesToBottom() {
      var area = document.getElementById('tmMessages');
      if (!area) return;
      var scroll = function () { area.scrollTop = Math.max(0, area.scrollHeight - area.clientHeight); };
      scroll();
      if (typeof window.requestAnimationFrame === 'function') window.requestAnimationFrame(scroll);
    }

    /** 初始化 TeachMate：加载会话列表，订阅状态变化 */
    async function initTeachMate() {
      // 订阅状态变化，自动刷新视图
      if (tmViewUnsub) tmViewUnsub();
      tmViewUnsub = teachMateState.subscribe(function () {
        if (activeTab === 'teachmate') render();
      });

      // 加载会话列表
      try {
        var sessions = await teachMateApi.listSessions(currentTermId);
        teachMateState.setSessions(sessions || []);
        if (typeof teachMateApi.getSessionPreferences === 'function' && currentTermId) {
          teachMateApi.getSessionPreferences(currentTermId).then(function (preferences) {
            if (preferences && typeof teachMateState.setSessionPreferences === 'function') {
              teachMateState.setSessionPreferences(preferences);
            }
          }).catch(function () {});
        }
        // 初次进入 TeachMate 时，列表请求晚于首屏导航渲染；同步重绘导航，
        // 让历史对话直接出现，不必再点击左侧区域触发一次重绘。
        if (typeof renderNav === 'function' && activeTab === 'teachmate') renderNav();
      } catch (e) {
        console.error('TeachMate: failed to load sessions', e);
        teachMateState.setSessions([]);
        if (typeof renderNav === 'function' && activeTab === 'teachmate') renderNav();
      }

      // 加载能力列表和 provider 信息（非阻塞）
      teachMateApi.listCapabilities().then(function (caps) {
        teachMateState.setCapabilities(caps || []);
      }).catch(function () {});
      teachMateApi.listPlugins().then(function (payload) {
        teachMateState.setPlugins(payload || []);
      }).catch(function () {
        // 插件宿主不可用时不影响普通 TeachMate 对话；设置页会显示空状态。
        teachMateState.setPlugins([]);
      });
      if (typeof teachMateApi.getSettings === 'function') {
        teachMateApi.getSettings().then(function (settings) {
          teachMateState.setPersonalization(settings || {});
          if (typeof state !== 'undefined' && state.teacher && settings) {
            if (settings.teacher_name) state.teacher.name = settings.teacher_name;
            if (settings.subject && (!settings.subject_key || settings.subject_key === subjectKey)) {
              state.teacher.subject = settings.subject;
            }
          }
        }).catch(function () {});
      }
      teachMateApi.getProviderInfo().then(function (info) {
        teachMateState.setProviderInfo(info);
      }).catch(function () {});
      teachMateApi.listModelProfiles().then(function (models) {
        teachMateState.setSavedModels(models || {});
      }).catch(function () {});
      // 考试选择器只读取当前学期的数据库考试，不回退到可能过期的工作区快照。
      tmRefreshAvailableExams(currentTermId);
      if (typeof teachMateTasks !== 'undefined') teachMateTasks.refresh(true);

      // U5: 数据就绪检查 + a11y 状态播报（非阻塞）
      if (typeof teachMateOnboarding !== 'undefined' && typeof teachMateState.setDataReady === 'function') {
        teachMateOnboarding.checkDataReadiness().then(function (r) {
          teachMateState.setDataReady(r);
        }).catch(function () {});
      }
      if (typeof teachMateA11y !== 'undefined') {
        teachMateA11y.subscribeStatus();
        teachMateA11y.wrapModalFocus();
      }

      if (!tmPersonalizationBound) {
        document.addEventListener('change', function (event) {
          if (!event.target || event.target.id !== 'tm-settings-tone') return;
          var wrap = document.getElementById('tm-settings-custom-tone-wrap');
          if (wrap) wrap.hidden = event.target.value !== 'custom';
        });
        tmPersonalizationBound = true;
      }

      // 聊天框支持拖入教学资料；ZIP 交给插件安装确认流程，不进入附件解析。
      if (!tmFileDropBound) {
        document.addEventListener('dragover', function (event) {
          var target = event.target && event.target.closest ? event.target.closest('.tm-composer') : null;
          if (!target || !event.dataTransfer || !Array.prototype.includes.call(event.dataTransfer.types || [], 'Files')) return;
          event.preventDefault();
          target.classList.add('tm-drag-over');
          if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy';
        });
        document.addEventListener('dragleave', function (event) {
          var target = event.target && event.target.closest ? event.target.closest('.tm-composer') : null;
          if (target && (!event.relatedTarget || !target.contains(event.relatedTarget))) target.classList.remove('tm-drag-over');
        });
        document.addEventListener('drop', function (event) {
          var target = event.target && event.target.closest ? event.target.closest('.tm-composer') : null;
          if (!target || !event.dataTransfer || !event.dataTransfer.files || !event.dataTransfer.files.length) return;
          event.preventDefault();
          target.classList.remove('tm-drag-over');
          _tmHandleDroppedFiles(event.dataTransfer.files);
        });
        tmFileDropBound = true;
      }

      // 全局点击：点击菜单/弹层外部时关闭「导入」菜单与「更多操作」弹层
      document.addEventListener('click', function (e) {
        var importMenu = document.getElementById('tmImportMenu');
        var clickedAction = e.target.closest && e.target.closest('[data-act]');
        var keepsToolMenu = clickedAction && (clickedAction.dataset.act === 'tm-plus-select-exam' || clickedAction.dataset.act === 'tm-plus-select-plugin');
        if (importMenu && !importMenu.hidden && !e.target.closest('.tm-import-group')) {
          tmCloseImportMenu();
        } else if (!keepsToolMenu && e.target.closest && e.target.closest('.tm-import-menu')) {
          // 点击菜单内的条目后收起菜单
          tmCloseImportMenu();
        }
        var modelMenu = document.getElementById('tmModelMenu');
        if (modelMenu && !modelMenu.hidden && e.target.closest && !e.target.closest('.tm-model-picker')) {
          tmCloseModelMenu();
        }
        var examMenu = document.getElementById('tmExamMenu');
        var toolGroup = e.target.closest && e.target.closest('.tm-import-group');
        var examPicker = e.target.closest && e.target.closest('.tm-exam-picker-wrap');
        if (examMenu && !examMenu.hidden && !examPicker) {
          tmCloseExamMenu();
        }
        var classMenu = document.getElementById('tmClassMenu');
        var classPicker = e.target.closest && e.target.closest('.tm-class-picker-wrap');
        if (classMenu && !classMenu.hidden && !classPicker) {
          tmCloseClassMenu();
        }
        var pluginMenu = document.getElementById('tmPluginMenu');
        if (pluginMenu && !pluginMenu.hidden && !toolGroup) tmClosePluginMenu();
        if (tmConvMenuSessionId !== null && e.target.closest && !e.target.closest('.tm-conv-wrap')) {
          tmConvMenuSessionId = null;
          renderNav();
        }
      });
      // Esc 关闭「导入」菜单与「更多操作」弹层
      document.addEventListener('keydown', function (e) {
        if (e.key !== 'Escape') return;
        var modelMenu = document.getElementById('tmModelMenu');
        if (modelMenu && !modelMenu.hidden) { tmCloseModelMenu(); e.stopPropagation(); return; }
        var examMenu = document.getElementById('tmExamMenu');
        if (examMenu && !examMenu.hidden) { tmCloseExamMenu(); e.stopPropagation(); return; }
        var classMenu = document.getElementById('tmClassMenu');
        if (classMenu && !classMenu.hidden) { tmCloseClassMenu(); e.stopPropagation(); return; }
        var pluginMenu = document.getElementById('tmPluginMenu');
        if (pluginMenu && !pluginMenu.hidden) { tmClosePluginMenu(); e.stopPropagation(); return; }
        var importMenu = document.getElementById('tmImportMenu');
        if (importMenu && !importMenu.hidden) { tmCloseImportMenu(); e.stopPropagation(); return; }
        if (tmConvMenuSessionId !== null) {
          tmConvMenuSessionId = null;
          renderNav();
          e.stopPropagation();
        }
      });
    }

    /** 刷新当前学期的数据库考试，防止异步请求或切换学期造成旧列表串入。 */
    function tmRefreshAvailableExams(termId) {
      if (typeof teachMateApi.listExams !== 'function' || typeof teachMateState.setAvailableExams !== 'function') return;
      var requestedTermId = termId == null ? currentTermId : termId;
      teachMateState.setAvailableExams([], requestedTermId);
      teachMateApi.listExams(requestedTermId).then(function (exams) {
        // 请求返回时如果当前学期已改变，丢弃旧请求的结果。
        if (Number(currentTermId) !== Number(requestedTermId)) return;
        teachMateState.setAvailableExams(Array.isArray(exams) ? exams : [], requestedTermId);
      }).catch(function () {
        if (Number(currentTermId) !== Number(requestedTermId)) return;
        teachMateState.setAvailableExams([], requestedTermId);
      });
    }

    /** 收起导入菜单并同步按钮状态 */
    function tmCloseImportMenu() {
      var menu = document.getElementById('tmImportMenu');
      if (!menu) return;
      menu.hidden = true;
      tmClosePluginMenu();
      var btn = document.querySelector('[data-act="tm-composer-plus"]');
      if (btn) btn.setAttribute('aria-expanded', 'false');
    }

    /** 模型弹层：切换后续发送任务实际使用的模型档案。 */
    function tmCloseModelMenu() {
      var menu = document.getElementById('tmModelMenu');
      if (!menu || menu.hidden) return;
      menu.hidden = true;
      var btn = document.querySelector('[data-act="tm-model-toggle"]');
      if (btn) btn.setAttribute('aria-expanded', 'false');
    }

    function tmToggleModelMenu(button) {
      var menu = document.getElementById('tmModelMenu');
      if (!menu || button.disabled) return;
      var open = menu.hidden;
      menu.hidden = !open;
      button.setAttribute('aria-expanded', open ? 'true' : 'false');
    }

    function tmSelectModelMockup(option) {
      tmCloseModelMenu();
      // 只调用真实切换；成功后由 state 触发重绘，失败时保留原模型，避免出现“看起来已切换但实际未切换”。
      tmSwitchModel(option.dataset.modelId);
    }

    function tmCloseExamMenu() {
      var menu = document.getElementById('tmExamMenu');
      if (!menu || menu.hidden) return;
      menu.hidden = true;
      var btn = document.querySelector('[data-act="tm-exam-toggle"]');
      if (btn) btn.setAttribute('aria-expanded', 'false');
    }

    function tmCloseClassMenu() {
      var menu = document.getElementById('tmClassMenu');
      if (!menu || menu.hidden) return;
      menu.hidden = true;
      var btn = document.querySelector('[data-act="tm-class-toggle"]');
      if (btn) btn.setAttribute('aria-expanded', 'false');
    }

    function tmClosePluginMenu() {
      var menu = document.getElementById('tmPluginMenu');
      if (menu) menu.hidden = true;
    }

    function tmOpenToolSubmenu(id) {
      var examMenu = document.getElementById('tmExamMenu');
      var pluginMenu = document.getElementById('tmPluginMenu');
      if (examMenu) examMenu.hidden = id !== 'exam';
      if (pluginMenu) pluginMenu.hidden = id !== 'plugin';
    }

    function tmToggleExamMenu(button) {
      var menu = document.getElementById('tmExamMenu');
      if (!menu || button.disabled) return;
      var open = menu.hidden;
      tmCloseClassMenu();
      tmCloseImportMenu();
      menu.hidden = !open;
      button.setAttribute('aria-expanded', open ? 'true' : 'false');
    }

    function tmToggleClassMenu(button) {
      var menu = document.getElementById('tmClassMenu');
      if (!menu || button.disabled) return;
      var open = menu.hidden;
      tmCloseExamMenu();
      tmCloseImportMenu();
      menu.hidden = !open;
      button.setAttribute('aria-expanded', open ? 'true' : 'false');
    }

    /** 选择下一轮 TeachMate 分析使用的班级。分析运行中由视图层禁止切换。 */
    function tmSelectClass(option) {
      var className = option.dataset.className || '';
      if (typeof teachMateState.setSelectedClassName === 'function') teachMateState.setSelectedClassName(className);
      tmCloseClassMenu();
      render();
    }

    /** 选择 TeachMate 分析用考试；空值表示本次不绑定数据库考试。 */
    function tmSelectExam(option) {
      var examId = option.dataset.examId || '';
      if (typeof teachMateState.setSelectedExamId === 'function') teachMateState.setSelectedExamId(examId);
      teachMateState.setBindCurrentExam(!!examId);
      tmCloseExamMenu();
    }

    function tmSelectPlugin(option) {
      var pluginId = option.dataset.pluginId || '';
      if (typeof teachMateState.setSelectedPluginId === 'function') teachMateState.setSelectedPluginId(pluginId);
      tmClosePluginMenu();
      render();
    }

    /**
     * 已经有消息的会话不能直接改写原来的会话范围。
     * 如果教师在底部重新选择了班级或考试，下一次发送会自动创建新会话，
     * 这样旧报告仍然保留，新一轮分析则使用刚刚选择的范围。
     */
    async function _scopeChangeRequiresNewSession(snapshot, session, capability) {
      if (!snapshot || !session || !Array.isArray(snapshot.messages) || !snapshot.messages.length) {
        return { changed: false, payload: null };
      }
      var classChanged = snapshot.selectedClassName !== null && snapshot.selectedClassName !== undefined;
      var examChanged = snapshot.examSelectionTouched === true;
      if (!classChanged && !examChanged) return { changed: false, payload: null };

      var result = await _buildSessionPayload(capability || 'general_chat');
      var changed = false;
      if (classChanged && String(result.payload.class_id || '') !== String(session.class_id || '')) changed = true;
      if (examChanged && String(result.payload.exam_id || '') !== String(session.exam_id || '')) changed = true;
      return { changed: changed, payload: result.payload };
    }

    var TM_PLUGIN_ICON_POOL = ['analytics', 'diagnosis', 'plan', 'document', 'spark', 'target'];
    var TM_BUILTIN_PLUGIN_ICONS = {
      exam_analysis: 'analytics',
      student_diagnosis: 'diagnosis',
      review_plan: 'plan',
      document_export_word: 'document',
      document_export_pdf: 'document',
    };
    function tmPluginIconPath(plugin) {
      var id = String(plugin && plugin.id || 'external');
      var iconName = TM_BUILTIN_PLUGIN_ICONS[id];
      if (!iconName) {
        var stored = {};
        try { stored = JSON.parse(window.localStorage.getItem('teachmate:plugin-icons') || '{}') || {}; } catch (e) {}
        iconName = stored[id];
        if (!TM_PLUGIN_ICON_POOL.includes(iconName)) {
          var hash = 0;
          for (var i = 0; i < id.length; i += 1) hash = (hash * 31 + id.charCodeAt(i)) >>> 0;
          iconName = TM_PLUGIN_ICON_POOL[hash % TM_PLUGIN_ICON_POOL.length];
          stored[id] = iconName;
          try { window.localStorage.setItem('teachmate:plugin-icons', JSON.stringify(stored)); } catch (e) {}
        }
      }
      return '/workbench-assets/plugin-icons/' + iconName + '.svg';
    }

    /** 在聊天输入区切换已保存模型；只影响后续发送的分析任务。 */
    async function tmSwitchModel(profileId) {
      if (!profileId || teachMateState.getSnapshot().isRunning) return;
      try {
        var result = await teachMateApi.activateModelProfile(profileId);
        if (!result || !result.success) throw new Error(result && result.message || '切换失败');
        teachMateState.setCurrentModelId(profileId);
        if (result.provider_info) teachMateState.setProviderInfo(result.provider_info);
        var models = await teachMateApi.listModelProfiles();
        teachMateState.setSavedModels(models || {});
        if (typeof showToast === 'function') showToast('已切换模型，后续消息将使用新模型', 'success');
      } catch (e) {
        if (typeof showToast === 'function') showToast('模型切换失败：' + (e.message || e), 'error');
      }
    }
    if (typeof window !== 'undefined') window.tmSwitchModel = tmSwitchModel;

    /**
     * P1-12: 构建会话创建 payload，提交真实的数字 class_id / exam_id / student_id。
     * 不再发送被 Schema 静默忽略的 class_filter（班级名称字符串）。
     * @returns {Promise<{payload: object, examId: number|null}>}
     *   payload: 可直接传给 createSession 的对象
     *   examId: 解析后的数字 exam_id（可能为 null）
     */
    function _extractStudentReference(text, allowBareReference) {
      var value = String(text || '').trim();
      if (!value) return null;
      var students = (typeof state !== 'undefined' && Array.isArray(state.students)) ? state.students : [];
      // 优先使用当前已加载的学生列表做精确匹配，避免把“成绩波动较大的学生”误识别成姓名。
      var exact = students.filter(function (student) {
        if (!student) return false;
        var name = String(student.name || '').trim();
        var no = String(student.studentNo || student.student_no || '').trim();
        return (name && value.indexOf(name) >= 0) || (no && value.indexOf(no) >= 0);
      }).sort(function (a, b) {
        var al = String(a.name || a.studentNo || a.student_no || '').length;
        var bl = String(b.name || b.studentNo || b.student_no || '').length;
        return bl - al;
      });
      if (exact.length) {
        var first = exact[0];
        var firstName = String(first.name || first.studentNo || first.student_no || '').trim();
        var exactNames = [];
        exact.forEach(function (student) {
          var label = String(student.name || student.studentNo || student.student_no || '').trim();
          if (label && exactNames.indexOf(label) < 0) exactNames.push(label);
        });
        if (exactNames.length > 1) return { key: '', name: exactNames.slice(0, 3).join('、'), ambiguous: true };
        // 同名时不擅自选人，后续提示教师补充学号。
        var same = exact.filter(function (student) { return String(student.name || '').trim() === firstName; });
        if (same.length === 1) return { key: firstName, name: String(first.name || firstName), ambiguous: false };
        return { key: firstName, name: firstName, ambiguous: true };
      }
      // 学生列表尚未加载时，仅接受带有明确“学生/同学/学号”标记的短引用。
      var marked = value.match(/(?:学生|同学|学号|编号)\s*[：:#]?\s*([A-Za-z0-9一-龥·_-]{1,20})/);
      // “给学生做画像”“所有学生进行诊断”中的动作短语不是姓名。
      // 这里作为批量意图优先之外的第二道保护，避免相近表达再次误路由。
      if (marked && marked[1] && !/^(?:成绩|画像|情况|表现|诊断|分析|波动|做|给|生成|建立|更新|进行)/.test(marked[1])) {
        return { key: marked[1], name: marked[1], ambiguous: false };
      }
      var numbered = value.match(/([A-Za-z0-9_-]{1,20})号学生/);
      if (numbered && numbered[1]) return { key: numbered[1], name: numbered[1] + '号学生', ambiguous: false };
      var named = value.match(/(?:分析|诊断|画像|评估)(?:一下|下)?\s*(?:学生|同学)?\s*([一-龥]{2,4})(?:的|学生|同学|$)/);
      var namedValue = named && named[1] ? named[1].replace(/的$/, '') : '';
      if (namedValue && !/^(成绩|画像|情况|表现|诊断|分析|波动)$/.test(namedValue)) {
        return { key: namedValue, name: namedValue, ambiguous: false };
      }
      // 只在已经明确进入“请补充学生”的上下文后，接受单独的
      // 姓名或学号。普通对话仍不把任意短词误判为学生。
      if (allowBareReference) {
        var bare = value.match(/^([一-龥·]{2,8}|[A-Za-z0-9_-]{1,20})$/);
        if (bare && bare[1] && !/^(成绩|画像|情况|表现|诊断|分析|波动|学生|同学|全班|全体|班级|所有学生|所有同学|每个人)$/.test(bare[1])) {
          return { key: bare[1], name: bare[1], ambiguous: false };
        }
      }
      return null;
    }

    function _discardStagedStudentScopeRequest() {
      var snapshot = teachMateState.getSnapshot();
      var messages = Array.isArray(snapshot.messages) ? snapshot.messages : [];
      var persisted = messages.filter(function (message) { return !message._scopeDraft; });
      if (persisted.length !== messages.length) teachMateState.setMessages(persisted);
    }

    function _setStudentScopePrompt(reference, unresolved, options) {
      options = options || {};
      var prompt;
      if (reference && reference.ambiguous) {
        prompt = {
          kind: 'unresolved',
          studentName: reference.name,
          text: '当前班级里有多位“' + reference.name + '”，请补充学号，或在学生列表中选中一位后再发送。',
        };
      } else if (reference && unresolved) {
        prompt = {
          kind: 'unresolved',
          studentName: reference.name,
          text: '我按单个学生诊断处理，但当前范围内没有找到“' + reference.name + '”。请检查姓名/学号，或在学生列表中选中一位。',
        };
      } else {
        prompt = {
          kind: 'individual',
          text: '你希望诊断哪位学生？请直接告诉我学生姓名或学号，例如“分析张三”或“分析12号学生”。',
        };
      }
      var requestText = String(options.requestText || '').trim();
      if (requestText) prompt.requestText = requestText;
      if (options.appendUserMessage && requestText) {
        // 缺少学生范围时也要把“这一轮已发出”明确呈现给教师。
        // 这是一条临时的范围请求；教师补充姓名/学号后，会合并成
        // 一条完整请求发给后端，避免丢失“做学生画像”的原始意图。
        _discardStagedStudentScopeRequest();
        teachMateState.appendMessage({
          role: 'user',
          content_text: requestText,
          plugin: options.plugin ? { name: options.plugin.name, icon: options.plugin.icon_asset } : null,
          _scopeDraft: true,
          created_at: new Date().toISOString(),
        });
      }
      if (typeof teachMateState.setScopePrompt === 'function') teachMateState.setScopePrompt(prompt);
      var currentInput = document.getElementById('tmInput');
      if (options.consumeInput && currentInput) {
        currentInput.value = '';
        currentInput.style.height = 'auto';
        if (typeof teachMateState.clearDraft === 'function') teachMateState.clearDraft();
      } else if (typeof teachMateState.setDraft === 'function') {
        teachMateState.setDraft((currentInput || {}).value || '');
      }
      render();
      var input = document.getElementById('tmInput');
      if (input && typeof input.focus === 'function') input.focus();
    }

    function _isBatchStudentRequest(text) {
      var value = String(text || '');
      return /全班(?:的)?学生?|全体(?:的)?学生|所有(?:的)?学生|所有(?:的)?同学|每个同学|每一个同学|每位同学|每一位同学|每个学生|每一个学生|每个人|各个学生|各位学生|班级所有|批量(?:画像|诊断)|班级(?:学生)?画像/.test(value)
        || /(?:当前|这个|本)?班(?:级)?(?:的)?(?:所有|全体)?(?:学生|同学)?\s*(?:做|生成|建立|更新|进行)?\s*(?:一份|一批)?\s*(?:学生)?(?:画像|诊断)/.test(value)
        || (/(?:[ABCD]{1,4}|[ABCD](?:\s*[、，,和及与/]\s*[ABCD])+)\s*(?:(?:两|二)(?:个)?\s*)?(?:层级|层)/i.test(value) && /学生|同学/.test(value));
    }

    function _extractStudentTiers(text) {
      var value = String(text || '').toUpperCase();
      var match = value.match(/((?:[ABCD]{1,4})|(?:[ABCD](?:\s*[、，,和及与/]\s*[ABCD])+))\s*(?:(?:两|二)(?:个)?\s*)?(?:层级|层)/);
      if (!match) return [];
      var tiers = /^[ABCD]{1,4}$/.test(match[1]) ? match[1].split('') : match[1].split(/[、，,和及与/\s]+/);
      return tiers.filter(function (tier, index, all) {
        return /^[ABCD]$/.test(tier) && all.indexOf(tier) === index;
      });
    }

    // 支持“80-90 分”“80至90分”等自然语言范围。范围筛选只影响批量画像，
    // 不把分数区间误当成学生姓名，也不要求教师先手动勾选学生。
    function _extractScoreRange(text) {
      var value = String(text || '');
      var match = value.match(/(\d+(?:\.\d+)?)\s*(?:[-—–~～至到])\s*(\d+(?:\.\d+)?)\s*分?/);
      if (!match) return null;
      var first = Number(match[1]);
      var second = Number(match[2]);
      if (!Number.isFinite(first) || !Number.isFinite(second)) return null;
      return { min: Math.min(first, second), max: Math.max(first, second) };
    }

    function _findLocalExamForTier(snapshot) {
      var key = snapshot && snapshot.examSelectionTouched ? snapshot.selectedExamId : ((snapshot && snapshot.selectedExamId) || currentExamId);
      var exams = (typeof state !== 'undefined' && Array.isArray(state.exams)) ? state.exams : [];
      return exams.find(function (exam) {
        return String(exam.id) === String(key) || String(exam.sourceKey || exam.source_key || '') === String(key);
      }) || null;
    }

    function _findLocalStudentForBatch(student) {
      var localStudents = (typeof state !== 'undefined' && Array.isArray(state.students)) ? state.students : [];
      return localStudents.find(function (item) {
        var localId = String(item.id || '').trim();
        var remoteId = String(student.id || '').trim();
        var localName = String(item.name || '').trim();
        var remoteName = String(student.name || '').trim();
        var localNo = String(item.studentNo || item.student_no || '').trim();
        var remoteNo = String(student.student_no || student.studentNo || '').trim();
        return (localId && remoteId && localId === remoteId)
          || (localName && remoteName && localName === remoteName)
          || (localNo && remoteNo && localNo === remoteNo);
      }) || student;
    }

    async function _startBatchStudentAnalysis(text, plugin) {
      var examSnapshot = teachMateState.getSnapshot();
      var selectedExam = examSnapshot.examSelectionTouched ? examSnapshot.selectedExamId : (examSnapshot.selectedExamId || currentExamId);
      // 批量画像默认使用当前学期/班级最近一场考试，避免教师只选了班级
      // 时被误导成单人诊断。若教师已有明确选择，仍完全尊重该选择。
      if (!selectedExam && typeof state !== 'undefined' && Array.isArray(state.exams) && state.exams.length) {
        var latestExam = state.exams.slice().sort(function (a, b) {
          var ad = String(a.date || a.examDate || '').localeCompare(String(b.date || b.examDate || ''));
          return ad || (Number(a.id || 0) - Number(b.id || 0));
        }).pop();
        selectedExam = latestExam && (latestExam.id || latestExam.sourceKey || latestExam.source_key);
        if (selectedExam && typeof teachMateState.setSelectedExamId === 'function') {
          teachMateState.setSelectedExamId(selectedExam);
        }
      }
      if (!selectedExam) {
        if (typeof teachMateState.setScopePrompt === 'function') teachMateState.setScopePrompt({ kind: 'unresolved', text: '批量学生画像需要先选择一场考试，请在底部考试选择器中绑定后再发送。' });
        render();
        return true;
      }
      var createdSessionId = null;
      var groupRequestStarted = false;
      try {
        // 批量画像不应继承学生管理页残留的单选学生，否则会触发单学生解析，
        // 并把批量意图错误地绑定到某一位学生。
        var resolved = await _buildSessionPayload('student_diagnosis', null, { skipStudentScope: true });
        var students = await teachMateApi.listStudents(currentTermId, resolved.payload.class_id);
        var uniqueStudents = [];
        var seenStudentIds = {};
        (Array.isArray(students) ? students : []).forEach(function (student) {
          var id = Number(student && student.id);
          if (!Number.isFinite(id) || id <= 0 || seenStudentIds[id]) return;
          seenStudentIds[id] = true;
          uniqueStudents.push(student);
        });
        var requestedTiers = _extractStudentTiers(text);
        var requestedScoreRange = _extractScoreRange(text);
        var localExam = (requestedTiers.length || requestedScoreRange) ? _findLocalExamForTier(teachMateState.getSnapshot()) : null;
        if (requestedTiers.length) {
          if (!localExam || !localExam.tierLines || typeof getExamScore !== 'function' || typeof getExamTier !== 'function') {
            if (typeof teachMateState.setScopePrompt === 'function') teachMateState.setScopePrompt({
              kind: 'unresolved',
              text: '已识别你要分析 ' + requestedTiers.join('、') + ' 层学生，但当前考试缺少可用的分层数据。请先补充分层线，或直接在学生列表中选择范围。',
            });
            render();
            return true;
          }
          uniqueStudents = uniqueStudents.filter(function (student) {
            var localStudent = _findLocalStudentForBatch(student);
            var score = getExamScore(localExam, localStudent);
            return requestedTiers.indexOf(getExamTier(localExam, score)) >= 0;
          });
        }
        if (requestedScoreRange) {
          if (!localExam || typeof getExamScore !== 'function') {
            if (typeof teachMateState.setScopePrompt === 'function') teachMateState.setScopePrompt({
              kind: 'unresolved',
              text: '已识别你要分析 ' + requestedScoreRange.min + '—' + requestedScoreRange.max + ' 分的学生，但当前考试没有可用成绩。请先选择有成绩的考试后再试。',
            });
            render();
            return true;
          }
          uniqueStudents = uniqueStudents.filter(function (student) {
            var localStudent = _findLocalStudentForBatch(student);
            var score = getExamScore(localExam, localStudent);
            return Number.isFinite(Number(score))
              && Number(score) >= requestedScoreRange.min
              && Number(score) <= requestedScoreRange.max;
          });
        }
        if (!uniqueStudents.length) {
          if (typeof teachMateState.setScopePrompt === 'function') teachMateState.setScopePrompt({
            kind: 'unresolved',
            text: requestedScoreRange
              ? '当前班级没有成绩落在 ' + requestedScoreRange.min + '—' + requestedScoreRange.max + ' 分的学生。'
              : '当前班级没有可用于画像的学生记录，请先检查班级和学期范围。',
          });
          render();
          return true;
        }
        if (uniqueStudents.length > 200) {
          if (typeof teachMateState.setScopePrompt === 'function') teachMateState.setScopePrompt({
            kind: 'unresolved',
            text: '当前范围包含 ' + uniqueStudents.length + ' 名学生，单个批量任务最多支持 200 人。请缩小班级范围后再试。',
          });
          render();
          return true;
        }

        // 批量任务必须使用不绑定单个学生的会话。
        var sessionPayload = Object.assign({}, resolved.payload, { title: '全班学生诊断' });
        delete sessionPayload.student_id;
        var session = await teachMateApi.createSession(sessionPayload);
        createdSessionId = session && session.id != null ? session.id : null;
        var sessions;
        try {
          sessions = await teachMateApi.listSessions(currentTermId);
        } catch (listError) {
          sessions = (teachMateState.getSnapshot().sessions || []).concat([session]);
        }
        teachMateState.setSessions(sessions || []);
        teachMateState.setCurrentSession(session.id);

        var names = teachMateState.getSnapshot().contextNames || {};
        var studentNames = Object.assign({}, names.student || {});
        uniqueStudents.forEach(function (student) {
          if (student && student.id != null && student.name) studentNames[student.id] = student.name;
        });
        if (typeof teachMateState.setContextNames === 'function') {
          teachMateState.setContextNames({
            class: Object.assign({}, names.class || {}),
            exam: Object.assign({}, names.exam || {}),
            student: studentNames,
          });
        }

        groupRequestStarted = true;
        var group = await teachMateApi.createAnalysisGroup({
          session_id: Number(session.id),
          capability: 'student_diagnosis',
          term_id: Number(currentTermId),
          class_id: resolved.payload.class_id == null ? null : Number(resolved.payload.class_id),
          exam_id: Number(resolved.payload.exam_id),
          student_ids: uniqueStudents.map(function (student) { return Number(student.id); }),
          max_concurrency: 4,
          shard_size: 6,
          require_confirmation: true,
        });
        if (!group) throw new Error('批量任务创建失败');

        var input = document.getElementById('tmInput');
        if (input) { input.value = ''; input.style.height = 'auto'; }
        teachMateState.clearDraft();
        teachMateState.appendMessage({
          role: 'user',
          content_text: text,
          plugin: plugin ? { name: plugin.name, icon: plugin.icon_asset } : null,
          created_at: new Date().toISOString(),
        });
        var batchScopeLabel = requestedScoreRange
          ? requestedScoreRange.min + '—' + requestedScoreRange.max + ' 分'
          : (requestedTiers.length ? requestedTiers.join('、') + '层' : '全班');
        teachMateState.appendMessage({
          role: 'assistant',
          content_text: '已整理好' + batchScopeLabel + '学生诊断方案，共 ' + uniqueStudents.length + ' 名学生。请核对上方名单，并在输入区确认后执行；确认前不会开始分析。',
          created_at: new Date().toISOString(),
        });
        teachMateState._pendingQuickTask = null;
        renderNav();
        render();
        tmScrollMessagesToBottom();
      } catch (e) {
        if (e && e.code === 'SCOPE_MISSING_EXAM') {
          if (typeof teachMateState.setScopePrompt === 'function') teachMateState.setScopePrompt({ kind: 'unresolved', text: '批量学生画像需要先选择一场考试，请在底部考试选择器中绑定后再发送。' });
          render();
        } else if (e && groupRequestStarted && Number(e.status) === 404) {
          if (createdSessionId != null && teachMateApi && typeof teachMateApi.deleteSession === 'function') {
            await teachMateApi.deleteSession(createdSessionId).catch(function () {});
            var remainingSessions = (teachMateState.getSnapshot().sessions || []).filter(function (item) { return String(item.id) !== String(createdSessionId); });
            teachMateState.setSessions(remainingSessions);
            teachMateState.setCurrentSession(null);
            renderNav();
            render();
          }
          showToast('批量分析接口未加载，请重启工作台后再试。', 'error');
        } else {
          showToast('创建批量画像任务失败：' + (e.message || e), 'error');
        }
      }
      return true;
    }

    async function _buildSessionPayload(capabilityName, studentKeyOverride, options) {
      var termId = currentTermId;
      var payload = { term_id: termId };
      var effectiveCapability = capabilityName || 'general_chat';
      var scopeSnapshot = teachMateState.getSnapshot();
      var skipStudentScope = options && options.skipStudentScope === true;

      // 班级：优先使用聊天区本地选择，未选择时才沿用旧会话/工作台上下文。
      // 显式选择的班级解析失败必须阻断发送，不能静默退化成全体班级。
      var hasLocalClassSelection = scopeSnapshot.selectedClassName !== null && scopeSnapshot.selectedClassName !== undefined;
      var className = hasLocalClassSelection
        ? String(scopeSnapshot.selectedClassName || '').trim()
        : normalizeClassFilter(dashboardClass);
      if (className) {
        try {
          var classId = await teachMateApi.findClassIdByName(className, termId);
          if (classId == null) throw new Error('未找到班级');
          payload.class_id = Number(classId);
        } catch (e) {
          console.warn('TeachMate: 查找 class_id 失败', e);
          throw new Error('无法读取所选班级，请刷新班级列表后重试。');
        }
      }

      // 学生：优先使用消息里明确提到的姓名/学号；否则沿用学生列表中的单选。
      var hasStudentOverride = studentKeyOverride != null && String(studentKeyOverride).trim() !== '';
      if (!skipStudentScope && (hasStudentOverride || (typeof selectedStudentIds !== 'undefined' && selectedStudentIds.size === 1))) {
        try {
          var selectedKey = hasStudentOverride ? String(studentKeyOverride).trim() : Array.from(selectedStudentIds)[0];
          var localStudent = (state.students || []).find(function (s) {
            return String(s.id) === String(selectedKey) || String(s.name || '').trim() === String(selectedKey) || String(s.studentNo || s.student_no || '').trim() === String(selectedKey);
          });
          var lookupKey = hasStudentOverride ? selectedKey : (localStudent ? (localStudent.studentNo || localStudent.student_no || localStudent.id) : selectedKey);
          var studentId = await teachMateApi.findStudentId(lookupKey, termId, payload.class_id);
          if (studentId == null) {
            var notFound = new Error('未找到学生');
            notFound.code = 'STUDENT_NOT_FOUND';
            throw notFound;
          }
          payload.student_id = Number(studentId);
        } catch (e) {
          if (e && e.code === 'STUDENT_NOT_FOUND') throw e;
          console.warn('TeachMate: 查找 student_id 失败', e);
          throw new Error('无法读取所选学生，请刷新学生列表后重试。');
        }
      }

      // 考试：exam_analysis 仅在教师显式勾选时绑定当前考试；
      // student_diagnosis/review_plan 仍必须使用已选择考试。
      var examId = null;
      var mustBindExam = ['student_diagnosis', 'review_plan'].includes(effectiveCapability);
      var examSnapshot = scopeSnapshot;
      // 教师在 TeachMate 底部明确点选考试时，选择本身就是本轮绑定意图；
      // bindCurrentExam 只控制“沿用 WorkBench 当前考试”这一隐式绑定。
      var explicitlyBindExam = examSnapshot.bindCurrentExam === true || examSnapshot.examSelectionTouched === true;
      // 优先使用聊天区刚刚选择的考试；未选择时，只有强制绑定能力才回退到 WorkBench 当前考试。
      var examLookupKey = examSnapshot.examSelectionTouched
        ? examSnapshot.selectedExamId
        : (examSnapshot.selectedExamId || ((mustBindExam || explicitlyBindExam) ? currentExamId : ''));
      if (examLookupKey && (mustBindExam || explicitlyBindExam)) {
        try {
          examId = await teachMateApi.findExamIdByKey(examLookupKey, termId);
          if (examId) payload.exam_id = examId;
        } catch (e) {
          console.warn('TeachMate: 查找 exam_id 失败', e);
          throw new Error('无法读取所选考试，请刷新考试列表后重试。');
        }
        // 选了考试但没有解析到数据库数字 ID 时，不能静默降级成“无考试”发送。
        if (!examId) throw new Error('所选考试未找到对应的数据库记录，请刷新后重新选择。');
      }

      return { payload: payload, examId: examId };
    }

    /**
     * 检查快捷任务的必要上下文。
     * 考试整体分析允许不绑定数据库考试：教师可以直接通过文字或已确认附件分析。
     * 学生诊断和复习计划仍保留其数据库考试门禁。
     */
    function _ensureExamSelected(quickTask) {
      var capability = quickTask || 'general_chat';
      var examSnapshot = teachMateState.getSnapshot();
      var selectedExamId = examSnapshot.examSelectionTouched
        ? examSnapshot.selectedExamId
        : (examSnapshot.selectedExamId || currentExamId);
      if (['student_diagnosis', 'review_plan'].includes(capability) && !selectedExamId) {
        showToast('这个分析需要先选择考试，请先在成绩面板选择一次考试。');
        return false;
      }
      return true;
    }

    /** 新建对话 */
    async function tmNewChat() {
      // “新建对话”只创建一个本地草稿，不立即写入后端。
      // 首条消息发送时由 tmSendMessage 统一创建会话，避免历史记录出现空壳对话。
      teachMateState.setCurrentSession(null);
      tmStopAnalysisGroupPolling();
      if (typeof teachMateState.clearAnalysisGroup === 'function') teachMateState.clearAnalysisGroup();
      if (typeof teachMateState.clearScopePrompt === 'function') teachMateState.clearScopePrompt();
      teachMateState.clearDraft();
      teachMateState.clearPendingAttachments();
      teachMateState._pendingQuickTask = null;
      renderNav();
      render();
    }

    /** 选择对话 */
    async function tmSelectSession(sessionId) {
      var loadToken = ++tmSessionLoadToken;
      var normalizedSessionId = Number(sessionId);
      tmStopAnalysisGroupPolling();
      if (typeof teachMateState.clearAnalysisGroup === 'function') teachMateState.clearAnalysisGroup();
      if (typeof teachMateState.clearScopePrompt === 'function') teachMateState.clearScopePrompt();
      teachMateState.setCurrentSession(sessionId);
      renderNav();
      render();
      _fadeMsgIn();
      // 加载消息
      try {
        var msgs = await teachMateApi.listMessages(sessionId);
        if (loadToken !== tmSessionLoadToken || teachMateState.currentSessionId !== normalizedSessionId) return;
        if (typeof teachMateReport !== 'undefined') teachMateReport.hydrateMaterialEdits(msgs || []);
        teachMateState.setMessages(msgs || []);
        // 重新打开历史对话时恢复该对话最近一次运行的时间信息，
        // 这样报告中的“本次用时”不会因切换对话而丢失。
        var latestRunId = null;
        for (var i = (msgs || []).length - 1; i >= 0; i--) {
          if (msgs[i] && msgs[i].run_id) { latestRunId = msgs[i].run_id; break; }
        }
        if (latestRunId && typeof teachMateState.setCurrentRun === 'function') {
          try {
            var latestRun = await teachMateApi.getRun(latestRunId);
            if (loadToken !== tmSessionLoadToken || teachMateState.currentSessionId !== normalizedSessionId) return;
            if (latestRun) teachMateState.setCurrentRun(latestRun);
          } catch (runError) { /* 历史运行时间读取失败不影响聊天内容 */ }
        }
      } catch (e) {
        if (loadToken !== tmSessionLoadToken || teachMateState.currentSessionId !== normalizedSessionId) return;
        console.error('TeachMate: failed to load messages', e);
        teachMateState.setMessages([]);
      }
      // 批量诊断的学生明细不会作为聊天消息回显；重新打开历史对话时，
      // 需要恢复对应的任务组总览卡片，避免消息区退回欢迎页。
      if (loadToken !== tmSessionLoadToken || teachMateState.currentSessionId !== normalizedSessionId) return;
      if (typeof teachMateState.setAnalysisGroup === 'function' && typeof teachMateApi.getLatestAnalysisGroupForSession === 'function') {
        try {
          var restoredGroup = await teachMateApi.getLatestAnalysisGroupForSession(normalizedSessionId);
          if (loadToken !== tmSessionLoadToken || teachMateState.currentSessionId !== normalizedSessionId) return;
          teachMateState.setAnalysisGroup(restoredGroup || null);
          // 批量学生明细在服务端不会回显到聊天消息；为历史对话补一条可见的
          // 总体摘要，确保重新打开时仍能看到这次任务的结果概览。
          if (restoredGroup && (!Array.isArray(msgs) || !msgs.length)) {
            var groupTasks = Array.isArray(restoredGroup.tasks) ? restoredGroup.tasks : [];
            var groupCounts = { completed: 0, failed: 0, cancelled: 0, other: 0 };
            groupTasks.forEach(function (task) {
              var taskStatus = String(task && task.status || 'queued');
              if (taskStatus === 'degraded') taskStatus = 'completed';
              if (Object.prototype.hasOwnProperty.call(groupCounts, taskStatus)) groupCounts[taskStatus] += 1;
              else groupCounts.other += 1;
            });
            var groupTotal = Number(restoredGroup.requested_student_count || groupTasks.length || 0);
            var groupSummary = '批量学生诊断已完成，共 ' + groupTotal + ' 名学生：' + groupCounts.completed + ' 名已完成，' + (groupCounts.failed + groupCounts.cancelled + groupCounts.other) + ' 名未完成。';
            teachMateState.setMessages([{
              id: 'analysis-group-' + String(restoredGroup.id),
              role: 'assistant',
              content_text: groupSummary,
              // 不把合并后的逐生发现/建议展开成报告；聊天区只保留整体状态，
              // 详细结果继续留在学生画像和任务审计中。
              structured_answer: null,
              evidence_ids: [],
              capability: 'student_diagnosis',
              created_at: restoredGroup.completed_at || restoredGroup.created_at || new Date().toISOString(),
              _batchOverview: true,
            }]);
          }
          if (restoredGroup && !['completed', 'partially_completed', 'failed', 'cancelled', 'waiting_confirmation'].includes(String(restoredGroup.status || ''))) {
            tmRefreshAnalysisGroup(restoredGroup.id, true);
          }
        } catch (groupError) {
          // 没有批量任务的普通对话不应受影响；确保不会残留上一个对话的任务卡。
          if (loadToken === tmSessionLoadToken && teachMateState.currentSessionId === normalizedSessionId) {
            teachMateState.setAnalysisGroup(null);
          }
        }
      }
      // P1-F: 按会话固定 ID 解析上下文名称，不依赖全局筛选
      _resolveContextNames(sessionId);
    }

    /** B3-16: 会话切换时消息区柔和淡入 */
    function _fadeMsgIn() {
      var area = document.getElementById('tmMessages');
      if (!area) return;
      if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
      area.classList.remove('tm-msg-fade');
      void area.offsetWidth;
      area.classList.add('tm-msg-fade');
    }

    /**
     * P1-F: 异步解析会话上下文中的班级/考试/学生名称。
     * 从后端按 ID 查询真实名称并缓存到状态，渲染时使用缓存名称而非裸 ID。
     */
    async function _resolveContextNames(sessionId) {
      var session = (teachMateState.getSnapshot().sessions || []).find(function (s) { return Number(s.id) === Number(sessionId); });
      if (!session) return;

      var existing = teachMateState.getSnapshot().contextNames || {};
      var names = {
        class: Object.assign({}, existing.class || {}),
        exam: Object.assign({}, existing.exam || {}),
        student: Object.assign({}, existing.student || {}),
      };
      var changed = false;
      var termId = session.term_id || currentTermId;

      // 解析班级名称
      if (session.class_id && !names.class[session.class_id]) {
        try {
          var classes = await teachMateApi.listClasses(termId);
          var cls = classes.find(function (c) { return c.id === session.class_id; });
          if (cls) {
            names.class[session.class_id] = formatClassLabel(cls.name);
            changed = true;
          }
        } catch (e) { /* ignore */ }
      }

      // 解析考试名称
      if (session.exam_id && !names.exam[session.exam_id]) {
        try {
          var exams = await teachMateApi.listExams(termId);
          var exam = exams.find(function (e) { return e.id === session.exam_id; });
          if (exam) {
            names.exam[session.exam_id] = exam.name;
            changed = true;
          }
        } catch (e) { /* ignore */ }
      }

      // 解析学生名称
      if (session.student_id && !names.student[session.student_id]) {
        try {
          var students = await teachMateApi.listStudents(termId, session.class_id);
          var stu = students.find(function (s) { return s.id === session.student_id; });
          if (stu) {
            names.student[session.student_id] = stu.name;
            changed = true;
          }
        } catch (e) { /* ignore */ }
      }

      // 批量诊断的会话没有 session.student_id，学生 ID 只保存在任务卡中。
      // 恢复历史会话时也要把这些 ID 映射回真实姓名，否则界面会回落到“学生 #N”。
      var restoredGroup = teachMateState.getSnapshot().analysisGroup;
      var batchStudentIds = [];
      if (restoredGroup && Array.isArray(restoredGroup.tasks)) {
        restoredGroup.tasks.forEach(function (task) {
          if (!task || !Array.isArray(task.student_ids)) return;
          task.student_ids.forEach(function (id) {
            if (id != null && batchStudentIds.indexOf(String(id)) < 0) batchStudentIds.push(String(id));
          });
        });
      }
      if (batchStudentIds.length) {
        try {
          var batchClassId = session.class_id || (restoredGroup && restoredGroup.class_id) || null;
          var batchStudents = await teachMateApi.listStudents(termId, batchClassId);
          var batchIdSet = new Set(batchStudentIds);
          (batchStudents || []).forEach(function (student) {
            if (!student || student.id == null || !batchIdSet.has(String(student.id)) || names.student[student.id]) return;
            names.student[student.id] = student.name;
            changed = true;
          });
        } catch (e) { /* ignore: task card keeps a safe ID fallback */ }
      }

      if (changed) {
        if (teachMateState.currentSessionId === Number(sessionId)) teachMateState.setContextNames(names);
      }
    }

    /** 删除对话：先弹内联确认框，再执行 */
    function tmDeleteSession(sessionId) {
      sessionId = Number(sessionId);
      var session = (teachMateState.getSnapshot().sessions || []).find(function (s) { return s.id === sessionId; });
      var title = session && (session.title || session.summary) ? escapeHtml((session.title || session.summary)) : '该对话';
      openModal('删除对话',
        '<p>删除对话「<strong>' + title + '</strong>」吗？<br>删除后将不再显示在对话列表中。</p>',
        '<button class="btn btn-text" data-act="tm-delete-cancel">取消</button>' +
        '<button class="btn btn-primary" data-act="tm-delete-confirm" data-id="' + sessionId + '">确认删除</button>');
    }

    /** 执行删除（内联确认后的实际动作) */
    async function _doDeleteSession(sessionId) {
      sessionId = Number(sessionId);
      try {
        await teachMateApi.deleteSession(sessionId);
        var sessions = await teachMateApi.listSessions(currentTermId);
        teachMateState.setSessions(sessions || []);
        if (teachMateState.currentSessionId === sessionId) {
          teachMateState.setCurrentSession(null);
        }
        renderNav();
        render();
        showToast('对话已删除');
      } catch (e) {
        showToast('删除失败: ' + (e.message || e));
      }
    }

    /** 重命名对话：内联输入弹窗，替代原生 prompt */
    function tmRenameSession(sessionId) {
      var current = (teachMateState.getSnapshot().sessions || []).find(function (s) { return s.id === Number(sessionId); });
      if (!current) return;
      var currentTitle = escapeAttr(String(current.title || '新对话'));
      openModal('重命名对话',
        '<div class="tm-rename-field">' +
        '<label for="tmRenameInput" style="display:block;margin-bottom:6px;font-size:13px;font-weight:600;color:var(--tm-text-secondary);">对话名称</label>' +
        '<input id="tmRenameInput" class="tm-rename-input" value="' + currentTitle + '" maxlength="80" autocomplete="off" aria-label="对话名称" style="width:100%;min-height:42px;padding:0 12px;border:1px solid var(--tm-border);border-radius:10px;font:inherit;font-size:14px;">' +
        '</div>',
        '<button class="btn btn-text" data-act="tm-rename-cancel">取消</button>' +
        '<button class="btn btn-primary" data-act="tm-rename-confirm" data-id="' + Number(sessionId) + '">保存</button>');
      var input = document.getElementById('tmRenameInput');
      if (input) { input.focus(); input.select(); }
    }

    /** 执行重命名（内联输入后） */
    async function _doRenameSession(sessionId, title) {
      title = String(title || '').trim();
      if (!title) { showToast('名称不能为空'); return; }
      try {
        await teachMateApi.updateSession(sessionId, { title: title });
        teachMateState.setSessions(await teachMateApi.listSessions(currentTermId));
        closeModal();
        renderNav();
        render();
        showToast('已重命名');
      } catch (e) { showToast('重命名失败: ' + (e.message || e)); }
    }

    async function tmArchiveSession(sessionId) {
      try {
        await teachMateApi.updateSession(sessionId, { status: 'archived' });
        teachMateState.setSessions(await teachMateApi.listSessions(currentTermId));
        if (teachMateState.currentSessionId === Number(sessionId)) teachMateState.setCurrentSession(null);
        renderNav();
        showToast('已归档，可在已归档对话中查看', 'success');
      } catch (e) { showToast('归档失败: ' + (e.message || e)); }
    }

    function tmArchiveSelectedIds() {
      return Array.from(document.querySelectorAll('#tmArchivedConversationList input[data-act="tm-archive-select"]:checked'))
        .map(function (input) { return Number(input.dataset.id); })
        .filter(function (id) { return Number.isFinite(id); });
    }

    function tmUpdateArchiveSelection() {
      var list = document.getElementById('tmArchivedConversationList');
      if (!list) return;
      var manager = list.closest('.tm-archive-manager') || list;
      var checkboxes = Array.from(list.querySelectorAll('input[data-act="tm-archive-select"]'));
      var selected = checkboxes.filter(function (input) { return input.checked; }).length;
      var selectAll = manager.querySelector('input[data-act="tm-archive-select-all"]');
      var restoreButton = manager.querySelector('[data-act="tm-archive-batch-restore"]');
      var deleteButton = manager.querySelector('[data-act="tm-archive-batch-delete"]');
      var count = manager.querySelector('[data-role="tm-archive-selection-count"]');
      if (selectAll) {
        selectAll.checked = checkboxes.length > 0 && selected === checkboxes.length;
        selectAll.indeterminate = selected > 0 && selected < checkboxes.length;
      }
      if (restoreButton) restoreButton.disabled = selected === 0;
      if (deleteButton) deleteButton.disabled = selected === 0;
      if (count) count.textContent = selected ? ('已选择 ' + selected + ' 条') : '可批量选择';
    }

    function tmArchiveManagerHtml(sessions) {
      tmArchivedSessions = Array.isArray(sessions) ? sessions : [];
      var rows = tmArchivedSessions.map(function (s) {
        return '<div class="tm-archive-conversation-row">' +
          '<label class="tm-archive-select-wrap"><input type="checkbox" data-act="tm-archive-select" data-id="' + s.id + '" aria-label="选择' + escapeAttr(s.title || '新对话') + '"></label>' +
          '<div class="tm-archive-conversation-copy"><strong>' + escapeHtml(s.title || '新对话') + '</strong><small>' + escapeHtml(s.summary || '暂无摘要') + ' · ' + escapeHtml(_formatSessionTime(s.updated_at || s.created_at)) + '</small></div>' +
          '<div class="tm-archive-conversation-actions"><button class="btn btn-sm" data-act="tm-archive-restore" data-id="' + s.id + '">恢复并回复</button><button class="btn btn-sm btn-text danger-text" data-act="tm-archive-delete" data-id="' + s.id + '">删除</button></div>' +
          '</div>';
      }).join('');
      if (!rows) rows = '<div class="tm-archive-empty"><span class="material-symbols-rounded">archive</span><strong>暂无已归档对话</strong><small>在对话列表中归档的内容会显示在这里。</small></div>';
      return '<div class="tm-archive-manager"><div class="tm-archive-toolbar"><label class="tm-archive-select-all"><input type="checkbox" data-act="tm-archive-select-all">全选</label><span data-role="tm-archive-selection-count">可批量选择</span><div class="tm-archive-toolbar-actions"><button class="btn btn-sm" data-act="tm-archive-batch-restore" disabled>批量恢复</button><button class="btn btn-sm btn-text danger-text" data-act="tm-archive-batch-delete" disabled>批量删除</button></div></div><div id="tmArchivedConversationList" class="tm-archive-conversation-list">' + rows + '</div></div>';
    }

    function tmRenderArchivedConversations(sessions) {
      var body = tmArchiveManagerHtml(sessions);
      openModal('已归档对话', body, '<button class="btn btn-primary" data-act="about-close">关闭</button>');
      tmUpdateArchiveSelection();
    }

    async function tmLoadArchivedConversationsInline() {
      try {
        var sessions = typeof teachMateApi.listArchivedSessions === 'function'
          ? await teachMateApi.listArchivedSessions(currentTermId)
          : [];
        tmArchivedSessions = Array.isArray(sessions) ? sessions : [];
        tmArchiveLoaded = true;
        if (typeof tmSettingsTab !== 'undefined' && tmSettingsTab === 'archive' && document.querySelector('.tm-settings-shell')) {
          tmRenderAgentSettingsTab('archive');
          tmUpdateArchiveSelection();
        }
      } catch (e) { showToast('读取已归档对话失败: ' + (e.message || e)); }
    }

    async function tmOpenArchivedConversations() {
      try {
        var sessions = typeof teachMateApi.listArchivedSessions === 'function'
          ? await teachMateApi.listArchivedSessions(currentTermId)
          : [];
        tmRenderArchivedConversations(sessions);
      } catch (e) { showToast('读取已归档对话失败: ' + (e.message || e)); }
    }

    async function tmRestoreArchivedSessions(sessionIds, openSessionId) {
      var ids = (sessionIds || []).map(Number).filter(function (id) { return Number.isFinite(id); });
      if (!ids.length) return;
      try {
        await Promise.all(ids.map(function (id) { return teachMateApi.restoreSession(id); }));
        var sessions = await teachMateApi.listSessions(currentTermId);
        teachMateState.setSessions(sessions || []);
        var inlineArchive = document.querySelector('.tm-settings-shell') && typeof tmSettingsTab !== 'undefined' && tmSettingsTab === 'archive';
        if (openSessionId) {
          if (!inlineArchive) closeModal();
          teachMateState.setCurrentSession(Number(openSessionId));
          renderNav();
          render();
          await tmSelectSession(Number(openSessionId));
        } else {
          if (inlineArchive) {
            tmArchiveLoaded = false;
            await tmLoadArchivedConversationsInline();
          } else {
            await tmOpenArchivedConversations();
          }
        }
        showToast(ids.length > 1 ? ('已恢复 ' + ids.length + ' 条对话') : '对话已恢复，可继续回复', 'success');
      } catch (e) { showToast('恢复失败: ' + (e.message || e)); }
    }

    function tmConfirmArchivedDelete(sessionIds) {
      var ids = (sessionIds || []).map(Number).filter(function (id) { return Number.isFinite(id); });
      if (!ids.length) return;
      tmArchivePendingDeleteIds = ids;
      tmArchiveDeleteFromInline = !!(document.querySelector('.tm-settings-shell') && typeof tmSettingsTab !== 'undefined' && tmSettingsTab === 'archive');
      openModal('删除已归档对话', '<p>确定删除选中的 <strong>' + ids.length + '</strong> 条对话吗？删除后将不再显示在已归档对话中。</p>', '<button class="btn btn-text" data-act="tm-archive-delete-cancel">取消</button><button class="btn btn-danger" data-act="tm-archive-delete-confirm">确认删除</button>');
    }

    async function tmDeleteArchivedSessions() {
      var ids = tmArchivePendingDeleteIds.slice();
      var returnToInlineArchive = tmArchiveDeleteFromInline;
      tmArchivePendingDeleteIds = [];
      tmArchiveDeleteFromInline = false;
      if (!ids.length) return;
      try {
        await Promise.all(ids.map(function (id) { return teachMateApi.deleteSession(id); }));
        if (ids.some(function (id) { return Number(teachMateState.getSnapshot().currentSessionId) === id; })) teachMateState.setCurrentSession(null);
        closeModal();
        if (returnToInlineArchive) {
          tmOpenAgentSettings('archive');
          tmArchiveLoaded = false;
          await tmLoadArchivedConversationsInline();
        } else if (document.querySelector('.tm-settings-shell') && typeof tmSettingsTab !== 'undefined' && tmSettingsTab === 'archive') {
          tmArchiveLoaded = false;
          await tmLoadArchivedConversationsInline();
        } else {
          await tmOpenArchivedConversations();
        }
        teachMateState.setSessions(await teachMateApi.listSessions(currentTermId));
        renderNav();
        showToast(ids.length > 1 ? ('已删除 ' + ids.length + ' 条对话') : '对话已删除', 'success');
      } catch (e) { showToast('删除失败: ' + (e.message || e)); }
    }

    async function tmRestoreSession(sessionId) {
      try {
        await teachMateApi.restoreSession(sessionId);
        closeModal();
        teachMateState.setSessions(await teachMateApi.listSessions(currentTermId));
      } catch (e) { showToast('恢复失败: ' + (e.message || e)); }
    }

    /** 切换顶部搜索面板（由 header 搜索按钮触发） */
    function tmToggleSearchPanel() {
      var panel = document.getElementById('tmSearchPanel');
      // Header 搜索按钮在页面尚未渲染 sidebar 导航时，先触发一次导航渲染，避免按钮无响应。
      if (!panel) {
        if (typeof renderNav === 'function') renderNav();
        panel = document.getElementById('tmSearchPanel');
      }
      if (!panel) return;
      tmSearchPanelOpen = !tmSearchPanelOpen;
      panel.hidden = !tmSearchPanelOpen;
      if (tmSearchPanelOpen && !panel.querySelector('.tm-session-search')) {
        panel.innerHTML = _renderTeachMateSearchInput();
      }
      var headerButton = document.getElementById('tmHeaderSearchBtn');
      if (headerButton) {
        headerButton.setAttribute('aria-expanded', String(tmSearchPanelOpen));
        headerButton.setAttribute('aria-label', tmSearchPanelOpen ? '关闭搜索对话' : '搜索对话');
        headerButton.setAttribute('title', tmSearchPanelOpen ? '关闭搜索对话' : '搜索对话');
      }
      if (tmSearchPanelOpen) {
        var input = panel.querySelector('[data-act="tm-session-search"]');
        if (input) setTimeout(function () { input.focus(); }, 0);
      }
    }

    var tmSearchTimer = null;
    var tmSearchSeq = 0;  // P1-5: 防乱序序列号，每次搜索递增
    function tmSearchSessions(value) {
      // P1-5: 搜索词持久化到状态，重绘后恢复
      teachMateState.setSearchTerm(value || '');
      clearTimeout(tmSearchTimer);
      var mySeq = ++tmSearchSeq;
      tmSearchTimer = setTimeout(async function () {
        try {
          var sessions = await teachMateApi.listSessions(currentTermId, (value || '').trim());
          // P1-5: 只接受最新一次搜索的结果，丢弃过期响应
          if (mySeq !== tmSearchSeq) return;
          teachMateState.setSessions(sessions || []);
          // teachmate 模式下只重绘历史对话列表区，保留搜索输入框不被销毁，避免连续打字时焦点/字符丢失
          var hist = document.querySelector('.tm-side-history');
          if (hist && typeof renderTeachMateHistorySection === 'function') hist.outerHTML = renderTeachMateHistorySection();
        }
        catch (e) { console.error('TeachMate: search failed', e); }
      }, 250);
    }

    function tmToggleImportMenu(button) {
      var menu = document.getElementById('tmImportMenu');
      if (!menu) return;
      if (menu.hidden) {
        tmClosePluginMenu();
        menu.hidden = false;
        if (button) button.setAttribute('aria-expanded', 'true');
      } else {
        tmCloseImportMenu();
      }
    }

    /** 切换某条会话的「更多操作」弹层 */
    function tmToggleConvMenu(button) {
      var id = button ? button.dataset.id : null;
      tmConvMenuSessionId = (tmConvMenuSessionId === String(id)) ? null : String(id);
      renderNav();
    }

    /** 将会话移入/移出指定文件夹（folderId 为空表示移出） */
    function tmMoveSessionToFolder(sessionId, folderId) {
      var target = folderId ? String(folderId) : null;
      teachMateState.moveSessionToFolder(String(sessionId), target);
      tmConvMenuSessionId = null;
      renderNav();
      if (!target) { showToast('已移出文件夹', 'success'); return; }
      var folder = teachMateState.getSnapshot().sessionFolders.find(function (f) { return String(f.id) === target; });
      showToast('已移动到「' + (folder ? folder.name : '文件夹') + '」', 'success');
    }

    function tmOpenFolderModal(sessionId) {
      var moveSessionId = sessionId == null ? '' : String(sessionId);
      openModal('新建文件夹',
        '<div class="tm-rename-field">' +
        '<label for="tmFolderNameInput" style="display:block;margin-bottom:6px;font-size:13px;font-weight:600;color:var(--tm-text-secondary);">文件夹名称</label>' +
        '<input id="tmFolderNameInput" class="tm-rename-input" maxlength="80" autocomplete="off" aria-label="文件夹名称" placeholder="例如：期中考试 2025-05" style="width:100%;min-height:42px;padding:0 12px;border:1px solid var(--tm-border);border-radius:10px;font:inherit;font-size:14px;">' +
        '<small style="display:block;margin-top:7px;color:var(--tm-text-secondary);">用于整理 TeachMate 对话，不会修改考试数据。</small>' +
        '</div>',
        '<button class="btn btn-text" data-act="tm-folder-create-cancel">取消</button>' +
        '<button class="btn btn-primary" data-act="tm-folder-create-confirm" data-session-id="' + escapeAttr(moveSessionId) + '">创建</button>');
      var input = document.getElementById('tmFolderNameInput');
      if (input) input.focus();
    }

    /** 新建文件夹并直接移入当前会话 */
    function tmMoveToNewFolder(sessionId) {
      tmOpenFolderModal(sessionId);
    }

    function tmCreateFolder() {
      tmOpenFolderModal();
    }

    function tmConfirmFolderCreate(sessionId) {
      var input = document.getElementById('tmFolderNameInput');
      var name = input ? String(input.value || '').trim() : '';
      if (!name) { showToast('文件夹名称不能为空', 'error'); if (input) input.focus(); return; }
      var snapshot = teachMateState.getSnapshot();
      var exists = (snapshot.sessionFolders || []).some(function (folder) { return String(folder.name || '').trim() === name; });
      if (exists) { showToast('文件夹名称已存在', 'error'); if (input) input.focus(); return; }
      var folder = teachMateState.createSessionFolder(name);
      if (!folder) { showToast('文件夹创建失败', 'error'); return; }
      if (sessionId) teachMateState.moveSessionToFolder(String(sessionId), String(folder.id));
      tmConvMenuSessionId = null;
      closeModal();
      renderNav();
      showToast(sessionId ? '已创建文件夹并移入' : '已创建文件夹', 'success');
    }

    var tmSettingsTab = 'system';
    var tmSettingsPageStack = [];

    function tmResetSettingsPageHistory() {
      tmSettingsPageStack = [];
      var content = document.getElementById('tmSettingsContent');
      if (content) delete content.dataset.tmSettingsSubpage;
    }

    // 设置页中的插件详情是同一弹层里的二级页，也需要和教程一样逐级返回。
    function tmHandleSettingsBack() {
      var content = document.getElementById('tmSettingsContent');
      if (!content || !content.dataset.tmSettingsSubpage) return false;
      var previous = tmSettingsPageStack.pop() || { tab: 'plugins' };
      tmRenderAgentSettingsTab(previous.tab);
      return true;
    }
    if (typeof window !== 'undefined') window.tmHandleSettingsBack = tmHandleSettingsBack;

    // 插件清单来自后端 Plugin Manager；前端不再内置三项能力的元数据。
    function tmPluginDefinitions() {
      var snapshot = teachMateState.getSnapshot();
      return (Array.isArray(snapshot.plugins) ? snapshot.plugins : []).filter(function (plugin) {
        return plugin && plugin.ui_visible !== false;
      }).map(function (plugin) {
        var capability = Array.isArray(plugin.capabilities) ? plugin.capabilities[0] : null;
        return {
          id: String(plugin.id || plugin.name || ''),
          icon: plugin.icon || (String(plugin.id || '').indexOf('student') >= 0 ? 'person_search' : String(plugin.id || '').indexOf('review') >= 0 ? 'event_note' : 'analytics'),
          name: plugin.display_name || plugin.displayName || plugin.name || '未命名插件',
          desc: plugin.description || (capability && (capability.description || capability.name)) || '',
          enabled: plugin.enabled !== false,
          available: plugin.health !== 'failed' && plugin.health !== 'invalid_runtime',
          source: plugin.source || 'external',
          version: plugin.version || '',
          permissions: Array.isArray(plugin.permissions) ? plugin.permissions : [],
          capability_ids: Array.isArray(plugin.capabilities) ? plugin.capabilities.map(function (item) { return String(item && item.id || item || ''); }) : [],
          icon_asset: tmPluginIconPath({ id: String(plugin.id || plugin.name || ''), source: plugin.source }),
          raw: plugin,
        };
      });
    }
    function tmPluginQuickTask(plugin) {
      if (!plugin) return null;
      var ids = plugin.capability_ids || [];
      return ['exam_analysis', 'student_diagnosis', 'review_plan', 'exam_ingestion'].find(function (id) { return ids.includes(id) || plugin.id === id; }) || null;
    }
    function tmIsPluginEnabled(pluginId) {
      var plugin = tmPluginDefinitions().find(function (item) { return item.id === String(pluginId); });
      return !plugin || plugin.enabled;
    }
    function tmFindPlugin(pluginId) {
      var id = String(pluginId || '');
      return tmPluginDefinitions().find(function (plugin) { return plugin.id === id; }) || null;
    }
    function tmPluginCapabilityReady(plugin, capabilities) {
      // The practice workspace is a local UI, not a chat capability endpoint.
      if (plugin.id === 'targeted_practice') return true;
      return !capabilities.length || capabilities.some(function (item) {
        return String(item.id || item.name || '').indexOf(plugin.id) >= 0 || String(item.name || '').indexOf(plugin.name) >= 0;
      });
    }

    function tmOpenWorkBuddyConnect() {
      showToast('正在准备连接信息…');
      teachMateApi.connectWorkBuddy().then(function (payload) {
        var configText = JSON.stringify((payload && payload.config) || {}, null, 2);
        var body = '<div class="tm-workbuddy-connect"><div class="tm-workbuddy-connect-hero is-ready"><span class="material-symbols-rounded">check_circle</span><div><strong>连接信息已准备好</strong><small>无需配对码，也不需要运行命令。</small></div></div><div class="tm-workbuddy-quick-steps"><div><b>1</b><span><strong>复制连接信息</strong><small>点击下方按钮即可复制。</small></span></div><div><b>2</b><span><strong>粘贴到 WorkBuddy</strong><small>打开 WorkBuddy 的 MCP 设置，添加本地服务后粘贴。</small></span></div></div><details class="tm-workbuddy-advanced"><summary>查看连接详情</summary><textarea id="tmWorkBuddyConfig" class="tm-workbuddy-config" readonly>' + escapeHtml(configText) + '</textarea></details><p class="tm-workbuddy-connect-note"><span class="material-symbols-rounded">shield</span>只读连接：可以读取教学数据和报告，不能修改 TeachMate 内容。</p></div>';
        var footer = '<button class="btn btn-text" data-act="tm-workbuddy-connect-close">稍后</button><button class="btn btn-primary" data-act="tm-copy-workbuddy-config"><span class="material-symbols-rounded">content_copy</span><span>复制连接信息</span></button>';
        openModal('连接 WorkBuddy', body, footer);
        tmLoadWorkBuddyStatus();
      }).catch(function (error) {
        showToast('准备连接信息失败：' + (error.message || error), 'error');
      });
    }

    function tmCopyWorkBuddyConfig() {
      var field = document.getElementById('tmWorkBuddyConfig');
      if (!field) return;
      var text = field.value || '';
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(function () {
          var button = document.querySelector('[data-act="tm-copy-workbuddy-config"]');
          if (button) button.innerHTML = '<span class="material-symbols-rounded">check</span><span>已复制，去 WorkBuddy 粘贴</span>';
          showToast('连接信息已复制', 'success');
        }).catch(function () { _fallbackCopy(text); });
      } else {
        _fallbackCopy(text);
      }
    }

    function tmLoadWorkBuddyStatus() {
      var status = document.getElementById('tm-workbuddy-connection-status');
      var disconnect = document.querySelector('[data-act="tm-disconnect-workbuddy"]');
      if (!status) return;
      teachMateApi.listWorkBuddyConnections().then(function (items) {
        var active = (Array.isArray(items) ? items : []).filter(function (item) { return item && !item.revoked; });
        status.textContent = active.length ? '已连接' : '未连接';
        status.classList.toggle('is-connected', active.length > 0);
        if (disconnect) disconnect.hidden = !active.length;
      }).catch(function () {
        status.textContent = '状态暂不可用';
      });
    }

    function tmDisconnectWorkBuddy() {
      teachMateApi.listWorkBuddyConnections().then(function (items) {
        var active = (Array.isArray(items) ? items : []).filter(function (item) { return item && !item.revoked && item.id; });
        return Promise.all(active.map(function (item) { return teachMateApi.revokeWorkBuddyConnection(item.id); }));
      }).then(function () {
        showToast('已断开 WorkBuddy 只读连接', 'success');
        tmLoadWorkBuddyStatus();
      }).catch(function (error) {
        showToast('断开连接失败：' + (error.message || error), 'error');
      });
    }

    // --- v3 前端：插件详情页与内置教程（内容见 teachmate-tutorials.js）---

    function tmGuideStyles() {
      if (document.getElementById('tmGuideStyle')) return '';
      return [
        '.tm-guide-back{display:inline-flex;align-items:center;gap:4px;margin-bottom:10px;}',
        '.tm-guide-head{display:flex;align-items:center;gap:12px;margin:4px 0 10px;}',
        '.tm-guide-head img{width:40px;height:40px;border-radius:10px;}',
        '.tm-guide-tags{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0 2px;}',
        '.tm-guide-tag{background:var(--tm-surface-subtle,#f1f5f9);color:#475569;border-radius:999px;padding:4px 12px;font-size:12px;}',
        '.tm-guide-section{margin:18px 0 8px;font-weight:600;display:flex;align-items:center;gap:6px;color:#0f172a;}',
        '.tm-guide-intro{color:#334155;line-height:1.8;font-size:13.5px;margin:8px 0 0;}',
        '.tm-guide-steps{margin:0;padding-left:18px;color:#334155;line-height:1.9;font-size:13.5px;}',
        '.tm-guide-prompt-list{display:flex;flex-direction:column;gap:10px;margin-top:10px;}',
        '.tm-guide-prompt{display:flex;align-items:center;justify-content:space-between;gap:12px;text-align:left;width:100%;background:#f8fafc;border:1px solid var(--tm-border,#e2e8f0);border-radius:12px;padding:12px 16px;cursor:pointer;font-size:13.5px;color:#334155;}',
        '.tm-guide-prompt:hover{border-color:#94a3b8;background:#f1f5f9;}',
        '.tm-guide-prompt .material-symbols-rounded{color:#94a3b8;font-size:18px;}',
        '.tm-tutorial-section{margin:16px 0 6px;font-weight:600;color:#0f172a;}',
        '.tm-tutorial-body{color:#334155;line-height:1.8;font-size:13.5px;margin:6px 0;}',
      ].join('\n');
    }

    function tmRenderPluginDetails(pluginId) {
      var plugin = tmFindPlugin(pluginId);
      var guide = window.tmPluginGuideForSubject
        ? window.tmPluginGuideForSubject(String(pluginId), subjectConfig())
        : (window.TM_PLUGIN_GUIDES || {})[String(pluginId)];
      var content = document.getElementById('tmSettingsContent');
      if (!content) return;
      if (!plugin || !guide) { tmRenderAgentSettingsTab('plugins'); return; }
      if (!content.dataset.tmSettingsSubpage) tmSettingsPageStack.push({ tab: tmSettingsTab });
      content.dataset.tmSettingsSubpage = 'plugin-details';
      var snapshot = teachMateState.getSnapshot();
      var capabilities = Array.isArray(snapshot.capabilities) ? snapshot.capabilities : [];
      var enabled = plugin.enabled;
      var available = plugin.available && tmPluginCapabilityReady(plugin, capabilities);
      var statusText = !enabled ? '已停用' : (available ? '已启用' : '待配置');
      var html = [
        '<button class="tm-settings-quiet tm-guide-back" data-act="tm-settings-details-back"><span class="material-symbols-rounded">arrow_back</span>返回功能扩展</button>',
        '<div class="tm-guide-head"><img src="' + escapeAttr(plugin.icon_asset) + '" alt=""><div><h2 style="margin:0;">' + escapeHtml(guide.name || plugin.name) + '</h2><small class="tm-settings-plugin-state ' + (enabled ? '' : 'is-disabled') + '">' + statusText + '</small></div></div>',
        '<p class="tm-guide-intro">' + escapeHtml(guide.intro) + '</p>',
        '<div class="tm-guide-tags">' + (guide.tags || []).map(function (tag) { return '<span class="tm-guide-tag">' + escapeHtml(tag) + '</span>'; }).join('') + '</div>',
        '<div class="tm-guide-section"><span class="material-symbols-rounded">route</span>怎么用</div>',
        '<ol class="tm-guide-steps">' + (guide.steps || []).map(function (step) { return '<li>' + escapeHtml(step) + '</li>'; }).join('') + '</ol>',
        '<div class="tm-guide-section"><span class="material-symbols-rounded">tips_and_updates</span>试一试（点击填入输入框）</div>',
        '<div class="tm-guide-prompt-list">' + (guide.prompts || []).map(function (prompt) {
          return '<button type="button" class="tm-guide-prompt" data-act="tm-guide-apply" data-plugin-id="' + escapeAttr(plugin.id) + '" data-prompt="' + escapeAttr(prompt) + '"><span>“' + escapeHtml(prompt) + '”</span><span class="material-symbols-rounded">north_west</span></button>';
        }).join('') + '</div>',
        '<div class="tm-guide-section"><span class="material-symbols-rounded">tune</span>管理</div>',
        '<button class="tm-settings-quiet" data-act="tm-settings-plugin-toggle" data-plugin-id="' + escapeAttr(plugin.id) + '">' + (enabled ? '停用插件' : '启用插件') + '</button>',
      ].join('');
      var style = document.getElementById('tmGuideStyle');
      content.innerHTML = (style ? '' : '<style id="tmGuideStyle">' + tmGuideStyles() + '</style>') + html;
    }

    function tmOpenTutorial(key) {
      var tutorial = (window.TM_TUTORIALS || {})[String(key)];
      if (!tutorial) return;
      var style = document.getElementById('tmGuideStyle');
      var sections = (tutorial.sections || []).map(function (section) {
        var html = '<div class="tm-tutorial-section">' + escapeHtml(section.heading) + '</div>';
        if (section.body) html += '<p class="tm-tutorial-body">' + escapeHtml(section.body) + '</p>';
        if (section.steps) html += '<ol class="tm-guide-steps">' + section.steps.map(function (step) { return '<li>' + escapeHtml(step) + '</li>'; }).join('') + '</ol>';
        return html;
      }).join('');
      var body = (style ? '' : '<style id="tmGuideStyle">' + tmGuideStyles() + '</style>')
        + '<p class="tm-guide-intro">' + escapeHtml(tutorial.intro || '') + '</p>' + sections;
      openModal(tutorial.title, body, '');
    }

    if (typeof window !== 'undefined') window.tmOpenTutorial = tmOpenTutorial;

    function tmOpenPluginManager(pluginId) {
      var plugin = tmFindPlugin(pluginId);
      if (!plugin) return;
      var enabled = plugin.enabled;
      var body = '<div class="tm-plugin-detail"><div class="tm-plugin-detail-head"><img class="tm-plugin-svg-icon tm-plugin-detail-icon" src="' + escapeAttr(plugin.icon_asset) + '" alt=""><div><strong>' + escapeHtml(plugin.name) + '</strong><small>' + escapeHtml(plugin.desc) + '</small></div></div><div class="tm-plugin-detail-status"><span>当前状态</span><b class="' + (enabled ? 'is-enabled' : 'is-disabled') + '">' + (enabled ? '已启用' : '已停用') + '</b></div><p class="tm-plugin-detail-note">该能力只会在你主动发送对应分析请求时调用。停用后，首页快捷入口和对应的分析路由都会暂时不可用。</p></div>';
      var footer = '<button class="btn btn-text" data-act="tm-settings-plugin-cancel">取消</button><button class="btn ' + (enabled ? 'btn-danger' : 'btn-primary') + '" data-act="tm-settings-plugin-toggle-confirm" data-plugin-id="' + escapeAttr(plugin.id) + '">' + (enabled ? '停用插件' : '启用插件') + '</button>';
      openModal('管理插件', body, footer);
    }

    function tmOpenPluginInstallModal(initialFile) {
      tmPendingPluginArchiveFile = initialFile || null;
      var selectedName = tmPendingPluginArchiveFile && tmPendingPluginArchiveFile.name
        ? '<div class="tm-plugin-drop-file"><span class="material-symbols-rounded">folder_zip</span><span>已选择：' + escapeHtml(tmPendingPluginArchiveFile.name) + '</span></div>'
        : '';
      var body = '<div class="tm-plugin-install"><p class="tm-plugin-install-intro">导入一个本地 TeachMate/Codex 插件包。安装前会校验 manifest、权限和压缩包路径。</p>' + selectedName + '<label class="tm-plugin-install-field">插件压缩包<input id="tmPluginArchive" type="file" accept=".zip,application/zip"></label><div class="tm-plugin-install-divider"><span>或</span></div><label class="tm-plugin-install-field">本机路径<input id="tmPluginPath" type="text" placeholder="/Users/you/Downloads/my-plugin.zip" autocomplete="off"></label><small class="tm-plugin-install-hint">ZIP 可以从聊天框拖入，也可以在这里选择。未通过 manifest、权限或路径校验的插件不会被启用。</small></div>';
      var footer = '<button class="btn btn-text" data-act="tm-settings-plugin-cancel">取消</button><button class="btn btn-primary" data-act="tm-plugin-install-submit">安装插件</button>';
      openModal('安装外部插件', body, footer);
    }

    function tmSettingsModelRows() {
      var snapshot = teachMateState.getSnapshot();
      var models = Array.isArray(snapshot.savedModels) ? snapshot.savedModels : [];
      var currentId = String(snapshot.currentModelId || (snapshot.providerInfo && snapshot.providerInfo.profile_id) || '');
      if (!models.length) {
        return '<div class="tm-settings-empty"><span class="material-symbols-rounded">view_in_ar</span><strong>还没有添加版本</strong><span>添加后，TeachMate 才能开始分析。</span><button class="tm-settings-primary" data-act="model-add"><span class="material-symbols-rounded">add</span>添加版本</button></div>';
      }
      return '<div class="tm-settings-model-list">' + models.map(function (model) {
        var id = String(model.id || '');
        var current = id === currentId;
        var label = model.display_name || model.model_name || '未命名模型';
        var detail = [model.builtin ? '内置模型' : (model.provider || '兼容接口'), model.model_name || '', model.api_key_configured ? 'API Key 已配置' : 'API Key 未配置'].filter(Boolean).join(' · ');
        var primaryAction = current ? '<span class="tm-settings-current-label">当前使用</span>' : (model.builtin && !model.api_key_configured ? '<button class="tm-settings-quiet" data-act="model-edit" data-id="' + escapeAttr(id) + '">填写 API Key</button>' : '<button class="tm-settings-quiet" data-act="model-activate" data-id="' + escapeAttr(id) + '">使用</button>');
        var deleteAction = model.builtin ? '' : '<button class="tm-settings-quiet danger" data-act="model-delete" data-id="' + escapeAttr(id) + '">删除</button>';
        return '<div class="tm-settings-model-row' + (current ? ' is-current' : '') + '"><span class="tm-settings-model-icon material-symbols-rounded">view_in_ar</span><div class="tm-settings-model-copy"><strong>' + escapeHtml(label) + (current ? '<em>使用中</em>' : '') + '</strong><small>' + escapeHtml(detail) + '</small></div><div class="tm-settings-model-actions">' + primaryAction + '<button class="tm-settings-quiet" data-act="model-edit" data-id="' + escapeAttr(id) + '">编辑</button>' + deleteAction + '</div></div>';
      }).join('') + '</div>';
    }

    function tmTokenUsageHtml() {
      return '<div class="tm-settings-page-head tm-token-page-head"><div><h2>使用情况</h2><p>查看近期用量情况。</p></div><label class="tm-token-range-label">统计范围<select id="tm-token-range"><option value="today">今天</option><option value="7d">近 7 天</option><option value="30d">近 30 天</option><option value="all">全部</option></select></label></div><div class="tm-token-hero tm-settings-card"><div class="tm-token-hero-head"><div><span class="tm-token-kicker">使用量</span><strong id="tm-token-total">—</strong><small id="tm-token-period">正在读取使用情况…</small></div><span class="material-symbols-rounded tm-token-hero-icon">monitoring</span></div><div id="tm-token-chart" class="tm-token-chart" role="img" aria-label="使用量变化图"></div><div class="tm-token-legend"><span><i class="tm-token-dot tm-token-dot-input"></i>新输入</span><span><i class="tm-token-dot tm-token-dot-cache"></i>重复内容</span><span><i class="tm-token-dot tm-token-dot-output"></i>输出内容</span></div></div><div class="tm-token-stats"><div class="tm-settings-card tm-token-stat"><span class="material-symbols-rounded">input</span><div><small>新输入</small><strong id="tm-token-input">—</strong></div></div><div class="tm-settings-card tm-token-stat"><span class="material-symbols-rounded">cached</span><div><small>重复内容</small><strong id="tm-token-cache">—</strong><em id="tm-token-cache-rate">重复率 —</em></div></div><div class="tm-settings-card tm-token-stat"><span class="material-symbols-rounded">output</span><div><small>输出内容</small><strong id="tm-token-output">—</strong></div></div></div><div class="tm-token-detail tm-settings-card"><div class="tm-token-detail-head"><div><strong>使用较多的记录</strong><small>按每次使用量排序，不显示输入内容。</small></div><span id="tm-token-calls">—</span></div><div id="tm-token-top-runs"><div class="tm-token-empty">正在读取使用记录…</div></div></div>';
    }

    function tmFormatTokenNumber(value) {
      var n = Number(value || 0);
      if (n >= 1000000) return (n / 1000000).toFixed(n >= 10000000 ? 0 : 1) + 'M';
      if (n >= 1000) return (n / 1000).toFixed(n >= 10000 ? 0 : 1) + 'K';
      return Math.round(n).toLocaleString('zh-CN');
    }

    function tmRenderTokenUsage(payload) {
      payload = payload || {};
      var totals = payload.totals || {};
      var total = Number(totals.total_tokens || 0);
      var setText = function (id, value) { var el = document.getElementById(id); if (el) el.textContent = value; };
      setText('tm-token-total', total.toLocaleString('zh-CN'));
      setText('tm-token-input', tmFormatTokenNumber(totals.input_tokens));
      setText('tm-token-cache', tmFormatTokenNumber(totals.cache_read_tokens));
      setText('tm-token-output', tmFormatTokenNumber(totals.output_tokens));
      setText('tm-token-cache-rate', '命中率 ' + Number(totals.cache_hit_rate || 0).toFixed(1) + '%');
      setText('tm-token-calls', Number(totals.calls || 0).toLocaleString('zh-CN') + ' 次请求');
      setText('tm-token-period', payload.range === 'today' ? '今天 · 输入（新 + 缓存）与输出' : (payload.range === 'all' ? '全部记录 · 输入（新 + 缓存）与输出' : '统计周期 · 输入（新 + 缓存）与输出'));

      var chartEl = document.getElementById('tm-token-chart');
      if (chartEl) {
        var drawTokenChart = function () {
          if (!chartEl.isConnected || typeof echarts === 'undefined') return;
          if (chartEl.__tmChart) chartEl.__tmChart.dispose();
          var chart = echarts.init(chartEl);
          chartEl.__tmChart = chart;
          var buckets = Array.isArray(payload.buckets) ? payload.buckets : [];
          chart.setOption({
            animation: false,
            grid: { left: 42, right: 12, top: 18, bottom: 26 },
            tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, valueFormatter: function (v) { return Number(v || 0).toLocaleString('zh-CN') + ' tokens'; } },
            xAxis: { type: 'category', data: buckets.map(function (b) { return b.label || ''; }), axisLine: { lineStyle: { color: '#d8dce5' } }, axisLabel: { color: '#69728a', interval: buckets.length > 12 ? Math.ceil(buckets.length / 8) - 1 : 0 } },
            yAxis: { type: 'value', axisLabel: { color: '#69728a', formatter: function (v) { return tmFormatTokenNumber(v); } }, splitLine: { lineStyle: { color: '#e5e8ef' } } },
            series: [
              { name: '新输入', type: 'bar', stack: 'tokens', barMaxWidth: 28, itemStyle: { color: '#8fd4fa', borderRadius: [0, 0, 0, 0] }, data: buckets.map(function (b) { return b.input_tokens || 0; }) },
              { name: '缓存命中', type: 'bar', stack: 'tokens', barMaxWidth: 28, itemStyle: { color: '#5a9cf6' }, data: buckets.map(function (b) { return b.cache_read_tokens || 0; }) },
              { name: '输出', type: 'bar', stack: 'tokens', barMaxWidth: 28, itemStyle: { color: '#246bdb', borderRadius: [4, 4, 0, 0] }, data: buckets.map(function (b) { return b.output_tokens || 0; }) }
            ]
          });
          if (typeof window !== 'undefined') window.addEventListener('resize', function () { if (chartEl.__tmChart) chartEl.__tmChart.resize(); }, { once: true });
        };
        if (typeof echarts !== 'undefined') drawTokenChart();
        else ensureEcharts().then(drawTokenChart).catch(function () {
          if (chartEl.isConnected) chartEl.innerHTML = '<div class="tm-token-empty">图表组件加载失败，请重试。</div>';
        });
      }

      var list = document.getElementById('tm-token-top-runs');
      var runs = Array.isArray(payload.top_runs) ? payload.top_runs : [];
      if (list) list.innerHTML = runs.length ? runs.map(function (run) {
        var risk = run.risk === 'critical' ? ' is-critical' : (run.risk === 'warning' ? ' is-warning' : '');
        return '<div class="tm-token-run-row' + risk + '"><div class="tm-token-run-icon"><span class="material-symbols-rounded">' + (run.risk === 'normal' ? 'chat_bubble' : 'warning') + '</span></div><div class="tm-token-run-copy"><strong>' + escapeHtml(run.title || run.capability || '未命名记录') + '</strong><small>' + escapeHtml(run.capability || '使用记录') + ' · ' + Number(run.calls || 0) + ' 次使用</small></div><div class="tm-token-run-total"><strong>' + tmFormatTokenNumber(run.total_tokens) + '</strong><small>¥' + Number(run.cost_yuan || 0).toFixed(2) + '</small></div></div>';
      }).join('') : '<div class="tm-token-empty"><span class="material-symbols-rounded">monitoring</span>当前统计范围内还没有用量记录</div>';
    }

    async function tmLoadTokenUsage() {
      var range = document.getElementById('tm-token-range');
      var rangeKey = range ? range.value : 'today';
      try {
        var payload = await teachMateApi.getTokenUsage(rangeKey);
        tmRenderTokenUsage(payload);
      } catch (error) {
        var total = document.getElementById('tm-token-total');
        if (total) total.textContent = '暂不可用';
        var list = document.getElementById('tm-token-top-runs');
        if (list) list.innerHTML = '<div class="tm-token-empty is-error"><span class="material-symbols-rounded">error</span>用量数据暂时无法读取，请稍后重试</div>';
      }
    }

    function tmSettingsTabHtml(tab) {
      var snapshot = teachMateState.getSnapshot();
      var teacher = (typeof state !== 'undefined' && state.teacher) || {};
      var savedModels = Array.isArray(snapshot.savedModels) ? snapshot.savedModels : [];
      var currentModelId = String(snapshot.currentModelId || '');
      var currentModel = currentModelId
        ? (savedModels.find(function (model) { return String(model.id) === currentModelId; }) || null)
        : null;
      var modelName = currentModel ? (currentModel.display_name || currentModel.model_name || '未命名模型') : '未配置模型';
      var modelStatus = currentModel
        ? '<span class="tm-settings-status-dot">已连接</span>'
        : '<span class="tm-settings-status-dot is-unconfigured">未配置</span>';
      if (tab === 'models') {
        return '<div class="tm-settings-page-head"><div><h2>模型</h2><p>选择回答时使用的版本，并管理连接设置。</p></div><div class="tm-settings-page-head-actions"><button class="tm-settings-quiet" data-act="tm-open-tutorial" data-tutorial="model"><span class="material-symbols-rounded">menu_book</span>使用教程</button><button class="tm-settings-primary" data-act="model-add"><span class="material-symbols-rounded">add</span>添加模型</button></div></div><div class="tm-settings-card tm-settings-model-summary"><span class="material-symbols-rounded">auto_awesome</span><div><strong>当前模型</strong><small>' + escapeHtml(modelName) + '</small></div>' + modelStatus + '</div>' + tmSettingsModelRows();
      }
      if (tab === 'personalization') {
        var personalization = snapshot.personalization || {};
        var tone = ['rigorous', 'friendly', 'custom'].includes(String(personalization.tone || '')) ? String(personalization.tone) : 'rigorous';
        var customPrompt = String(personalization.custom_prompt || '');
        return '<div class="tm-settings-page-head"><div><h2>回复偏好</h2><p>让回答更符合你的教学方式和课堂习惯。</p></div></div><div class="tm-settings-card tm-settings-form-card"><div class="tm-settings-form-title"><span class="material-symbols-rounded">person</span><div><strong>教师资料</strong><small>这些信息会用于调整回答方式，不会改变事实、隐私和安全规则。</small></div></div><div class="tm-settings-form-grid"><label>教师姓名<input id="tm-settings-teacher-name" value="' + escapeAttr(teacher.name || '') + '" placeholder="例如：王老师"></label><label>学科<input id="tm-settings-teacher-subject" value="' + escapeAttr(teacher.subject || '') + '" placeholder="例如：' + escapeAttr(subjectName()) + '"></label></div><div class="tm-settings-form-grid tm-settings-preference-grid"><label>我怎么称呼你<input id="tm-settings-user-address" value="' + escapeAttr(personalization.user_address || '老师') + '" placeholder="例如：王老师、老师"></label><label>回答风格<select id="tm-settings-tone"><option value="rigorous"' + (tone === 'rigorous' ? ' selected' : '') + '>严谨务实</option><option value="friendly"' + (tone === 'friendly' ? ' selected' : '') + '>亲和友好</option><option value="custom"' + (tone === 'custom' ? ' selected' : '') + '>自定义语气</option></select></label></div><label class="tm-settings-field-full tm-settings-custom-tone-field" id="tm-settings-custom-tone-wrap"' + (tone === 'custom' ? '' : ' hidden') + '>自定义语气<textarea id="tm-settings-custom-prompt" maxlength="1200" placeholder="例如：回答像一位耐心的教研组长，先用一句话总结，再给出三条课堂行动建议。">' + escapeHtml(customPrompt) + '</textarea><small>只影响表达方式，不会覆盖事实、隐私和安全规则。</small></label><div class="tm-settings-form-foot"><span>保存后，下一次发送分析请求时生效。</span><button class="tm-settings-primary" data-act="tm-settings-personalization-save">保存个性化</button></div></div>';
      }
      if (tab === 'plugins') {
        var capabilities = Array.isArray(snapshot.capabilities) ? snapshot.capabilities : [];
        var pluginList = tmPluginDefinitions();
        var teachingPluginIds = ['exam_analysis', 'student_diagnosis', 'review_plan', 'targeted_practice'];
        var listHtml = pluginList.length ? '<div class="tm-settings-plugin-list">' + pluginList.map(function (plugin) { var enabled = plugin.enabled; var available = plugin.available && tmPluginCapabilityReady(plugin, capabilities); var statusClass = !enabled ? 'is-disabled' : (available ? 'is-ready' : ''); var statusText = !enabled ? '已停用' : (available ? '已启用' : '待配置'); var isTeachingPlugin = teachingPluginIds.indexOf(String(plugin.id)) >= 0; var actionAttrs = isTeachingPlugin ? 'data-act="tm-settings-plugin-details"' : 'data-act="tm-settings-plugin-toggle"'; var actionLabel = isTeachingPlugin ? '详情' : '管理'; return '<div class="tm-settings-plugin-row' + (!enabled ? ' is-disabled' : '') + '"><img class="tm-plugin-svg-icon tm-settings-plugin-svg-icon" src="' + escapeAttr(plugin.icon_asset) + '" alt=""><div class="tm-settings-plugin-copy"><strong>' + escapeHtml(plugin.name) + '</strong><small>' + escapeHtml(plugin.desc) + '</small></div><span class="tm-settings-plugin-state ' + statusClass + '">' + statusText + '</span><button class="tm-settings-quiet" ' + actionAttrs + ' data-plugin-id="' + escapeAttr(plugin.id) + '">' + actionLabel + '</button>' + (plugin.id === 'targeted_practice' && enabled ? '<button class="tm-settings-quiet" data-act="tm-practice-plugin-open">打开推题</button>' : '') + '</div>'; }).join('') + '</div>' : '<div class="tm-settings-empty"><span class="material-symbols-rounded">extension_off</span><strong>暂时没有可用插件</strong><span>插件服务未连接或尚未安装插件包。</span></div>';
        var countLabel = pluginList.length && pluginList.every(function (plugin) { return plugin.source === 'bundled'; }) ? ' 个内置插件' : ' 个插件';
        var workBuddyCard = '<div class="tm-settings-card tm-workbuddy-card"><div class="tm-workbuddy-card-icon"><span class="material-symbols-rounded">hub</span></div><div class="tm-workbuddy-card-copy"><div><strong>WorkBuddy 联动</strong><span id="tm-workbuddy-connection-status" class="tm-workbuddy-status">检查连接…</span></div><p>让 WorkBuddy 读取 TeachMate 已完成的分析，可以继续生成 PDF、PPT 或其他材料。</p></div><div class="tm-workbuddy-card-actions"><button class="tm-settings-quiet" data-act="tm-disconnect-workbuddy" hidden>断开连接</button><button class="tm-settings-primary" data-act="tm-connect-workbuddy"><span class="material-symbols-rounded">link</span>快速连接</button></div></div>';
        return '<div class="tm-settings-page-head"><div><h2>功能扩展</h2><p>连接外部工具，并管理 TeachMate 当前可用的教学能力。</p></div><div class="tm-settings-page-head-actions"><span class="tm-settings-count-badge">' + pluginList.length + countLabel + '</span><button class="tm-settings-quiet tm-settings-install-button" data-act="tm-plugin-install"><span class="material-symbols-rounded">add</span>安装插件</button></div></div>' + workBuddyCard + '<div class="tm-settings-section-title"><strong>TeachMate 工具</strong><span>按需启用</span></div>' + listHtml + '<div class="tm-settings-info-banner"><span class="material-symbols-rounded">shield</span><span>外部工具只有在你主动调用时才会读取已确认的数据。</span></div>';
      }
      if (tab === 'token-usage') {
        return tmTokenUsageHtml();
      }
      if (tab === 'archive') {
        var archiveContent = tmArchiveLoaded
          ? tmArchiveManagerHtml(tmArchivedSessions)
          : '<div class="tm-settings-archive-loading"><span class="material-symbols-rounded">sync</span>正在读取归档对话…</div>';
        return '<div class="tm-settings-page-head"><div><h2>已归档对话</h2><p>查看暂时不在主列表显示的对话，可以恢复后继续回复，也可以删除。</p></div></div><div class="tm-settings-archive-inline">' + archiveContent + '</div>';
      }
      return tmSettingsTabHtml('models');
    }

    function tmRenderAgentSettingsTab(tab) {
      tmSettingsTab = ['models', 'personalization', 'token-usage', 'plugins', 'archive'].includes(tab) ? tab : 'models';
      var shell = document.querySelector('.tm-settings-shell');
      var content = document.getElementById('tmSettingsContent');
      if (!shell || !content) return;
      tmSettingsPageStack = [];
      delete content.dataset.tmSettingsSubpage;
      shell.querySelectorAll('[data-act="tm-settings-tab"]').forEach(function (item) { item.classList.toggle('is-active', item.dataset.settingsTab === tmSettingsTab); });
      content.innerHTML = tmSettingsTabHtml(tmSettingsTab);
      if (tmSettingsTab === 'token-usage') {
        var tokenRange = document.getElementById('tm-token-range');
        if (tokenRange) tokenRange.addEventListener('change', tmLoadTokenUsage);
        tmLoadTokenUsage();
      }
      if (tmSettingsTab === 'plugins') tmLoadWorkBuddyStatus();
      if (tmSettingsTab === 'archive' && !tmArchiveLoaded) tmLoadArchivedConversationsInline();
    }

    function tmOpenAgentSettings(tab) {
      tmResetSettingsPageHistory();
      var teacher = (typeof state !== 'undefined' && state.teacher) || {};
      var nav = [{ id: 'models', icon: 'view_in_ar', label: '模型' }, { id: 'personalization', icon: 'auto_awesome', label: '回复偏好' }, { id: 'token-usage', icon: 'monitoring', label: '使用情况' }, { id: 'plugins', icon: 'extension', label: '功能扩展' }, { id: 'archive', icon: 'inventory_2', label: '已归档对话' }];
      var activeTab = ['models', 'personalization', 'token-usage', 'plugins', 'archive'].includes(tab) ? tab : 'models';
      var body = '<div class="tm-settings-shell"><aside class="tm-settings-sidebar"><div class="tm-settings-account"><span class="tm-side-avatar">' + escapeHtml(String(teacher.name || '教').charAt(0).toUpperCase()) + '</span><div><strong>' + escapeHtml(teacher.name || '教师') + '</strong></div></div><div class="tm-settings-nav-label">设置</div><nav class="tm-settings-nav" aria-label="设置分类">' + nav.map(function (item) { return '<button type="button" data-act="tm-settings-tab" data-settings-tab="' + item.id + '" class="' + (activeTab === item.id ? 'is-active' : '') + '"><span class="material-symbols-rounded">' + item.icon + '</span><span>' + item.label + '</span></button>'; }).join('') + '</nav><div class="tm-settings-sidebar-foot"><span class="material-symbols-rounded">lock</span><span>设置仅保存在本机</span></div></aside><section class="tm-settings-main" id="tmSettingsContent"></section></div>';
      openModal('设置', body, '');
      var content = document.querySelector('#modal .modal-content');
      if (content) content.classList.add('tm-settings-modal-content');
      tmRenderAgentSettingsTab(activeTab);
    }
    /** 发送消息 */
    async function tmSendMessage() {
      var input = document.getElementById('tmInput');
      if (!input) return;
      var text = input.value.trim();
      if (!text) return;

      var initialSnapshot = teachMateState.getSnapshot();
      var modelUnavailable = typeof _teachMateUnavailableReason === 'function'
        ? _teachMateUnavailableReason(initialSnapshot.providerInfo) : '';
      if (modelUnavailable) { showToast(modelUnavailable); return; }
      var pendingScopePrompt = initialSnapshot.scopePrompt;
      if (typeof teachMateState.clearScopePrompt === 'function' && initialSnapshot.scopePrompt) teachMateState.clearScopePrompt();
      var selectedPlugin = tmFindPlugin(initialSnapshot.selectedPluginId);
      var selectedPluginTask = tmPluginQuickTask(selectedPlugin);
      var requestedCapability = teachMateState._pendingQuickTask || selectedPluginTask || null;
      // 先判断整班/多人范围，再尝试提取单个学生。像“给当前班级所有学生做学生画像”
      // 这样的句子里，“学生做学生画像”不能被误截成一个学生姓名。
      var explicitBatchScope = _isBatchStudentRequest(text) || !!_extractScoreRange(text);
      var studentReference = requestedCapability === 'student_diagnosis' && !explicitBatchScope
        ? _extractStudentReference(text, !!(pendingScopePrompt && pendingScopePrompt.requestText))
        : null;
      if (
        requestedCapability === 'student_diagnosis'
        && studentReference
        && !studentReference.ambiguous
        && pendingScopePrompt
        && pendingScopePrompt.requestText
      ) {
        var scopeReply = text;
        text = String(pendingScopePrompt.requestText).trim() + '\n学生：' + scopeReply;
        _discardStagedStudentScopeRequest();
        initialSnapshot = teachMateState.getSnapshot();
      }
      var hasExplicitStudentReference = !!(studentReference && !studentReference.ambiguous);
      var hasAmbiguousStudentReference = !!(studentReference && studentReference.ambiguous);
      var routingSession = (initialSnapshot.sessions || []).find(function (session) {
        return String(session.id) === String(initialSnapshot.currentSessionId);
      });
      var hasBoundStudentSession = !!(routingSession && routingSession.student_id != null);
      // 新的学生诊断默认覆盖当前班级；语义明确指出某位学生时走单人流程。
      // 已经绑定单个学生的会话继续沿用原会话，保证追问不会被误开成批量任务。
      var batchRequested = !hasAmbiguousStudentReference && !hasExplicitStudentReference && (
        (requestedCapability === 'student_diagnosis' && !hasBoundStudentSession)
        || (explicitBatchScope && (!requestedCapability || requestedCapability === 'student_diagnosis'))
      );
      if (studentReference && initialSnapshot.analysisGroup && typeof teachMateState.clearAnalysisGroup === 'function') {
        teachMateState.clearAnalysisGroup();
      }
      if (requestedCapability && !tmIsPluginEnabled(requestedCapability)) {
        showToast('该插件已停用，请先在设置 → 功能扩展中启用。');
        return;
      }

      // 发送前先做不会产生副作用的门禁检查。这样附件未就绪、必需考试/学生
      // 未绑定时不会先插入一条“用户消息 + 思考中”假消息，再被后续校验撤回。
      var preflightCapability = requestedCapability || 'general_chat';
      var preflightAttachmentErrors = teachMateState.getPendingAttachments().filter(function (a) {
        return a.status !== 'ready' && a.status !== 'confirmed';
      });
      if (preflightAttachmentErrors.length) {
        showToast('附件尚未准备好，请等待解析完成后再发送。');
        return;
      }
      if (batchRequested) {
        await _startBatchStudentAnalysis(text, selectedPlugin || tmFindPlugin('student_diagnosis'));
        return;
      }
      var preflightSession = (initialSnapshot.sessions || []).find(function (s) {
        return String(s.id) === String(initialSnapshot.currentSessionId);
      });
      var hasPreflightExam = !!(
        (initialSnapshot.examSelectionTouched && initialSnapshot.selectedExamId)
        || (preflightSession && preflightSession.exam_id)
        || currentExamId
      );
      var hasPreflightStudent = !!(
        (preflightSession && preflightSession.student_id)
        || (typeof selectedStudentIds !== 'undefined' && selectedStudentIds.size === 1)
        || (studentReference && !studentReference.ambiguous)
      );
      if (['student_diagnosis', 'review_plan'].includes(preflightCapability)) {
        if (!hasPreflightExam) {
          showToast('这个分析需要先选择考试，请先在成绩面板选择一次考试后再发送。');
          return;
        }
        if (preflightCapability === 'student_diagnosis' && studentReference && studentReference.ambiguous) {
          _setStudentScopePrompt(studentReference, false, {
            requestText: text,
            appendUserMessage: true,
            consumeInput: true,
            plugin: selectedPlugin || tmFindPlugin('student_diagnosis'),
          });
          return;
        }
        if (preflightCapability === 'student_diagnosis' && !hasPreflightStudent) {
          _setStudentScopePrompt(null, false, {
            requestText: text,
            appendUserMessage: true,
            consumeInput: true,
            plugin: selectedPlugin || tmFindPlugin('student_diagnosis'),
          });
          return;
        }
      }

      // U5-03: 数据就绪检查——发送前先确认班级/考试数据存在
      if (typeof teachMateOnboarding !== 'undefined') {
        try {
          var readiness = await teachMateOnboarding.checkDataReadiness();
          teachMateState.setDataReady(readiness);
          if (!readiness.ready) {
            // 仍允许发送；普通对话不需要考试，分析类快捷任务会在后续门禁中提示。
            showToast('数据就绪检查：' + readiness.issues.map(function (i) { return i.message; }).join('；'));
          }
        } catch (e) { /* 检查失败不阻断发送 */ }
      }

      // 确保有会话；如果教师在已完成对话中改了班级/考试，先创建新的分析会话。
      var sessionId = teachMateState.currentSessionId;
      var pendingQuickTask = teachMateState._pendingQuickTask || selectedPluginTask || null;
      var currentSession = (initialSnapshot.sessions || []).find(function (s) { return String(s.id) === String(sessionId); });
      var createFreshSession = false;
      var resolvedScopePayload = null;
      if (sessionId && currentSession && !initialSnapshot.isRunning) {
        try {
          var scopeResolution = await _scopeChangeRequiresNewSession(initialSnapshot, currentSession, pendingQuickTask || 'general_chat');
          createFreshSession = !!(scopeResolution && scopeResolution.changed);
          resolvedScopePayload = scopeResolution && scopeResolution.payload;
        } catch (scopeError) {
          showToast('读取新的班级或考试范围失败：' + (scopeError.message || scopeError));
          return;
        }
      }
      // 消息中点名了学生时，单人诊断必须固定到该学生；不要复用未绑定或绑定了另一位学生的旧会话。
      // 点名与换人由服务端本轮实体解析处理，保留当前对话的连续性。
      if (!sessionId || createFreshSession) {
        try {
          // exam_analysis 可在未绑定数据库考试时基于文字/已确认附件运行；
          // 只有学生诊断和复习计划仍需要先选择考试。
          if (!_ensureExamSelected(pendingQuickTask)) return;

          var result = resolvedScopePayload
            ? { payload: resolvedScopePayload }
            : await _buildSessionPayload(pendingQuickTask || 'general_chat', studentReference && studentReference.key);
          var session = await teachMateApi.createSession(result.payload);
          sessionId = session.id;
          var sessions = await teachMateApi.listSessions(currentTermId);
          teachMateState.setSessions(sessions || []);
          teachMateState.setCurrentSession(sessionId);
          renderNav();
          if (createFreshSession && !studentReference && typeof showToast === 'function') showToast('已按新的班级/考试范围开始分析', 'success');
          // P1-F: 解析新会话的上下文名称
          _resolveContextNames(sessionId);
        } catch (e) {
          if (e && e.code === 'STUDENT_NOT_FOUND') {
            _setStudentScopePrompt(studentReference, true, {
              requestText: text,
              appendUserMessage: true,
              consumeInput: true,
              plugin: selectedPlugin || tmFindPlugin('student_diagnosis'),
            });
            return;
          }
          showToast('创建对话失败: ' + (e.message || e));
          return;
        }
      }

      // 清空输入框和草稿
      input.value = '';
      input.style.height = 'auto';
      teachMateState.clearDraft();

      // 添加用户消息到状态（附上待发送附件快照：run 完成后以服务端数据覆盖）
      var pendingForMessage = teachMateState.getPendingAttachments()
        .filter(function (a) { return a.status === 'ready' || a.status === 'confirmed'; })
        .map(function (a) {
          return {
            id: Number(a.attachmentId),
            title: a.title || null,
            original_name: a.originalName || a.title || null,
            mime_type: null,
            size_bytes: null,
          };
        });
      // 追问没有重新选择文件时，后端会沿用会话里的正式附件；这里同步展示
      // 最近消息中的附件，让用户明确看到本轮仍在使用同一份资料。
      var optimisticAtts = pendingForMessage;
      if (!optimisticAtts.length) {
        var inheritedById = {};
        (initialSnapshot.messages || []).forEach(function (message) {
          (message.attachments || []).forEach(function (attachment) {
            if (attachment && attachment.id != null) inheritedById[String(attachment.id)] = attachment;
          });
        });
        optimisticAtts = Object.keys(inheritedById).map(function (id) { return inheritedById[id]; });
      }
      teachMateState.appendMessage({
        role: 'user',
        content_text: text,
        plugin: selectedPlugin ? { name: selectedPlugin.name, icon: selectedPlugin.icon_asset } : null,
        attachments: optimisticAtts,
        created_at: new Date().toISOString(),
      });

      // 添加 pending assistant 消息
      teachMateState.appendMessage({
        role: 'assistant',
        content_text: studentReference ? '我会为“' + studentReference.name + '”进行学生诊断。' : '',
        _pending: true,
        created_at: new Date().toISOString(),
      });

      // 开始提交
      teachMateState.startSubmitting();
      render();
      tmScrollMessagesToBottom();

      try {
        // P1-12/P1-13: 附带快捷任务路由（如果有）
        var quickTask = teachMateState._pendingQuickTask || selectedPluginTask || null;
        var effectiveCapability = quickTask || 'general_chat';
        var currentSession = (
          teachMateState.getSnapshot().sessions || []
        ).find(function (s) {
          return s.id === Number(sessionId);
        });
        // U5: 携带待发送附件
        var attachmentIds = teachMateState.getPendingAttachments()
          .filter(function (a) { return a.status === 'ready' || a.status === 'confirmed'; })
          .map(function (a) { return a.attachmentId; });
        // 附件与必需作用域已在插入乐观消息前完成预检；这里保留发送前的
        // 快照读取，只用于构造本次请求，不再把失败校验写成助手消息。
        // exam_analysis 可不绑定数据库考试；其余需要考试的能力保持原有门禁。
        if (['student_diagnosis', 'review_plan'].includes(effectiveCapability)) {
          if ((!currentSession || !currentSession.exam_id) && !currentExamId) {
            teachMateState.setIdle();
            teachMateState.updateLastAssistantMessage({
              content_text: '这个分析需要先选择考试，请先在成绩面板选择一次考试后再发送。',
            });
            teachMateState._pendingQuickTask = null;
            render();
            return;
          }
        }
        if (effectiveCapability === 'student_diagnosis' && (!currentSession || !currentSession.student_id) && !(studentReference && !studentReference.ambiguous)) {
          teachMateState.setIdle();
          teachMateState.updateLastAssistantMessage({
            content_text: '学生诊断需要绑定一名学生。请在学生列表中选中一名学生后新建对话。',
          });
          teachMateState._pendingQuickTask = null;
          render();
          return;
        }
        var modelId = teachMateState.getSnapshot().currentModelId || null;
        var response = await teachMateApi.sendMessage(sessionId, text, quickTask, attachmentIds, modelId,
          null, initialSnapshot.selectedPluginId === 'targeted_practice' ? 'targeted_practice' : null);
        // 发送期间允许教师切换历史对话；晚到的响应不能把运行状态写进新会话。
        if (teachMateState.currentSessionId !== Number(sessionId)) return;
        if (attachmentIds.length) teachMateState.clearPendingAttachments();
        await teachMateState.handleSendResponse(response);
        // 清除一次性 quick_task
        teachMateState._pendingQuickTask = null;
        render();
      } catch (e) {
        var errMsg = e && e.message ? String(e.message) : String(e || '发送失败');
        if (e && e.code === 'SCOPE_MISSING_EXAM') {
          errMsg = '当前任务缺少必要范围：exam_id。（SCOPE_MISSING_EXAM）请先在“选择考试”中绑定一个考试，再重试；本次输入已保留。';
        }
        // 任何服务端校验失败都保留本次文字，避免用户修正考试/附件后重新输入。
        var failedInput = document.getElementById('tmInput');
        if (failedInput) failedInput.value = text;
        teachMateState.setDraft(text);
        // 网络断开/超时特殊处理。网络失败时恢复输入；附件始终保留，便于修复后直接重发。
        if (e && (e.name === 'TypeError' || e.code === 'REQUEST_TIMEOUT') || errMsg.includes('fetch') || errMsg.includes('network') || errMsg.includes('Network')) {
          errMsg = e && e.code === 'REQUEST_TIMEOUT'
            ? '请求超时，请检查网络后重试。'
            : '网络连接失败，请检查网络后重试。';
        }
        teachMateState.updateLastAssistantMessage({
          content_text: '发送失败: ' + errMsg,
        });
        teachMateState._pendingQuickTask = null;
        teachMateState.failSubmitting(errMsg);
        render();
      }
    }

    /** 取消当前运行 */
    async function tmCancelRun() {
      try {
        await teachMateState.cancelCurrentRun();
        render();
      } catch (e) {
        showToast('取消失败: ' + (e.message || e));
      }
    }

    /** 重试当前运行 */
    async function tmRetryRun() {
      try {
        await teachMateState.retryCurrentRun();
        render();
      } catch (e) {
        showToast('重试失败: ' + (e.message || e));
      }
    }

    async function tmConfirmBudget() {
      await teachMateState.confirmCurrentRun();
      render();
    }

    var tmBatchPollTimer = null;
    var tmBatchPollingGroupId = null;
    var tmBatchScopeDraft = null;
    var tmBatchRetryDraft = null;
    function tmStopAnalysisGroupPolling() {
      if (tmBatchPollTimer) { clearTimeout(tmBatchPollTimer); tmBatchPollTimer = null; }
      tmBatchPollingGroupId = null;
    }
    async function tmRefreshAnalysisGroup(groupId, poll) {
      if (!groupId || !teachMateApi || typeof teachMateApi.getAnalysisGroup !== 'function') return null;
      if (poll !== false) tmBatchPollingGroupId = String(groupId);
      try {
        var group = await teachMateApi.getAnalysisGroup(Number(groupId));
        if (tmBatchPollingGroupId !== String(groupId)) return null;
        var currentGroup = teachMateState.getSnapshot().analysisGroup;
        if (currentGroup && String(currentGroup.id) !== String(groupId)) return null;
        if (typeof teachMateState.setAnalysisGroup === 'function') teachMateState.setAnalysisGroup(group);
        var terminal = ['completed', 'partially_completed', 'failed', 'cancelled', 'waiting_confirmation'].indexOf(String(group && group.status || '')) >= 0;
        if (poll !== false && !terminal) {
          tmStopAnalysisGroupPolling();
          tmBatchPollTimer = setTimeout(function () { tmRefreshAnalysisGroup(groupId, true); }, 3000);
        } else if (terminal) tmStopAnalysisGroupPolling();
        return group;
      } catch (e) {
        if (tmBatchPollingGroupId !== String(groupId)) return null;
        var activeGroup = teachMateState.getSnapshot().analysisGroup;
        if (activeGroup && String(activeGroup.id) !== String(groupId)) return null;
        if (poll !== false) {
          tmStopAnalysisGroupPolling();
          tmBatchPollTimer = setTimeout(function () { tmRefreshAnalysisGroup(groupId, true); }, 5000);
        }
        return null;
      }
    }
    async function tmCancelAnalysisGroup(groupId) {
      if (!groupId || !teachMateApi || typeof teachMateApi.cancelAnalysisGroup !== 'function') return;
      try {
        var group = await teachMateApi.cancelAnalysisGroup(Number(groupId));
        tmStopAnalysisGroupPolling();
        if (typeof teachMateState.setAnalysisGroup === 'function') teachMateState.setAnalysisGroup(group);
        render();
      } catch (e) { showToast('停止批量任务失败：' + (e.message || e), 'error'); }
    }
    async function tmConfirmAnalysisGroup(groupId) {
      var group = teachMateState.getSnapshot().analysisGroup;
      if (!group || String(group.id) !== String(groupId) || String(group.status) !== 'waiting_confirmation') return;
      var budget = Number(group.estimated_cost_yuan || 0);
      if (!Number.isFinite(budget) || budget < 0) { showToast('暂时无法确认这份执行方案，请刷新后重试。', 'error'); return; }
      try {
        var result = await teachMateApi.confirmAnalysisGroup(Number(groupId), budget);
        if (typeof teachMateState.setAnalysisGroup === 'function') teachMateState.setAnalysisGroup(result);
        showToast('已确认执行，开始生成学生画像。', 'success');
        render();
        tmRefreshAnalysisGroup(groupId, true);
      } catch (e) { showToast('确认执行失败：' + (e.message || e), 'error'); }
    }
    function tmUpdateBatchRetrySelection() {
      var list = document.getElementById('tmBatchRetryList');
      if (!list) return;
      var boxes = Array.from(list.querySelectorAll('input[data-act="tm-batch-retry-student"]'));
      var checked = boxes.filter(function (input) { return input.checked; }).length;
      var selectAll = document.querySelector('[data-act="tm-batch-retry-select-all"]');
      if (selectAll) {
        selectAll.checked = boxes.length > 0 && checked === boxes.length;
        selectAll.indeterminate = checked > 0 && checked < boxes.length;
      }
      var count = document.querySelector('[data-role="tm-batch-retry-count"]');
      if (count) count.textContent = '已选择 ' + checked + ' / ' + boxes.length + ' 名学生';
      var save = document.querySelector('[data-act="tm-batch-retry-save"]');
      if (save) save.disabled = checked === 0;
    }
    async function tmRetryAnalysisGroup(groupId) {
      var snapshot = teachMateState.getSnapshot();
      var group = snapshot.analysisGroup;
      if (!group || String(group.id) !== String(groupId)) return;
      var retryable = ['failed', 'cancelled', 'queued', 'running', 'waiting_confirmation'];
      var contextNames = (snapshot && snapshot.contextNames) || {};
      var studentNames = contextNames.student || {};
      var candidates = [];
      (Array.isArray(group.tasks) ? group.tasks : []).forEach(function (task) {
        if (!task || task.task_role === 'exam_agent' || retryable.indexOf(String(task.status || '')) < 0) return;
        (Array.isArray(task.student_ids) ? task.student_ids : []).forEach(function (id) {
          var key = String(id);
          if (candidates.some(function (item) { return item.id === key; })) return;
          var stateLabel = ({ failed: '失败', cancelled: '已停止', queued: '未完成', running: '进行中', waiting_confirmation: '待执行' }[String(task.status || '')] || '未完成');
          if (String(task.status || '') === 'failed' && Number(task.retry_count || 0) > 0) stateLabel = '失败（已自动重试）';
          candidates.push({ id: key, name: studentNames[id] || studentNames[key] || ('学生 #' + id), status: stateLabel });
        });
      });
      if (!candidates.length) { showToast('当前没有可重新生成的学生', 'error'); return; }
      tmBatchRetryDraft = { groupId: String(groupId), group: group, candidates: candidates };
      var rows = candidates.map(function (item) {
        return '<label class="tm-batch-scope-row tm-batch-retry-row"><input type="checkbox" data-act="tm-batch-retry-student" data-id="' + escapeAttr(item.id) + '" checked><span class="tm-batch-scope-check" aria-hidden="true"></span><span class="tm-batch-scope-copy"><strong>' + escapeHtml(item.name) + '</strong><small>' + escapeHtml(item.status) + '</small></span></label>';
      }).join('');
      var body = '<div class="tm-batch-retry-editor"><div class="tm-batch-scope-toolbar"><label><input type="checkbox" data-act="tm-batch-retry-select-all" checked>全选可重试学生</label><span data-role="tm-batch-retry-count">—</span></div><div class="tm-batch-scope-list tm-batch-retry-list" id="tmBatchRetryList">' + rows + '</div><p class="tm-batch-scope-note">已完成的学生不会重复生成；这里只会重新处理选中的失败或未完成学生。</p></div>';
      openModal('重新生成学生画像', body, '<button class="btn btn-text" data-act="tm-batch-retry-cancel">取消</button><button class="btn btn-primary" data-act="tm-batch-retry-save">重新生成</button>');
      tmUpdateBatchRetrySelection();
    }
    async function tmApplyBatchRetry() {
      var draft = tmBatchRetryDraft;
      if (!draft) return;
      var boxes = Array.from(document.querySelectorAll('#tmBatchRetryList input[data-act="tm-batch-retry-student"]:checked'));
      var studentIds = boxes.map(function (input) { return Number(input.dataset.id); }).filter(function (id) { return Number.isFinite(id) && id > 0; });
      if (!studentIds.length) { showToast('至少选择一名学生', 'error'); return; }
      var save = document.querySelector('[data-act="tm-batch-retry-save"]');
      if (save) save.disabled = true;
      try {
        var result = await teachMateApi.retryAnalysisGroup(Number(draft.groupId), studentIds);
        tmBatchRetryDraft = null;
        tmStopAnalysisGroupPolling();
        if (typeof teachMateState.setAnalysisGroup === 'function') teachMateState.setAnalysisGroup(result);
        closeModal({ restorePrevious: false });
        render();
        showToast('已提交 ' + studentIds.length + ' 名学生，开始重新生成。', 'success');
        tmRefreshAnalysisGroup(draft.groupId, true);
      } catch (e) {
        if (save) save.disabled = false;
        showToast('重新生成失败：' + (e.message || e), 'error');
      }
    }
    function tmCancelBatchRetryEditor() {
      tmBatchRetryDraft = null;
      closeModal({ restorePrevious: false });
    }
    function tmUpdateBatchScopeSelection() {
      var list = document.getElementById('tmBatchScopeList');
      if (!list) return;
      var boxes = Array.from(list.querySelectorAll('input[data-act="tm-batch-scope-student"]'));
      var checked = boxes.filter(function (input) { return input.checked; }).length;
      var selectAll = document.querySelector('[data-act="tm-batch-scope-select-all"]');
      if (selectAll) {
        selectAll.checked = boxes.length > 0 && checked === boxes.length;
        selectAll.indeterminate = checked > 0 && checked < boxes.length;
      }
      var count = document.querySelector('[data-role="tm-batch-scope-count"]');
      if (count) count.textContent = '已选择 ' + checked + ' / ' + boxes.length + ' 名学生';
      var apply = document.querySelector('[data-act="tm-batch-scope-save"]');
      if (apply) apply.disabled = checked === 0;
    }

    async function tmEditAnalysisGroup(groupId) {
      var snapshot = teachMateState.getSnapshot();
      var group = snapshot.analysisGroup;
      if (!group || String(group.id) !== String(groupId) || String(group.status) !== 'waiting_confirmation') return;
      try {
        showToast('正在读取当前班级学生…');
        var students = await teachMateApi.listStudents(currentTermId, group.class_id);
        students = (Array.isArray(students) ? students : []).filter(function (student) {
          return student && Number.isFinite(Number(student.id));
        });
        var selectedIds = {};
        (Array.isArray(group.tasks) ? group.tasks : []).forEach(function (task) {
          (Array.isArray(task.student_ids) ? task.student_ids : []).forEach(function (id) { selectedIds[String(id)] = true; });
        });
        tmBatchScopeDraft = { groupId: String(groupId), group: group, students: students };
        var rows = students.map(function (student) {
          var id = Number(student.id);
          var name = String(student.name || ('学生 #' + id));
          var no = String(student.student_no || student.studentNo || '').trim();
          return '<label class="tm-batch-scope-row"><input type="checkbox" data-act="tm-batch-scope-student" data-id="' + escapeAttr(String(id)) + '"' + (selectedIds[String(id)] ? ' checked' : '') + '><span class="tm-batch-scope-check" aria-hidden="true"></span><span class="tm-batch-scope-copy"><strong>' + escapeHtml(name) + '</strong>' + (no ? '<small>学号 ' + escapeHtml(no) + '</small>' : '') + '</span></label>';
        }).join('');
        if (!rows) rows = '<div class="tm-batch-scope-empty">当前班级暂无学生记录。</div>';
        var body = '<div class="tm-batch-scope-editor"><div class="tm-batch-scope-toolbar"><label><input type="checkbox" data-act="tm-batch-scope-select-all">全选当前班级</label><span data-role="tm-batch-scope-count">—</span></div><div class="tm-batch-scope-list" id="tmBatchScopeList">' + rows + '</div><p class="tm-batch-scope-note">选择完成后会重新生成执行方案；确认前不会开始分析。</p></div>';
        openModal('修改学生范围', body, '<button class="btn btn-text" data-act="tm-batch-scope-cancel">取消</button><button class="btn btn-primary" data-act="tm-batch-scope-save">应用范围</button>');
        tmUpdateBatchScopeSelection();
      } catch (e) {
        tmBatchScopeDraft = null;
        showToast('读取学生名单失败：' + (e.message || e), 'error');
      }
    }

    async function tmApplyBatchScope() {
      var draft = tmBatchScopeDraft;
      if (!draft || !draft.group) return;
      var boxes = Array.from(document.querySelectorAll('#tmBatchScopeList input[data-act="tm-batch-scope-student"]:checked'));
      var studentIds = boxes.map(function (input) { return Number(input.dataset.id); }).filter(function (id) { return Number.isFinite(id) && id > 0; });
      if (!studentIds.length) { showToast('至少选择一名学生', 'error'); return; }
      var group = draft.group;
      var snapshot = teachMateState.getSnapshot();
      var sessionId = snapshot.currentSessionId;
      if (!sessionId) { showToast('当前对话已失效，请新建对话后重试', 'error'); return; }
      var saveButton = document.querySelector('[data-act="tm-batch-scope-save"]');
      if (saveButton) saveButton.disabled = true;
      try {
        var replacement = await teachMateApi.createAnalysisGroup({
          session_id: Number(sessionId),
          capability: 'student_diagnosis',
          term_id: Number(currentTermId),
          class_id: group.class_id == null ? null : Number(group.class_id),
          exam_id: Number(group.exam_id),
          student_ids: studentIds,
          max_concurrency: Math.max(1, Math.min(4, Number(group.max_concurrency || 4))),
          shard_size: 6,
          require_confirmation: true,
        });
        await teachMateApi.cancelAnalysisGroup(Number(group.id));
        tmBatchScopeDraft = null;
        tmStopAnalysisGroupPolling();
        if (typeof teachMateState.setAnalysisGroup === 'function') teachMateState.setAnalysisGroup(replacement);
        if (typeof teachMateState.appendMessage === 'function') {
          teachMateState.appendMessage({
            role: 'assistant',
            content_text: '已按所选学生重新整理诊断方案，共 ' + studentIds.length + ' 名学生。请核对名单后在输入区确认执行。',
            created_at: new Date().toISOString(),
          });
        }
        closeModal({ restorePrevious: false });
        render();
        showToast('学生范围已更新，请重新确认执行。', 'success');
      } catch (e) {
        if (saveButton) saveButton.disabled = false;
        showToast('更新学生范围失败：' + (e.message || e), 'error');
      }
    }
    function tmCancelBatchScopeEditor() {
      tmBatchScopeDraft = null;
      closeModal({ restorePrevious: false });
    }
    function tmOpenBatchResult(groupId) {
      var group = teachMateState.getSnapshot().analysisGroup;
      if (!group || String(group.id) !== String(groupId)) return;
      var result = group.merged_result || {};
      var findings = Array.isArray(result.findings) ? result.findings : [];
      var recommendations = Array.isArray(result.recommendations) ? result.recommendations : [];
      var body = '<div class="tm-batch-result-modal"><p class="tm-batch-result-summary">' + escapeHtml(result.summary || '批量画像结果已生成。') + '</p>' +
        (findings.length ? '<h4>共性发现</h4><ul>' + findings.slice(0, 8).map(function (item) { return '<li><strong>' + escapeHtml(item.title || '发现') + '</strong>：' + escapeHtml(item.claim || item.description || '') + '</li>'; }).join('') + '</ul>' : '<p class="tm-batch-result-empty">暂无可合并的共性发现。</p>') +
        (recommendations.length ? '<h4>教学建议</h4><ul>' + recommendations.slice(0, 8).map(function (item) { return '<li><strong>' + escapeHtml(item.action || '建议') + '</strong>：' + escapeHtml(item.rationale || '') + '</li>'; }).join('') + '</ul>' : '') + '</div>';
      openModal('批量画像汇总', body);
    }
    if (typeof window !== 'undefined') window.tmRefreshAnalysisGroup = tmRefreshAnalysisGroup;

    /** 查看证据 */
    async function tmViewEvidence(runId) {
      if (!runId) return;
      try {
        var evidence = await teachMateApi.getRunEvidence(runId);
        var html = '<div class="tm-evidence-modal">';
        if (!evidence || !evidence.length) {
          html += '<p class="tm-evidence-empty">暂无证据数据</p>';
        } else {
          evidence.forEach(function (ev, i) {
            html += '<div class="tm-evidence-item">';
            html += '<div class="tm-evidence-header"><strong>#' + (i + 1) + '</strong> ';
            html += '<span class="tm-evidence-type">' + escapeHtml(ev.evidence_type || ev.type || '未知') + '</span></div>';
            if (ev.display_summary) {
              html += '<div class="tm-evidence-summary">' + escapeHtml(ev.display_summary) + '</div>';
            }
            if (ev.local_fact) {
              var factStr = typeof ev.local_fact === 'string'
                ? ev.local_fact
                : JSON.stringify(ev.local_fact, null, 2);
              html += '<pre class="tm-evidence-result">' + escapeHtml(factStr) + '</pre>';
            }
            if (ev.source_entity) {
              html += '<div class="tm-evidence-source">来源: ' + escapeHtml(ev.source_entity) + '</div>';
            }
            html += '</div>';
          });
        }
        html += '</div>';
        openModal('运行证据', html);
      } catch (e) {
        showToast('加载证据失败: ' + (e.message || e));
      }
    }

    /** 从已完成报告准备一个带当前会话上下文的后续任务；教师仍需检查并发送。 */
    function tmPrepareReportFollowup(button) {
      var capability = String(button && button.dataset.capability || 'general_chat');
      var prompt = String(button && button.dataset.prompt || '').trim();
      if (!prompt) return;
      var snapshot = teachMateState.getSnapshot();
      var session = (snapshot.sessions || []).find(function (item) {
        return String(item.id) === String(snapshot.currentSessionId);
      });
      if (capability === 'review_plan') {
        if (!session || !session.exam_id) {
          showToast('请先为当前对话选择一场考试，再生成复习计划。');
          return;
        }
        var planPlugin = tmFindPlugin('review_plan');
        if (!planPlugin || !planPlugin.enabled) {
          showToast('复习计划功能当前不可用，请在设置 → 功能扩展中检查状态。');
          return;
        }
        teachMateState.setSelectedPluginId(planPlugin.id);
        teachMateState._pendingQuickTask = 'review_plan';
      } else {
        // 材料生成沿用当前会话与已核验分析，不再强制调用原来的专用插件。
        teachMateState.setSelectedPluginId('');
        teachMateState._pendingQuickTask = null;
      }
      teachMateState.setDraft(prompt);
      render();
      var input = document.getElementById('tmInput');
      if (input) {
        input.value = prompt;
        input.focus();
        input.setSelectionRange(prompt.length, prompt.length);
      }
    }

    /** 处理 TeachMate 相关的 data-act 点击事件 */
    function handleTeachMateAction(act, t) {
      if (act.indexOf('tm-task-') === 0 && typeof teachMateTasks !== 'undefined') {
        teachMateTasks.action(act, t);
        return true;
      }
      if (act === 'tm-materials-expand' || act === 'tm-materials-collapse') {
        teachMateReport.setMaterialsExpanded(t, act === 'tm-materials-expand');
        return true;
      }
      var materialActions = {
        'tm-material-edit': 'edit',
        'tm-material-compare': 'compare',
        'tm-material-back': 'back',
        'tm-material-save': 'save',
        'tm-material-cancel': 'cancel',
      };
      if (materialActions[act]) {
        var materialCard = t.closest('.tm-material-card');
        var materialRunId = materialCard && materialCard.dataset.materialRunId;
        var materialIndex = materialCard && materialCard.dataset.sectionIndex;
        if (act === 'tm-material-save') {
          var draft = materialCard && teachMateReport.getMaterialDraft(materialRunId, materialIndex);
          if (!draft || !materialRunId) { showToast('编辑草稿不可用，请重新编辑', 'error'); return true; }
          t.disabled = true;
          teachMateApi.saveMaterialEdit(materialRunId, materialIndex, draft).then(function (saved) {
            teachMateReport.commitSavedMaterialEdit(materialRunId, materialIndex, saved);
            render();
            showToast('教师修改已保存，导出将使用修改稿', 'success');
          }).catch(function (error) {
            showToast(error && error.message ? error.message : '保存失败，草稿仍在当前页面', 'error');
          }).finally(function () { if (t.isConnected) t.disabled = false; });
          return true;
        }
        var sourceAnswer = materialRunId ? _findReportForRun(materialRunId) : null;
        if (!materialCard || !sourceAnswer || !teachMateReport.modifyMaterial(materialActions[act], t, sourceAnswer)) {
          showToast('无法读取这份材料，请刷新后重试');
          return true;
        }
        render();
        setTimeout(function () {
          var targetCard = Array.from(document.querySelectorAll('.tm-material-card')).find(function (card) {
            return card.dataset.materialRunId === String(materialRunId) && card.dataset.sectionIndex === String(materialIndex);
          });
          if (targetCard) {
            var focusTarget = act === 'tm-material-edit' || act === 'tm-material-save'
              ? targetCard.querySelector('.tm-material-editor textarea, [data-act="tm-material-edit"], [data-act="tm-material-compare"]')
              : targetCard.querySelector('[data-act="tm-material-edit"]');
            if (focusTarget) focusTarget.focus();
          }
        }, 0);
        return true;
      }
      if (act === 'tm-new-chat') { tmNewChat(); return true; }
      if (act === 'tm-bind-current-exam') {
        teachMateState.setBindCurrentExam(!teachMateState.getSnapshot().bindCurrentExam);
        render();
        return true;
      }
      if (act === 'tm-toggle-search') { tmToggleSearchPanel(); return true; }
      if (act === 'tm-composer-plus') { tmToggleImportMenu(t); return true; }
      if (act === 'tm-plus-select-exam') { tmOpenToolSubmenu('exam'); return true; }
      if (act === 'tm-plus-select-plugin') { tmOpenToolSubmenu('plugin'); return true; }
      if (act === 'tm-plugin-option') { tmSelectPlugin(t); return true; }
      if (act === 'tm-plugin-clear') {
        teachMateState.setSelectedPluginId('');
        teachMateState._pendingQuickTask = null;
        if (typeof teachMateState.clearScopePrompt === 'function') teachMateState.clearScopePrompt();
        _discardStagedStudentScopeRequest();
        render();
        return true;
      }
      if (act === 'tm-model-toggle') { tmToggleModelMenu(t); return true; }
      if (act === 'tm-model-option') { tmSelectModelMockup(t); return true; }
      if (act === 'tm-model-custom') { tmCloseModelMenu(); tmOpenAgentSettings('models'); return true; }
      if (act === 'tm-class-toggle') { tmToggleClassMenu(t); return true; }
      if (act === 'tm-class-option') { tmSelectClass(t); return true; }
      if (act === 'tm-exam-toggle') { tmToggleExamMenu(t); return true; }
      if (act === 'tm-exam-option') { tmSelectExam(t); return true; }
      if (act === 'tm-toggle-inline-quality') {
        var qualityBox = t.closest('.tm-inline-report-quality');
        var qualityDetail = qualityBox && qualityBox.querySelector('.tm-inline-report-quality-detail');
        if (qualityDetail) {
          var willOpen = qualityDetail.hidden;
          qualityDetail.hidden = !willOpen;
          t.setAttribute('aria-expanded', willOpen ? 'true' : 'false');
          t.textContent = willOpen ? '收起详情' : '查看详情';
        }
        return true;
      }
      if (act === 'tm-right-tab') { tmSelectRightPanelTab(t.dataset.tab || 'report'); return true; }
      if (act === 'tm-practice-plugin-open') { closeModal(); tmSelectPlugin({ dataset: { pluginId: 'targeted_practice' } }); var input = document.getElementById('tmInput'); if (input) input.focus(); showToast('输入学生姓名和练习需求即可，例如：查看李明近期的薄弱知识点，再出三道专项练习。'); return true; }
      if (act === 'tm-plugin-placeholder') { tmOpenAgentSettings('plugins'); return true; }
      if (act === 'tm-settings-tab') {
        var settingsTab = t.dataset.settingsTab || 'models';
        if (settingsTab === 'archive') tmArchiveLoaded = false;
        tmRenderAgentSettingsTab(settingsTab);
        return true;
      }
      if (act === 'tm-settings-toggle') { t.classList.toggle('is-on'); t.setAttribute('aria-pressed', t.classList.contains('is-on') ? 'true' : 'false'); return true; }
      if (act === 'tm-plugin-install') { tmOpenPluginInstallModal(); return true; }
      if (act === 'tm-connect-workbuddy') { tmOpenWorkBuddyConnect(); return true; }
      if (act === 'tm-disconnect-workbuddy') { tmDisconnectWorkBuddy(); return true; }
      if (act === 'tm-copy-workbuddy-config') { tmCopyWorkBuddyConfig(); return true; }
      if (act === 'tm-workbuddy-connect-close') { closeModal(); return true; }
      if (act === 'tm-plugin-install-submit') {
        var archiveInput = document.getElementById('tmPluginArchive');
        var pathInput = document.getElementById('tmPluginPath');
        var selectedFile = (archiveInput && archiveInput.files && archiveInput.files[0]) || tmPendingPluginArchiveFile;
        var installRequest = selectedFile ? teachMateApi.installPluginArchive(selectedFile) : (pathInput && pathInput.value.trim() ? teachMateApi.installPlugin(pathInput.value.trim()) : Promise.reject(new Error('请选择 ZIP 文件或填写本机插件路径')));
        installRequest.then(function () { return teachMateApi.listPlugins(); }).then(function (payload) {
          tmPendingPluginArchiveFile = null;
          teachMateState.setPlugins(payload || []);
          closeModal();
          tmRenderAgentSettingsTab('plugins');
          showToast('插件安装成功', 'success');
        }).catch(function (error) { tmPendingPluginArchiveFile = null; showToast('插件安装失败：' + (error.message || error), 'error'); });
        return true;
      }
      if (act === 'tm-settings-plugin-toggle') { tmOpenPluginManager(t.dataset.pluginId); return true; }
      if (act === 'tm-settings-plugin-details') { tmRenderPluginDetails(t.dataset.pluginId); return true; }
      if (act === 'tm-settings-details-back') { tmRenderAgentSettingsTab('plugins'); return true; }
      if (act === 'tm-open-tutorial') { tmOpenTutorial(t.dataset.tutorial); return true; }
      if (act === 'tm-guide-apply') {
        // 插件详情页的示例问法：填入输入框 + 选中插件（出现“调用 ××”状态条），
        // 关闭设置后回到对话区，点发送即以该插件能力执行。
        var guidePrompt = t.dataset.prompt || '';
        var guidePluginId = t.dataset.pluginId || '';
        if (typeof teachMateState.setSelectedPluginId === 'function') teachMateState.setSelectedPluginId(guidePluginId);
        teachMateState._pendingQuickTask = tmPluginQuickTask(tmFindPlugin(guidePluginId));
        var guideInput = document.getElementById('tmInput');
        if (guideInput) { guideInput.value = guidePrompt; }
        teachMateState.setDraft(guidePrompt);
        closeModal({ restorePrevious: false });
        render();
        var readyGuideInput = document.getElementById('tmInput');
        if (readyGuideInput) readyGuideInput.focus();
        return true;
      }
      if (act === 'tm-settings-plugin-cancel') { tmPendingPluginArchiveFile = null; closeModal(); return true; }
      if (act === 'tm-settings-plugin-toggle-confirm') {
        var managedPlugin = tmFindPlugin(t.dataset.pluginId);
        if (!managedPlugin) { closeModal(); return true; }
        var nextEnabled = !managedPlugin.enabled;
        var togglePromise = nextEnabled ? teachMateApi.enablePlugin(managedPlugin.id) : teachMateApi.disablePlugin(managedPlugin.id);
        togglePromise.then(function (payload) {
          teachMateState.setPlugins(payload ? [payload].concat(tmPluginDefinitions().filter(function (item) { return item.id !== managedPlugin.id; }).map(function (item) { return item.raw; })) : []);
          closeModal();
          tmRenderAgentSettingsTab('plugins');
          showToast(managedPlugin.name + (nextEnabled ? '已启用' : '已停用'), 'success');
        }).catch(function (error) {
          showToast('插件状态更新失败：' + (error.message || error), 'error');
        });
        return true;
      }
      if (act === 'tm-settings-personalization-save') {
        var teacherName = document.getElementById('tm-settings-teacher-name');
        var teacherSubject = document.getElementById('tm-settings-teacher-subject');
        var userAddress = document.getElementById('tm-settings-user-address');
        var tone = document.getElementById('tm-settings-tone');
        var customPrompt = document.getElementById('tm-settings-custom-prompt');
        if (typeof state !== 'undefined' && state.teacher) {
          var previousTeacherName = state.teacher.name;
          var previousSubject = state.teacher.subject;
          state.teacher.name = (teacherName && teacherName.value || '').trim();
          state.teacher.subject = (teacherSubject && teacherSubject.value || '').trim() || subjectConfig().teacher_subject_default || subjectName();
          var payload = {
            teacher_name: state.teacher.name,
            subject: state.teacher.subject,
            user_address: (userAddress && userAddress.value || '').trim() || '老师',
            tone: (tone && tone.value) || 'rigorous',
            custom_prompt: (customPrompt && customPrompt.value || '').trim(),
          };
          teachMateApi.updateSettings(payload).then(function (settings) {
            teachMateState.setPersonalization(settings || payload);
            if (typeof saveData === 'function') saveData().catch(function () {});
            if (typeof renderNav === 'function') renderNav();
            showToast('个性化设置已保存', 'success');
          }).catch(function (error) {
            state.teacher.name = previousTeacherName;
            state.teacher.subject = previousSubject;
            showToast('个性化设置保存失败：' + (error.message || error), 'error');
          });
        }
        return true;
      }
      if (act === 'tm-settings-archive-restore') {
        var archivedId = t.dataset.id;
        var archivedFile = typeof state !== 'undefined' && (state.archivedDocuments || []).find(function (file) { return String(file.id) === String(archivedId); });
        if (!archivedFile) { showToast('未找到归档文件', 'error'); return true; }
        var restoreResult = typeof commitMutation === 'function' ? commitMutation(function () {
          state.paperDocuments = Array.isArray(state.paperDocuments) ? state.paperDocuments : [];
          state.paperDocuments.push(archivedFile);
          state.archivedDocuments = (state.archivedDocuments || []).filter(function (file) { return String(file.id) !== String(archivedId); });
        }, { successMessage: '文件已恢复到资料库', renderNavigation: true }) : null;
        Promise.resolve(restoreResult).then(function () { tmRenderAgentSettingsTab('archive'); });
        return true;
      }
      if (act === 'tm-settings-archive-purge') { showToast('为避免误删，永久清理将在下一阶段接入确认和数据库删除'); return true; }
      if (act === 'tm-archive-select') { tmUpdateArchiveSelection(); return true; }
      if (act === 'tm-archive-select-all') {
        var archiveList = document.getElementById('tmArchivedConversationList');
        if (archiveList) archiveList.querySelectorAll('input[data-act="tm-archive-select"]').forEach(function (input) { input.checked = t.checked; });
        tmUpdateArchiveSelection();
        return true;
      }
      if (act === 'tm-archive-restore') { tmRestoreArchivedSessions([t.dataset.id], t.dataset.id); return true; }
      if (act === 'tm-archive-batch-restore') { tmRestoreArchivedSessions(tmArchiveSelectedIds()); return true; }
      if (act === 'tm-archive-delete') { tmConfirmArchivedDelete([t.dataset.id]); return true; }
      if (act === 'tm-archive-batch-delete') { tmConfirmArchivedDelete(tmArchiveSelectedIds()); return true; }
      if (act === 'tm-archive-delete-cancel') {
        var returnToArchive = tmArchiveDeleteFromInline;
        tmArchivePendingDeleteIds = [];
        tmArchiveDeleteFromInline = false;
        closeModal();
        if (returnToArchive) tmOpenAgentSettings('archive');
        return true;
      }
      if (act === 'tm-archive-delete-confirm') { tmDeleteArchivedSessions(); return true; }
      if (act === 'tm-folder-create') { tmCreateFolder(); return true; }
      if (act === 'tm-folder-create-cancel') { closeModal(); return true; }
      if (act === 'tm-folder-create-confirm') { tmConfirmFolderCreate(t.dataset.sessionId); return true; }
      if (act === 'tm-folder-toggle') { teachMateState.toggleSessionFolder(t.dataset.folderId); renderNav(); return true; }
      if (act === 'tm-pin-session') {
        var wasPinned = (teachMateState.getSnapshot().pinnedSessionIds || []).map(String).indexOf(String(t.dataset.id)) >= 0;
        teachMateState.togglePinnedSession(t.dataset.id);
        renderNav();
        showToast(wasPinned ? '已取消置顶' : '已置顶，将固定在列表顶部', 'success');
        return true;
      }
      if (act === 'tm-conv-more') { tmToggleConvMenu(t); return true; }
      if (act === 'tm-move-to-folder') { tmMoveSessionToFolder(t.dataset.id, t.dataset.folderId); return true; }
      if (act === 'tm-move-new-folder') { tmMoveToNewFolder(t.dataset.id); return true; }
      if (act === 'tm-open-agent-settings') { tmOpenAgentSettings('system'); return true; }
      if (act === 'tm-conversation-select') { tmSelectSession(t.dataset.id); return true; }
      if (act === 'tm-suggestion') {
        var input = document.getElementById('tmInput');
        if (input) { input.value = t.dataset.prompt; teachMateState.setDraft(t.dataset.prompt); }
        // P1-12/P1-13: 如果快捷卡携带 data-quick-task，记住它，发送时附带；
        // 同时选中对应插件 → 输入区出现「调用 ××」状态条（与工具菜单选择一致）
        var suggestionQuickTask = t.dataset.quickTask || '';
        teachMateState._pendingQuickTask = suggestionQuickTask || null;
        if (suggestionQuickTask && typeof teachMateState.setSelectedPluginId === 'function') {
          var suggestionPlugin = tmFindPlugin(suggestionQuickTask);
          if (suggestionPlugin && suggestionPlugin.enabled) {
            teachMateState.setSelectedPluginId(suggestionPlugin.id);
          }
        }
        render();
        var readyInput = document.getElementById('tmInput');
        if (readyInput) readyInput.focus();
        if (t.dataset.providerUnavailable === 'true') showToast(t.title || '模型未就绪，可以先编辑问题，配置模型后再发送。');
        return true;
      }
      if (act === 'tm-send') { tmSendMessage(); return true; }
      if (act === 'tm-cancel') { tmConfirmCancel(); return true; }
      if (act === 'tm-retry') { tmRetryRun(); return true; }
      if (act === 'tm-confirm-budget') { tmConfirmBudget(); return true; }
      if (act === 'tm-batch-toggle') { if (typeof teachMateState.toggleAnalysisGroupDetails === 'function') teachMateState.toggleAnalysisGroupDetails(); return true; }
      if (act === 'tm-batch-cancel') { tmCancelAnalysisGroup(t.dataset.groupId); return true; }
      if (act === 'tm-batch-confirm') { tmConfirmAnalysisGroup(t.dataset.groupId); return true; }
      if (act === 'tm-batch-retry') { tmRetryAnalysisGroup(t.dataset.groupId); return true; }
      if (act === 'tm-batch-retry-student') { tmUpdateBatchRetrySelection(); return true; }
      if (act === 'tm-batch-retry-select-all') {
        document.querySelectorAll('#tmBatchRetryList input[data-act="tm-batch-retry-student"]').forEach(function (input) { input.checked = t.checked; });
        tmUpdateBatchRetrySelection();
        return true;
      }
      if (act === 'tm-batch-retry-cancel') { tmCancelBatchRetryEditor(); return true; }
      if (act === 'tm-batch-retry-save') { tmApplyBatchRetry(); return true; }
      if (act === 'tm-batch-edit') { tmEditAnalysisGroup(t.dataset.groupId); return true; }
      if (act === 'tm-batch-scope-student') { tmUpdateBatchScopeSelection(); return true; }
      if (act === 'tm-batch-scope-select-all') {
        document.querySelectorAll('#tmBatchScopeList input[data-act="tm-batch-scope-student"]').forEach(function (input) { input.checked = t.checked; });
        tmUpdateBatchScopeSelection();
        return true;
      }
      if (act === 'tm-batch-scope-cancel') { tmCancelBatchScopeEditor(); return true; }
      if (act === 'tm-batch-scope-save') { tmApplyBatchScope(); return true; }
      if (act === 'tm-batch-result') { tmOpenBatchResult(t.dataset.groupId); return true; }
      if (act === 'tm-view-evidence') { tmViewEvidence(t.dataset.runId); return true; }
      if (act === 'tm-view-evidence-item') { tmViewEvidenceItem(t.dataset.evidenceId, t.dataset.runId); return true; }
      if (act === 'tm-view-evidence-id') { tmViewEvidenceItem(t.dataset.evidenceId, t.dataset.runId); return true; }
      if (act === 'tm-attach') { tmAttachFile(); return true; }
      if (act === 'tm-library') { tmOpenLibrary(); return true; }
      if (act === 'tm-library-cancel') { closeModal(); return true; }
      if (act === 'tm-library-confirm') { tmConfirmLibrarySelection(); tmCloseImportMenu(); return true; }
      if (act === 'tm-attach-remove') { tmRemovePendingAttachment(t.dataset.attachmentId); return true; }
      if (act === 'tm-delete-session') { tmDeleteSession(t.dataset.id); return true; }
      if (act === 'tm-delete-cancel') { closeModal(); return true; }
      if (act === 'tm-delete-confirm') { closeModal(); _doDeleteSession(t.dataset.id); return true; }
      if (act === 'tm-rename-session') { tmRenameSession(t.dataset.id); return true; }
      if (act === 'tm-rename-cancel') { closeModal(); return true; }
      if (act === 'tm-rename-confirm') { _doRenameSession(t.dataset.id, (document.getElementById('tmRenameInput') || {}).value); return true; }
      if (act === 'tm-archive-session') { tmArchiveSession(t.dataset.id); return true; }
      if (act === 'tm-open-trash') { tmOpenArchivedConversations(); return true; }
      if (act === 'tm-restore-session') { tmRestoreSession(t.dataset.id); return true; }
      if (act === 'tm-toggle-right-panel') { tmToggleRightPanel(); return true; }
      if (act === 'tm-drawer-close') { tmCloseRightPanel(); return true; }
      if (act === 'tm-followup-task') { tmPrepareReportFollowup(t); return true; }
      if (act === 'tm-load-more') { tmLoadMoreMessages(); return true; }
      if (act === 'tm-toggle-thinking') {
        var box = document.getElementById('tmThinkingBox');
        if (!box) return true;
        var open = box.hidden;
        box.hidden = !open;
        t.setAttribute('aria-expanded', open ? 'true' : 'false');
        var chevron = t.querySelector('.tm-thinking-chevron');
        if (chevron) chevron.textContent = open ? 'expand_less' : 'expand_more';
        // 展开时同步最新完整思考内容（思考增量是 DOM 级追加的，期间可能落后于 state）
        if (open) {
          var pre = box.querySelector('.tm-thinking-content');
          if (pre) {
            pre.textContent = (teachMateState.getSnapshot().thinkingContent || '');
            pre.scrollTop = pre.scrollHeight;
          }
        }
        try { localStorage.setItem('teachmate:thinking-open', open ? '1' : '0'); } catch (e) {}
        return true;
      }
      // U5: 首次设置与数据就绪
      if (act === 'tm-setup-test') { tmRunConnectionTest(); return true; }
      if (act === 'tm-data-retry') { tmRunDataReadiness(); return true; }
      if (act === 'tm-import-classes') { tmJumpToImport(); return true; }
      if (act === 'tm-open-exams') { tmJumpToExams(); return true; }
      // U5: 报告导出与教师确认
      if (act === 'tm-export-print') { tmExportPrint(t.dataset.runId); return true; }
      if (act === 'tm-export-doc') { tmExportDocument(t.dataset.runId, t.dataset.format || 'pdf', t); return true; }
      if (act === 'tm-export-json') { tmExportJSON(t.dataset.runId); return true; }
      if (act === 'tm-report-confirm') { tmOpenReportConfirm(t.dataset.runId); return true; }
      if (act === 'tm-copy-diagnostics') { tmCopyDiagnostics(); return true; }
      if (act === 'tm-copy-summary') { tmCopySummary(t.dataset.copyText || ''); return true; }
      if (act === 'tm-copy-dialog-close') { closeModal(); return true; }
      if (act === 'tm-evaluation-save') { tmSaveEvaluation(false); return true; }
      if (act === 'tm-evaluation-confirm') { tmSaveEvaluation(true); return true; }
      if (act === 'tm-evaluation-view') { tmViewEvaluationAudit(t.dataset.evaluationId); return true; }
      if (act === 'tm-profile-confirm') { tmConfirmProfileRevision(t.dataset.revisionId); return true; }
      if (act === 'tm-profile-reject') { tmRejectProfileRevision(t.dataset.revisionId); return true; }
      return false;
    }

    /** 切换右栏抽屉（窄屏用）。打开时联动遮罩层，点击遮罩/Esc 均可关闭。 */
    function tmToggleRightPanel() {
      var panel = document.getElementById('tmRightPanel');
      if (!panel) return;
      var open = panel.classList.toggle('tm-drawer-open');
      document.body.classList.toggle('tm-drawer-veil', open);
      var btn = document.querySelector('[data-act="tm-toggle-right-panel"]');
      if (btn) btn.setAttribute('aria-expanded', open ? 'true' : 'false');
      if (open) {
        // 窄屏下锁住页面滚动，避免背景滚动
        document.body.style.overflow = 'hidden';
        var closeBtn = panel.querySelector('.tm-right-close');
        if (closeBtn) closeBtn.focus();
      } else {
        document.body.style.overflow = '';
      }
    }

    /** 关闭右栏抽屉（遮罩点击 / Esc 调用） */
    function tmCloseRightPanel() {
      var panel = document.getElementById('tmRightPanel');
      if (!panel || !panel.classList.contains('tm-drawer-open')) return;
      panel.classList.remove('tm-drawer-open');
      document.body.classList.remove('tm-drawer-veil');
      document.body.style.overflow = '';
      var btn = document.querySelector('[data-act="tm-toggle-right-panel"]');
      if (btn) btn.setAttribute('aria-expanded', 'false');
    }

    /** 切换成果区中的报告 / 证据 / 数据质量，不触发整页重绘。 */
    function tmSelectRightPanelTab(tabName) {
      var panel = document.getElementById('tmRightPanel');
      if (!panel) return;
      panel.querySelectorAll('[data-act="tm-right-tab"][data-tab]').forEach(function (button) {
        var selected = button.dataset.tab === tabName;
        button.classList.toggle('is-active', selected);
        button.setAttribute('aria-selected', selected ? 'true' : 'false');
      });
      panel.querySelectorAll('[data-right-tab-panel]').forEach(function (content) {
        content.hidden = content.dataset.rightTabPanel !== tabName;
      });
    }

    /** 展开被折叠的更早消息（临时移除限制） */
    function tmLoadMoreMessages() {
      var snapshot = teachMateState.getSnapshot();
      if (snapshot.messages && snapshot.messages.length > 100) {
        teachMateState._renderAllMessages = true;
        render();
      }
    }

    // ================= U5: 首次设置与数据就绪 =================

    /** 连接测试：读取 provider 配置判断可达性。 */
    async function tmRunConnectionTest() {
      if (typeof teachMateOnboarding === 'undefined') return;
      try {
        var result = await teachMateOnboarding.runConnectionTest();
        var btn = document.querySelector('[data-act="tm-setup-test"]');
        if (btn) btn.disabled = true;
        if (result.ok) {
          var info = result.info;
          var detail = (info.display_name || info.provider || '') + ' · ' + (info.model_name || '');
          detail += info.api_key_configured ? ' · API Key 已配置' : ' · 缺少 API Key';
          showToast('后端连接正常：' + detail);
          teachMateState.setProviderInfo(info);
        } else {
          showToast('后端连接失败：' + (result.error || '未知错误'));
        }
      } catch (e) {
        showToast('连接测试失败：' + (e.message || e));
      } finally {
        var btn2 = document.querySelector('[data-act="tm-setup-test"]');
        if (btn2) setTimeout(function () { btn2.disabled = false; }, 3000);
      }
    }

    /** 重新执行数据就绪检查。 */
    async function tmRunDataReadiness() {
      if (typeof teachMateOnboarding === 'undefined') return;
      try {
        var r = await teachMateOnboarding.checkDataReadiness();
        teachMateState.setDataReady(r);
        render();
      } catch (e) {
        showToast('数据就绪检查失败：' + (e.message || e));
      }
    }

    /** 跳转到导入资料入口。 */
    function tmJumpToImport() {
      var btn = document.getElementById('importBtn');
      if (btn) { btn.click(); return; }
      showToast('请在右上角点击“导入数据”上传名单或成绩');
    }

    /** 跳转到成绩面板选择考试。 */
    function tmJumpToExams() {
      if (typeof curModule !== 'undefined' && typeof animateTabSwitch === 'function') {
        curModule = 'score';
        animateTabSwitch('workbench');
        return;
      }
      var navItem = document.querySelector('[data-act="nav"][data-key="score"]');
      if (navItem) { navItem.click(); return; }
      showToast('请在成绩面板选择或导入考试');
    }

    // ================= U-5: 报告导出与教师确认 =================

    /** 从消息中提取 run 的 structured_answer。 */
    function _findReportForRun(runId) {
      var snapshot = teachMateState.getSnapshot();
      runId = runId ? Number(runId) : snapshot.currentRunId;
      var msgs = snapshot.messages || [];
      for (var i = msgs.length - 1; i >= 0; i--) {
        if (msgs[i].role === 'assistant' && msgs[i].structured_answer) {
          var a = msgs[i].structured_answer;
          if (typeof a === 'string') { try { a = JSON.parse(a); } catch (e) { a = null; } }
          if (a && typeof a === 'object') {
            if (!runId || String(msgs[i].run_id) === String(runId)) {
              return teachMateReport.applyMaterialEdits(a, msgs[i].run_id);
            }
          }
        }
      }
      return null;
    }

    /** 导出打印版。 */
    async function tmExportPrint(runId) {
      var answer = _findReportForRun(runId);
      if (!answer) { showToast('当前没有可导出的结构化报告'); return; }
      runId = runId ? Number(runId) : teachMateState.currentRunId;
      var evidence = [];
      if (runId) {
        try { evidence = await teachMateApi.getRunEvidence(runId); } catch (e) { /* 无证据也可导出 */ }
      }
      if (typeof teachMateReport !== 'undefined') {
        teachMateReport.exportForPrint({ answer: answer, evidence: evidence, runId: runId });
      }
    }

    /** 导出结构化 JSON。 */
    async function tmExportJSON(runId) {
      var answer = _findReportForRun(runId);
      if (!answer) { showToast('没有可导出的结构化报告'); return; }
      runId = runId ? Number(runId) : teachMateState.currentRunId;
      var evidence = [];
      if (runId) {
        try { evidence = await teachMateApi.getRunEvidence(runId); } catch (e) { /* ignore */ }
      }
      if (typeof teachMateReport !== 'undefined') {
        teachMateReport.exportJSON({ answer: answer, evidence: evidence, runId: runId });
      } else {
        showToast('导出组件未加载');
      }
    }

    /**
     * L4-D：导出为 PDF 或 DOCX 文档。
     * 直接把屏幕上已确认的结构化答案作为 inline_content 传给后端，所见即所得，
     * 不依赖后端按 session 重新取数；后端只负责排版落盘。
     * @param {string|number} runId
     * @param {string} format - 'pdf' | 'docx'
     * @param {HTMLElement} [btn] - 触发按钮，用于 loading 态
     */
    async function tmExportDocument(runId, format, btn) {
      format = (format === 'docx') ? 'docx' : 'pdf';
      var answer = _findReportForRun(runId);
      if (!answer) { showToast('没有可导出的结构化报告'); return; }
      runId = runId ? Number(runId) : teachMateState.currentRunId;
      if (btn && btn.dataset.exportVariant === 'student-handout') {
        var handoutSections = (Array.isArray(answer.sections) ? answer.sections : []).filter(function (section) {
          return section && section.kind === 'student_handout';
        });
        if (!handoutSections.length) { showToast('这份教学包没有可单独导出的学生练习单'); return; }
        // 学生副本只携带学生练习分节，避免教师答案、风险提示和班级分析进入发放文件。
        answer = {
          answer_type: 'student_handout',
          summary: '',
          findings: [],
          recommendations: [],
          limitations: [],
          timeline: '',
          sections: handoutSections,
        };
      }

      // loading 态：禁用按钮并显示进度文案
      var prevHtml = null;
      var prevDisabled = false;
      if (btn) {
        prevHtml = btn.innerHTML;
        prevDisabled = btn.disabled;
        btn.disabled = true;
        btn.innerHTML = '<span class="material-symbols-rounded tm-spin" aria-hidden="true">hourglass_empty</span>生成中…';
      }
      var resetBtn = function () {
        if (btn) { btn.disabled = prevDisabled; btn.innerHTML = prevHtml; }
      };
      try {
        var title = (btn && btn.dataset.exportVariant === 'student-handout'
          ? '学生练习单'
          : (answer.title || answer.summary || 'TeachMate 教学包')).slice(0, 120);
        var spec = {
          format: format,
          template: 'report',
          title: title,
          locale: 'zh-CN',
          source: { session_ids: [], attachment_ids: [] },
          provider: { type: 'local', remote_enabled: false },
          options: {},
          inline_content: answer,
        };
        var res = await teachMateApi.exportDocument(spec);
        var artifact = res && res.artifact;
        if (!artifact) { showToast('导出任务未返回产物，请重试'); resetBtn(); return; }
        var blob = await teachMateApi.downloadDocumentArtifact(artifact.id);
        var filename = artifact.filename || ('teachmate-report.' + (format === 'docx' ? 'docx' : 'pdf'));
        var url = URL.createObjectURL(blob);
        var a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        setTimeout(function () { URL.revokeObjectURL(url); }, 4000);
        showToast(res.reused ? '已复用上次生成的文档' : ('已导出 ' + (format === 'docx' ? 'DOCX' : 'PDF')));
        resetBtn();
      } catch (err) {
        resetBtn();
        var msg = '导出失败';
        if (err && err.status === 503) msg = '文档导出功能未启用';
        else if (err && err.status === 409) msg = '远程文档服务未配置，已禁用';
        else if (err && err.status === 501) msg = '该导出方式尚未实现';
        else if (err && err.detail && err.detail.message) msg = err.detail.message;
        else if (err && err.message) msg = err.message;
        showToast(msg);
      }
    }

    /** 打开教师确认面板（弹层）。 */
    async function tmOpenReportConfirm(runId) {
      var snapshot = teachMateState.getSnapshot();
      runId = runId ? Number(runId) : snapshot.currentRunId;
      var msgs = snapshot.messages || [];
      var msg = null;
      for (var i = msgs.length - 1; i >= 0; i--) {
        if (msgs[i].role === 'assistant' && msgs[i].structured_answer && (!runId || String(msgs[i].run_id) === String(runId))) {
          msg = msgs[i]; break;
        }
      }
      if (!msg) { showToast('没有可确认的报告'); return; }

      // 确认弹窗展示自然语言；结构化内容仅作为后台存储和审计依据。
      // 传入 structured_answer 可避免把 JSON 原文直接展示给教师。
      var aiOriginal = msg.structured_answer || msg.content_text || msg.content || '';
      // 尝试生成教师可修改的评价文稿（找到该 run 的现有评价）
      var evals = (typeof teachMateState.getEvaluationsForRun === 'function')
        ? teachMateState.getEvaluationsForRun(Number(runId)) : [];
      var evaluation = evals.length ? evals[0] : null;

      var ctx = {
        runId: runId,
        aiOriginal: aiOriginal,
        teacherText: evaluation ? (evaluation.teacher_confirmed_text || evaluation.ai_original_text || aiOriginal) : aiOriginal,
        status: evaluation ? (evaluation.status || 'draft') : 'draft',
        evaluationId: evaluation ? evaluation.id : null,
      };
      var html = (typeof teachMateReport !== 'undefined')
        ? teachMateReport.renderConfirmPanel(ctx)
        : '<p class="tm-evidence-empty">教师确认组件未加载</p>';
      if (typeof openModal === 'function') openModal('教师确认', html);
      var content = document.querySelector('#modal .modal-content');
      if (content) content.classList.add('tm-teacher-confirm-modal');
      // 聚焦编辑区
      setTimeout(function () {
        var ta = document.getElementById('tmTeacherText');
        if (ta) ta.focus();
      }, 50);
    }

    /** 确认/拒绝 AI 提出的学生画像变更。画像接口本身带版本校验，避免覆盖教师后来修改。 */
    async function tmConfirmProfileRevision(revisionId) {
      if (!revisionId || typeof teachMateApi === 'undefined' || !teachMateApi.confirmProfileRevision) return;
      try {
        await teachMateApi.confirmProfileRevision(Number(revisionId));
        if (typeof closeModal === 'function') closeModal();
        showToast('学生画像已确认并写入', 'success');
        render();
      } catch (e) {
        showToast('画像写入失败：' + (e.message || e), 'error');
      }
    }

    async function tmRejectProfileRevision(revisionId) {
      if (!revisionId || typeof teachMateApi === 'undefined' || !teachMateApi.rejectProfileRevision) return;
      try {
        await teachMateApi.rejectProfileRevision(Number(revisionId), '教师拒绝');
        if (typeof closeModal === 'function') closeModal();
        showToast('已拒绝这条画像变更', 'success');
        render();
      } catch (e) {
        showToast('拒绝画像变更失败：' + (e.message || e), 'error');
      }
    }

    /** 保存（或确认）教师评价。 */
    async function tmSaveEvaluation(confirm) {
      var snapshot = teachMateState.getSnapshot();
      var runId = snapshot.currentRunId;
      var run = snapshot.currentRun || {};
      var teacherTextEl = document.getElementById('tmTeacherText');
      var aiTextEl = document.getElementById('tmAiOriginalText');
      if (!teacherTextEl) { showToast('未找到教师修改文本'); return; }
      var teacherText = teacherTextEl.value.trim();
      var aiOriginal = aiTextEl ? aiTextEl.value.trim() : '';

      // 需要 student_id 上下文
      var session = (snapshot.sessions || []).find(function (s) { return s.id === snapshot.currentSessionId; });
      var studentId = session && session.student_id;
      if (!studentId) {
        showToast('当前会话未绑定学生，无法写入教师评价。请在学生列表选中一名学生后新建对话。');
        return;
      }
      var payload = {
        student_id: studentId,
        term_id: session.term_id,
        exam_id: session.exam_id,
        ai_original_text: aiOriginal,
        analysis_run_id: runId,
        evidence_snapshot: snapshot.currentEvidence || {},
      };

      try {
        var evals = teachMateState.getEvaluationsForRun(runId);
        var evaluationId = evals.length ? evals[0].id : null;
        var saved;
        if (evaluationId) {
          saved = await teachMateApi.updateEvaluation(evaluationId, { teacher_confirmed_text: teacherText });
        } else {
          saved = await teachMateApi.createEvaluation(payload);
        }
        if (confirm) {
          saved = await teachMateApi.confirmEvaluation(saved.id);
        }
        teachMateState.setEvaluations(runId, [saved]);
        if (typeof closeModal === 'function') closeModal();
        showToast(confirm ? '评价已确认并保存' : '评价草稿已保存');
        render();
      } catch (e) {
        showToast('保存评价失败：' + (e.message || e));
      }
    }

    /** 查看评价审计记录。 */
    async function tmViewEvaluationAudit(evaluationId) {
      if (!evaluationId) return;
      try {
        var audit = await teachMateApi.getEvaluationAudit(evaluationId);
        if (!audit || !audit.length) { showToast('暂无审计记录'); return; }
        var html = '<div class="tm-audit-modal">';
        audit.forEach(function (entry) {
          html += '<div class="tm-audit-row"><span class="tm-audit-action">' + escapeHtml(entry.action || '') + '</span>' +
            '<span class="tm-audit-actor">' + escapeHtml(entry.actor || '') + '</span>' +
            '<span class="tm-audit-time">' + escapeHtml(_formatSessionTime(entry.created_at)) + '</span></div>';
        });
        html += '</div>';
        if (typeof openModal === 'function') openModal('评价审计记录', html);
      } catch (e) {
        showToast('读取审计记录失败：' + (e.message || e));
      }
    }

    /** 复制诊断信息（run_id / 状态 / 错误 / 最近事件）。 */
    function tmCopyDiagnostics() {
      var snapshot = teachMateState.getSnapshot();
      var lines = [
        'TeachMate 诊断信息',
        '时间: ' + new Date().toISOString(),
        '会话: ' + (snapshot.currentSessionId || '-'),
        '运行: ' + (snapshot.currentRunId || '-'),
        '状态: ' + (snapshot.runState || '-'),
      ];
      if (snapshot.currentRun && snapshot.currentRun.id) {
        lines.push('运行ID: ' + snapshot.currentRun.id);
        if (snapshot.currentRun.error_message) lines.push('错误: ' + snapshot.currentRun.error_message);
      }
      if (snapshot.error) lines.push('错误: ' + snapshot.error);
      if (snapshot.timeline && snapshot.timeline.length) {
        lines.push('时间线:');
        snapshot.timeline.slice(-8).forEach(function (it) {
          lines.push('  - ' + (it.text || it.label || it.kind || '') + (it.detail ? ': ' + it.detail : ''));
        });
      }
      var text = lines.join('\n');
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(function () { showToast('诊断信息已复制'); }).catch(function () { _fallbackCopy(text); });
      } else {
        _fallbackCopy(text);
      }
    }

    function tmCopySummary(text) {
      var value = String(text || '').trim();
      if (!value) { showToast('这份报告没有可复制的结论', 'warning'); return; }
      var fallback = function () {
        openModal('复制结论',
          '<p style="margin:0 0 10px;font-size:14px;color:var(--tm-text-secondary);">当前环境无法自动复制，请手动选择结论。</p>' +
          '<textarea readonly style="width:100%;min-height:160px;padding:10px 12px;border:1px solid var(--tm-border);border-radius:10px;font:inherit;line-height:1.6;resize:vertical;box-sizing:border-box;">' + escapeHtml(value) + '</textarea>',
          '<button class="btn btn-primary" data-act="tm-copy-dialog-close">关闭</button>');
      };
      if (!navigator.clipboard || typeof navigator.clipboard.writeText !== 'function') { fallback(); return; }
      navigator.clipboard.writeText(value).then(function () {
        showToast('报告结论已复制', 'success');
      }).catch(fallback);
    }

    function _fallbackCopy(text) {
      try { navigator.clipboard.writeText(text).then(function () { showToast('诊断信息已复制'); }).catch(function () { _showManualCopyDialog(text); }); }
      catch (e) { _showManualCopyDialog(text); }
    }

    /** 剪贴板不可用时展示内联复制弹窗 */
    function _showManualCopyDialog(text) {
      openModal('复制诊断信息',
        '<p style="margin:0 0 10px;font-size:14px;color:var(--tm-text-secondary);">当前环境无法自动复制，请手动选择以下内容复制：</p>' +
        '<textarea readonly style="width:100%;min-height:160px;padding:10px 12px;border:1px solid var(--tm-border);border-radius:10px;font:12px/1.5 monospace;resize:vertical;box-sizing:border-box;">' + escapeHtml(String(text || '')) + '</textarea>',
        '<button class="btn btn-primary" data-act="tm-copy-dialog-close">关闭</button>');
    }

    /** 针对单个证据打开检查器。 */
    async function tmViewEvidenceItem(evidenceId, runId) {
      if (typeof teachMateEvidence === 'undefined') { tmViewEvidence(runId); return; }
      try {
        await teachMateEvidence.openEvidenceById(runId, evidenceId);
      } catch (e) {
        showToast('打开证据失败：' + (e.message || e));
      }
    }

    // ================= U5: 附件上传（U4 管线前台入口） =================

    async function _waitForAttachmentParse(attachmentId, jobId) {
      var deadline = Date.now() + 45000;
      while (Date.now() < deadline) {
        var job = await teachMateApi.getJob(jobId);
        if (job.status === 'completed') {
          var result = await teachMateApi.getAttachmentParseResult(attachmentId);
          if (result.error || result.status === 'parse_failed') throw new Error(result.error || '附件解析失败');
          if (!String(result.content || '').trim()) throw new Error('附件没有可供模型读取的文字内容');
          return result;
        }
        if (job.status === 'failed' || job.status === 'cancelled' || job.status === 'waiting_ocr') {
          if (job.status === 'waiting_ocr') {
            var reason = '图片或扫描页需要文字识别，当前资料尚未完成处理';
            try {
              var parsed = await teachMateApi.getAttachmentParseResult(attachmentId);
              reason = (parsed.metadata && parsed.metadata.ocr_unavailable_reason) || reason;
            } catch (ignored) {}
            var pending = new Error(reason);
            pending.code = 'PENDING_OCR';
            throw pending;
          }
          throw new Error(job.last_error || '附件解析失败');
        }
        await new Promise(function (resolve) { setTimeout(resolve, 500); });
      }
      return { status: 'processing' };
    }

    async function _resumeAttachmentParse(attachmentId, jobId, originalName) {
      if (!teachMateState.getPendingAttachments().some(function (item) {
        return Number(item.attachmentId) === attachmentId;
      })) return;
      try {
        var result = await _waitForAttachmentParse(attachmentId, jobId);
        if (result.status === 'processing') {
          setTimeout(function () { _resumeAttachmentParse(attachmentId, jobId, originalName); }, 5000);
          return;
        }
        teachMateState.updatePendingAttachment(attachmentId, { status: 'ready', error: '' });
        render();
        showToast('资料已解析，可随消息发送：' + originalName);
      } catch (error) {
        var pendingOcr = error && error.code === 'PENDING_OCR';
        teachMateState.updatePendingAttachment(attachmentId, {
          status: pendingOcr ? 'pending_ocr' : 'error',
          error: error.message || String(error),
        });
        render();
        showToast((pendingOcr ? '资料待文字识别：' : '资料不可用：') + originalName);
      }
    }

    async function _prepareAttachment(att) {
      var attachmentId = Number(att.id || att.attachmentId);
      var originalName = att.original_name || att.originalName || att.title || ('附件 #' + attachmentId);
      teachMateState.addPendingAttachment({
        attachmentId: attachmentId,
        title: att.title || originalName,
        originalName: originalName,
        status: 'parsing',
      });
      render();
      try {
        var parse = await teachMateApi.parseAttachment(attachmentId, 'chat_context');
        var result = await _waitForAttachmentParse(attachmentId, parse.job_id);
        if (result.status === 'processing') {
          setTimeout(function () { _resumeAttachmentParse(attachmentId, parse.job_id, originalName); }, 5000);
          return;
        }
        teachMateState.updatePendingAttachment(attachmentId, { status: 'ready', error: '' });
        render();
        showToast('资料已解析，可随消息发送：' + originalName);
      } catch (error) {
        if (error && error.code === 'PENDING_OCR') {
          teachMateState.updatePendingAttachment(attachmentId, { status: 'pending_ocr', error: error.message });
          render();
          showToast('资料待文字识别：' + originalName);
          return;
        }
        teachMateState.updatePendingAttachment(attachmentId, { status: 'error', error: error.message || String(error) });
        render();
        showToast('资料不可用：' + (error.message || error));
      }
    }

    // ================= L1: 多部件流式上传 =================
    // 前端从 /capabilities 读取限制与开关，不再硬编码 15MB；当 multipart_ui_enabled
    // 打开时改用 FormData + XHR（带进度/可取消），关闭时回退 Base64 兼容入口。

    let _tmUploadCaps = null;          // 能力缓存（首次请求后复用）
    const _tmUploadControllers = Object.create(null);  // tempId -> AbortController

    /** 读取上传能力（AttachmentUploadAPI v1）；失败返回安全默认。 */
    async function _tmGetUploadCapabilities() {
      if (_tmUploadCaps) return _tmUploadCaps;
      try {
        var caps = await apiRequest('/api/v1/attachments/capabilities');
        _tmUploadCaps = caps || null;
      } catch (error) {
        _tmUploadCaps = null;
      }
      if (!_tmUploadCaps) {
        // 后端不可达时的保守默认：关闭多部件，回退 Base64，限制 50MB。
        _tmUploadCaps = {
          api_version: '1.0',
          multipart_enabled: false,
          max_attachment_bytes: 50 * 1024 * 1024,
          allowed_extensions: ['.pdf', '.xlsx', '.csv', '.txt', '.md', '.docx', '.png', '.jpg', '.jpeg'],
          accept_attribute: '.pdf,.xlsx,.csv,.txt,.md,.docx,.png,.jpg,.jpeg,.zip',
        };
      }
      return _tmUploadCaps;
    }

    function _tmTempUploadId() {
      return -(Date.now() + Math.floor(Math.random() * 100000));
    }

    /** 选择并上传附件：按能力声明选择多部件或 Base64 路径。 */
    function tmAttachFile() {
      if (typeof teachMateState === 'undefined' || typeof teachMateApi === 'undefined') return;
      var input = document.createElement('input');
      input.type = 'file';
      input.style.display = 'none';
      // ZIP 由同一个入口接收，但会在 change 中转入插件安装确认流程。
      input.accept = '.pdf,.xlsx,.csv,.txt,.md,.docx,.png,.jpg,.jpeg,.zip,application/zip';
      input.addEventListener('change', function () {
        var file = input.files && input.files[0];
        input.remove();
        if (file) _tmHandleAttachFile(file);
      });
      input.addEventListener('cancel', function () { input.remove(); });
      document.body.appendChild(input);
      input.click();
    }

    /** 统一处理拖入文件；ZIP 进入插件安装确认，其余文件进入资料附件流程。 */
    function _tmHandleDroppedFiles(files) {
      var list = Array.prototype.slice.call(files || []).filter(function (file) { return file && file.name; });
      if (!list.length) return;
      var zipFiles = list.filter(function (file) { return /\.zip$/i.test(file.name); });
      var regularFiles = list.filter(function (file) { return !/\.zip$/i.test(file.name); });
      if (zipFiles.length) {
        if (zipFiles.length > 1 || regularFiles.length) showToast('一次只安装一个 ZIP 插件包，其他文件请分别导入。');
        tmOpenPluginInstallModal(zipFiles[0]);
        return;
      }
      // 普通资料沿用现有上传校验；一次拖入多个文件时逐个提交。
      regularFiles.forEach(function (file) { _tmHandleAttachFile(file); });
    }

    /** 处理已选文件：校验 → 多部件上传 / Base64 回退 → 解析。 */
    async function _tmHandleAttachFile(file) {
      var caps;
      try {
        caps = await _tmGetUploadCapabilities();
      } catch (error) {
        caps = null;
      }
      var allowed = (caps && caps.allowed_extensions) ||
        ['.pdf', '.xlsx', '.csv', '.txt', '.md', '.docx', '.png', '.jpg', '.jpeg'];
      var maxBytes = (caps && caps.max_attachment_bytes) || (50 * 1024 * 1024);
      var acceptAttr = (caps && caps.accept_attribute) || allowed.join(',');
      var originalName = file.name || 'upload.bin';
      var ext = (originalName.indexOf('.') >= 0)
        ? ('.' + originalName.split('.').pop().toLowerCase())
        : '';

      // ZIP 不是教学附件格式，必须先经过插件安装确认，不读取、不解析其正文。
      if (ext === '.zip') {
        tmOpenPluginInstallModal(file);
        return;
      }

      if (!ext || allowed.indexOf(ext) < 0) {
        showToast('不支持的文件类型' + (ext ? '（' + ext + '）' : '') + '；仅支持：' + acceptAttr);
        return;
      }
      if (file.size > maxBytes) {
        showToast('文件超过 ' + _tmFormatBytes(maxBytes) + ' 上限，请压缩后重试');
        return;
      }

      var useMultipart = !!(caps && caps.multipart_enabled);
      if (useMultipart) {
        await _tmUploadViaMultipart(file, originalName, ext, maxBytes);
      } else {
        await _tmUploadViaBase64(file, originalName);
      }
    }

    /** L1 多部件上传（XHR + FormData），带进度与取消。 */
    async function _tmUploadViaMultipart(file, originalName, ext, maxBytes) {
      var tempId = _tmTempUploadId();
      var controller = new AbortController();
      _tmUploadControllers[tempId] = controller;
      teachMateState.addPendingAttachment({
        attachmentId: tempId,
        title: originalName.replace(/\.[^.]+$/, ''),
        originalName: originalName,
        status: 'uploading',
        progress: 0,
      });
      render();
      try {
          var att = await teachMateApi.uploadAttachment(file, {
            termId: currentTermId,
            title: originalName.replace(/\.[^.]+$/, ''),
            source: 'teachmate-ui',
            onProgress: function (pct) {
            teachMateState.updatePendingAttachment(tempId, { status: 'uploading', progress: pct });
            render();
          },
          signal: controller.signal,
        });
        delete _tmUploadControllers[tempId];
        teachMateState.removePendingAttachment(tempId);
        _tmOnUploadSuccess(att);
      } catch (error) {
        delete _tmUploadControllers[tempId];
        teachMateState.removePendingAttachment(tempId);
        if (error && error.code === 'UPLOAD_CANCELLED') {
          showToast('已取消上传：' + originalName);
          return;
        }
        // 透明降级：多部件失败时回退 Base64（功能等价），避免卡死。
        if (error && (error.code === 'NETWORK_ERROR' || error.status >= 500)) {
          showToast('多部件上传失败，正在回退兼容模式…');
          try {
            var fallback = await _tmUploadViaBase64(file, originalName);
            return;
          } catch (fbError) {
            error = fbError || error;
          }
        }
        showToast('附件上传失败：' + ((error && error.message) || error));
      }
    }

    /** Base64 兼容上传（多部件关闭或回退时使用），与历史行为一致。 */
    async function _tmUploadViaBase64(file, originalName) {
      var mimeType = file.type || 'application/octet-stream';
      var title = originalName.replace(/\.[^.]+$/, '');
      var base64 = await new Promise(function (resolve, reject) {
        var reader = new FileReader();
        reader.onload = function () { resolve(String(reader.result).split(',')[1] || ''); };
        reader.onerror = function () { reject(new Error('读取文件失败')); };
        reader.readAsDataURL(file);
      });
      // 占位临时态，便于在解析前展示；真实 id 由服务器返回。
      var tempId = _tmTempUploadId();
      teachMateState.addPendingAttachment({
        attachmentId: tempId,
        title: title,
        originalName: originalName,
        status: 'uploading',
        progress: 100,
      });
      render();
      try {
        var att = await teachMateApi.createAttachment({
          title: title,
          original_name: originalName,
          mime_type: mimeType,
          content_base64: base64,
          metadata: { source: 'teachmate-ui' },
        }, currentTermId);
        teachMateState.removePendingAttachment(tempId);
        _tmOnUploadSuccess(att);
        return att;
      } catch (error) {
        teachMateState.removePendingAttachment(tempId);
        throw error;
      }
    }

    /** 上传成功后的统一处理：幂等复用 or 触发解析。 */
    function _tmOnUploadSuccess(att) {
      var realId = Number(att.id || att.attachmentId);
      var existing = teachMateState.getPendingAttachments().find(function (x) {
        return Number(x.attachmentId) === realId;
      });
      if (existing) {
        // 内容去重不能把等待 OCR 或失败的附件直接标成可发送。
        if (existing.status === 'pending_ocr' || existing.status === 'error') {
          teachMateState.updatePendingAttachment(realId, { status: 'parsing', error: '' });
          _prepareAttachment(att);
          return;
        }
        showToast('该附件已添加，已复用：' + (att.original_name || att.title || ('附件 #' + realId)));
        render();
        return;
      }
      _prepareAttachment(att);
    }

    /** 移除待发送附件；若正在上传则一并取消。 */
    function tmRemovePendingAttachment(attachmentId) {
      var id = Number(attachmentId);
      var controller = _tmUploadControllers[id];
      if (controller) {
        try { controller.abort(); } catch (abortError) {}
        delete _tmUploadControllers[id];
      }
      teachMateState.removePendingAttachment(id);
    }


    function _tmFileTypeIcon(name) {
      var ext = String(name || '').split('.').pop().toLowerCase();
      if (ext === 'pdf') return 'picture_as_pdf';
      if (ext === 'docx' || ext === 'doc') return 'article';
      if (ext === 'xlsx' || ext === 'xls' || ext === 'csv') return 'table';
      if (ext === 'txt' || ext === 'md') return 'text_snippet';
      return 'insert_drive_file';
    }
    function _tmFileTypeLabel(name) {
      var ext = String(name || '').split('.').pop().toLowerCase();
      if (ext === 'pdf') return 'PDF';
      if (ext === 'docx' || ext === 'doc') return 'Word';
      if (ext === 'xlsx' || ext === 'xls') return 'Excel';
      if (ext === 'csv') return 'CSV';
      if (ext === 'txt' || ext === 'md') return '文本';
      return '文件';
    }
    function _tmFormatBytes(bytes) {
      var n = Number(bytes) || 0;
      if (n < 1024) return n + ' B';
      if (n < 1048576) return (n / 1024).toFixed(1).replace(/\.0$/, '') + ' KB';
      if (n < 1073741824) return (n / 1048576).toFixed(1).replace(/\.0$/, '') + ' MB';
      return (n / 1073741824).toFixed(1).replace(/\.0$/, '') + ' GB';
    }
    function _tmParsedStatus(parsed) {
      var status = (parsed && parsed.status) || '';
      if (status === 'confirmed') return { text: '已确认', className: 'confirmed' };
      if (status === 'pending_review') return { text: '待确认', className: 'pending' };
      return { text: '未解析', className: 'unparsed' };
    }
    function _tmBindLibraryModalEvents() {
      var selectAll = document.getElementById('tmLibrarySelectAll');
      var search = document.getElementById('tmLibrarySearch');
      var list = document.querySelector('.tm-library-list');
      if (selectAll) {
        selectAll.addEventListener('change', function () {
          var checked = !!selectAll.checked;
          document.querySelectorAll('.tm-library-row:not(.hidden) [data-library-attachment]').forEach(function (input) {
            input.checked = checked;
          });
          _tmUpdateLibrarySelection();
        });
      }
      if (search) {
        search.addEventListener('input', function () {
          var kw = (search.value || '').trim().toLowerCase();
          document.querySelectorAll('.tm-library-row').forEach(function (row) {
            var key = (row.getAttribute('data-library-keyword') || '').toLowerCase();
            row.classList.toggle('hidden', kw.length > 0 && key.indexOf(kw) < 0);
          });
          _tmSyncLibrarySelectAll();
        });
      }
      if (list) {
        list.addEventListener('change', function (e) {
          if (e.target && e.target.matches('[data-library-attachment]')) {
            _tmUpdateLibrarySelection();
          }
        });
      }
    }
    function _tmUpdateLibrarySelection() {
      var checked = document.querySelectorAll('[data-library-attachment]:checked').length;
      var countEl = document.getElementById('tmLibrarySelectedCount');
      if (countEl) countEl.textContent = String(checked);
      _tmSyncLibrarySelectAll();
      var confirmBtn = document.querySelector('[data-act="tm-library-confirm"]');
      if (confirmBtn) confirmBtn.disabled = checked === 0;
    }
    function _tmSyncLibrarySelectAll() {
      var selectAll = document.getElementById('tmLibrarySelectAll');
      if (!selectAll) return;
      var visibleInputs = document.querySelectorAll('.tm-library-row:not(.hidden) [data-library-attachment]');
      var allChecked = visibleInputs.length > 0 && Array.from(visibleInputs).every(function (input) { return input.checked; });
      selectAll.checked = allChecked;
    }

    /** 从 WorkBench 当前学期资料库选择 PDF/DOCX 等已有资料。 */
    async function tmOpenLibrary() {
      try {
        var attachments = await teachMateApi.listAttachments(currentTermId);
        var supported = (attachments || []).filter(function (item) {
          // TeachMate 从外部上传的文件只属于当前对话附件，不进入 WorkBench
          // 资料库选择器；只有明确标记为 paper_documents 的资料可被此处复用。
          var source = item && item.metadata && item.metadata.source;
          return source === 'paper_documents' && /\.(pdf|docx|xlsx|csv|txt|md)$/i.test(item.original_name || '');
        });
        var selectedIds = new Set(teachMateState.getPendingAttachments().map(function (item) { return Number(item.attachmentId); }));
        var total = supported.length;
        var html = '<div class="tm-library-modal">';
        if (!total) {
          html += '<div class="tm-library-empty"><span class="material-symbols-rounded">folder_open</span><p>当前学期资料库中没有可读取的 PDF、DOCX、Excel 或文本资料。</p></div>';
        } else {
          html += '<div class="tm-library-toolbar">' +
            '<label class="tm-library-select-all">' +
              '<input type="checkbox" id="tmLibrarySelectAll" data-act="tm-library-select-all">' +
              '<span class="tm-library-check"></span><span>全选</span>' +
            '</label>' +
            '<div class="tm-library-search">' +
              '<span class="material-symbols-rounded">search</span>' +
              '<input type="text" id="tmLibrarySearch" data-act="tm-library-search" placeholder="搜索文件名...">' +
            '</div>' +
            '<span class="tm-library-count">已选 <strong id="tmLibrarySelectedCount">0</strong> / <strong>' + total + '</strong></span>' +
          '</div>' +
          '<div class="tm-library-list">';
          supported.forEach(function (item) {
            var parsed = (item.metadata && item.metadata.parsed) || {};
            var status = _tmParsedStatus(parsed);
            var checked = selectedIds.has(Number(item.id));
            var keyword = escapeHtml((item.original_name || item.title || '').toLowerCase());
            html += '<label class="tm-library-row" data-library-keyword="' + keyword + '">' +
              '<input type="checkbox" data-library-attachment="' + escapeAttr(String(item.id)) + '"' + (checked ? ' checked' : '') + '>' +
              '<span class="tm-library-check"></span>' +
              '<span class="tm-library-icon material-symbols-rounded" aria-hidden="true" data-ext="' + escapeAttr(String(item.original_name || '').split('.').pop().toLowerCase()) + '">' + _tmFileTypeIcon(item.original_name) + '</span>' +
              '<span class="tm-library-info">' +
                '<span class="tm-library-name" title="' + escapeAttr(item.original_name || item.title || ('附件 #' + item.id)) + '">' + escapeHtml(item.original_name || item.title || ('附件 #' + item.id)) + '</span>' +
                '<span class="tm-library-meta">' +
                  '<span class="tm-library-type">' + _tmFileTypeLabel(item.original_name) + '</span>' +
                  '<span class="tm-library-size">' + _tmFormatBytes(item.size_bytes) + '</span>' +
                  '<span class="tm-library-status ' + status.className + '">' + status.text + '</span>' +
                '</span>' +
              '</span>' +
            '</label>';
          });
          html += '</div>';
        }
        html += '</div>';
        openModal('选择 WorkBench 资料库文件', html,
          '<button class="btn btn-text" data-act="tm-library-cancel">取消</button>' +
          '<button class="btn btn-primary" data-act="tm-library-confirm">解析并添加</button>');
        document.querySelector('#modal .modal-content')?.classList.add('tm-library-modal-content');
        _tmBindLibraryModalEvents();
        _tmUpdateLibrarySelection();
      } catch (error) {
        showToast('读取 WorkBench 资料库失败：' + (error.message || error));
      }
    }

    async function tmConfirmLibrarySelection() {
      var selected = Array.from(document.querySelectorAll('[data-library-attachment]:checked')).map(function (input) {
        return Number(input.getAttribute('data-library-attachment'));
      });
      var attachments;
      try {
        attachments = await teachMateApi.listAttachments(currentTermId);
      } catch (error) {
        showToast('读取资料库失败：' + (error.message || error));
        return;
      }
      closeModal();
      var byId = new Map((attachments || []).map(function (item) { return [Number(item.id), item]; }));
      selected.forEach(function (attachmentId) {
        var item = byId.get(attachmentId);
        if (item) _prepareAttachment(item);
      });
    }

    /** 从待发送列表移除附件。 */
    function tmRemovePendingAttachment(attachmentId) {
      teachMateState.removePendingAttachment(Number(attachmentId));
      render();
    }

    /** 确认取消对话框 */
    function tmConfirmCancel() {
      var overlay = document.createElement('div');
      overlay.className = 'tm-confirm-overlay';
      overlay.innerHTML =
        '<div class="tm-confirm-dialog">' +
        '<h3 class="tm-confirm-title">取消运行？</h3>' +
        '<p class="tm-confirm-body">取消后当前分析将停止，已生成的部分结果不会保留。</p>' +
        '<div class="tm-confirm-actions">' +
        '<button class="tm-confirm-btn" data-act="tm-cancel-abort">不取消</button>' +
        '<button class="tm-confirm-btn tm-confirm-danger" data-act="tm-cancel-confirm">确认取消</button>' +
        '</div></div>';
      document.body.appendChild(overlay);
      overlay.addEventListener('click', function (e) {
        var btn = e.target.closest('[data-act]');
        if (!btn) return;
        if (btn.dataset.act === 'tm-cancel-confirm') {
          overlay.remove();
          tmCancelRun();
        } else if (btn.dataset.act === 'tm-cancel-abort') {
          overlay.remove();
        }
      });
      // 点击遮罩关闭
      overlay.addEventListener('click', function (e) {
        if (e.target === overlay) overlay.remove();
      });
    }

    /** 教学伙伴输入框 keydown 处理 */
    function handleTeachMateKeydown(e) {
      if (e.key === 'Enter' && !e.shiftKey && e.target.dataset && e.target.dataset.act === 'tm-input') {
        e.preventDefault();
        tmSendMessage();
        return true;
      }
      // Esc 关闭右栏抽屉（窄屏）
      if (e.key === 'Escape') {
        var panel = document.getElementById('tmRightPanel');
        if (panel && panel.classList.contains('tm-drawer-open')) {
          tmCloseRightPanel();
          var toggleBtn = document.querySelector('[data-act="tm-toggle-right-panel"]');
          if (toggleBtn) toggleBtn.focus();
          return true;
        }
        // 桌面宽屏（panel 非抽屉模式）时恢复焦点到切换按钮
        var toggleBtn2 = document.querySelector('[data-act="tm-toggle-right-panel"]');
        if (toggleBtn2 && document.body.classList.contains('tm-drawer-veil')) {
          tmCloseRightPanel();
          toggleBtn2.focus();
          return true;
        }
        // Esc 关闭确认对话框
        var overlay = document.querySelector('.tm-confirm-overlay');
        if (overlay) {
          overlay.remove();
          return true;
        }
      }
      return false;
    }
