const fs = require('fs');
const path = require('path');
const { JSDOM, ResourceLoader } = require('jsdom');

const { loadWorkbenchHtml } = require('./helpers/workbench_html');
const html = loadWorkbenchHtml({ includeThirdParty: true });
class LocalAssetLoader extends ResourceLoader {
  fetch(url) {
    const pathname = new URL(url).pathname;
    if (pathname.startsWith('/workbench-assets/')) return Promise.resolve(fs.readFileSync(path.join(__dirname, '..', 'workbench-assets', path.basename(pathname))));
    return null;
  }
}

const fixture = {
  schema: 4, dataContract: 2, teacher: { name: '', subject: '初中英语' }, classes: [],
  settings: { excellent: 90, pass: 60, criticalLow: 55 }, students: [], exams: [], currentExamId: '',
  recitations: [], writings: [], errors: [], critical: [], todos: [], alignments: [], customs: [], dictation: {}, dictationNames: ['自定义1', '自定义2'],
};
const dom = new JSDOM(html, {
  runScripts: 'dangerously', resources: new LocalAssetLoader(), url: 'https://localhost/',
  beforeParse(window) { window.localStorage.setItem('hye_db_v1', JSON.stringify(fixture)); },
});

setTimeout(() => {
  const workbook = { SheetNames: ['学科红绿表'], Sheets: { '学科红绿表': dom.window.XLSX.utils.aoa_to_sheet([
    ['红绿表说明'], ['班级', '学号', '姓名', '英语', '英语排名'],
    ['甲班', 'A001', '张三', 94, 12], ['甲班', 'A002', '李四', 80, 88], ['甲班', 'A003', '王五', '缺考', ''],
  ]) } };
  const result = dom.window.importFromWorkbooks([{ name: 'entrance.xlsx', workbook }]);
  const saved = JSON.parse(dom.window.localStorage.getItem('hye_db_v1'));
  const checks = [
    ['同一班级的语义写法自动归一', dom.window.normalizeClassName('七年级11班') === '711' && dom.window.normalizeClassName('711班') === '711'],
    ['班级标签不会重复追加“班”', dom.window.formatClassLabel('711') === '711班' && dom.window.formatClassLabel('模拟测试班') === '模拟测试班'],
    ['第二行表头被识别', result.imported === 3],
    ['英语排名列被识别', saved.exams[0].scores.A001.gradeRank === 12],
    ['缺考状态被保留', saved.exams[0].scores.A003.attendanceStatus === 'absent'],
    ['自动生成入学考试基线', saved.exams[0].examKind === 'entrance'],
    ['学生管理不再显示目标分/薄弱标签/座位', !dom.window.document.querySelector('th') || !dom.window.document.body.textContent.includes('目标分')],
  ];
  [...dom.window.document.querySelectorAll('.nav-item')].find(node => node.dataset.key === 'stu').click();
  const detailButton = dom.window.document.querySelector('[data-act="stu-detail"]');
  checks.push(['学生管理有查看明细', Boolean(detailButton)]);
  detailButton.click();
  const text = dom.window.document.getElementById('modalBody').textContent;
  checks.push(['明细包含英语考试历史', text.includes('英语考试历史')]);
  checks.push(['明细包含年级排名和班级排名', text.includes('年级排名') && text.includes('班级排名')]);
  checks.push(['明细包含层级列', text.includes('层级')]);
  checks.push(['学生画像保留左侧内容并新增右侧题型能力图', text.includes('学生画像') && text.includes('题型能力') && Boolean(dom.window.document.querySelector('.student-profile-layout'))]);
  checks.push(['题型能力图按七类题型预留数据容器', Boolean(dom.window.document.getElementById('student-ability-radar-A001'))]);
  checks.push(['明细包含四张横向对比图表', ['student-chart-score', 'student-chart-grade-rank', 'student-chart-class-rank', 'student-chart-tier'].every(id => Boolean(dom.window.document.getElementById(id)))]);
  dom.window.document.querySelector('[data-act="tag-add"]').click();
  checks.push(['新建评价弹窗正常显示', dom.window.document.getElementById('modal').classList.contains('show') && dom.window.document.getElementById('modalBody').textContent.includes('标签名称')]);
  dom.window.document.getElementById('m-tag-name').value = '需要关注';
  dom.window.document.getElementById('m-tag-save').click();
  const afterCreate = JSON.parse(dom.window.localStorage.getItem('hye_db_v1'));
  checks.push(['可以创建学生评价标签', afterCreate.studentTags.length === 1 && afterCreate.studentTags[0].name === '需要关注']);
  checks.push(['新建文字评价后自动归入当前学生', afterCreate.students[0].evaluationTags.includes(afterCreate.studentTags[0].id)]);
  const tagNameInput = dom.window.document.querySelector('[data-act="tag-name-change"]');
  tagNameInput.value = '需要重点关注';
  tagNameInput.dispatchEvent(new dom.window.Event('change', { bubbles: true }));
  const afterRename = JSON.parse(dom.window.localStorage.getItem('hye_db_v1'));
  checks.push(['创建后可以直接输入文字评价', afterRename.studentTags[0].name === '需要重点关注']);
  dom.window.document.querySelector('[data-act="tag-batch-delete"]').click();
  checks.push(['打开批量管理不会出现空白画布', dom.window.document.getElementById('modalBody').textContent.includes('勾选后批量删除')]);
  checks.push(['批量管理提供全选', Boolean(dom.window.document.querySelector('[data-act="tag-select-all"]'))]);
  dom.window.document.getElementById('modalClose').click();
  checks.push(['关闭批量管理返回学生明细', dom.window.document.getElementById('modalBody').textContent.includes('文字评价')]);
  detailButton.click();
  dom.window.document.querySelector('[data-act="tag-batch-delete"]').click();
  dom.window.document.querySelector('[data-act="tag-lock"]').click();
  const afterLock = JSON.parse(dom.window.localStorage.getItem('hye_db_v1'));
  checks.push(['可以锁定评价标签', afterLock.studentTags[0].locked === true]);
  dom.window.document.querySelector('[data-act="tag-select"]').checked = true;
  dom.window.document.querySelector('[data-act="tag-select"]').dispatchEvent(new dom.window.Event('change', { bubbles: true }));
  dom.window.document.querySelector('[data-act="tag-batch-confirm"]').click();
  const afterLockedDelete = JSON.parse(dom.window.localStorage.getItem('hye_db_v1'));
  checks.push(['锁定标签不会被批量删除', afterLockedDelete.studentTags.length === 1]);
  dom.window.document.getElementById('modalClose').click();
  detailButton.click();
  dom.window.document.querySelector('[data-act="tag-batch-delete"]').click();
  dom.window.document.querySelector('[data-act="tag-lock"]').click();
  dom.window.document.querySelector('[data-act="tag-select"]').checked = true;
  dom.window.document.querySelector('[data-act="tag-select"]').dispatchEvent(new dom.window.Event('change', { bubbles: true }));
  dom.window.document.querySelector('[data-act="tag-batch-confirm"]').click();
  const afterDelete = JSON.parse(dom.window.localStorage.getItem('hye_db_v1'));
  checks.push(['可以批量删除评价标签', afterDelete.studentTags.length === 0 && afterDelete.students[0].evaluationTags.length === 0]);
  console.log(JSON.stringify(checks.map(([name, pass]) => ({ name, pass })), null, 2));
  const pass = checks.every(([, value]) => value);
  console.log('OVERALL:', pass ? 'PASS' : 'FAIL');
  dom.window.close();
  process.exit(pass ? 0 : 1);
}, 550);
