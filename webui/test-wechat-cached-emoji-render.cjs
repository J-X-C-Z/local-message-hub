const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, 'app.js'), 'utf8');
const context = {
  URL,
  localStorage: {getItem: () => 'light'},
  document: {addEventListener: () => {}},
  window: {addEventListener: () => {}},
  matchMedia: () => ({addEventListener: () => {}}),
};
vm.runInNewContext(source.replace('\ninit();\n',
  '\nglobalThis.__renderMessage = renderMessage;\n'), context);

const message = {id: 'fixture-1', platform: 'wechat', raw_type: 47, type: 'sticker',
  timestamp: 1, sender_name: 'Fixture', text: '[表情]'};
const unresolved = context.__renderMessage(message);
assert.match(unresolved, /读取表情缓存/);
assert.doesNotMatch(unresolved, /<img/);

message.media_url = '/api/media/file?token=opaque';
const resolved = context.__renderMessage(message);
assert.match(resolved, /<img class="media-img" src="\/api\/media\/file\?token=opaque"/);
assert.match(resolved, /data-media-type="image"/);
assert.doesNotMatch(resolved, /读取表情缓存/);

const animated = context.__renderMessage({...message, id: 'fixture-animated', raw_type: 11000,
  media_url: undefined});
assert.match(animated, /读取表情缓存/);
assert.doesNotMatch(animated, /<img/);

message.media_url = 'https://example.invalid/sticker.gif';
assert.doesNotMatch(context.__renderMessage(message), /<img/);

const wxgf = {id: 'fixture-wxgf', platform: 'wechat', type: 'image', timestamp: 1,
  sender_name: 'Fixture', media_url: '/local-media/opaque',
  media_mime_type: 'application/x-wechat-wxgf'};
const wxgfHtml = context.__renderMessage(wxgf);
assert.match(wxgfHtml, /href="\/local-media\/opaque" download/);
assert.match(wxgfHtml, /WXGF 原始文件/);
assert.doesNotMatch(wxgfHtml, /<img/);
console.log('PASS cached WeChat emoji stays inline after local resolution');
