const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const handlers = {}, windowHandlers = {}, requests = [], app = {innerHTML: ''}, overlay = {innerHTML: ''};
const context = {
  URL, URLSearchParams, localStorage: {getItem: () => 'light'}, location: {hash: '#chat'},
  history: {replaceState(_, __, hash) {context.location.hash = hash;}},
  document: {body: {dataset: {theme: 'light'}}, addEventListener(type, fn) {(handlers[type] ||= []).push(fn);}, querySelector(selector) {return selector === '#app' ? app : selector === '#overlay' ? overlay : null;}, querySelectorAll: () => []},
  window: {addEventListener(type, fn) {windowHandlers[type] = fn;}}, matchMedia: () => ({addEventListener() {}}),
  setTimeout: () => 1, clearTimeout() {},
};
const source = fs.readFileSync(path.join(__dirname, 'app.js'), 'utf8');
vm.runInNewContext(source.replace('\ninit();\n', `
globalThis.__test = {state, renderShell, renderCampus, renderCampusNavigation, campusRoute, navigateCampus};
renderView = () => {};closeModal = () => {};clearMediaSelection = () => {};
loadView = async () => {globalThis.loads.push({view: state.view, platform: state.platform, section: state.campus.section});};
toast = () => {};
`), context);
context.loads = [];
const {state, renderShell, renderCampus, renderCampusNavigation, campusRoute, navigateCampus} = context.__test;
context.fetch = async (url, options) => {requests.push({url, options});return {ok: true, json: async () => ({})};};
const click = async dataset => {
  const target = {dataset, disabled: false};
  await handlers.click[0]({target: {closest: selector => selector === '[data-media-select]' ? null : target}});
};
(async () => {
  state.platform = 'wechat';state.view = 'contacts';renderShell();
  assert.match(app.innerHTML, /data-station="selected"/);
  assert.doesNotMatch(app.innerHTML, /data-view="campus"/);
  await click({station: 'all'});
  assert.equal(state.platform, 'wechat');assert.equal(state.view, 'campus');assert.equal(state.campus.section, 'all');
  assert.equal(context.location.hash, '#campus/all');
  assert.match(app.innerHTML, /aria-label="信息站导航"/);
  for (const section of ['selected', 'all', 'hot', 'daily', 'topics', 'bookmarks', 'sources', 'pipeline', 'services', 'profile']) assert.match(renderCampusNavigation(), new RegExp(`data-station="${section}"`));
  assert.doesNotMatch(app.innerHTML, /data-view="contacts"|data-view="media"|class="sidebar-sources"/);
  assert.match(app.innerHTML, /data-platform="wechat" aria-pressed="false"/);
  await click({platform: 'wechat'});
  assert.equal(state.view, 'contacts', 'return to last chat context');assert.equal(state.platform, 'wechat');
  assert.equal(context.location.hash, '#contacts');
  assert.equal(campusRoute('#campus'), 'selected');assert.equal(campusRoute('#campus/daily'), 'daily');
  assert.equal(campusRoute('#campus/constructor'), '');assert.equal(campusRoute('#campus/unknown'), '');
  state.settings = {capabilities: {campus: {enabled: false}}};
  const count = context.loads.length;await navigateCampus('all');assert.equal(context.loads.length, count);renderShell();assert.doesNotMatch(app.innerHTML, /data-station=/);
  state.settings = {};await navigateCampus('hot');
  state.campus.briefing = {topics: [{id: 'scholarship', name: '奖学金', count: 1}], counts: {materials: 2, events: 1}, available_dates: ['2026-10-22']};
  state.campus.items = [{id: 'event-1', representative_id: 'material-1', title: '<奖学金>', summary: '<script>alert(1)</script>', url: 'https://www.sysu.edu.cn/notice', material_status: 'summary_only', topics: ['scholarship'], reasons: ['本科'], heat: 2, source_count: 3, hot_source_count: 2, reports: [{id: 'material-1', source_name: '<教务部>', title: '<选课>', url: 'javascript:evil()'}]}];state.loading = false;
  let html = renderCampus();
  assert.match(html, /data-id="material-1"/);assert.match(html, /2 个来源/);assert.match(html, /&lt;奖学金&gt;/);assert.match(html, /&lt;script&gt;/);assert.match(html, /信息依据：本科/);assert.doesNotMatch(html, /<script>|href="javascript/);
  state.campus.section = 'selected';assert.match(renderCampus(), /入选依据：本科/);
  state.campus.section = 'all';state.campus.items[0].future_date = true;assert.match(renderCampus(), /来源日期待核实/);
  state.campus.items[0].bookmarked = true;
  await click({action: 'campus-bookmark', id: 'material-1'});
  assert.equal(requests.at(-1).url, '/api/campus/events/material-1/state');
  assert.deepEqual(JSON.parse(requests.at(-1).options.body), {bookmarked: false});
  await click({campusTopic: 'scholarship'});assert.equal(state.campus.filters.topic, 'scholarship');
  state.campus.section = 'daily';state.campus.filters.date = '2026-10-22';html = renderCampus();assert.match(html, /value="2026-10-22"/);assert.match(html, /data-campus-date="2026-10-22"/);
  await click({campusDate: '2026-10-21'});assert.equal(state.campus.filters.date, '2026-10-21');
  state.campus.section = 'sources';state.campus.sources = [{id: 'feed-safe', kind: 'feed', name: '教务部', url: 'https://jwb.sysu.edu.cn/', enabled: true, status: 'ok'}];
  html = renderCampus();assert.match(html, /data-action="campus-source-toggle"/);assert.match(html, /已更新/);assert.doesNotMatch(html, /等待更新/);
  await click({action: 'campus-source-toggle', id: 'feed-safe'});assert.equal(requests.at(-1).url, '/api/campus/sources/feed-safe');assert.deepEqual(JSON.parse(requests.at(-1).options.body), {enabled: false});
  state.campus.section = 'pipeline';state.campus.briefing.pipeline = {stages: [{id: 'collected', name: '归档素材', count: 2}]};html = renderCampus();assert.match(html, /归档素材/);assert.match(html, /当前使用本地规则/);
  state.campus.section = 'profile';state.campus.profile = {faculty: '电子与信息学院', campus: '南校园', next_campus: '东校园', level: '本科', keywords: ['奖学金']};html = renderCampus();assert.match(html, /南校园 → 东校园/);assert.match(html, /编辑学生资料/);
  context.location.hash = '#campus/bookmarks';await windowHandlers.hashchange();assert.equal(state.campus.section, 'bookmarks');
  console.log('PASS station: peer platform, independent sidebar, chat context, routes, capability gate, grouped provenance, filters, source toggle, pipeline and profile');
})().catch(error => {console.error(error);process.exitCode = 1;});
