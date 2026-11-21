import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

function renderer(api){
 class Element{
  constructor(){this.value='';this.hidden=false;this.disabled=false;this.open=false;this.textContent='';this.listeners=new Map();this.children=[];this.dataset={};this.classList={toggle(){}};}
  addEventListener(name,callback){this.listeners.set(name,callback);}
  append(...nodes){this.children.push(...nodes);}
  prepend(...nodes){this.children.unshift(...nodes);}
  querySelectorAll(){return [];}
  replaceChildren(){this.children=[];}
  showModal(){this.open=true;}
  close(){this.open=false;}
 }
 const html=fs.readFileSync(new URL('../desktop/mail.html',import.meta.url),'utf8'),elements=new Map([...html.matchAll(/\bid="([^"]+)"/g)].map(match=>[match[1],new Element()]));
 const providers=['outlook','gmail'].map(provider=>Object.assign(new Element(),{dataset:{provider}}));
 const folders=['inbox','sent'].map(folder=>Object.assign(new Element(),{dataset:{folder}}));
 const document={body:new Element(),hidden:false,getElementById:id=>{assert(elements.has(id),'Missing HTML element: '+id);return elements.get(id);},querySelectorAll:selector=>selector==='[data-provider]'?providers:folders,createElement:()=>new Element()};
 vm.runInNewContext(fs.readFileSync(new URL('../desktop/mail-ui.js',import.meta.url),'utf8'),{window:{mailCenter:api},document,setInterval:()=>0,confirm:()=>true,crypto:globalThis.crypto});
 return {get:id=>elements.get(id),switchProvider:id=>providers.find(element=>element.dataset.provider===id).listeners.get('click')()};
}
const flush=()=>new Promise(resolve=>setImmediate(resolve));
test('未授权首页说明接入状态，官方邮箱按钮按当前提供商打开',async()=>{
 const opened=[];let reads=0;
 const ui=renderer({status:async()=>({outlook:{},gmail:{}}),list:async()=>{reads++;},openWeb:async id=>opened.push(id)});
 await flush();assert.equal(ui.get('refresh-button').disabled,true);assert.equal(reads,0);
 assert.match(ui.get('empty-title').textContent,/Outlook 尚未接入/);
 await ui.get('empty-web').onclick();assert.deepEqual(opened,['outlook']);assert.match(ui.get('notice').textContent,/系统浏览器/);
 ui.switchProvider('gmail');await flush();assert.match(ui.get('empty-title').textContent,/Gmail 尚未接入/);
 await ui.get('empty-web').onclick();assert.deepEqual(opened,['outlook','gmail']);assert.equal(reads,0);
 ui.get('empty-connect').onclick();assert.equal(ui.get('settings').open,true);
});
test('Gmail 配置未完成时阻止无效登录并显示原因',async()=>{
 let connects=0;
 const ui=renderer({status:async()=>({outlook:{},gmail:{configured:true,clientId:'desktop.apps.googleusercontent.com',authorizationReady:false,setupError:'请导入完整 OAuth JSON'}}),connect:async()=>{connects++;}});
 await flush();ui.switchProvider('gmail');ui.get('empty-connect').onclick();
 assert.match(ui.get('settings-feedback').textContent,/完整 OAuth JSON/);
 await ui.get('connect-button').onclick();assert.equal(connects,0);assert.equal(ui.get('connect-button').disabled,false);
 assert.match(ui.get('settings-feedback').textContent,/完整 OAuth JSON/);
});
test('邮箱初始化失败明确显示错误，避免空白且无提示',async()=>{
 const ui=renderer({status:async()=>{throw new Error('无法读取邮箱授权文件');}});
 await flush();assert.equal(ui.get('notice').hidden,false);assert.match(ui.get('notice').textContent,/无法读取邮箱授权文件/);
});
