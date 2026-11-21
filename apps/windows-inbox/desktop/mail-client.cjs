const http=require('node:http');
const {randomBytes,createHash,timingSafeEqual}=require('node:crypto');

const PROVIDERS={
 gmail:{auth:'https://accounts.google.com/o/oauth2/v2/auth',token:'https://oauth2.googleapis.com/token',api:'https://gmail.googleapis.com/gmail/v1/users/me',scope:'https://www.googleapis.com/auth/gmail.readonly https://www.googleapis.com/auth/gmail.send'},
 outlook:{auth:'https://login.microsoftonline.com/common/oauth2/v2.0/authorize',token:'https://login.microsoftonline.com/common/oauth2/v2.0/token',api:'https://graph.microsoft.com/v1.0/me',scope:'offline_access User.Read Mail.Read Mail.Send'},
};
function provider(id){if(!Object.hasOwn(PROVIDERS,id))throw new Error('不支持的邮箱');return PROVIDERS[id];}
function configuration(id,input){
 provider(id);const clientId=String(input?.clientId||'').trim(),clientSecret=String(input?.clientSecret||'').trim();
 if(id==='outlook'?!/^[a-f0-9-]{36}$/i.test(clientId):!/^[-a-zA-Z0-9.]+\.apps\.googleusercontent\.com$/.test(clientId))throw new Error('请填写有效的应用 Client ID');
 if(clientSecret.length>1024||/[\r\n\0]/.test(clientSecret))throw new Error('客户端配置无效');
 return {clientId,...(id==='gmail'&&clientSecret?{clientSecret}:{})};
}
function authorizationConfiguration(id,input){
 const config=configuration(id,input);
 if(id==='gmail'&&!config.clientSecret)throw new Error('Gmail 配置不完整：请导入 Google“桌面应用”的完整 OAuth JSON（包含 client_id 和 client_secret），再登录授权。');
 return config;
}
async function jsonResponse(response){
 if(!response.ok){
  const error=new Error(response.status===401?'邮箱授权已失效，请重新连接。':response.status===403?'邮箱权限不足，请检查应用授权与管理员设置。':response.status===429?'邮箱服务请求过多，请稍后刷新。':`邮箱服务请求失败（${response.status}），请稍后重试。`);
  error.status=response.status;throw error;
 }
 if(response.status===204||response.status===202)return {};
 return response.json();
}
async function tokenRequest(id,config,fields,fetcher=fetch){
 const body=new URLSearchParams({client_id:config.clientId,...(config.clientSecret?{client_secret:config.clientSecret}:{}),...fields});
 let response;try{response=await fetcher(provider(id).token,{method:'POST',body,signal:AbortSignal.timeout(25000),redirect:'error'});}catch{throw new Error('无法连接邮箱授权服务器，请检查系统网络和代理设置后重试。');}
 if(!response.ok){
  let code='';try{code=(await response.json()).error;}catch{}
  const messages={invalid_client:'邮箱应用配置无效：Gmail 请重新导入完整 OAuth JSON；Outlook 请检查桌面应用 Client ID。',invalid_grant:'邮箱授权已过期或被撤销，请重新登录授权。',invalid_scope:'邮箱应用权限配置不完整，请检查读取和发送权限后重新授权。',unauthorized_client:'此应用未获准连接邮箱，请检查桌面应用类型及组织管理员设置。',access_denied:'邮箱账号或管理员拒绝了授权，请检查账号许可。'};
  throw new Error(Object.hasOwn(messages,code)?messages[code]:`邮箱授权或刷新失败（${response.status}），请检查应用配置后重新连接。`);
 }
 const token=await response.json();if(typeof token.access_token!=='string'||!token.access_token)throw new Error('邮箱服务未返回有效授权');
 return {accessToken:token.access_token,refreshToken:token.refresh_token||'',expiresAt:Date.now()+Number(token.expires_in||3600)*1000};
}
// The callback listens only on a random loopback port. Wrong state must not consume it.
async function authorize(id,config,openExternal,{fetcher=fetch,timeout=180000,onPending=()=>{}}={}){
 const p=provider(id),state=randomBytes(32).toString('base64url'),verifier=randomBytes(48).toString('base64url');
 const challenge=createHash('sha256').update(verifier).digest('base64url');
 let finish,timer,server,settled=false;
 const result=new Promise((resolve,reject)=>{finish=(error,value)=>{if(settled)return;settled=true;clearTimeout(timer);server?.close();onPending(null);error?reject(error):resolve(value);};});
 result.catch(()=>{});
 server=http.createServer((req,res)=>{
  res.setHeader('Cache-Control','no-store');res.setHeader('Content-Type','text/plain; charset=utf-8');
  let url;try{url=new URL(req.url,'http://localhost');}catch{res.writeHead(400).end();return;}
  const expectedHost=id==='outlook'?`localhost:${server.address().port}`:`127.0.0.1:${server.address().port}`;
  if(req.method!=='GET'||req.headers.host!==expectedHost||url.pathname!=='/oauth/callback'){res.writeHead(404).end();return;}
  const received=Buffer.from(url.searchParams.get('state')||''),expected=Buffer.from(state);
  if(received.length!==expected.length||!timingSafeEqual(received,expected)){res.writeHead(400).end('授权状态不匹配，请返回应用重新连接。');return;}
  if(url.searchParams.has('error')){res.end('已取消邮箱授权，请返回应用。');finish(new Error('邮箱授权已取消'));return;}
  const code=url.searchParams.get('code');if(!code||code.length>8192){res.writeHead(400).end();return;}
  res.end('授权已收到，请返回企微收件台。');finish(null,code);
 });
 server.on('error',()=>finish(new Error('无法启动本机邮箱授权回调')));
 timer=setTimeout(()=>finish(new Error('邮箱登录超时，请重试连接')),timeout);
 onPending(()=>finish(new Error('邮箱连接已取消')));
 try{
  await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(0,'127.0.0.1',resolve);});
  const redirectUri=`http://${id==='outlook'?'localhost':'127.0.0.1'}:${server.address().port}/oauth/callback`;
  const url=new URL(p.auth);url.search=new URLSearchParams({client_id:config.clientId,response_type:'code',redirect_uri:redirectUri,scope:p.scope,state,code_challenge:challenge,code_challenge_method:'S256',...(id==='gmail'?{access_type:'offline',prompt:'consent'}:{prompt:'select_account',response_mode:'query'})}).toString();
  await openExternal(url.href);
  const code=await result;
  return await tokenRequest(id,config,{code,grant_type:'authorization_code',redirect_uri:redirectUri,code_verifier:verifier},fetcher);
 }catch(error){finish(error);await result.catch(()=>{});throw error;}
}
function plainText(html){return String(html||'').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'').replace(/<style\b[^>]*>[\s\S]*?<\/style>/gi,'').replace(/<(br|\/p|\/div|\/tr)\b[^>]*>/gi,'\n').replace(/<[^>]*>/g,'').replace(/&nbsp;/g,' ').replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&quot;/g,'"').replace(/&#39;|&apos;/g,"'").replace(/&amp;/g,'&');}
function gmailMessage(m){
 const headers=m.payload?.headers||[],header=name=>headers.find(h=>h.name.toLowerCase()===name)?.value||'';
 const texts=[],html=[],attachments=[];let visited=0;
 function walk(part,depth=0){if(!part||depth>20||++visited>500)return;if(part.filename){attachments.push(part.filename);return;}const content=part.body?.data?Buffer.from(part.body.data,'base64url').toString('utf8'):'';if(part.mimeType==='text/plain')texts.push(content);if(part.mimeType==='text/html')html.push(content);for(const child of part.parts||[])walk(child,depth+1);}
 walk(m.payload);
 return {id:m.id,threadId:m.threadId,subject:header('Subject')||'（无主题）',from:header('From'),replyTo:header('Reply-To')||header('From'),to:header('To'),date:Number(m.internalDate)||header('Date'),unread:(m.labelIds||[]).includes('UNREAD'),snippet:m.snippet||'',body:texts.length?texts.join('\n'):plainText(html.join('\n')),messageId:header('Message-ID'),attachments};
}
function outlookMessage(m){const address=p=>p?.emailAddress?`${p.emailAddress.name||''} <${p.emailAddress.address||''}>`:'';return {id:m.id,subject:m.subject||'（无主题）',from:address(m.from),replyTo:address(m.replyTo?.[0])||address(m.from),to:(m.toRecipients||[]).map(address).join(', '),date:m.receivedDateTime||m.sentDateTime,unread:m.isRead===false,snippet:m.bodyPreview||'',body:m.body?.contentType?.toLowerCase()==='html'?plainText(m.body.content):m.body?.content||'',attachments:m.hasAttachments?['邮件含附件，请在官方邮箱中打开或下载']:[],messageId:m.internetMessageId||''};}
function recipients(text,required=false){
 if(typeof text!=='string'||text.length>4000||/[\r\n\0]/.test(text))throw new Error('收件人格式无效');
 const list=text.split(/[,;；，]/).map(s=>s.trim()).filter(Boolean);
 if((required&&!list.length)||list.length>50||list.some(s=>!/^[-\w.!#$%&'*+/=?^`{|}~]+@[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?\.[a-zA-Z]{2,}$/.test(s)))throw new Error('请填写有效邮箱地址，多个地址用逗号分隔');
 return list;
}
function validateDraft(input){
 if(!input||typeof input!=='object')throw new Error('邮件格式无效');
 const to=recipients(input.to,true),cc=recipients(input.cc||''),bcc=recipients(input.bcc||'');
 if(typeof input.subject!=='string'||!input.subject.trim()||input.subject.length>500||/[\r\n\0]/.test(input.subject))throw new Error('请填写有效主题（最多 500 字）');
 if(typeof input.body!=='string'||!input.body.trim()||Buffer.byteLength(input.body)>500000)throw new Error('请填写正文（最多 500 KB）');
 const reply=input.reply?{id:String(input.reply.id||''),messageId:String(input.reply.messageId||''),threadId:String(input.reply.threadId||'')}:null;
 if(reply&&Object.values(reply).some(v=>v.length>1000||/[\r\n\0]/.test(v)))throw new Error('回复信息无效');
 return {to,cc,bcc,subject:input.subject.trim(),body:input.body,reply};
}
function mimeMessage(draft,from=''){
 const headers=[...(from?[`From: ${recipients(from,true)[0]}`]:[]),`To: ${draft.to.join(', ')}`];if(draft.cc.length)headers.push(`Cc: ${draft.cc.join(', ')}`);if(draft.bcc.length)headers.push(`Bcc: ${draft.bcc.join(', ')}`);
 const subject=Array.from(draft.subject.matchAll(/.{1,10}/gu),m=>`=?UTF-8?B?${Buffer.from(m[0]).toString('base64')}?=`).join('\r\n ');
 headers.push(`Subject: ${subject}`,'MIME-Version: 1.0','Content-Type: text/plain; charset=UTF-8','Content-Transfer-Encoding: base64');
 if(draft.reply?.messageId)headers.push(`In-Reply-To: ${draft.reply.messageId}`,`References: ${draft.reply.messageId}`);
 return Buffer.from(headers.join('\r\n')+'\r\n\r\n'+Buffer.from(draft.body).toString('base64').match(/.{1,76}/g).join('\r\n')).toString('base64url');
}
class MailClient{
 constructor({getAccount,saveAccount,fetcher=fetch}){Object.assign(this,{getAccount,saveAccount,fetcher});this.refreshing=new Map();this.listing=new Map();}
 async token(id){
  const account=this.getAccount(id);if(!account?.tokens)throw new Error('请先连接此邮箱');
  if(account.tokens.expiresAt>Date.now()+60000)return account.tokens.accessToken;
  if(!account.tokens.refreshToken)throw new Error('授权已过期，请重新连接邮箱');
  if(!this.refreshing.has(id))this.refreshing.set(id,(async()=>{const tokens=await tokenRequest(id,account.config,{grant_type:'refresh_token',refresh_token:account.tokens.refreshToken},this.fetcher);if(this.getAccount(id)!==account)throw new Error('邮箱连接已变更，请刷新');tokens.refreshToken||=account.tokens.refreshToken;account.tokens=tokens;await this.saveAccount(id,account);return tokens.accessToken;})().finally(()=>this.refreshing.delete(id)));
  return this.refreshing.get(id);
 }
 async request(id,suffix,{method='GET',body,raw,headers={}}={}){
  const account=this.getAccount(id),token=await this.token(id);const url=provider(id).api+suffix;
  const perform=async accessToken=>{
   try{return await this.fetcher(url,{method,headers:{Authorization:`Bearer ${accessToken}`,...(body?{'Content-Type':'application/json'}:{}),...headers},...(raw?{body:raw}:body?{body:JSON.stringify(body)}:{}),signal:AbortSignal.timeout(25000),redirect:'error'});}catch{throw new Error(method==='POST'?'发送结果未确认，请先查看已发送邮件，避免重复发送。':'邮箱网络连接失败，请检查系统网络和代理设置后刷新。');}
  };
  let response=await perform(token);
  if(response.status===401&&method==='GET'&&this.getAccount(id)===account&&account?.tokens?.refreshToken){
   await response.body?.cancel();
   if(account.tokens.accessToken===token)account.tokens.expiresAt=0;
   response=await perform(await this.token(id));
  }
  return jsonResponse(response);
 }
 async profile(id){const m=await this.request(id,id==='gmail'?'/profile':'?$select=mail,userPrincipalName');const email=m.emailAddress||m.mail||m.userPrincipalName||'';recipients(email,true);return email;}
 async list(id,folder='inbox',cursor=''){
  if(!['inbox','sent'].includes(folder)||typeof cursor!=='string'||cursor.length>4096)throw new Error('邮件分页参数无效');
  const key=JSON.stringify([id,folder,cursor]),account=this.getAccount(id),previous=this.listing.get(key);
  if(previous?.account===account)return previous.promise;
  const job={account};job.promise=this.readList(id,folder,cursor).finally(()=>{if(this.listing.get(key)===job)this.listing.delete(key);});this.listing.set(key,job);return job.promise;
 }
 async readList(id,folder,cursor){
  if(id==='gmail'){
   const query=new URLSearchParams({labelIds:folder==='inbox'?'INBOX':'SENT',maxResults:'25',...(cursor?{pageToken:cursor}:{})});
   const page=await this.request(id,'/messages?'+query);
   const messages=[];const rows=page.messages||[];
   for(let offset=0;offset<rows.length;offset+=5){messages.push(...await Promise.all(rows.slice(offset,offset+5).map(m=>this.get(id,m.id,true))));}
   return {messages,next:page.nextPageToken||''};
  }
  const endpoint=`/mailFolders/${folder==='inbox'?'inbox':'sentitems'}/messages`;
  const query=new URLSearchParams({'$top':'25','$orderby':'receivedDateTime desc','$select':'id,subject,from,toRecipients,receivedDateTime,sentDateTime,isRead,bodyPreview',...(cursor?{'$skip':cursor}:{})});
  if(cursor&&!/^\d{1,10}$/.test(cursor))throw new Error('邮件分页游标无效');
  const page=await this.request(id,endpoint+'?'+query);let next='';
  if(page['@odata.nextLink']){const link=new URL(page['@odata.nextLink']);if(link.origin!=='https://graph.microsoft.com'||link.pathname!=='/v1.0/me'+endpoint)throw new Error('邮箱返回了无效分页地址');next=link.searchParams.get('$skip')||'';if(next&&!/^\d{1,10}$/.test(next))throw new Error('邮箱返回了无效分页游标');}
  return {messages:(page.value||[]).map(outlookMessage),next};
 }
 async get(id,messageId,summary=false){
  if(typeof messageId!=='string'||!messageId||messageId.length>1000||/[\r\n\0]/.test(messageId))throw new Error('邮件 ID 无效');
  const suffix='/messages/'+encodeURIComponent(messageId);
  if(id==='gmail')return gmailMessage(await this.request(id,suffix+'?format='+(summary?'metadata':'full')));
  return outlookMessage(await this.request(id,suffix,{headers:{Prefer:'outlook.body-content-type="text"'}}));
 }
 async send(id,draft){
  const from=this.getAccount(id)?.email;if(!from)throw new Error('邮箱身份未确认，请重新连接');
  if(id==='gmail'){const result=await this.request(id,'/messages/send',{method:'POST',body:{raw:mimeMessage(draft,from),...(draft.reply?.threadId?{threadId:draft.reply.threadId}:{})}});if(!result.id)throw new Error('发送结果未确认，请检查已发送邮件');return {accepted:true,id:result.id};}
  await this.request(id,'/sendMail',{method:'POST',raw:Buffer.from(mimeMessage(draft,from),'base64url').toString('base64'),headers:{'Content-Type':'text/plain'}});
  return {accepted:true};
 }
}
module.exports={PROVIDERS,configuration,authorizationConfiguration,authorize,MailClient,validateDraft,mimeMessage,gmailMessage,outlookMessage};
