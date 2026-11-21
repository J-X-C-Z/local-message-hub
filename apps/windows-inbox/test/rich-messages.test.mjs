import test from 'node:test';
import assert from 'node:assert/strict';
import {emojiText,linkedText,normalizeQuote,quoteContent,recordDetails,richContent,validDocumentUrl,cachedShareCard} from '../backend/wecom-workbench/dist/rich-ui.js';
import {createRequire} from 'node:module';
import {mkdtempSync,mkdirSync,writeFileSync,rmSync,realpathSync,statSync,unlinkSync} from 'node:fs';
import {createHash} from 'node:crypto';
import path from 'node:path';
import os from 'node:os';
import {EventStore} from '../backend/wecom-mcp/src/store.js';
import {createWorkbenchServer} from '../backend/wecom-workbench/server.mjs';
import {EMOJI_IMAGES} from '../backend/wecom-workbench/dist/emoji-map.js';
const require=createRequire(import.meta.url);
const {installAttachments}=require('../desktop/attachments-desktop.cjs');
const escapeHtml=value=>String(value).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
test('常见表情还原为本机图片，普通方括号文字和HTML保持转义',()=>{
 const html=emojiText('[抱拳][玫瑰][嘿哈]<img src=x>[表格]',escapeHtml);
 assert.equal((html.match(/class="inline-emoji"/g)||[]).length,3);
 assert.match(html,/alt="\[抱拳\]"/);assert.match(html,/&lt;img/);assert.match(html,/\[表格\]/);
 assert.doesNotMatch(html,/https?:/);
});
test('原消息不在历史时，引用仍显示姓名、原文与行内表情，并转义所有文本',()=>{
 const quote=normalizeQuote({kind:'text',sender:'<script>',text:'原文[抱拳]\n<img src=x>',target:'../wrong'});
 const html=quoteContent(quote,null,{id:'live-2'},{escapeHtml});
 assert.match(html,/blockquote/);assert.match(html,/引用 · &lt;script&gt;/);assert.match(html,/inline-emoji/);
 assert.match(html,/&lt;img src=x&gt;/);assert.doesNotMatch(html,/data-quote-target|<script>|<img src=x>/);
 assert.equal(quote.target,'');
});
test('图片引用使用已同步的原消息缩略图，未缓存时保留类型',()=>{
 const target='a'.repeat(32),id='b'.repeat(64),quote=normalizeQuote({kind:'image',text:'图片',target});
 const render=image=>quoteContent(quote,image&&{image,sender:'小林'},{id:'live-2'},{escapeHtml});
 const ready=render({status:'ready',id});
 assert.match(ready,/引用 · 小林/);assert.match(ready,new RegExp(`/api/media\\?id=${id}`));
 assert.match(ready,new RegExp(`data-quote-target="${target}"`));
 assert.match(render(null),/图片/);assert.doesNotMatch(render({status:'ready',id:'../bad'}),/src=/);
});
test('视频消息卡片展示时长和状态而非内部参数',()=>{
 const html=richContent({video:{duration:168,size:26665768,status:'pending'},text:'*1*opaque-token'}, {escapeHtml,icon:()=>'',attachmentCard:()=>''});
 assert.match(html,/视频消息/);assert.match(html,/2:48/);assert.match(html,/原视频未缓存/);assert.doesNotMatch(html,/opaque-token|data-video-preview/);
 const ready=richContent({video:{duration:15,size:5098923,id:'d'.repeat(64),status:'on_demand'}},{escapeHtml,icon:()=>'',attachmentCard:()=>''});
 assert.match(ready,/0:15/);assert.match(ready,/播放视频/);assert.match(ready,/保留离线副本/);
 assert.doesNotMatch(ready,/<video|https?:/);
});
test('在线表格卡片和本机动态表情分别展示',()=>{
 const config={escapeHtml,icon:()=>'',attachmentCard:()=>''};
 const card=richContent({document:{title:'课表',kind:'spreadsheet',url:'https://doc.weixin.qq.com/sheet/test'}},config);
 assert.match(card,/在线表格/);assert.match(card,/data-document-open/);
 const gif=richContent({sticker:{status:'ready',id:'a'.repeat(64)}},config);
 assert.match(gif,/\/api\/media/);
 assert.match(gif,/data-media-retry/);
 assert.match(richContent({sticker:{status:'unsupported'}},config),/暂不支持预览/);
 assert.equal(validDocumentUrl('javascript:alert(1)'),null);assert.equal(validDocumentUrl('https://doc.weixin.qq.com.evil.example/a'),null);
});
test('图片显示真实缓存地址和预览标记，非法媒体编号不进入图片地址',()=>{
 const config={escapeHtml,icon:()=>'',attachmentCard:()=>''};
 const preview=richContent({image:{status:'ready',id:'e'.repeat(64),quality:'preview'}},config);
 assert.match(preview,/class="chat-image"/);assert.match(preview,/本机缩略图/);assert.match(preview,/data-media-retry/);
 assert.match(preview,/data-image-preview=/);assert.doesNotMatch(preview,/data-attachment-open=/);
 const original=richContent({image:{status:'ready',id:'e'.repeat(64),quality:'original'}},config);
 assert.match(original,/已同步原图/);
 const invalid=richContent({image:{status:'ready',id:'../secret'}},config);
 assert.doesNotMatch(invalid,/src=/);assert.match(invalid,/等待客户端缓存/);
});
test('正文与转发记录中的网页链接可点开，非网页协议和 HTML 保持纯文本',()=>{
 const html=linkedText('详见（https://example.org/a?q=1&v=2）。 file:///C:/hidden <script>[抱拳]',escapeHtml);
 assert.match(html,/href="https:\/\/example.org\/a\?q=1&amp;v=2"/);assert.match(html,/。/);
 assert.doesNotMatch(html,/href="file:|<script>/);assert.match(html,/inline-emoji/);
 const details=recordDetails({entries:[{text:'https://example.org/a',sender:'<image>'},{kind:'图片'}]},escapeHtml);
 assert.match(details,/data-web-link/);assert.match(details,/&lt;image&gt;/);assert.match(details,/图片 · 本机暂无缓存/);
});
test('转发中的缩略图可放大，嵌套记录和第 61 条以后也可展开',()=>{
 const entries=Array.from({length:61},()=>({kind:'文字',text:'fixture'}));
 entries.push({kind:'聊天记录',record:{entries:[{kind:'图片',image:{id:'a'.repeat(64),status:'ready',quality:'preview'}}]}});
 const html=recordDetails({entries},escapeHtml);
 assert.match(html,/展开第 61–62 条/);assert.match(html,/展开转发记录/);assert.match(html,/本机缩略图/);assert.match(html,/data-image-preview/);
});
test('后台扫描不会把已补齐的转发图片退回为未同步，也不会插入新消息',()=>{
 const store=new EventStore(':memory:');
 try{
  const message={Platform:'wechat',Source:'local-database',CreateTime:Date.now()/1000,Content:'转发',ChatRecord:{entries:[{kind:'图片'}]}};
  const resolved={...message,ChatRecord:{media_checked:true,entries:[{kind:'图片',image:{id:'a'.repeat(64),status:'ready'}}]}};
  store.upsertLocal('wechat:record',message);const first=store.list().events[0];
  store.upsertLocal('wechat:record',resolved);assert.equal(store.upsertLocal('wechat:record',message),false);
  const updated=store.list().events;assert.equal(updated.length,1);assert.equal(updated[0].id,first.id);assert.equal(updated[0].received_at,first.received_at);assert.equal(updated[0].message.ChatRecord.entries[0].image.status,'ready');
 }finally{store.close();}
});
test('已缓存的微信分享显示卡片，只有网页地址提供浏览器入口',()=>{
 const options={escapeHtml,icon:()=>'',attachmentCard:()=>''};
 const card=cachedShareCard(5*4294967296+49,'[链接] 公开文章\n摘要\nhttps://example.org/page');
 assert.match(richContent({card},options),/data-web-link/);assert.equal(card.description,'摘要');
 const mini=cachedShareCard(33*4294967296+49,'[小程序] <名称>\n来源');
 const html=richContent({card:mini},options);
 assert.match(html,/&lt;名称&gt;/);assert.match(html,/微信原聊天/);assert.doesNotMatch(html,/href=/);
 assert.equal(cachedShareCard(5*4294967296+49,'[链接] 测试\nfile:///C:/example').url,null);
 assert.equal(cachedShareCard(5*4294967296+1,'[链接] 测试'),null);
});
test('网页 IPC 仅接受收件台主框架和 HTTP(S) 链接',async()=>{
 let handler,opened=[];const frame={url:'wecom-inbox://app/'},webContents={mainFrame:frame};
 installAttachments({app:{},ipcMain:{handle:(name,fn)=>{if(name==='links:open')handler=fn;}},shell:{openExternal:async value=>opened.push(value)},getMainWindow:()=>({webContents}),getOrigin:()=> 'wecom-inbox://app'});
 const event={sender:webContents,senderFrame:frame};
 assert.equal((await handler(event,'https://example.org/a')).ok,true);
 for(const value of ['file:///C:/private','ms-settings://test','javascript:alert(1)','https://user:secret@example.org/'])assert.equal((await handler(event,value)).ok,false);
 await assert.rejects(handler({...event,senderFrame:{url:frame.url}},'https://example.org/'),/无权/);
 assert.deepEqual(opened,['https://example.org/a']);
});
test('文档打开IPC验证官方域名、主框架并等待用户点击',async()=>{
 let handler,opened='';const frame={url:'wecom-inbox://app/'},webContents={mainFrame:frame};
 installAttachments({app:{},ipcMain:{handle:(name,fn)=>{if(name==='documents:open')handler=fn;}},shell:{openExternal:async url=>{opened=url;}},getMainWindow:()=>({webContents}),getOrigin:()=> 'wecom-inbox://app'});
 const event={sender:webContents,senderFrame:frame};
 assert.equal(opened,'');assert.equal((await handler(event,'https://doc.weixin.qq.com/sheet/test')).ok,true);
 assert.equal(opened,'https://doc.weixin.qq.com/sheet/test');
 assert.equal((await handler(event,'file:///C:/secret')).ok,false);
 await assert.rejects(handler({...event,senderFrame:{url:frame.url}},opened),/无权/);
});
test('表情图片只通过鉴权的本机资源接口读取，GIF字节保持不变',async t=>{
 const directory=mkdtempSync(path.join(os.tmpdir(),'inbox-rich-')),id='d'.repeat(64),folder=path.join(directory,'attachments',id);mkdirSync(path.join(folder,'content'),{recursive:true});
 const gif=Buffer.from('GIF89a sample cached animation');writeFileSync(path.join(folder,'content','sticker.gif'),gif);writeFileSync(path.join(folder,'manifest.json'),JSON.stringify({version:1,id,name:'sticker.gif',size:gif.length}));
 const bmpId='b'.repeat(64), bmpFolder=path.join(directory,'attachments',bmpId);
 const bmp=Buffer.from('424d3a0000000000000036000000280000000100000001000000010018000000000004000000000000000000000000000000000000000000ff00','hex');
 mkdirSync(path.join(bmpFolder,'content'),{recursive:true});
 writeFileSync(path.join(bmpFolder,'content','pixel.bmp'),bmp);
 writeFileSync(path.join(bmpFolder,'manifest.json'),JSON.stringify({version:1,id:bmpId,name:'pixel.bmp',size:bmp.length}));
 const invalidBmpId='c'.repeat(64), invalidBmpFolder=path.join(directory,'attachments',invalidBmpId);
 const invalidBmp=Buffer.from(bmp);invalidBmp.writeUInt32LE(bmp.length+100,10);
 mkdirSync(path.join(invalidBmpFolder,'content'),{recursive:true});
 writeFileSync(path.join(invalidBmpFolder,'content','bad.bmp'),invalidBmp);
 writeFileSync(path.join(invalidBmpFolder,'manifest.json'),JSON.stringify({version:1,id:invalidBmpId,name:'bad.bmp',size:invalidBmp.length}));
 const deferredId='e'.repeat(64),deferredFolder=path.join(directory,'attachments',deferredId),account=path.join(directory,'account'),cache=path.join(account,'msg','file','2026-11'),source=path.join(cache,'pixel.bmp');
 for(const dir of [deferredFolder,cache,path.join(directory,'wechat-reading')])mkdirSync(dir,{recursive:true});
 writeFileSync(source,bmp);writeFileSync(path.join(directory,'wechat-reading','account-path.txt'),account);
 const canonical=realpathSync(account),rootId=createHash('sha256').update(process.platform==='win32'?canonical.toLowerCase():canonical).digest('hex');
 writeFileSync(path.join(deferredFolder,'manifest.json'),JSON.stringify({version:1,id:deferredId,name:'pixel.bmp',size:bmp.length,source:{platform:'wechat',relative:'msg/file/2026-11/pixel.bmp',mtime_ms:Number(statSync(source,{bigint:true}).mtimeNs/1000000n),root_id:rootId}}));
 const publicDir=path.join(directory,'public'),emojiPath=path.join(publicDir,EMOJI_IMAGES['抱拳']);
 mkdirSync(path.dirname(emojiPath),{recursive:true});
 const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVQIW2NgAAIAAAUAAXpeqz8AAAAASUVORK5CYII=','base64');
 writeFileSync(emojiPath,png);
 const dbPath=path.join(directory,'events.db'),store=new EventStore(dbPath),token='a'.repeat(64),server=createWorkbenchServer({dbPath,accessToken:token,publicDir});
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));const url=`http://127.0.0.1:${server.address().port}`,headers={'X-Workbench-Key':token};
 t.after(async()=>{server.closeStreams();await new Promise(resolve=>server.close(resolve));store.close();rmSync(directory,{recursive:true,force:true});});
 assert.equal((await fetch(url+'/api/media?id='+id)).status,403);
 const response=await fetch(url+'/api/media?id='+id,{headers});assert.equal(response.headers.get('content-type'),'image/gif');assert.deepEqual(Buffer.from(await response.arrayBuffer()),gif);
 const bitmap=await fetch(url+'/api/media?id='+bmpId,{headers});assert.equal(bitmap.status,200);assert.equal(bitmap.headers.get('content-type'),'image/bmp');assert.deepEqual(Buffer.from(await bitmap.arrayBuffer()),bmp);
 assert.equal((await fetch(url+'/api/media?id='+invalidBmpId,{headers,method:'HEAD'})).status,415);
 const deferred=await fetch(url+'/api/media?id='+deferredId,{headers});assert.equal(deferred.status,200);assert.equal(deferred.headers.get('cache-control'),'no-store');assert.deepEqual(Buffer.from(await deferred.arrayBuffer()),bmp);
 unlinkSync(source);assert.equal((await fetch(url+'/api/media?id='+deferredId,{headers})).status,404);
 assert.equal((await fetch(url+'/api/media?id=../secret',{headers})).status,404);
 const emoji=await fetch(url+EMOJI_IMAGES['抱拳'],{headers});assert.equal(emoji.status,200);assert.equal(emoji.headers.get('content-type'),'image/png');assert.deepEqual(Buffer.from(await emoji.arrayBuffer()),png);
});
