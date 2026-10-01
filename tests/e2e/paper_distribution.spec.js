const { test, expect, E2E_TOKEN, dismissFirstUseModal } = require('./fixtures');
const headers = { Authorization: `Bearer ${E2E_TOKEN}` };
async function fillRange(container, type, start, end) {
  const row = container.locator(`[data-paper-type="${type}"]`);
  await row.locator('[data-range-start]').fill(String(start));
  await row.locator('[data-range-end]').fill(String(end));
}
async function expectRange(container, type, start, end) {
  const row = container.locator(`[data-paper-type="${type}"]`);
  await expect(row.locator('[data-range-start]')).toHaveValue(String(start));
  await expect(row.locator('[data-range-end]')).toHaveValue(String(end));
}

// Run against the isolated test server; no models and no production database.
test('teacher defaults and exam overrides drive imported scores and the ability chart', async ({ page, request }, info) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await dismissFirstUseModal(page);
  await page.goto('/workbench');
  await page.locator('[data-act="nav"][data-key="settings"]').click();
  const defaults = page.locator('#paper-default-rows');
  await expect(defaults.locator('[data-paper-type]')).toHaveCount(6);
  expect(await defaults.locator('[data-paper-type]').evaluateAll(nodes => nodes.map(n => n.dataset.paperType)))
    .toEqual(['听力理解', '阅读理解', '完形填空', '词汇运用', '语法填空', '书面表达']);
  await expect(page.locator('#paper-default-card select')).toHaveCount(0);
  await expect(page.locator('#paper-default-card input[type="number"]')).toHaveCount(12);
  for (const field of await defaults.locator('input').all()) await field.fill('');
  await defaults.locator('[data-paper-type="听力理解"] [data-range-start]').fill('1');
  await page.locator('[data-act="paper-default-save"]').click();
  await expect(page.locator('#paper-default-status')).toContainText('起止两个题号');
  await fillRange(defaults, '听力理解', 2, 1);
  await page.locator('[data-act="paper-default-save"]').click();
  await expect(page.locator('#paper-default-status')).toContainText('起始题号不能大于结束题号');
  await fillRange(defaults, '听力理解', 1, 2);
  await fillRange(defaults, '阅读理解', 3, 3);
  await fillRange(defaults, '书面表达', 4, 4);
  await page.locator('[data-act="paper-default-save"]').click();
  await expect(page.locator('#paper-default-status')).toHaveText('已保存');
  await page.reload();
  await page.locator('[data-act="nav"][data-key="settings"]').click();
  await expectRange(page.locator('#paper-default-rows'), '听力理解', 1, 2);

  const term = await (await request.get('/api/v1/terms/current', { headers })).json();
  const workspace = await (await request.get(`/api/v1/terms/${term.id}/workspace-state`, { headers })).json();
  const key = `paper-${info.project.name}`;
  const studentNo = info.project.name === 'desktop' ? '99001' : '99002';
  const state = workspace.state || { schema: 4, todos: [], archivedStudents: [], archivedExams: [] };
  state.teacher = { name: '测试老师', subject: '初中英语' };
  state.classes = [...new Set([...(state.classes || []), '试卷测试班'])];
  state.students = [...(state.students || []), { id: studentNo, name: '试卷测试学生', class: '试卷测试班' }];
  state.exams = [...(state.exams || []), { id: key, name: `试卷设置测试-${info.project.name}`, date: '2026-10-01', fullScore: 100,
    scores: { [studentNo]: { 英语: 80 } }, tierLines: { a: 90, b: 75, c: 60 } }];
  state.currentExamId = key;
  const seed = await request.put(`/api/v1/terms/${term.id}/workspace-state`, { headers, data: { state, expected_revision: workspace.revision } });
  expect(seed.ok()).toBeTruthy();
  await page.reload();
  await page.locator('[data-act="nav"][data-key="score"]').click();
  await page.locator('#score-exam-select').selectOption(key);
  await page.locator('[data-act="score-paper-settings"]').click();
  await expectRange(page.locator('#exam-paper-rows'), '听力理解', 1, 2);
  // Both validation failure and successful retry operate through the real API.
  await fillRange(page.locator('#exam-paper-rows'), '阅读理解', 2, 3);
  await page.locator('#exam-paper-save').click();
  await expect(page.locator('#exam-paper-status')).toContainText('重复');
  await fillRange(page.locator('#exam-paper-rows'), '阅读理解', 3, 3);
  await page.locator('#exam-paper-save').click();
  await expect(page.locator('#modal')).not.toHaveClass(/show/);
  await page.locator('[data-act="score-item-upload"]').click();
  await page.locator('#m-item-score-paste').fill(`题号\t学号\t得分\n1\t${studentNo}\t2\n2\t${studentNo}\t0\n3\t${studentNo}\t3\n4\t${studentNo}\t5`);
  await page.locator('#m-item-score-submit').click();
  await expect(page.locator('#item-score-errors')).toContainText('已写入 4 条小分');
  await page.locator('#modalClose').click();

  // Reclassify already imported question 2 and verify actual score retention.
  await page.locator('[data-act="score-paper-settings"]').click();
  await fillRange(page.locator('#exam-paper-rows'), '听力理解', 1, 1);
  await fillRange(page.locator('#exam-paper-rows'), '阅读理解', 2, 3);
  await page.locator('#exam-paper-save').click();
  await expect(page.locator('#modal')).not.toHaveClass(/show/);
  await page.locator('[data-act="nav"][data-key="stu"]').click();
  await page.locator('[data-act="stu-search"]').fill(studentNo);
  await page.locator(`[data-act="stu-detail"][data-id="${studentNo}"]`).click();
  const chart = page.locator(`#student-ability-radar-${studentNo}`);
  await expect(chart).toHaveAttribute('data-measured-count', '3');
  await expect(chart.locator('canvas')).toHaveCount(1);
  await expect(page.locator('.student-profile-radar-head small')).toContainText('每题平均得分');
  const chartOption = await chart.evaluate(node => window.echarts.getInstanceByDom(node).getOption());
  expect(chartOption.radar[0].indicator.map(item => item.name)).toEqual(['听力理解', '阅读理解', '书面表达']);
  expect(chartOption.series[0].data[0].value).toEqual([2, 1.5, 5]);
  // Unmeasured types must be omitted, never plotted at the center as zero.
  await expect(page.locator('.student-ability-missing')).toContainText('语法填空');
  await page.locator('#modalClose').click();
  await page.locator('[data-act="nav"][data-key="settings"]').click();
  await expectRange(page.locator('#paper-default-rows'), '听力理解', 1, 2);
  expect(errors).toEqual([]);
  await page.screenshot({ path: info.outputPath('paper-defaults.png'), fullPage: true });
});


test('merged reading retains separate ranges in the settings editor', async ({ page, request }) => {
  const previous = await (await request.get('/api/v1/exams/paper-distribution/default', { headers })).json();
  const seeded = await request.put('/api/v1/exams/paper-distribution/default', { headers, data: {
    subject_key: 'english', expected_revision: previous.revision,
    rows: [{ question_type: '阅读理解', question_numbers: '1～3' },
           { question_type: '任务型阅读', question_numbers: '8～9、4-1～4-2' }],
  } });
  expect(seeded.ok()).toBeTruthy();
  await dismissFirstUseModal(page);
  await page.goto('/workbench');
  await page.locator('[data-act="nav"][data-key="settings"]').click();
  const card = page.locator('#paper-default-card');
  await expect(card.locator('.card-title')).toHaveText('试卷设置');
  await expect(card.locator('[data-paper-type="任务型阅读"]')).toHaveCount(0);
  const reading = card.locator('[data-paper-type="阅读理解"]');
  await expect(reading.locator('.paper-distribution-range')).toHaveCount(3);
  await expect(reading.locator('[data-range-start]').nth(0)).toHaveValue('1');
  await expect(reading.locator('[data-range-end]').nth(0)).toHaveValue('3');
  await expect(reading.locator('[data-range-start]').nth(1)).toHaveValue('8');
  await expect(reading.locator('[data-range-end]').nth(1)).toHaveValue('9');
  await expect(reading.locator('.paper-distribution-range').nth(2)).toContainText('第4大题');
  await page.locator('[data-act="paper-default-save"]').click();
  await expect(page.locator('#paper-default-status')).toHaveText('已保存');
  const saved = await (await request.get('/api/v1/exams/paper-distribution/default', { headers })).json();
  expect(saved.rows.find(row => row.question_type === '阅读理解').question_numbers).toBe('1～3、8～9、4-1～4-2');
  // Clear only this test's defaults so the other browser case has a blank start.
  const reset = await request.put('/api/v1/exams/paper-distribution/default', { headers,
    data: { subject_key: 'english', expected_revision: saved.revision, rows: [] } });
  expect(reset.ok()).toBeTruthy();
});
