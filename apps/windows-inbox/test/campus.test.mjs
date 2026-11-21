import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {CampusMonitor,parseListing,allowed,SOURCES} from '../backend/campus.mjs';
import {publicIPv4,fetchCustomFeed} from '../backend/campus-sources.mjs';
const source={id:'test',name:'学院',url:'https://seit.sysu.edu.cn/admission/graduate',category:'招生升学',priority:0,mode:'list'};
const listing=(title='2027年推免报名通知')=>`<li><time datetime="2026-11-18T08:00:00+08:00">11-18</time><a href="/article/123">${title}</a></li>`;
const html=body=>new Response(body,{headers:{'content-type':'text/html'}});
function monitor(t,options={}){const directory=fs.mkdtempSync(path.join(os.tmpdir(),'campus-test-'));t.after(()=>{for(const name of fs.readdirSync(directory))fs.unlinkSync(path.join(directory,name));fs.rmdirSync(directory);});return new CampusMonitor({directory,sources:[source],...options});}

test('解析招生日期、标签、官网推荐公众号；排除非官方与脚本链接',()=>{
 const items=parseListing(listing()+`<li><time datetime="2026-11-19T08:00:00Z"></time><a href="https://mp.weixin.qq.com/s/abc">近红外芯片学术报告会</a></li><a href="javascript:alert(1)">恶意链接测试内容</a><a href="https://evil.example/article/1">非官方链接测试内容</a>`,source);
 assert.equal(items.length,2);assert.equal(items[0].date,'2026-11-18');assert(items[0].tags.includes('推免'));assert.equal(items[1].channel,'公众号（官网推荐）');assert(!allowed('https://seit.sysu.edu.cn.evil.example/'));assert(!allowed('https://user@seit.sysu.edu.cn/'));
});
test('合并重复来源，保留日期与已读状态，标题变化重新提醒，故障保留历史并显示错误',async t=>{
 let body=listing(),fail=false,calls=0;
 const m=monitor(t,{sources:[source,{...source,id:'duplicate'}],fetcher:async()=>{calls++;if(fail)throw Error('离线');return html(body);}});
 const one=m.refresh(),two=m.refresh();assert.equal(one,two);await one;assert.equal(calls,2);assert.equal(m.data.items.length,1);assert.equal(m.data.items[0].sourceIds.length,2);
 const id=m.data.items[0].id;m.markRead([id]);await m.refresh();assert.equal(m.data.items[0].read,true);
 body=listing('2027年推免报名通知（时间调整）');await m.refresh();assert.equal(m.data.items[0].read,false);assert.equal(m.data.items[0].revision,2);
 fail=true;await m.refresh();assert.equal(m.data.items.length,1);assert.match(m.snapshot().sources[0].error,/离线/);assert(m.snapshot().sources[0].lastSuccessAt);
 const restored=new CampusMonitor({directory:path.dirname(m.file),sources:[]});assert.equal(restored.data.items[0].revision,2);
});
test('图书馆功能正文变化会产生新版本；读取详情不会覆盖未读变更',async t=>{
 let body='<main><article>校外访问说明：通过统一认证使用数据库。当前版本需要校内账号登录。</article></main>';
 const m=monitor(t,{sources:[{...source,mode:'page',category:'图书馆'}],fetcher:async()=>html(body)});await m.refresh();let item=m.data.items[0];m.markRead([item.id]);body=body.replace('当前版本','新增功能版本');await m.refresh();assert.equal(item.revision,2);assert.equal(item.read,false);assert.match(item.detail,/新增功能版本/);
});
test('已关注公众号的多图文与普通链接均可汇总；同名个人会话不收录',async t=>{
 const official={Platform:'wechat',ConversationType:'official',ConversationName:'[微信] 中大科研',CreateTime:1794960000};
 const m=monitor(t,{sources:[],readWeChat:()=>[
  {...official,Content:'新的科研项目申报通知',Articles:[{title:'新的科研项目申报通知',url:'https://mp.weixin.qq.com/s/test',description:'项目申报摘要'},{title:'学术报告日期变更',url:'https://mp.weixin.qq.com/s/second'}]},
  {...official,Content:'[链接] 单条科研通知\nhttps://mp.weixin.qq.com/s/single'},
  {...official,ConversationType:'person',Content:'[链接] 同名会话通知\nhttps://mp.weixin.qq.com/s/other'},
  {...official,ConversationName:'其他公众号',Content:'[链接] 未关注的通知\nhttps://mp.weixin.qq.com/s/third'},
 ],fetcher:async()=>new Response(null,{status:302,headers:{location:'https://evil.example/'}})});
 await m.refresh();assert.equal(m.data.wechat.count,3);assert.equal(m.data.items.find(item=>item.url.endsWith('/test')).summary,'项目申报摘要');
 await assert.rejects(m.getHtml(source.url),/非官方/);
});
test('公开订阅可新增、暂停、恢复与移除，并保留条目和浏览器链接',async t=>{
 const calls=[];
 const m=monitor(t,{sources:[],customFetcher:async url=>{calls.push(url);return new Response(`<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>信号处理报告</title><updated>2026-11-18T15:00:00Z</updated><link rel="alternate" href="/articles/1#top" /><summary>报告摘要</summary></entry><entry><title>外部站点</title><link href="https://other.example/offer" /></entry></feed>`,{headers:{'content-type':'application/atom+xml'}});}});
 const address='https://public.example/feed.xml';
 const added=m.addSource('feed','自定义订阅',address);const id=added.sources[0].id;
 await m.refresh();assert.deepEqual(calls,[address]);assert.equal(m.data.items.length,1);
 const item=m.data.items[0];assert.equal(item.channel,'Atom');assert.equal((await m.detail(item.id)).summary,'报告摘要');assert.equal(m.openAddress(item.url),'https://public.example/articles/1');
 await m.refresh(false);assert.equal(calls.length,1);
 m.setSource(id,false);await m.refresh();assert.equal(calls.length,1);
 m.setSource(id,true);await m.refresh();assert.equal(calls.length,2);m.removeSource(id);assert.equal(m.snapshot().sources.length,0);assert.equal(m.data.items.length,1);
 m.addSource('bookmark','已授权校内目录','https://files.campus.internal/folder#docs');assert.equal(m.openAddress('https://files.campus.internal/folder#docs'),'https://files.campus.internal/folder#docs');
 assert.throws(()=>m.openAddress('https://random.example/'),/不在/);
 assert.throws(()=>m.addSource('feed','受保护订阅','https://public.example/rss?access_token=placeholder'),/凭据/);
});
test('任意公开订阅不能请求本机或校内地址，跨站跳转也不会跟随',async t=>{
 for(const address of ['127.0.0.1','10.1.0.1','172.16.0.1','192.168.1.1','169.254.169.254','100.64.0.1','::1'])assert.equal(publicIPv4(address),false);
 await assert.rejects(fetchCustomFeed('https://127.0.0.1/feed',{signal:AbortSignal.timeout(1000),headers:{}}),/内网地址/);
 const calls=[];const m=monitor(t,{sources:[],customFetcher:async url=>{calls.push(url);return new Response(null,{status:302,headers:{location:'https://other.example/rss'}});}});
 m.addSource('feed','跳转订阅','https://public.example/rss');await m.refresh();assert.deepEqual(calls,['https://public.example/rss']);assert.match(m.snapshot().sources[0].error,/不同站点/);
});

test('学院栏目和社团归类可重叠，能区分空栏目及抓取失败',async t=>{
 const sources=SOURCES.filter(source=>['seit-news','seit-org-xsh','seit-student-style','seit-career-notices'].includes(source.id));
 const monitorInstance=monitor(t,{sources,fetcher:async url=>{
  if(url.endsWith('/student/style'))return html('<article><div data-block-plugin-id="block_content:ce571c56-9c2e-467e-88a8-9823419de643" class="block"> </div></article>');
  if(url.endsWith('/career/notice'))return html('<article>服务异常，请稍后访问。</article>');
  return html('<a href="/article/100">其他栏目不应收录</a><article>'+listing('电子与信息工程学院学生会招新公告')+'</article>');
 }});
 await monitorInstance.refresh();
 assert.equal(monitorInstance.data.items.length,1);
 const [item]=monitorInstance.data.items;
 assert(item.tags.includes('学院官方')&&item.tags.includes('社团与团学'));
 assert(item.sourceIds.includes('seit-org-xsh'));
 assert.equal(monitorInstance.data.statuses['seit-student-style'].count,0);
 assert.match(monitorInstance.data.statuses['seit-career-notices'].error,/未识别/);
});

test('学院公众号别名和自定义社团号归入对应栏目，不收录普通会话',async t=>{
 const message=(name,slug,kind='official')=>({Platform:'wechat',ConversationType:kind,ConversationName:name,Articles:[{title:'本周公开活动通知',url:'https://mp.weixin.qq.com/s/'+slug}]});
 const monitorInstance=monitor(t,{sources:[],readWeChat:()=>[
  message('[微信] 中山大学电子与信息工程学院（微电子学院）','official'),message('[微信] 中大seit学生园地','students'),
  message('[微信] 测试社团号','custom'),message('[微信] 测试社团号','private','person'),
 ]});
 monitorInstance.addSource('seit-account','测试社团号');
 await monitorInstance.refresh();
 const bySlug=Object.fromEntries(monitorInstance.data.items.map(item=>[item.url.split('/').at(-1),item]));
 assert(bySlug.official.tags.includes('学院官方'));
 assert(bySlug.students.tags.includes('社团与团学')&&bySlug.custom.tags.includes('社团与团学'));
 assert.equal(bySlug.custom.priority,0);
 assert(!bySlug.private);
});
