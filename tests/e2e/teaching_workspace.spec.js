const { test, expect, E2E_TOKEN, dismissFirstUseModal } = require('./fixtures');
const headers={Authorization:`Bearer ${E2E_TOKEN}`};

test('existing task data remains readable while the frontend opens ordinary chat', async ({ page,request },info) => {
  const term = await (await request.get('/api/v1/terms/current',{headers})).json();
  const cls = await (await request.post('/api/v1/classes?term_id='+term.id,{headers,data:{name:'历史兼容-'+info.project.name}})).json();
  const response = await request.post('/api/v1/teaching/tasks',{headers,data:{term_id:term.id,class_id:cls.id,title:'历史任务',goal:'阅读证据'}});
  expect(response.status()).toBe(201);const task=await response.json();
  const existing = await request.get('/api/v1/teaching/tasks/'+task.id,{headers});
  expect(existing.status()).toBe(200);expect((await existing.json()).title).toBe('历史任务');
  await dismissFirstUseModal(page);await page.goto('/workbench');
  await page.locator('[data-act="tab-switch"][data-tab="teachmate"]').click();
  await expect(page.locator('#tmInput')).toBeVisible();
  await expect(page.getByRole('button',{name:'新建教学任务',exact:true})).toHaveCount(0);
  await expect(page.getByRole('complementary',{name:'教学工作区'})).toHaveCount(0);
  await expect(page.locator('.tm-suggestion-card')).toHaveCount(3);
});
