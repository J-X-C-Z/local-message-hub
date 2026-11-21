const {resolveAttachment,resolveDeferredAttachment,openable}=require('./attachment-files.cjs');
const fs=require('node:fs/promises');
const {constants}=require('node:fs');
const path=require('node:path');
const {randomUUID}=require('node:crypto');
module.exports.installAttachments=({app,ipcMain,shell,dialog,getMainWindow,getOrigin})=>{
 ipcMain.handle('links:open',async(event,value)=>{
  const main=getMainWindow(),frame=event.senderFrame;
  if(!main||event.sender!==main.webContents||frame!==main.webContents.mainFrame||!frame?.url.startsWith('wecom-inbox://app/'))throw new Error('无权打开链接');
  try{
   if(typeof value!=='string'||value.length>4096||!/^https?:\/\/[^\s<>\\]+$/i.test(value))throw new Error('invalid');
   const url=new URL(value);
   if(!url.hostname||url.username||url.password)throw new Error('invalid');
   await shell.openExternal(url.href);return {ok:true};
  }catch{return {ok:false,error:'网页链接无效或无法打开。'};}
 });
 ipcMain.handle('documents:open',async(event,value)=>{
  const main=getMainWindow(),location=event.senderFrame?.url||'';
  const trusted=location.startsWith('wecom-inbox://app/')||(getOrigin()&&location.startsWith(getOrigin()+'/'));
  if(!main||event.sender!==main.webContents||event.senderFrame!==event.sender.mainFrame||!trusted)throw new Error('无权打开在线文档');
  try{
   const url=new URL(value);
   if(url.protocol!=='https:'||url.username||url.password||(url.port&&url.port!=='443')||!['doc.weixin.qq.com','docs.qq.com','drive.weixin.qq.com'].includes(url.hostname))throw new Error('invalid');
   await shell.openExternal(url.href);return {ok:true};
  }catch{return {ok:false,error:'在线文档链接无效或无法打开。'};}
 });
 const authorize=event=>{
  const main=getMainWindow();
  const url=event.senderFrame?.url||'';
  const trustedUrl=url.startsWith('wecom-inbox://app/')||(getOrigin()&&url.startsWith(getOrigin()+'/'));
  if(!main||event.sender!==main.webContents||event.senderFrame!==event.sender.mainFrame||!trustedUrl)throw new Error('无权打开附件');
 };
 const locate=(directory,id)=>{
  try{return resolveAttachment(directory,id);}catch{return resolveDeferredAttachment(directory,id);}
 };
 ipcMain.handle('attachments:open',async(event,id)=>{
  authorize(event);
  try{
   const directory=app.getPath('userData');
   const file=locate(directory,id);
   if(!openable.test(file.name)){shell.showItemInFolder(file.filename);return {ok:true,revealed:true};}
   const error=await shell.openPath(file.filename);
   return error?{ok:false,error:'未能打开文件，请检查系统中是否安装了相应阅读器。'}:{ok:true};
  }catch{return {ok:false,error:'本机附件不可用。若客户端已清理缓存，请在原聊天中重新下载。'};}
 });
 ipcMain.handle('attachments:save-as',async(event,id)=>{
  authorize(event);
  let temporary;
  try{
   const directory=app.getPath('userData'),source=locate(directory,id);
   const choice=await dialog.showSaveDialog(getMainWindow(),{title:'另存聊天附件',defaultPath:path.join(app.getPath('downloads'),source.name)});
   if(choice.canceled||!choice.filePath)return {ok:false,canceled:true};
   const destination=choice.filePath;
   const selectedFolder=await fs.realpath(path.dirname(destination));
   const selectedFile=path.join(selectedFolder,path.basename(destination));
   const canonical=await fs.realpath(selectedFile).catch(error=>{if(error.code==='ENOENT')return selectedFile;throw error;});
   const key=value=>process.platform==='win32'?value.toLowerCase():value;
   if(key(canonical)===key(source.filename))return {ok:true};
   temporary=path.join(selectedFolder,`.inbox-${randomUUID()}.tmp`);
   const before=await fs.stat(source.filename,{bigint:true});
   await fs.copyFile(source.filename,temporary,constants.COPYFILE_EXCL);
   const checked=locate(directory,id),after=await fs.stat(checked.filename,{bigint:true});
   if(checked.filename!==source.filename||after.size!==BigInt(source.size)||before.size!==after.size||before.mtimeNs!==after.mtimeNs||before.ino!==after.ino||(await fs.stat(temporary,{bigint:true})).size!==after.size)throw new Error('changed');
   await fs.rename(temporary,selectedFile);
   return {ok:true};
  }catch{return {ok:false,error:'未能另存附件，请检查文件是否仍可用和目标目录是否可写。'};}
  finally{if(temporary)await fs.rm(temporary,{force:true}).catch(()=>{});}
 });
 ipcMain.handle('attachments:keep',async(event,id)=>{
  authorize(event);
  const directory=app.getPath('userData');
  let temporary;
  try{
   try{resolveAttachment(directory,id);return {ok:true};}catch{}
   const source=resolveDeferredAttachment(directory,id);
   const content=path.join(source.folder,'content');
   await fs.mkdir(content,{recursive:true});
   if(await fs.realpath(content)!==path.join(await fs.realpath(source.folder),'content'))throw new Error('invalid directory');
   temporary=path.join(content,`.inbox-${randomUUID()}.tmp`);
   await fs.copyFile(source.filename,temporary,constants.COPYFILE_EXCL);
   const checked=resolveDeferredAttachment(directory,id);
   if(checked.filename!==source.filename||checked.size!==source.size||checked.mtime!==source.mtime||(await fs.stat(temporary)).size!==source.size)throw new Error('changed');
   await fs.rename(temporary,path.join(content,source.name));
   return {ok:true};
  }catch{return {ok:false,error:'未能保留副本，请确认原附件仍可打开。'};}
  finally{if(temporary)await fs.rm(temporary,{force:true}).catch(()=>{});}
 });
};
