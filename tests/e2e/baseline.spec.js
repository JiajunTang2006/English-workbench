// ================================================================
// S0-03 工程基线 e2e：一键入口与鉴权边界
//
// 增补覆盖：
//   1. /health 可访问（webServer 健康检查口径一致）；
//   2. 无 Token 调用 API 返回 401（生产鉴权保持开启）；
//   3. 有测试 Token 可读取运行时信息（版本/迁移号与源码一致）；
//   4. 页面无未处理脚本错误且 TeachMate 核心模块加载成功；
//   5. 桌面与窄屏布局均可用（双层 project 自动覆盖）。
//
// Token 通过公共 fixture 自动注入页面；API 级断言显式携带
// Bearer 头，避免向测试报告路径泄漏 Token。
// ================================================================
const { test, expect, E2E_TOKEN } = require('./fixtures');
const fs = require('node:fs');
const path = require('node:path');

const versionSource = fs.readFileSync(
  path.join(__dirname, '../../backend/app/version.py'),
  'utf8',
);
const schemaRevision = versionSource.match(/^SCHEMA_REVISION = "([^"]+)"/m)?.[1];
const appVersion = versionSource.match(/^APP_VERSION = "([^"]+)"/m)?.[1];
if (!schemaRevision) throw new Error('SCHEMA_REVISION is missing from backend/app/version.py');
if (!appVersion) throw new Error('APP_VERSION is missing from backend/app/version.py');

function bearer() {
  return { Authorization: `Bearer ${E2E_TOKEN}` };
}

test('S0-03: /health 可访问', async ({ request }) => {
  const res = await request.get('/health');
  expect(res.status()).toBe(200);
  const body = await res.json();
  expect(body.status).toBe('ok');
  expect(body.service).toBe('english-workbench');
});

test('S0-03: 无 Token 调用 API 返回 401', async ({ request }) => {
  const res = await request.get('/api/v1/runtime');
  expect(res.status()).toBe(401);
});

test('S0-03: 有测试 Token 可以读取运行时信息', async ({ request }) => {
  const res = await request.get('/api/v1/runtime', { headers: bearer() });
  expect(res.status()).toBe(200);
  const body = await res.json();
  expect(body.version).toBe(appVersion);
  expect(body.schema).toBe(schemaRevision);
});

test('S0-03: 页面无未处理脚本错误且 TeachMate 模块加载成功', async ({ page }) => {
  const errors = [];
  page.on('pageerror', (err) => errors.push(err.message));
  await page.goto('/workbench');
  await page.waitForTimeout(600);
  expect(errors).toEqual([]);
  const modules = await page.evaluate(() => ({
    state: typeof window.teachMateState,
    report: typeof window.teachMateReport,
    api: typeof window.teachMateApi,
  }));
  expect(modules.state).toBe('object');
  expect(modules.report).toBe('object');
  expect(modules.api).toBe('object');
});
