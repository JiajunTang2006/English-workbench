// 生成随机默写成绩（每生两轮），输出为工作台可直接导入的 JSON 备份。
// 用法：node tools/gen_dictation_data.js [学生JSON] [输出JSON]
const fs = require('fs');
const pathMod = require('path');
const inFile = pathMod.resolve(process.argv[2] || pathMod.join(__dirname, '..', 'data', 'students_entrance.json'));
const out = pathMod.resolve(process.argv[3] || pathMod.join(__dirname, '..', 'data', 'dictation_generated.json'));
const students = require(inFile);

function genScore(english) {
  let base = Math.round(english * 0.92);            // 以入学英语为基线（弱生偏低）
  let score = base + Math.round((Math.random() - 0.5) * 16); // 随机波动
  if (Math.random() < 0.12) score -= Math.round(10 + Math.random() * 25); // 少量不及格
  return Math.max(20, Math.min(100, score));
}

const appStudents = students.map(s => ({
  id: s.id, name: s.name, gender: s.gender, class: s.class, english: s.english,
  target: '', weakTags: '', phone: s.phone || '', seat: ''
}));

const dictation = {};
appStudents.forEach(s => {
  dictation[s.id] = [genScore(s.english), genScore(s.english)];
});

const state = {
  schema: 2,
  teacher: { name: '', subject: '初中英语' },
  students: appStudents,
  exams: [],
  classes: [...new Set(appStudents.map(student => String(student.class || '').trim()).filter(Boolean))],
  settings: { excellent: 90, pass: 60, criticalLow: 55 },
  recitations: [], writings: [], errors: [], critical: [], todos: [],
  alignments: [], customs: [],
  dictation,
  dictationNames: ['自定义1', '自定义2']
};

fs.writeFileSync(out, JSON.stringify(state, null, 2), 'utf8');

// 统计概览
let r1 = [], r2 = [], fail = 0, total = 0;
appStudents.forEach(s => {
  r1.push(dictation[s.id][0]); r2.push(dictation[s.id][1]);
  [dictation[s.id][0], dictation[s.id][1]].forEach(v => { total++; if (v < state.settings.pass) fail++; });
});
const avg = a => (a.reduce((x,y)=>x+y,0)/a.length).toFixed(1);
console.log('已生成:', out);
console.log(`学生数: ${appStudents.length}（${state.classes.map(name => `${name}: ${appStudents.filter(student => student.class === name).length}`).join(' / ')}）`);
console.log(`默写1均分: ${avg(r1)}  默写2均分: ${avg(r2)}  不及格占比: ${(fail/total*100).toFixed(1)}%`);
