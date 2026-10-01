// ================================================================
// e2e 公共 Fixture (S0-03)
//
// 职责：
//   1. 在每个页面加载前，通过 addInitScript 写入仅供测试使用的
//      sessionStorage.workbench_token，使前端获得与本测试服务一致的
//      Bearer Token（服务由 playwright.config.js 的 webServer 自动拉起，
//      Token 与 tools/run_e2e_server.py 的默认值保持一致）；
//   2. 提供统一的 test/expect 导出，供所有 e2e spec 引用；
//   3. 不修改、不关闭生产鉴权，不把 Token 写入截图或 trace。
//
// E2E_TOKEN 只对本测试服务有效（端口 18323 + 独立临时数据目录），
// 不是生产密钥；不要在测试断言或报告中打印它。
// ================================================================
const { test: base, expect } = require('@playwright/test');

const E2E_TOKEN = 'workbench-e2e-test-token-18323';

async function dismissFirstUseModal(page) {
  // First-use settings may appear after asynchronous startup has finished.
  await page.addLocatorHandler(page.locator('#modal.show').filter({
    has: page.getByRole('heading', { name: '选择任教学科' })
  }), async () => { await page.locator('#modalClose').click(); });
}

const test = base.extend({
  page: async ({ page }, use) => {
    await page.addInitScript((token) => {
      try {
        sessionStorage.setItem('workbench_token', token);
      } catch (err) {
        // about:blank 等无源文档上 sessionStorage 可能不可用；导航到
        // 真实来源后 addInitScript 会再次执行并成功写入。
      }
    }, E2E_TOKEN);
    await use(page);
  },
});

module.exports = { test, expect, E2E_TOKEN, dismissFirstUseModal };
