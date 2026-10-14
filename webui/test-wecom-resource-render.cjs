const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, 'app.js'), 'utf8');
const initCall = '\ninit();\n';
assert.ok(source.includes(initCall), 'app bootstrap invocation is available to isolate media tests');

const context = {
  URL,
  localStorage: {getItem: () => 'light'},
  document: {addEventListener: () => {}, querySelector: () => context.overlay},
  window: {addEventListener: () => {}},
  overlay: {innerHTML: ''},
  setTimeout: () => 0,
  matchMedia: () => ({addEventListener: () => {}}),
};
vm.runInNewContext(source.replace(initCall,
  '\nglobalThis.__mediaEntries = mediaEntries; globalThis.__resourceExportItems = resourceExportItems; globalThis.__renderMediaTile = renderMediaTile; globalThis.__renderMessage = renderMessage; globalThis.__renderResourceToolbar = renderResourceToolbar; globalThis.__state = state; globalThis.__mediaEntryKey = mediaEntryKey; globalThis.__mediaEntryAvailable = mediaEntryAvailable; globalThis.__renderListing = renderListing; globalThis.__previewMedia = previewMedia;\n'), context);

const wecomMessage = {
  platform: 'wecom', account_id: 'fixture-account', id: 'fixture-message',
  conversation_id: 'fixture-room', type: 'image', file_name: 'first.png',
  extra: {attachments: [
    {name: 'first.png', type: 'image', size: 10},
    {name: 'second.pdf', type: 'file', size: 20},
  ]},
};
context.__state.contextMessage = null;
const wechatImage = {platform: 'wechat', account_id: 'fixture-account', id: 'wechat-image',
  conversation_id: 'fixture-room', type: 'image'};
context.__state.items = [wechatImage];
const wechatImageEntry = context.__mediaEntries()[0];
context.__state.selectedMedia.set(context.__mediaEntryKey(wechatImageEntry), wechatImageEntry);
const tierToolbar = context.__renderResourceToolbar();
assert.match(tierToolbar, /aria-label="微信图片导出质量"/);
assert.match(tierToolbar, /value="best" selected/);
assert.deepEqual(JSON.parse(JSON.stringify(context.__resourceExportItems([wechatImageEntry]))), [{
  platform: 'wechat', account_id: 'fixture-account', message_id: 'wechat-image',
  conversation_id: 'fixture-room', image_tier: 'best',
}]);
context.__state.imageExportTier = 'thumb';
assert.deepEqual(JSON.parse(JSON.stringify(context.__resourceExportItems([wechatImageEntry]))), [{
  platform: 'wechat', account_id: 'fixture-account', message_id: 'wechat-image',
  conversation_id: 'fixture-room', image_tier: 'thumb',
}]);
const emoji = {platform: 'wechat', account_id: 'fixture-account', id: 'wechat-emoji',
  conversation_id: 'fixture-room', type: 'image', raw_type: '47', text: '[表情]',
  media_url: '/local-media/fixture', timestamp: 1};
context.__state.selectedMedia.clear();
context.__state.items = [emoji];
const emojiEntry = context.__mediaEntries()[0];
assert.equal(emojiEntry.type, 'image', 'stickers retain image media/filter semantics');
assert.equal(context.__mediaEntryAvailable(emojiEntry), true);
context.__state.selectedMedia.set(context.__mediaEntryKey(emojiEntry), emojiEntry);
assert.match(context.__renderResourceToolbar(), /微信图片导出质量/);
context.__state.view = 'media';
context.__state.loading = false;
const emojiGallery = context.__renderListing();
assert.match(emojiGallery, /media-open[^>]*aria-label="预览图片"/);
assert.match(emojiGallery, /<img src="\/local-media\/fixture" alt="微信表情（仅本机缓存）"/);
assert.match(emojiGallery, /<input type="checkbox" data-media-select="0" checked[^>]*><span>加入批量导出<\/span>/);
assert.doesNotMatch(emojiGallery, /不支持解码/);
assert.match(context.__renderMessage(emoji), /data-media-url="\/local-media\/fixture"/);
emoji.media_available = false;
assert.equal(context.__mediaEntryAvailable(emojiEntry), false);
assert.match(context.__renderListing(), /media-open[^>]*disabled[^>]*aria-label="预览图片"/);
assert.match(context.__renderListing(), /<input type="checkbox" data-media-select="0" checked disabled/);
emoji.media_available = true;
context.__state.selectedMedia.clear();
const bubble = context.__renderMessage({...wecomMessage, sender_name: 'Alice', timestamp: 1, text: '附件说明'});
assert.match(bubble, /first\.png/);
assert.match(bubble, /second\.pdf/);
assert.match(bubble, /10 B/);
assert.match(bubble, /20 B/);
assert.match(bubble, /data-action="resolve-media:fixture-message:0"/);
assert.match(bubble, /data-action="resolve-media:fixture-message:1"/);
assert.equal((bubble.match(/class="roaming-resource"/g) || []).length, 2);
context.__state.items = [wecomMessage];
const wecomEntries = context.__mediaEntries();
context.__state.selectedMedia.set(context.__mediaEntryKey(wecomEntries[0]), wecomEntries[0]);
assert.doesNotMatch(context.__renderResourceToolbar(), /微信图片导出质量/);
context.__state.selectedMedia.clear();
assert.equal(wecomEntries.length, 2);
assert.deepEqual(Array.from(wecomEntries, entry => [entry.name, entry.type, entry.resourceIndex]), [
  ['first.png', 'image', 0],
  ['second.pdf', 'file', 1],
]);
assert.deepEqual(JSON.parse(JSON.stringify(context.__resourceExportItems([wecomEntries[1]]))), [{
  platform: 'wecom', account_id: 'fixture-account', message_id: 'fixture-message',
  conversation_id: 'fixture-room', resource_index: 1,
}]);
context.__state.items = [{...wecomMessage, media_entry: {
  resource_index: 1, resource: {name: 'second.pdf', type: 'file', size: 20}, type: 'file',
}}];
const projectedEntries = context.__mediaEntries();
assert.equal(projectedEntries.length, 1, 'server-paged attachment rows are not expanded twice');
assert.equal(projectedEntries[0].name, 'second.pdf');
assert.deepEqual(JSON.parse(JSON.stringify(context.__resourceExportItems(projectedEntries))), [{
  platform: 'wecom', account_id: 'fixture-account', message_id: 'fixture-message',
  conversation_id: 'fixture-room', resource_index: 1,
}]);
const localizedWecom = {...wecomMessage, id: 'localized-wecom', extra: {attachments: [
  {name: 'photo.png', type: '图片', media_url: '/local/photo'},
  {name: 'voice.amr', type: '语音', media_url: '/local/voice'},
  {name: 'clip.mp4', type: '视频', media_url: '/local/video'},
  {name: 'notes.dat', type: '文件', media_url: '/local/file'},
]}};
context.__state.items = [localizedWecom];
const localizedEntries = context.__mediaEntries();
assert.deepEqual(Array.from(localizedEntries, entry => entry.type), ['image', 'voice', 'video', 'file']);
const localizedBubble = context.__renderMessage(localizedWecom);
assert.match(localizedBubble, /<img class="media-img" src="\/local\/photo" alt="photo\.png"/);
assert.match(localizedBubble, /<audio src="\/local\/voice" controls/);
assert.match(localizedBubble, /<video src="\/local\/video" controls/);
assert.match(localizedBubble, /download>.*notes\.dat/s);

const qqMessage = {
  platform: 'qq', account_id: 'fixture-account', id: 'qq-message', conversation_id: 'qq-room',
  extra: {roaming_resources: [
    {name: 'roaming.png', type: 'image'},
    {name: 'roaming.mp4', type: 'video'},
  ]},
};
context.__state.items = [qqMessage];
const qqEntries = context.__mediaEntries();
assert.deepEqual(Array.from(qqEntries, entry => [entry.name, entry.type, entry.resourceIndex]), [
  ['roaming.png', 'image', 0],
  ['roaming.mp4', 'video', 1],
]);
assert.deepEqual(JSON.parse(JSON.stringify(context.__resourceExportItems([qqEntries[1]]))), [{
  platform: 'qq', account_id: 'fixture-account', message_id: 'qq-message',
  conversation_id: 'qq-room', resource_index: 1,
}]);

const qqMediaAlias = {...qqMessage, id: 'qq-media-alias', extra: {media: [
  {name: 'alias.png', type: 'image'},
]}};
context.__state.items = [qqMediaAlias];
const aliasEntries = context.__mediaEntries();
assert.deepEqual(Array.from(aliasEntries, entry => [entry.name, entry.type, entry.resourceIndex]), [
  ['alias.png', 'image', 0],
]);
assert.match(context.__renderMessage(qqMediaAlias), /data-action="resolve-media:qq-media-alias:0"/,
  'QQ media alias remains previewable from the chat bubble');
context.__state.items = [{...qqMediaAlias, id: 'qq-empty-primary', extra: {
  roaming_resources: [], media: [{name: 'ignored.png', type: 'image'}],
}}];
assert.notEqual(context.__mediaEntries()[0].name, 'ignored.png',
  'an explicit roaming_resources array remains authoritative over the compatibility alias');

context.__state.items = [{resource_kind: 'moment', platform: 'wechat', account_id: 'fixture-account',
  id: 'post-1', post_id: 'post-1', media_index: 0, variant: 'original', timestamp: 100,
  media_entry: {resource_kind: 'moment', post_id: 'post-1', media_index: 0, variant: 'original',
    resource: {name: 'moment.jpg', media_url: '/local-moment-media/abcdefghijklmnopqrstuvwx12345678'}, type: 'image'}}];
const momentEntries = context.__mediaEntries();
assert.equal(momentEntries.length, 1);
assert.equal(context.__mediaEntryAvailable(momentEntries[0]), true);
assert.match(context.__renderMediaTile(momentEntries[0], 0), /<img src="\/local-moment-media\/abcdefghijklmnopqrstuvwx12345678"/,
  'cached Moments images render their local media token as an image');
assert.deepEqual(JSON.parse(JSON.stringify(context.__resourceExportItems(momentEntries))), [{
  resource_kind: 'moment', platform: 'wechat', account_id: 'fixture-account',
  post_id: 'post-1', media_index: 0, variant: 'original',
}]);
assert.match(context.__mediaEntryKey(momentEntries[0]), /moment/);
context.__state.items[0].media_entry.resource.media_url = null;
assert.equal(context.__mediaEntryAvailable(context.__mediaEntries()[0]), false);

context.__state.items = [{resource_kind: 'moment', platform: 'wechat', account_id: 'fixture-account',
  id: 'post-video', post_id: 'post-video', media_index: 0, variant: 'video', timestamp: 100,
  media_entry: {resource_kind: 'moment', post_id: 'post-video', media_index: 0, variant: 'video',
    resource: {name: 'moment.mp4', media_url: '/local-moment-media/video-token'}, type: 'video'}}];
const momentVideoTile = context.__renderMediaTile(context.__mediaEntries()[0], 0);
assert.doesNotMatch(momentVideoTile, /<img[^>]+video-token/,
  'playable Moments video URLs are never rendered as image thumbnails');
assert.match(momentVideoTile, /data-lucide="video"/,
  'Moments videos without a poster render the video placeholder icon');

context.__state.items = [{platform: 'qq', account_id: 'qq-account', id: 'opaque-resource-id',
  conversation_id: 'qq-cache-gallery', conversation_title: 'QQ 缓存资源库', type: 'image',
  file_name: 'cache photo.png', extra: {resource_kind: 'qq_cache', resource_id: 'opaque-resource-id', size: 32768}}];
const galleryEntries = context.__mediaEntries();
assert.equal(galleryEntries.length, 1);
assert.equal(galleryEntries[0].resourceKind, 'qq_cache');
assert.equal(context.__mediaEntryAvailable(galleryEntries[0]), true);
assert.match(context.__mediaEntryKey(galleryEntries[0]), /qq_cache/);
assert.deepEqual(JSON.parse(JSON.stringify(context.__resourceExportItems(galleryEntries))), [{
  resource_kind: 'qq_cache', platform: 'qq', account_id: 'qq-account', resource_id: 'opaque-resource-id',
}]);
context.__state.view = 'media';
context.__state.loading = false;
context.__state.filters.source = 'qq-cache';
const qqGallery = context.__renderListing();
assert.match(qqGallery, /cache photo\.png/);
assert.match(qqGallery, /<small>32 KB<\/small>/, 'QQ gallery tile shows the API-provided size');
context.__previewMedia('/local-media/qq-audio', 'audio', 'voice.silk');
assert.doesNotMatch(context.overlay.innerHTML, /<audio/,
  'raw QQ SILK is not presented as browser-playable audio');
assert.match(context.overlay.innerHTML, /href="\/local-media\/qq-audio" download/,
  'raw QQ SILK offers a local download');
context.__state.items[0].extra.size = 0;
assert.doesNotMatch(context.__renderListing(), /<small>0 KB<\/small>/, 'unknown size stays hidden');
