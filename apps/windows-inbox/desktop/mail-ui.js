const api=window.mailCenter,$=id=>document.getElementById(id);
let provider='outlook',folder='inbox',accounts={},items=[],next='',selected=null,generation=0,reading=0,loading=false;
let draftProvider=null,reply=null,requestId=null,sendBusy=false,uncertain=false,connecting=false;
const folderCache=new Map(),bodyCache=new Map();
const cacheKey=(id,part)=>`${id}:${accounts[id]?.email||''}:${accounts[id]?.clientId||''}:${part}`;
function clearMailCache(id){for(const cache of [folderCache,bodyCache])for(const key of cache.keys())if(key.startsWith(id+':'))cache.delete(key);}
function notice(text=''){$('notice').textContent=text;$('notice').hidden=!text;}
function feedback(error){notice(error.message||String(error));}
function formatDate(value){const date=new Date(value);return Number.isNaN(date.getTime())?'':date.toLocaleString('zh-CN',{hour12:false});}
function label(id){return id==='gmail'?'Gmail':'Outlook';}
function dirty(){return ['draft-to','draft-cc','draft-bcc','draft-subject','draft-body'].some(id=>$(id).value);}
function accountView(){
 for(const id of ['outlook','gmail']){const a=accounts[id]||{};$(id+'-account').textContent=a.email||(a.configured?(a.authorizationReady?'待登录授权':'配置未完成'):'尚未接入收件台');$(id+'-dot').classList.toggle('connected',Boolean(a.connected));}
 const connected=Boolean(accounts[provider]?.connected);
 $('status-label').textContent=accounts[provider]?.email||'尚未接入收件台';$('provider-title').textContent=label(provider).toUpperCase();$('folder-title').textContent=folder==='inbox'?'收件箱':'已发送';
 $('empty-title').textContent=connected?'选择一封邮件':`${label(provider)} 尚未接入收件台`;
 $('empty-description').textContent=connected?'从左侧列表选择邮件，或开始写邮件。':'可先在系统浏览器打开官方邮箱，登录后收发邮件。在收件台内查看和发送邮件，需要完成独立账号授权。';
 $('empty-web').hidden=connected;$('empty-connect').hidden=connected;
 $('empty-web').textContent=`打开 ${label(provider)} 官方邮箱 ↗`;
 $('refresh-button').disabled=!connected;
 document.querySelectorAll('[data-provider]').forEach(button=>button.classList.toggle('selected',button.dataset.provider===provider));
 document.querySelectorAll('[data-folder]').forEach(button=>button.classList.toggle('selected',button.dataset.folder===folder));
}
async function status(){accounts=await api.status();accountView();}
function resetReader(){$('message').hidden=true;$('reader-empty').hidden=false;selected=null;reading++;}
function renderList(){
 const list=$('list');list.replaceChildren();
 if(!items.length){const empty=document.createElement('div');empty.className='empty';empty.textContent=accounts[provider]?.connected?'这个文件夹暂时没有邮件。':'连接邮箱后，在这里查看最近邮件。';list.append(empty);}
 for(const item of items){
  const button=document.createElement('button');button.className='mail-item'+(item.unread?' unread':'')+(selected?.id===item.id?' selected':'');
  const top=document.createElement('div');top.className='item-top';const from=document.createElement('span'),date=document.createElement('time');from.textContent=item.from;date.textContent=formatDate(item.date).split(' ')[0];top.append(from,date);
  const subject=document.createElement('span');subject.className='item-subject';subject.textContent=item.subject;
  const snippet=document.createElement('span');snippet.className='item-snippet';snippet.textContent=item.snippet;
  button.append(top,subject,snippet);button.addEventListener('click',()=>read(item));list.append(button);
 }
 $('more-button').hidden=!next;
}
async function refresh(more=false){
 if(loading)return;const id=provider,currentFolder=folder,g=++generation;loading=true;$('refresh-button').disabled=true;$('more-button').disabled=true;
 if(!accounts[id]?.connected){items=[];next='';renderList();loading=false;$('refresh-button').disabled=true;return;}
 notice(items.length?'正在更新，当前显示上次读取的邮件。':'正在加载邮件…');
 try{const page=await api.list(id,currentFolder,more?next:'');if(g!==generation||id!==provider||currentFolder!==folder)return;
  const all=more?[...items,...page.messages]:page.messages;items=[...new Map(all.map(m=>[m.id,m])).values()];next=page.next;folderCache.set(cacheKey(id,currentFolder),{items:[...items],next});while(folderCache.size>8)folderCache.delete(folderCache.keys().next().value);renderList();notice('');
 }catch(error){if(g===generation)notice((items.length?'刷新失败，当前列表为上次读取的邮件。\n':'')+error.message);}
 finally{if(g===generation){loading=false;$('refresh-button').disabled=false;$('more-button').disabled=false;}}
}
async function read(item){
 const id=provider,g=++reading;notice('正在读取邮件…');
 try{const key=cacheKey(id,item.id),cached=bodyCache.get(key);const message=cached&&Date.now()-cached.at<30000?cached.message:await api.get(id,item.id);if(g!==reading||id!==provider)return;if((message.body?.length||0)<256000){bodyCache.delete(key);bodyCache.set(key,{message,at:Date.now()});while(bodyCache.size>10)bodyCache.delete(bodyCache.keys().next().value);}selected=message;renderList();$('reader-empty').hidden=true;$('message').hidden=false;
  for(const [field,value] of Object.entries({subject:message.subject,from:message.from,to:message.to,date:formatDate(message.date),body:message.body||'（无文本正文）'}))$('message-'+field).textContent=value;
  $('message-attachments').hidden=!message.attachments.length;$('message-attachments').textContent='附件：'+message.attachments.join('、');notice('');
 }catch(error){if(g===reading)feedback(error);}
}
function switchView(id=provider,newFolder=folder){
 if(connecting){notice('请先完成或取消邮箱登录。');return;}
 provider=id;folder=newFolder;generation++;loading=false;notice('');const cached=accounts[id]?.connected?folderCache.get(cacheKey(id,newFolder)):null;items=cached?[...cached.items]:[];next=cached?.next||'';resetReader();accountView();renderList();refresh();
}
function showSettings(){
 $('settings-title').textContent='连接 '+label(provider);$('client-id').value=accounts[provider]?.clientId||'';
 $('google-config').hidden=provider!=='gmail';$('outlook-config').hidden=provider!=='outlook';$('settings-feedback').textContent=accounts[provider]?.configured&&!accounts[provider]?.authorizationReady?accounts[provider].setupError:'';
 if(!$('settings').open)$('settings').showModal();
}
function settingsBusy(busy){connecting=busy;for(const id of ['connect-button','save-config','import-google','disconnect-button','client-id'])$(id).disabled=busy;$('cancel-connect').hidden=!busy;}
async function configure(){
 const existing=accounts[provider];if(existing?.clientId===$('client-id').value.trim())return;
 if(existing?.connected&&!confirm('更换应用配置将清除这个邮箱在本机的授权，是否继续？'))throw new Error('已取消修改配置');
 accounts=await api.configure(provider,{clientId:$('client-id').value});clearMailCache(provider);accountView();items=[];next='';resetReader();renderList();
}
function clearDraft(){
 for(const id of ['draft-to','draft-cc','draft-bcc','draft-subject','draft-body'])$(id).value='';
 draftProvider=null;reply=null;requestId=null;uncertain=false;$('send-feedback').textContent='';$('send-button').disabled=false;
}
function compose(replyMessage=null){
 if(!accounts[provider]?.connected){showSettings();return;}
 if(replyMessage&&dirty()&&!confirm('已有未发送草稿，是否放弃并回复当前邮件？'))return;
 if(replyMessage){clearDraft();const match=(replyMessage.replyTo||replyMessage.from).match(/<([^<>]+)>/);$('draft-to').value=match?match[1]:replyMessage.replyTo||replyMessage.from;$('draft-subject').value=/^re:/i.test(replyMessage.subject)?replyMessage.subject:'Re: '+replyMessage.subject;reply={id:replyMessage.id,threadId:replyMessage.threadId||'',messageId:replyMessage.messageId||''};}
 if(!draftProvider)draftProvider=provider;
 $('compose-account').textContent=`${label(draftProvider)} · ${accounts[draftProvider]?.email||'未连接'}${reply?' · 回复':''}`;
 if(!$('compose').open)$('compose').showModal();
}
document.querySelectorAll('[data-provider]').forEach(button=>button.addEventListener('click',()=>switchView(button.dataset.provider,'inbox')));
document.querySelectorAll('[data-folder]').forEach(button=>button.addEventListener('click',()=>switchView(provider,button.dataset.folder)));
$('refresh-button').onclick=()=>refresh();$('more-button').onclick=()=>refresh(true);$('settings-button').onclick=showSettings;$('empty-connect').onclick=showSettings;
$('close-settings').onclick=()=>{if(!connecting)$('settings').close();};$('settings').addEventListener('cancel',event=>{if(connecting)event.preventDefault();});
$('save-config').onclick=async()=>{try{await configure();$('settings-feedback').textContent=accounts[provider]?.authorizationReady?'应用配置已保存，请点击“登录并授权”。':accounts[provider]?.setupError||'请完成应用配置。';}catch(error){$('settings-feedback').textContent=error.message;}};
$('import-google').onclick=async()=>{try{if(accounts.gmail?.connected&&!confirm('导入新配置将清除 Gmail 本机授权，是否继续？'))return;accounts=await api.importGoogle();clearMailCache('gmail');accountView();$('client-id').value=accounts.gmail.clientId;$('settings-feedback').textContent='已读取 Google 桌面应用配置。';items=[];next='';resetReader();renderList();}catch(error){$('settings-feedback').textContent=error.message;}};
$('connect-button').onclick=async()=>{if(connecting)return;const id=provider;settingsBusy(true);try{await configure();if(!accounts[id]?.authorizationReady)throw new Error(accounts[id]?.setupError||'请先完成应用授权配置。');$('settings-feedback').textContent='请在系统浏览器完成登录和授权（3 分钟内）。';accounts=await api.connect(id);clearMailCache(id);accountView();$('settings').close();items=[];next='';resetReader();await refresh();}catch(error){$('settings-feedback').textContent=error.message;}finally{settingsBusy(false);}};
$('cancel-connect').onclick=()=>api.cancel(provider).catch(feedback);
$('disconnect-button').onclick=async()=>{if(!confirm('删除此邮箱在本机的授权？现有邮件不会被删除。'))return;try{accounts=await api.disconnect(provider);clearMailCache(provider);accountView();items=[];next='';resetReader();renderList();$('settings-feedback').textContent='已断开本机授权。';}catch(error){$('settings-feedback').textContent=error.message;}};
async function openOfficial(){
 try{await api.openWeb(provider);notice(`已请求系统浏览器打开 ${label(provider)} 官方邮箱。请在浏览器中登录和收发邮件；收件台内的账号仍需独立连接。`);}catch(error){feedback(new Error('无法打开系统浏览器：'+error.message));}
}
$('web-button').onclick=openOfficial;$('empty-web').onclick=openOfficial;$('settings-web').onclick=openOfficial;$('compose-button').onclick=()=>compose();$('reply-button').onclick=()=>{if(selected)compose(selected);};
$('close-compose').onclick=()=>{if(!sendBusy)$('compose').close();};$('compose').addEventListener('cancel',event=>{if(sendBusy)event.preventDefault();});
$('discard-button').onclick=()=>{if(sendBusy||!confirm('放弃当前草稿？'))return;clearDraft();$('compose').close();};
$('compose-form').addEventListener('submit',async event=>{
 event.preventDefault();if(sendBusy||uncertain)return;sendBusy=true;$('send-button').disabled=true;$('discard-button').disabled=true;
 const fields=['draft-to','draft-cc','draft-bcc','draft-subject','draft-body'];fields.forEach(id=>$(id).disabled=true);requestId||=crypto.randomUUID();
 try{const result=await api.send(draftProvider,requestId,{to:$('draft-to').value,cc:$('draft-cc').value,bcc:$('draft-bcc').value,subject:$('draft-subject').value,body:$('draft-body').value,reply});
  if(result.canceled){requestId=null;$('send-feedback').textContent='已取消发送，草稿保留。';return;}
  clearDraft();$('compose').close();notice('邮箱服务已接受发送请求。可到“已发送”查看；这不代表对方已收到。');
 }catch(error){uncertain=/未确认|网络|请求失败|刷新失败|失效|权限|请求过多/.test(error.message);$('send-feedback').textContent=error.message+(uncertain?'\n草稿已保留；请先在官方邮箱检查发送状态，再决定是否重新撰写。':'');if(!uncertain)requestId=null;}
 finally{sendBusy=false;$('send-button').disabled=uncertain;$('discard-button').disabled=false;fields.forEach(id=>$(id).disabled=false);}
});
status().then(()=>refresh()).catch(feedback);
setInterval(()=>{if(!document.hidden&&!$('compose').open&&!$('settings').open&&accounts[provider]?.connected&&!next)refresh();},60000);

const sectionBar=document.createElement('nav');sectionBar.className='section-bar';sectionBar.innerHTML='<button data-section="wechat">微信</button><button data-section="wecom">企业微信</button><button class="active" data-section="mail">邮箱</button>';document.body.prepend(sectionBar);sectionBar.querySelectorAll('[data-section]').forEach(b=>b.onclick=()=>{if(b.dataset.section!=='mail')api.platform(b.dataset.section).catch(feedback);});
