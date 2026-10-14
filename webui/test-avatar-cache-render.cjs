const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, 'app.js'), 'utf8');
const initCall = '\ninit();\n';
assert.ok(source.includes(initCall));
const listeners = {};
const context = {
  URL,
  localStorage: {getItem: () => 'light'},
  document: {addEventListener(name, callback, capture) { listeners[name] = {callback, capture}; }, querySelector: () => ({setAttribute() {}, querySelector() { return null; }, querySelectorAll() { return []; }})},
  window: {addEventListener() {}, lucide: null},
  matchMedia: () => ({addEventListener() {}}),
  fetch: async () => ({ok: true, json: async () => ({})}),
};
vm.runInNewContext(source.replace(initCall,
  '\nglobalThis.__avatarSource = avatarSource; globalThis.__avatar = avatar; globalThis.__renderConversation = renderConversation; globalThis.__renderMessage = renderMessage; globalThis.__renderInspector = renderInspector; globalThis.__renderConversationRoster = renderConversationRoster; globalThis.__state = state;\n'), context);

const url = 'https://q.qlogo.cn/g?b=qq&nk=10001&s=100';
const wecomAvatar = 'https://wework.qpic.cn/avatar/synthetic';
assert.equal(context.__avatarSource(url), '/api/avatar?url=' + encodeURIComponent(url));
assert.equal(context.__avatarSource(wecomAvatar), '/api/avatar?url=' + encodeURIComponent(wecomAvatar));
for (const qceAvatar of [
  'https://q1.qlogo.cn/g?b=qq&nk=10001&s=640',
  'https://p.qlogo.cn/gh/10001/10001/640/',
]) {
  assert.equal(context.__avatarSource(qceAvatar), '/api/avatar?url=' + encodeURIComponent(qceAvatar));
}
assert.equal(context.__avatarSource('/api/avatar?url=fixture'), '/api/avatar?url=fixture');
assert.equal(context.__avatarSource('/api/contacts'), '');
assert.equal(context.__avatarSource('/api/avatar/../contacts'), '');
assert.equal(context.__avatarSource('/\\attacker.example/avatar.png'), '');
const localAvatar = 'hub-avatar://wechat/' + 'a'.repeat(64);
assert.equal(context.__avatarSource(localAvatar), '/api/avatar?url=' + encodeURIComponent(localAvatar));
const localQQAvatar = 'hub-avatar://qq/' + 'a'.repeat(64);
assert.equal(context.__avatarSource(localQQAvatar), '/api/avatar?url=' + encodeURIComponent(localQQAvatar));
assert.equal(context.__avatarSource('hub-avatar://wechat/' + 'A'.repeat(64)), '');
assert.equal(context.__avatarSource('hub-avatar://qq/' + 'A'.repeat(64)), '');
assert.equal(context.__avatarSource(localAvatar + '?remote=1'), '');
assert.match(context.__avatar('Alice', 'wechat', localAvatar), /src="\/api\/avatar\?url=hub-avatar%3A%2F%2Fwechat%2F/);
let avatarUrlConversions = 0;
const countedAvatarUrl = {toString() { avatarUrlConversions += 1; return url; }};
assert.match(context.__avatar('Alice', 'qq', countedAvatarUrl), /class="avatar-image"/);
assert.equal(avatarUrlConversions, 1);
const conversationHtml = context.__renderConversation({title: 'Fixture', platform: 'wechat', avatar_url: localAvatar, last_timestamp: 1, message_count: 2});
assert.match(conversationHtml, /<img class="avatar-image" src="\/api\/avatar\?url=hub-avatar%3A%2F%2Fwechat%2F[a-f0-9]{64}" alt="" loading="lazy">/);
const messageHtml = context.__renderMessage({id: 'synthetic-message', platform: 'wechat', sender_name: 'Synthetic sender', sender_avatar_url: localAvatar, text: 'Synthetic message', timestamp: 1});
assert.match(messageHtml, /<div class="message /);
assert.match(messageHtml, /<img class="avatar-image" src="\/api\/avatar\?url=hub-avatar%3A%2F%2Fwechat%2F[a-f0-9]{64}" alt="" loading="lazy">/);
assert.match(messageHtml, /Synthetic sender/);
const conversation = {id: 'synthetic-conversation', title: 'Synthetic conversation', platform: 'wechat', kind: 'group', avatar_url: localAvatar, last_timestamp: 1, message_count: 1};
context.__state.settings = {capabilities: {}};
context.__state.messages = [];
const inspectorHtml = context.__renderInspector(conversation);
assert.match(inspectorHtml, /class="inspector-profile"><span class="avatar wechat large">/);
assert.match(inspectorHtml, /<img class="avatar-image" src="\/api\/avatar\?url=hub-avatar%3A%2F%2Fwechat%2F[a-f0-9]{64}" alt="" loading="lazy">/);
context.__state.settings.capabilities.wechat_send = {available: true, enabled: true};
const wechatInspectorHtml = context.__renderInspector({...conversation, kind: 'private'});
assert.match(wechatInspectorHtml, /准备微信文本发送/);
assert.doesNotMatch(wechatInspectorHtml, /准备 QQ 文本发送/);
const rosterHtml = context.__renderConversationRoster([{id: 'synthetic-member', name: 'Synthetic member', avatar_url: localAvatar}], 'wechat');
assert.match(rosterHtml, /class="member-list roster-list"/);
assert.match(rosterHtml, /<img class="avatar-image" src="\/api\/avatar\?url=hub-avatar%3A%2F%2Fwechat%2F[a-f0-9]{64}" alt="" loading="lazy">/);
assert.equal(context.__avatarSource('https://attacker.example/avatar.png'), '');
assert.equal(context.__avatarSource('http://wx.qlogo.cn/avatar'), '');
assert.equal(context.__avatarSource('https://user@q.qlogo.cn/avatar'), '');
assert.equal(context.__avatarSource('https://q.qlogo.cn:444/avatar'), '');
assert.equal(context.__avatarSource('https://p.qlogo.cn.attacker.example/avatar'), '');
assert.match(context.__avatar('Alice', 'qq', url), /src="\/api\/avatar\?url=/);
assert.match(context.__avatar('Alice', 'qq', url), /class="avatar-image"/);
assert.doesNotMatch(context.__avatar('Alice', 'qq', 'https://attacker.example/avatar.png'), /<img/);
assert.match(context.__avatar('Synthetic WeCom', 'wecom', wecomAvatar), /class="avatar-image"/);
assert.match(context.__renderConversation({title: 'Synthetic WeCom', platform: 'wecom', avatar_url: wecomAvatar, last_timestamp: 1, message_count: 1}), /class="avatar-image"/);
assert.match(context.__renderConversationRoster([{id: 'synthetic-member', name: 'Synthetic member', avatar_url: wecomAvatar}], 'wecom'), /class="avatar-image"/);
let removed = false;
listeners.error.callback({target: {matches: selector => selector === 'img.avatar-image', remove: () => { removed = true; }}});
assert.equal(listeners.error.capture, true);
assert.equal(removed, true);
