const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, 'app.js'), 'utf8');
const context = {
  URL, localStorage: {getItem: () => 'light'},
  document: {addEventListener: () => {}, querySelector: () => null},
  window: {addEventListener: () => {}}, matchMedia: () => ({addEventListener: () => {}}),
};
vm.runInNewContext(source.replace('\ninit();\n', `
globalThis.__state = state;
let captured;
modal = (...args) => { captured = args; };
globalThis.__showComposer = () => {
  state.selected = {platform: 'qq', account_id: '2680455179',
    id: 'u_-PBswiplK-7J7bmaQLA-mA', kind: 'private', title: 'Local peer'};
  state.settings.capabilities = {qq_send: {available: true, enabled: true},
    wechat_send: {available: true, enabled: true}};
  showSendComposer();
  return captured;
};
globalThis.__showWeChatComposer = () => {
  state.selected = {platform: 'wechat', account_id: 'local-account',
    id: 'wx-peer', kind: 'private', title: 'Local WeChat peer'};
  showSendComposer();
  return captured;
};
globalThis.__showWeChatGroupComposer = () => {
  state.selected = {platform: 'wechat', account_id: 'local-account',
    id: '44766057488@chatroom', kind: 'group', title: 'Local WeChat group'};
  showSendComposer();
  return captured;
};
globalThis.__sendRouteBase = sendRouteBase;
`), context);

const [title, form] = context.__showComposer();
assert.match(title, /准备 QQ 文本发送/);
// A local uid conversation must survive the composer's own validation.
const target = /<input name="conversation_id" ([^>]*)>/.exec(form);
assert.ok(target, 'conversation input is missing');
assert.match(target[1], /value="u_-PBswiplK-7J7bmaQLA-mA"/);
assert.match(target[1], /pattern="\[A-Za-z0-9_\.-\]\{1,64\}"/);
assert.doesNotMatch(target[1], /inputmode="numeric"/);
assert.match(form, /可填本地会话 UID/);
// The sender account stays a plain QQ number.
const account = /<input name="account_id" ([^>]*)>/.exec(form);
assert.match(account[1], /pattern="\[0-9\]\{5,20\}"/);

const [wechatTitle, wechatForm] = context.__showWeChatComposer();
assert.match(wechatTitle, /准备个人微信文本发送/);
assert.match(wechatForm, /data-send-platform="wechat"/);
assert.equal(context.__sendRouteBase('wechat'), '/api/wechat-sends');
const [, wechatGroupForm] = context.__showWeChatGroupComposer();
const wechatGroupTarget = /<input name="conversation_id" ([^>]*)>/.exec(wechatGroupForm);
assert.ok(wechatGroupTarget[1].includes('pattern="[A-Za-z0-9_-]{1,64}(@chatroom)?"'));
assert.match(wechatGroupTarget[1], /maxlength="73"/);
assert.match(wechatGroupTarget[1], /value="44766057488@chatroom"/);
