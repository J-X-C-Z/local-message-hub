const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const handlers = {}, timers = new Map(), requests = [], overlay = {innerHTML: ''};
let timerId = 0;
const context = {
  URL, URLSearchParams, localStorage: {getItem: () => 'light'},
  document: {addEventListener(type, handler) {(handlers[type] ||= []).push(handler);}, querySelector: s => s === '#overlay' ? overlay : null, querySelectorAll: () => []},
  window: {addEventListener() {}}, matchMedia: () => ({addEventListener() {}}),
  setTimeout(fn) {timers.set(++timerId, fn);return timerId;}, clearTimeout(id) {timers.delete(id);},
  FormData: class {constructor(form) {this.values = form.entries;}[Symbol.iterator]() {return this.values[Symbol.iterator]();}get(key) {return this.values.find(v => v[0] === key)?.[1] || null;}},
};
const source = fs.readFileSync(path.join(__dirname, 'app.js'), 'utf8');
vm.runInNewContext(source.replace('\ninit();\n', '\nglobalThis.__test = {state, views, loadView, renderCampus, campusUrl, campusDate, showCampusProfile, showCampusWechat, refreshCampus, setCampusItemState, saveCampusProfile, importCampusWechat, showCampusWechatAccounts, importCampusWechatAccounts};\nrenderView = () => {};toast = () => {};\n'), context);
const {state, views, loadView, renderCampus, campusUrl, campusDate, showCampusProfile, showCampusWechat, refreshCampus, setCampusItemState, saveCampusProfile, importCampusWechat, showCampusWechatAccounts, importCampusWechatAccounts} = context.__test;
const sources = [
  {id: 'teach', name: '教务部', url: 'https://jwb.sysu.edu.cn/', kind: 'feed', category: '选课', status: 'error', error: '<网络异常>'},
  {id: 'portal', name: '学生服务', url: 'https://portal.sysu.edu.cn/', kind: 'portal', category: '办事'},
];
const item = {id: 'notice', title: '<选课通知>', url: 'https://jwb.sysu.edu.cn/article/1', source_name: '<教务部>', category: '选课', summary: '<script>alert(1)</script>', campus: '南校园', relevance_reasons: ['本科'], published_at: null};
let refresh = {state: 'idle'};
context.fetch = async (url, options = {}) => {
  requests.push({url, options});
  const route = url.split('?')[0];
  const data = route === '/api/campus/wechat-accounts' ? {items: [{id: 'gh_exact', account_id: 'wxid_exact', name: '<康乐解学>', alias: 'sysu&student', selected: true, article_count: 18}]} : route === '/api/campus/briefing' ? {items: [item], total: 51} : route === '/api/campus/sources' ? {items: sources} : route === '/api/campus/profile' ? {campus: '南校园', next_campus: '东校园', faculty: '电子与信息学院', level: '本科', keywords: ['奖学金']} : route === '/api/campus/refresh' ? refresh : {};
  return {ok: true, json: async () => data};
};

(async () => {
  assert.equal(views.campus.name, '中大学生信息站');
  assert.equal(state.campus.filters.relevant, '1', 'student-relevant information is the initial view');
  for (const url of ['javascript:alert(1)', 'https://sysu.edu.cn.evil.test/', 'https://user@sysu.edu.cn/', 'https://sysu.edu.cn:444/', 'http://sysu.edu.cn/', 'https://mp.weixin.qq.com.evil.test/s/x', 'https://mp.weixin.qq.com/profile']) assert.equal(campusUrl(url), '', url);
  for (const url of ['https://www.sysu.edu.cn/', 'https://sysu.edu.cn/', 'https://mp.weixin.qq.com/s?__biz=1', 'https://mp.weixin.qq.com/s/article']) assert.ok(campusUrl(url), url);
  assert.equal(campusDate(null), '发布日期未提供');assert.equal(campusDate('invalid'), '发布日期未提供');
  state.view = 'campus';await loadView();
  assert.equal(requests.length, 5);assert.equal(state.campus.items[0].id, 'notice');
  let html = renderCampus();
  for (const text of ['不代表学校全部网页', '发布日期未提供', '&lt;选课通知&gt;', '&lt;script&gt;', '下一页', '入选依据', 'target="_blank" rel="noopener noreferrer"']) assert.ok(html.includes(text), text);
  state.campus.section = 'sources';assert.match(renderCampus(), /&lt;网络异常&gt;/);
  state.campus.section = 'services';assert.match(renderCampus(), /学生服务/);
  state.campus.section = 'selected';
  assert.ok(!html.includes('<script>'));assert.ok(!html.includes('href="javascript:'));
  showCampusProfile();assert.match(overlay.innerHTML, /name="next_campus"/);assert.match(overlay.innerHTML, /value="东校园" selected/);
  showCampusWechat();assert.match(overlay.innerHTML, /不登录微信、不绕过验证码/);
  const before = requests.length;
  await setCampusItemState('notice', 'bookmarked');
  assert.equal(requests[before].url, '/api/campus/items/notice/state');assert.deepEqual(JSON.parse(requests[before].options.body), {bookmarked: true});
  state.campus.filters.q = '奖学金';state.campus.section = 'bookmarks';state.campus.offset = 50;
  await loadView();const query = new URL(requests.at(-5).url, 'http://localhost').searchParams;
  assert.equal(query.get('q'), '奖学金');assert.equal(query.get('offset'), '50');assert.equal(query.get('section'), 'bookmarks');
  const form = {reportValidity: () => true, entries: [['campus', '南校园'], ['next_campus', '东校园'], ['faculty', '电子与信息学院'], ['level', '本科'], ['year', '2025'], ['keywords', '奖学金， 竞赛,选课']]};
  const saveIndex = requests.length;await saveCampusProfile(form);
  assert.deepEqual(JSON.parse(requests[saveIndex].options.body).keywords, ['奖学金', '竞赛', '选课']);
  const button = {disabled: false};
  const importIndex = requests.length;
  await importCampusWechat({reportValidity: () => true, entries: [['url', 'https://mp.weixin.qq.com/s/article']], querySelector: () => button});
  assert.equal(requests[importIndex].url, '/api/campus/wechat');assert.equal(button.disabled, false);
  const invalidIndex = requests.length;
  await importCampusWechat({reportValidity: () => true, entries: [['url', 'https://www.sysu.edu.cn/']], querySelector: () => button});
  assert.equal(requests.length, invalidIndex);
  await showCampusWechatAccounts();
  assert.equal(requests.at(-1).url, '/api/campus/wechat-accounts');
  assert.match(overlay.innerHTML, /&lt;康乐解学&gt;/);
  assert.match(overlay.innerHTML, /value="gh_exact" data-account-id="wxid_exact" checked/);
  assert.match(overlay.innerHTML, /18 篇/);
  assert.match(overlay.innerHTML, /不代表已获取文章全文/);
  const localImportIndex = requests.length;
  const accountButton = {disabled: false};
  await importCampusWechatAccounts({querySelector: () => accountButton, querySelectorAll: () => [
    {value: 'gh_exact', dataset: {accountId: 'wxid_exact'}},
    {value: 'gh_second', dataset: {accountId: 'wxid_exact'}},
    {value: 'gh_other_account', dataset: {accountId: 'wxid_other'}},
  ]});
  assert.equal(requests[localImportIndex].url, '/api/campus/wechat-accounts/import');
  assert.deepEqual(JSON.parse(requests[localImportIndex].options.body), {account_id: 'wxid_exact', contact_ids: ['gh_exact', 'gh_second']});
  assert.deepEqual(JSON.parse(requests[localImportIndex + 1].options.body), {account_id: 'wxid_other', contact_ids: ['gh_other_account']});
  assert.equal(accountButton.disabled, false);
  const emptySelectionIndex = requests.length;
  await importCampusWechatAccounts({querySelectorAll: () => []});
  assert.equal(requests.length, emptySelectionIndex);
  state.campus.items = [];assert.match(renderCampus(), /暂无符合条件的信息/);assert.match(renderCampus(), /刷新公开信息/);

  timers.clear();refresh = {state: 'running', processed: 1, total: 3};
  await refreshCampus();assert.equal(requests.at(-1).options.method, 'POST');assert.equal(timers.size, 1);
  state.view = 'search';state.filters.q = '';await loadView();assert.equal(timers.size, 0);assert.equal(state.campus.items.length, 0);

  // A response from an abandoned campus view must not populate another view or restart polling.
  state.view = 'campus';const releases = [];
  context.fetch = () => new Promise(resolve => releases.push(value => resolve({ok: true, json: async () => value})));
  const pending = loadView();state.view = 'search';await loadView();
  releases[0]({items: [{id: 'stale'}], total: 1});releases[1]({items: []});releases[2]({});releases[3]({state: 'running'});releases[4]({});await pending;
  assert.equal(state.campus.items.length, 0);assert.equal(timers.size, 0);
  console.log('PASS campus view: sources, filtering, safe links, escaped notices, profile, WeChat import, item state, background refresh, stale-response isolation');
})().catch(error => {console.error(error);process.exitCode = 1;});
