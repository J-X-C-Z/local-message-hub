import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { EventStore } from '../backend/wecom-mcp/src/store.js';
import { createWorkbenchServer } from '../backend/wecom-workbench/server.mjs';

async function connect(url, lastId) {
  const response = await fetch(url, { headers: lastId ? { 'Last-Event-ID': lastId } : {}, signal: AbortSignal.timeout(4000) });
  assert.equal(response.status, 200);
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = '';
  return {
    close: () => reader.cancel(),
    async next() {
      for (;;) {
        const end = buffer.indexOf('\n\n');
        if (end < 0) {
          const result = await reader.read();
          assert.equal(result.done, false, 'stream closed before expected event');
          buffer += result.value;
          continue;
        }
        const fields = buffer.slice(0, end).split('\n');
        buffer = buffer.slice(end + 2);
        const value = name => fields.find(line => line.startsWith(`${name}: `))?.slice(name.length + 2);
        if (value('id')) return { id: value('id'), type: value('event'), data: JSON.parse(value('data')) };
      }
    },
  };
}

test('历史加载与断线之间的媒体修订可补齐，已收到的修订不重放', { timeout: 10000 }, async t => {
  const directory = mkdtempSync(path.join(os.tmpdir(), 'inbox-stream-'));
  const dbPath = path.join(directory, 'messages.db');
  const store = new EventStore(dbPath);
  const server = createWorkbenchServer({ dbPath, port: 0 });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(async () => {
    server.closeStreams();
    await new Promise(resolve => { server.close(resolve); server.closeAllConnections(); });
    store.close();
    assert.equal(path.dirname(directory), path.resolve(os.tmpdir()));
    assert.match(path.basename(directory), /^inbox-stream-/);
    rmSync(directory, { recursive: true, force: true });
  });
  const origin = `http://127.0.0.1:${server.address().port}`;
  const message = { Source: 'local-database', MsgType: 'text', CreateTime: Date.now() / 1000, Content: 'fixture' };
  const updateFile = status => store.upsertLocal('file', { ...message, Attachment: { name: 'sample.pdf', status } });
  for (let revision = 0; revision < 225; revision++) updateFile(`old-${revision}`);
  const snapshot = await (await fetch(`${origin}/api/messages?include_update_watermark=1`)).json();
  assert(snapshot.update_watermark > 200);
  const file = snapshot.events[0];
  updateFile('pending');
  updateFile('ready');
  const url = `${origin}/api/stream?after_id=${snapshot.next_after_id}&after_update_id=${snapshot.update_watermark}`;
  const first = await connect(url);
  const latest = await first.next();
  assert.equal(latest.type, 'message-updates');
  assert.equal(latest.data.events.length, 1);
  assert.equal(latest.data.events[0].id, file.id);
  assert.equal(latest.data.events[0].received_at, file.received_at);
  assert.equal(latest.data.events[0].message.Attachment.status, 'ready');
  assert.equal(latest.id, `${file.id}:${snapshot.update_watermark + 2}`);
  await first.close();

  updateFile('updated-while-offline');
  store.upsertLocal('new', message);
  store.upsertLocal('new', { ...message, Content: 'latest-new-content' });
  const second = await connect(url, latest.id);
  const arrival = await second.next();
  assert.equal(arrival.type, 'messages');
  assert.equal(arrival.data.events.length, 1);
  assert.equal(arrival.data.events[0].message.Content, 'latest-new-content');
  const revisions = await second.next();
  assert.equal(revisions.type, 'message-updates');
  assert.equal(revisions.data.events.find(event => event.id === file.id).message.Attachment.status, 'updated-while-offline');
  await second.close();

  const resumed = await connect(url, arrival.id);
  assert.deepEqual(await resumed.next(), revisions);
  await resumed.close();

  store.db.prepare('UPDATE events SET received_at=? WHERE id=?').run(Date.now() - 31 * 86400000, file.id);
  updateFile('expired');
  const afterExpiry = await connect(url, revisions.id);
  const checkpoint = await afterExpiry.next();
  assert.equal(checkpoint.type, 'message-updates');
  assert.deepEqual(checkpoint.data.events, []);
  assert.notEqual(checkpoint.id, revisions.id);
  await afterExpiry.close();
});

test('启动预览覆盖各分区，后台补齐有固定边界，实时通道不重放历史', {timeout:10000}, async t => {
  const directory=mkdtempSync(path.join(os.tmpdir(),'inbox-fast-start-'));
  const dbPath=path.join(directory,'messages.db'),store=new EventStore(dbPath);
  const server=createWorkbenchServer({dbPath,port:0});
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  t.after(async()=>{
    server.closeStreams();await new Promise(resolve=>{server.close(resolve);server.closeAllConnections();});store.close();
    assert.equal(path.dirname(directory),path.resolve(os.tmpdir()));assert.match(path.basename(directory),/^inbox-fast-start-/);
    rmSync(directory,{recursive:true,force:true});
  });
  const origin=`http://127.0.0.1:${server.address().port}`,read=async route=>{
    const response=await fetch(origin+route);assert.equal(response.status,200);return response.json();
  };
  const now=Math.floor(Date.now()/1000);
  const add=(room,time,key,extra={})=>store.upsertLocal(key,{Source:'local-database',MsgType:'text',ConversationId:room,CreateTime:now+time,Content:key,...extra});
  for(let i=0;i<125;i++)add('wechat-chat',i,'wx'+i,{Platform:'wechat'});
  add('wechat-chat',-50,'late',{Platform:'wechat'});
  add('wechat-room2',0,'another-wechat-chat',{Platform:'wechat'});
  add('official',0,'official',{Platform:'wechat',ConversationType:'official'});
  add('wecom',0,'wecom');
  const preview=await read('/api/inbox-preview');
  assert.equal(preview.events.length,103);
  assert.equal(preview.events.filter(e=>e.message.ConversationId==='wechat-chat').at(-1).message.Content,'wx124');
  assert.equal(preview.events.some(e=>e.message.Content==='late'),false);
  assert.equal(preview.events.some(e=>e.message.Content==='official'),true);
  add('wecom',1,'new');
  add('wecom',0,'wecom',{Content:'updated'});
  const stream=await connect(`${origin}/api/stream?after_id=${preview.history_through_id}&after_update_id=${preview.update_watermark}`);
  const arrival=await stream.next(),revision=await stream.next();await stream.close();
  assert.equal(arrival.type,'messages');assert.deepEqual(arrival.data.events.map(e=>e.message.Content),['new']);
  assert.equal(revision.type,'message-updates');assert.equal(revision.data.events[0].message.Content,'updated');
  const full=await read(`/api/messages?through_id=${preview.history_through_id}&limit=600`);
  assert.equal(full.events.length,129);assert.equal(full.has_more,false);
  assert.equal(full.events.some(e=>e.message.Content==='late'),true);
  assert.equal(full.events.some(e=>e.message.Content==='new'),false);
});
