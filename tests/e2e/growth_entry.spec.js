const { test, expect } = require('./fixtures');

test('growth: confirmed points, batch entry and readable detail', async ({ page }, testInfo) => {
  await page.goto('/workbench');
  const seeded = await page.evaluate(async (suffix) => {
    const headers = { Authorization: 'Bearer ' + sessionStorage.getItem('workbench_token'), 'Content-Type': 'application/json' };
    async function call(url, body) {
      const res = await fetch('/api/v1/' + url, { headers, ...(body ? { method: 'POST', body: JSON.stringify(body) } : {}) });
      if (!res.ok) throw new Error('seed failed: ' + res.status);
      return res.json();
    }
    const term = await call('terms/current');
    const cls = await call('classes', { name: '成长测试-' + suffix });
    const students = [];
    for (let i = 0; i < 2; i++) students.push(await call('students', {
      student_no: 'growth-' + suffix + i, name: '演示学生' + suffix + i, class_id: cls.id,
    }));
    return { term: term.id, students };
  }, testInfo.project.name);
  await page.reload();
  await page.locator('[data-act="nav"][data-key="growth"]').click();
  await expect(page.locator('[data-act="growth-refresh"]')).toHaveCSS('background-color', 'rgb(255, 250, 243)');
  const student = seeded.students[0];
  const card = page.locator(`.growth-card[data-id="${student.id}"]`);
  await card.focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('.growth-detail-modal')).toBeVisible();
  const width = await page.locator('.growth-detail-modal').evaluate(el => el.getBoundingClientRect().width);
  expect(width).toBeGreaterThan(1000);
  await page.locator('[data-act="growth-custom-add"]').click();
  await expect(page.locator('#growthCustomNote')).toBeFocused();
  await expect(page.locator('#growthCustomNote')).toHaveAttribute('aria-invalid', 'true');
  await page.locator('#growthCustomNote').fill('完成单元复习并讲解订正依据');
  await expect(page.locator('#growthCustomNote')).not.toHaveAttribute('aria-invalid');
  await page.locator('#growthCustomPoints').fill('7');
  let releaseSave;
  const saveGate = new Promise(resolve => { releaseSave = resolve; });
  await page.route('**/api/v1/growth/activities', async route => { await saveGate; await route.continue(); });
  await page.locator('[data-act="growth-custom-add"]').click();
  await expect(page.locator('[data-act="growth-custom-add"]')).toBeDisabled();
  await expect(page.locator('[data-act="growth-custom-add"]')).toHaveText('正在补录…');
  await expect(page.locator('#growthCustomPoints')).toBeDisabled();
  releaseSave();
  await expect(page.locator('#toast')).toContainText('营养合计 +7');
  await page.unroute('**/api/v1/growth/activities');
  await expect(card).toContainText('7 营养');
  // Repeated category on the same day: neither category nor daily cap blocks entry.
  await card.click();
  await page.locator('#growthCustomNote').fill('完成第二项阅读任务');
  await page.locator('#growthCustomPoints').fill('7');
  await page.locator('[data-act="growth-custom-add"]').click();
  await expect(card).toContainText('14 营养');
  await card.click();
  await expect(page.locator('.growth-record-table tbody tr')).toHaveCount(2);
  await page.locator('.growth-detail-modal').evaluate(el => Promise.all(el.getAnimations().map(a => a.finished)));
  expect(await page.locator('#modalBody').evaluate(el => el.scrollTop)).toBe(0);
  const layout = await page.locator('.growth-record-table').evaluate(el => {
    const row = el.tBodies[0].rows[0];
    return { date: row.cells[0].getBoundingClientRect().width, reason: row.cells[2].getBoundingClientRect().width,
      nowrap: getComputedStyle(row.cells[0]).whiteSpace };
  });
  expect(layout.date).toBeGreaterThanOrEqual(100);
  expect(layout.reason).toBeGreaterThan(180);
  expect(layout.nowrap).toBe('nowrap');
  await page.screenshot({ animations: 'disabled', path: testInfo.outputPath('growth-wide.png') });
  await page.setViewportSize({ width: 800, height: 900 });
  await expect(page.locator('.growth-detail-grid')).toHaveCSS('grid-template-columns', /^(?!.* .*).*$/);
  expect(await page.locator('.growth-detail-modal').evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
  await page.screenshot({ animations: 'disabled', path: testInfo.outputPath('growth-stacked.png') });
  await page.locator('.growth-record-table [data-act="growth-reverse"]').first().click();
  await expect(card).toContainText('7 营养');
  await page.setViewportSize(testInfo.project.use.viewport);
  await page.locator('[data-act="growth-batch-toggle"]').click();
  for (const row of seeded.students) await page.locator(`.growth-card[data-id="${row.id}"]`).click();
  await page.locator('[data-act="growth-batch-open"]').click();
  await page.locator('#growthCustomType').selectOption('weekly_goal');
  await expect(page.locator('#growthCustomPoints')).toHaveValue('2');
  await page.locator('#growthCustomNote').fill('教师确认的阶段学习奖励');
  await page.locator('#growthCustomPoints').fill('50');
  await page.locator('[data-act="growth-batch-apply"][data-type="custom"]').click();
  await expect(page.locator('#toast')).toContainText('营养合计 +100');
  await expect(card).toContainText('57 营养');
  await expect(page.locator(`.growth-card[data-id="${seeded.students[1].id}"]`)).toContainText('50 营养');
  await expect(page.locator('#toast')).not.toHaveClass(/show/);
  await page.screenshot({ animations: 'disabled', path: testInfo.outputPath('growth-forest.png') });
  // A different modal must return to its normal size.
  await card.locator('[data-act="growth-quick-open"]').click();
  await expect(page.locator('#modal .modal-content')).not.toHaveClass(/growth-detail-modal/);
  await page.locator('#modalClose').click();
  // A historical capped entry is still visible, clearly explained, and filterable.
  await page.route(`**/api/v1/growth/students/${student.id}?*`, async route => {
    const response = await route.fetch();
    const detail = await response.json();
    detail.records.unshift({ event_id: 900001, business_date: '2026-09-25', source_type: 'teacher',
      scoring_mode: null, applied_points: 0, cap_reason: 'category_daily_awards',
      event_label: '完成学习任务', note: '历史记录示例', reversible: false });
    await route.fulfill({ response, json: detail });
  });
  await card.click();
  await expect(page.locator('.growth-history-notice')).toContainText('1 条旧补录');
  await page.locator('[data-filter="limited"]').click();
  await expect(page.locator('.growth-record-table tbody tr:visible')).toHaveCount(1);
  await expect(page.locator('.growth-record-table tbody')).toContainText('旧规则');
  await expect(page.locator('.growth-filter-count')).toHaveText('1 条记录');
  await page.locator('[data-filter="credited"]').click();
  await expect(page.locator('[data-record-kind="limited"]')).toBeHidden();
  await page.locator('[data-filter="all"]').click();
  await page.locator('#modalBody').evaluate(el => { el.scrollTop = 0; });
  await page.screenshot({ animations: 'disabled', path: testInfo.outputPath('growth-history.png') });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.locator('.growth-detail-modal').evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
  expect(await page.locator('#modalBody').evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
  await page.locator('#modalBody').evaluate(el => { el.scrollTop = 0; });
  await page.screenshot({ animations: 'disabled', path: testInfo.outputPath('growth-mobile.png') });
});
