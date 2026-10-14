const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, 'app.js'), 'utf8');
const documentListeners = new Map();
const timers = [];
const intervals = [];
const requests = [];
let reloads = 0;
const context = {
  URL,
  fetch: async url => {
    requests.push(url);
    return {ok: true, json: async () => ({locked: context.__locked})};
  },
  localStorage: {getItem: () => 'light'},
  document: {visibilityState: 'visible', addEventListener: (name, callback) => documentListeners.set(name, callback),
    querySelector: () => null},
  location: {reload: () => {reloads++;}},
  window: {addEventListener: () => {}},
  matchMedia: () => ({addEventListener: () => {}}),
  setTimeout: (callback, delay) => {timers.push({callback, delay}); return timers.length;},
  clearTimeout: () => {},
  setInterval: (callback, delay) => {intervals.push({callback, delay}); return intervals.length;},
  clearInterval: () => {},
};
vm.runInNewContext(source.replace('\ninit();\n',
  '\nglobalThis.__state = state; globalThis.__setLastActivityPing = value => { lastActivityPing = value; };\n' +
  'globalThis.__checkAccountLockOnResume = checkAccountLockOnResume;\n' +
  'globalThis.__syncAccountLockPolling = syncAccountLockPolling;\n' +
  'globalThis.__resetLockTransition = () => { accountLockTransition = false; };\n'), context);

context.__state.settings = {account_lock_enabled: true, account_lock_idle_timeout_seconds: 900};
context.__setLastActivityPing(Date.now());
documentListeners.get('wheel')();
assert.equal(timers.at(-1).delay, 900_000);

async function main() {
  context.__locked = false;
  await context.__checkAccountLockOnResume();
  assert.deepEqual(requests, ['/api/account-safety/status']);
  assert.equal(reloads, 0, 'an unlocked session stays open when returning to the tab');

  context.document.visibilityState = 'hidden';
  await context.__checkAccountLockOnResume();
  assert.equal(requests.length, 1, 'hidden tabs do not issue lock-status requests');

  context.document.visibilityState = 'visible';
  context.__locked = true;
  await context.__checkAccountLockOnResume();
  assert.equal(reloads, 1, 'an expired session is cleared when its tab becomes visible');
  assert.equal(requests.length, 2);

  context.__resetLockTransition();
  context.__locked = true;
  context.__state.settings.capabilities = {notifications: {enabled: false}};
  context.__syncAccountLockPolling();
  assert.equal(intervals.at(-1).delay, 2000, 'enabled account lock starts the cross-tab status poll');
  await intervals.at(-1).callback();
  assert.equal(requests.length, 3, 'lock polling is independent of desktop notifications');
  assert.equal(reloads, 2, 'a lock from another tab clears the active page promptly');
  console.log('PASS wheel activity, tab resume and cross-tab polling enforce account lock');
}

main().catch(error => { console.error(error); process.exitCode = 1; });
