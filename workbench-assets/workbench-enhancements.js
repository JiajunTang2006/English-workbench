// ================= WorkBench 前端增强脚本 =================
// 轻量 Markdown 渲染 · 骨架屏 · 键盘快捷键 · Toast 增强 · 消息复制
// 本文件作为增强层，不修改已有逻辑，通过事件委托和 DOM 观察实现。
// =============================================================

(function () {
  'use strict';

  // ---------- 1. 轻量 Markdown 渲染器 ----------
  // 安全地将纯文本转为基本 Markdown HTML
  function renderMarkdown(text) {
    if (!text) return '';
    // 先转义 HTML
    var html = String(text)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');

    // 代码块（```...```）
    html = html.replace(/```(\w*)\n?([\s\S]*?)```/g, function (_, lang, code) {
      return '<div class="md-codeblock">' + code.replace(/\n$/, '') + '</div>';
    });

    // Markdown 表格：表头下一行必须是分隔线（|---|---|），避免把普通含 | 文本误判为表格。
    // 在其它行级格式化之前转换，单元格内容仍会继续经过加粗/行内代码等处理。
    function splitTableRow(line) {
      var value = String(line || '').trim();
      if (value.indexOf('|') < 0) return null;
      if (value.charAt(0) === '|') value = value.slice(1);
      if (value.charAt(value.length - 1) === '|') value = value.slice(0, -1);
      var cells = value.split('|').map(function (cell) { return cell.trim(); });
      return cells.length >= 2 && cells.every(function (cell) { return cell.length > 0; }) ? cells : null;
    }
    function isTableDivider(cells) {
      return !!cells && cells.length >= 2 && cells.every(function (cell) {
        return /^:?-{3,}:?$/.test(cell);
      });
    }
    var mdLines = html.split('\n');
    var renderedLines = [];
    var lineIndex = 0;
    while (lineIndex < mdLines.length) {
      var headerCells = splitTableRow(mdLines[lineIndex]);
      var dividerCells = splitTableRow(mdLines[lineIndex + 1]);
      if (headerCells && isTableDivider(dividerCells)) {
        var tableHtml = '<div class="md-table-wrap"><table class="md-table"><thead><tr>' +
          headerCells.map(function (cell) { return '<th>' + cell + '</th>'; }).join('') +
          '</tr></thead><tbody>';
        lineIndex += 2;
        while (lineIndex < mdLines.length) {
          var rowCells = splitTableRow(mdLines[lineIndex]);
          if (!rowCells || isTableDivider(rowCells)) break;
          tableHtml += '<tr>' + rowCells.map(function (cell) { return '<td>' + cell + '</td>'; }).join('') + '</tr>';
          lineIndex++;
        }
        tableHtml += '</tbody></table></div>';
        renderedLines.push(tableHtml);
        continue;
      }
      renderedLines.push(mdLines[lineIndex]);
      lineIndex++;
    }
    html = renderedLines.join('\n');

    // 行内代码（`code`）
    html = html.replace(/`([^`\n]+)`/g, '<code class="md-code">$1</code>');

    // 标题（# ## ###）
    html = html.replace(/^### (.+)$/gm, '<div class="md-h3">$1</div>');
    html = html.replace(/^## (.+)$/gm, '<div class="md-h2">$1</div>');
    html = html.replace(/^# (.+)$/gm, '<div class="md-h1">$1</div>');

    // 水平分割线
    html = html.replace(/^---+$/gm, '<hr class="md-hr">');

    // 引用块
    html = html.replace(/^&gt; (.+)$/gm, '<blockquote class="md-blockquote">$1</blockquote>');

    // 加粗和斜体
    html = html.replace(/\*\*([^*]+)\*\*/g, '<strong class="md-strong">$1</strong>');
    html = html.replace(/(^|[^*])\*([^*]+)\*(?!\*)/g, '$1<em class="md-em">$2</em>');

    // 无序列表
    html = html.replace(/^(\s*)[-*] (.+)$/gm, function (match, indent, content) {
      return '<div class="md-li" data-indent="' + indent.length + '">• ' + content + '</div>';
    });
    // 合并连续列表项
    html = html.replace(/(<div class="md-li"[^>]*>.*?<\/div>\n?)+/g, function (group) {
      var items = group.match(/<div class="md-li"[^>]*>(.*?)<\/div>/g);
      if (!items) return group;
      var lis = items.map(function (item) {
        return item.replace(/<div class="md-li"[^>]*>(.*?)<\/div>/, '<li class="md-li">$1</li>');
      }).join('');
      return '<ul class="md-ul">' + lis + '</ul>';
    });

    // 有序列表
    html = html.replace(/^\d+\. (.+)$/gm, function (_, content) {
      return '<div class="md-oli">' + content + '</div>';
    });
    html = html.replace(/(<div class="md-oli">.*?<\/div>\n?)+/g, function (group) {
      var items = group.match(/<div class="md-oli">(.*?)<\/div>/g);
      if (!items) return group;
      var lis = items.map(function (item) {
        return item.replace(/<div class="md-oli">(.*?)<\/div>/, '<li class="md-li">$1</li>');
      }).join('');
      return '<ol class="md-ol">' + lis + '</ol>';
    });

    // 段落：将剩余的连续非空行包裹在 <p> 中
    html = html.split(/\n\n+/).map(function (block) {
      block = block.trim();
      if (!block) return '';
      // 跳过已包裹的块
      if (block.startsWith('<')) return block;
      return '<p class="md-p">' + block.replace(/\n/g, '<br>') + '</p>';
    }).join('\n');

    return html;
  }

  // 检测是否包含 Markdown 语法
  function hasMarkdown(text) {
    if (!text) return false;
    return /(^|\n)\s*(?:#{1,6}\s|[-*]\s|\d+[.)]\s|>\s)|```|`[^`\n]+`|\*\*[^*]+\*\*|\|[^\n|]+\|/.test(String(text));
  }

  // ---------- 2. 增强 TeachMate 消息渲染 ----------
  function enhanceTeachMateMessages() {
    // 为 AI 消息内容添加 Markdown 渲染
    var aiMessages = document.querySelectorAll('.tm-message-ai .tm-message-content');
    aiMessages.forEach(function (el) {
      if (el.dataset.mdEnhanced) return;
      var raw = el.textContent || '';
      if (!raw.trim()) return;
      // 如果已有结构化内容（tm-structured），跳过
      if (el.parentElement.querySelector('.tm-structured') || el.querySelector('.tm-plain-analysis')) {
        el.dataset.mdEnhanced = 'true';
        return;
      }
      if (hasMarkdown(raw)) {
        el.innerHTML = renderMarkdown(raw);
      }
      el.dataset.mdEnhanced = 'true';

      // 添加复制按钮
      if (!el.parentElement.querySelector('.tm-message-actions')) {
        var actions = document.createElement('div');
        actions.className = 'tm-message-actions';
        var copyBtn = document.createElement('button');
        copyBtn.className = 'tm-copy-btn';
        copyBtn.innerHTML = '<span class="material-symbols-rounded">content_copy</span>复制';
        copyBtn.addEventListener('click', function () {
          copyToClipboard(raw).then(function () {
            copyBtn.classList.add('copied');
            copyBtn.innerHTML = '<span class="material-symbols-rounded">check</span>已复制';
            setTimeout(function () {
              copyBtn.classList.remove('copied');
              copyBtn.innerHTML = '<span class="material-symbols-rounded">content_copy</span>复制';
            }, 2000);
          });
        });
        actions.appendChild(copyBtn);
        el.parentElement.appendChild(actions);
      }
    });
  }

  // ---------- 3. 复制到剪贴板 ----------
  function copyToClipboard(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text);
    }
    return new Promise(function (resolve, reject) {
      var textarea = document.createElement('textarea');
      textarea.value = text;
      textarea.style.position = 'fixed';
      textarea.style.opacity = '0';
      document.body.appendChild(textarea);
      textarea.select();
      try {
        document.execCommand('copy');
        document.body.removeChild(textarea);
        resolve();
      } catch (e) {
        document.body.removeChild(textarea);
        reject(e);
      }
    });
  }

  // ---------- 4. 骨架屏 ----------
  function createSkeleton(type, count) {
    count = count || 1;
    var html = '';
    for (var i = 0; i < count; i++) {
      switch (type) {
        case 'line':
          html += '<div class="wb-skeleton wb-skeleton-line"></div>';
          break;
        case 'line-sm':
          html += '<div class="wb-skeleton wb-skeleton-line sm"></div>';
          break;
        case 'block':
          html += '<div class="wb-skeleton wb-skeleton-block"></div>';
          break;
        case 'card':
          html += '<div class="wb-skeleton-card"><div class="wb-skeleton wb-skeleton-line lg"></div><div class="wb-skeleton wb-skeleton-line"></div><div class="wb-skeleton wb-skeleton-line"></div><div class="wb-skeleton wb-skeleton-line sm"></div></div>';
          break;
        case 'grid':
          html += '<div class="wb-skeleton-grid"><div class="wb-skeleton wb-skeleton-block"></div><div class="wb-skeleton wb-skeleton-block"></div><div class="wb-skeleton wb-skeleton-block"></div><div class="wb-skeleton wb-skeleton-block"></div></div>';
          break;
        case 'row':
          html += '<div class="wb-skeleton-row"><div class="wb-skeleton wb-skeleton-avatar"></div><div style="flex:1"><div class="wb-skeleton wb-skeleton-line"></div><div class="wb-skeleton wb-skeleton-line sm"></div></div></div>';
          break;
        case 'table':
          for (var r = 0; r < 5; r++) {
            html += '<div class="wb-skeleton-row"><div class="wb-skeleton wb-skeleton-avatar"></div><div style="flex:1"><div class="wb-skeleton wb-skeleton-line"></div></div><div class="wb-skeleton wb-skeleton-line sm" style="width:60px"></div></div>';
          }
          break;
        default:
          html += '<div class="wb-skeleton wb-skeleton-block"></div>';
      }
    }
    return html;
  }

  function showSkeleton(container, type, count) {
    if (!container) return;
    container.innerHTML = createSkeleton(type, count);
  }

  // ---------- 5. Toast 通知增强 ----------
  var toastIcons = {
    success: 'check_circle',
    error: 'error',
    warning: 'warning',
    info: 'info',
  };

  function enhanceToast(message, type) {
    var toast = document.getElementById('toast');
    if (!toast) return;
    type = type || 'info';
    toast.className = 'toast ' + type;
    var icon = toastIcons[type] || 'info';
    toast.innerHTML = '<span class="material-symbols-rounded toast-icon">' + icon + '</span><span>' + escapeHtmlToast(message) + '</span>';
    toast.classList.add('show');
    clearTimeout(toast._wbTimer);
    toast._wbTimer = setTimeout(function () {
      toast.classList.add('hide');
      setTimeout(function () {
        toast.classList.remove('show', 'hide');
        toast.innerHTML = '';
      }, 150);
    }, 3000);
  }

  function escapeHtmlToast(text) {
    var div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }

  // ---------- 6. 自动滚动到最新消息 ----------
  function scrollToLatestMessage() {
    var container = document.getElementById('tmMessages');
    if (!container) return;
    // MutationObserver 会在 TeachMate 每次状态轮询重绘后触发。只有教师
    // 原本就在底部时才跟随新消息；查看历史内容时必须保留当前滚动位置，
    // 否则每隔几秒的状态更新都会把视口强行弹回底部。
    var bottomGap = container.scrollHeight - container.scrollTop - container.clientHeight;
    if (bottomGap <= 24) container.scrollTop = container.scrollHeight;
  }

  function handleKeyboard(e) {
    // 忽略输入框内的快捷键（除了 Esc）
    var isInput = ['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName) || e.target.isContentEditable;

    if (e.key === 'Escape') return; // 已有逻辑处理

    var mod = e.metaKey || e.ctrlKey;
    if (!mod) return;

    // Ctrl/Cmd + B: 切换侧边栏
    if (e.key === 'b' || e.key === 'B') {
      e.preventDefault();
      var toggle = document.getElementById('sidebarToggle');
      if (toggle) toggle.click();
      return;
    }

    // Ctrl/Cmd + ,: 打开设置
    if (e.key === ',') {
      e.preventDefault();
      if (typeof curModule !== 'undefined' && typeof render === 'function') {
        curModule = 'settings';
        renderNav();
        render();
      }
      return;
    }

    // Ctrl/Cmd + 1~9: 切换模块
    if (typeof MODULES !== 'undefined' && typeof curModule !== 'undefined') {
      var num = parseInt(e.key, 10);
      if (num >= 1 && num <= MODULES.length) {
        e.preventDefault();
        curModule = MODULES[num - 1].key;
        if (typeof render === 'function') {
          renderNav();
          render();
        }
        return;
      }
    }
  }

  // ---------- 8. 导航项点击波纹效果 ----------
  function addRippleEffect() {
    document.addEventListener('click', function (e) {
      var navItem = e.target.closest('.nav-item');
      if (!navItem) return;
      var rect = navItem.getBoundingClientRect();
      var x = ((e.clientX - rect.left) / rect.width) * 100;
      var y = ((e.clientY - rect.top) / rect.height) * 100;
      navItem.style.setProperty('--ripple-x', x + '%');
      navItem.style.setProperty('--ripple-y', y + '%');
    });
  }

  // ---------- 9. MutationObserver: 自动增强新渲染的内容 ----------
  function setupObservers() {
    // 观察 workarea 的变化，自动增强 TeachMate 消息
    var workarea = document.getElementById('workarea');
    if (!workarea) return;

    var observer = new MutationObserver(function (mutations) {
      for (var i = 0; i < mutations.length; i++) {
        if (mutations[i].addedNodes.length > 0) {
          // 延迟执行，确保 DOM 完全渲染
          setTimeout(function () {
            enhanceTeachMateMessages();
            scrollToLatestMessage();
          }, 0);
          break;
        }
      }
    });
    observer.observe(workarea, { childList: true, subtree: true });
  }

  // ---------- 10. Toast 集成 ----------
  // 在 init 时 hook 全局 showToast，将其委托给增强版
  function hookShowToast() {
    // showToast 定义在 workbench-interactions.js 的 IIFE 作用域内，
    // 但被暴露为全局函数（因 script 直接在全局作用域执行）。
    // 我们用一个拦截器覆盖 window.showToast
    if (typeof window.showToast !== 'function') {
      // 如果 showToast 不是全局可访问的，尝试通过事件代理拦截
      // 方案 B：监听 toast 元素的 class 变化，增强其内容
      hookToastViaObserver();
      return;
    }
    var originalShowToast = window.showToast;
    window.showToast = function (msg, type) {
      // 先调用增强版
      var result = enhanceToast(msg, type);
      // 如果增强版成功处理了，就不调用原始版
      if (result) return;
      // 降级到原始版
      originalShowToast(msg, type);
    };
  }

  // 方案 B：通过 MutationObserver 监听 toast 元素，增强其内容
  function hookToastViaObserver() {
    var toast = document.getElementById('toast');
    if (!toast) return;
    var observer = new MutationObserver(function () {
      if (!toast.classList.contains('show')) return;
      var text = toast.textContent || '';
      if (!text.trim()) return;
      // 检测是否已增强（有图标）
      if (toast.querySelector('.toast-icon')) return;
      // 从 class 推断类型
      var type = 'info';
      if (toast.classList.contains('error')) type = 'error';
      else if (toast.classList.contains('success')) type = 'success';
      else if (toast.classList.contains('warning')) type = 'warning';
      // 重建内容
      var icon = toastIcons[type] || 'info';
      toast.innerHTML = '<span class="material-symbols-rounded toast-icon">' + icon + '</span><span>' + escapeHtmlToast(text) + '</span>';
      // 重置自动消失计时器
      clearTimeout(toast._wbTimer);
      toast._wbTimer = setTimeout(function () {
        toast.classList.add('hide');
        setTimeout(function () {
          toast.classList.remove('show', 'hide');
          toast.innerHTML = '';
        }, 150);
      }, 3000);
    });
    observer.observe(toast, { childList: true, characterData: true, attributes: true, attributeFilter: ['class'] });
  }

  // ---------- 12. 右键上下文菜单 ----------
  var contextMenu = null;

  function createContextMenu() {
    if (contextMenu) return contextMenu;
    contextMenu = document.createElement('div');
    contextMenu.className = 'wb-context-menu';
    contextMenu.style.display = 'none';
    document.body.appendChild(contextMenu);

    // 点击其他区域关闭菜单
    document.addEventListener('click', function (e) {
      if (!contextMenu.contains(e.target)) hideContextMenu();
    });
    // 滚动或 Esc 关闭
    document.addEventListener('scroll', hideContextMenu, true);
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') hideContextMenu();
    });

    return contextMenu;
  }

  function showContextMenu(x, y, items) {
    var menu = createContextMenu();
    var html = '';
    items.forEach(function (item) {
      if (item.separator) {
        html += '<div class="wb-ctx-separator"></div>';
      } else {
        var icon = item.icon ? '<span class="material-symbols-rounded">' + item.icon + '</span>' : '';
        var disabled = item.disabled ? ' disabled' : '';
        html += '<div class="wb-ctx-item' + disabled + '" data-ctx-action="' + (item.action || '') + '"' + disabled + '>' + icon + '<span>' + escapeHtmlToast(item.label) + '</span></div>';
      }
    });
    menu.innerHTML = html;

    // 绑定点击
    menu.querySelectorAll('.wb-ctx-item:not(.disabled)').forEach(function (el) {
      el.addEventListener('click', function () {
        var action = this.getAttribute('data-ctx-action');
        var item = items.find(function (i) { return i.action === action; });
        if (item && typeof item.onClick === 'function') {
          item.onClick();
        }
        hideContextMenu();
      });
    });

    // 定位（防止溢出）
    menu.style.display = 'block';
    menu.style.visibility = 'hidden';
    var rect = menu.getBoundingClientRect();
    var menuW = rect.width;
    var menuH = rect.height;
    var finalX = Math.min(x, window.innerWidth - menuW - 8);
    var finalY = Math.min(y, window.innerHeight - menuH - 8);
    menu.style.left = Math.max(8, finalX) + 'px';
    menu.style.top = Math.max(8, finalY) + 'px';
    menu.style.visibility = 'visible';
    menu.classList.add('wb-ctx-show');
  }

  function hideContextMenu() {
    if (!contextMenu) return;
    contextMenu.classList.remove('wb-ctx-show');
    contextMenu.style.display = 'none';
  }

  // 为表格行绑定右键菜单
  function setupTableContextMenu() {
    document.addEventListener('contextmenu', function (e) {
      var row = e.target.closest('tr');
      if (!row || !row.closest('table')) return;
      // 跳过表头
      if (row.tagName === 'TR' && row.parentElement.tagName === 'THEAD') return;
      // 跳过骨架行
      if (row.classList.contains('wb-skeleton-row')) return;

      e.preventDefault();

      // 收集单元格文本
      var cells = row.querySelectorAll('td');
      var cellTexts = [];
      cells.forEach(function (cell) {
        cellTexts.push((cell.textContent || '').trim());
      });
      var rowText = cellTexts.join('\t');

      // 获取右键所在的单元格
      var targetCell = e.target.closest('td');
      var cellText = targetCell ? (targetCell.textContent || '').trim() : '';

      var items = [
        { icon: 'content_copy', label: '复制当前单元格', action: 'copy-cell', onClick: function () { copyToClipboard(cellText).then(function () { enhanceToast('已复制单元格内容', 'success'); }); } },
        { icon: 'table_rows', label: '复制整行', action: 'copy-row', onClick: function () { copyToClipboard(rowText).then(function () { enhanceToast('已复制整行数据', 'success'); }); } },
        { separator: true },
        { icon: 'search', label: '搜索此内容', action: 'search', disabled: !cellText, onClick: function () { searchInWorkbench(cellText); } },
      ];

      showContextMenu(e.clientX, e.clientY, items);
    });
  }

  // 在全局搜索栏中搜索
  function searchInWorkbench(text) {
    if (!text) return;
    // 尝试找到全局搜索输入框
    var searchInput = document.querySelector('.toolbar input[type="search"], .toolbar input[type="text"]');
    if (searchInput) {
      searchInput.value = text;
      searchInput.dispatchEvent(new Event('input', { bubbles: true }));
      enhanceToast('已在当前模块搜索', 'info');
    } else {
      // 降级：复制到剪贴板
      copyToClipboard(text).then(function () {
        enhanceToast('搜索栏未找到，已复制到剪贴板', 'info');
      });
    }
  }

  // ---------- 初始化 ----------
  function init() {
    // 注册键盘快捷键
    document.addEventListener('keydown', handleKeyboard);

    // 注册导航波纹效果
    addRippleEffect();

    // 设置 MutationObserver
    setupObservers();

    // Hook showToast（延迟执行，确保 interactions.js 已加载）
    setTimeout(hookShowToast, 200);

    // 注册表格右键菜单
    setupTableContextMenu();

    console.log('[WBEnhancements] 前端增强层已加载');
  }

  // 等待 DOM 和其他脚本就绪
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () {
      setTimeout(init, 100);
    });
  } else {
    setTimeout(init, 100);
  }

  // ---------- 暴露 API ----------
  window.WBEnhancements = {
    renderMarkdown: renderMarkdown,
    hasMarkdown: hasMarkdown,
    copyToClipboard: copyToClipboard,
    enhanceTeachMateMessages: enhanceTeachMateMessages,
    createSkeleton: createSkeleton,
    showSkeleton: showSkeleton,
    enhanceToast: enhanceToast,
    scrollToLatestMessage: scrollToLatestMessage,
    showContextMenu: showContextMenu,
    hideContextMenu: hideContextMenu,
  };

})();
