import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import {createRequire} from 'node:module';
import http from 'node:http';
import {once} from 'node:events';
const {createWorkbenchHandler}=createRequire(import.meta.url)('../desktop/workbench-protocol.cjs');
test('桌面静态界面不等待后台服务；模块白名单和主来源校验生效',async()=>{
 let backendCalls=0;const handler=createWorkbenchHandler({directory:path.resolve('backend/wecom-workbench/dist'),backendReady:()=>{backendCalls++;throw new Error('not ready');},accessToken:'fixture',fetcher:()=>assert.fail('static should not fetch')});
 const page=await handler(new Request('wecom-inbox://app/'));assert.equal(page.status,200);assert.equal(backendCalls,0);
 const html=await page.text();
 for(const [name,type] of [['inbox-layout.css',/^text\/css/],['pane-resizer.js',/javascript/]]){
  assert(html.includes(`./${name}"`));
  const response=await handler(new Request(`wecom-inbox://app/${name}`));
  assert.equal(response.status,200);assert.match(response.headers.get('content-type'),type);
 }
 assert.doesNotMatch(html,/aihot-quickread/);
 const script=await handler(new Request('wecom-inbox://app/view-performance.mjs'));assert.equal(script.status,200);assert.match(script.headers.get('content-type'),/javascript/);
 assert.equal((await handler(new Request('wecom-inbox://app/attachments-ui.js'))).status,200);
 assert.equal((await handler(new Request('wecom-inbox://app/desktop/main.cjs'))).status,404);
 assert.equal((await handler(new Request('wecom-inbox://other/'))).status,403);
 const untrusted=new Request('wecom-inbox://app/api/messages');Object.defineProperty(untrusted,'initiatorOrigin',{value:'https://untrusted.example'});assert.equal((await handler(untrusted)).status,403);
 assert.equal((await handler(new Request('wecom-inbox://app/api/messages',{method:'POST'}))).status,405);
});
test('API代理固定回环目标并附加鉴权，流响应及重连游标不被缓冲或丢弃',async()=>{
 let supplied;const handler=createWorkbenchHandler({directory:'.',backendReady:async()=> 'http://127.0.0.1:12345',accessToken:'private-fixture',fetcher:async(url,options)=>{supplied={url,options};return new Response('event: messages\ndata: {}\n\n',{headers:{'Content-Type':'text/event-stream'}});}});
 const response=await handler(new Request('wecom-inbox://app/api/stream?after_id=42',{headers:{'Last-Event-ID':'99','Range':'bytes=0-10'}}));
 assert.equal(supplied.url,'http://127.0.0.1:12345/api/stream?after_id=42');assert.equal(supplied.options.headers['X-Workbench-Key'],'private-fixture');assert.equal(supplied.options.headers['last-event-id'],'99');assert.equal(supplied.options.redirect,'error');assert.equal(response.headers.get('content-type'),'text/event-stream');assert.match(await response.text(),/event: messages/);
});
test('真实回环转发不继承页面来源，流读取可取消',async t=>{
 let received;
 const server=http.createServer((req,res)=>{
  received=req.headers;
  res.writeHead(200,{'Content-Type':'text/event-stream'});
  res.write('event: receiver-status\ndata: {"database_ready":true}\n\n');
 });
 server.listen(0,'127.0.0.1');await once(server,'listening');
 t.after(async()=>{server.closeAllConnections();await new Promise(resolve=>server.close(resolve));});
 const origin=`http://127.0.0.1:${server.address().port}`;
 const handler=createWorkbenchHandler({directory:'.',backendReady:async()=>origin,accessToken:'synthetic-token',fetcher:fetch});
 const cancel=new AbortController();
 const request=new Request('wecom-inbox://app/api/stream',{signal:cancel.signal});
 Object.defineProperty(request,'initiatorOrigin',{value:'wecom-inbox://app'});
 const response=await handler(request),reader=response.body.getReader();
 try{
  assert.match(new TextDecoder().decode((await reader.read()).value),/database_ready/);
  assert.equal(received['x-workbench-key'],'synthetic-token');
  assert.equal(received.origin,undefined);
  assert.notEqual(received['sec-fetch-site'],'cross-site');
 }finally{cancel.abort();await reader.cancel().catch(()=>{});}
});
