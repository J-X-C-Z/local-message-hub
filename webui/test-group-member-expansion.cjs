const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, 'app.js'), 'utf8');
const initCall = '\ninit();\n';
assert.ok(source.includes(initCall));
const content = {setAttribute() {}, innerHTML: ''};
const requests = [];
const context = {
  URL, URLSearchParams, Intl,
  localStorage: {getItem: () => 'light'},
  document: {addEventListener() {}, querySelector: selector => selector === '#content' ? content : null},
  window: {addEventListener() {}, lucide: null},
  matchMedia: () => ({addEventListener() {}}),
  fetch: async url => {
    requests.push(new URL(url, 'http://localhost'));
    return {ok: true, json: async () => ({items: [{
      platform: 'wechat', account_id: 'account-1', conversation_id: 'group-1',
      members: Array.from({length: 13}, (_, index) => ({name: `Member ${index + 1}`, message_count: 20 - index})),
    }]})};
  },
};
vm.runInNewContext(source.replace(initCall,
  '\nglobalThis.__state = state; globalThis.__renderRows = renderGroupProfileRows; globalThis.__toggleMembers = toggleGroupMembers;\n'), context);

const state = context.__state;
state.view = 'stats';
state.statsTab = 'groups';
state.loading = false;
state.settings = {capabilities: {advanced_analytics: {enabled: true, available: true}}};
state.stats = {};
state.groupProfiles = [{platform: 'wechat', account_id: 'account-1', conversation_id: 'group-1',
  title: 'Group', member_count: 13, observed_member_count: 13, message_count: 100,
  sent_count: 1, received_count: 99, last_timestamp: 1,
  members: Array.from({length: 12}, (_, index) => ({name: `Member ${index + 1}`, message_count: 20 - index}))}];
state.groupMemberLists = new Map();
const key = JSON.stringify(['wechat', 'account-1', 'group-1']);

let html = context.__renderRows();
assert.match(html, /Member 12/);
assert.doesNotMatch(html, /Member 13/);
assert.match(html, /查看完整排行/);

(async () => {
  await context.__toggleMembers(key);
  html = content.innerHTML;
  assert.match(html, /Member 13/);
  assert.equal(requests.length, 1);
  assert.equal(requests[0].pathname, '/api/analytics/groups');
  assert.equal(requests[0].searchParams.get('member_limit'), '5000');
  assert.equal(requests[0].searchParams.get('platform'), 'wechat');
  assert.equal(requests[0].searchParams.get('account_id'), 'account-1');
  assert.equal(requests[0].searchParams.get('conversation_id'), 'group-1');
  await context.__toggleMembers(key);
  html = content.innerHTML;
  assert.doesNotMatch(html, /Member 13/);
  assert.match(html, /查看完整排行/);
  assert.equal(requests.length, 1);
  state.groupMemberLists.clear();
  let finish;
  context.fetch = () => new Promise(resolve => {finish = resolve;});
  const pending = context.__toggleMembers(key);
  state.view = 'settings';state.request++;
  state.groupMemberLists.clear();
  const before = content.innerHTML;
  finish({ok: true, json: async () => ({items: [{platform: 'wechat', account_id: 'account-1',
    conversation_id: 'group-1', members: [{name: 'stale'}]}]})});
  await pending;
  assert.equal(state.groupMemberLists.size, 0);
  assert.equal(content.innerHTML, before);
  console.log('PASS on-demand full group member ranking');
})().catch(error => {console.error(error);process.exitCode = 1;});
