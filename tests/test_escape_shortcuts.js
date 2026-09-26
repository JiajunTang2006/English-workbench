const test = require('node:test');
const assert = require('node:assert/strict');
const { JSDOM } = require('jsdom');
const { loadWorkbenchHtml } = require('./helpers/workbench_html');
const { createWorkbenchFixture } = require('./fixtures/workbench_fixture');

const fixture = createWorkbenchFixture();
const wait = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));

test('Esc 按优先级关闭界面状态且不会误保存编辑内容', async () => {
  const dom = new JSDOM(loadWorkbenchHtml(), {
    runScripts: 'dangerously',
    url: 'https://localhost/',
    beforeParse(window) {
      window.localStorage.setItem('hye_db_v1', JSON.stringify(fixture));
    },
  });
  const { document } = dom.window;
  dom.window.HTMLElement.prototype.scrollIntoView = function () {};
  await wait(700);

  const goto = key => [...document.querySelectorAll('.nav-item')].find(item => item.dataset.key === key).click();
  const escape = (target = document, options = {}) => target.dispatchEvent(new dom.window.KeyboardEvent('keydown', {
    key: 'Escape',
    bubbles: true,
    cancelable: true,
    ...options,
  }));
  const input = (target, value) => {
    target.value = value;
    target.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
  };

  goto('stu');
  document.querySelector('[data-act="stu-add"]').click();
  const nameInput = document.getElementById('m-stu-name');
  input(nameInput, '尚未保存的姓名');
  let confirmCalls = 0;
  dom.window.confirm = () => { confirmCalls += 1; return false; };
  escape(nameInput);
  assert.equal(document.getElementById('modal').classList.contains('show'), true, '取消放弃后应保留弹窗');
  assert.equal(confirmCalls, 1, '修改过的表单应询问是否放弃');
  dom.window.confirm = () => true;
  escape(nameInput);
  assert.equal(document.getElementById('modal').classList.contains('show'), false, '确认放弃后应关闭弹窗');

  const rowMenu = document.querySelector('details.row-actions');
  rowMenu.open = true;
  rowMenu.querySelector('summary').focus();
  escape(rowMenu.querySelector('summary'));
  assert.equal(rowMenu.open, false, 'Esc 应先关闭行操作菜单');

  goto('dictation');
  const scoreCell = document.querySelector('[data-act="dict-edit"]');
  const originalText = scoreCell.textContent;
  const originalStoredScore = JSON.parse(dom.window.localStorage.getItem('hye_db_v1')).dictation[scoreCell.dataset.sid][Number(scoreCell.dataset.r) - 1];
  scoreCell.focus();
  scoreCell.textContent = '999';
  escape(scoreCell);
  assert.equal(scoreCell.textContent, originalText, 'Esc 应恢复进入编辑前的内容');
  assert.equal(JSON.parse(dom.window.localStorage.getItem('hye_db_v1')).dictation[scoreCell.dataset.sid][Number(scoreCell.dataset.r) - 1], originalStoredScore, '取消编辑不应写入数据');

  const batchCases = [
    ['stu', 'stu-batch-toggle'],
    ['dictation', 'dict-batch-toggle'],
    ['recite', 'recite-batch-toggle'],
    ['writing', 'writing-batch-toggle'],
    ['homework', 'hw-batch-toggle'],
  ];
  for (const [moduleKey, action] of batchCases) {
    goto(moduleKey);
    document.querySelector(`[data-act="${action}"]`).click();
    assert.match(document.querySelector(`[data-act="${action}"]`).textContent, /退出批量管理|批量管理中/);
    escape(document);
    await wait(0);
    assert.equal(document.querySelector(`[data-act="${action}"]`).textContent.trim(), '批量管理', `${moduleKey} 应可用 Esc 退出批量管理`);
  }

  goto('stu');
  document.body.classList.add('navopen');
  escape(document);
  assert.equal(document.body.classList.contains('navopen'), false, 'Esc 应关闭移动端侧栏');

  const search = document.querySelector('[data-act="stu-search"]');
  input(search, '测试学生001');
  document.querySelector('[data-act="stu-batch-toggle"]').click();
  const activeSearch = document.querySelector('[data-act="stu-search"]');
  activeSearch.focus();
  escape(activeSearch);
  await wait(0);
  assert.equal(document.querySelector('[data-act="stu-search"]').value, '测试学生001', '批量模式优先于清空搜索');
  escape(document.querySelector('[data-act="stu-search"]'));
  await wait(0);
  assert.equal(document.querySelector('[data-act="stu-search"]').value, '', '第二次 Esc 才清空聚焦的搜索框');

  input(document.querySelector('[data-act="stu-search"]'), '测试');
  escape(document.querySelector('[data-act="stu-search"]'), { isComposing: true, keyCode: 229 });
  assert.equal(document.querySelector('[data-act="stu-search"]').value, '测试', '输入法组合阶段不响应 Esc 快捷键');

  goto('dash');
  dom.window.eval("riskDetailLevel = 'C'; curModule = 'risk'; renderNav(); render();");
  assert.ok(document.querySelector('.risk-detail-page'));
  escape(document);
  assert.ok(document.querySelector('.risk-attention-card'), '重点关注详情页应可用 Esc 返回仪表盘');

  dom.window.close();
});
