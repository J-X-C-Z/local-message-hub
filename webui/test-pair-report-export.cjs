const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, 'app.js'), 'utf8');
let csv = '', filename = '', requestUrl = '', overlay = {innerHTML: ''};
const context = {
  URLSearchParams,
  URL: {createObjectURL: () => 'blob:fixture', revokeObjectURL: () => {}},
  Blob: class {constructor(parts) {csv = parts.join('');}},
  fetch: async url => {
    requestUrl = url;
    return {ok: true, json: async () => ({platform: 'wechat', account_id: 'fixture', conversation_id: 'peer',
      conversation_title: 'Peer', year: 2024, total_messages: 1, sent_messages: 1, counterpart_messages: 0,
      active_days: 1, sent_ratio: 1, counterpart_name: 'Peer', response_count: 2,
      response_avg_seconds: 900, response_fastest_seconds: 600,
      hourly: [{hour: 9, count: 1}], message_types: []})};
  },
  localStorage: {getItem: () => 'light'},
  document: {
    addEventListener: () => {},
    querySelector: selector => selector === '#overlay' ? overlay : selector === '#toasts' ? {append: () => {}} : {focus: () => {}},
    querySelectorAll: () => [],
    createElement: () => ({set download(value) {filename = value;}, click: () => {}, remove: () => {}}),
    body: {append: () => {}}
  },
  window: {addEventListener: () => {}},
  matchMedia: () => ({addEventListener: () => {}}),
  setTimeout: () => {}
};
vm.runInNewContext(source.replace('\ninit();\n',
  '\nglobalThis.__downloadPairReport = downloadPairReport; globalThis.__downloadAnnualReport = downloadAnnualReport; globalThis.__renderAnnualReport = renderAnnualReport; globalThis.__showPairReport = showPairReport; globalThis.__state = state;\n'), context);

context.__state.pairReport = {
  platform: 'wechat', conversation_title: 'Private chat', year: null,
  counterpart_name: '=1+1', counterpart_id: 'peer', total_messages: 3,
  sent_messages: 1, counterpart_messages: 2,
  response_count: 2, response_avg_seconds: 900, response_fastest_seconds: 600,
  active_days: 2, sent_ratio: 1 / 3, first_timestamp: 1, last_timestamp: 2,
  hourly: [{hour: 9, count: 3}], message_types: [{type: 'text', count: 3}]
};
context.__downloadPairReport();
assert.equal(filename, '双人报告-wechat-全部时间.csv');
assert.ok(csv.startsWith('\uFEFF'));
assert.match(csv, /'=1\+1/);
assert.match(csv, /对方消息/);
assert.match(csv, /平均回复时长（秒）","900/);
assert.match(csv, /最快回复时长（秒）","600/);
assert.match(csv, /全部时间/);
assert.doesNotMatch(csv, /消息正文|secret body/);
context.__state.platform = 'wechat';
context.__state.loading = false;
context.__state.reportYear = 2024;
context.__state.annualReport = {
  year: 2024,
  total_messages: 4, sent_messages: 2, received_messages: 2, active_days: 3, sent_ratio: 0.5,
  first_timestamp: 1, last_timestamp: 2,
  busiest_day: {date: '2024-06-01', count: 3}, busiest_hour: {hour: 9, count: 2},
  activity_heatmap: Array.from({length: 7}, () => Array(24).fill(0)),
  longest_streak: {days: 3, start_date: '2024-05-30', end_date: '2024-06-01'},
  monthly: [], hourly: [{hour: 9, count: 2}], message_types: [],
  top_contacts: [{platform: 'wechat', name: 'Peer', count: 2}],
};
const annualHtml = context.__renderAnnualReport({}, '');
assert.match(annualHtml, /年度来信联系人/);
assert.match(annualHtml, /全部时间/);
assert.match(annualHtml, /report-year-scope/);
assert.match(annualHtml, /24 小时消息分布/);
assert.match(annualHtml, /aria-label="24 小时消息分布，时区 UTC\+08:00；00:00 0 条；.*09:00 2 条/);
assert.match(annualHtml, /09:00 · 2 条/);
assert.match(annualHtml, /最长连续活跃 <strong>3 天/);
assert.match(annualHtml, /活跃热图/);
assert.match(annualHtml, /aria-label="按星期和小时统计的消息热图/);
assert.equal((annualHtml.match(/data-level="0"/g) || []).length, 168);
assert.equal((annualHtml.match(/class="hour-bar"/g) || []).length, 24);
assert.match(annualHtml, /Peer/);
assert.doesNotMatch(annualHtml, /sender_id|account_id/);
context.__downloadAnnualReport();
assert.equal(filename, '年度报告-2024-wechat.csv');
assert.match(csv, /最活跃日消息数/);
assert.match(csv, /最长连续活跃日/);
assert.match(csv, /2024-05-30/);
assert.match(csv, /星期时段/);
assert.match(csv, /2024-06-01/);
assert.match(csv, /最活跃时段消息数/);
assert.match(csv, /首次消息/);
assert.match(csv, /最近消息/);
assert.match(csv, /来信联系人/);
assert.match(csv, /微信 · Peer/);
context.__state.annualReport.year = null;
const allTimeHtml = context.__renderAnnualReport({}, '');
assert.match(allTimeHtml, /全部时间/);
context.__downloadAnnualReport();
assert.equal(filename, '年度报告-全部时间-wechat.csv');
assert.match(csv, /全部时间/);

(async () => {
  await context.__showPairReport({platform: 'wechat', account_id: 'fixture', id: 'peer', title: 'Peer', kind: 'private'}, 2024);
  assert.match(requestUrl, /\/api\/reports\/pair\?/);
  assert.match(requestUrl, /year=2024/);
  assert.match(overlay.innerHTML, /2024 年/);
  assert.match(overlay.innerHTML, /平均回复<\/div><strong>15 分钟/);
  assert.match(overlay.innerHTML, /最快回复<\/div><strong>10 分钟/);
  assert.match(overlay.innerHTML, /有效回复 2 次/);
  assert.match(overlay.innerHTML, /<div class="hour-chart" role="img" aria-label="24 小时消息分布，时区 UTC\+08:00；00:00 0 条；.*09:00 1 条/);
  assert.match(overlay.innerHTML, /class="hour-bar" aria-hidden="true"/);
  assert.equal((overlay.innerHTML.match(/class="hour-bar" aria-hidden="true"/g) || []).length, 24);
  assert.match(overlay.innerHTML, /title="00:00 · 0 条"/);
  assert.match(overlay.innerHTML, /title="09:00 · 1 条"/);
  console.log('PASS pair range API and annual/pair CSV exports');
})().catch(error => {console.error(error);process.exitCode = 1;});
