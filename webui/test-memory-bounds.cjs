const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, 'app.js'), 'utf8');
const context = {
  URL, URLSearchParams,
  localStorage: {getItem: () => 'light'},
  document: {addEventListener() {}, querySelector: () => null},
  window: {addEventListener() {}, lucide: null},
  matchMedia: () => ({addEventListener() {}}),
  fetch: async url => {
    const offset = Number(new URL(url, 'http://localhost').searchParams.get('offset') || 0);
    return {ok: true, json: async () => ({items: Array.from({length: 100}, (_, index) => ({
      id: String(offset + index), platform: 'qq', account_id: 'fixture', conversation_id: 'room',
      type: 'text', text: 'synthetic message '.repeat(30), timestamp: 1700000000 - offset - index
    })), total: 10000, has_more: offset + 100 < 10000})};
  }
};
vm.runInNewContext(source.replace('\ninit();\n', '\nglobalThis.__test = {state, loadMessages, releaseInactiveViewData, cacheVoiceTranscript, renderMessage};\n'), context);
const {state, loadMessages, releaseInactiveViewData, cacheVoiceTranscript, renderMessage} = context.__test;

(async () => {
  state.selected = {platform: 'qq', account_id: 'fixture', id: 'room'};
  let baselineRows = [];
  for (let page = 0; page < 100; page++) {
    await loadMessages(page > 0);
    baselineRows.push(...state.messages.slice(-100));
    assert.equal(state.messages.length, Math.min(500, (page + 1) * 100));
    assert.equal(state.messageOffset, (page + 1) * 100);
  }
  assert.equal(state.messages[0].id, '9500');
  assert.equal(state.messages.at(-1).id, '9999');
  assert.equal(state.messageHasMore, false);
  const baselineBytes = Buffer.byteLength(JSON.stringify(baselineRows));
  const windowBytes = Buffer.byteLength(JSON.stringify(state.messages));
  const baselineMarkupBytes = Buffer.byteLength(baselineRows.map(renderMessage).join(''));
  const windowMarkupBytes = Buffer.byteLength(state.messages.map(renderMessage).join(''));
  assert.ok(windowBytes / baselineBytes < 0.051);
  await loadMessages(false);
  assert.equal(state.messages[0].id, '0');
  assert.equal(state.messageOffset, 100);

  // A cursor must remain authoritative after the visible window has been trimmed.
  let requested;
  context.fetch = async url => {
    requested = new URL(url, 'http://localhost').searchParams;
    return {ok: true, json: async () => ({items: [], total: 10000, has_more: false})};
  };
  state.messageCursor = 'opaque-history-cursor';
  await loadMessages(true);
  assert.equal(requested.get('cursor'), 'opaque-history-cursor');
  assert.equal(requested.has('offset'), false);

  // Superseded requests cannot append the same page twice or overwrite a newer page.
  const completions = [];
  context.fetch = () => new Promise(resolve => completions.push(resolve));
  const oldRequest = loadMessages(false), newRequest = loadMessages(false);
  completions[1]({ok: true, json: async () => ({items: [{id: 'new'}], total: 1})});
  await newRequest;
  completions[0]({ok: true, json: async () => ({items: [{id: 'stale'}], total: 1})});
  await oldRequest;
  assert.equal(state.messages[0].id, 'new');

  for (let index = 0; index < 1000; index++) cacheVoiceTranscript(String(index), {transcript: 'fixture'});
  assert.equal(state.voiceTranscripts.size, 100);
  assert.equal(state.voiceTranscripts.has('0'), false);
  state.view = 'settings';
  state.conversations = [{id: 'room'}];state.items = [{id: 'file'}];
  state.moments = {items: [{id: 'moment'}]};state.privateProfiles = [{id: 'person'}];
  releaseInactiveViewData();
  assert.equal(state.messages.length, 0);
  assert.equal(state.conversations.length, 0);
  assert.equal(state.items.length, 0);
  assert.equal(state.moments.items.length, 0);
  assert.equal(state.privateProfiles.length, 0);
  assert.equal(state.voiceTranscripts.size, 0);
  console.log(JSON.stringify({pages: 100, totalBrowsed: 10000, retainedMessages: 500,
    baselineSerializedBytes: baselineBytes, optimizedSerializedBytes: windowBytes,
    baselineMarkupBytes, optimizedMarkupBytes: windowMarkupBytes,
    note: 'Synthetic retained payload/markup sizes, not browser RSS.'}));
})().catch(error => {console.error(error);process.exitCode = 1;});
