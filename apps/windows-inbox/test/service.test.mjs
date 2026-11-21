import test from 'node:test';
import assert from 'node:assert/strict';
import {fork} from 'node:child_process';
import {mkdtempSync,rmSync,mkdirSync,writeFileSync} from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {randomBytes,createCipheriv} from 'node:crypto';
import {createHash} from 'node:crypto';
import net from 'node:net';
import {EventStore} from '../backend/wecom-mcp/src/store.js';

test('读取器首次同步尚未结束时，工作台仍立即提供上次已提交的缓存', {timeout:10000},async t=>{
 const directory=mkdtempSync(path.join(os.tmpdir(),'inbox-cached-start-'));
 const selected=path.join(directory,'source');mkdirSync(selected);
 const privateDirectory=path.join(directory,'local-reading');mkdirSync(privateDirectory);
 writeFileSync(path.join(privateDirectory,'profile-path.txt'),selected);
 writeFileSync(path.join(privateDirectory,'database-key.dpapi'),'synthetic-key');
 const store=new EventStore(path.join(directory,'messages.db'));
 store.add('cached',{CreateTime:Date.now()/1000,FromUserName:'fixture',MsgType:'text',Content:'cached message'});store.close();
 const accessToken=randomBytes(32).toString('hex');
 const child=fork('build/service.cjs',[],{stdio:['ignore','ignore','ignore','ipc']});
 t.after(async()=>{
  if(child.exitCode===null){child.kill();await new Promise(resolve=>child.once('exit',resolve));}
  const resolved=path.resolve(directory),temporary=path.resolve(os.tmpdir())+path.sep;
  assert(resolved.startsWith(temporary)&&path.basename(resolved).startsWith('inbox-cached-start-'));
  rmSync(resolved,{recursive:true,force:true});
 });
 const ready=new Promise((resolve,reject)=>{child.on('message',m=>{if(m.type==='ready')resolve(m);if(m.type==='fatal')reject(new Error(m.error));});child.once('exit',()=>reject(new Error('服务提前退出')));});
 const start=performance.now();
 // Node with an open stdin deliberately never emits a reader snapshot.
 child.send({type:'init',settings:null,dataDirectory:directory,accessToken,localReaderExecutable:process.execPath});
 const result=await ready;
 assert(performance.now()-start<3500,'读取器预热不应阻塞工作台启动');
 assert.equal(result.localMode,true);
 const response=await fetch(result.url+'/api/messages',{headers:{'X-Workbench-Key':accessToken}});
 assert.equal((await response.json()).events[0].message.Content,'cached message');
 child.send({type:'shutdown'});await new Promise(resolve=>child.once('exit',resolve));
});

test('打包服务隔离启动、应用令牌访问、无 Secret 加密回调接收及退出', {timeout:15000},async t=>{
 const directory=mkdtempSync(path.join(os.tmpdir(),'desktop-inbox-'));
 const free=net.createServer();await new Promise(resolve=>free.listen(0,'127.0.0.1',resolve));const port=free.address().port;await new Promise(resolve=>free.close(resolve));
 const key=randomBytes(32);const accessToken=randomBytes(32).toString('hex');
 const child=fork('build/service.cjs',[],{stdio:['ignore','ignore','pipe','ipc']});
 let errors='';child.stderr.on('data',data=>{errors+=data;});
 t.after(async()=>{if(child.exitCode===null){child.kill();await new Promise(resolve=>child.once('exit',resolve));}rmSync(directory,{recursive:true,force:true});});
 const ready=new Promise((resolve,reject)=>{child.on('message',message=>{if(message.type==='ready')resolve(message);if(message.type==='fatal')reject(new Error(message.error));});child.once('exit',()=>reject(new Error('服务提前退出 '+errors)));});
 child.send({type:'init',dataDirectory:directory,accessToken,settings:{corpId:'wwtest',agentId:'42',callbackToken:'token',callbackAesKey:key.toString('base64').slice(0,-1),callbackPort:port,retentionDays:30}});
 const result=await ready;assert.equal(result.receiverError,'');
 assert.equal((await fetch(result.url+'/api/status')).status,403);
 const headers={'X-Workbench-Key':accessToken};const status=await(await fetch(result.url+'/api/status',{headers})).json();assert.equal(status.mode,'receive-only');assert.equal(status.live_available,true);
 const timestamp=String(Math.floor(Date.now()/1000));const message=Buffer.from(`<xml><ToUserName>wwtest</ToUserName><FromUserName>testsender</FromUserName><CreateTime>${timestamp}</CreateTime><MsgType>text</MsgType><Content>桌面接收测试</Content><MsgId>1</MsgId><AgentID>42</AgentID></xml>`);
 const length=Buffer.alloc(4);length.writeUInt32BE(message.length);const plain=Buffer.concat([randomBytes(16),length,message,Buffer.from('wwtest')]);const pad=32-plain.length%32;
 const cipher=createCipheriv('aes-256-cbc',key,key.subarray(0,16));cipher.setAutoPadding(false);const encrypted=Buffer.concat([cipher.update(Buffer.concat([plain,Buffer.alloc(pad,pad)])),cipher.final()]).toString('base64');
 const signature=createHash('sha1').update(['token',timestamp,'123',encrypted].sort().join('')).digest('hex');
 const response=await fetch(`http://127.0.0.1:${port}/wecom/callback?${new URLSearchParams({timestamp,nonce:'123',msg_signature:signature})}`,{method:'POST',body:`<xml><Encrypt>${encrypted}</Encrypt></xml>`});assert.equal(response.status,200);
 const data=await(await fetch(result.url+'/api/messages',{headers})).json();assert.equal(data.events[0].message.Content,'桌面接收测试');
 const html=await(await fetch(result.url,{headers})).text();assert.doesNotMatch(html,/id="send-button"|id="message-input"/);
 assert.equal((await fetch(result.url+'/api/send',{headers,method:'POST'})).status,405);
 child.send({type:'shutdown'});await new Promise(resolve=>child.once('exit',resolve));assert.equal(child.exitCode,0);
});
