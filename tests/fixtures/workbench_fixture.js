'use strict';

const TEST_PHONES = ['13800138000', '13900139000', '13800000000', '13912345678'];

function createWorkbenchFixture() {
  const students = Array.from({ length: 92 }, (_, index) => {
    const className = index < 46 ? '711' : '712';
    const classIndex = index % 46;
    const idPrefix = className === '711' ? '202611' : '202612';

    return {
      id: `${idPrefix}${String(classIndex + 1).padStart(2, '0')}`,
      name: `测试学生${String(index + 1).padStart(3, '0')}`,
      gender: index % 2 === 0 ? '女' : '男',
      class: className,
      english: 40 + ((index * 13) % 61),
      target: '',
      weakTags: '',
      phone: TEST_PHONES[index % TEST_PHONES.length],
      seat: '',
    };
  });

  const dictation = Object.fromEntries(students.map((student, index) => [
    student.id,
    [35 + ((index * 17) % 66), 35 + ((index * 23) % 66)],
  ]));

  return {
    schema: 1,
    teacher: { name: '测试教师', subject: '初中英语' },
    students,
    exams: [],
    classes: ['711', '712'],
    settings: { excellent: 90, pass: 60, criticalLow: 55 },
    recitations: [],
    writings: [],
    errors: [],
    critical: [],
    todos: [],
    alignments: [],
    customs: [],
    dictation,
    dictationNames: ['自定义1', '自定义2'],
  };
}

module.exports = { createWorkbenchFixture };
