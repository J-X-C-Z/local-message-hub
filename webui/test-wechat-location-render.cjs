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
  '\nglobalThis.__renderMessage = renderMessage; globalThis.__openWechatQuote = openWechatQuote; globalThis.__state = state;\n'), context);

const html = context.__renderMessage({
  id: 'wx-location', platform: 'wechat', type: 'location', text: '[位置]',
  sender_name: 'Alice', timestamp: 1, extra: {location: {
    title: '<Office>', address: 'A & B', latitude: 22.3, longitude: 114.2,
  }},
});
assert.match(html, /<div class="wechat-location-card">/);
assert.match(html, /&lt;Office&gt;/);
assert.match(html, /A &amp; B/);
assert.match(html, /22\.30000, 114\.20000/);
assert.doesNotMatch(html, /<a\b|<iframe\b|https?:\/\//);

const quoteHtml = context.__renderMessage({
  id: 'wx-quote', platform: 'wechat', type: 'link', text: 'Shared link',
  sender_name: 'Bob', timestamp: 1,
  extra: {wechat_quote: {sender: '<Alice>', content: 'quoted <script>alert(1)</script>'}},
});
assert.match(quoteHtml, /&lt;Alice&gt;/);
assert.match(quoteHtml, /quoted &lt;script&gt;alert\(1\)&lt;\/script&gt;/);
assert.doesNotMatch(quoteHtml, /<script>/);
const navigableQuote = context.__renderMessage({
  id: 'wx-quote-link', platform: 'wechat', type: 'link', text: 'Reply', timestamp: 1,
  extra: {wechat_quote: {sender: '<Alice>', content: '<Target>', target_message_id: 'room:target',
    preview: {type: 'image', media_url: '/local-media/abcdefghijklmnopqrstuvwxyzABCDEF'}}},
});
assert.match(navigableQuote, /data-action="open-wechat-quote"/);
assert.match(navigableQuote, /查看原消息/);
assert.match(navigableQuote, /class="quote-media-preview" src="\/local-media\//);
assert.match(navigableQuote, /&lt;Alice&gt;/);
assert.match(navigableQuote, /&lt;Target&gt;/);
assert.doesNotMatch(navigableQuote, /server_id|12345/);
const remoteQuotePreview = context.__renderMessage({
  id: 'wx-quote-remote', platform: 'wechat', type: 'link', text: 'Reply', timestamp: 1,
  extra: {wechat_quote: {sender: 'Alice', content: 'Target', target_message_id: 'room:target',
    preview: {type: 'image', media_url: 'https://private.invalid/image'}}},
});
assert.doesNotMatch(remoteQuotePreview, /private\.invalid|quote-media-preview/);
const quoteWithoutId = context.__renderMessage({
  id: 'wx-quote-no-id', platform: 'wechat', type: 'link', text: 'Reply', timestamp: 1,
  extra: {wechat_quote: {sender: 'Alice', content: 'Target'}},
});
assert.doesNotMatch(quoteWithoutId, /open-wechat-quote:/);
assert.match(quoteWithoutId, /<blockquote class="qq-rich-reply">/);
const conversation = {id: 'room', platform: 'wechat', account_id: 'account', title: 'Room', kind: 'group'};
context.__state.selected = conversation;
let requested;
let opened;
context.api = async (path, params) => {requested = {path, params}; return {target_message_id: 'room:target'};};
context.openMessageContext = async message => {opened = message;};
async function testQuoteJump() {
  await context.__openWechatQuote('room:reply');
  assert.equal(requested.path, '/api/quoted-message');
  assert.deepEqual(JSON.parse(JSON.stringify(requested.params)), {
    platform: 'wechat', account_id: 'account', conversation_id: 'room',
    reference_message_id: 'room:reply',
    before: 8, after: 8,
  });
  assert.equal(opened.id, 'room:target');
  assert.equal(opened.account_id, 'account');
}

const noCoordinates = context.__renderMessage({
  id: 'wx-location-no-coords', platform: 'wechat', type: 'location', text: '[位置]',
  sender_name: 'Alice', timestamp: 1,
  extra: {location: {title: 'Nearby', address: '', latitude: 91, longitude: 181}},
});
assert.match(noCoordinates, /Nearby/);
assert.doesNotMatch(noCoordinates, /91\.00000|181\.00000/);
testQuoteJump().then(() => console.log('PASS WeChat quote navigation and location rendering'))
  .catch(error => {console.error(error); process.exitCode = 1;});
