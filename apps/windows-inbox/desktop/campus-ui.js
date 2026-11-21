const $=id=>document.getElementById(id),api=window.campus;
let state={items:[],sources:[]},category='学院重点',limit=60,selected=null,detailRequest=0;
const categories=['全部','学院重点','学院官方','社团与团学','专业调整','夏令营','推免','考研','调剂','项目申报','科研成果','学术会议','图书馆','校园服务','研究前沿','开发工具','自定义订阅'];
const el=(tag,text,cls)=>{const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(cls)node.className=cls;return node;};
function date(value){return value?new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'尚未完成';}
function notify(text){$('notice').textContent=text;$('notice').hidden=!text;}
async function call(action,...args){const r=await api[action](...args);if(!r.ok)throw new Error(r.error);return r.data;}
function actionButton(label,action){const button=el('button',label);button.type='button';button.onclick=action;return button;}
async function linkAction(action,url,feedback){
 try{await call(action,url);if(action==='copy')feedback('已复制链接。');else if(feedback!==notify)feedback('已交给系统默认浏览器。');}catch(error){feedback(action==='open'?'未能打开：'+error.message+'。可复制链接重试。':error.message);}
}
async function changeSource(action,...args){
 try{state=await call(action,...args);render();$('source-feedback').textContent='已保存；点击「立即更新」可马上检查。';return true;}catch(error){$('source-feedback').textContent=error.message;return false;}
}
function topic(item,name){return name==='全部'||(name==='学院重点'?item.priority===0:item.tags.includes(name));}
function selectTopic(name,source=''){
 category=name;$('source-filter').value=source;limit=60;
 $('search').value='';$('channel').value='';$('only-unread').checked=false;
 render();$('result-count').scrollIntoView({block:'nearest'});
}
function filtered(){const q=$('search').value.trim().toLowerCase(),channel=$('channel').value,source=$('source-filter').value,sort=$('sort').value;return state.items.filter(x=>topic(x,category)&&(!$('only-unread').checked||!x.read)&&(!source||x.sourceIds.includes(source))&&(!channel||(channel==='feed'?['RSS','Atom'].includes(x.channel):x.channel.startsWith(channel)))&&(!q||[x.title,x.sourceName,...x.tags,x.summary||''].join(' ').toLowerCase().includes(q))).sort((a,b)=>{
 if(sort==='priority'&&a.priority!==b.priority)return a.priority-b.priority;
 const key=sort==='updated'?'updatedAt':'date';return (b[key]||'').localeCompare(a[key]||'')||b.firstSeenAt.localeCompare(a.firstSeenAt);
 });}
function render(){
 $('total').textContent=state.items.length;$('unread-total').textContent=state.items.filter(x=>!x.read).length;$('college-total').textContent=state.items.filter(x=>x.priority===0).length;
 const active=state.sources.filter(source=>!source.paused),healthy=active.filter(source=>source.lastSuccessAt&&!source.error).length;$('source-total').textContent=healthy+' / '+active.length;
 $('sync').textContent=state.refreshing?'正在检查信源…':'最近更新 '+date(state.lastCompletedAt);$('refresh').disabled=!!state.refreshing;
 const sourceSelect=$('source-filter'),previous=sourceSelect.value;
 sourceSelect.replaceChildren(new Option('全部来源',''),...state.sources.map(source=>new Option(source.name,source.id)),new Option('本机公众号','wechat'));
 sourceSelect.value=[...sourceSelect.options].some(option=>option.value===previous)?previous:'';
 $('categories').replaceChildren(...categories.map(name=>{const b=el('button',undefined,name===category?'active':'');b.append(el('span',name),el('span',state.items.filter(x=>topic(x,name)).length));b.onclick=()=>{category=name;limit=60;$('source-filter').value='';render();};return b;}));
 for(const [id,topicName] of [['seit-official','学院官方'],['seit-students','社团与团学']])$(id).setAttribute('aria-pressed',String(category===topicName));
 const items=filtered();$('result-count').textContent=items.length+' 条资讯';$('more').hidden=items.length<=limit;
 $('feed').replaceChildren(...items.slice(0,limit).map(item=>{
  const card=el('article',undefined,'card'+(!item.read?' unread':'')),top=el('div',undefined,'card-top');
  if(item.priority===0)top.append(el('span','学院优先','tag priority'));if(!item.read)top.append(el('span','未读','tag priority'));if(item.revision>1)top.append(el('span','已变更 · 第 '+item.revision+' 版','tag changed'));
  for(const tag of item.tags.slice(0,4))top.append(el('span',tag,'tag'));top.append(el('span',item.channel));
  const h=el('h2'),title=el('button',item.title,'title-button');title.onclick=()=>showDetail(item);h.append(title);
  const meta=el('div',undefined,'meta');meta.append(el('span',item.sourceName),el('span',item.date?(item.dateLabel||'发布日期')+' '+item.date:'发布日期未提供'),el('span','发现 '+date(item.firstSeenAt)));
  meta.append(actionButton('打开原文 ↗',()=>linkAction('open',item.url,notify)),actionButton('复制链接',()=>linkAction('copy',item.url,notify)));card.append(top,h,meta);if(item.summary)card.append(el('p',item.summary,'excerpt'));return card;
 }));
 if(!items.length){const empty=el('div',undefined,'empty');empty.append(el('strong',state.refreshing?'正在获取第一批资讯':'当前条件下暂无资讯'),el('span',state.refreshing?'首次更新可能需要几十秒，完成后自动显示。':'可以切换主题、清空搜索，或查看来源状态。'));$('feed').append(empty);}
 $('source-grid').replaceChildren(...state.sources.map(source=>{
  const box=el('div',undefined,'source'+(source.error&&!source.paused?' error':'')),buttons=el('div',undefined,'source-actions');
  buttons.append(actionButton(source.paused?'启用':'暂停',()=>changeSource('setSource',source.id,source.paused)));
  if(source.custom)buttons.append(actionButton('移除',()=>changeSource('removeSource',source.id)));
  box.append(actionButton(source.name+' ↗',()=>linkAction('open',source.url,notify)),el('div',source.paused?'已暂停':source.error?'更新失败：'+source.error:source.lastSuccessAt?'正常 · 本次 '+source.count+' 条':'等待首次检查',source.error&&!source.paused?'error-text':''),el('div','最近成功 '+date(source.lastSuccessAt)),buttons);return box;
 }));
 function renderEntry(entry,organization=false){
  const box=el('article',undefined,'entry-card'),buttons=el('div',undefined,'source-actions');
  if(entry.sourceId)buttons.append(actionButton('看收件台动态',()=>selectTopic('社团与团学',entry.sourceId)),actionButton('打开官网动态 ↗',()=>linkAction('open',entry.dynamicsUrl,notify)));
  buttons.append(actionButton(organization?(entry.sourceId?'打开组织简介 ↗':'打开官方介绍 ↗'):entry.kind==='小程序'?'打开扫码指引 ↗':'在浏览器打开 ↗',()=>linkAction('open',entry.url,notify)));
  if(!organization)buttons.append(actionButton('复制链接',()=>linkAction('copy',entry.url,notify)));
  if(entry.custom)buttons.append(actionButton('移除',()=>changeSource('removeSource',entry.id)));
  box.append(el('span',organization?(entry.sourceId?'学院官网组织栏目':'学校公开资料'):entry.kind,'tag'),el('h3',entry.name),el('p',entry.note),buttons);return box;
 }
 $('entries').replaceChildren(...(state.entries||[]).map(entry=>renderEntry(entry)));
 $('seit-orgs').replaceChildren(...(state.organizations||[]).map(entry=>renderEntry(entry,true)));
 $('wechat-status').textContent='公众号：'+(state.wechat?.error||state.wechat?.note||'等待检查本机微信缓存。');
 $('accounts').replaceChildren(...(state.accounts||[]).map(account=>{
  const chip=el('span',account,'tag');if(state.preferences?.accounts.includes(account))chip.append(actionButton('移除',()=>changeSource('removeSource','account:'+account)));return chip;
 }));
 const failures=active.filter(source=>source.error);notify(state.loadError||(failures.length?failures.length+' 个来源更新失败，已保留历史记录；详情见“来源与状态”。':''));
}
async function reload(){try{state=await call('snapshot');render();}catch(e){notify(e.message);}}
async function showDetail(item){
 selected=item;const request=++detailRequest;$('detail-feedback').textContent='';$('detail-title').textContent=item.title;$('detail-meta').textContent=item.sourceName+' · '+(item.date?(item.dateLabel||'发布日期')+' '+item.date:'日期未提供')+' · '+item.channel;$('detail-content').textContent=item.detail||item.summary||'正在读取…';if(!$('detail').open)$('detail').showModal();
 try{const d=await call('detail',item.id);if(request!==detailRequest)return;$('detail-content').textContent=d.detail||d.summary||'请打开官方原文查看内容。';state=await call('read',[item.id]);render();}catch(e){if(request===detailRequest)$('detail-content').textContent=(item.summary?item.summary+'\n\n':'')+e.message;}
}
$('refresh').onclick=async()=>{state.refreshing=true;render();try{state=await call('refresh');render();}catch(e){state.refreshing=false;render();notify(e.message);}};
$('source-toggle').onclick=()=>{$('sources').hidden=!$('sources').hidden;};
$('entry-toggle').onclick=()=>{const opened=$('entries').hidden;$('entries').hidden=!opened;$('entry-toggle').textContent=opened?'收起入口':'展开入口';$('entry-toggle').setAttribute('aria-expanded',String(opened));};
$('seit-official').onclick=()=>selectTopic('学院官方');
$('seit-students').onclick=()=>selectTopic('社团与团学');
$('seit-org-toggle').onclick=()=>{const panel=$('seit-orgs');panel.hidden=!panel.hidden;$('seit-org-toggle').setAttribute('aria-expanded',String(!panel.hidden));};
function updateSourceForm(){
 const type=$('source-type').value,account=type.endsWith('account');$('source-url').hidden=account;$('source-url').required=!account;
 $('source-hint').textContent=type==='seit-account'?'填写微信收件台里显示的社团公众号名称，已接收的文章会归入「社团与团学」。':account?'填写微信收件台里显示的公众号名称。只汇总本机已接收的文章。':type==='bookmark'?'网页保存到「校园服务入口」，点击后在浏览器打开。':'填写网站提供的公开 RSS 或 Atom 地址，添加后可立即更新。';
}
$('source-type').onchange=updateSourceForm;updateSourceForm();
$('source-form').onsubmit=async event=>{event.preventDefault();const type=$('source-type').value;if(await changeSource('addSource',type,$('source-name').value,type.endsWith('account')?'':$('source-url').value)){$('source-name').value='';$('source-url').value='';if(type==='bookmark'&&$('entries').hidden)$('entry-toggle').click();}};
for(const id of ['search','channel','source-filter','sort','only-unread'])$(id).addEventListener(id==='search'?'input':'change',()=>{limit=60;render();});
$('mark-all').onclick=async()=>{try{state=await call('read',filtered().map(x=>x.id));render();}catch(e){notify(e.message);}};
$('more').onclick=()=>{limit+=60;render();};$('close-detail').onclick=()=>$('detail').close();$('detail').addEventListener('close',()=>{detailRequest++;});
const detailFeedback=message=>{$('detail-feedback').textContent=message;};
$('open-original').onclick=()=>selected&&linkAction('open',selected.url,detailFeedback);
$('copy-original').onclick=()=>selected&&linkAction('copy',selected.url,detailFeedback);
api.onUpdate(reload);setInterval(reload,30000);reload();
