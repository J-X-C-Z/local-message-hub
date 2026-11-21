import test from 'node:test';
import assert from 'node:assert/strict';
import {LiveConversationIndex,messagePage,conversationSearchText,quoteOriginal,conversationSummary,conversationExport,dayKey} from '../backend/wecom-workbench/dist/view-performance.mjs';
import {EventStore} from '../backend/wecom-mcp/src/store.js';

test('增量索引保留未变会话，迟到消息按时间插入且淘汰记录会同步删除',()=>{
 let normalized=0;const index=new LiveConversationIndex({normalize:e=>{normalized++;return {id:String(e.id),time:e.message.time,text:e.message.text};},key:m=>m.conversation,create:(_e,_m,id)=>({id,name:id})});
 const a={id:1,message:{conversation:'a',time:10,text:'first'}},b={id:2,message:{conversation:'b',time:20,text:'second'}};
 const first=index.sync([a,b]),retained=first[0],messages=retained.messages,bRevision=first[1].revision;
 const late={id:3,message:{conversation:'a',time:5,text:'late'}};
 const second=index.sync([a,b,late]);assert.equal(normalized,3);assert.equal(second[0],retained);assert.equal(second[0].messages,messages);assert.deepEqual(messages.map(m=>m.time),[5,10]);
 assert.equal(second[1].revision,bRevision);index.sync([b,late]);assert.deepEqual(messages.map(m=>m.time),[5]);
 assert.equal(index.sync([b]).length,1);const replacement={...b,message:{...b.message,text:'updated'}};const updated=index.sync([replacement]);assert.equal(updated[0].messages[0].text,'updated');assert(updated[0].revision>bRevision);
});
test('一万条消息每页最多100条，全部历史可遍历，搜索缓存随增量失效',()=>{
 const messages=Array.from({length:10000},(_,id)=>({id,text:'内容'+id}));
 assert.equal(messagePage(messages).first,9900);assert.equal(messagePage(messages).messages.length,100);
 const ids=new Set();for(let start=0;start<10000;start+=100)for(const m of messagePage(messages,start).messages)ids.add(m.id);assert.equal(ids.size,10000);
 assert.equal(messagePage(messages,-100).first,0);assert.equal(messagePage(messages,999999).first,9900);
 const c={name:'中文会话',messages:[{text:'旧内容'}],revision:1};let builds=0;const preview=m=>{builds++;return m.text;};
 assert(conversationSearchText(c,preview).includes('旧内容'));conversationSearchText(c,preview);assert.equal(builds,1);
 c.messages.push({text:'新关键词'});c.revision++;assert(conversationSearchText(c,preview).includes('新关键词'));assert.equal(builds,3);
});
test('同一秒收到的消息按数字顺序排列，修订不改变顺序',()=>{
 const index=new LiveConversationIndex({normalize:event=>({id:`live-${event.id}`,sequence:event.id,time:1,text:event.message.text}),key:()=> 'room',create:()=>({id:'room'})});
 const first={id:999,message:{text:'first'}},next={id:1000,message:{text:'next'}};
 assert.deepEqual(index.sync([next,first])[0].messages.map(message=>message.sequence),[999,1000]);
 assert.deepEqual(index.sync([next,{...first,message:{text:'edited'}}])[0].messages.map(message=>message.sequence),[999,1000]);
});
test('引用只匹配当前会话，迟到原文和媒体修订可解析，歧义或自引用不跳转',()=>{
 const index=new LiveConversationIndex({normalize:event=>({id:`live-${event.id}`,time:1,...event.message}),key:m=>m.room,create:()=>({})});
 const reply={id:2,message:{room:'a',referenceKey:'reply'}},other={id:3,message:{room:'b',referenceKey:'target'}};
 const [room]=index.sync([reply,other]);
 assert.equal(quoteOriginal(room,'target','live-2'),null);
 assert.equal(quoteOriginal(room,'reply','live-2'),null);
 const original={id:1,message:{room:'a',referenceKey:'target'}};
 index.sync([reply,other,original]);assert.equal(quoteOriginal(room,'target','live-2').id,'live-1');
 index.sync([reply,other,{...original,message:{...original.message,image:'ready'}}]);
 assert.equal(quoteOriginal(room,'target','live-2').image,'ready');
 index.sync([reply,other,original,{...original,id:4}]);assert.equal(quoteOriginal(room,'target','live-2'),null);
});
test('后台历史与实时修订交错时保留最新内容并去重',()=>{
 const index=new LiveConversationIndex({normalize:e=>({id:String(e.id),time:e.received_at,text:e.message.text}),key:()=> 'room',create:()=>({})});
 const revision={id:2,received_at:2,message:{text:'修订版本'}};
 index.ingest([revision]);
 assert.deepEqual(index.ingest([{...revision,message:{text:'较老的版本'}},{id:1,received_at:1,message:{text:'历史'}}],{replace:false}).map(e=>e.id),[1]);
 assert.deepEqual(index.groups.get('room').messages.map(m=>m.text),['历史','修订版本']);
 assert.equal(index.expire(2),true);assert.deepEqual(index.groups.get('room').messages.map(m=>m.id),['2']);
});
test('消息统计、最新预览及引用随修订和过期更新',()=>{
 const t=new Date(2026,10,20,12).getTime();
 const index=new LiveConversationIndex({normalize:e=>({id:String(e.id),sequence:e.id,...e.message}),key:()=> 'room',create:()=>({})});
 const old={id:1,received_at:1,message:{time:t-86400000,text:'旧消息'}},newest={id:2,received_at:2,message:{time:t,text:'新消息',referenceKey:'r'}},system={id:3,received_at:3,message:{time:t+1,system:true}};
 const [room]=index.sync([newest,system,old]);const stats=conversationSummary(room);
 assert.equal(stats.count,2);assert.equal(stats.days.get(dayKey(t)),1);assert.equal(stats.last.id,'2');
 index.ingest([{...newest,message:{...newest.message,text:'更新后'}}]);
 assert.equal(quoteOriginal(room,'r','1').text,'更新后');assert.equal(stats.last.text,'更新后');assert.equal(stats.count,2);
 index.expire(2);assert.equal(stats.count,1);assert.equal(stats.days.has(dayKey(t-86400000)),false);
 index.ingest([{...newest,message:{...newest.message,system:true}}]);assert.equal(stats.count,0);assert.equal(stats.last,null);
});

test('异步导出在分批让出执行时保持同一个会话快照',async()=>{
 const messages=Array.from({length:406},(_,i)=>({id:i,text:`内容 ${i}`}));
 const room={id:'test',name:'测试',messages};let yields=0;
 const blob=await conversationExport(room,'live',async()=>{if(++yields===1){messages[0]={id:0,text:'实时修订'};messages.push({id:406});}});
 const data=JSON.parse(await blob.text());
 assert.ok(yields>1);assert.equal(data.source,'live');assert.equal(data.conversation.messages.length,406);assert.equal(data.conversation.messages[0].text,'内容 0');
});
test('批量导入只复用语句，过期记录仍拒绝入库且显式清理立即生效',()=>{
 const store=new EventStore(':memory:',1);let cleanups=0;const original=store.deleteExpired.run.bind(store.deleteExpired);store.deleteExpired.run=(...args)=>{cleanups++;return original(...args);};
 try{
  const timestamp=Date.now()/1000;for(let i=0;i<1000;i++)store.add('key'+i,{CreateTime:timestamp,Content:'fixture'});
  assert.equal(cleanups,0);assert.equal(store.add('too-old',{CreateTime:timestamp-172800}),false);
  store.db.prepare('UPDATE events SET received_at=? WHERE id=1').run(Date.now()-172800000);store.prune();
  assert.equal(cleanups,1);assert.equal(store.db.prepare('SELECT COUNT(*) n FROM events').get().n,999);
 }finally{store.close();}
});
