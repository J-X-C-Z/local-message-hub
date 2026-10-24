const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, 'app.js'), 'utf8');
const overlay = {innerHTML: ''};
const handlers = {};
const commit = {disabled: false};
const controls = {};
const requests = [];
const context = {
  URL, URLSearchParams, location: {origin: 'http://localhost'},
  localStorage: {getItem: () => 'light'},
  document: {
    addEventListener(type, listener) {(handlers[type] ||= []).push(listener);},
    querySelector: selector => selector === '#overlay' ? overlay : selector === '[data-action="commit-import"]' ? commit : controls[selector] || null,
    querySelectorAll: () => [],
  },
  window: {addEventListener() {}}, matchMedia: () => ({addEventListener() {}}),
  setTimeout: () => 0,
  FormData: class {constructor(form) {return form.entries[Symbol.iterator]();}},
  fetch: async (url, options) => {requests.push({url, options});return {ok: true, json: async () => ({valid: 1, total: 1})};},
};
vm.runInNewContext(source.replace('\ninit();\n', '\nglobalThis.__test = {state, platforms, renderSources, renderImports, renderSettings, footerStatus, showConfigure, saveConfig, uploadImport};\n'), context);
vm.runInNewContext('renderView = () => {};toast = () => {};sync = async () => {};', context);

(async () => {
  const {state, platforms, renderSources, renderImports, renderSettings, footerStatus, showConfigure, saveConfig, uploadImport} = context.__test;
  assert.equal(platforms.email.icon, 'mail');
  state.view = 'sources';
  state.sources = [{id: 'email', state: 'connected', email_config: {configured: true, provider: 'gmail', host: 'imap.gmail.com', username: '<mail>@example.com', auth: 'oauth2', folder: 'INBOX'}}];
  const sources = renderSources();
  assert.match(footerStatus(), /1 \/ 4/);
  assert.match(sources, /configure:email/);
  assert.match(sources, /sync:email/);
  assert.match(sources, /&lt;mail&gt;@example.com/);
  showConfigure('email');
  assert.match(overlay.innerHTML, /name="provider"/);
  assert.match(overlay.innerHTML, /value="imap"/);
  assert.match(overlay.innerHTML, /value="gmail" selected/);
  assert.match(overlay.innerHTML, /value="oauth2" selected/);
  assert.match(overlay.innerHTML, /name="password" type="password" autocomplete="off"/);
  assert.match(overlay.innerHTML, /name="access_token" type="password" autocomplete="off"/);
  assert.doesNotMatch(overlay.innerHTML, /name="(?:password|access_token)"[^>]*value=/);
  assert.match(overlay.innerHTML, /不会自动刷新/);
  controls['#configure-form'] = {dataset: {source: 'email'}, reportValidity: () => true, entries: [['provider', 'imap'], ['host', 'imap.example.com'], ['port', '993'], ['username', 'mail@example.com'], ['auth', 'password'], ['folder', 'INBOX'], ['password', 'test-secret'], ['access_token', 'test-token']]};
  await saveConfig({disabled: false});
  assert.equal(requests[0].url, '/api/source/email/configure');
  assert.deepEqual(JSON.parse(requests[0].options.body), {provider: 'imap', host: 'imap.example.com', port: '993', username: 'mail@example.com', email: 'mail@example.com', auth: 'password', folder: 'INBOX', password: 'test-secret', access_token: 'test-token'});
  assert.doesNotMatch(JSON.stringify(state), /test-secret|test-token/);
  state.view = 'imports';
  assert.match(renderImports(), /\.eml,\.mbox/);
  assert.match(renderImports(), /value="email"/);
  state.view = 'settings';
  assert.match(renderSettings(), /value="email"/);
  state.view = 'imports';
  for (const extension of ['eml', 'MBOX']) {
    state.importFile = {name: `archive.${extension}`};state.importPlatform = 'all';
    await uploadImport(true);
    const query = new URL(requests.at(-1).url, 'http://localhost').searchParams;
    assert.equal(query.get('format'), extension.toLowerCase());
    assert.equal(query.get('platform'), 'email');
  }
  state.importPreview = {valid: 1};
  const target = {id: 'import-account', value: 'changed', closest: () => null};
  for (const handler of handlers.input) handler({target});
  assert.equal(state.importPreview, null);
  assert.equal(commit.disabled, true);
  let resolvePreview;
  context.fetch = async () => ({ok: true, json: () => new Promise(resolve => {resolvePreview = resolve;})});
  state.importFile = {name: 'pending.eml'};
  const pending = uploadImport(true);
  await new Promise(setImmediate);
  state.importAccount = 'new account';
  resolvePreview({valid: 5});
  await pending;
  assert.equal(state.importPreview, null, 'a response for previous import selections must stay invalid');
  console.log('PASS email source config, secret payload isolation, archive detection, and stale import preview');
})().catch(error => {console.error(error);process.exitCode = 1;});

