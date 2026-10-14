const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, 'app.js'), 'utf8');
const listeners = {};
let activeForm = null;
let overlayHtml = '';
const overlay = {set innerHTML(value) {
  overlayHtml = value;
  if (!value.includes('send-compose-form')) activeForm = null;
}, get innerHTML() {return overlayHtml;}};
const context = {
  URL, AbortController, localStorage: {getItem: () => 'light'},
  document: {addEventListener: (name, handler) => { (listeners[name] ||= []).push(handler); },
    querySelector: selector => selector === '#overlay' ? overlay : selector === '#send-compose-form' ? activeForm : null,
    setActiveForm: form => {activeForm = form;}}, window: {addEventListener: () => {}},
  FormData: class { constructor() {return new Map([['media_type', 'image'], ['account_id', 'bot'],
    ['conversation_type', 'private'], ['conversation_id', 'target'], ['text', '']]);} },
  matchMedia: () => ({addEventListener: () => {}}),
};
vm.runInNewContext(source.replace('\ninit();\n', `
globalThis.__state = state;
let captured;
modal = (...args) => { captured = args; };
globalThis.__showComposer = () => {
  state.selected = {platform: 'wecom', account_id: 'bot', id: 'target', kind: 'private'};
  state.wecomBotIdentity = {configured: true, bot_id: 'bot', identity: '企业微信智能机器人（非当前员工身份）'};
  state.settings.capabilities = {wecom_bot_send: {available: true, enabled: true}};
  showSendComposer();
  return captured;
};
globalThis.__renderSendRequest = renderSendRequest;
globalThis.__reviewSendRequest = request => {
  state.pendingSends = [request];
  reviewSend(request.request_id);
  return captured[1];
};
globalThis.__cancelDuringUpload = async () => {
  let resolveUpload;
  let prepareCount = 0;
  let uploadSignal;
  fetch = (_url, options) => { uploadSignal = options.signal; return new Promise(resolve => {resolveUpload = resolve;}); };
  api = async path => {if (path.endsWith('/prepare')) prepareCount++; return {};};
  const form = {dataset: {sendPlatform: 'wecom_bot', sendComposeRevision: String(sendComposeRevision)},
    elements: {media_file: {files: [{name: 'photo.png'}]}}};
  document.setActiveForm(form);
  const pending = prepareSend(form);
  await Promise.resolve();
  closeModal();
  resolveUpload({ok: true, json: async () => ({attachment_id: 'staged-photo'})});
  await pending;
  return {prepareCount, aborted: uploadSignal.aborted};
};
`), context);

const [title, body] = context.__showComposer();
assert.match(title, /企微机器人发送/);
assert.match(body, /企业微信智能机器人（非当前员工身份）/);
for (const type of ['markdown', 'image', 'file', 'voice', 'video']) assert.match(body, new RegExp(`value="${type}"`));
assert.match(body, /type="file" accept="[^"]+" disabled/);

const controls = {fileLabel: {hidden: true}, file: {disabled: true, required: false, accept: ''},
  textLabel: {hidden: false, querySelector: () => controls.text},
  text: {required: false, disabled: false, value: 'unsent text'}};
const form = {elements: {media_file: controls.file}, querySelector: selector =>
  selector === '[data-media-file-label]' ? controls.fileLabel : controls.textLabel};
for (const [type, accept] of Object.entries({image: '.jpg,.jpeg,.png,.gif,.webp',
  file: '.pdf,.txt,.csv,.md,.log,.zip,.docx,.xlsx,.pptx', voice: '.amr',
  video: '.mp4,.m4v,.mov,.webm'})) {
  listeners.change[0]({target: {matches: selector => selector === '#send-compose-form [name="media_type"]',
    value: type, form}});
  assert.equal(controls.file.accept, accept);
  assert.equal(controls.file.disabled, false);
  assert.equal(controls.file.required, true);
  assert.equal(controls.fileLabel.hidden, false);
  assert.equal(controls.text.required, false);
  assert.equal(controls.text.disabled, true);
  assert.equal(controls.text.value, '');
  assert.equal(controls.textLabel.hidden, true);
}
listeners.change[0]({target: {matches: selector => selector === '#send-compose-form [name="media_type"]',
  value: 'markdown', form}});
assert.equal(controls.file.disabled, true);
assert.equal(controls.file.required, false);
assert.equal(controls.fileLabel.hidden, true);
assert.equal(controls.text.required, true);
assert.equal(controls.text.disabled, false);
assert.equal(controls.textLabel.hidden, false);

const request = context.__renderSendRequest({request_id: 'request-12345678', platform: 'wecom_bot',
  account_id: 'bot', conversation_id: 'target', conversation_type: 'private', text: '',
  attachment: {filename: 'photo.png', media_type: 'image', size: 1024}, status: 'pending_approval'}, true);
assert.match(request, /企微机器人身份/);
assert.match(request, /附件 · photo\.png/);
for (const [media_type, filename, marker] of [
  ['image', 'photo.png', /<img class="send-review-image"[^>]+attachment-preview/],
  ['video', 'movie.mp4', /<video class="send-review-video"[^>]+controls/],
  ['voice', 'voice.amr', /下载 AMR 语音后核验/],
  ['file', 'report.pdf', /下载文件后核验/],
]) {
  const html = context.__reviewSendRequest({request_id: 'request-12345678', platform: 'wecom_bot',
    account_id: 'bot', conversation_id: 'target', conversation_type: 'private', text: '',
    attachment: {filename, media_type, size: 1024}, status: 'pending_approval'}, true);
  assert.match(html, marker);
}

context.__cancelDuringUpload().then(result => {
  assert.equal(result.aborted, true);
  assert.equal(result.prepareCount, 0);
}).catch(error => {process.nextTick(() => {throw error;});});
