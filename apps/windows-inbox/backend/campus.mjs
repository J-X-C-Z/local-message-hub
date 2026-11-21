import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {EXTRA_SOURCES,ENTRIES,httpsAddress,parsePublicFeed,fetchCustomFeed} from './campus-sources.mjs';
import {SEIT_ENTRIES,SEIT_SOURCES,SEIT_OFFICIAL,SEIT_STUDENTS,isEmptySeitListing} from './campus-seit.mjs';
export const SOURCES=[
 ['seit-admission','学院 · 硕士招生','https://seit.sysu.edu.cn/admission/graduate','招生升学',0],
 ['seit-notice','学院 · 公告栏','https://seit.sysu.edu.cn/Announcement','学院动态',0],
 ['seit-research','学院 · 科研进展','https://seit.sysu.edu.cn/research/progress','科研成果',0],
 ['seit-home','学院 · 新闻与学术活动','https://seit.sysu.edu.cn/','学院动态',0],
 ['graduate','中大 · 研究生招生','https://graduate.sysu.edu.cn/zsw/','招生升学',1],
 ['research-notice','中大 · 科研项目申报','https://research.sysu.edu.cn/cat/111','项目申报',1],
 ['research-events','中大 · 学术预告','https://research.sysu.edu.cn/academic-events','学术会议',1],
 ['library','图书馆 · 公告与资源上新','https://library.sysu.edu.cn/','图书馆',1],
 ['library-events','图书馆 · 培训讲座','https://library.sysu.edu.cn/events','图书馆',1],
 ['library-access','图书馆 · 校外访问','https://library.sysu.edu.cn/vpn-visit','图书馆',1,'page'],
 ['library-search','图书馆 · 数据库与试用','https://library.sysu.edu.cn/page/3640?title=&eresource_sel5%5B0%5D=1','图书馆',1,'page'],
 ['library-citation','图书馆 · 查收查引','https://library.sysu.edu.cn/basic/3752','图书馆',1,'page'],
 ['library-novelty','图书馆 · 科技查新','https://library.sysu.edu.cn/basic/3732','图书馆',1,'page'],
 ['jwb','中大 · 专业与教学调整','https://jwb.sysu.edu.cn/','教学动态',2],
].map(([id,name,url,category,priority,mode='list'])=>({id,name,url,category,priority,mode,section:id.startsWith('seit-')?SEIT_OFFICIAL:undefined}));
SOURCES.splice(4,0,...SEIT_SOURCES);
SOURCES.push(...EXTRA_SOURCES);
export const ACCOUNTS=['中山大学电子与信息工程学院','中山大学研究生教育','中大科研','中山大学图书馆','中山大学教务部','中大SEIT学生园地','中山大学','中山大学信息技术服务帮助台'];
const hash=value=>createHash('sha256').update(value).digest('hex').slice(0,24);
export function wechatAccount(value){return String(value||'').replace(/^\[微信\]\s*/,'').normalize('NFKC').replace(/[\s()·]/g,'').toLowerCase();}
const seitAccount='中山大学电子与信息工程学院';
export function wechatKey(value){const name=wechatAccount(value);return name===seitAccount+'微电子学院'?seitAccount:name;}
const studentActivity=/招新|社团|学生会|研究生会|团学|团委|志愿服务|电子设计竞赛|学业加油站|学业与发展指导|第二课堂|体育文化节/;
export function plain(value=''){return String(value).replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'').replace(/<style\b[^>]*>[\s\S]*?<\/style>/gi,'').replace(/<[^>]+>/g,' ').replace(/&#(x[\da-f]+|\d+);/gi,(_,code)=>{const n=code[0].toLowerCase()==='x'?parseInt(code.slice(1),16):Number(code);return n>0&&n<=0x10ffff?String.fromCodePoint(n):'';}).replace(/&(nbsp|amp|lt|gt|quot|apos);/g,(_,key)=>({nbsp:' ',amp:'&',lt:'<',gt:'>',quot:'"',apos:"'"}[key])).replace(/\s+/g,' ').trim();}
export function allowed(value){try{const u=new URL(value);return u.protocol==='https:'&&!u.username&&!u.password&&(!u.port||u.port==='443')&&(u.hostname==='sysu.edu.cn'||u.hostname.endsWith('.sysu.edu.cn')||u.hostname==='mp.weixin.qq.com');}catch{return false;}}
function attr(tag,name){return tag.match(new RegExp('\\b'+name+'\\s*=\\s*["\x27]([^"\x27]*)["\x27]','i'))?.[1]||'';}
export function classify(title,source){const tags=new Set([source.section]);if(source.section&&studentActivity.test(title))tags.add(SEIT_STUDENTS);for(const [tag,re] of [['专业调整',/专业.*(?:调整|新增|撤销|停招|目录)|考试科目|参考书目|培养方案/],['夏令营',/夏令营|开放日/],['推免',/推免|推荐免试|免试攻读|预报名|直博/],['考研',/硕士|研究生招生|复试|初试|考研/],['调剂',/调剂|缺额/],['项目申报',/申报|申请指南|科技计划|基金.*(?:指南|通告)|征集/],['科研成果',/科研进展|科研动态|科研成果|发表|获奖|突破|课题组|教授团队/],['学术会议',/学术(?:活动|报告|会议|预告|年会)|论坛|会议|研讨|讲座|报告会|培训|征稿/],['图书馆',/数据库|图书馆|校外访问|CARSI|查收查引|查新|文献|试用/]])if(re.test(title))tags.add(tag);tags.add(source.category);return [...tags].filter(Boolean);}
export function pageContent(html){const body=html.match(/<article\b[^>]*>([\s\S]*?)<\/article>/i)?.[1]||html.match(/<main\b[^>]*>([\s\S]*?)<\/main>/i)?.[1]||html.match(/<div\b[^>]*class=["'][^"']*(?:field--name-body|node__content|region-content)[^"']*["'][^>]*>([\s\S]*)/i)?.[1]||'';return plain(body.replace(/<(?:nav|footer|header)\b[^>]*>[\s\S]*?<\/(?:nav|footer|header)>/gi,'')).slice(0,30000);}
function sourcePageContent(html,source){return source?.id==='seit-student-rules'?plain(html.match(/<div class="block-region-right">([\s\S]*?)<\/article>/i)?.[1]||'').slice(0,30000):pageContent(html);}
export function parseListing(html,source){
 if(source.section&&source.id!=='seit-home')html=html.match(/<article\b[^>]*>([\s\S]*?)<\/article>/i)?.[1]||html;
 const items=new Map(),blocks=[...html.matchAll(/<(li|tr)\b[^>]*>[\s\S]*?<\/\1>/gi)].map(m=>m[0]);
 function parse(block,dated){for(const m of block.matchAll(/<a\b([^>]*)>([\s\S]*?)<\/a>/gi)){
  let url;try{url=new URL(plain(attr(m[1],'href')),source.url);url.hash='';}catch{continue;}
  const wechat=url.hostname==='mp.weixin.qq.com';
  if(!allowed(url.href)||(!wechat&&url.hostname!==new URL(source.url).hostname)||(!wechat&&!/(?:\/(?:article|event|news|notice|node)\/\d+|\/content\/|\/info\/[^/]+\/\d+\.htm)/i.test(url.pathname)))continue;
  const title=plain(attr(m[1],'title')||m[2].replace(/<time\b[^>]*>[\s\S]*?<\/time>/gi,'')).replace(/^20\d{2}[-/]\d{1,2}[-/]\d{1,2}\s*/,'');if(title.length<7||title.length>400||/^(借阅制度|逾期与遗失|联系方式)$/.test(title))continue;
  const rawDate=dated?(attr(block.match(/<time\b([^>]*)>/i)?.[1]||'','datetime')||plain(block).match(/20\d{2}[-/]\d{1,2}[-/]\d{1,2}/)?.[0]):'';
  const date=rawDate?rawDate.slice(0,10).replaceAll('/','-').split('-').map((v,i)=>i?v.padStart(2,'0'):v).join('-'):'';
  const old=items.get(url.href);if(!old||(!old.date&&date))items.set(url.href,{id:hash(url.href),url:url.href,title,date:date||'',tags:classify(title,source),sourceIds:[source.id],sourceName:source.name,priority:source.priority,channel:wechat?'公众号（官网推荐）':'官网',dateLabel:source.id==='research-events'||/^学术(?:活动|报告)/.test(title)?'活动日期':'发布日期'});
 }}
 for(const block of blocks)parse(block,true);
 for(const block of html.split(/<div class=["']views-row["']>/i).slice(1))parse(block,true);parse(html,false);return [...items.values()].slice(0,100);
}
export class CampusMonitor{
 constructor({directory,fetcher=fetch,customFetcher=fetchCustomFeed,readWeChat=()=>[],intervalMs=600000,sources=SOURCES,onUpdate=()=>{}}){
  Object.assign(this,{file:path.join(directory,'campus-feed.json'),fetcher,customFetcher,readWeChat,intervalMs,sources,onUpdate,busy:null,stopped:false,controllers:new Set()});this.data={version:1,items:[],statuses:{},lastCompletedAt:null};
  if(fs.existsSync(this.file)){try{const d=JSON.parse(fs.readFileSync(this.file,'utf8'));if(d.version!==1||!Array.isArray(d.items)||!d.statuses)throw new Error('缓存格式无效');this.data=d;}catch{this.loadError='历史缓存损坏，已重新获取；原文件保留为 .damaged';fs.copyFileSync(this.file,this.file+'.damaged');}}
  this.data.preferences??={paused:[],custom:[],accounts:[]};
  this.data.preferences.studentAccounts??=[];
 }
 start(){this.refresh(false);this.timer=setInterval(()=>this.refresh(false),this.intervalMs);this.timer.unref?.();}
 stop(){this.stopped=true;clearInterval(this.timer);for(const c of this.controllers)c.abort();}
 allSources(){return [...this.sources,...this.data.preferences.custom.filter(source=>source.mode==='feed')];}
 accounts(){return [...ACCOUNTS,...this.data.preferences.accounts];}
 snapshot(){return {...this.data,refreshing:!!this.busy,intervalMs:this.intervalMs,loadError:this.loadError||'',sources:this.allSources().map(s=>({...s,...this.data.statuses[s.id],paused:this.data.preferences.paused.includes(s.id)})),entries:[...ENTRIES,...this.data.preferences.custom.filter(source=>source.mode==='bookmark')],organizations:SEIT_ENTRIES,accounts:this.accounts()};}
 persist(){fs.mkdirSync(path.dirname(this.file),{recursive:true});fs.writeFileSync(this.file+'.tmp',JSON.stringify(this.data));fs.renameSync(this.file+'.tmp',this.file);}
 setSource(id,enabled){
  if(typeof enabled!=='boolean'||!this.allSources().some(source=>source.id===id))throw new Error('无效来源');
  const paused=new Set(this.data.preferences.paused);if(enabled)paused.delete(id);else paused.add(id);this.data.preferences.paused=[...paused];this.persist();return this.snapshot();
 }
 addSource(type,name,value){
  if(typeof name!=='string'||!(name=name.trim())||name.length>80)throw new Error('请输入不超过 80 字的名称');
  const preferences=this.data.preferences;
  if(type==='account'||type==='seit-account'){
   const key=wechatKey(name);if(!key)throw new Error('请输入有效的公众号名称');
   if(ACCOUNTS.some(account=>wechatKey(account)===key))throw new Error('该公众号已内置');
   const existing=preferences.accounts.find(account=>wechatKey(account)===key);
   if(existing&&(type==='account'||preferences.studentAccounts.includes(existing)))throw new Error('已关注此公众号');
   if(!existing){if(preferences.accounts.length>=30)throw new Error('自定义公众号已达上限');preferences.accounts.push(name);}
   if(type==='seit-account')preferences.studentAccounts.push(existing||name);
  }else{
   if(!['feed','bookmark'].includes(type))throw new Error('不支持的来源类型');
   const address=httpsAddress(value);if(!address)throw new Error('请输入不含账号密码的 HTTPS 链接');
   if(type==='feed'){
    address.hash='';
   }
   if([...address.searchParams.keys()].some(key=>/token|password|secret|signature|auth|session|api.?key|cookie|credential|^code$/i.test(key)))throw new Error('地址请勿携带登录凭据');
   if([...this.sources,...preferences.custom].some(source=>source.url===address.href&&source.mode===type))throw new Error('已添加此来源');
   if(preferences.custom.length>=30)throw new Error('自定义链接已达上限');
   preferences.custom.push({id:'custom-'+hash(type+address.href),name,url:address.href,kind:'自定义网页',note:'在默认浏览器打开；访问权限由网站管理。',category:'自定义订阅',mode:type,priority:2,intervalMs:1800000,custom:true});
  }
  this.persist();return this.snapshot();
 }
 removeSource(id){
  const preferences=this.data.preferences;
  if(!preferences.custom.some(source=>source.id===id)&&!preferences.accounts.some(account=>'account:'+account===id))throw new Error('只能移除自定义来源');
  preferences.custom=preferences.custom.filter(source=>source.id!==id);
  preferences.accounts=preferences.accounts.filter(account=>'account:'+account!==id);
  preferences.studentAccounts=preferences.studentAccounts.filter(account=>'account:'+account!==id);
  preferences.paused=preferences.paused.filter(source=>source!==id);
  if(typeof id==='string'&&/^custom-[a-f0-9]{24}$/.test(id))delete this.data.statuses[id];
  this.persist();return this.snapshot();
 }
 openAddress(url){
  const address=httpsAddress(url);if(!address)throw new Error('只能打开 HTTPS 页面');
  if(allowed(address.href)||this.allSources().some(source=>source.url===address.href)||this.data.preferences.custom.some(source=>source.url===address.href)||this.data.items.some(item=>item.url===address.href))return address.href;
  throw new Error('链接不在资讯或信源中');
 }
 async getHtml(url,source){
  const feed=source?.mode==='feed';
  const custom=feed&&source.custom;
  const base=feed?httpsAddress(source.url):null;
  const check=address=>feed?Boolean(base&&httpsAddress(address)?.origin===base.origin):allowed(address);
  if(!check(url))throw new Error('非官方来源');const controller=new AbortController();this.controllers.add(controller);const timer=setTimeout(()=>controller.abort(),20000);
  try{let current=url;for(let step=0;step<5;step++){
   const response=await (custom?this.customFetcher:this.fetcher)(current,{signal:controller.signal,redirect:'manual',headers:{'User-Agent':'SYSU-CampusInbox/2.0','Accept':feed?'application/atom+xml, application/rss+xml, application/xml, text/xml':'text/html'}});
   if([301,302,303,307,308].includes(response.status)){await response.body?.cancel();const location=response.headers.get('location');const next=location&&new URL(location,current).href;if(!next||!check(next))throw new Error('来源跳转至非官方地址或不同站点');current=next;continue;}
   const mime=response.headers.get('content-type')||'';
   if(!response.ok||!response.body||(!feed&&!/text\/html|application\/xhtml/i.test(mime))){await response.body?.cancel();throw new Error(!response.ok?'HTTP '+response.status:'来源未返回网页');}
   const reader=response.body.getReader();let size=0,chunks=[];try{while(true){const {done,value}=await reader.read();if(done)break;size+=value.length;if(size>3*1024*1024)throw new Error('页面超出大小限制');chunks.push(value);}}finally{await reader.cancel().catch(()=>{});}return new TextDecoder().decode(Buffer.concat(chunks));
  }throw new Error('页面重定向过多');}finally{clearTimeout(timer);this.controllers.delete(controller);}
 }
 merge(item){const old=this.data.items.find(x=>x.id===item.id),now=new Date().toISOString();if(old){
  const primary=old.sourceIds[0]===item.sourceIds[0]||item.priority<old.priority;
  const changed=(primary&&old.title!==item.title)||(item.contentHash&&old.contentHash&&old.contentHash!==item.contentHash),sourceIds=[...new Set(primary?[...item.sourceIds,...old.sourceIds]:[...old.sourceIds,...item.sourceIds])],tags=[...new Set([...old.tags,...item.tags])],priority=Math.min(old.priority,item.priority);
  if(primary){Object.assign(old,item,{date:item.date||old.date,dateLabel:item.date?(item.dateLabel||'发布日期'):old.dateLabel,summary:item.summary||old.summary});}else{
   if(!old.summary&&item.summary)old.summary=item.summary;
   if(!old.date&&item.date){old.date=item.date;old.dateLabel=item.dateLabel||'发布日期';}
  }
  Object.assign(old,{sourceIds,tags,priority,lastSeenAt:now});if(changed){old.updatedAt=now;old.revision=(old.revision||1)+1;old.read=false;}return old;
 }const created={...item,firstSeenAt:now,lastSeenAt:now,updatedAt:now,read:false,revision:1};this.data.items.push(created);return created;}
 refresh(force=true){if(this.busy)return this.busy;if(this.stopped)return Promise.resolve();this.busy=this.perform(force).catch(error=>{this.loadError='更新未完成：'+error.message;}).finally(()=>{this.busy=null;this.onUpdate(this.snapshot());});return this.busy;}
 async perform(force){const queue=this.allSources().filter(source=>{const lastCheck=Date.parse(this.data.statuses[source.id]?.checkedAt);return !this.data.preferences.paused.includes(source.id)&&(force||!Number.isFinite(lastCheck)||Date.now()-lastCheck>=(source.intervalMs||this.intervalMs));});await Promise.all(Array.from({length:3},async()=>{while(queue.length&&!this.stopped){const source=queue.shift(),before=this.data.statuses[source.id]||{},checkedAt=new Date().toISOString();
  try{const html=await this.getHtml(source.url,source);let items;if(source.mode==='page'){const content=sourcePageContent(html,source);if(content.length<30)throw new Error('未识别到功能说明正文');const title=source.section?source.name:source.name+' · 功能说明';items=[{id:hash(source.url),url:source.url,title,date:'',tags:classify(title,source),sourceIds:[source.id],sourceName:source.name,priority:source.priority,channel:'官网',summary:content.slice(0,360),contentHash:hash(content),detail:content,detailFetchedAt:checkedAt}];}else if(source.mode==='feed'){items=parsePublicFeed(html,source,plain).map(entry=>({...entry,id:hash(entry.url),tags:classify(entry.title,source),sourceIds:[source.id],sourceName:source.name,priority:source.priority,channel:entry.format}));}else{items=parseListing(html,source);if(!items.length&&!(source.allowEmpty&&isEmptySeitListing(html)))throw new Error('未识别到动态列表，请检查页面结构');}
   if(this.stopped||!this.allSources().some(active=>active.id===source.id)||this.data.preferences.paused.includes(source.id))continue;
   for(const item of items)this.merge(item);this.data.statuses[source.id]={checkedAt,lastSuccessAt:checkedAt,error:'',count:items.length};
  }catch(error){if(this.allSources().some(active=>active.id===source.id))this.data.statuses[source.id]={...before,checkedAt,error:error.name==='AbortError'?'请求超时或已停止':error.message};}
 }}));if(this.stopped)return;
 try{const names=this.accounts();const keys=new Set(names.map(wechatKey));const studentKeys=new Set(['中大SEIT学生园地',...this.data.preferences.studentAccounts].map(wechatKey));const rows=await this.readWeChat(names);const seen=new Set();for(const row of rows){const message=row.message||row,name=String(message.ConversationName||'').replace(/^\[微信\]\s*/,''),content=String(message.Content||'');
  const key=wechatKey(name);if(message.Platform!=='wechat'||!['official','service'].includes(message.ConversationType)||!keys.has(key))continue;
  const links=Array.isArray(message.Articles)&&message.Articles.length?message.Articles.slice(0,20):[...content.matchAll(/https:\/\/mp\.weixin\.qq\.com\/[^\s<>]+/g)].slice(0,20).map(match=>({url:match[0],title:content.split('\n')[0].replace(/^\[[^\]]+\]\s*/,''),description:''}));
  for(const article of links){const address=httpsAddress(String(article?.url||'').replaceAll('&amp;','&'));if(address?.hostname!=='mp.weixin.qq.com'||!/^\/s(?:\/|$)/.test(address.pathname))continue;address.hash='';const url=address.href,title=plain(article.title).slice(0,300);if(!title||seen.has(url))continue;
   const section=key===seitAccount?SEIT_OFFICIAL:studentKeys.has(key)?SEIT_STUDENTS:undefined;
   const source={name,section,category:/图书馆/.test(name)?'图书馆':/科研/.test(name)?'科研成果':section===SEIT_STUDENTS?'学生活动':'学院动态',priority:section?0:1},published=Number(message.CreateTime)*1000;
   this.merge({id:hash(url),url,title,date:Number.isFinite(published)&&published>0&&published<8640000000000000?new Date(published).toLocaleDateString('sv-SE',{timeZone:'Asia/Shanghai'}):'',dateLabel:'微信接收日期',tags:classify(title,source),sourceIds:['wechat'],sourceName:name,priority:source.priority,channel:'公众号（本机已接收）',summary:plain(article.description).slice(0,600)});seen.add(url);}
 }const count=seen.size;this.data.wechat={checkedAt:new Date().toISOString(),count,error:'',note:count?'从本机微信已接收的公众号文章汇总 '+count+' 条。':'暂未发现匹配的本机公众号文章；已接收并缓存后才会出现。'};
 }catch(error){this.data.wechat={checkedAt:new Date().toISOString(),error:error.message,count:0};}
 this.data.lastCompletedAt=new Date().toISOString();this.data.items.sort((a,b)=>b.updatedAt.localeCompare(a.updatedAt));this.data.items=this.data.items.slice(0,2500);this.persist();
 }
 markRead(ids){if(!Array.isArray(ids)||ids.length>2500)throw new Error('标记参数无效');for(const item of this.data.items)if(ids.includes(item.id))item.read=true;this.persist();return this.snapshot();}
 async detail(id){const item=this.data.items.find(x=>x.id===id);if(!item)throw new Error('资讯不存在');if(item.channel==='官网'&&(!item.detailFetchedAt||Date.now()-Date.parse(item.detailFetchedAt)>3600000)){const html=await this.getHtml(item.url),text=sourcePageContent(html,this.sources.find(source=>source.id===item.sourceIds[0]));if(text.length<20)throw new Error('正文提取失败，请查看原文');this.merge({...item,detail:text,summary:text.slice(0,360),contentHash:hash(text),detailFetchedAt:new Date().toISOString()});this.persist();}return item;}
}
