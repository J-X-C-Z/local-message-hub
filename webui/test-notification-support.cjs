const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, 'app.js'), 'utf8');
const initCall = '\ninit();\n';
const context = {
  localStorage: {getItem: () => 'light'},
  document: {addEventListener: () => {}},
  window: {addEventListener: () => {}},
  matchMedia: () => ({addEventListener: () => {}}),
  location: {origin: 'http://localhost'},
};
vm.runInNewContext(source.replace(initCall,
  '\nglobalThis.__render = renderSettingsBase; globalThis.__state = state;\n'), context);
context.head = () => '';
context.__state.settings = {
  capabilities: {notifications: {name: '桌面通知', available: true, enabled: false}},
  notification_filters: {platforms: ['wechat', 'wecom', 'qq'], mode: 'all', style: 'normal'},
};
const settings = context.__render();
assert.match(settings, /aria-label="桌面通知"[^>]*aria-checked="false"/);
assert.match(settings, /data-action="notification-test" disabled/);
assert.doesNotMatch(source, /Notification\.requestPermission|new Notification\(/,
  'notification delivery uses the local desktop service instead of browser permissions');
context.__state.settings.capabilities.notifications.enabled = true;
assert.match(context.__render(), /data-action="notification-test"(?![^>]*disabled)/);
console.log('PASS desktop notification controls do not depend on browser notification permissions');
