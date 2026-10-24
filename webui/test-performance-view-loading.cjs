const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const context = {
  URL, URLSearchParams, localStorage: {getItem: () => 'light'},
  document: {addEventListener() {}, querySelector: () => null, querySelectorAll: () => []},
  window: {addEventListener() {}}, matchMedia: () => ({addEventListener() {}}),
  setTimeout, clearTimeout,
};
const source = fs.readFileSync(path.join(__dirname, 'app.js'), 'utf8');
vm.runInNewContext(source.replace('\ninit();\n', '\nglobalThis.__test = {state, loadView};\nrenderView = () => {};startMediaPreviews = () => {};\n'), context);
const {state, loadView} = context.__test;

async function pendingView(view, tab, expected) {
  state.view = view;state.statsTab = tab;
  const calls = [], releases = [];
  context.fetch = url => {
    calls.push(url.split('?')[0]);
    return new Promise(resolve => releases.push(value => resolve({ok: true, json: async () => value})));
  };
  const pending = loadView();
  await new Promise(setImmediate);
  assert.deepEqual(calls.slice().sort(), expected.slice().sort(), `${view}/${tab}: independent requests must start before any response`);
  return {pending, releases};
}

(async () => {
  for (const tab of ['private', 'groups', 'annual', 'relationships', 'daily']) {
    const route = {private: '/api/analytics/private', groups: '/api/analytics/groups', annual: '/api/reports/annual', relationships: '/api/analytics/relationships', daily: '/api/analytics/self-sent-daily'}[tab];
    const {pending, releases} = await pendingView('stats', tab, ['/api/stats', route]);
    releases.forEach(resolve => resolve({items: []}));await pending;
    assert.equal(state.error, '');
  }
  state.settings = {capabilities: {advanced_analytics: {enabled: false}}};
  let loading = await pendingView('stats', 'daily', ['/api/stats']);
  loading.releases.forEach(resolve => resolve({}));await loading.pending;

  loading = await pendingView('media', 'overview', ['/api/media', '/api/accounts']);
  loading.releases[0]({items: [{id: 'media'}], total: 1});
  loading.releases[1]({items: [{account_id: 'account'}]});await loading.pending;
  assert.equal(state.items[0].id, 'media');assert.equal(state.accountScopes[0].account_id, 'account');

  state.settings = {capabilities: {qq_send: {enabled: true}, wechat_send: {enabled: true}, wecom_bot_send: {enabled: true}}};
  loading = await pendingView('settings', 'overview', ['/api/backups', '/api/mcp', '/api/operations', '/api/wecom-bot/identity', '/api/sends', '/api/wecom-bot-sends', '/api/wechat-sends']);
  loading.releases.forEach((resolve, index) => resolve({items: [{id: index, created_at: index}]}));await loading.pending;
  assert.deepEqual(Array.from(state.pendingSends, item => item.id), [6, 5, 4]);

  for (const view of ['media', 'settings', 'stats']) {
    state.view = view;state.statsTab = 'daily';
    const releases = [];
    context.fetch = () => new Promise(resolve => releases.push(value => resolve({ok: true, json: async () => value})));
    const pending = loadView();
    state.view = 'search';state.filters.q = '';
    await loadView();
    const snapshot = JSON.stringify({items: state.items, accounts: state.accountScopes, mcp: state.mcp, stats: state.stats});
    releases.forEach(resolve => resolve({items: [{id: 'stale'}], total: 100}));await pending;
    assert.equal(JSON.stringify({items: state.items, accounts: state.accountScopes, mcp: state.mcp, stats: state.stats}), snapshot, `${view}: stale batches must not update the new view`);
  }
  console.log('PASS 5 statistics tabs (2 requests), media (2), and settings (7) launch in one batch; disabled capabilities and stale responses remain isolated');
})().catch(error => {console.error(error);process.exitCode = 1;});
