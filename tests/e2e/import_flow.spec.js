// S0-04：真实文件导入冒烟
// 覆盖名单导入、成绩导入、导入后的数据库持久化和成绩页展示。
const path = require('path');
const { test, expect } = require('./fixtures');

const DATA_DIR = path.join(__dirname, '..', '..', 'test_data');

test('S0-04: 名单和成绩文件可通过真实页面导入并持久化', async ({ page }, testInfo) => {
  // 该用例会写入共享的隔离测试数据库；桌面和窄屏项目并行执行会
  // 人为制造同一班级的写入竞争。窄屏布局由 TeachMate 用例覆盖，导入
  // 链路只需在一个项目中执行一次。
  testInfo.project.name === 'narrow' && testInfo.skip('导入写入测试仅在桌面项目执行一次');
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.goto('/workbench');
  await page.waitForTimeout(700);

  await page.locator('#fileInput').setInputFiles(path.join(DATA_DIR, 'class_711_roster.csv'));
  await expect(page.locator('#toast')).toContainText('名单导入成功', { timeout: 5000 });
  await expect(page.locator('#fileInput')).toBeEnabled();

  const rosterCheck = await page.evaluate(async () => {
    const token = sessionStorage.getItem('workbench_token');
    const [classes, students, workspace] = await Promise.all([
      fetch('/api/v1/classes', { headers: { Authorization: 'Bearer ' + token } }).then((r) => r.json()),
      fetch('/api/v1/students', { headers: { Authorization: 'Bearer ' + token } }).then((r) => r.json()),
      fetch('/api/v1/workspace-state', { headers: { Authorization: 'Bearer ' + token } }).then((r) => r.json()),
    ]);
    return { classNames: classes.map((item) => item.name), studentCount: students.length, revision: workspace.revision };
  });
  expect(rosterCheck.classNames).toContain('711');
  expect(rosterCheck.studentCount).toBeGreaterThan(0);
  expect(rosterCheck.revision).toBeGreaterThan(0);

  await page.locator('#fileInput').setInputFiles(path.join(DATA_DIR, 'september_exam.csv'));
  await expect(page.locator('#toast')).toContainText('入学考试', { timeout: 5000 });
  await page.locator('[data-act="nav"][data-key="score"]').click();
  await expect.poll(() => page.locator('#score-exam-select').inputValue()).not.toBe('');
  await expect(page.locator('.score-table')).toContainText('84.5');
  await expect(page.locator('.score-table')).toContainText('247');
  expect(errors).toEqual([]);
});
