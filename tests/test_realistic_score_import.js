const fs = require('fs');
const path = require('path');
const { JSDOM, ResourceLoader } = require('jsdom');

const { loadWorkbenchHtml } = require('./helpers/workbench_html');

class LocalAssetLoader extends ResourceLoader {
  fetch(url) {
    const pathname = new URL(url).pathname;
    if (pathname.startsWith('/workbench-assets/')) {
      return Promise.resolve(fs.readFileSync(path.join(__dirname, '..', 'workbench-assets', path.basename(pathname))));
    }
    return null;
  }
}

const fixture = {
  schema: 4,
  dataContract: 2,
  teacher: { name: '', subject: '初中英语' },
  classes: [],
  settings: { excellent: 90, pass: 60, criticalLow: 55 },
  students: [],
  exams: [],
  currentExamId: '',
  recitations: [],
  writings: [],
  errors: [],
  paperDocuments: [],
  critical: [],
  todos: [],
  alignments: [],
  customs: [],
  dictation: {},
  dictationNames: ['自定义1', '自定义2'],
  studentTags: [],
  classAliases: {}
};

const dom = new JSDOM(loadWorkbenchHtml({ includeThirdParty: true }), {
  runScripts: 'dangerously',
  resources: new LocalAssetLoader(),
  url: 'https://localhost/',
  beforeParse(window) {
    window.localStorage.setItem('hye_db_v1', JSON.stringify(fixture));
  }
});

setTimeout(() => {
  const workbook = {
    SheetNames: ['年级', '5班'],
    Sheets: {
      年级: dom.window.XLSX.utils.aoa_to_sheet([
        ['英语学科整理', '第一部分 听力（共两节，满分 20 分）', '第二部分 阅读理解（共两节，满分 40 分）', '第三部分 英语知识运用（共两节，满分 25 分）', '第四部分 写作（共两节，满分 35 分）'],
        ['学号', '考号', '姓名', '班级', '得分', '班级名次', '年级名次'],
        ['S001', 'S001', '甲同学', '5班', 114, 1, 19],
      ]),
      '5班': dom.window.XLSX.utils.aoa_to_sheet([
        ['学号', '考号', '姓名', '班级', '得分', '班级名次', '年级名次'],
        ['S001', 'S001', '甲同学', '5班', 114, 1, 19],
      ])
    }
  };

  const result = dom.window.importFromWorkbooks([{
    name: '2025 学年第一学期九年级英语U6-7学科整理_英语_九年级5班_小分表.xlsx',
    workbook
  }]);
  const saved = JSON.parse(dom.window.localStorage.getItem('hye_db_v1'));
  const exam = saved.exams[0];
  const score = exam?.scores?.S001;
  const checks = [
    ['得分列被识别', score?.英语 === 114],
    ['按试卷分项满分推断总分120', exam?.fullScore === 120],
    ['文件名补全九年级5班为95', saved.students[0]?.class === '95'],
    ['年级排名被识别', score?.gradeRank === 19],
    ['重复工作表不重复导入', result.imported === 1 && saved.students.length === 1],
    ['超过100分的成绩没有被错误判为缺考', score?.attendanceStatus === 'present'],
  ];
  console.log(JSON.stringify(checks.map(([name, pass]) => ({ name, pass: Boolean(pass) })), null, 2));
  const pass = checks.every(([, value]) => value);
  console.log('OVERALL:', pass ? 'PASS' : 'FAIL');
  dom.window.close();
  process.exit(pass ? 0 : 1);
}, 650);
