const path=require('node:path');
const {readFile}=require('node:fs/promises');
const UI_ORIGIN='wecom-inbox://app';
const assets=new Set(['index.html','style.css','inbox-layout.css','app.js','pane-resizer.js','view-performance.mjs','attachments-ui.js','integration-ui.js','integration.css']);
assets.add('rich-ui.js');assets.add('emoji-map.js');
const mime={'.html':'text/html; charset=utf-8','.css':'text/css; charset=utf-8','.js':'text/javascript; charset=utf-8','.mjs':'text/javascript; charset=utf-8','.png':'image/png'};
function createWorkbenchHandler({directory,backendReady,accessToken,fetcher}){
 const cache=new Map();
 return async request=>{
  const error=(status,message)=>new Response(JSON.stringify({error:message}),{status,headers:{'Content-Type':'application/json','Cache-Control':'no-store'}});
  let url;try{url=new URL(request.url);}catch{return error(400,'地址无效');}
  if(url.protocol!=='wecom-inbox:'||url.host!=='app'||url.username||url.password)return error(403,'仅允许应用内部地址');
  if(request.initiatorOrigin&&request.initiatorOrigin!==UI_ORIGIN)return error(403,'不允许其他页面访问工作台');
  if(!['GET','HEAD'].includes(request.method))return error(405,'工作台只提供读取接口');
  try{
   if(url.pathname.startsWith('/api/')){
    const origin=await backendReady();
    if(!/^http:\/\/127\.0\.0\.1:\d+$/.test(origin))return error(503,'本机服务地址不可用');
    const headers={'X-Workbench-Key':accessToken};for(const name of ['last-event-id','range','if-range']){const value=request.headers.get(name);if(value)headers[name]=value;}
    return await fetcher(origin+url.pathname+url.search,{method:request.method,headers,signal:request.signal,redirect:'error'});
   }
   const name=url.pathname==='/'?'index.html':url.pathname.slice(1);if(!assets.has(name)&&!/^emoji\/[a-f0-9]{16}\.png$/.test(name))return error(404,'资源不存在');
   let content=cache.get(name);if(!content){content=await readFile(path.join(directory,name));cache.set(name,content);}
   return new Response(request.method==='HEAD'?null:content,{headers:{'Content-Type':mime[path.extname(name)],'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Content-Security-Policy':"default-src 'self'; style-src 'self'; img-src 'self' data:; script-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"}});
  }catch{return error(503,'本机消息服务暂时不可用，请通过连接设置重试');}
 };
}
module.exports={UI_ORIGIN,createWorkbenchHandler};
