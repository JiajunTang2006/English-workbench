const assert = require('node:assert/strict');
const test = require('node:test');
const { JSDOM } = require('jsdom');
const { loadWorkbenchHtml } = require('./helpers/workbench_html');

test('设置页导出读取已保存的学期数据并触发下载', async () => {
  const requests = [];
  const downloads = [];
  const response = payload => ({ ok: true, status: 200, json: async () => payload });
  const initial = { schema: 4, classes: ['711'], students: [{ id: '1', name: '旧姓名', class: '711' }] };
  const saved = { schema: 4, classes: ['711'], students: [{ id: '1', name: '新姓名', class: '711' }] };
  let workspaceReads = 0;
  const dom = new JSDOM(loadWorkbenchHtml(), {
    runScripts: 'dangerously',
    url: 'http://127.0.0.1:8765/workbench?token=test-token',
    beforeParse(window) {
      window.URL.createObjectURL = () => 'blob:test-export';
      window.URL.revokeObjectURL = () => {};
      window.HTMLAnchorElement.prototype.click = function () {
        downloads.push({ href: this.href, name: this.download, connected: this.isConnected });
      };
      window.fetch = async url => {
        const path = String(url);
        requests.push(path);
        if (path === '/api/v1/terms') return response([{ id: 1, code: '2026-S1', name: '当前学期', status: 'active' }]);
        if (path === '/api/v1/terms/current') return response({ id: 1, code: '2026-S1', name: '当前学期' });
        if (path === '/api/v1/runtime') return response({ version: 'test' });
        if (path === '/api/v1/terms/1/workspace-state') {
          workspaceReads += 1;
          return response({ state: workspaceReads === 1 ? initial : saved, revision: 1 });
        }
        if (path.startsWith('/api/v1/attachments')) return response([]);
        return response({});
      };
    },
  });
  const { window } = dom;
  await new Promise(resolve => setTimeout(resolve, 70));
  window.document.querySelector('[data-key="settings"]').click();
  window.document.querySelector('#workarea [data-act="export-json"]').click();
  await new Promise(resolve => setTimeout(resolve, 30));
  assert.equal(workspaceReads, 2);
  assert.equal(downloads.length, 1);
  assert.equal(downloads[0].connected, true);
  assert.match(downloads[0].name, /^workbench_term_2026-S1_\d{4}-\d{2}-\d{2}\.json$/);
  assert.ok(requests.includes('/api/v1/terms/1/workspace-state'));
  dom.window.close();
});
