import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,mkdirSync,writeFileSync,rmSync,unlinkSync,readFileSync,realpathSync,statSync} from 'node:fs';
import {createHash} from 'node:crypto';
import path from 'node:path';
import os from 'node:os';
import {createRequire} from 'node:module';
import {EventStore} from '../backend/wecom-mcp/src/store.js';
import {createWorkbenchServer} from '../backend/wecom-workbench/server.mjs';
import {normalizeAttachment,attachmentCard} from '../backend/wecom-workbench/dist/attachments-ui.js';
const require=createRequire(import.meta.url);
const {resolveAttachment,openable}=require('../desktop/attachment-files.cjs');
const {installAttachments}=require('../desktop/attachments-desktop.cjs');

test('打开附件只接受收件台主框架，支持内部安全源',async t=>{
 const root=mkdtempSync(path.join(os.tmpdir(),'inbox-open-'));t.after(()=>rmSync(root,{recursive:true,force:true}));
 const id='c'.repeat(64),folder=path.join(root,'attachments',id);mkdirSync(path.join(folder,'content'),{recursive:true});
 writeFileSync(path.join(folder,'content','file.pdf'),'pdf');writeFileSync(path.join(folder,'manifest.json'),JSON.stringify({version:1,id,name:'file.pdf',size:3}));
 const frame={url:'wecom-inbox://app/'},webContents={mainFrame:frame};let handler,opened;
 installAttachments({app:{getPath:()=>root},ipcMain:{handle:(name,fn)=>{if(name==='attachments:open')handler=fn;}},shell:{openPath:async filename=>{opened=filename;return '';}},getMainWindow:()=>({webContents}),getOrigin:()=> 'wecom-inbox://app'});
 const result=await handler({sender:webContents,senderFrame:frame},id);assert.equal(result.ok,true);assert.equal(path.basename(opened),'file.pdf');
 await assert.rejects(handler({sender:webContents,senderFrame:{url:'wecom-inbox://app/'}},id),/无权/);
 frame.url='https://example.com/';await assert.rejects(handler({sender:webContents,senderFrame:frame},id),/无权/);
});

test('附件仅按本机清单定位；路径穿越、错误大小和执行文件不能直接打开',t=>{
 const root=mkdtempSync(path.join(os.tmpdir(),'inbox-file-'));t.after(()=>rmSync(root,{recursive:true,force:true}));
 const id='a'.repeat(64),folder=path.join(root,'attachments',id);mkdirSync(path.join(folder,'content'),{recursive:true});
 writeFileSync(path.join(folder,'content','file.pdf'),'pdf');
 const manifest=path.join(folder,'manifest.json');writeFileSync(manifest,JSON.stringify({version:1,id,name:'file.pdf',size:3}));
 assert.equal(resolveAttachment(root,id).name,'file.pdf');
 assert.throws(()=>resolveAttachment(root,'../file.pdf'));
 writeFileSync(manifest,JSON.stringify({version:1,id,name:'../secret',size:3}));assert.throws(()=>resolveAttachment(root,id));
 writeFileSync(manifest,JSON.stringify({version:1,id,name:'file.pdf',size:99}));assert.throws(()=>resolveAttachment(root,id));
 assert.equal(openable.test('file.pdf'),true);assert.equal(openable.test('file.cmd'),false);assert.equal(openable.test('file.exe'),false);
});

test('附件卡片转义文件名并区分已同步和等待下载',()=>{
 const escapeHtml=s=>String(s).replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
 const config={escapeHtml,icon:()=>''},id='b'.repeat(64);
 const ready=attachmentCard(normalizeAttachment({id,name:'<file>.pdf',size:1024,type:'PDF',status:'ready'}),config);
 assert.match(ready,/&lt;file&gt;.pdf/);assert.match(ready,/已同步到本机/);assert.doesNotMatch(ready,/ disabled/);
 const pending=attachmentCard(normalizeAttachment({id,name:'file.pdf',status:'pending'}),config);
 assert.match(pending,/等待客户端下载/);assert.match(pending,/ disabled/);
 const deferred=attachmentCard(normalizeAttachment({id,name:'file.pdf',status:'on_demand'}),config);
 assert.match(deferred,/保留离线副本/);assert.match(deferred,/data-attachment-save/);assert.doesNotMatch(deferred,/ disabled/);
 const mp3=attachmentCard(normalizeAttachment({id,name:'file.mp3',status:'on_demand'}),config);
 assert.match(mp3,/data-audio-preview/);
});

test('另存为保留消息文件名及原始字节，取消不会写文件',async t=>{
 const root=mkdtempSync(path.join(os.tmpdir(),'inbox-save-')),id='d'.repeat(64);
 const verified=realpathSync(root);
 assert.equal(path.dirname(verified),realpathSync(os.tmpdir()));assert.match(path.basename(verified),/^inbox-save-/);
 t.after(()=>rmSync(verified,{recursive:true,force:true}));
 const folder=path.join(root,'attachments',id),content=path.join(folder,'content'),bytes=Buffer.from([37,80,68,70,45,0,255]);
 mkdirSync(content,{recursive:true});writeFileSync(path.join(content,'old-report.pdf'),bytes);
 writeFileSync(path.join(folder,'manifest.json'),JSON.stringify({version:1,id,name:'old-report.pdf',display_name:'通知.pdf',size:bytes.length}));
 const frame={url:'wecom-inbox://app/'},webContents={mainFrame:frame};let handler,canceled=true;
 const target=path.join(root,'通知.pdf');
 installAttachments({app:{getPath:()=>root},ipcMain:{handle:(name,fn)=>{if(name==='attachments:save-as')handler=fn;}},shell:{},dialog:{showSaveDialog:async(win,opts)=>{
  assert.equal(opts.defaultPath,target);return {canceled,filePath:target};
 }},getMainWindow:()=>({webContents}),getOrigin:()=> 'wecom-inbox://app'});
 const event={sender:webContents,senderFrame:frame};
 assert.deepEqual(await handler(event,id),{ok:false,canceled:true});assert.throws(()=>readFileSync(target),{code:'ENOENT'});
 canceled=false;assert.deepEqual(await handler(event,id),{ok:true});assert.deepEqual(readFileSync(target),bytes);
});

test('本机 MP3 先校验类型，再通过受保护的音频接口提供字节区间',async t=>{
 const root=mkdtempSync(path.join(os.tmpdir(),'inbox-audio-')),id='3'.repeat(64);
 const bytes=Buffer.concat([Buffer.from('ID3'),Buffer.alloc(40,3)]);
 const folder=path.join(root,'attachments',id),content=path.join(folder,'content'),source=path.join(content,'file.mp3');
 mkdirSync(content,{recursive:true});writeFileSync(source,bytes);
 writeFileSync(path.join(folder,'manifest.json'),JSON.stringify({version:1,id,name:'file.mp3',size:bytes.length}));
 const server=createWorkbenchServer({dbPath:path.join(root,'messages.db'),accessToken:'audio-fixture'});
 await new Promise(r=>server.listen(0,'127.0.0.1',r));
 t.after(async()=>{await new Promise(r=>{server.close(r);server.closeAllConnections();});const verified=realpathSync(root);assert.equal(path.dirname(verified),realpathSync(os.tmpdir()));assert.match(path.basename(verified),/^inbox-audio-/);rmSync(verified,{recursive:true,force:true});});
 const url=`http://127.0.0.1:${server.address().port}/api/audio?id=${id}`,headers={'X-Workbench-Key':'audio-fixture'};
 assert.equal((await fetch(url)).status,403);
 const clip=await fetch(url,{headers:{...headers,Range:'bytes=0-2'}});assert.equal(clip.status,206);assert.equal(clip.headers.get('content-type'),'audio/mpeg');assert.equal(await clip.text(),'ID3');
 assert.deepEqual(Buffer.from(await (await fetch(url,{headers})).arrayBuffer()),bytes);
 writeFileSync(source,Buffer.alloc(bytes.length));assert.equal((await fetch(url,{headers})).status,415);
});

test('企业微信原视频按需读取，支持拖动进度且不返回已变化的缓存文件',async t=>{
 const root=mkdtempSync(path.join(os.tmpdir(),'inbox-video-'));
 const id='f'.repeat(64),folder=path.join(root,'attachments',id),account=path.join(root,'selected'),base=path.join(account,'Cache','Video');
 for(const dir of [folder,base,path.join(root,'local-reading')])mkdirSync(dir,{recursive:true});
 const bytes=Buffer.from('000000106674797069736f6d00000000000000086d646174','hex');
 const original=path.join(base,'native.mp4');writeFileSync(original,bytes);
 writeFileSync(path.join(root,'local-reading','profile-path.txt'),path.join(account,'Data'));
 const canonical=realpathSync(base),identity=process.platform==='win32'?canonical.toLowerCase():canonical;
 writeFileSync(path.join(folder,'manifest.json'),JSON.stringify({version:1,id,name:'视频.mp4',size:bytes.length,source:{platform:'wecom',cache:'video',relative:'native.mp4',mtime_ms:Number(statSync(original,{bigint:true}).mtimeNs/1000000n),root_id:createHash('sha256').update(identity).digest('hex')}}));
 const server=createWorkbenchServer({dbPath:path.join(root,'messages.db'),accessToken:'video-fixture'});
 await new Promise(r=>server.listen(0,'127.0.0.1',r));
 t.after(async()=>{await new Promise(r=>{server.close(r);server.closeAllConnections();});assert.equal(path.dirname(realpathSync(root)),realpathSync(os.tmpdir()));rmSync(root,{recursive:true,force:true});});
 const url=`http://127.0.0.1:${server.address().port}/api/video?id=${id}`,headers={'X-Workbench-Key':'video-fixture'};
 assert.equal((await fetch(url)).status,403);
 const head=await fetch(url,{headers,method:'HEAD'});assert.equal(head.headers.get('content-length'),String(bytes.length));
 const slice=await fetch(url,{headers:{...headers,Range:'bytes=4-7'}});assert.equal(slice.status,206);assert.equal(slice.headers.get('content-range'),`bytes 4-7/${bytes.length}`);assert.equal(await slice.text(),'ftyp');
 const invalid=await fetch(url,{headers:{...headers,Range:'bytes=999-'}});assert.equal(invalid.status,416);
 const suffix=await fetch(url,{headers:{...headers,Range:'bytes=-4'}});assert.equal(await suffix.text(),'mdat');
 assert.deepEqual(Buffer.from(await (await fetch(url,{headers})).arrayBuffer()),bytes);
 writeFileSync(original,Buffer.concat([bytes,bytes]));
 assert.equal((await fetch(url,{headers})).status,404);
});

test('按需访问不复制文件，选择保留后可以离线打开',async t=>{
 const root=mkdtempSync(path.join(os.tmpdir(),'inbox-demand-'));
 const verified=realpathSync(root);
 assert.equal(path.dirname(verified),realpathSync(os.tmpdir()));assert.match(path.basename(verified),/^inbox-demand-/);
 t.after(()=>rmSync(verified,{recursive:true,force:true}));
 const id='e'.repeat(64),folder=path.join(root,'attachments',id),profile=path.join(root,'selected'),cache=path.join(profile,'msg','file','2026-11');
 for(const dir of [folder,cache,path.join(root,'wechat-reading')])mkdirSync(dir,{recursive:true});
 const original=path.join(cache,'source.pdf'),saved=path.join(folder,'content','file.pdf');
 writeFileSync(original,'pdf');writeFileSync(path.join(root,'wechat-reading','account-path.txt'),profile);
 const canonical=realpathSync(profile),identity=process.platform==='win32'?canonical.toLowerCase():canonical;
 const mtime=()=>Number(statSync(original,{bigint:true}).mtimeNs/1000000n);
 writeFileSync(path.join(folder,'manifest.json'),JSON.stringify({version:1,id,name:'file.pdf',size:3,source:{platform:'wechat',relative:'msg/file/2026-11/source.pdf',mtime_ms:mtime(),root_id:createHash('sha256').update(identity).digest('hex')}}));
 const frame={url:'wecom-inbox://app/'},webContents={mainFrame:frame},handlers=new Map(),event={sender:webContents,senderFrame:frame};let opened;
 installAttachments({app:{getPath:()=>root},ipcMain:{handle:(name,handler)=>handlers.set(name,handler)},shell:{openPath:async filename=>{opened=filename;return '';}},getMainWindow:()=>({webContents}),getOrigin:()=> 'wecom-inbox://app'});
 const open=handlers.get('attachments:open'),keep=handlers.get('attachments:keep');
 assert.equal((await open(event,id)).ok,true);assert.equal(opened,realpathSync(original));assert.throws(()=>resolveAttachment(root,id));
 writeFileSync(original,'new size');assert.equal((await open(event,id)).ok,false);
 writeFileSync(original,'pdf');const manifest=path.join(folder,'manifest.json'),data=JSON.parse(readFileSync(manifest));data.source.mtime_ms=mtime();writeFileSync(manifest,JSON.stringify(data));
 assert.equal((await keep(event,id)).ok,true);unlinkSync(original);
 assert.equal(readFileSync(saved,'utf8'),'pdf');assert.equal((await open(event,id)).ok,true);assert.equal(opened,realpathSync(saved));
});

test('同一消息的附件下载完成通过修订流推送，保留消息ID和接收时间',{timeout:8000},async t=>{
 const directory=mkdtempSync(path.join(os.tmpdir(),'inbox-update-')),dbPath=path.join(directory,'messages.db');
 const store=new EventStore(dbPath),server=createWorkbenchServer({dbPath,port:0});
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const controller=new AbortController();
 t.after(async()=>{controller.abort();server.closeStreams();await new Promise(resolve=>server.close(resolve));store.close();rmSync(directory,{recursive:true,force:true});});
 const message={Source:'local-database',CreateTime:Date.now()/1000,MsgType:'text',Content:'[文件] file.pdf',Attachment:{id:'b'.repeat(64),name:'file.pdf',status:'pending'}};
 store.upsertLocal('file',message);const previous=store.list().events[0];
 const response=await fetch(`http://127.0.0.1:${server.address().port}/api/stream?after_id=${previous.id}`,{signal:controller.signal});
 const reader=response.body.getReader();await reader.read();
 store.upsertLocal('file',{...message,Attachment:{...message.Attachment,status:'ready',size:3}});
 let text='';while(!text.includes('message-updates')||!text.includes('ready')){const chunk=await reader.read();text+=new TextDecoder().decode(chunk.value);}
 assert.match(text,/"status":"ready"/);
 const updated=store.list().events[0];assert.equal(updated.id,previous.id);assert.equal(updated.received_at,previous.received_at);assert.equal(store.list().events.length,1);
 await reader.cancel();
});
