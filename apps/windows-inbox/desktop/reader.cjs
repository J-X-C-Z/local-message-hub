const {desktopCapturer}=require('electron');
const {execFile}=require('node:child_process');
const {createHash}=require('node:crypto');
const {mkdirSync,writeFileSync,readFileSync,existsSync,renameSync,rmSync}=require('node:fs');
const path=require('node:path');

class DesktopReader{
 constructor(directory){this.directory=path.join(directory,'desktop-reader');mkdirSync(this.directory,{recursive:true});this.filename=path.join(this.directory,'history.json');this.records=[];this.timer=null;this.busy=false;this.interval=10;this.status='未开始';this.lastChecked=null;this.lastHash='';this.latestImage=null;
  try{if(existsSync(this.filename)){const saved=JSON.parse(readFileSync(this.filename,'utf8'));if(Array.isArray(saved))this.records=saved.slice(-100);}}catch{}
 }
 state(){return{enabled:Boolean(this.timer),busy:this.busy,interval:this.interval,status:this.status,lastChecked:this.lastChecked,records:this.records.slice().reverse(),image:this.latestImage};}
 start(seconds){if(![5,10,30,60].includes(seconds))throw new Error('读取间隔无效');this.stop();this.interval=seconds;this.status='等待读取';this.timer=setInterval(()=>this.read(),seconds*1000);this.read();return this.state();}
 stop(){clearInterval(this.timer);this.timer=null;this.status='已停止定时读取';return this.state();}
 async read(){
  if(this.busy)return this.state();this.busy=true;this.status='正在读取企业微信窗口';
  const sessionEnabled=Boolean(this.timer);
  try{
   const sources=await desktopCapturer.getSources({types:['window'],thumbnailSize:{width:1600,height:1200},fetchWindowIcons:false});
   const candidates=sources.filter(source=>/^(企业微信|WeCom)(?:\s|$|[-—])/i.test(source.name));
   if(candidates.length!==1){this.status=candidates.length?'存在多个企业微信窗口，请保留一个主窗口':'没有找到企业微信主窗口，请打开并登录';return this.state();}
   const source=candidates[0];
   if(source.thumbnail.isEmpty()||source.thumbnail.getSize().width<100){this.status='窗口无法读取，请恢复企业微信窗口';return this.state();}
   const image=source.thumbnail.toPNG();const hash=createHash('sha256').update(image).digest('hex');this.lastChecked=new Date().toISOString();
   if(hash===this.lastHash){this.status='已检查，画面未变化';return this.state();}
   const imagePath=path.join(this.directory,'pending-ocr.png');writeFileSync(imagePath,image,{mode:0o600});
   const ocr=await new Promise(resolve=>{
    const helper=path.join(__dirname,'OcrHelper.exe').replace('app.asar'+path.sep,'app.asar.unpacked'+path.sep);
    execFile(helper,[imagePath],{windowsHide:true,timeout:20000,maxBuffer:2*1024*1024,encoding:'utf8'},(error,stdout)=>{
     try{const result=JSON.parse(stdout.trim().replace(/^\uFEFF/,''));resolve(result);}catch{resolve({ok:false,error:'文字识别不可用，仍可查看窗口图像。'});}
    });
   });
   rmSync(imagePath,{force:true});
   if(/设备环境异常|安全验证|扫码登录|扫描二维码登录|手机.*扫码/.test(ocr.text||'')){
    this.stop();this.status='企业微信要求登录或安全验证，定时读取已暂停';this.latestImage=null;return this.state();
   }
   if(sessionEnabled&&!this.timer){this.status='读取已停止';return this.state();}
   if(!ocr.ok){this.stop();this.status='文字识别不可用，已暂停定时读取；请检查 Windows OCR 组件';return this.state();}
   writeFileSync(path.join(this.directory,'latest.png'),image,{mode:0o600});
   this.latestImage='data:image/png;base64,'+image.toString('base64');this.lastHash=hash;
   const text=typeof ocr.text==='string'?ocr.text:'';
   if(this.records.at(-1)?.text!==text||!text){
    this.records.push({id:Date.now().toString(),capturedAt:this.lastChecked,window:source.name,text,ocrAvailable:Boolean(ocr.ok),source:'desktop-window-ocr'});this.records=this.records.slice(-100);
    const temporary=this.filename+'.tmp';writeFileSync(temporary,JSON.stringify(this.records),{mode:0o600});renameSync(temporary,this.filename);
   }
   this.status=ocr.ok?'已读取当前可见窗口（文字识别可能有误）':ocr.error;
  }catch{this.status='无法读取窗口，请确认企业微信未最小化，且当前桌面未锁定';}
  finally{this.busy=false;}
  return this.state();
 }
}
module.exports={DesktopReader};
