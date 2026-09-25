#!/usr/bin/env node
/**
 * 为响应式审计准备真实数据：走前端真实导入通道灌入名单 + 三次月考成绩。
 *
 * 用法：node tools/seed-e2e-data.cjs [--url http://127.0.0.1:18411]
 */
const { chromium } = require('playwright');
const path = require('path');

const args = process.argv.slice(2);
const i = args.indexOf('--url');
const BASE_URL = i >= 0 && args[i + 1] ? args[i + 1] : 'http://127.0.0.1:18411';
const TOKEN = 'workbench-e2e-test-token-18323';
const DATA = path.join(__dirname, '..', 'test_data');

const ROSTERS = ['class_711_roster.csv', 'class_712_roster.csv'];
const EXAMS = ['september_exam.csv', 'october_exam.csv', 'november_exam.csv'];

(async () => {
  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await context.addInitScript((t) => {
    try { sessionStorage.setItem('workbench_token', t); } catch (e) {}
  }, TOKEN);
  const page = await context.newPage();
  page.on('pageerror', (e) => console.log('  [pageerror]', String(e.message).slice(0, 120)));

  await page.goto(BASE_URL + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(1500);

  for (const file of [...ROSTERS, ...EXAMS]) {
    const abs = path.join(DATA, file);
    console.log(`导入 ${file} ...`);
    await page.setInputFiles('#fileInput', abs);
    await page.waitForTimeout(2500);
    // 导入后可能需要确认（预览弹窗），点掉所有可见的确认按钮
    for (const sel of ['#modalFooter .btn-primary', '#modalFooter .btn:not(.btn-text)', '.import-confirm', '#modal .btn-primary']) {
      const btn = page.locator(sel).first();
      if (await btn.count() && await btn.isVisible().catch(() => false)) {
        await btn.click({ force: true }).catch(() => {});
        await page.waitForTimeout(1800);
      }
    }
    const toast = await page.locator('#toast').textContent().catch(() => '');
    console.log(`   提示: ${String(toast || '').trim().slice(0, 80) || '(无)'}`);
  }

  const stats = await page.evaluate(`(() => {
    return {
      students: (window.__state && window.__state.students ? window.__state.students.length : -1),
    };
  })()`);
  console.log('内部状态学生数:', stats.students);

  // 通过 API 校验
  const apiCheck = await page.evaluate(`(async () => {
    const t = sessionStorage.getItem('workbench_token');
    const r = await fetch('/api/v1/classes', { headers: { Authorization: 'Bearer ' + t } });
    const c = r.ok ? await r.json() : [];
    const s = await fetch('/api/v1/students', { headers: { Authorization: 'Bearer ' + t } });
    const sd = s.ok ? await s.json() : [];
    return { classes: c.length, students: sd.length };
  })()`);
  console.log('API 校验:', JSON.stringify(apiCheck));

  await browser.close();
})();
