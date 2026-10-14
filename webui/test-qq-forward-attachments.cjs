const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, 'app.js'), 'utf8');
let clickHandler;
const requests = [];
const revokedUrls = [];
const context = {
  AbortController,
  URL: {createObjectURL: () => 'blob:fixture', revokeObjectURL: url => revokedUrls.push(url)},
  Blob,
  localStorage: {getItem: () => 'light'},
  document: {addEventListener: (name, handler) => {if (name === 'click') clickHandler = handler;}, querySelector: () => null,
    createElement: () => ({click: () => {}, remove: () => {}}), body: {append: () => {}}},
  window: {addEventListener: () => {}},
  matchMedia: () => ({addEventListener: () => {}}),
  fetch: async (url, options = {}) => {
    requests.push({url, options});
    if (options.method === 'POST') return {ok: true, json: async () => ({truncated: true, items: [
      {handle: 'opaque-image', type: 'image', name: 'bad"><img src=x>.png', size: 12, mime_type: 'image/png'},
      {handle: 'opaque-doc', type: 'file', name: 'notes.pdf', size: 2048, mime_type: 'application/pdf'},
    ]})};
    if (url.endsWith('/opaque-image')) return {ok: false, status: 503, json: async () => ({error: 'temporary outage'})};
    throw new Error(`Unexpected request ${url}`);
  },
};
vm.runInNewContext(source.replace('\ninit();\n',
  '\nglobalThis.__state = state; globalThis.__renderQQ = renderQQRoamingContent; globalThis.__loadQQForwardResources = loadQQForwardResources; globalThis.__pruneQQForwardResources = pruneQQForwardResources; globalThis.__revokeQQForwardObjectUrls = revokeQQForwardObjectUrls;\n'), context);

const message = {platform: 'qq', account_id: 'account-1', conversation_id: 'room-2', id: 'message-3', extra: {
  roaming_content: {elements: [{type: 'forward', data: {title: '转发', messages: [
    {sender: 'Alice', content: {text: '附件', resources: [
      {type: 'image', name: 'photo.png'}, {type: 'file', name: 'notes.pdf'},
    ]}},
  ]}}]},
}};
context.__state.messages = [message];
context.__state.selected = {id: 'room-2', platform: 'qq', account_id: 'account-1'};
const button = {dataset: {action: 'qq-forward-read'}, disabled: false,
  closest(selector) {return selector === '.message' ? {dataset: {messageId: 'message-3'}} : selector === '[data-media-select]' ? null : this;}};
clickHandler({target: button}).then(async () => {
  assert.equal(requests[0].url, '/api/qq/forward-resources');
  assert.equal(requests[0].options.method, 'POST');
  assert.deepEqual(JSON.parse(requests[0].options.body), {
    account_id: 'account-1', conversation_id: 'room-2', message_id: 'message-3',
  });
  assert.equal(requests[1].url, '/api/qq/forward-resources/opaque-image');
  const row = context.__state.qqForwardResources.get(JSON.stringify(['account-1', 'room-2', 'message-3']));
  assert.equal(row.items[0].error, 'temporary outage');
  let markup = context.__renderQQ(message, true);
  assert.match(markup, /读取失败/);
  assert.match(markup, /data-action="qq-forward-resource:0"/);
  assert.match(markup, /notes\.pdf/);
  assert.match(markup, /data-action="qq-forward-resource:1"/);
  assert.match(markup, /已列出 2 项 · 服务端结果已截断/);
  assert.match(markup, /bad&quot;&gt;&lt;img src=x&gt;\.png/);
  assert.doesNotMatch(markup, /opaque-image|opaque-doc|<img src=x>/);

  context.fetch = async () => ({ok: true, blob: async () => new Blob(['image bytes'], {type: 'image/png'})});
  const retry = {dataset: {action: 'qq-forward-resource:0'}, disabled: false,
    closest(selector) {return selector === '.message' ? {dataset: {messageId: 'message-3'}} : selector === '[data-media-select]' ? null : this;}};
  await clickHandler({target: retry});
  assert.equal(retry.disabled, false);
  markup = context.__renderQQ(message, true);
  assert.match(markup, /src="blob:fixture"/);

  const videoMessage = {...message, id: 'forwarded-mp4', extra: {roaming_content: {elements: [{type: 'forward', data: {messages: [
    {content: {resources: [{type: 'video', name: 'clip.mp4'}]}},
  ]}}]}}};
  context.__state.messages = [videoMessage];
  context.fetch = async (url, options = {}) => {
    requests.push({url, options});
    if (options.method === 'POST') return {ok: true, json: async () => ({items: [
      {handle: 'opaque-mp4', type: 'video', name: 'clip.mp4', mime_type: 'video/mp4'},
    ]})};
    return {ok: true, blob: async () => new Blob(['mp4 bytes'], {type: 'video/mp4'})};
  };
  await context.__loadQQForwardResources(videoMessage);
  assert.equal(requests.at(-1).url, '/api/qq/forward-resources/opaque-mp4');
  markup = context.__renderQQ(videoMessage, true);
  assert.match(markup, /<video class="qq-forward-video" controls preload="metadata"/);
  assert.match(markup, /type="video\/mp4"/);
  assert.match(markup, /data-action="qq-forward-resource:0">下载 · clip\.mp4/);
  const webmMessage = {...videoMessage, id: 'forwarded-webm'};
  context.fetch = async (url, options = {}) => {
    requests.push({url, options});
    if (options.method === 'POST') return {ok: true, json: async () => ({items: [
      {handle: 'opaque-webm', type: 'video', name: 'clip.webm', mime_type: 'video/webm'},
    ]})};
    return {ok: true, blob: async () => new Blob(['webm bytes'], {type: 'video/webm'})};
  };
  await context.__loadQQForwardResources(webmMessage);
  markup = context.__renderQQ(webmMessage, true);
  assert.match(markup, /<video class="qq-forward-video" controls preload="metadata"/);
  assert.match(markup, /type="video\/webm"/);
  assert.match(markup, /下载 · clip\.webm/);
  context.__revokeQQForwardObjectUrls();
  assert.ok(revokedUrls.includes('blob:fixture'), 'rendered preview URLs should use the existing cleanup lifecycle');

  let active = 0, peak = 0;
  const many = {...message, id: 'many-images', extra: {roaming_content: {elements: [{type: 'forward', data: {messages: [
    {content: {resources: Array.from({length: 8}, (_, i) => ({type: 'image', name: `image-${i}.png`}))}},
  ]}}]}}};
  context.__state.messages = [many];
  context.fetch = async (url, options = {}) => {
    if (options.method === 'POST') return {ok: true, json: async () => ({items: Array.from({length: 8}, (_, i) => ({handle: `image-${i}`, type: 'image', mime_type: 'image/png'}))})};
    active++;peak = Math.max(peak, active);
    await new Promise(resolve => setTimeout(resolve, 2));
    active--;
    return {ok: true, blob: async () => new Blob(['image'], {type: 'image/png'})};
  };
  await context.__loadQQForwardResources(many);
  assert.equal(peak, 4, 'image fetches should be bounded to four concurrent requests');
  const key = JSON.stringify(['account-1', 'room-2', 'many-images']);
  const controller = context.__state.qqForwardResources.get(key).controller;
  context.__state.messages = [];
  context.__pruneQQForwardResources();
  assert.equal(controller.signal.aborted, true, 'leaving the chat should abort outstanding attachment requests');
  console.log('PASS QQ forwarded attachment UI interactions');
}).catch(error => {console.error(error);process.exitCode = 1;});
