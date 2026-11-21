import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {EventEmitter} from 'node:events';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import http from 'node:http';
const require=createRequire(import.meta.url),{installMailCenter}=require('../desktop/mail-desktop.cjs'),{MailClient}=require('../desktop/mail-client.cjs');
test('邮箱 IPC 限定自身主框架；重复发送请求只确认并提交一次',async t=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'mail-ipc-test-'));
 t.after(()=>{for(const file of fs.readdirSync(dir))fs.unlinkSync(path.join(dir,file));fs.rmdirSync(dir);});
 const accounts={gmail:{email:'sender@example.com',config:{clientId:'test.apps.googleusercontent.com'},tokens:{accessToken:'fake',expiresAt:Date.now()+3600000}}};
 fs.writeFileSync(path.join(dir,'mail-accounts.bin'),Buffer.from(JSON.stringify(accounts)));
 const handlers=new Map(),app=new EventEmitter();app.getPath=()=>dir;
 const ipcMain={handle:(name,fn)=>handlers.set(name,fn),on:()=>{}};let lastWindow,confirmations=0,sends=0;
 class Window extends EventEmitter{constructor(){super();lastWindow=this;this.webContents=new EventEmitter();this.webContents.setWindowOpenHandler=()=>{};this.webContents.mainFrame={url:pathToFileURL(path.resolve('desktop/mail.html')).href};}loadFile(){}show(){}isDestroyed(){return false;}}
 const safeStorage={isEncryptionAvailable:()=>true,decryptString:b=>b.toString(),encryptString:s=>Buffer.from(s)};
 const original=MailClient.prototype.send;MailClient.prototype.send=async()=>{sends++;await new Promise(r=>setTimeout(r,5));return {accepted:true};};t.after(()=>MailClient.prototype.send=original);
 const center=installMailCenter({app,BrowserWindow:Window,ipcMain,safeStorage,shell:{},dialog:{showMessageBox:async()=>{confirmations++;return {response:1};}},getMainWindow:()=>null});center.open();
 const event={sender:lastWindow.webContents,senderFrame:lastWindow.webContents.mainFrame};
 assert.equal((await handlers.get('mail:status')({...event,senderFrame:{url:event.senderFrame.url}})).ok,false);
 const state=await handlers.get('mail:status')(event);assert.equal(state.ok,true);assert.equal(state.data.gmail.connected,true);assert(!JSON.stringify(state).includes('accessToken'));
 const args=['gmail','11111111-1111-1111-1111-111111111111',{to:'receiver@example.com',subject:'test',body:'test'}];
 const results=await Promise.all([handlers.get('mail:send')(event,...args),handlers.get('mail:send')(event,...args)]);
 assert(results.every(result=>result.ok&&result.data.accepted));assert.equal(sends,1);assert.equal(confirmations,1);
});
test('未连接时明确配置状态；授权交换和收件统一使用注入的系统网络通道',async t=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'mail-network-test-'));
 t.after(()=>{for(const file of fs.readdirSync(dir))fs.unlinkSync(path.join(dir,file));fs.rmdirSync(dir);});
 const googleConfig=path.join(dir,'google.json');fs.writeFileSync(googleConfig,JSON.stringify({installed:{client_id:'desktop.apps.googleusercontent.com',client_secret:'synthetic-secret'}}));
 const handlers=new Map(),app=new EventEmitter();app.getPath=()=>dir;
 let lastWindow;const opened=[],network=[];
 class Window extends EventEmitter{constructor(){super();lastWindow=this;this.webContents=new EventEmitter();this.webContents.setWindowOpenHandler=()=>{};this.webContents.mainFrame={url:pathToFileURL(path.resolve('desktop/mail.html')).href};}loadFile(){}show(){}isDestroyed(){return false;}}
 const center=installMailCenter({app,BrowserWindow:Window,ipcMain:{handle:(name,handler)=>handlers.set(name,handler),on:()=>{}},safeStorage:{isEncryptionAvailable:()=>true,decryptString:b=>b.toString(),encryptString:s=>Buffer.from(s)},getMainWindow:()=>null,
  dialog:{showOpenDialog:async()=>({canceled:false,filePaths:[googleConfig]})},
  shell:{openExternal:async address=>{
   opened.push(address);const url=new URL(address);if(url.hostname!=='accounts.google.com')return;
   const callback=new URL(url.searchParams.get('redirect_uri'));callback.search=new URLSearchParams({state:url.searchParams.get('state'),code:'synthetic-code'});
   await new Promise((resolve,reject)=>{const request=http.get({host:'127.0.0.1',port:callback.port,path:callback.pathname+callback.search,headers:{Host:callback.host}},response=>{response.resume();response.on('end',resolve);});request.on('error',reject);});
  }},
  fetcher:async(url,options)=>{network.push(url);return new Response(JSON.stringify(url.endsWith('/token')?{access_token:'synthetic-access',refresh_token:'synthetic-refresh',expires_in:3600}:url.endsWith('/profile')?{emailAddress:'test@example.com'}:{messages:[]}));},
 });
 center.open();const event={sender:lastWindow.webContents,senderFrame:lastWindow.webContents.mainFrame};
 const invoke=(name,...args)=>handlers.get('mail:'+name)(event,...args);
 assert.equal((await invoke('status')).data.gmail.authorizationReady,false);
 assert.equal((await invoke('connect','outlook')).ok,false);assert.equal(opened.length,0);
 await invoke('open-web','gmail');assert.deepEqual(opened,['https://mail.google.com/']);
 await invoke('configure','gmail',{clientId:'desktop.apps.googleusercontent.com'});
 const incomplete=await invoke('connect','gmail');assert.equal(incomplete.ok,false);assert.match(incomplete.error,/完整 OAuth JSON/);assert.equal(opened.length,1);
 assert.equal((await invoke('import-google')).data.gmail.authorizationReady,true);
 const connected=await invoke('connect','gmail');assert.equal(connected.ok,true);assert.equal(connected.data.gmail.email,'test@example.com');
 assert.equal(JSON.stringify(connected).includes('synthetic-'),false);
 assert.equal((await invoke('list','gmail','inbox','')).ok,true);
 assert(network.some(url=>url==='https://oauth2.googleapis.com/token'));assert(network.some(url=>url.endsWith('/profile')));assert(network.some(url=>url.includes('/messages?')));
});
