const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, 'app.js'), 'utf8');
const formats = ['json', 'html', 'csv', 'md', 'txt', 'xlsx', 'sql', 'chatlab', 'chatlab-jsonl'];
let currentFormat = 'json', requestUrl = '', downloaded = null, failRequest = false;
const overlay = {innerHTML: ''}, notices = [];
const links = [];
const context = {
  URLSearchParams,
  URL: {createObjectURL: blob => (downloaded = blob, 'blob:fixture'), revokeObjectURL: () => {}},
  FormData: class {get(name) {assert.equal(name, 'format');return currentFormat;}},
  fetch: async url => {
    requestUrl = url;
    return failRequest
      ? {ok: false, json: async () => ({error: 'synthetic export failure'})}
      : {ok: true, blob: async () => ({bytes: 'synthetic archive'})};
  },
  localStorage: {getItem: () => 'light'},
  document: {
    addEventListener: () => {},
    querySelector: selector => selector === '#overlay' ? overlay : selector === '#toasts' ? {append: node => notices.push(node.innerText || node.innerHTML)} : null,
    querySelectorAll: () => [],
    createElement: tag => {
      if (tag === 'a') {
        const link = {click() {}, remove() {}};
        Object.defineProperty(link, 'download', {set: value => {link.filename = value;}});
        links.push(link);
        return link;
      }
      return {remove() {}};
    },
    body: {append: () => {}}
  },
  window: {addEventListener: () => {}},
  matchMedia: () => ({addEventListener: () => {}}),
  setTimeout: () => {}
};
vm.runInNewContext(source.replace('\ninit();\n',
  '\nglobalThis.__downloadExport = downloadExport; globalThis.__state = state;\n'), context);

const query = () => Object.fromEntries(new URLSearchParams(requestUrl.split('?')[1]));
const expectedDate = new Date().toISOString().slice(0, 10);

(async () => {
  context.__state.view = 'chat';
  context.__state.selected = {platform: 'wecom', account_id: 'account-7', id: 'peer-9'};
  context.__state.messageFilter = {q: 'invoice', type: 'image', start: '2026-01-01', end: '2026-01-31'};
  for (const format of formats) {
    currentFormat = format;
    downloaded = null;
    await context.__downloadExport({disabled: false});
    assert.equal(requestUrl.split('?')[0], '/api/export');
    assert.deepEqual(query(), {platform: 'wecom', account_id: 'account-7', conversation_id: 'peer-9',
      q: 'invoice', type: 'image', start: '2026-01-01', end: '2026-01-31', format});
    assert.deepEqual(downloaded, {bytes: 'synthetic archive'});
    const extension = format === 'chatlab-jsonl' ? 'jsonl' : format;
    assert.equal(links.at(-1).filename, `消息档案-${expectedDate}.${extension}`);
  }

  context.__state.view = 'search';
  context.__state.platform = 'all';
  context.__state.filters = {q: 'needle', type: 'voice', kind: 'private', start: '2026-02-01', end: '2026-02-02',
    source: 'all', account_scope: 'wechat:account-1', conversation_id: 'peer-search'};
  currentFormat = 'csv';
  await context.__downloadExport({disabled: false});
  assert.deepEqual(query(), {...context.__state.filters, platform: 'all', format: 'csv'});

  failRequest = true;
  const button = {disabled: false};
  await context.__downloadExport(button);
  assert.equal(button.disabled, false);
  assert.ok(notices.some(message => String(message).includes('synthetic export failure')));
  console.log('PASS chat/search export formats, identity filters, downloads, and failure recovery');
})().catch(error => {console.error(error);process.exitCode = 1;});
