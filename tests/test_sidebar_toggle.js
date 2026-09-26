const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const { loadWorkbenchHtml } = require('./helpers/workbench_html');
const html = loadWorkbenchHtml();
const dom = new JSDOM(html, { runScripts: 'dangerously', url: 'https://localhost/' });
const { window } = dom;
const { document } = window;
const results = [];
const check = (name, value) => results.push({ name, pass: Boolean(value) });

setTimeout(() => {
  const toggle = document.getElementById('sidebarToggle');
  check('桌面端默认显示左侧菜单', !document.body.classList.contains('sidebar-collapsed'));
  check('顶部存在双栏菜单开关', Boolean(toggle) && toggle.querySelector('.material-symbols-rounded')?.textContent.trim() === 'view_sidebar');
  check('不再使用箭头把手', !document.querySelector('.sidebar-toggle') && !toggle.textContent.includes('chevron'));

  toggle.click();
  check('点击后隐藏左侧菜单', document.body.classList.contains('sidebar-collapsed'));
  check('收起状态被保存', window.localStorage.getItem('workbench_sidebar_collapsed') === '1');
  check('收起后按钮提示可展开', toggle.getAttribute('aria-label') === '展开左侧菜单' && toggle.getAttribute('aria-expanded') === 'false');

  Object.defineProperty(window, 'innerWidth', { value: 700, writable: true, configurable: true });
  window.dispatchEvent(new window.Event('resize'));
  check('窄屏不受桌面收起状态影响', !document.body.classList.contains('sidebar-collapsed'));

  window.innerWidth = 1024;
  window.dispatchEvent(new window.Event('resize'));
  check('回到桌面恢复上次收起状态', document.body.classList.contains('sidebar-collapsed'));

  toggle.click();
  check('再次点击恢复左侧菜单', !document.body.classList.contains('sidebar-collapsed'));
  check('展开状态被保存', window.localStorage.getItem('workbench_sidebar_collapsed') === '0');

  const pass = results.every(item => item.pass);
  console.log(JSON.stringify(results, null, 2));
  console.log('OVERALL:', pass ? 'PASS' : 'FAIL');
  dom.window.close();
  process.exit(pass ? 0 : 1);
}, 500);
