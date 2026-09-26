const test = require('node:test');
const assert = require('node:assert/strict');
const { JSDOM } = require('jsdom');
const { loadWorkbenchHtml } = require('./helpers/workbench_html');
const { createWorkbenchFixture } = require('./fixtures/workbench_fixture');

test('家长电话默认脱敏且只能逐行临时显示', async () => {
  const fixture = createWorkbenchFixture();
  const dom = new JSDOM(loadWorkbenchHtml(), {
    runScripts: 'dangerously',
    url: 'https://localhost/',
    beforeParse(window) { window.localStorage.setItem('hye_db_v1', JSON.stringify(fixture)); }
  });
  dom.window.HTMLElement.prototype.scrollIntoView = function () {};
  await new Promise(resolve => setTimeout(resolve, 700));
  const doc = dom.window.document;
  [...doc.querySelectorAll('.nav-item')].find(node => node.dataset.key === 'stu').click();

  const phones = fixture.students.slice(0, 2).map(student => student.phone);
  const values = [...doc.querySelectorAll('.phone-value')];
  assert.equal(values[0].textContent, '••••••••');
  assert.equal(values[1].textContent, '••••••••');
  assert.ok(!doc.getElementById('workarea').textContent.includes(phones[0]));

  const buttons = [...doc.querySelectorAll('[data-act="stu-toggle-phone"]')];
  buttons[0].click();
  assert.ok(doc.getElementById('workarea').textContent.includes(phones[0]));
  assert.ok(!doc.getElementById('workarea').textContent.includes(phones[1]));

  [...doc.querySelectorAll('[data-act="stu-toggle-phone"]')][1].click();
  assert.ok(!doc.getElementById('workarea').textContent.includes(phones[0]));
  assert.ok(doc.getElementById('workarea').textContent.includes(phones[1]));

  [...doc.querySelectorAll('.nav-item')].find(node => node.dataset.key === 'dash').click();
  [...doc.querySelectorAll('.nav-item')].find(node => node.dataset.key === 'stu').click();
  assert.ok(!doc.getElementById('workarea').textContent.includes(phones[0]));
  assert.ok(!doc.getElementById('workarea').textContent.includes(phones[1]));
  dom.window.close();
});
