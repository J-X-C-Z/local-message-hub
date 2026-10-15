const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, 'app.js'), 'utf8');
const formats = ['json', 'html', 'csv', 'md', 'txt', 'xlsx', 'sql', 'chatlab', 'chatlab-jsonl'];
let currentFormat = 'json', requestUrl = '', downloaded = null, failRequest = false, includeMedia = false;
const overlay = {innerHTML: ''}, notices = [];
const links = [];
const requests = [];
const context = {
  URLSearchParams,
  URL: {createObjectURL: blob => (downloaded = blob, 'blob:fixture'), revokeObjectURL: () => {}},
  FormData: class {get(name) {return name === 'format' ? currentFormat : name === 'include_media' ? includeMedia : null;}},
  fetch: async (url, options = {}) => {
    requestUrl = url;
    requests.push({url, options});
    if (url === '/api/export/bundle') return {ok: true, json: async () => ({id: 'bundle-job', state: 'queued'})};
    if (url === '/api/resource-jobs/bundle-job') return {ok: true, json: async () => ({id: 'bundle-job', state: 'completed', exported: 1, failed: 0, download_url: '/api/resource-jobs/bundle-job/download'})};
    if (url === '/api/resource-jobs/bundle-job/cancel') return {ok: true, json: async () => ({id: 'bundle-job', state: 'cancelling'})};
    return failRequest
      ? {ok: false, json: async () => ({error: 'synthetic export failure'})}
      : {ok: true, blob: async () => ({bytes: 'synthetic archive'}), json: async () => ({})};
  },
  localStorage: {getItem: () => 'light'},
  document: {
    addEventListener: () => {},
    querySelector: selector => selector === '#overlay' ? overlay : selector === '#toasts' ? {append: node => notices.push(node.innerText || node.innerHTML)} : selector === '#export-form [name="include_media"]' ? {checked: includeMedia} : selector === '#export-bundle-status' ? {hidden: true, textContent: ''} : null,
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
  setTimeout: callback => {callback();},
  location: {href: ''}
};
vm.runInNewContext(source.replace('\ninit();\n',
  '\nglobalThis.__downloadExport = downloadExport; globalThis.__state = state;\n'), context);

const query = () => Object.fromEntries(new URLSearchParams(requestUrl.split('?')[1]));
const expectedDate = new Date().toISOString().slice(0, 10);

(async () => {
  context.__state.view = 'chat';
  context.__state.selected = {platform: 'wecom', account_id: 'account-7', id: 'peer-9'};
  context.__state.settings = {capabilities: {media: {enabled: true}}};
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

  includeMedia = true;
  currentFormat = 'xlsx';
  const beforeBundle = requests.length;
  await context.__downloadExport({disabled: false});
  const bundleRequests = requests.slice(beforeBundle);
  assert.equal(bundleRequests[0].url, '/api/export/bundle');
  assert.deepEqual(JSON.parse(bundleRequests[0].options.body), {query: {
    platform: 'wecom', account_id: 'account-7', conversation_id: 'peer-9',
    q: 'invoice', type: 'image', start: '2026-01-01', end: '2026-01-31', format: 'xlsx'
  }});
  assert.ok(bundleRequests.some(request => request.url === '/api/resource-jobs/bundle-job'));
  assert.equal(context.location.href, '/api/resource-jobs/bundle-job/download');
  assert.ok(source.includes('仅打包本机已解析的媒体；未取得项会列入 manifest。'));
  includeMedia = false;

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
