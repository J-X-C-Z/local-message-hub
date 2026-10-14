const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync(require('node:path').join(__dirname, 'app.js'), 'utf8');
const initCall = '\ninit();\n';
assert.ok(source.includes(initCall), 'app bootstrap invocation is available to isolate for rendering tests');

const context = {
  URL,
  localStorage: {getItem: () => 'light'},
  document: {addEventListener: () => {}},
  window: {addEventListener: () => {}},
  matchMedia: () => ({addEventListener: () => {}}),
};
vm.runInNewContext(source.replace(initCall,
  '\nglobalThis.__renderMoments = renderMoments; globalThis.__momentCommentsMarkup = momentCommentsMarkup; globalThis.__state = state;\n'), context);

context.__state.view = 'moments';
context.__state.platform = 'wechat';
context.__state.momentFilter = {q: '', username: '', start: '', end: ''};
context.__state.moments = {
  total: 1,
  account_id: 'wxid_fixture',
  accounts: ['wxid_fixture', '<script>'],
  items: [{
    id: 'fixture-post', username: 'wxid_fixture', nickname: 'Fixture', createTime: 1,
    avatar_url: 'https://wx.qlogo.cn/cache/fixture',
    media: [{
      type: 'image', media_url: '/local-moment-media/image-token', local_available: true,
      livePhoto: {type: 'video', media_url: '/local-moment-media/video-token', local_available: true},
    }],
    comments: [
      {id: 'root', nickname: 'Alice <root>', content: 'root & text'},
      {id: 'reply', refCommentId: 'root', nickname: 'Bob', content: 'reply'},
      {id: 'orphan', refCommentId: 'missing', nickname: 'Mallory', content: 'orphan'},
    ],
  }, {id: 'no-nickname', username: 'wxid_fallback', createTime: 2}],
};

const html = context.__renderMoments();
assert.match(html, /<img src="\/local-moment-media\/image-token" alt="朋友圈图片"/);
assert.match(html, /<img class="avatar-image" src="\/api\/avatar\?url=https%3A%2F%2Fwx\.qlogo\.cn%2Fcache%2Ffixture" alt="" loading="lazy">/);
assert.match(html, /<span class="avatar wechat ">w<\/span><div><strong>wxid_fallback<\/strong>/);
assert.match(html, /<video controls preload="none" src="\/local-moment-media\/video-token" aria-label="朋友圈实况视频"><\/video>/);
assert.equal((html.match(/<video\b/g) || []).length, 1);
assert.match(html, /<select id="moments-account" name="account_id" aria-label="朋友圈微信账号">/);
assert.match(html, /<option value="xlsx"[^>]*>Excel \/ XLSX<\/option>/);
assert.match(html, /&lt;script&gt;/);
assert.match(html, /回复 Alice &lt;root&gt;：/);
assert.match(html, /root &amp; text/);
assert.match(html, /<strong>Mallory<\/strong> orphan/);
assert.doesNotMatch(html, /回复 missing/);
