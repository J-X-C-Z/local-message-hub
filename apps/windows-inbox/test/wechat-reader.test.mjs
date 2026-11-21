import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,rmSync} from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {EventStore} from '../backend/wecom-mcp/src/store.js';

test('WeChat and WeCom identities remain distinct; refresh preserves ID and arrival time',()=>{
 const directory=mkdtempSync(path.join(os.tmpdir(),'inbox-wechat-test-'));
 const store=new EventStore(path.join(directory,'test.db'));
 try{
  const payload={Source:'local-database',Platform:'wechat',ConversationId:'wechat:account:chat',FromUserName:'wechat:user',MsgType:'text',Content:'first',CreateTime:Date.now()/1000};
  assert.equal(store.upsertLocal('wechat:one',payload),true);
  const first=store.db.prepare('SELECT id,received_at FROM events').get();
  assert.equal(store.upsertLocal('wechat:one',payload),false);
  assert.equal(store.upsertLocal('wechat:one',{...payload,Content:'updated'}),true);
  assert.deepEqual(store.db.prepare('SELECT id,received_at FROM events').get(),first);
  store.upsertLocal('local:one',{...payload,Platform:'wecom',ConversationId:'chat'});
  assert.equal(store.db.prepare('SELECT count(*) AS n FROM events').get().n,2);
 }finally{store.close();rmSync(directory,{recursive:true,force:true});}
});

test('微信文字扫描不重复写入待下载状态和按需附件，真实变化仍可推送',()=>{
 const store=new EventStore(':memory:');
 try{
  const text={Source:'local-database',Platform:'wechat',Content:'sample',CreateTime:Date.now()/1000};
  const media={Sticker:{status:'pending'},Attachment:{status:'on_demand',name:'sample.pdf'}};
  assert.equal(store.upsertLocal('s',{...text,...media}),true);
  assert.equal(store.upsertLocal('s',text),false);
  assert.equal(store.upsertLocal('s',{...text,...media}),false);
  assert.equal(store.db.prepare('select count(*) n from event_updates').get().n,0);
  assert.equal(store.upsertLocal('s',{...text,Sticker:{status:'ready'}}),true);
  assert.equal(store.upsertLocal('s',text),false);
  assert.equal(store.list().events[0].message.Attachment.status,'on_demand');
  assert.equal(store.db.prepare('select count(*) n from event_updates').get().n,1);
 }finally{store.close();}
});
