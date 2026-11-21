const SITE='https://aihot.news';
const ARTICLE=/^\/items\/[a-z0-9]+\/?$/i;
const PREFETCH_LIMIT=2;
const RECENT_TTL=120000;

if(process.isMainFrame)window.addEventListener('DOMContentLoaded',()=>{
 if(location.origin!==SITE)return;
 const recent=new Map();
 let inFlight=0;
 let initialTimer;
 let initialPath;

 function prefetch(link){
  if(!link||inFlight>=PREFETCH_LIMIT||location.origin!==SITE)return;
  const address=new URL(link.href,location.href);
  if(address.origin!==SITE||!ARTICLE.test(address.pathname)||address.pathname===location.pathname)return;
  const item=address.pathname.replace(/\/$/,'');
  const now=Date.now();
  if((recent.get(item)??0)>now-RECENT_TTL)return;
  if(recent.size>=24)recent.delete(recent.keys().next().value);
  recent.delete(item);
  recent.set(item,now);
  inFlight++;
  const data=new URL(item+'.data',SITE);
  data.searchParams.set('_routes','routes/item');
  fetch(data,{credentials:'same-origin',priority:'low',redirect:'error',signal:AbortSignal.timeout(2500)})
   .then(response=>{if(!response.ok)throw new Error('Prefetch unavailable');return response.arrayBuffer();})
   .catch(()=>recent.delete(item))
   .finally(()=>inFlight--);
 }

 const warmedSources=new Set();
 function prepareOriginal(link){
  if(!link?.matches('main a[target="_blank"]')||!ARTICLE.test(location.pathname))return;
  const address=new URL(link.href,location.href);
  if(address.protocol!=='https:'||address.port||address.username||address.password||address.origin===SITE||
   !/^[a-z\d-]+(?:\.[a-z\d-]+)*\.[a-z]{2,63}$/i.test(address.hostname)||
   /\.(?:localhost|local|internal|lan|home|test|invalid)$/i.test(address.hostname)||warmedSources.size>=8||warmedSources.has(address.origin))return;
  warmedSources.add(address.origin);
  const hint=document.createElement('link');
  hint.rel='preconnect';hint.href=address.origin;
  document.head.append(hint);
 }

 function intent(event){
  const link=event.target?.closest?.('a[href]');
  if(!link||link.contains(event.relatedTarget))return;
  prefetch(link);
  prepareOriginal(link);
 }

 function prefetchVisible(){
  if(document.hidden||ARTICLE.test(location.pathname)||initialPath===location.pathname)return;
  initialPath=location.pathname;
  clearTimeout(initialTimer);
  initialTimer=setTimeout(()=>{
   if(document.hidden||initialPath!==location.pathname)return;
   let count=0;
   for(const link of document.querySelectorAll('main a[href^="/items/"]')){
    const rect=link.getBoundingClientRect();
    if(rect.width&&rect.height&&rect.top<innerHeight&&rect.bottom>0){
     prefetch(link);
     if(++count>=PREFETCH_LIMIT)break;
    }
   }
  },220);
 }

 document.addEventListener('pointerover',intent,{passive:true});
 document.addEventListener('focusin',intent);
 document.addEventListener('visibilitychange',prefetchVisible);
 window.addEventListener('pageshow',prefetchVisible);
 prefetchVisible();
},{once:true});
