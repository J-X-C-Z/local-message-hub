const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, 'app.js'), 'utf8');
const initCall = '\ninit();\n';
assert.ok(source.includes(initCall));

async function main() {
const requests = [];
const observers = [];
const previews = new Map();
const previewFor = index => {
  if (!previews.has(index)) previews.set(index, {innerHTML: ''});
  return previews.get(index);
};
class FakeIntersectionObserver {
  constructor(callback, options) { this.callback = callback; this.options = options; observers.push(this); }
  observe(target) { (this.targets ||= []).push(target); }
  unobserve(target) { this.targets = this.targets.filter(item => item !== target); }
  disconnect() { this.targets = []; this.disconnected = true; }
  show(indices) {
    this.callback(indices.map(index => ({
      target: {dataset: {mediaPreviewTile: String(index)}}, isIntersecting: true,
    })), this);
  }
}
const context = {
  URL,
  URLSearchParams,
  AbortController,
  localStorage: {getItem: () => 'light'},
  document: {
    addEventListener: () => {},
    querySelector: selector => {
      const match = selector.match(/data-media-preview-tile="(\d+)"/);
      if (!match) return null;
      return selector.endsWith('.media-preview') ? previewFor(Number(match[1]))
        : {dataset: {mediaPreviewTile: match[1]}};
    },
  },
  IntersectionObserver: FakeIntersectionObserver,
  window: {addEventListener: () => {}},
  matchMedia: () => ({addEventListener: () => {}}),
  fetch: (url, options) => new Promise((resolve, reject) => {
    const request = {url, options, resolve, reject};
    requests.push(request);
    options.signal.addEventListener('abort', () => {
      const error = new Error('aborted');
      error.name = 'AbortError';
      reject(error);
    }, {once: true});
  }),
};
const controlledFetch = context.fetch;
vm.runInNewContext(source.replace(initCall,
  '\nglobalThis.__startMediaPreviews = startMediaPreviews; globalThis.__cancelMediaPreviews = cancelMediaPreviews; globalThis.__state = state; globalThis.__resolveMedia = resolveMedia; globalThis.__loadBestWechatImage = loadBestWechatImage; globalThis.__renderMediaTile = renderMediaTile; globalThis.__previewMedia = previewMedia; globalThis.__typeOptions = typeOptions; globalThis.__stubMediaUi = (render, preview) => { renderView = render; previewMedia = preview; };\n'), context);

const state = context.__state;
state.view = 'media';
state.filters.source = 'qq-cache';
const qqCacheTypes = context.__typeOptions('');
for (const type of ['image', 'video', 'voice', 'file']) assert.match(qqCacheTypes, new RegExp(`value="${type}"`));
assert.doesNotMatch(qqCacheTypes, /value="sticker"/, 'QQ cache gallery hides unsupported sticker filtering');
state.filters.source = 'all';
assert.match(context.__typeOptions(''), /value="sticker"/, 'chat media retains the cached sticker filter');
const avatarTile = context.__renderMediaTile({type: 'image', name: 'fixture.png', resourceKind: 'message',
  resourceIndex: 0, resource: {available: true}, message: {id: 'fixture', platform: 'wecom', sender_name: 'Synthetic sender',
    sender_avatar_url: 'https://wework.qpic.cn/avatar.png', timestamp: 1, media_available: true}}, 0);
assert.match(avatarTile, /class="avatar wecom media-avatar"/);
assert.match(avatarTile, /src="\/api\/avatar\?url=https%3A%2F%2Fwework\.qpic\.cn%2Favatar\.png"/);
assert.match(avatarTile, /Synthetic sender/);
const cacheTile = context.__renderMediaTile({type: 'image', name: 'cached.png', resourceKind: 'qq_cache',
  resourceIndex: null, resource: {available: true}, message: {id: 'cached', platform: 'qq', timestamp: 1}}, 1);
assert.doesNotMatch(cacheTile, /media-sender/);
const heicTile = context.__renderMediaTile({type: 'image', name: 'photo.heic', resourceKind: 'qq_cache',
  resourceIndex: null, resource: null,
  message: {id: 'heic', platform: 'qq', timestamp: 1,
    extra: {resource_kind: 'qq_cache', mime_type: 'image/heic'}}}, 2);
assert.match(heicTile, /浏览器不支持预览/);
assert.doesNotMatch(heicTile, /<img/);
let openedModal;
context.modal = (...args) => { openedModal = args; };
context.__previewMedia('/local-media/heic-token', 'image', 'photo.heic', 'image/heic');
assert.match(openedModal[1], /当前浏览器无法预览 HEIC 图片/);
assert.match(openedModal[1], /href="\/local-media\/heic-token" download="photo\.heic"/);
context.__stubMediaUi(() => {}, context.__previewMedia);
context.fetch = async () => ({ok: true, json: async () => ({
  media_url: '/local-media/qq-heic-token', mime_type: 'image/heic', download_name: 'photo.heic',
})});
await context.__resolveMedia({id: 'heic', platform: 'qq', account_id: 'fixture-account',
  type: 'image', file_name: 'photo.heic',
  extra: {resource_kind: 'qq_cache', resource_id: 'opaque-heic', mime_type: 'image/heic'}});
assert.match(openedModal[1], /当前浏览器无法预览 HEIC 图片/,
  'QQ cache resolution carries the image MIME into the modal');
assert.match(openedModal[1], /href="\/local-media\/qq-heic-token" download="photo\.heic"/,
  'QQ HEIC preview offers its original local file');
context.fetch = controlledFetch;
state.view = 'media';
state.loading = false;
state.request = 9;
const images = Array.from({length: 2}, (_, index) => ({
  id: `wechat-${index}`, platform: 'wechat', account_id: 'fixture-account',
  conversation_id: 'fixture-room', type: 'image', timestamp: 1,
}));
state.items = [...images,
  {id: 'sticker', platform: 'wechat', account_id: 'fixture-account', conversation_id: 'fixture-room', type: 'image', raw_type: '47'},
  {id: 'animated-sticker', platform: 'wechat', account_id: 'fixture-account', conversation_id: 'fixture-room', type: 'image', raw_type: '11000'},
  {id: 'qq-image', platform: 'qq', account_id: 'fixture-account', conversation_id: 'fixture-room',
    media_entry: {type: 'image', resource_index: 7, resource: {type: 'image', name: 'qq.png'}}},
  {id: 'wechat-video', platform: 'wechat', account_id: 'fixture-account', conversation_id: 'fixture-room', type: 'video'},
  {id: 'wecom-attachments', platform: 'wecom', account_id: 'fixture-account', conversation_id: 'fixture-room',
    type: 'attachment', extra: {attachments: [{type: 'image', name: 'fixture.png'}]}},
];

context.__startMediaPreviews(9);
assert.equal(requests.length, 0, 'offscreen image tiles do not resolve before intersection');
assert.equal(observers[0].options.rootMargin, '240px', 'near-viewport tiles are eligible');
assert.equal(observers[0].targets.length, 5, 'WeChat images, video posters, and indexed WeCom/QQ image attachments are observed');
observers[0].show([0, 1, 3, 4, 5]);
assert.equal(requests.length, 3, 'only three resolvers run concurrently');
for (const request of requests.slice(0, 2)) {
  const url = new URL(request.url, 'http://localhost');
  assert.equal(url.pathname, '/api/media/resolve');
  assert.equal(url.searchParams.get('image_tier'), 'mid');
  assert.equal(url.searchParams.get('platform'), 'wechat');
  assert.match(url.searchParams.get('message_id'), /^wechat-\d$/);
  assert.equal(request.options.signal instanceof AbortSignal, true);
}
const qqParams = new URL(requests[2].url, 'http://localhost').searchParams;
assert.equal(qqParams.get('platform'), 'qq');
assert.equal(qqParams.get('account_id'), 'fixture-account');
assert.equal(qqParams.get('message_id'), 'qq-image');
assert.equal(qqParams.get('resource_index'), '7');
context.__cancelMediaPreviews();
assert.ok(requests.every(request => request.options.signal.aborted), 'page changes abort in-flight resolvers');
assert.equal(observers[0].disconnected, true, 'page changes disconnect the visibility observer');
observers[0].show([0, 1, 4]);
assert.equal(requests.length, 3, 'aborted page does not schedule remaining images');
assert.ok(images.every(message => !message.media_url), 'aborted results do not mutate the old page');

const one = {id: 'wechat-visible', platform: 'wechat', account_id: 'fixture-account',
  conversation_id: 'fixture-room', type: 'image', timestamp: 1};
const otherImage = {id: 'wechat-other', platform: 'wechat', account_id: 'fixture-account',
  conversation_id: 'fixture-room', type: 'image', timestamp: 1, media_url: '/local-media/other-mid'};
state.items = [one, otherImage];
context.fetch = async (url, options) => {
  assert.equal(new URL(url, 'http://localhost').searchParams.get('image_tier'), 'mid');
  return {ok: true, json: async () => ({media_url: '/local-media/fixture-token'})};
};
context.__startMediaPreviews(9);
observers[1].show([0]);
await new Promise(resolve => setImmediate(resolve));
assert.equal(one.media_url, '/local-media/fixture-token', 'resolved local thumbnail is attached to its message');
const bestTile = context.__renderMediaTile({message: one, type: 'image', name: 'wechat.png',
  resourceKind: 'message', resourceIndex: null, resource: null}, 0);
assert.match(bestTile, /data-action="wechat-best-image:0"/);
assert.match(bestTile, /title="读取本机缓存中的最佳画质"/);
const otherPreview = previewFor(1).innerHTML;
context.toast = () => {};
context.fetch = async url => {
  const params = new URL(url, 'http://localhost').searchParams;
  assert.equal(params.get('image_tier'), 'best');
  assert.equal(params.get('platform'), 'wechat');
  assert.equal(params.get('message_id'), 'wechat-visible');
  assert.equal(params.get('conversation_id'), 'fixture-room');
  return {ok: true, json: async () => ({media_url: '/local-media/fixture-best', mime_type: 'image/png'})};
};
await context.__loadBestWechatImage(0);
assert.equal(one.media_url, '/local-media/fixture-best', 'explicit action replaces only the selected image preview');
assert.equal(one.media_mime_type, 'image/png');
assert.equal(otherImage.media_url, '/local-media/other-mid', 'other image previews are unchanged');
assert.equal(previewFor(0).innerHTML, '<img src="/local-media/fixture-best" alt="图片" loading="lazy">');
assert.equal(previewFor(1).innerHTML, otherPreview, 'only the selected tile DOM is updated');
const retainedBestUrl = one.media_url;
const retainedPreview = previewFor(0).innerHTML;
context.fetch = async () => ({ok: true, json: async () => ({media_url: null, available: false})});
await context.__loadBestWechatImage(0);
assert.equal(one.media_url, retainedBestUrl, 'a local-cache miss preserves the current preview');
assert.equal(previewFor(0).innerHTML, retainedPreview, 'a local-cache miss leaves the tile unchanged');

const video = {id: 'wechat-poster', platform: 'wechat', account_id: 'fixture-account',
  conversation_id: 'fixture-room', type: 'video', timestamp: 1};
state.items = [video];
context.fetch = async url => {
  assert.equal(new URL(url, 'http://localhost').searchParams.get('video_variant'), 'poster');
  return {ok: true, json: async () => ({media_url: '/local-media/poster-token'})};
};
context.__startMediaPreviews(9);
observers[2].show([0]);
await new Promise(resolve => setImmediate(resolve));
assert.equal(video.media_poster_url, '/local-media/poster-token', 'poster is stored separately from the playable video URL');
assert.equal(video.media_url, undefined, 'poster resolution does not replace the MP4 resolver state');

const wecomImage = {id: 'wecom-image', platform: 'wecom', account_id: 'fixture-account',
  conversation_id: 'fixture-room', type: 'attachment',
  extra: {attachments: [{type: 'image', name: 'fixture.png'}]}};
state.items = [wecomImage];
context.fetch = async url => {
  const params = new URL(url, 'http://localhost').searchParams;
  assert.equal(params.get('platform'), 'wecom');
  assert.equal(params.get('message_id'), 'wecom-image');
  assert.equal(params.get('resource_index'), '0');
  return {ok: true, json: async () => ({media_url: '/local-media/wecom-token'})};
};
context.__startMediaPreviews(9);
observers[3].show([0]);
await new Promise(resolve => setImmediate(resolve));
assert.equal(wecomImage.extra.attachments[0].media_url, '/local-media/wecom-token',
  'resolved WeCom attachment thumbnail is attached to its resource');

const qqImage = {id: 'qq-visible', platform: 'qq', account_id: 'fixture-account',
  conversation_id: 'fixture-room', media_entry: {type: 'image', resource_index: 4,
    resource: {type: 'image', name: 'qq.png'}}};
state.items = [qqImage];
context.fetch = async url => {
  const params = new URL(url, 'http://localhost').searchParams;
  assert.equal(params.get('platform'), 'qq');
  assert.equal(params.get('message_id'), 'qq-visible');
  assert.equal(params.get('resource_index'), '4');
  return {ok: true, json: async () => ({media_url: '/local-media/qq-token'})};
};
context.__startMediaPreviews(9);
observers[4].show([0]);
await new Promise(resolve => setImmediate(resolve));
assert.equal(qqImage.media_entry.resource.media_url, '/local-media/qq-token',
  'resolved QQ roaming thumbnail is attached to its indexed resource');

const pagedFile = {id: 'paged-file', platform: 'wecom', account_id: 'fixture-account',
  conversation_id: 'fixture-room', media_entry: {resource_index: 3, type: 'file', resource: {
    name: 'report.pdf', type: 'file',
  }}};
const resolvedCalls = [];
let preview;
context.__stubMediaUi(() => {}, (...args) => {preview = args;});
context.fetch = async url => {
  resolvedCalls.push(new URL(url, 'http://localhost'));
  return {ok: true, json: async () => ({media_url: '/local-media/paged-file-token'})};
};
await context.__resolveMedia(pagedFile, 3);
assert.equal(resolvedCalls[0].searchParams.get('resource_index'), '3');
assert.equal(pagedFile.media_entry.resource.media_url, '/local-media/paged-file-token',
  'paged attachment rows resolve against their selected media_entry resource');
assert.deepEqual(preview, ['/local-media/paged-file-token', 'file', 'report.pdf', undefined]);

const qqCacheImage = {id: 'opaque-qq-cache-image', platform: 'qq', account_id: 'fixture-account',
  type: 'image', file_name: 'cached.png', media_available: true,
  extra: {resource_kind: 'qq_cache', resource_id: 'opaque-qq-cache-image', mime_type: 'image/png'}};
state.items = [qqCacheImage];
context.fetch = async url => {
  const params = new URL(url, 'http://localhost').searchParams;
  assert.equal(params.get('resource_kind'), 'qq_cache');
  assert.equal(params.get('platform'), 'qq');
  assert.equal(params.get('account_id'), 'fixture-account');
  assert.equal(params.get('resource_id'), 'opaque-qq-cache-image');
  assert.equal(params.has('image_tier'), false);
  return {ok: true, json: async () => ({media_url: '/local-media/qq-cache-image', mime_type: 'image/png'})};
};
context.__startMediaPreviews(9);
observers[5].show([0]);
await new Promise(resolve => setImmediate(resolve));
assert.equal(qqCacheImage.media_url, '/local-media/qq-cache-image',
  'QQ global-cache images use the opaque local resolver identity');
assert.equal(qqCacheImage.media_mime_type, 'image/png');
assert.equal(previewFor(0).innerHTML,
  '<img src="/local-media/qq-cache-image" alt="cached.png" loading="lazy">');

console.log('PASS visible WeChat, WeCom, and QQ images resolve through bounded local preview paths');
}

main().catch(error => {console.error(error);process.exitCode = 1;});
