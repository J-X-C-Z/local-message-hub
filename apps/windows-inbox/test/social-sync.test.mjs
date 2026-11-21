import assert from 'node:assert/strict';
import { once } from 'node:events';
import { mkdtempSync, unlinkSync, rmdirSync, readdirSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { createReaderRefresh } from '../backend/reader-refresh.mjs';
import { EventStore } from '../backend/wecom-mcp/src/store.js';
import { createWorkbenchServer } from '../backend/wecom-workbench/server.mjs';

test('漏掉文件通知时仍会补查，读取繁忙时合并重复请求', async () => {
  const commands = [];
  const scheduler = createReaderRefresh(command => { commands.push(command); return true; }, { debounceMs: 5, fallbackMs: 20 });
  const delay = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));
  try {
    await delay(45);
    assert.deepEqual(commands, []);
    scheduler.done();
    await delay(35);
    assert.deepEqual(commands, ['refresh']);
    scheduler.request('attachments');
    scheduler.request();
    await delay(30);
    assert.equal(commands.length, 1);
    scheduler.done();
    await delay(30);
    assert.deepEqual(commands, ['refresh', 'attachments']);
  } finally { scheduler.stop(); }
});

test('朋友圈接口只接受桌面鉴权，每页返回有限条目', async t => {
  const directory = mkdtempSync(path.join(os.tmpdir(), 'inbox-social-'));
  const dbPath = path.join(directory, 'messages.db');
  const store = new EventStore(dbPath);
  const posts = Array.from({ length: 13 }, (_, index) => ({id: String(index), text: '示例'}));
  store.setReceiverState('wechat_social', JSON.stringify({ avatars: [['wechat:fixture:contact', 'a'.repeat(64)]], posts }));
  const server = createWorkbenchServer({ dbPath, accessToken: 'fixture-secret' });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  t.after(async () => {
    await new Promise(resolve => { server.close(resolve); server.closeAllConnections(); });
    store.close();
    for (const name of readdirSync(directory)) unlinkSync(path.join(directory, name));
    rmdirSync(directory);
  });
  const origin = `http://127.0.0.1:${server.address().port}`;
  const headers = {'X-Workbench-Key': 'fixture-secret'};
  assert.equal((await fetch(origin + '/api/wechat/moments')).status, 403);
  const response = await fetch(origin + '/api/wechat/moments', {headers});
  const first = await response.json();
  assert.equal(response.headers.get('cache-control'), 'no-store');
  assert.equal(first.ready, true);
  assert.equal(first.posts.length, 12);
  assert.equal(first.next_offset, 12);
  const last = await (await fetch(origin + '/api/wechat/moments?offset=12', {headers})).json();
  assert.deepEqual(last.posts, [posts[12]]);
  assert.equal(last.next_offset, null);
  assert.equal((await fetch(origin + '/api/wechat/moments?offset=-1', {headers})).status, 400);
});
