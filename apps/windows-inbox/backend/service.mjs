import path from 'node:path';
import {createWorkbenchServer} from './wecom-workbench/server.mjs';
import {readIntegrationConfig,LiveIntegration} from './wecom-workbench/integration.mjs';
import {loadConfig} from './wecom-mcp/src/config.js';
import {EventStore} from './wecom-mcp/src/store.js';
import {createCallbackServer} from './wecom-mcp/src/callback.js';
import {startLocalReader} from './local-reader.mjs';
import {startWeChatReader} from './wechat-reader.mjs';

let workbench,receiver,store,heartbeat,prune,localReader,wechatReader,wechatWarmup;let initialized=false,stopping=false;
function send(value){if(process.connected)process.send(value);else if(process.env.WECOM_RUST_PIPE==='1')process.stdout.write(JSON.stringify(value)+'\n');}
async function listen(server,port,host){return new Promise((resolve,reject)=>{const onError=error=>reject(error);server.once('error',onError);server.listen(port,host,()=>{server.off('error',onError);resolve();});});}
async function stop(){
 if(stopping)return;stopping=true;clearInterval(heartbeat);clearInterval(prune);clearTimeout(wechatWarmup);
 await localReader?.stop();
 await wechatReader?.stop();
 if(store){try{store.setReceiverState('heartbeat_at',0);}catch{}}
 workbench?.closeStreams();
 await Promise.all([workbench,receiver].filter(s=>s?.listening).map(s=>new Promise(resolve=>{s.close(resolve);s.closeAllConnections();})));
 store?.close();process.exit(0);
}
async function receive(message){
 if(message?.type==='shutdown'){await stop();return;}
 if(message?.type!=='init'||initialized)return;initialized=true;
 try{
  const settings=message.settings;const directory=message.dataDirectory;
  if(typeof directory!=='string'||!path.isAbsolute(directory)||!/^[a-f0-9]{64}$/.test(message.accessToken||''))throw new Error('本地服务启动参数无效');
  const env={WECOM_ENABLE_WRITES:'false',WECOM_DB_PATH:path.join(directory,'messages.db'),WECOM_CORP_ID:settings?.corpId,WECOM_AGENT_ID:settings?.agentId,WECOM_CORP_SECRET:settings?.secret,WECOM_EXTERNAL_CONTACT_SECRET:settings?.externalSecret,WECOM_APP_CHAT_IDS:settings?.appChatIds,WECOM_CALLBACK_TOKEN:settings?.callbackToken,WECOM_CALLBACK_AES_KEY:settings?.callbackAesKey};
  let receiverError='';
  store=new EventStore(env.WECOM_DB_PATH,settings?.retentionDays||30);
  if(message.localReaderExecutable)localReader=startLocalReader({directory,executable:message.localReaderExecutable,store,days:settings?.retentionDays||30});
  let warmWeChat;
  if(message.wechatReaderExecutable){
   store.setReceiverState('source','local-database');
   store.setReceiverState('wechat_status','正在准备微信接收');
   warmWeChat=()=>{
    if(stopping||wechatReader)return;
    clearTimeout(wechatWarmup);
    try{wechatReader=startWeChatReader({directory,executable:message.wechatReaderExecutable,store,days:settings?.retentionDays||30});}
    catch{store.setReceiverState('wechat_error','无法启动微信读取器');}
   };
  }
  // The workbench can serve the last committed cache while the reader warms up.
  // Its first import and health update are delivered through the existing stream.
  if(settings&&!localReader){
   try{
    const config=loadConfig({...env,WECOM_CALLBACK_TOKEN:settings.callbackToken,WECOM_CALLBACK_AES_KEY:settings.callbackAesKey,WECOM_CALLBACK_PORT:String(settings.callbackPort),WECOM_CALLBACK_HOST:'127.0.0.1',WECOM_EVENT_RETENTION_DAYS:String(settings.retentionDays)},{receiveOnly:true});
    store.setReceiverState('heartbeat_at',0);
    receiver=createCallbackServer(config,store);
    await listen(receiver,config.callbackPort,'127.0.0.1');
    store.setReceiverState('started_at',Date.now());store.setReceiverState('heartbeat_at',Date.now());
    heartbeat=setInterval(()=>store.setReceiverState('heartbeat_at',Date.now()),5000).unref();
    prune=setInterval(()=>store.prune(),3600000).unref();
   }catch(error){receiverError=error.code==='EADDRINUSE'?'接收端口被占用，请修改端口或关闭其他接收器。':error.message;}
  }
  const scheduleWeChat=warmWeChat&&(()=>{
   if(stopping||wechatReader)return;
   clearTimeout(wechatWarmup);wechatWarmup=setTimeout(warmWeChat,150).unref();
  });
  workbench=createWorkbenchServer({dbPath:env.WECOM_DB_PATH,snapshotPath:path.join(directory,'client-snapshot.json'),port:0,retentionDays:settings?.retentionDays||30,integration:new LiveIntegration(readIntegrationConfig(env)),accessToken:message.accessToken,onPreview:scheduleWeChat});
  await listen(workbench,0,'127.0.0.1');
  if(warmWeChat)wechatWarmup=setTimeout(warmWeChat,1750).unref();
  send({type:'ready',url:`http://127.0.0.1:${workbench.address().port}`,receiverError,localMode:Boolean(localReader)});
 }catch(error){send({type:'fatal',error:error.message});await stop();}
}
process.on('message',receive);
if(process.env.WECOM_RUST_PIPE==='1'){
 const {startDesktopPipe}=require('./rust-pipe.cjs');
 startDesktopPipe({receive,send,stop});
}
process.on('disconnect',stop);process.on('SIGTERM',stop);process.on('SIGINT',stop);
