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
  '\nglobalThis.__renderQQ = renderQQRoamingContent; globalThis.__renderMessage = renderMessage;\n'), context);

const attachmentMarkup = context.__renderMessage({id: 'peer:message', platform: 'qq', type: 'text', text: 'attachments', extra: {media: [
  {type: 'file', name: 'first.pdf'},
  {type: 'image', name: 'quoted.png', quoted: true},
  {type: 'file', name: 'last.pdf'},
]}});
assert.deepEqual([...attachmentMarkup.matchAll(/data-action="resolve-media:peer%3Amessage:(\d+)"/g)].map(match => match[1]), ['0', '2']);
assert.doesNotMatch(attachmentMarkup, /quoted\.png/);

const markup = context.__renderQQ({id: 'peer:message', extra: {roaming_content: {elements: [
  {type: 'text', data: {text: 'hello <script>alert(1)</script>'}},
  {type: 'at', data: {name: 'Alice'}},
  {type: 'reply', data: {senderName: 'Bob', content: 'quoted', resources: [
    {resource_index: 0, type: 'image', name: 'quote <photo>.png', size: 1536},
  ]}},
  {type: 'forward', data: {title: 'forwarded', preview: ['one', 'two'],
    messages: [{sender: {name: 'Carol'}, content: {text: 'nested <b>text</b>', resources: [
      {type: 'image', name: 'photo <img>.png', size: 1536, md5: 'secret-md5',
        url: 'https://private.example/file', path: 'C:\\private\\file'},
    ]}}]}},
  {type: 'location', data: {name: 'Office', address: 'Room 1'}},
  {type: 'wallet', data: {summary: '恭喜发财'}},
  {type: 'live_gift', data: {summary: '<img src=x onerror=alert(1)> 鲜花', recipient: 'private-user', payload: {token: 'secret'}}},
  {type: 'giphy', data: {summary: 'Giphy 动图'}},
  {type: 'image', data: {}},
]}}}, true);
assert.match(markup, /hello &lt;script&gt;alert\(1\)&lt;\/script&gt;/);
assert.match(markup, /@Alice/);
assert.match(markup, /quoted/);
assert.match(markup, /data-action="resolve-media:peer%3Amessage:0"/);
assert.match(markup, /图片 · quote &lt;photo&gt;\.png · 2 KB · 读取本机缓存/);
assert.match(markup, /forwarded/);
assert.match(markup, /Carol：nested &lt;b&gt;text&lt;\/b&gt;/);
assert.match(markup, /附件 · image · photo &lt;img&gt;\.png · 1\.5 KB · 未读取/);
assert.match(markup, /Office/);
assert.match(markup, /红包 \/ 钱包消息/);
assert.match(markup, /恭喜发财/);
assert.match(markup, /<strong>群礼物<\/strong>/);
assert.match(markup, /&lt;img src=x onerror=alert\(1\)&gt; 鲜花/);
assert.match(markup, /Giphy 动图/);
assert.doesNotMatch(markup, /<script>|\[图片\]/);
assert.doesNotMatch(markup, /<img src=x|private-user|secret/);
assert.match(markup, /data-action="qq-forward-read">读取附件/);
assert.doesNotMatch(markup, /secret-md5|private\.example|C:\\private|<a /);
assert.equal(context.__renderQQ({extra: {roaming_content: {elements: [
  {type: 'market_face', data: {name: 'local-sticker.gif'}},
]}}}, true), '');
assert.match(context.__renderQQ({extra: {roaming_content: {elements: [
  {type: 'market_face', data: {name: 'local-sticker.gif'}},
]}}}, false), /local-sticker\.gif/);
console.log('PASS QQ roaming rich message rendering');
