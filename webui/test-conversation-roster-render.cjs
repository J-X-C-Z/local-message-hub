const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, 'app.js'), 'utf8');
const initCall = '\ninit();\n';
assert.ok(source.includes(initCall));
const context = {
  URL,
  localStorage: {getItem: () => 'light'},
  document: {addEventListener() {}, querySelector: () => ({setAttribute() {}, querySelector() { return null; }, querySelectorAll() { return []; }})},
  window: {addEventListener() {}, lucide: null},
  matchMedia: () => ({addEventListener() {}}),
  fetch: async () => ({ok: true, json: async () => ({})}),
};
vm.runInNewContext(source.replace(initCall,
  '\nglobalThis.__renderRoster = renderConversationRoster;\n'), context);

const html = context.__renderRoster([
  {id: 'wx-1', name: '<Owner>', avatar_url: 'https://q.qlogo.cn/avatar', role: 'owner'},
  {id: 'qq-2', name: 'Admin', role: 'admin'},
  {id: 'member-3', name: 'Member', role: 'member'},
], 'qq');
assert.match(html, /src="\/api\/avatar\?url=/);
assert.match(html, /&lt;Owner&gt;/);
assert.match(html, /群主/);
assert.match(html, /管理员/);
assert.doesNotMatch(html, /<Owner>/);
assert.match(html, /member-3/);
