const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, 'app.js'), 'utf8');
const initCall = '\ninit();\n';
assert.ok(source.includes(initCall));

function renderSettings(notificationSupported, enabled, extraCapabilities = {}) {
  const context = {
    localStorage: {getItem: () => 'light'},
    document: {addEventListener: () => {}},
    window: notificationSupported
      ? {Notification: function Notification() {}, addEventListener: () => {}}
      : {addEventListener: () => {}},
    matchMedia: () => ({addEventListener: () => {}}),
    location: {origin: 'http://localhost'},
  };
  vm.runInNewContext(source.replace(initCall,
    '\nglobalThis.__renderSettingsBase = renderSettingsBase; globalThis.__state = state;\n'), context);
  context.head = () => '';
  context.__state.settings = {
    capabilities: {
      notifications: {name: '桌面通知', available: true, enabled},
      ...extraCapabilities,
    },
    notification_filters: {platforms: ['wechat', 'wecom', 'qq'], mode: 'all', style: 'normal'},
  };
  return context.__renderSettingsBase();
}

const unsupported = renderSettings(false, false);
assert.match(unsupported, /当前浏览器不支持桌面通知，关闭功能仍可使用。/);
assert.match(unsupported, /aria-label="桌面通知"[^>]*disabled aria-disabled="true"/);

const enabledButUnsupported = renderSettings(false, true);
assert.match(enabledButUnsupported, /aria-label="桌面通知"[^>]*aria-checked="true"/);
assert.doesNotMatch(enabledButUnsupported, /aria-label="桌面通知"[^>]*disabled/,
  'users can still disable a capability enabled in another browser');

const supported = renderSettings(true, false);
assert.doesNotMatch(supported, /当前浏览器不支持桌面通知/);
assert.match(supported, /aria-label="桌面通知"[^>]*aria-checked="false"/);
assert.doesNotMatch(supported, /aria-label="桌面通知"[^>]*disabled/);

const sourceMutationCapabilities = renderSettings(true, false, {
  anti_recall: {name: '防撤回拦截', available: false, enabled: false, reason: '尚未开放'},
  qq_history: {name: 'QQ 在线漫游历史', available: false, enabled: false, reason: '已停用'},
});
for (const [id, label] of [['anti_recall', '防撤回拦截'], ['qq_history', 'QQ 在线漫游历史']]) {
  assert.doesNotMatch(sourceMutationCapabilities, new RegExp(`data-action="capability:${id}"`),
    'an unavailable capability must not receive an interactive toggle');
  assert.match(sourceMutationCapabilities, new RegExp(`${label}[\\s\\S]*?未开放`));
}
assert.doesNotMatch(sourceMutationCapabilities, /data-action="capability:(?:anti_recall|qq_history)"[^>]*role="switch"/,
  'source mutation and retired online-history controls must remain non-interactive');

console.log('PASS unavailable settings stay non-interactive and notification support is explained');
