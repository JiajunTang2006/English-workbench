// 原卷资料库与错题页码关联回归测试
const fs = require('fs');
const path = require('path');
const { JSDOM, ResourceLoader } = require('jsdom');

const { loadWorkbenchHtml } = require('./helpers/workbench_html');
const { createWorkbenchFixture } = require('./fixtures/workbench_fixture');
const html = loadWorkbenchHtml();
const fixture = createWorkbenchFixture();
fixture.paperDocuments = [
  {
    id: 'paper-pdf', name: '入学考试.pdf', extension: 'PDF', type: 'application/pdf', size: 120,
    content: 'data:application/pdf;base64,JVBERi0xLjQK', createdAt: '2026-08-08T10:00:00.000Z'
  },
  {
    id: 'paper-image', name: '错题截图.png', extension: 'PNG', type: 'image/png', size: 80,
    content: 'data:image/png;base64,iVBORw0KGgo=', createdAt: '2026-08-08T10:01:00.000Z'
  },
  {
    id: 'paper-word', name: '试卷文字.docx', extension: 'DOCX', type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', size: 180,
    content: 'data:application/vnd.openxmlformats-officedocument.wordprocessingml.document;base64,UEsDBA==',
    previewText: '阅读理解第3题：根据上下文选择正确答案。', createdAt: '2026-08-08T10:02:00.000Z'
  }
];
fixture.errors = [{ id: 'err-1', qnum: '3', type: '阅读理解', point: '主旨大意', count: 6, documentId: 'paper-pdf', page: 3, reason: '未抓住文章中心', key: '' }];

class LocalAssetLoader extends ResourceLoader {
  fetch(url) {
    const pathname = new URL(url).pathname;
    if (pathname.startsWith('/workbench-assets/')) {
      return Promise.resolve(fs.readFileSync(path.join(__dirname, '..', 'workbench-assets', path.basename(pathname))));
    }
    return null;
  }
}

const errors = [];
const dom = new JSDOM(html, {
  runScripts: 'dangerously', resources: new LocalAssetLoader(), url: 'https://localhost/',
  beforeParse(window) { window.localStorage.setItem('hye_db_v1', JSON.stringify(fixture)); }
});
const window = dom.window;
const doc = window.document;
window.onerror = (msg, url, line) => errors.push({ msg, line });

setTimeout(() => {
  const results = [];
  const check = (name, condition) => results.push({ name, pass: !!condition });
  const errorsNav = [...doc.querySelectorAll('.nav-item')].find(item => item.dataset.key === 'errors');
  errorsNav.click();

  check('原卷页包含资料库布局', !!doc.querySelector('.error-workspace'));
  check('原卷上传支持PDF、Word和图片', doc.querySelector('#error-doc-file')?.accept.includes('.pdf') && doc.querySelector('#error-doc-file')?.accept.includes('.docx') && doc.querySelector('#error-doc-file')?.accept.includes('.png'));
  check('PDF资料默认显示预览和下载', !!doc.querySelector('.error-document-preview iframe') && !!doc.querySelector('a[download="入学考试.pdf"]'));
  check('错题显示原卷页码和来源', doc.body.textContent.includes('第3页') && doc.body.textContent.includes('入学考试.pdf'));
  check('新增错题弹窗提供原卷关联选项', !!doc.querySelector('[data-act="error-add"]'));
  doc.querySelector('[data-act="error-add"]').click();
  check('错题录入含原卷和页码字段', !!doc.querySelector('#m-err-document') && !!doc.querySelector('#m-err-page') && doc.querySelector('#m-err-document').options.length === 4);
  doc.querySelector('#modalFooter .btn').click();

  const imageDoc = [...doc.querySelectorAll('[data-act="error-doc-select"]')].find(item => item.dataset.id === 'paper-image');
  imageDoc.click();
  check('图片资料可本地预览', !!doc.querySelector('.error-document-preview img'));
  const wordDoc = [...doc.querySelectorAll('[data-act="error-doc-select"]')].find(item => item.dataset.id === 'paper-word');
  wordDoc.click();
  check('DOCX资料可显示本地文字预览', doc.querySelector('.error-document-text')?.textContent.includes('阅读理解第3题'));
  check('全程无脚本错误', errors.length === 0);
  if (errors.length) console.log('Script errors:', JSON.stringify(errors));
  console.log(JSON.stringify(results, null, 2));
  const allPass = results.every(item => item.pass);
  console.log('OVERALL:', allPass ? 'PASS' : 'FAIL');
  dom.window.close();
  process.exit(allPass ? 0 : 1);
}, 700);
