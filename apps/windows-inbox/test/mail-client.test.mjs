import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import http from 'node:http';
const {MailClient,authorize,configuration,authorizationConfiguration,validateDraft,mimeMessage,gmailMessage}=createRequire(import.meta.url)('../desktop/mail-client.cjs');
const draft=()=>validateDraft({to:'a@example.com',cc:'b@example.com',bcc:'c@example.com',subject:'中文主题',body:'中文正文\n第二行',reply:{id:'message-1',threadId:'thread-1',messageId:'<original@example.com>'}});
const json=(data,status=200)=>new Response(JSON.stringify(data),{status});
function fixture(fetcher,expired=false){const account={email:'sender@example.com',config:{clientId:'test'},tokens:{accessToken:'old-access',refreshToken:'old-refresh',expiresAt:expired?0:Date.now()+3600000}};const accounts={gmail:account,outlook:structuredClone(account)};let saved=0;return {client:new MailClient({getAccount:id=>accounts[id],saveAccount:(id,data)=>{accounts[id]=data;saved++;},fetcher}),accounts,saves:()=>saved};}

test('邮件输入拒绝地址与主题换行注入，MIME 保留中文与回复头',()=>{
 assert.throws(()=>validateDraft({to:'a@example.com\r\nBcc: victim@example.com',subject:'a',body:'b'}));
 assert.throws(()=>validateDraft({to:'a@example.com',subject:'a\r\nBcc: victim@example.com',body:'b'}));
 const d=draft(),mime=Buffer.from(mimeMessage(d),'base64url').toString();
 assert.match(mime,/In-Reply-To: <original@example.com>/);assert.match(mime,/Bcc: c@example.com/);
 assert.match(mime,/Subject: =\?UTF-8\?B\?/);assert.equal(Buffer.from(mime.split('\r\n\r\n')[1].replaceAll('\r\n',''),'base64').toString(),d.body);
 assert.throws(()=>configuration('outlook',{clientId:'invalid'}));assert.throws(()=>configuration('gmail',{clientId:'not-a-desktop-client'}));
});
test('Gmail 分页只读请求，纯文本优先且附件不会混入正文',async()=>{
 const calls=[];const {client}=fixture(async(url,options)=>{calls.push({url,options});if(url.includes('/messages?'))return json({messages:[{id:'abc'}],nextPageToken:'next-page'});return json({id:'abc',labelIds:['UNREAD'],internalDate:'1700000000000',payload:{headers:[{name:'Subject',value:'test'}],parts:[{mimeType:'text/plain',body:{data:Buffer.from('正文').toString('base64url')}},{mimeType:'text/html',body:{data:Buffer.from('<script>bad()</script>html').toString('base64url')}},{mimeType:'text/plain',filename:'attachment.txt',body:{data:Buffer.from('附件').toString('base64url')}}]}});});
 const page=await client.list('gmail','inbox','opaque cursor');assert.equal(page.next,'next-page');assert.equal(page.messages[0].unread,true);
 const m=await client.get('gmail','abc');assert.equal(m.body,'正文');assert.deepEqual(m.attachments,['attachment.txt']);
 assert(calls.every(c=>c.options.method==='GET'));assert(calls[0].url.includes('labelIds=INBOX'));assert(calls[0].url.includes('pageToken=opaque+cursor'));
});
test('Outlook 分页不允许邮件服务返回任意主机地址',async()=>{
 const {client}=fixture(async()=>json({value:[],'@odata.nextLink':'https://attacker.example/v1.0/me/mailFolders/inbox/messages?$skip=25'}));
 await assert.rejects(()=>client.list('outlook'),/无效分页/);
});
test('同一账号的并发邮箱列表读取合并，下一次刷新仍访问服务',async()=>{
 let calls=0;const {client}=fixture(async()=>{calls++;await new Promise(resolve=>setTimeout(resolve,10));return json({messages:[]});});
 await Promise.all([client.list('gmail'),client.list('gmail')]);assert.equal(calls,1);
 await client.list('gmail');assert.equal(calls,2);
});
test('并发读取只刷新一次，轮换后的 refresh token 会保存',async()=>{
 let refreshes=0;const {client,accounts,saves}=fixture(async url=>{if(url.includes('/token')){refreshes++;await new Promise(r=>setTimeout(r,10));return json({access_token:'new-access',refresh_token:'new-refresh',expires_in:3600});}return json({emailAddress:'a@example.com'});},true);
 await Promise.all([client.profile('gmail'),client.profile('gmail')]);assert.equal(refreshes,1);assert.equal(accounts.gmail.tokens.refreshToken,'new-refresh');assert.equal(saves(),1);
});
test('Gmail 和 Outlook 发送使用各自官方端点；Outlook 202 只代表已接受',async()=>{
 const calls=[];const {client}=fixture(async(url,options)=>{calls.push({url,options});return url.includes('googleapis')?json({id:'sent-1'}):new Response(null,{status:202});});
 assert.equal((await client.send('gmail',draft())).id,'sent-1');assert.equal((await client.send('outlook',draft())).accepted,true);
 assert.match(calls[0].url,/gmail\.googleapis\.com.*messages\/send$/);assert.match(calls[1].url,/graph\.microsoft\.com.*sendMail$/);
 const mime=Buffer.from(calls[1].options.body,'base64').toString();assert.match(mime,/In-Reply-To:/);assert.match(mime,/From: sender@example.com/);assert.equal(calls[1].options.headers['Content-Type'],'text/plain');
});
test('发送网络失败不重放；401 不重放发送请求',async()=>{
 let calls=0;const {client}=fixture(async()=>{calls++;throw new Error('timeout');});await assert.rejects(()=>client.send('gmail',draft()),/结果未确认/);assert.equal(calls,1);
 const revoked=fixture(async()=>{calls++;return json({},401);});await assert.rejects(()=>revoked.client.send('outlook',draft()),/授权已失效/);assert.equal(calls,2);
});
function loopback(url){return new Promise((resolve,reject)=>{const parsed=new URL(url);const req=http.get({host:'127.0.0.1',port:parsed.port,path:parsed.pathname+parsed.search,headers:{Host:parsed.host}},res=>{res.resume();res.on('end',()=>resolve(res.statusCode));});req.on('error',reject);});}
test('OAuth 使用 PKCE、随机 state、本机回调，错误 state 不消耗授权',async()=>{
 let tokenRequest;const tokens=await authorize('gmail',{clientId:'test.apps.googleusercontent.com'},async auth=>{
  const u=new URL(auth);assert.equal(u.origin,'https://accounts.google.com');assert.equal(u.searchParams.get('code_challenge_method'),'S256');
  const callback=new URL(u.searchParams.get('redirect_uri'));assert.equal(callback.hostname,'127.0.0.1');callback.search=new URLSearchParams({state:'wrong',code:'x'});assert.equal(await loopback(callback),400);
  callback.search=new URLSearchParams({state:u.searchParams.get('state'),code:'valid'});assert.equal(await loopback(callback),200);
 },{timeout:2000,fetcher:async(url,options)=>{tokenRequest=options.body;return json({access_token:'access',refresh_token:'refresh',expires_in:3600});}});
 assert.equal(tokens.accessToken,'access');assert.equal(tokenRequest.get('grant_type'),'authorization_code');assert(tokenRequest.get('code_verifier').length>40);
});
test('OAuth 被取消时结束回调监听，不发起令牌交换',async()=>{
 await assert.rejects(()=>authorize('outlook',{clientId:'00000000-0000-0000-0000-000000000000'},async auth=>{const u=new URL(auth);const callback=new URL(u.searchParams.get('redirect_uri'));callback.search=new URLSearchParams({state:u.searchParams.get('state'),error:'access_denied'});await loopback(callback);},{timeout:2000,fetcher:async()=>{assert.fail('取消后不能请求令牌');}}),/已取消/);
});
test('邮件中 HTML/脚本只作为文本解析，不执行远程内容',()=>{
 const m=gmailMessage({id:'x',payload:{mimeType:'text/html',body:{data:Buffer.from('<script>bad()</script><p>Hello &amp; bye</p><img src="https://tracker.example">').toString('base64url')}}});assert.equal(m.body.trim(),'Hello & bye');
});
test('Gmail 登录前识别缺少 client_secret，完整桌面配置才可授权',()=>{
 assert.throws(()=>authorizationConfiguration('gmail',{clientId:'desktop.apps.googleusercontent.com'}),/完整 OAuth JSON/);
 assert.equal(authorizationConfiguration('gmail',{clientId:'desktop.apps.googleusercontent.com',clientSecret:'test-secret'}).clientSecret,'test-secret');
});
test('读取遇到401可刷新一次，刷新失败说明原因且不泄露服务端内容',async()=>{
 let reads=0,refreshes=0;
 const {client}=fixture(async(url,options)=>{
  if(url.includes('/token')){refreshes++;return json({access_token:'new-access',expires_in:3600});}
  reads++;return options.headers.Authorization==='Bearer old-access'?json({},401):json({emailAddress:'sender@example.com'});
 });
 assert.equal(await client.profile('gmail'),'sender@example.com');assert.equal(reads,2);assert.equal(refreshes,1);
 const failed=fixture(async url=>url.includes('/token')?json({error:'invalid_grant',error_description:'PRIVATE CONTENT'},400):json({},401));
 await assert.rejects(()=>failed.client.profile('gmail'),error=>/重新登录/.test(error.message)&&!error.message.includes('PRIVATE'));
});
test('授权服务器网络失败显示系统代理检查提示',async()=>{
 const {client}=fixture(async()=>{throw new Error('fetch failed private detail');},true);
 await assert.rejects(()=>client.profile('gmail'),error=>/系统网络和代理/.test(error.message)&&!error.message.includes('private'));
});
