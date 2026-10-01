const { test, expect, dismissFirstUseModal } = require('./fixtures');

async function enter(page) {
  await dismissFirstUseModal(page);
  await page.goto('/workbench');
  await page.locator('[data-act="tab-switch"][data-tab="teachmate"]').click();
  await expect(page.locator('#tmInput')).toBeVisible();
}

test('targeted practice selects chat skill without a task form or losing the teacher draft', async ({ page }, info) => {
  const errors = []; page.on('pageerror', e => errors.push(e.message));
  await enter(page);
  await page.evaluate(() => teachMateState.setProviderInfo({agent_enabled:true,text_agent_enabled:true,api_key_configured:true}));
  await page.locator('#tmInput').fill('查看小明近期的薄弱知识点');
  await page.locator('[data-act="tm-plugin-placeholder"]').click();
  await expect(page.locator('.tm-settings-plugin-row').filter({ hasText:'专项推题' }).locator('.tm-settings-plugin-state')).toHaveText('已启用');
  await page.getByRole('button', { name:'打开推题' }).click();
  await expect(page.locator('#modal')).not.toHaveClass(/show/);
  await expect(page.locator('.tm-plugin-chip')).toContainText('专项推题');
  await expect(page.locator('#tmInput')).toHaveValue('查看小明近期的薄弱知识点');
  await expect(page.locator('#tmInput')).toBeFocused();
  await expect(page.getByRole('heading', {name:'新建教学任务', exact:true})).toHaveCount(0);
  await expect(page.locator('#tmTaskCanvas')).toHaveCount(0);
  await page.screenshot({path:info.outputPath('targeted-chat-plugin.png'),animations:'disabled'});
  await page.locator('[data-act="tm-plugin-clear"]').click();
  await expect(page.locator('.tm-plugin-chip')).toHaveCount(0);
  await expect(page.locator('#tmInput')).toHaveValue('查看小明近期的薄弱知识点');
  expect(errors).toEqual([]);
});

test('practice chat sends teacher language and optional skill without task or exam requirements', async ({ page }) => {
  await enter(page);
  await page.locator('[data-act="tm-composer-plus"]').click();
  await page.locator('[data-act="tm-plus-select-plugin"]').click();
  await page.locator('[data-act="tm-plugin-option"][data-plugin-id="targeted_practice"]').click();
  await page.evaluate(() => teachMateState.setProviderInfo({agent_enabled:true,text_agent_enabled:true,api_key_configured:true}));
  let sent;
  // Capture the transport contract; stop before a model call in this offline UI test.
  await page.route('**/api/v1/agent/sessions/*/messages', async route => {
    if(route.request().method() !== 'POST') return route.continue();
    sent = route.request().postDataJSON();
    await route.fulfill({status:409,json:{detail:{code:'TEST_CAPTURE',message:'模拟模型已接收请求'}}});
  });
  await page.locator('#tmInput').fill('查看小明近期薄弱点，再找一道原错题和两道新练习');
  await page.locator('[data-act="tm-send"]').click();
  await expect.poll(() => sent).toBeTruthy();
  expect(sent.content).toBe('查看小明近期薄弱点，再找一道原错题和两道新练习');
  expect(sent.plugin_id).toBe('targeted_practice');
  expect(sent.quick_task).toBeUndefined();
  expect(sent.teaching_artifact_id).toBeUndefined();
  await expect(page.getByText('请先选择考试')).toHaveCount(0);
  await expect(page.locator('#tmTaskCanvas')).toHaveCount(0);
});
