const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const overlay = {innerHTML: ''}, timers = new Map(), requests = [], notices = [];
let timerId = 0, refresh = {state: 'idle'}, updates = {auto_updates_enabled: false, interval_minutes: 15, fetch_full_text: true, batch_limit: 20};
let article = {id: 'article', title: '<奖学金>', source_name: '康乐解学', url: 'https://mp.weixin.qq.com/s/example', summary: '保留的摘要', content_status: 'summary_only'};
let failFetch = false, releaseArticle;
const context = {
  URL, URLSearchParams, localStorage: {getItem: () => 'light'},
  document: {addEventListener() {}, querySelector: s => s === '#overlay' ? overlay : null, querySelectorAll: () => []},
  window: {addEventListener() {}}, matchMedia: () => ({addEventListener() {}}),
  setTimeout(fn, delay) {if (!delay) return 0;timers.set(++timerId, {fn, delay});return timerId;}, clearTimeout(id) {timers.delete(id);},
  FormData: class {constructor(form) {this.values = form.entries;}get(key) {return this.values.find(v => v[0] === key)?.[1] || null;}},
  recordNotice: (...args) => notices.push(args),
};
const source = fs.readFileSync(path.join(__dirname, 'app.js'), 'utf8');
vm.runInNewContext(source.replace('\ninit();\n', '\nglobalThis.__test = {state, loadView, renderCampus, campusContentLabel, campusUpdateStatus, showCampusArticle, showCampusUpdates, saveCampusUpdates, scheduleCampusPoll};\nrenderView = () => {};toast = recordNotice;\n'), context);
const {state, loadView, renderCampus, campusContentLabel, campusUpdateStatus, showCampusArticle, showCampusUpdates, saveCampusUpdates, scheduleCampusPoll} = context.__test;
context.fetch = async (url, options = {}) => {
  requests.push({url, options});
  const route = url.split('?')[0];
  if (route === '/api/campus/items/article' && releaseArticle) return new Promise(resolve => {releaseArticle.resolve = data => resolve({ok: true, json: async () => data});});
  if (route.endsWith('/fetch') && failFetch) return {ok: false, json: async () => ({error: '访问受限'})};
  if (route === '/api/campus/updates' && options.method === 'POST') updates = {...updates, ...JSON.parse(options.body)};
  const data = route === '/api/campus/updates' ? updates : route === '/api/campus/refresh' ? refresh : route === '/api/campus/briefing' ? {items: [article], total: 1} : route === '/api/campus/sources' ? {items: []} : route === '/api/campus/profile' ? {} : article;
  return {ok: true, json: async () => data};
};
const form = entries => ({entries, reportValidity: () => true, querySelector: () => ({disabled: false})});
(async () => {
  state.view = 'campus';await loadView();
  assert.equal(timers.size, 0);
  state.campus.section = 'pipeline';assert.match(renderCampus(), /自动更新未开启/);state.campus.section = 'all';
  assert.match(renderCampus(), /阅读全文/);
  showCampusUpdates();assert.match(overlay.innerHTML, /name="auto_updates_enabled" >/);
  assert.match(overlay.innerHTML, /不扫描公众号全部历史/);
  timers.clear();
  await saveCampusUpdates(form([['auto_updates_enabled', 'on'], ['interval_minutes', '15'], ['fetch_full_text', 'on'], ['batch_limit', '20']]));
  assert.deepEqual(JSON.parse(requests.at(-1).options.body), {auto_updates_enabled: true, interval_minutes: 15, fetch_full_text: true, batch_limit: 20});
  assert.equal(timers.size, 1);assert.equal([...timers.values()][0].delay, 5000);
  updates.content_counts = {full_text: 4, pending: 2};updates.last_check = '2026-10-23T10:00:00+08:00';updates.next_due = '2026-10-23T10:15:00+08:00';
  state.campus.updates = updates;assert.match(campusUpdateStatus(), /已有全文 4 篇 · 待获取 2 篇/);
  showCampusUpdates();assert.match(overlay.innerHTML, /name="auto_updates_enabled" checked/);
  const count = requests.length;
  await saveCampusUpdates(form([['interval_minutes', '0'], ['batch_limit', '20']]));assert.equal(requests.length, count);
  await saveCampusUpdates(form([['interval_minutes', '15'], ['batch_limit', '21']]));assert.equal(requests.length, count);
  await saveCampusUpdates(form([['interval_minutes', '15'], ['batch_limit', '20']]));
  assert.equal(state.campus.updates.auto_updates_enabled, false);assert.equal(timers.size, 0);
  await showCampusArticle('article');
  assert.match(overlay.innerHTML, /仅摘要，待获取全文/);assert.match(overlay.innerHTML, /保留的摘要/);
  assert.ok(!overlay.innerHTML.includes('<奖学金>'));
  article = {...article, content_status: 'full_text', content: '<script>sendSecrets()</script>\n<img src=x onerror=evil()>\n正文'};
  await showCampusArticle('article', true, {disabled: false});
  assert.equal(requests.at(-1).url, '/api/campus/items/article/fetch');
  assert.equal(requests.at(-1).options.method, 'POST');
  assert.match(overlay.innerHTML, /已获取全文/);assert.match(overlay.innerHTML, /&lt;script&gt;/);assert.match(overlay.innerHTML, /&lt;img/);
  assert.ok(!overlay.innerHTML.includes('<script>'));assert.ok(!overlay.innerHTML.includes('<img '));
  assert.ok(!overlay.innerHTML.includes('data-action="campus-fetch-article"'));
  article = {...article, content_status: 'fetch_error', content_error: '暂时无法获取'};
  await showCampusArticle('article');
  assert.match(overlay.innerHTML, /全文获取失败，保留摘要/);assert.match(overlay.innerHTML, /保留的摘要/);assert.match(overlay.innerHTML, /重试全文/);
  assert.ok(!overlay.innerHTML.includes('sendSecrets'));
  const saved = overlay.innerHTML;failFetch = true;
  const button = {disabled: false};await showCampusArticle('article', true, button);
  assert.equal(button.disabled, false);assert.equal(overlay.innerHTML, saved);assert.equal(notices.at(-1)[1], true);
  releaseArticle = {};const pending = showCampusArticle('article');overlay.innerHTML = '';
  releaseArticle.resolve(article);await pending;assert.equal(overlay.innerHTML, '', 'closed readers are not reopened by late responses');releaseArticle = null;
  updates.auto_updates_enabled = true;state.campus.updates = updates;scheduleCampusPoll();
  state.view = 'search';state.filters.q = '';await loadView();assert.equal(timers.size, 0);
  assert.equal(campusContentLabel({content_status: 'blocked'}), '访问受限，保留摘要');
  console.log('PASS campus updates: enable/disable, bounds, polling, safe full-text reader, failed fetch preserves summary, stale dialog isolation');
})().catch(error => {console.error(error);process.exitCode = 1;});
