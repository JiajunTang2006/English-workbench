// ================================================================
// U5-05 UI 自动化：TeachMate 关键流程 e2e
//
// 覆盖（不依赖真实 API Key）：
//   1. 页面加载 TeachMate 模块且无脚本错误；
//   2. 欢迎页隐藏配置状态区，展示三个可直接发起的提问入口；
//   3. 左栏会话导航、搜索入口与"新建分析"入口存在；
//   4. 输入区附件按钮可用（上传接线存在）；
//   5. 窄屏下右栏抽屉可打开/关闭（双层 project 覆盖）；
//   6. 运行时间线插件已挂载（通过 state 模块存在性验证）。
// ================================================================
const { test, expect } = require('./fixtures');

async function gotoTeachMate(page) {
  const errors = [];
  page.on('pageerror', (err) => errors.push(err.message));
  await page.goto('/workbench');
  await page.waitForTimeout(600);
  // 关闭可能出现的"页面已失效"守卫弹窗（上一次启动保留的旧页面提示）
  const staleModal = page.locator('#modal.show');
  if (await staleModal.count()) {
    const closeBtn = staleModal.locator('#modalClose');
    if (await closeBtn.count()) {
      await closeBtn.first().click();
      await page.waitForTimeout(200);
    }
  }
  // 进入 TeachMate 标签
  const nav = page.locator('[data-tab="teachmate"], [data-act="teachmate"]');
  if (await nav.count()) {
    await nav.first().click();
    await page.waitForTimeout(600);
  }
  return errors;
}

test('U5-05: page boots without script errors and loads TeachMate modules', async ({ page }) => {
  const errors = await gotoTeachMate(page);
  expect(errors).toEqual([]);

  const modules = await page.evaluate(() => ({
    state: typeof window.teachMateState,
    report: typeof window.teachMateReport,
    evidence: typeof window.teachMateEvidence,
    onboarding: typeof window.teachMateOnboarding,
    a11y: typeof window.teachMateA11y,
    api: typeof window.teachMateApi,
  }));
  expect(modules.state).toBe('object');
  expect(modules.report).toBe('object');
  expect(modules.evidence).toBe('object');
  expect(modules.onboarding).toBe('object');
  expect(modules.a11y).toBe('object');
  expect(modules.api).toBe('object');
});

test('U5-05: 欢迎页展示简洁提问入口', async ({ page }) => {
  const errors = await gotoTeachMate(page);
  expect(errors).toEqual([]);

  await expect(page.locator('.tm-quick-prompts-title')).toHaveText('你可以这样问我');
  await expect(page.locator('.tm-suggestion-card')).toHaveCount(3);
  await expect(page.locator('.tm-welcome-underline')).toHaveCount(1);
  await expect(page.locator('.tm-welcome-books')).toHaveCount(1);
  for (const selector of ['.tm-welcome-underline', '.tm-welcome-books']) {
    await expect.poll(() => page.locator(selector).evaluate((img) => img.complete && img.naturalWidth > 0)).toBe(true);
  }
  await expect(page.locator('[data-testid="setup-card"]')).toHaveCount(0);
  await expect(page.locator('[data-testid="readiness-ok"], [data-testid="readiness-issues"]')).toHaveCount(0);
  await expect(page.locator('[data-testid="exam-binding-choice"]')).toHaveCount(0);
});

test('U5-05: 左栏会话导航、侧栏搜索与新建分析入口可用', async ({ page }) => {
  await gotoTeachMate(page);
  const newChatButton = page.locator('[data-act="tm-new-chat"]').first();
  await expect(newChatButton).toBeVisible({ timeout: 5000 });

  // 新建只进入本地草稿态：不写入后端，也不在历史记录生成空壳会话。
  const historyBefore = await page.locator('.tm-conv-item').count();
  await newChatButton.click();
  await expect(page.locator('#tmMessages')).toHaveAttribute('data-session-id', '');
  await expect(page.locator('.tm-suggestion-card')).toHaveCount(3);
  await expect(page.getByText('这个对话还是空的')).toHaveCount(0);
  await expect(page.locator('.tm-conv-item')).toHaveCount(historyBefore);

  // 搜索入口已随聊天壳改版下放到侧栏，顶部按钮已移除。
  const searchButton = page.locator('[data-act="tm-toggle-search"]');
  await expect(searchButton).toBeVisible();
  await searchButton.click();

  const searchInput = page.locator('[data-act="tm-session-search"]').first();
  await expect(searchInput).toBeVisible();
  await expect(searchInput).toBeFocused();

  // 已归档对话统一从教师设置进入，左侧不再显示独立入口
  await expect(page.locator('[data-act="tm-open-trash"]')).toHaveCount(0);
  await page.locator('[data-act="tm-open-agent-settings"]').click();
  await page.locator('[data-act="tm-settings-tab"][data-settings-tab="archive"]').click();
  await expect(page.locator('.tm-settings-page-head h2')).toHaveText('已归档对话');
});

test('U5-05: Header 分区、聊天视口与消息对齐使用真实布局', async ({ page }) => {
  await gotoTeachMate(page);
  await expect(page.locator('.tm-center')).toBeVisible();

  await page.locator('#tmMessages').evaluate((messages) => {
    const rows = [];
    for (let i = 0; i < 36; i += 1) {
      rows.push(
        '<div class="tm-message tm-message-ai">' +
          '<div class="tm-message-avatar">AI</div>' +
          '<div class="tm-message-body"><div class="tm-message-content">分析结果 ' + i +
          '<pre>' + 'long-token-'.repeat(80) + '</pre></div></div>' +
        '</div>' +
        '<div class="tm-message tm-message-user">' +
          '<div class="tm-message-content">教师消息 ' + i + '</div>' +
        '</div>'
      );
    }
    messages.innerHTML = rows.join('');
  });

  const layout = await page.evaluate(() => {
    const rect = (selector) => document.querySelector(selector).getBoundingClientRect();
    const header = rect('header');
    const app = document.querySelector('#app');
    const container = document.querySelector('.container');
    const layoutNode = document.querySelector('.tm-layout');
    const headerLeft = rect('header .header-left-zone');
    const headerRight = rect('header .header-right-zone');
    const sidebar = rect('#sidebar');
    const title = rect('#appTitle');
    const main = document.querySelector('#workarea');
    const center = rect('.tm-center');
    const messages = document.querySelector('#tmMessages');
    const inputArea = rect('.tm-input-area');
    const userRow = document.querySelector('.tm-message-user');
    const assistantRow = document.querySelector('.tm-message-ai');
    const user = rect('.tm-message-user .tm-message-content');
    const assistant = rect('.tm-message-ai .tm-message-body');
    const messageStyle = getComputedStyle(messages);

    return {
      headerLeftWidth: headerLeft.width,
      sidebarWidth: sidebar.width,
      splitDelta: Math.abs(headerLeft.right - headerRight.left),
      titleStartsInRightZone: title.left >= headerRight.left,
      inputBottomDelta: Math.abs(center.bottom - inputArea.bottom),
      mainOverflowY: getComputedStyle(main).overflowY,
      mainFitsViewport: main.scrollHeight <= main.clientHeight + 1,
      messagesOverflowY: messageStyle.overflowY,
      messagesClientHeight: messages.clientHeight,
      messagesScrollHeight: messages.scrollHeight,
      appHeight: app.clientHeight,
      containerHeight: container.clientHeight,
      containerFlex: getComputedStyle(container).flex,
      containerHeightStyle: getComputedStyle(container).height,
      layoutHeight: layoutNode.clientHeight,
      layoutGridRows: getComputedStyle(layoutNode).gridTemplateRows,
      mainHeight: main.clientHeight,
      centerHeight: center.height,
      inputHeight: inputArea.height,
      messagesCanScroll: messages.scrollHeight > messages.clientHeight,
      userLeft: user.left,
      userRight: user.right,
      userRowJustify: getComputedStyle(userRow).justifyContent,
      userRowDisplay: getComputedStyle(userRow).display,
      userRowDirection: getComputedStyle(userRow).flexDirection,
      userRowWidth: userRow.getBoundingClientRect().width,
      userRowLeft: userRow.getBoundingClientRect().left,
      userRowRight: userRow.getBoundingClientRect().right,
      userContentMarginLeft: getComputedStyle(document.querySelector('.tm-message-user .tm-message-content')).marginLeft,
      userContentMarginRight: getComputedStyle(document.querySelector('.tm-message-user .tm-message-content')).marginRight,
      assistantLeft: assistant.left,
      assistantRight: assistant.right,
      // 当前聊天壳让 AI 回复占满内容流宽度，用户消息仍靠右；比较起点而不是
      // 回复容器右边界，避免把全宽 AI 内容误判为用户未右对齐。
      userStartsRightOfAssistant: user.left > assistant.left,
      pageHasHorizontalOverflow:
        document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
    };
  });

  expect(Math.abs(layout.headerLeftWidth - layout.sidebarWidth)).toBeLessThanOrEqual(1);
  expect(layout.splitDelta).toBeLessThanOrEqual(1);
  expect(layout.titleStartsInRightZone).toBe(true);
  expect(layout.inputBottomDelta).toBeLessThanOrEqual(1);
  expect(layout.mainOverflowY).toBe('hidden');
  expect(layout.mainFitsViewport).toBe(true);
  expect(layout.messagesOverflowY).toBe('auto');
  expect(
    layout.messagesCanScroll,
    JSON.stringify({
      clientHeight: layout.messagesClientHeight,
      scrollHeight: layout.messagesScrollHeight,
      appHeight: layout.appHeight,
      containerHeight: layout.containerHeight,
      containerFlex: layout.containerFlex,
      containerHeightStyle: layout.containerHeightStyle,
      layoutHeight: layout.layoutHeight,
      layoutGridRows: layout.layoutGridRows,
      mainHeight: layout.mainHeight,
      centerHeight: layout.centerHeight,
      inputHeight: layout.inputHeight,
    })
  ).toBe(true);
  expect(
    layout.userStartsRightOfAssistant,
    JSON.stringify({
      userLeft: layout.userLeft,
      userRight: layout.userRight,
      userRowJustify: layout.userRowJustify,
      userRowDisplay: layout.userRowDisplay,
      userRowDirection: layout.userRowDirection,
      userRowWidth: layout.userRowWidth,
      userRowLeft: layout.userRowLeft,
      userRowRight: layout.userRowRight,
      userContentMarginLeft: layout.userContentMarginLeft,
      userContentMarginRight: layout.userContentMarginRight,
      assistantLeft: layout.assistantLeft,
      assistantRight: layout.assistantRight,
    })
  ).toBe(true);
  expect(layout.pageHasHorizontalOverflow).toBe(false);
});

test('U5-05: 输入区附件按钮可用（U4 管线前台入口）', async ({ page }) => {
  await gotoTeachMate(page);
  const attach = page.locator('[data-act="tm-attach"]').first();
  const importToggle = page.locator('[data-act="tm-composer-plus"]').first();
  await expect(importToggle).toBeVisible({ timeout: 5000 });
  expect(await importToggle.isDisabled()).toBe(false);
  await importToggle.click();
  await expect(attach).toBeVisible({ timeout: 5000 });
  expect(await attach.isDisabled()).toBe(false);
});

test('U5-05: 窄屏下右侧抽屉可开合', async ({ page }) => {
  const errors = await gotoTeachMate(page);
  expect(errors).toEqual([]);

  const toggle = page.locator('[data-act="tm-toggle-right-panel"]').first();
  // 桌面宽度下右栏常驻，抽屉切换仅在窄屏视口有意义 → 桌面 project 直接通过
  if (!(await toggle.isVisible().catch(() => false))) return;
  await toggle.click();
  await page.waitForTimeout(300);
  const drawer = page.locator('#tmRightPanel');
  const cls = await drawer.getAttribute('class');
  expect(cls).toContain('tm-drawer-open');
  // Esc 关闭
  await page.keyboard.press('Escape');
  await page.waitForTimeout(200);
  const clsAfter = await drawer.getAttribute('class');
  expect(clsAfter).not.toContain('tm-drawer-open');
});

test('U5-05: 报告画布与证据检查器可在渲染层调用（离线 raport 冒烟）', async ({ page }) => {
  await gotoTeachMate(page);
  const res = await page.evaluate(() => {
    const html = window.teachMateReport.renderReportCanvas({
      summary: '摘要',
      findings: [{ title: '发现', claim: '内容', evidence_ids: ['ev-1'] }],
      recommendations: [{ action: '建议', rationale: '理由', priority: 1 }],
      limitations: ['局限'],
    });
    return {
      hasCanvas: html.includes('report-canvas'),
      hasEvRef: html.includes('ev-1'),
      hasRec: html.includes('建议'),
    };
  });
  expect(res.hasCanvas).toBe(true);
  expect(res.hasEvRef).toBe(true);
  expect(res.hasRec).toBe(true);
});
