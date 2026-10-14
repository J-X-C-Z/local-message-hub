const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, 'app.js'), 'utf8');
const overlay = {innerHTML: ''};
const calls = [];
const context = {
  URL,
  localStorage: {getItem: () => 'light'},
  document: {
    addEventListener() {}, querySelector: selector => selector === '#overlay' ? overlay : null,
    querySelectorAll: () => [],
  },
  window: {addEventListener() {}},
  matchMedia: () => ({addEventListener() {}}),
  setTimeout: () => 0,
  fetch: async url => {
    calls.push(url);
    return {ok: true, json: async () => url === '/api/sources'
      ? {sources: [{id: 'qq', state: 'login_required', message_count: 10}]}
      : {mode: 'local_database', available: false, state: 'locked',
        message: 'Synthetic <locked> database', database_count: 2}};
  },
};
vm.runInNewContext(source.replace('\ninit();\n',
  '\nglobalThis.__test = {state, renderSources, showConfigure, refreshSources};\n'), context);

(async () => {
  const {state, renderSources, showConfigure, refreshSources} = context.__test;
  state.view = 'sources';
  await refreshSources(false);
  assert.deepEqual(calls, ['/api/sources', '/api/qq/local-status']);
  const markup = renderSources();
  assert.match(markup, /内置 QQ 本地数据库读取/);
  assert.match(markup, /待解密/);
  assert.match(markup, /2 个本地数据库/);
  assert.match(markup, /Synthetic &lt;locked&gt; database/);
  assert.doesNotMatch(markup, /qq-login|漫游历史|NapCat/);
  showConfigure('qq');
  assert.match(overlay.innerHTML, /name="data_dir"/);
  assert.match(overlay.innerHTML, /name="key" type="password"/);
  assert.match(overlay.innerHTML, /留空保留本机已保存的密钥/);
  assert.doesNotMatch(overlay.innerHTML, /name="key"[^>]*value=/);
  assert.doesNotMatch(source, /\/api\/qq\/(?:start|login-status|history-import)/);
  state.view = 'chat';
  calls.length = 0;
  await refreshSources(false);
  assert.deepEqual(calls, ['/api/sources'], 'local database probing is only requested in the source view');
  console.log('PASS QQ local source status, masked local configuration, and removal of QR/roaming requests');
})().catch(error => {console.error(error);process.exitCode = 1;});
