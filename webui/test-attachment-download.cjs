const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, 'app.js'), 'utf8');
const initCall = '\ninit();\n';
assert.ok(source.includes(initCall));

async function main() {
  const handlers = {};
  const requests = [];
  const ui = {toasts: [], previews: []};
  const context = {
    URL,
    URLSearchParams,
    Intl,
    localStorage: {getItem: () => 'light'},
    location: {origin: 'http://localhost'},
    document: {
      addEventListener(type, listener) {(handlers[type] ||= []).push(listener);},
      querySelector: () => null,
      querySelectorAll: () => []
    },
    window: {addEventListener() {}},
    matchMedia: () => ({matches: false, addEventListener() {}}),
    setTimeout: () => 0,
    __ui: ui
  };
  vm.runInNewContext(source.replace(initCall,
    '\nglobalThis.__test = {state, renderMessage, renderSettings, renderMediaTile, mediaEntries, downloadAttachment};\n'), context);
  vm.runInNewContext(`
    renderView = () => {};
    toast = (message, error = false) => __ui.toasts.push({message, error});
    previewMedia = (...args) => __ui.previews.push(args);
    syncAccountLockPolling = () => {};
    syncNotificationStream = () => {};
  `, context);

  const {state, renderMessage, renderSettings, renderMediaTile, mediaEntries, downloadAttachment} = context.__test;
  state.settings = {
    capabilities: {
      media: {available: true, enabled: true},
      attachment_download: {available: true, enabled: false}
    },
    auto_download_attachments: false,
    backups: []
  };

  const email = {
    id: 'email-message', platform: 'email', account_id: 'mail-account',
    conversation_id: 'inbox', type: 'text', text: 'mail body', timestamp: 1,
    extra: {attachments: [{type: 'file', name: 'report.pdf', size: 2048, available: false}]}
  };
  const emailDisabled = renderMessage(email);
  assert.match(emailDisabled, /report\.pdf/);
  assert.match(emailDisabled, /data-action="resolve-media:email-message:0"/);
  assert.doesNotMatch(emailDisabled, /download-attachment/);

  state.settings.capabilities.attachment_download.enabled = true;
  const emailEnabled = renderMessage(email);
  assert.match(emailEnabled, /data-action="resolve-media:email-message:0"/);
  assert.match(emailEnabled, /data-action="download-attachment:email-message:0"/);
  assert.match(emailEnabled, /下载到中枢/);

  state.items = [email];
  assert.equal(mediaEntries()[0].name, 'report.pdf');
  const emailTile = renderMediaTile(mediaEntries()[0], 0);
  assert.match(emailTile, /data-action="download-media:0"/);
  assert.match(emailTile, /附件未缓存 · 可补下载/);
  const oversizedEmail = {
    ...email,
    extra: {attachments: [{type: 'file', name: 'huge.zip', size: 32 * 1024 * 1024 + 1,
      available: false, too_large: true}]}
  };
  assert.doesNotMatch(renderMessage(oversizedEmail), /download-attachment/);

  const qqResource = {
    id: 'qq-file', platform: 'qq', account_id: 'qq-account',
    conversation_id: 'qq-room', type: 'text', text: '', timestamp: 2,
    extra: {roaming_resources: [{
      type: 'file', name: 'archive.zip', size: 4096, available: false,
      md5: '0123456789abcdef0123456789abcdef'
    }]}
  };
  const qqResourceHtml = renderMessage(qqResource);
  assert.match(qqResourceHtml, /data-action="resolve-media:qq-file:0"/);
  assert.match(qqResourceHtml, /data-action="download-attachment:qq-file:0"/);
  const qqNoDigest = {
    ...qqResource,
    extra: {roaming_resources: [{type: 'file', name: 'unknown.bin', size: 1, available: false}]}
  };
  assert.doesNotMatch(renderMessage(qqNoDigest), /download-attachment/);

  const qqSingleFile = {
    id: 'qq-single', platform: 'qq', account_id: 'qq-account',
    conversation_id: 'qq-room', type: 'file', file_name: 'single.bin', timestamp: 3,
    extra: {media: [{
      type: 'file', name: 'single.bin', size: 1024,
      md5: 'fedcba9876543210fedcba9876543210'
    }]}
  };
  const qqSingleFileHtml = renderMessage(qqSingleFile);
  assert.match(qqSingleFileHtml, /data-action="resolve-media:qq-single:0"/);
  assert.match(qqSingleFileHtml, /data-action="download-attachment:qq-single:0"/);

  const wechatFile = {
    id: 'wechat-file', platform: 'wechat', account_id: 'wxid-fixture',
    conversation_id: 'fixture@chatroom', type: 'file', raw_type: '49',
    file_name: 'school.pdf', timestamp: 4,
    extra: {content: '<msg><appmsg><title>school.pdf</title><type>6</type></appmsg></msg>'}
  };
  const wechatFileHtml = renderMessage(wechatFile);
  assert.match(wechatFileHtml, /data-action="download-attachment:wechat-file"/);

  const wecom = {
    id: 'wecom-file', platform: 'wecom', account_id: 'wecom-account',
    conversation_id: 'wecom-room', type: 'text', text: '', timestamp: 4,
    extra: {attachments: [{type: 'file', name: 'old.pdf', available: false}]}
  };
  const wecomHtml = renderMessage(wecom);
  assert.match(wecomHtml, /class="roaming-resource unavailable"/);
  assert.doesNotMatch(wecomHtml, /download-attachment/);

  state.view = 'settings';
  state.platform = 'all';
  state.sources = [];
  state.backups = [];
  state.operations = [];
  state.pendingSends = [];
  state.mcp = {operations: []};
  state.settings.capabilities.attachment_download.enabled = false;
  const settingsDisabledHtml = renderSettings();
  assert.match(settingsDisabledHtml, /需先在“功能模块”启用“附件下载”/);
  assert.match(settingsDisabledHtml, /data-action="toggle-auto-download-attachments"[^>]*disabled/);
  state.settings.capabilities.attachment_download.enabled = true;
  const settingsHtml = renderSettings();
  assert.match(settingsHtml, /data-action="toggle-auto-download-attachments"/);
  assert.match(settingsHtml, /微信文件按需由已登录客户端接收/);
  state.settings.capabilities.media.enabled = false;
  assert.doesNotMatch(renderMessage(qqResource), /download-attachment/);
  state.settings.capabilities.media.enabled = true;

  context.fetch = async (url, options) => {
    requests.push({url, options});
    if (url === '/api/settings') {
      return {ok: true, json: async () => ({
        ...state.settings,
        auto_download_attachments: JSON.parse(options.body).auto_download_attachments
      })};
    }
    return {ok: true, json: async () => ({
      available: true,
      media_url: '/local-media/token?download_name=report.pdf',
      download_name: 'report.pdf',
      message: ''
    })};
  };
  const toggle = {
    dataset: {action: 'toggle-auto-download-attachments'},
    closest(selector) {return selector === '[data-media-select]' ? null : this;}
  };
  await handlers.click[0]({target: toggle});
  assert.equal(requests[0].url, '/api/settings');
  assert.deepEqual(JSON.parse(requests[0].options.body), {auto_download_attachments: true});
  assert.equal(state.settings.auto_download_attachments, true);

  await handlers.click[0]({
    target: {
      dataset: {action: 'download-media:0'},
      closest(selector) {return selector === '[data-media-select]' ? null : this;}
    }
  });
  assert.equal(requests[1].url, '/api/attachments/download');
  assert.equal(requests[1].options.method, 'POST');
  assert.deepEqual(JSON.parse(requests[1].options.body), {
    platform: 'email',
    account_id: 'mail-account',
    message_id: 'email-message',
    conversation_id: 'inbox',
    resource_index: 0
  });
  assert.equal(email.extra.attachments[0].available, true);
  assert.equal(email.extra.attachments[0].media_url, '/local-media/token?download_name=report.pdf');
  assert.deepEqual(Array.from(ui.previews.at(-1)), [
    '/local-media/token?download_name=report.pdf', 'file', 'report.pdf'
  ]);

  context.fetch = async (url, options) => {
    requests.push({url, options});
    return {
      ok: true,
      json: async () => ({available: false, media_url: null, download_name: null, message: 'QCE 不在线'})
    };
  };
  await downloadAttachment(qqSingleFile);
  assert.equal(ui.toasts.at(-1).message, 'QCE 不在线');
  assert.equal(ui.toasts.at(-1).error, true);
  assert.deepEqual(JSON.parse(requests.at(-1).options.body), {
    platform: 'qq',
    account_id: 'qq-account',
    message_id: 'qq-single',
    conversation_id: 'qq-room',
    resource_index: 0
  });

  console.log('PASS email/QQ/WeChat attachment rendering, auto-download setting, and attachment POST');
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
