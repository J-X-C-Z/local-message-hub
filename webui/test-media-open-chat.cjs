const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, 'app.js'), 'utf8');
const context = {
  URL,
  localStorage: {getItem: () => 'light'},
  document: {addEventListener: () => {}},
  window: {addEventListener: () => {}},
  matchMedia: () => ({addEventListener: () => {}}),
};
vm.runInNewContext(source.replace('\ninit();\n',
  '\nglobalThis.__renderListing = renderListing; globalThis.__state = state;\n'), context);

const state = context.__state;
state.view = 'media';
state.platform = 'all';
state.loading = false;
state.filters = {q: '', type: '', source: 'all', start: '', end: ''};
state.items = [{id: 'message-1', platform: 'qq', account_id: 'fixture', conversation_id: 'room-1',
  conversation_title: 'Fixture chat', timestamp: 1, media_entry: {type: 'image', resource_kind: 'message'}}];
assert.match(context.__renderListing(), /data-action="open-media-chat:0"/);

for (const resourceKind of ['moment', 'qq_cache']) {
  state.items[0].media_entry.resource_kind = resourceKind;
  assert.doesNotMatch(context.__renderListing(), /open-media-chat/);
}
state.filters.source = 'qq-cache';
state.filters.start = '2026-10-01';
state.filters.end = '2026-10-02';
const qqCacheListing = context.__renderListing();
assert.match(qqCacheListing, /name="start"[^>]*value="2026-10-01"/);
assert.match(qqCacheListing, /name="end"[^>]*value="2026-10-02"/);
console.log('PASS media chat-context affordance scope');
