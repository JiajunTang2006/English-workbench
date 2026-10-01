const { test, expect } = require('./fixtures');

async function openTeachMate(page) {
  await page.goto('/workbench');
  await page.waitForTimeout(600);
  const firstUse = page.locator('#modal.show #modalClose');
  if (await firstUse.count()) await firstUse.click();
  await page.locator('[data-act="tab-switch"][data-tab="teachmate"]').click();
  await expect(page.locator('.tm-side-shell')).toBeVisible();
}

test('TeachMate desktop sidebar and composer controls respond to clicks', async ({ page }) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await openTeachMate(page);

  await page.locator('[data-act="tm-toggle-search"]').click();
  await expect(page.locator('[data-act="tm-session-search"]')).toBeVisible();
  await page.locator('[data-act="tm-toggle-search"]').click();
  await expect(page.locator('#tmSearchPanel')).toBeHidden();

  await page.locator('[data-act="tm-composer-plus"]').click();
  await expect(page.locator('#tmImportMenu')).toBeVisible();
  await page.locator('[data-act="tm-composer-plus"]').click();
  await expect(page.locator('#tmImportMenu')).toBeHidden();

  await page.locator('[data-act="tm-composer-plus"]').click();
  await page.locator('[data-act="tm-plus-select-plugin"]').click();
  await expect(page.locator('#tmPluginMenu')).toBeVisible();
  await page.locator('[data-act="tm-composer-plus"]').click();
  await expect(page.locator('#tmPluginMenu')).toBeHidden();

  await page.locator('[data-act="tm-class-toggle"]').click();
  await expect(page.locator('#tmClassMenu')).toBeVisible();
  await page.locator('[data-act="tm-class-option"][data-class-name=""]').click();
  await expect(page.locator('[data-act="tm-class-toggle"] .tm-scope-picker-label')).toHaveText('全部班级');

  await page.locator('[data-act="tm-exam-toggle"]').click();
  await expect(page.locator('#tmExamMenu')).toBeVisible();
  await page.locator('[data-act="tm-exam-option"][data-exam-id=""]').click();
  await expect(page.locator('#tmExamMenu')).toBeHidden();

  await page.locator('[data-act="tm-model-toggle"]').click();
  await expect(page.locator('#tmModelMenu')).toBeVisible();
  await page.locator('[data-act="tm-model-toggle"]').click();
  await expect(page.locator('#tmModelMenu')).toBeHidden();

  await page.locator('[data-act="tm-folder-create"]').click();
  await expect(page.locator('#modalTitle')).toHaveText('新建文件夹');
  await page.locator('[data-act="tm-folder-create-cancel"]').click();
  await expect(page.locator('#modal')).not.toHaveClass(/show/);

  await page.locator('[data-act="tm-plugin-placeholder"]').click();
  await expect(page.locator('.tm-settings-page-head h2')).toHaveText('功能扩展');
  await page.locator('#modalClose').click();
  await expect(page.locator('#modal')).not.toHaveClass(/show/);

  await page.locator('[data-act="tm-library"]').first().click();
  await expect(page.locator('#modalTitle')).toHaveText('选择 WorkBench 资料库文件');
  await page.locator('[data-act="tm-library-cancel"]').click();
  expect(errors).toEqual([]);
});

test('TeachMate quick prompt fills and focuses the current composer', async ({ page }) => {
  await openTeachMate(page);
  await page.evaluate(() => teachMateState.setProviderInfo({
    agent_enabled: true, text_agent_enabled: true, api_key_configured: true,
  }));
  const card = page.locator('[data-act="tm-suggestion"]').first();
  const prompt = await card.getAttribute('data-prompt');
  await card.click();
  await expect(page.locator('#tmInput')).toHaveValue(prompt);
  await expect(page.locator('#tmInput')).toBeFocused();
});

test('TeachMate file picker remains attached until a file is chosen', async ({ page }) => {
  await openTeachMate(page);
  const chooser = page.waitForEvent('filechooser');
  await page.locator('[data-act="tm-composer-plus"]').click();
  await page.locator('[data-act="tm-attach"]').click();
  const picker = await chooser;
  expect(await picker.element().evaluate(input => input.isConnected)).toBe(true);
  await picker.setFiles({ name: 'lesson.txt', mimeType: 'text/plain', buffer: Buffer.from('lesson') });
  await expect.poll(() => picker.element().evaluate(node => node.isConnected)).toBe(false);
});

test('TeachMate rename saves the edited conversation title', async ({ page }) => {
  await openTeachMate(page);
  await page.evaluate(() => {
    window.teachMateState.setSessions([{ id: 901, title: '旧名称', status: 'active' }]);
    window.teachMateApi.updateSession = async (_id, payload) => { window.__renamedTitle = payload.title; };
    window.teachMateApi.listSessions = async () => [{ id: 901, title: window.__renamedTitle, status: 'active' }];
    renderNav();
  });
  await page.locator('.tm-conv-item[data-id="901"]').hover();
  await page.locator('[data-act="tm-conv-more"][data-id="901"]').click();
  await page.locator('[data-act="tm-rename-session"][data-id="901"]').click();
  await page.locator('#tmRenameInput').fill('新名称');
  await page.locator('[data-act="tm-rename-confirm"]').click();
  await expect(page.locator('#modal')).not.toHaveClass(/show/);
  await expect(page.locator('.tm-conv-title')).toHaveText('新名称');
  expect(await page.evaluate(() => window.__renamedTitle)).toBe('新名称');
});

test('TeachMate exam guidance opens the score page', async ({ page }) => {
  await openTeachMate(page);
  await page.evaluate(() => {
    const button = document.createElement('button');
    button.dataset.act = 'tm-open-exams';
    button.textContent = '去成绩面板';
    document.body.appendChild(button);
    button.click();
    button.remove();
  });
  await expect(page.locator('[data-act="tab-switch"][data-tab="workbench"]')).toHaveClass(/active/);
  await expect(page.locator('.nav-item[data-key="score"]')).toHaveClass(/active/);
});

test('unconfigured model keeps all quick prompts in chat and never sends on Enter', async ({ page }) => {
  await openTeachMate(page);
  await page.evaluate(() => teachMateState.setProviderInfo({
    agent_enabled:true,text_agent_enabled:true,api_key_configured:false,
  }));
  const cards=page.locator('[data-act="tm-suggestion"]');
  let writes=0;
  page.on('request',request=>{if(request.method()==='POST' && /\/api\/v1\/agent\/(sessions|analysis-groups)/.test(request.url())) writes++;});
  for(let i=0;i<3;i++) {
    const prompt=await cards.nth(i).getAttribute('data-prompt');
    await cards.nth(i).click();
    await expect(page.locator('#modal')).not.toHaveClass(/show/);
    await expect(page.locator('#tmInput')).toHaveValue(prompt);
    await expect(page.locator('#tmInput')).toBeFocused();
    await expect(page.locator('#tmInput')).toBeEnabled();
    await expect(page.locator('.tm-plugin-chip')).toBeVisible();
  }
  await page.locator('#tmInput').fill('先编辑我的问题');
  await page.locator('#tmInput').press('Enter');
  await expect(page.locator('#tmInput')).toHaveValue('先编辑我的问题');
  expect(writes).toBe(0);
  await expect(page.getByRole('button',{name:'AI 分析不可用',exact:true})).toBeDisabled();
  await page.getByRole('button',{name:'配置模型',exact:true}).click();
  await expect(page.locator('#modal').getByRole('heading',{name:'模型',exact:true})).toBeVisible();
  await page.locator('#modalClose').click();
  await expect(page.locator('#tmInput')).toHaveValue('先编辑我的问题');
});
