const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const handlers = {}, timers = [];
let sidebarWrites = 0, toolbarWrites = 0, viewWrites = 0;
const sidebar = {set innerHTML(value) {sidebarWrites++;}};
const context = {
  URL, URLSearchParams,
  FormData: class { *[Symbol.iterator]() {yield ['q', 'fixture'];} },
  localStorage: {getItem: () => 'light'},
  document: {
    addEventListener(type, callback) {(handlers[type] ||= []).push(callback);},
    querySelector(selector) {return selector === '.sidebar-sources' ? sidebar : null;},
    querySelectorAll: () => []
  },
  window: {addEventListener() {}},
  matchMedia: () => ({addEventListener() {}}),
  setTimeout(callback, milliseconds) {
    if (milliseconds === 500) {Promise.resolve().then(callback);return 0;}
    timers.push(callback);return timers.length;
  },
  clearTimeout() {},
  fetch: async () => ({ok: true, json: async () => ({sources: [{id: 'qq', state: 'connected', message_count: 10}]})})
};
const source = fs.readFileSync(path.join(__dirname, 'app.js'), 'utf8');
vm.runInNewContext(source.replace('\ninit();\n', `
globalThis.__test = {state, refreshSources, loadView, startResourceExport, releaseInactiveViewData};
renderView = () => {globalThis.onView();};
refreshResourceToolbar = () => {globalThis.onToolbar();};
toast = () => {};
`), context);
context.onView = () => viewWrites++;
context.onToolbar = () => toolbarWrites++;
const {state, refreshSources, loadView, startResourceExport, releaseInactiveViewData} = context.__test;

(async () => {
  state.view = 'sources';
  await refreshSources();
  for (let index = 0; index < 12; index++) await refreshSources();
  assert.equal(sidebarWrites, 1);
  assert.equal(viewWrites, 1, 'unchanged status must preserve the data-source DOM');

  state.view = 'media';
  state.selectedMedia.set('fixture', {message: {platform: 'qq', id: 'fixture'}, resourceIndex: null});
  let jobReads = 0;
  context.fetch = async url => ({ok: true, json: async () => ({id: 'job', total: 1,
    state: url === '/api/resource-jobs' || ++jobReads < 5 ? 'running' : 'cancelled'})});
  viewWrites = 0;
  await startResourceExport();
  assert.equal(toolbarWrites, 7);
  assert.equal(viewWrites, 1, 'export polling must preserve media elements until completion');

  state.view = 'stats';state.statsTab = 'daily';
  let completeDetail;
  context.fetch = async url => ({ok: true, json: () => url.startsWith('/api/analytics/')
    ? new Promise(resolve => {completeDetail = resolve;})
    : Promise.resolve(url.startsWith('/api/conversations') ? {items: [], total: 0} : {})});
  const pending = loadView();
  while (!completeDetail) await Promise.resolve();
  state.view = 'chat';state.selected = null;
  await loadView();
  completeDetail({items: [{private: 'synthetic'}]});
  await pending;
  assert.equal(state.selfSentDaily, null, 'stale statistics must not repopulate an inactive page');

  const list = {innerHTML: ''};
  context.document.querySelector = selector => selector === '.conversation-list' ? list : null;
  let finishSearch;
  context.fetch = () => new Promise(resolve => {finishSearch = resolve;});
  const input = {value: 'older', closest: selector => selector === '#conversation-search'};
  for (const handler of handlers.input) handler({target: input});
  const oldSearch = timers.at(-1)();
  input.value = 'newer';
  for (const handler of handlers.input) handler({target: input});
  finishSearch({ok: true, json: async () => ({items: [{id: 'old'}], total: 1})});
  await oldSearch;
  assert.equal(list.innerHTML, '', 'a new keystroke invalidates the previous response before debounce');

  state.selected = {platform: 'qq', account_id: 'fixture', id: 'fixture'};
  let failSearch;
  context.fetch = () => new Promise((resolve, reject) => {failSearch = reject;});
  const pendingSearch = handlers.submit[0]({preventDefault() {}, target: {id: 'message-search'}});
  state.view = 'settings';state.request++;state.loading = true;state.error = '';
  viewWrites = 0;
  failSearch(new Error('stale search'));
  await pendingSearch;
  assert.equal(state.error, '');
  assert.equal(state.loading, true);
  assert.equal(viewWrites, 0, 'stale search completion must not redraw the new page');

  state.view = 'settings';state.groupMemberLists.set('fixture', {members: ['synthetic']});
  state.selfSentDaily = {items: ['synthetic']};
  releaseInactiveViewData();
  assert.equal(state.groupMemberLists.size, 0);
  assert.equal(state.selfSentDaily, null);
  console.log('PASS stable status polling, incremental export progress, stale responses, and inactive statistics release');
})().catch(error => {console.error(error);process.exitCode = 1;});
