const fs = require('fs');
const path = require('path');
const { JSDOM, ResourceLoader } = require('jsdom');
const test = require('node:test');
const assert = require('node:assert/strict');

const { loadWorkbenchHtml } = require('./helpers/workbench_html');
const html = loadWorkbenchHtml();
class LocalAssetLoader extends ResourceLoader {
  fetch(url) {
    const pathname = new URL(url).pathname;
    if (pathname.startsWith('/workbench-assets/')) return Promise.resolve(fs.readFileSync(path.join(__dirname, '..', 'workbench-assets', path.basename(pathname))));
    return null;
  }
}

test('班级语义别名支持手动归档合并', async () => {
  const fixture = {
    schema: 4, dataContract: 2, teacher: { name: '测试', subject: '英语' }, classes: ['101', '102'], classAliases: {},
    settings: { excellent: 90, pass: 60, criticalLow: 55 },
    students: [{ id: 's1', name: '甲', class: '102', english: 80, phone: '' }], exams: [], currentExamId: '',
    studentTags: [], recitations: [], writings: [], errors: [], critical: [], todos: [], alignments: [], customs: [], dictation: {}, dictationNames: ['自定义1']
  };
  const dom = new JSDOM(html, {
    runScripts: 'dangerously', resources: new LocalAssetLoader(), url: 'https://localhost/',
    beforeParse(window) { window.localStorage.setItem('hye_db_v1', JSON.stringify(fixture)); }
  });
  await new Promise(resolve => setTimeout(resolve, 550));
  [...dom.window.document.querySelectorAll('.nav-item')].find(node => node.dataset.key === 'settings').click();
  dom.window.document.getElementById('class-merge-source').value = '102';
  dom.window.document.getElementById('class-merge-target').value = '101';
  dom.window.document.querySelector('[data-act="class-merge"]').click();
  const saved = JSON.parse(dom.window.localStorage.getItem('hye_db_v1'));
  assert.equal(saved.classAliases['102'], '101');
  assert.equal(saved.students[0].class, '101');
  assert.deepEqual(saved.classes, ['101']);
  dom.window.close();
});
