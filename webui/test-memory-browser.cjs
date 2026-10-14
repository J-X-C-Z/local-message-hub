async page => {
  const conversation = {id: 'fixture-room', platform: 'qq', account_id: 'fixture', title: 'Synthetic performance fixture', kind: 'private'};
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    let result = {};
    if (url.pathname === '/api/sources') result = {sources: []};
    else if (url.pathname === '/api/settings') result = {capabilities: {notifications: {enabled: false}}};
    else if (url.pathname === '/api/conversations') result = {items: [conversation], total: 1};
    else if (url.pathname === '/api/messages') {
      const offset = Number(url.searchParams.get('offset') || 0);
      result = {items: Array.from({length: 100}, (_, index) => ({id: String(offset + index),
        platform: 'qq', account_id: 'fixture', conversation_id: conversation.id,
        sender_name: 'Synthetic', type: 'text', text: 'Synthetic message '.repeat(30),
        timestamp: 1700000000 - offset - index})), total: 10000, has_more: offset + 100 < 10000};
    }
    await route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify(result)});
  });
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('Network.setCacheDisabled', {cacheDisabled: true});
  await page.goto('http://127.0.0.1:8767/');
  await page.waitForSelector('[data-message-id]');
  await page.evaluate(() => {
    clearInterval(sourcePollTimer);
    const fixture = state.messages[0];
    state.messages = Array.from({length: 10000}, (_, index) => ({...fixture, id: String(index), timestamp: 1700000000 - index}));
    state.messageOffset = 10000;renderView();
  });
  await cdp.send('HeapProfiler.collectGarbage');
  const baseline = await cdp.send('Runtime.getHeapUsage');
  const baselineNodes = await page.locator('[data-message-id]').count();
  await page.evaluate(async () => {await loadMessages(false);renderView();});
  for (let index = 1; index < 100; index++) {
    await page.evaluate(async () => {await loadMessages(true);renderView();});
    const count = await page.locator('[data-message-id]').count();
    if (count > 500) throw new Error('Message DOM exceeded bound: ' + count);
  }
  await cdp.send('HeapProfiler.collectGarbage');
  const optimized = await cdp.send('Runtime.getHeapUsage');
  const optimizedNodes = await page.locator('[data-message-id]').count();
  await page.screenshot({path: 'output/performance-webui-window.png', fullPage: false});
  await page.evaluate(result => {window.__browserMemory = result;}, {baselineNodes, optimizedNodes, baseline, optimized,
    note: 'Synthetic unbounded 10000-row DOM versus 100-page 500-row window; JS heap, not process RSS.'});
  await page.locator('[data-action="latest"]').click();
  await page.waitForFunction(() => state.messages[0]?.id === '0' && state.messages.length === 100);
  await page.evaluate(() => {
    state.contextMode = true;state.contextMessage = 'context-fixture';
  });
  await page.locator('[data-action="latest"]').click();
  await page.waitForFunction(() => !state.contextMode && state.contextMessage === null);
  await page.evaluate(() => {window.__browserMemory.latestAndContextPassed = true;});
  await page.setViewportSize({width: 390, height: 844});
  await page.locator('[data-action="latest"]').click();
  const mobileBounds = await page.evaluate(() => ({
    buttonBottom: document.querySelector('[data-action="latest"]').getBoundingClientRect().bottom,
    footerTop: document.querySelector('.footer').getBoundingClientRect().top,
    scrollWidth: document.documentElement.scrollWidth, width: innerWidth
  }));
  if (mobileBounds.buttonBottom > mobileBounds.footerTop || mobileBounds.scrollWidth > mobileBounds.width)
    throw new Error('Mobile controls overlap or overflow: ' + JSON.stringify(mobileBounds));
  await page.screenshot({path: 'output/performance-webui-mobile.png', fullPage: false});
  await page.evaluate(result => {window.__browserMemory.mobileBounds = result;}, mobileBounds);
  await cdp.detach();
}
