const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, 'app.js'), 'utf8');
const initCall = '\ninit();\n';
assert.ok(source.includes(initCall), 'app bootstrap invocation is available to isolate source tests');

const context = {
  URL,
  localStorage: {getItem: () => 'light'},
  document: {addEventListener: () => {}},
  window: {addEventListener: () => {}},
  matchMedia: () => ({addEventListener: () => {}}),
};
vm.runInNewContext(source.replace(initCall,
  '\nglobalThis.__renderSources = renderSources; globalThis.__state = state;\n'), context);

context.__state.sources = [
  {id: 'wechat', state: 'connected', avatar_database: {state: 'locked', message: '数据库已锁定'}},
  {id: 'wecom', state: 'connected'},
  {id: 'qq', state: 'disconnected'},
];
context.__state.settings = {capabilities: {sync: {enabled: true}, qq_history: {enabled: true}}};
const markup = context.__renderSources();
assert.match(markup, /头像库 · 数据库已锁定/);
assert.equal((markup.match(/头像库 ·/g) || []).length, 1, 'avatar database status is shown only for WeChat');

context.__state.sources[0].avatar_database.message = '<img src=x onerror=alert(1)>';
const escapedMarkup = context.__renderSources();
assert.match(escapedMarkup, /&lt;img src=x onerror=alert\(1\)&gt;/);
assert.doesNotMatch(escapedMarkup, /<img src=x/);
