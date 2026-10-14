const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, 'app.js'), 'utf8');
const initCall = '\ninit();\n';
assert.ok(source.includes(initCall));
const overlay = {innerHTML: ''};
let resolveFetch;
const context = {
  URL, URLSearchParams, Intl,
  localStorage: {getItem: () => 'light'},
  document: {addEventListener() {}, querySelector: selector => selector === '#overlay' ? overlay : null,
    querySelectorAll: () => []},
  window: {addEventListener() {}, lucide: null},
  matchMedia: () => ({addEventListener() {}}),
  fetch: () => new Promise(resolve => { resolveFetch = resolve; }),
};
vm.runInNewContext(source.replace(initCall,
  '\nglobalThis.__state = state; globalThis.__showRoster = showConversationRoster;\n'), context);

(async () => {
  const state = context.__state;
  state.selected = {platform: 'wechat', account_id: 'account-a', id: 'group-a', kind: 'group'};
  const pending = context.__showRoster();
  state.selected = {platform: 'wechat', account_id: 'account-a', id: 'group-b', kind: 'group'};
  resolveFetch({ok: true, json: async () => ({total: 1, items: [{id: 'member-a', name: 'Member A'}]})});
  await pending;

  assert.match(overlay.innerHTML, /当前会话已切换/);
  assert.deepEqual(Array.from(state.wechatRoster), []);
  assert.equal(state.groupRosterKey, '');
  console.log('PASS stale group roster response cannot be exported for a switched conversation');
})().catch(error => {console.error(error);process.exitCode = 1;});
