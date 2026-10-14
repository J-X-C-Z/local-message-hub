const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, 'app.js'), 'utf8');
const calls = [];
const context = {
  URL,
  localStorage: {getItem: () => 'light'},
  document: {addEventListener: () => {}},
  window: {addEventListener: () => {}},
  matchMedia: () => ({addEventListener: () => {}}),
  fetch: async (url, options) => {
    calls.push({url, options});
    return {ok: true, json: async () => ({message: 'started'})};
  },
};
vm.runInNewContext(source.replace('\ninit();\n',
  '\nglobalThis.__sync = sync; globalThis.__state = state; globalThis.__renderConversationContent = renderConversationContent;\n'), context);
context.renderShell = () => {};
context.toast = () => {};
context.refreshSources = async () => {};

const selected = {id: 'opaque-conversation-id', account_id: 'qq-account', platform: 'qq', title: 'Fixture', kind: 'private'};
context.__state.settings = {capabilities: {sync: {enabled: true}}};
context.__state.messages = [];
const markup = context.__renderConversationContent(selected);
assert.match(markup, /data-action="sync-conversation"/);
assert.match(markup, /仅重新同步此 QQ 会话/);

context.__sync('qq', selected.id).then(() => {
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, '/api/sync');
  assert.deepEqual(JSON.parse(calls[0].options.body), {
    platform: 'qq', conversation_id: selected.id,
  });
  console.log('PASS QQ targeted sync is surfaced and submits one conversation identity');
}).catch(error => {console.error(error);process.exitCode = 1;});
