// ================= TeachMate 可用性与无障碍 =================
    // 职责：
    //   1. 焦点管理（弹层打开聚焦、关闭归还、Trap）
    //   2. aria-live 播报运行状态变化（不逐 Token 刷屏）
    //   3. 键盘导航辅助（Shift+Tab 循环、Enter 触发卡片）
    //   4. prefers-reduced-motion 辅助类

    const teachMateA11y = (function () {
      var _lastFocused = null;
      var _announcer = null;

      function _ensureAnnouncer() {
        if (_announcer && document.body.contains(_announcer)) return _announcer;
        _announcer = document.createElement('div');
        _announcer.setAttribute('aria-live', 'polite');
        _announcer.setAttribute('role', 'status');
        _announcer.setAttribute('class', 'sr-only');
        document.body.appendChild(_announcer);
        return _announcer;
      }

      /** 播报一句状态（aria-live）。 */
      function announce(message) {
        if (typeof document === 'undefined') return;
        var el = _ensureAnnouncer();
        el.textContent = '';
        // 延迟清空再写入，确保同一句重复播报
        setTimeout(function () {
          el.textContent = message;
        }, 20);
      }

      /** 记录当前焦点元素。 */
      function rememberFocus() {
        if (typeof document === 'undefined') return;
        var el = document.activeElement;
        if (el && el !== document.body) _lastFocused = el;
      }

      /** 恢复焦点到之前记录的触发元素。 */
      function restoreFocus() {
        if (typeof document === 'undefined') return;
        if (_lastFocused && document.body.contains(_lastFocused)) {
          try { _lastFocused.focus(); } catch (e) { /* ignore */ }
        }
      }

      /** 将焦点放入容器内第一个可聚焦元素。 */
      function focusFirst(container) {
        if (!container || typeof document === 'undefined') return;
        var el = container.querySelector('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])');
        if (el) { try { el.focus(); } catch (e) { /* ignore */ } }
      }

      /**
       * 为弹层建立焦点 Trap：Tab / Shift+Tab 在容器内循环。
       * @param {HTMLElement} container
       * @param {boolean} trap
       */
      function trapFocus(container, trap) {
        if (!container || typeof document === 'undefined') return;
        if (trap) {
          container.setAttribute('data-tm-focus-trap', 'true');
          container.addEventListener('keydown', _trapKeydown);
        } else {
          container.removeAttribute('data-tm-focus-trap');
          container.removeEventListener('keydown', _trapKeydown);
        }
      }

      function _trapKeydown(e) {
        if (e.key !== 'Tab') return;
        var container = e.currentTarget;
        var focusables = container.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])');
        if (!focusables.length) { e.preventDefault(); return; }
        var first = focusables[0];
        var last = focusables[focusables.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }

      /**
       * 运行状态播报（订阅 teachMateState 状态变化，仅播报关键转换）。
       * 不逐 Token 刷屏：只播报 submitting/queued/running/终态/错误。
       */
      function watchRunStatus(snapshot) {
        if (!snapshot) return;
        var labels = {
          submitting: '正在发送请求',
          queued: '任务已排队',
          running: '正在分析',
          waiting_confirmation: '等待预算确认',
          completed: '分析完成',
          failed: '分析失败',
          cancelled: '分析已取消',
          degraded: '分析完成，部分功能降级',
          idle: '',
        };
        var label = labels[snapshot.runState] || '';
        if (label) announce(label);
      }

      /** 订阅状态变化（幂等）。 */
      function subscribeStatus() {
        if (typeof teachMateState === 'undefined' || !teachMateState.subscribe) return;
        if (subscribeStatus._subscribed) return;
        subscribeStatus._subscribed = true;
        teachMateState.subscribe(function (snap) {
          watchRunStatus(snap);
        });
      }

      /** reduced-motion 检测辅助类（CSS 已有，这里供 JS 动画控制）。 */
      function prefersReducedMotion() {
        if (typeof window === 'undefined' || !window.matchMedia) return false;
        return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      }

      /**
       * 包装全局 openModal/closeModal，实现弹层焦点接管与归还（幂等，每文件一次）。
       * 不侵入原实现逻辑，仅在打开后聚焦首个可聚焦元素、关闭后归还焦点。
       */
      function wrapModalFocus() {
        if (typeof window === 'undefined') return;
        if (wrapModalFocus._done) return;
        wrapModalFocus._done = true;
        var w = window;

        var origOpen = w.openModal;
        if (typeof origOpen === 'function' && !origOpen.__tmA11y) {
          w.openModal = function () {
            rememberFocus();
            var result = origOpen.apply(w, arguments);
            setTimeout(function () {
              var modal = document.getElementById('modal');
              if (modal && modal.classList.contains('show')) focusFirst(modal);
            }, 20);
            return result;
          };
          w.openModal.__tmA11y = true;
        }

        var origClose = w.closeModal;
        if (typeof origClose === 'function' && !origClose.__tmA11y) {
          w.closeModal = function () {
            var result = origClose.apply(w, arguments);
            restoreFocus();
            return result;
          };
          w.closeModal.__tmA11y = true;
        }
      }

      return {
        announce: announce,
        rememberFocus: rememberFocus,
        restoreFocus: restoreFocus,
        focusFirst: focusFirst,
        trapFocus: trapFocus,
        watchRunStatus: watchRunStatus,
        subscribeStatus: subscribeStatus,
        prefersReducedMotion: prefersReducedMotion,
        wrapModalFocus: wrapModalFocus,
      };
    })();

    if (typeof window !== 'undefined') window.teachMateA11y = teachMateA11y;