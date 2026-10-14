const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, 'app.js'), 'utf8');
const initCall = '\ninit();\n';
assert.ok(source.includes(initCall));

let request;
const content = {setAttribute() {}, querySelector() { return null; }, querySelectorAll() { return []; }};
const context = {
  localStorage: {getItem: () => 'light'},
  document: {addEventListener() {}, querySelector: () => content},
  window: {addEventListener() {}, lucide: null},
  matchMedia: () => ({addEventListener() {}}),
  fetch: async (url, options) => {
    request = {url, options};
    return {ok: true, json: async () => ({available: true, transcript: '本机测试转写'})};
  },
};
vm.runInNewContext(source.replace(initCall,
  '\nglobalThis.__renderMessage = renderMessage; globalThis.__transcribeVoice = transcribeVoice; globalThis.__state = state;\n'), context);

context.__state.view = 'search';
context.__state.loading = true;
context.__state.settings = {capabilities: {voice_transcription: {enabled: true}}};
const message = {id: 'voice:1', platform: 'qq', account_id: 'fixture', conversation_id: 'room',
  type: 'voice', text: '', timestamp: 1, media_url: '/local-media/signed-token'};
const initial = context.__renderMessage(message);
assert.match(initial, /<audio src="\/local-media\/signed-token"/);
assert.match(initial, /data-action="transcribe-voice:voice%3A1"/);

(async () => {
  await context.__transcribeVoice(message);
  assert.equal(request.url, '/api/voice/transcribe');
  const submitted = JSON.parse(request.options.body);
  assert.deepEqual(Object.keys(submitted).sort(), ['account_id', 'conversation_id', 'message_id', 'platform']);
  assert.equal(submitted.message_id, 'voice:1');
  const after = context.__renderMessage(message);
  assert.match(after, /本机测试转写/);
  assert.match(after, /<p class="voice-transcript-text">本机测试转写<\/p>/);
  assert.doesNotMatch(after.slice(after.indexOf('voice-transcript-text')), /local-media\/signed-token/);
})().catch(error => { console.error(error); process.exitCode = 1; });
