const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, 'app.js'), 'utf8');
const listeners = new Map();
const calls = [];
const context = {
  localStorage: {getItem: () => 'light'},
  document: {hidden: true, addEventListener: () => {}},
  window: {addEventListener: () => {}},
  matchMedia: () => ({addEventListener: () => {}}),
  EventSource: class {
    constructor() { this.closed = false; }
    addEventListener(name, callback) { listeners.set(name, callback); }
    close() { this.closed = true; }
  },
  fetch: async (url, options) => {
    calls.push([url, options]);
    return {ok: true, json: async () => ({shown: true})};
  },
};
vm.runInNewContext(source.replace('\ninit();\n',
  '\nglobalThis.__state = state; globalThis.__sync = syncNotificationStream; globalThis.__test = sendTestNotification;\n'), context);
context.toast = () => {};
context.__state.settings = {capabilities: {notifications: {enabled: true}}, notification_filters: {
  platforms: ['wechat', 'wecom', 'qq'], mode: 'all', style: 'normal',
  allowed_conversations: [], muted_conversations: [],
}};
context.__sync();
listeners.get('message')({lastEventId: '19', data: JSON.stringify({
  platform: 'qq', account_id: 'private-account', conversation_id: 'private-room',
  conversation_title: 'Private title', text: 'PRIVATE_MESSAGE_BODY',
})});
assert.equal(calls.length, 1);
assert.equal(calls[0][0], '/api/notifications/message');
assert.equal(calls[0][1].body, '{"event_id":19}');
assert.doesNotMatch(calls[0][1].body, /PRIVATE_MESSAGE_BODY|private-account|private-room/);
context.__test();
setImmediate(() => {
  assert.equal(calls.length, 2);
  assert.equal(calls[1][0], '/api/notifications/test');
  assert.equal(calls[1][1].body, '{}');
  console.log('PASS notification requests contain only an opaque event ID or fixed test action');
});
