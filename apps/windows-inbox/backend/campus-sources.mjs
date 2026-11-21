import {XMLParser} from 'fast-xml-parser';
import {lookup} from 'node:dns/promises';
import {get} from 'node:https';
import {isIPv4} from 'node:net';
import {Readable} from 'node:stream';

export const EXTRA_SOURCES = [
 ['inc-help','中大 · 信息技术服务','https://inc.sysu.edu.cn/helpdesk','校园服务',2,'list'],
 ['graduate-policy','中大 · 研究生院通知','https://graduate.sysu.edu.cn/','招生升学',2,'list'],
 ['disclosure','中大 · 信息公开','https://xxgk.sysu.edu.cn/','校园服务',2,'list'],
 ['cse','中大 · 计算机学院','https://cse.sysu.edu.cn/','学院动态',2,'list'],
 ['sysu-news','中大 · 新闻网','https://www.sysu.edu.cn/news/','校园动态',2,'list'],
 ['library-faq','图书馆 · 常见问题','https://library.sysu.edu.cn/faq','图书馆',2,'page'],
 ['arxiv-sp','arXiv · 信号处理','https://export.arxiv.org/rss/eess.SP','研究前沿',2,'feed'],
 ['arxiv-it','arXiv · 信息论','https://export.arxiv.org/rss/cs.IT','研究前沿',2,'feed'],
 ['esp-idf','GitHub · ESP-IDF 发布','https://github.com/espressif/esp-idf/releases.atom','开发工具',2,'feed'],
 ['zephyr','GitHub · Zephyr 发布','https://github.com/zephyrproject-rtos/zephyr/releases.atom','开发工具',2,'feed'],
].map(([id,name,url,category,priority,mode])=>({id,name,url,category,priority,mode,intervalMs:mode==='feed'?3600000:1800000}));

export const ENTRIES = [
 {id:'portal',name:'中山大学统一门户',kind:'统一门户',url:'https://portal.sysu.edu.cn/',note:'在浏览器用自己的 NetID 登录，在新闻公告中查看学校文件。'},
 {id:'portal-help',name:'统一门户使用说明',kind:'统一门户',url:'https://inc.sysu.edu.cn/service/portal',note:'查询入口、通知和校内业务系统的使用方法。'},
 {id:'vpn',name:'中山大学 SVPN',kind:'校内网络',url:'https://inc.sysu.edu.cn/service/vpn',note:'校外访问受限时按官方指引接入；接入后仍需资源本身的权限。'},
 {id:'library-remote',name:'图书馆校外访问',kind:'校内网络',url:'https://library.sysu.edu.cn/vpn-visit',note:'按数据库的适用范围选择 CARSI 或学校认可的校外访问方式。'},
 {id:'library-card',name:'图书馆借阅激活与预约',kind:'公众号',url:'https://library.sysu.edu.cn/node/3725',note:'官方指引含二维码；公众号内可进入微服务和我的图书馆。'},
 {id:'campus-card',name:'中山大学校园卡',kind:'小程序',url:'https://inc.sysu.edu.cn/service/card',note:'打开官方指引，用微信扫一扫校园卡小程序二维码。'},
 {id:'mobile-portal',name:'中山大学企业微信工作台',kind:'移动门户',url:'https://inc.sysu.edu.cn/article/1190',note:'在企业微信切换到中山大学组织，进入工作台查看已授权服务。'},
];

export function httpsAddress(value){
 if(typeof value!=='string'||value.length>4096)return null;
 try{const address=new URL(value);return address.protocol==='https:'&&!address.username&&!address.password&&!address.port?address:null;}catch{return null;}
}

export function publicIPv4(address){
 if(!isIPv4(address))return false;
 const [first,second,third]=address.split('.').map(Number);
 return first>0&&first<224&&first!==10&&first!==127&&!(first===100&&second>=64&&second<=127)&&!(first===169&&second===254)&&!(first===172&&second>=16&&second<=31)&&!(first===192&&[0,168].includes(second))&&!(first===198&&[18,19].includes(second))&&!(first===198&&second===51&&third===100)&&!(first===203&&second===0&&third===113);
}

export async function fetchCustomFeed(url,{signal,headers}){
 const address=httpsAddress(url);
 if(!address||address.hostname.startsWith('[')||!address.hostname.includes('.')||/\.(?:localhost|local|internal|lan|home|onion)$/i.test(address.hostname))throw new Error('请输入公开的 HTTPS 订阅地址');
 const answers=await lookup(address.hostname,{family:4,all:true});
 if(!answers.length||answers.some(answer=>!publicIPv4(answer.address)))throw new Error('公开订阅不支持访问本机或内网地址');
 signal.throwIfAborted();
 const resolved=answers[0].address;
 return new Promise((resolve,reject)=>{
  get(address,{headers,signal,agent:false,lookup:(_hostname,options,callback)=>options.all?callback(null,[{address:resolved,family:4}]):callback(null,resolved,4)},response=>{
   const status=response.statusCode;
   const responseHeaders=Object.fromEntries(Object.entries(response.headers).filter(([,value])=>typeof value==='string'));
   if(status<200||status>=300||status===204||status===205){response.destroy();resolve(new Response(null,{status,headers:responseHeaders}));return;}
   resolve(new Response(Readable.toWeb(response),{status,headers:responseHeaders}));
  }).on('error',reject);
 });
}

function feedItemAddress(value,source){
 try{
  const address=httpsAddress(new URL(value,source.url).href);
  const host=new URL(source.url).hostname;
  if(!address||(address.hostname!==host&&!(host==='export.arxiv.org'&&address.hostname==='arxiv.org')))return null;
  address.hash='';
  return address.href;
 }catch{return null;}
}

const parser=new XMLParser({ignoreAttributes:false,removeNSPrefix:true,processEntities:false,parseTagValue:false,trimValues:true,maxNestedTags:48});
const list=value=>Array.isArray(value)?value:value?[value]:[];
const text=value=>typeof value==='string'?value:String(value?.['#text']||'');

export function parsePublicFeed(xml,source,plain){
 if(/<!\s*(?:DOCTYPE|ENTITY)\b/i.test(xml))throw new Error('订阅含不支持的 XML 声明');
 const parsed=parser.parse(xml);
 const format=parsed.feed?'Atom':parsed.rss?.channel||parsed.RDF?'RSS':'';
 if(!format)throw new Error('地址未返回 RSS 或 Atom 订阅');
 const entries=list(parsed.feed?.entry??parsed.rss?.channel?.item??parsed.RDF?.item);
 return entries.slice(0,60).flatMap(entry=>{
  const link=list(entry?.link).find(candidate=>typeof candidate==='string'||!candidate?.['@_rel']||candidate['@_rel']==='alternate');
  const rawUrl=typeof link==='string'?link:link?.['@_href']||text(link);
  const url=rawUrl&&feedItemAddress(rawUrl.replaceAll('&amp;','&'),source);
  const title=plain(text(entry?.title)).slice(0,300);
  if(!url||!title)return [];
  const timestamp=Date.parse(text(entry.published||entry.pubDate||entry.date||entry.updated));
  const summary=plain(text(entry.summary||entry.description||entry.content)).slice(0,3600);
  return [{url,title,summary,format,date:Number.isFinite(timestamp)?new Date(timestamp).toLocaleDateString('sv-SE',{timeZone:'Asia/Shanghai'}):''}];
 });
}
