const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, 'app.js'), 'utf8');
const initCall = '\ninit();\n';
const context = {
  URL,
  location: {origin: 'http://127.0.0.1'},
  localStorage: {getItem: () => 'light'},
  document: {addEventListener: () => {}},
  window: {addEventListener: () => {}},
  matchMedia: () => ({addEventListener: () => {}}),
};
vm.runInNewContext(source.replace(initCall,
  `\nglobalThis.__renderSendRequest = renderSendRequest;
globalThis.__renderConversationContent = renderConversationContent;
globalThis.__state = state;
globalThis.__loadView = loadView;
globalThis.__configureSettingsLoad = (capabilities, apiStub) => {
  state.view = 'settings';
  state.settings.capabilities = capabilities;
  state.pendingSends = [{request_id: 'stale'}];
  state.request = 0;
  state.backups = [];
  state.mcp = null;
  state.operations = [];
  releaseInactiveViewData = () => {};
  renderView = () => {};
  api = apiStub;
};
globalThis.__renderSettingsBase = renderSettingsBase;
globalThis.__reviewSend = reviewSend;
globalThis.__setPendingSends = items => { state.pendingSends = items; state.sendReview = null; };
modal = (...args) => { globalThis.__reviewModal = args; };
`), context);

const base = {request_id: 'request-12345678', account_id: '12345678',
  conversation_id: '87654321', conversation_type: 'private', text: 'hello',
  created_at: 0};
const pending = context.__renderSendRequest({...base, status: 'pending_approval'}, true);
assert.match(pending, /审核发送请求/);
assert.match(pending, /待审批/);

const unknown = context.__renderSendRequest({...base, status: 'unknown', error_code: 'timeout'}, true);
assert.match(unknown, /结果未知/);
assert.match(unknown, /请先在 ?QQ ?中核实，避免重复发送/);
assert.doesNotMatch(unknown, /data-action=/);
assert.doesNotMatch(unknown, /重试/);

const sent = context.__renderSendRequest({...base, status: 'sent', message_id: 'message-1'}, true);
assert.match(sent, /已发送/);
assert.match(sent, /QQ 消息 ID：message-1/);
assert.doesNotMatch(sent, /data-action=/);

const qqReview = {...base, platform: 'qq', conversation_id: '87654321', text: 'hello <world>'};
context.__setPendingSends([qqReview]);
context.__reviewSend(qqReview.request_id);
const [qqTitle, qqBody, qqFooter] = context.__reviewModal;
assert.match(qqTitle, /审核 QQ 发送/);
assert.match(qqBody, /当前登录的本机 QQ 账号/);
assert.match(qqBody, /87654321/);
assert.match(qqBody, /hello &lt;world&gt;/);
assert.match(qqBody, new RegExp(qqReview.request_id));
assert.match(qqFooter, new RegExp(`data-action="reject-send:${qqReview.request_id}"`));
assert.match(qqFooter, new RegExp(`data-action="approve-send:${qqReview.request_id}"`));

const localUidReview = {...base, request_id: 'local-uid-87654321', platform: 'qq',
  conversation_id: '2747277822', source_conversation_id: 'u_-PBswiplK-7J7bmaQLA-mA'};
context.__setPendingSends([localUidReview]);
context.__reviewSend(localUidReview.request_id);
const [, localBody] = context.__reviewModal;
assert.match(localBody, /本地会话/);
assert.match(localBody, /u_-PBswiplK-7J7bmaQLA-mA/);
assert.match(localBody, /2747277822/);
// Without a resolved source spelling, no extra row is invented.
context.__setPendingSends([qqReview]);
context.__reviewSend(qqReview.request_id);
const [, qqOnlyBody] = context.__reviewModal;
assert.doesNotMatch(qqOnlyBody, /本地会话/);

const botReview = {...base, request_id: 'bot-request-87654321', platform: 'wecom_bot',
  account_id: 'bot-123', conversation_id: 'member-456', conversation_type: 'group', text: '',
  attachment: {filename: 'invoice.pdf', media_type: 'file', size: 2048}};
context.__setPendingSends([botReview]);
context.__reviewSend(botReview.request_id);
const [botTitle, botBody, botFooter] = context.__reviewModal;
assert.match(botTitle, /审核企微机器人发送/);
assert.match(botBody, /企业微信智能机器人（非当前员工身份）/);
assert.match(botBody, /群聊/);
assert.match(botBody, /member-456/);
assert.match(botBody, /invoice[.]pdf/);
assert.match(botBody, /2,048 bytes/);
assert.match(botBody, new RegExp(`/api/wecom-bot-sends/${botReview.request_id}/attachment-preview`));
assert.match(botBody, /下载文件后核验/);
assert.match(botBody, new RegExp(botReview.request_id));
assert.match(botFooter, new RegExp(`data-action="reject-send:${botReview.request_id}"`));
assert.match(botFooter, new RegExp(`data-action="approve-send:${botReview.request_id}"`));

const imageReview = {...botReview, request_id: 'bot-image-87654321',
  attachment: {filename: 'image.png', media_type: 'image', size: 1024}};
context.__setPendingSends([imageReview]);
context.__reviewSend(imageReview.request_id);
const [, imageBody] = context.__reviewModal;
assert.match(imageBody, new RegExp(`/api/wecom-bot-sends/${imageReview.request_id}/attachment-preview`));
assert.match(imageBody, /alt="待发送图片：image\.png"/);


const wechatReview = {...base, request_id: 'wx-request-87654321', platform: 'wechat',
  account_id: 'wxid_self0001', conversation_id: 'wxid_friend01', text: 'hello <world>'};
context.__setPendingSends([wechatReview]);
context.__reviewSend(wechatReview.request_id);
const [wxTitle, wxBody, wxFooter] = context.__reviewModal;
assert.match(wxTitle, /审核个人微信发送/);
assert.match(wxBody, /当前登录的本机微信账号（GUI 拟人操作）/);
assert.match(wxBody, /wxid_friend01/);
assert.match(wxBody, /hello &lt;world&gt;/);
assert.match(wxBody, new RegExp(wechatReview.request_id));
assert.match(wxBody, /同名联系人.*直接拒绝/);
assert.match(wxBody, /输入框.*未发送的草稿/);
assert.match(wxFooter, new RegExp(`data-action="reject-send:${wechatReview.request_id}"`));
assert.match(wxFooter, new RegExp(`data-action="approve-send:${wechatReview.request_id}"`));

const wechatUnknown = context.__renderSendRequest(
  {...wechatReview, status: 'unknown', error_code: 'wechat_receipt_missing'}, true);
assert.match(wechatUnknown, /结果未知/);
assert.match(wechatUnknown, /请先在 ?微信 ?中核实，避免重复发送/);
assert.doesNotMatch(wechatUnknown, /data-action=/);

const wechatSent = context.__renderSendRequest(
  {...wechatReview, status: 'sent', message_id: 'wxid_friend01:message_1.db:12'}, true);
assert.match(wechatSent, /已发送/);
assert.match(wechatSent, /本机微信 ?消息 ID：wxid_friend01:message_1[.]db:12/);

const wechatChatroom = context.__renderSendRequest(
  {...wechatReview, request_id: 'wx-room-87654321', conversation_id: '12345@chatroom',
    conversation_type: 'group', status: 'sent'}, false);
assert.match(wechatChatroom, /群聊/);
assert.doesNotMatch(wechatChatroom, /data-action="review-send/);

context.__state.messages = [];
context.__state.loading = false;
context.__state.messageFilter = {q: '', type: '', start: '', end: ''};
context.__state.settings.capabilities = {qq_send: {enabled: true}, wecom_bot_send: {enabled: true}};
context.__state.wecomBotIdentity = {configured: true};
context.__state.settings.capabilities.wechat_send = {enabled: false};
const wechatDisabled = context.__renderConversationContent({platform: 'wechat',
  account_id: 'wxid_self0001', id: 'wxid_friend01', title: '测试会话', kind: 'private',
  message_count: 1});
assert.doesNotMatch(wechatDisabled, /data-action="prepare-send"/);
context.__state.settings.capabilities.wechat_send = {enabled: true};
for (const [platform, label] of [['qq', '准备 QQ 文本发送'], ['wecom', '准备企微机器人发送'],
  ['wechat', '准备微信文本发送']]) {
  const html = context.__renderConversationContent({platform, account_id: 'account', id: 'conversation',
    title: '测试会话', kind: 'private', message_count: 1});
  assert.match(html, new RegExp(`data-action="prepare-send" title="${label}" aria-label="${label}"`));
}

(async () => {
  const calls = [];
  context.__configureSettingsLoad({qq_send: {available: true, enabled: false},
    wecom_bot_send: {available: true, enabled: false}},
    async (path) => {
      calls.push(path);
      if (path === '/api/sends') throw new Error('403 capability_disabled');
      if (path === '/api/backups') return {items: ['backup']};
      if (path === '/api/mcp') return {available: true};
      if (path === '/api/operations') return {items: ['operation']};
      throw new Error(`unexpected API call: ${path}`);
    });
  await context.__loadView();
  assert.deepEqual(calls, ['/api/backups', '/api/mcp', '/api/operations']);

  const queued = [];
  context.__configureSettingsLoad({qq_send: {available: true, enabled: true},
    wecom_bot_send: {available: true, enabled: true}, wechat_send: {available: true, enabled: true}},
    async (path) => {
      queued.push(path);
      if (path === '/api/wecom-bot/identity') return {configured: true};
      if (path === '/api/backups') return {items: ['backup']};
      if (path === '/api/mcp') return {available: true};
      if (path === '/api/operations') return {items: ['operation']};
      if (path === '/api/sends') return {items: [{request_id: 'qq-1'}]};
      if (path === '/api/wecom-bot-sends') return {items: [{request_id: 'bot-1'}]};
      if (path === '/api/wechat-sends') return {items: [{request_id: 'wechat-1'}]};
      throw new Error(`unexpected API call: ${path}`);
    });
  await context.__loadView();
  assert.deepEqual([...queued].sort(), ['/api/backups', '/api/mcp', '/api/operations',
    '/api/sends', '/api/wecom-bot-sends', '/api/wechat-sends', '/api/wecom-bot/identity'].sort());
  assert.deepEqual(Array.from(context.__state.pendingSends).map(item => item.request_id).sort(),
    ['wechat-1', 'bot-1', 'qq-1'].sort());
  assert.equal(context.__state.error, '');
  assert.deepEqual(Array.from(context.__state.backups), ['backup']);
  assert.deepEqual(Array.from(context.__state.operations), ['operation']);

  context.__state.view = 'settings';
  context.__state.platform = 'all';
  context.__state.settings = {capabilities: {wecom_bot_send: {enabled: false}}};
  let settingsHtml = context.__renderSettingsBase();
  assert.match(settingsHtml, /QQ 发送通道/);
  assert.match(settingsHtml, /检查只会向本机 NapCat 请求登录身份，不会发送消息/);
  assert.match(settingsHtml, /data-action="qq-send-readiness"/);
  context.__state.settings.capabilities.wechat_send = {available: true, enabled: true};
  assert.match(context.__renderSettingsBase(), /data-action="wechat-send-readiness"/);
  context.__state.wechatSendReadiness = {state: 'ready', account_id: 'wxid_self0001',
    input_available: true, checked_at: 1760000000};
  settingsHtml = context.__renderSettingsBase();
  assert.match(settingsHtml, /个人微信发送通道/);
  assert.match(settingsHtml, /data-action="wechat-send-readiness"/);
  assert.match(settingsHtml, /可发送 · 本机微信 wxid_self0001/);
  context.__state.wechatSendReadiness = {state: 'window_not_found', input_available: true};
  assert.match(context.__renderSettingsBase(), /未找到微信窗口/);
  context.__state.wechatSendReadiness = null;
  context.__state.settings.capabilities.wechat_send = {available: true, enabled: false};
  assert.match(context.__renderSettingsBase(), /发送功能已关闭/);
  assert.match(settingsHtml, /先在上方启用“企业微信机器人消息发送”/);
  assert.doesNotMatch(settingsHtml, /wecom-bot-credentials-form/);
  context.__state.qqSendReadiness = {state: 'online', account_id: '12345678', nickname: 'Synthetic',
    endpoint_configured: true, endpoint_reachable: true, checked_at: 1760000000};
  settingsHtml = context.__renderSettingsBase();
  assert.match(settingsHtml, /已登录 · Synthetic · QQ 12345678/);
  assert.match(settingsHtml, /OneBot 本机端点可达/);
  context.__state.settings.capabilities.wecom_bot_send.enabled = true;
  context.__state.wecomBotIdentity = {configured: true, bot_id: 'bot-synthetic'};
  settingsHtml = context.__renderSettingsBase();
  assert.match(settingsHtml, /wecom-bot-credentials-form/);
  assert.match(settingsHtml, /bot-synthetic/);
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
