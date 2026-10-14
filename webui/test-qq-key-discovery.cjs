const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, 'app.js'), 'utf8');
const calls = [];
let prompt = '';
const context = {
  URL,
  localStorage: {getItem: () => 'light'},
  document: {addEventListener() {}, querySelector: () => null, querySelectorAll: () => []},
  window: {addEventListener() {}, confirm: value => {prompt = value; return false;}},
  matchMedia: () => ({addEventListener() {}}),
  fetch: async url => {calls.push(url); throw new Error('unexpected request');},
};
vm.runInNewContext(source.replace('\ninit();\n',
  '\nglobalThis.__test = {state, renderSources, discoverQQKey};\n'), context);

(async () => {
  const {state, renderSources, discoverQQKey} = context.__test;
  state.settings = {capabilities: {sync: {enabled: true}}};
  state.sources = [{id: 'qq', state: 'locked', error_code: 'key_required',
    key_discovery: {state: 'idle', message: '未开始'}}];
  const markup = renderSources();
  assert.match(markup, /data-action="discover-qq-key"/);
  assert.match(markup, /只读发现密钥/);
  await discoverQQKey();
  assert.match(prompt, /只读扫描当前 Windows 用户运行中的 QQ/);
  assert.match(prompt, /不会调试、注入或关闭 QQ/);
  assert.match(prompt, /密钥会保存到本机配置，并立即开始同步本机 QQ 历史记录/);
  assert.match(prompt, /密钥和聊天内容不会发送到网络服务/);
  state.sources[0].key_discovery = {state: 'limit_reached', limit_reason: 'memory_budget',
    message: '只读扫描达到 512 MiB 读取预算，未确认密钥。'};
  assert.match(renderSources(), /512 MiB 读取预算/);
  state.sources[0].key_discovery = {state: 'not_found', processes: 4, tested: 123, read_mib: 512,
    message: '未找到可验证的密钥'};
  const scanned = renderSources();
  assert.match(scanned, /扫描 4 个 QQ 进程/);
  assert.match(scanned, /验证 123 个候选/);
  assert.match(scanned, /读取 512 MiB/);
  assert.deepEqual(calls, [], 'declining the confirmation must not start a scan');
  console.log('PASS QQ key discovery requires explicit confirmation and exposes a read-only UI action');
})().catch(error => {console.error(error);process.exitCode = 1;});
