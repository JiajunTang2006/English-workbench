const { test, expect } = require('./fixtures');

async function openTeachMate(page) {
  await page.goto('/workbench');
  // 全新的隔离数据目录会弹出首次使用引导，先关闭再操作工作区。
  await page.addLocatorHandler(page.locator('#modal.show'), async () => {
    await page.locator('#modalClose').click();
  }, { times: 1 });
  await page.locator('[data-act="tab-switch"][data-tab="teachmate"]').click();
  await expect(page.locator('#tmInput')).toBeVisible();
  await expect(page.locator('.tm-suggestion-card')).toHaveCount(3);
}

async function seedTeachingPackage(page, withHistory = false) {
  await page.evaluate((history) => {
    const answer = {
      answer_type: 'review_plan',
      summary: '本周聚焦阅读推断：先找出原文依据，再练习解释选项。',
      timeline: '第一课时：讲评与基础练习。\n第二课时：分层训练与课后复测。',
      findings: [{ title: '推断题需要加强', description: '课堂练习中先定位证据，再解释推断。', evidence_ids: ['sample-1'] }],
      recommendations: [{ action: '围绕原文依据组织讲评', rationale: '让每个判断都能回到文本中核对。', priority: 1, supports: ['sample-1'] }],
      limitations: ['这是用于界面验收的示例材料。'],
      sections: [
        { kind: 'lesson_flow', title: '20 分钟阅读讲评课', body: '先独立思考，再交流依据。', items: ['5 分钟：重读短文并标记关键词。', '10 分钟：比较选项，解释排除理由。', '5 分钟：归纳推断过程。'] },
        { kind: 'student_handout', title: '阅读推断练习单', body: '阅读后，写出支持判断的原文句子。', items: ['问题一：人物为什么改变了计划？', '问题二：哪一句最能支持你的判断？'] },
        { kind: 'teacher_key', title: '答案与讲解要点', body: '教师参考答案：从人物行为变化中寻找线索。', items: ['检查学生是否区分原文事实和自己的推测。'] },
        { kind: 'followup_assessment', title: '课后复测草稿', body: '使用新短文检查证据定位方法是否能够迁移。', items: ['完成两道推断题，分别写明依据。'] },
      ],
    };
    const state = window.teachMateState;
    state.reset();
    state.setSessions([{ id: 71, title: '阅读讲评备课', term_id: 1, class_id: 5, exam_id: 3 }]);
    state.setCurrentSession(71);
    state.setMessages((history ? [40, 41] : [41]).map((id) => ({ role: 'assistant', structured_answer: answer, run_id: id })));
    state.setReportAnswer(answer);
    state.setCurrentRun({ id: 41, status: 'completed' });
    // This UI fixture has no persisted run; the API persistence path is
    // covered separately by backend tests.
    window.teachMateApi.saveMaterialEdit = async (runId, sectionIndex, edit) => ({
      run_id: Number(runId), section_index: Number(sectionIndex), ...edit,
    });
    window.render();
  }, withHistory);
}

test('教学材料：键盘展开、状态保留、历史报告隔离与学生版导出', async ({ page }, testInfo) => {
  await openTeachMate(page);
  await seedTeachingPackage(page, true);
  const reports = page.locator('.tm-inline-artifact');
  const current = reports.last();
  const history = reports.first();
  await expect(current.locator('.tm-material-card')).toHaveCount(4);
  await expect(current.locator('.tm-material-card[open]')).toHaveCount(1);
  await expect(current.locator('[data-testid="report-limitations"]')).toBeVisible();
  await current.getByRole('button', { name: '全部展开', exact: true }).click();
  await expect(current.locator('.tm-material-card[open]')).toHaveCount(4);
  await expect(history.locator('.tm-material-card[open]')).toHaveCount(1);
  await current.getByRole('button', { name: '全部收起', exact: true }).click();
  await expect(current.locator('.tm-material-card[open]')).toHaveCount(0);
  const teacherSummary = current.locator('.tm-material-teacher summary');
  await teacherSummary.focus();
  await page.keyboard.press('Enter');
  await expect(current.locator('.tm-material-teacher')).toHaveAttribute('open', '');
  await page.evaluate(() => window.render());
  await expect(current.locator('.tm-material-teacher')).toHaveAttribute('open', '');
  await expect(current.locator('.tm-material-card[open]')).toHaveCount(1);

  const studentCard = current.locator('.tm-material-student');
  await studentCard.locator('summary').click();
  await studentCard.getByRole('button', { name: '编辑材料' }).click();
  await studentCard.locator('[data-material-field="body"]').fill('学生修改版：先定位证据，再用自己的话解释推断。');
  await studentCard.locator('[data-material-field="items"]').fill('新问题一：哪处细节改变了人物的想法？\n新问题二：请抄写并解释你的证据。');
  await studentCard.getByRole('button', { name: '保存修改' }).click();
  await expect(studentCard).toContainText('教师已改');
  await studentCard.getByRole('button', { name: '查看修改对比' }).click();
  await expect(studentCard.locator('.tm-material-compare section').nth(0)).toContainText('阅读后，写出支持判断的原文句子。');
  await expect(studentCard.locator('.tm-material-compare section').nth(1)).toContainText('学生修改版：先定位证据');
  await expect(history.locator('.tm-material-student')).not.toContainText('学生修改版：先定位证据');
  await expect(page.locator('#toast')).not.toHaveClass(/show/);
  await studentCard.screenshot({ path: testInfo.outputPath('student-material-comparison.png'), animations: 'disabled' });

  await page.evaluate(() => {
    window.teachMateApi.exportDocument = async (spec) => {
      window.materialExportSpec = spec;
      return { artifact: { id: 1, filename: 'student-handout.pdf' } };
    };
    window.teachMateApi.downloadDocumentArtifact = async () => new Blob(['sample'], { type: 'application/pdf' });
  });
  const downloadPromise = page.waitForEvent('download');
  await current.getByRole('button', { name: '单独导出学生练习单', exact: true }).click();
  expect((await downloadPromise).suggestedFilename()).toBe('student-handout.pdf');
  const exported = await page.evaluate(() => window.materialExportSpec.inline_content);
  expect(exported.sections.map((section) => section.kind)).toEqual(['student_handout']);
  expect(exported.sections[0].body).toContain('学生修改版：先定位证据');
  expect(JSON.stringify(exported)).not.toContain('答案与讲解要点');
  expect(exported.findings).toEqual([]);
  expect(exported.timeline).toBe('');
});

test('教学材料：PC 窗口布局与减少动画偏好', async ({ page }, testInfo) => {
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await openTeachMate(page);
  const entry = page.locator('.tm-suggestion-plan');
  await entry.hover();
  await expect.poll(() => entry.locator('.tm-suggestion-icon').evaluate((node) => getComputedStyle(node).transform)).not.toBe('none');
  await page.keyboard.press('Tab');
  await entry.focus();
  expect(await entry.evaluate((node) => node.matches(':focus-visible'))).toBe(true);
  await page.screenshot({ path: testInfo.outputPath('welcome.png'), animations: 'disabled' });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  expect(await entry.evaluate((node) => getComputedStyle(node).transform)).toBe('none');
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await seedTeachingPackage(page);
  for (const width of [1440, 1100]) {
    await page.setViewportSize({ width, height: 900 });
    await page.locator('.tm-materials').evaluate((node) => node.scrollIntoView({ block: 'start', behavior: 'instant' }));
    const layout = await page.locator('.tm-materials').evaluate((node) => ({
      left: node.getBoundingClientRect().left,
      right: node.getBoundingClientRect().right,
      documentWidth: document.documentElement.scrollWidth,
      viewport: window.innerWidth,
    }));
    expect(layout.left).toBeGreaterThanOrEqual(0);
    expect(layout.right).toBeLessThanOrEqual(width);
    expect(layout.documentWidth).toBeLessThanOrEqual(layout.viewport);
    await page.screenshot({ path: testInfo.outputPath('materials-' + width + '.png'), animations: 'disabled' });
  }
  await page.emulateMedia({ reducedMotion: 'reduce' });
  expect(await page.locator('.tm-material-chevron').first().evaluate((node) => getComputedStyle(node).transitionDuration)).toBe('0s');
  await page.locator('.tm-material-student summary').click();
  await expect(page.locator('.tm-material-student .tm-material-content')).toBeVisible();
  expect(errors).toEqual([]);
});
