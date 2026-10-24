const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const timers = new Map(), requests = [], notices = [];
let timerId = 0;
let scoring = {enabled: false, running: false, completed: 0, pending: 2, error: 0, insufficient: 1};
const article = {id: 'a', title: '奖学金通知', url: 'https://www.sysu.edu.cn/notice', summary: '本科生申请材料'};
const context = {
  URL, URLSearchParams, localStorage: {getItem: () => 'light'},
  document: {addEventListener() {}, querySelector: () => null, querySelectorAll: () => []},
  window: {addEventListener() {}}, matchMedia: () => ({addEventListener() {}}),
  setTimeout(fn, delay) {if (!delay) return 0;timers.set(++timerId, {fn, delay});return timerId;},
  clearTimeout(id) {timers.delete(id);}, recordNotice: (...args) => notices.push(args),
};
const source = fs.readFileSync(path.join(__dirname, 'app.js'), 'utf8');
vm.runInNewContext(source.replace('\ninit();\n', '\nglobalThis.__test = {state, loadView, renderCampus, renderCampusScoring, updateCampusScoring, scheduleCampusPoll};\nrenderView = () => {};toast = recordNotice;\n'), context);
const {state, loadView, renderCampus, renderCampusScoring, updateCampusScoring, scheduleCampusPoll} = context.__test;
context.fetch = async (url, options = {}) => {
  requests.push({url, options});
  const route = url.split('?')[0];
  if (route === '/api/campus/scoring' && options.method === 'POST') scoring = {...scoring, ...JSON.parse(options.body)};
  if (route === '/api/campus/scoring/run') scoring = {...scoring, running: true};
  const data = route.startsWith('/api/campus/scoring') ? scoring : route === '/api/campus/briefing' ? {items: [article], total: 1, pipeline: {scoring}} : route === '/api/campus/updates' ? {auto_updates_enabled: false} : route === '/api/campus/refresh' ? {state: 'idle'} : route === '/api/campus/sources' ? {items: []} : {};
  return {ok: true, json: async () => data};
};
(async () => {
  state.view = 'campus';state.campus.section = 'pipeline';await loadView();
  assert.equal(timers.size, 0);
  assert.match(renderCampus(), /模型评分未开启，当前使用本地规则/);
  const button = {disabled: false};await updateCampusScoring(button);
  assert.deepEqual(JSON.parse(requests.find(r => r.options.method === 'POST').options.body), {enabled: true});
  assert.equal(button.disabled, false);assert.equal(timers.size, 1);
  assert.match(renderCampus(), /自动评分已开启/);assert.doesNotMatch(renderCampus(), /未配置模型处理/);
  await updateCampusScoring(button, true);
  assert.deepEqual(JSON.parse(requests.find(r => r.url.endsWith('/scoring/run')).options.body), {});
  assert.equal([...timers.values()][0].delay, 1500);
  const pending = [...timers.values()][0];timers.clear();
  scoring = {...scoring, running: false, completed: 1, pending: 1};
  await pending.fn();
  assert.equal(state.campus.briefing.pipeline.scoring.completed, 1);
  assert.equal([...timers.values()][0].delay, 5000);
  const complete = {scoring: {status: 'completed', score: 82, selected: true, material_basis: 'summary_only', models: [
    {model: 'westlake-hpc/deepseek-flash', score: 80, eligible: true, reasons: ['本科生可申请', '<script>unsafe()</script>']},
    {model: 'westlake-hpc/glm-flash', score: 84, eligible: true, reasons: ['奖学金申请']},
  ]}};
  let html = renderCampusScoring(complete);
  assert.match(html, /综合 82 分/);assert.match(html, /DeepSeek 80 分/);assert.match(html, /GLM 84 分/);
  assert.match(html, /依据现有摘要/);assert.match(html, /&lt;script&gt;/);assert.doesNotMatch(html, /<script>/);
  complete.scoring.material_basis = 'full_text';assert.match(renderCampusScoring(complete), /依据正文（最多12000字符）/);
  for (const [status, label] of [['pending', '等待双模型评分'], ['error', '评分未完成'], ['insufficient', '内容不足']]) {
    html = renderCampusScoring({scoring: {status, error: '<error>'}});assert.match(html, new RegExp(label));assert.doesNotMatch(html, /综合 .* 分/);
  }
  state.campus.section = 'selected';assert.match(renderCampus(), /西湖 DeepSeek 与 GLM 独立评分后精选/);
  await updateCampusScoring(button);assert.equal(scoring.enabled, false);assert.equal(timers.size, 0);
  scoring.running = true;state.campus.briefing.pipeline.scoring = scoring;scheduleCampusPoll();assert.equal(timers.size, 1, 'a running job is polled even after disable');
  state.view = 'search';state.filters.q = '';await loadView();assert.equal(timers.size, 0);
  console.log('PASS campus scoring: independent scores, evidence labels, escaped reasons, truthful states, enable/run/disable, background progress and navigation isolation');
})().catch(error => {console.error(error);process.exitCode = 1;});
