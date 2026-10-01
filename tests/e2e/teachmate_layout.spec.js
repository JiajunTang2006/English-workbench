const { test, expect, dismissFirstUseModal } = require('./fixtures');

test('original warm chat layout keeps sidebar and composer usable across sizes', async ({ page },info) => {
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await dismissFirstUseModal(page);await page.goto('/workbench');
  await page.locator('[data-act="tab-switch"][data-tab="teachmate"]').click();
  await expect(page.locator('.tm-welcome h2')).toBeVisible();
  await expect(page.getByText('你可以这样问我',{exact:true})).toBeVisible();
  await expect(page.locator('.tm-suggestion-card')).toHaveCount(3);
  await expect(page.locator('.tm-suggestion-card').last()).toContainText('生成复习计划');
  await expect(page.locator('.tm-layout-task,.tm-home-grid,#tmTaskCanvas')).toHaveCount(0);
  for(const width of [1440,1100,780]) {
    await page.setViewportSize({width,height:900});
    await expect(page.locator('#tmInput')).toBeVisible();
    const composer = await page.locator('.tm-composer').boundingBox();
    expect(composer.y+composer.height).toBeLessThanOrEqual(900);
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
    await page.screenshot({path:info.outputPath('original-chat-'+width+'.png'),animations:'disabled'});
  }
  expect(errors).toEqual([]);
});
