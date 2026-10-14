const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, 'app.js'), 'utf8');
const listeners = new Map();
const documentListeners = new Map();
const windowListeners = new Map();
const rememberListener = (target, name, callback) => {
  const callbacks = target.get(name) || [];
  callbacks.push(callback);
  target.set(name, callbacks);
};
const dispatch = (target, name) => {
  for (const callback of target.get(name) || []) callback();
};
const notifications = [];
let eventSource;
let focused = false;
let rendered = false;
let loaded = false;
const appRoot = {children: ['synthetic private chat'], replaceChildren(...children) { this.children = children; }};
const toastRoot = {children: ['synthetic toast'], replaceChildren(...children) { this.children = children; }};
const overlayRoot = {children: ['synthetic dialog'], replaceChildren(...children) { this.children = children; }};
class NotificationMock {
  static permission = 'granted';
  static requests = 0;
  static requestResult = 'granted';
  static async requestPermission() { this.requests++; return this.requestResult; }
  constructor(title, options) { this.title = title; this.options = options; notifications.push(this); }
  close() { this.closed = true; }
}
const context = {
  URL,
  Notification: NotificationMock,
  localStorage: {getItem: () => 'light'},
  document: {hidden: true, addEventListener: (name, callback) => rememberListener(documentListeners, name, callback),
    querySelector: selector => ({'#app': appRoot, '#toasts': toastRoot, '#overlay': overlayRoot})[selector] || null},
  window: {Notification: NotificationMock,
    addEventListener: (name, callback) => rememberListener(windowListeners, name, callback),
    focus: () => { focused = true; }},
  matchMedia: () => ({addEventListener: () => {}}),
  EventSource: class {
    constructor() { eventSource = this; this.closed = false; }
    addEventListener(name, callback) { listeners.set(name, callback); }
    close() { this.closed = true; }
  },
};
vm.runInNewContext(source.replace('\ninit();\n',
  '\nglobalThis.__state = state; globalThis.__api = api; globalThis.__syncNotificationStream = syncNotificationStream; globalThis.__toggleNotificationCapability = toggleNotificationCapability; globalThis.__returnToLockScreen = returnToLockScreen; globalThis.__setAccountLockTransition = value => { accountLockTransition = value; };\n'), context);
context.renderShell = () => { rendered = true; };
context.loadView = () => { loaded = true; };
context.toast = () => {};

const state = context.__state;
state.settings = {capabilities: {notifications: {enabled: true}}, notification_filters: {
  platforms: ['wechat', 'wecom', 'qq'], mode: 'all', style: 'normal',
  allowed_conversations: [], muted_conversations: []
}};
context.__syncNotificationStream();
const privateBody = 'PRIVATE_MESSAGE_BODY_MUST_NOT_APPEAR';
const incoming = {
  platform: 'wecom', account_id: 'account-42', conversation_id: 'room-7',
  conversation_title: 'Operations', conversation_kind: 'group', text: privateBody,
};
const deliverEvent = () => listeners.get('message')({data: JSON.stringify(incoming)});
state.settings.notification_filters.platforms = ['qq'];
deliverEvent();
assert.equal(notifications.length, 0, 'a stale event from a newly deselected platform is suppressed');
const conversationKey = JSON.stringify(['wecom', 'account-42', 'room-7']);
state.settings.notification_filters.platforms = ['wecom'];
state.settings.notification_filters.mode = 'allowlist';
deliverEvent();
assert.equal(notifications.length, 0, 'a stale event outside the current allowlist is suppressed');
state.settings.notification_filters.allowed_conversations = [conversationKey];
state.settings.notification_filters.muted_conversations = [conversationKey];
deliverEvent();
assert.equal(notifications.length, 0, 'a mute overrides allowlist membership in the browser');
state.settings.notification_filters.muted_conversations = [];
deliverEvent();

assert.equal(notifications.length, 1);
assert.equal(notifications[0].options.body, '企业微信 · 收到一条新消息');
assert.doesNotMatch(JSON.stringify([notifications[0].title, notifications[0].options]), /PRIVATE_MESSAGE_BODY_MUST_NOT_APPEAR/);
notifications[0].onclick();
assert.equal(state.view, 'chat');
assert.equal(state.platform, 'wecom');
assert.deepEqual(JSON.parse(JSON.stringify(state.selected)), {
  id: 'room-7', platform: 'wecom', account_id: 'account-42', title: 'Operations',
  kind: 'group', message_count: 0,
});
assert.equal(focused, true);
assert.equal(rendered, true);
assert.equal(loaded, true);
assert.equal(notifications[0].closed, true);

deliverEvent();
assert.equal(notifications.length, 2);
state.view = 'contacts';
const selectedBeforeLock = JSON.parse(JSON.stringify(state.selected));
let lockReloads = 0;
context.location = {reload() { lockReloads++; }};
context.__returnToLockScreen();
notifications[1].onclick();
assert.equal(lockReloads, 1);
assert.equal(notifications[1].closed, true, 'a stale notification is dismissed during lock transition');
assert.deepEqual(JSON.parse(JSON.stringify(state.selected)), selectedBeforeLock,
  'a stale notification cannot change the selected conversation after lock begins');
assert.equal(state.view, 'contacts', 'a stale notification cannot navigate during lock transition');
context.__setAccountLockTransition(false);

NotificationMock.permission = 'denied';
context.document.hidden = false;
dispatch(documentListeners, 'visibilitychange');
assert.equal(notifications.length, 2, 'revoked permission suppresses notifications on tab return');
assert.equal(eventSource.closed, true, 'tab return immediately closes the stream after permission revocation');
NotificationMock.permission = 'granted';
context.__syncNotificationStream();
const focusedStream = eventSource;
NotificationMock.permission = 'denied';
dispatch(windowListeners, 'focus');
assert.equal(focusedStream.closed, true, 'window focus rechecks and closes the stream after permission revocation');
listeners.get('message')({data: JSON.stringify({platform: 'qq', account_id: 'a', conversation_id: 'b'})});
assert.equal(notifications.length, 2, 'revoked browser permission suppresses later notifications');
assert.equal(eventSource.closed, true, 'revoked browser permission closes the event stream');
console.log('PASS returning to the app after permission revocation closes the notification stream');

async function verifyExplicitPermissionFlow() {
  state.settings.capabilities.notifications.enabled = false;
  context.saveSettings = async patch => {
    state.settings.capabilities.notifications.enabled = patch.capabilities.notifications;
  };
  NotificationMock.permission = 'default';
  assert.equal(NotificationMock.requests, 0, 'loading and notification delivery never request permission');
  await context.__toggleNotificationCapability(false);
  assert.equal(NotificationMock.requests, 0, 'disabling never requests permission');

  NotificationMock.permission = 'default';
  NotificationMock.requestResult = 'denied';
  await context.__toggleNotificationCapability(true);
  assert.equal(NotificationMock.requests, 1, 'permission is requested only after explicit enable');
  assert.equal(state.settings.capabilities.notifications.enabled, false,
    'denied permission leaves the notification capability disabled');

  NotificationMock.permission = 'granted';
  await context.__toggleNotificationCapability(true);
  assert.equal(NotificationMock.requests, 1, 'pre-granted permission is not requested again');
  assert.equal(state.settings.capabilities.notifications.enabled, true,
    'granted explicit permission saves the enabled capability');
  console.log('PASS notification permission is requested only on explicit enable and denial stays disabled');
}

async function verifyLockInAnotherTabReloadsTheCachedChat() {
  let status = {locked: false};
  let reloads = 0;
  let finishPendingRequest;
  context.fetch = path => path === '/api/account-safety/status'
    ? Promise.resolve({ok: true, json: async () => status})
    : new Promise(resolve => {finishPendingRequest = resolve;});
  context.location = {reload() { reloads++; }};
  context.api = async path => {
    assert.equal(path, '/api/account-safety/status');
    return status;
  };
  state.settings.account_lock_enabled = true;
  context.__syncNotificationStream();
  const currentStream = eventSource;
  const queuedMessageHandler = listeners.get('message');
  const pendingRequest = context.__api('/api/messages').catch(error => error.message);
  await Promise.resolve();
  listeners.get('error')();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(reloads, 0, 'transient stream failures do not lock out an authenticated session');

  status = {locked: true};
  appRoot.children = ['synthetic private chat'];
  toastRoot.children = ['synthetic toast'];
  overlayRoot.children = ['synthetic dialog'];
  listeners.get('error')();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(reloads, 1, 'a server-side lock in another tab sends this tab to the lock screen');
  assert.deepEqual(appRoot.children, [], 'cached chat DOM is cleared before the reload begins');
  assert.deepEqual(toastRoot.children, [], 'cached toast text is cleared before the reload begins');
  assert.deepEqual(overlayRoot.children, [], 'cached dialog content is cleared before the reload begins');
  assert.equal(currentStream.closed, true, 'the notification stream closes immediately on lock');
  context.document.hidden = true;
  queuedMessageHandler({data: JSON.stringify(incoming)});
  assert.equal(notifications.length, 2, 'a queued message from the closed stream cannot notify after account lock');
  finishPendingRequest({ok: true, json: async () => ({messages: ['private']})});
  assert.equal(await pendingRequest, '应用已锁定', 'an in-flight response is discarded after lock begins');
  context.toast('stale response');
  assert.deepEqual(toastRoot.children, [], 'locked pages reject stale toast writes');
  await assert.rejects(context.__api('/api/messages'), /应用已锁定/, 'new API requests are rejected during lock navigation');
  assert.deepEqual(appRoot.children, [], 'late asynchronous work cannot restore private chat DOM');
  console.log('PASS cross-tab account lock clears cached page content before navigating to the lock page');
}

verifyExplicitPermissionFlow().then(verifyLockInAnotherTabReloadsTheCachedChat)
  .catch(error => {console.error(error);process.exitCode = 1;});
console.log('PASS notification click targets platform/account/conversation without exposing message body');
