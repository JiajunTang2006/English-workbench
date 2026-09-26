const fs = require('fs');
const path = require('path');
const { JSDOM, ResourceLoader } = require('jsdom');
const { loadWorkbenchHtml } = require('./helpers/workbench_html');
const html = loadWorkbenchHtml();

class LocalAssetLoader extends ResourceLoader {
  fetch(url) {
    const pathname = new URL(url).pathname;
    if (pathname.startsWith('/workbench-assets/')) {
      const filename = path.basename(pathname);
      return Promise.resolve(fs.readFileSync(path.join(__dirname, '..', 'workbench-assets', filename)));
    }
    return null;
  }
}

const errors = [];
const dom = new JSDOM(html, {
  runScripts: 'dangerously',
  resources: new LocalAssetLoader(),
  url: 'https://localhost/',
});
const window = dom.window;
window.onerror = (msg, url, line) => { errors.push({ msg, line }); };

setTimeout(() => {
  const doc = window.document;
  const navItems = doc.querySelectorAll('.nav-item');
  // 重型第三方库现在按需加载；普通导航不应在首屏初始化它们。
  let pass = !window.XLSX && !window.JSZip && !window.echarts;
  if (!pass) errors.push({ msg: '重型 Excel、ZIP 或图表资源被首屏加载' });
  navItems.forEach(item => {
    item.click();
    const workarea = doc.getElementById('workarea');
    if (!workarea || !workarea.innerHTML.trim()) {
      pass = false;
      errors.push({ msg: 'Empty workarea for ' + item.dataset.key });
    }
  });
  console.log('Navigation test:', pass ? 'PASS' : 'FAIL');
  console.log('Errors:', errors.length ? errors : 'none');
  try { console.log('LocalStorage key exists:', !!window.localStorage.getItem('hye_db_v1')); } catch(e){}
  dom.window.close();
  process.exit(pass && errors.length === 0 ? 0 : 1);
}, 500);
