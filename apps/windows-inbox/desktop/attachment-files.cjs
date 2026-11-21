const fs=require('node:fs');
const path=require('node:path');
const {createHash}=require('node:crypto');
const validName=name=>typeof name==='string'&&name.length>0&&name.length<=255&&!/[. ]$|[<>:"/\\|?*\x00-\x1f]/.test(name)&&!/^(?:CON|PRN|AUX|NUL|(?:COM|LPT)[0-9¹²³])\s*$/i.test(name.split('.')[0]);

function attachmentManifest(directory,id){
 if(typeof id!=='string'||!/^[a-f0-9]{64}$/.test(id))throw new Error('附件编号无效');
 const root=path.resolve(directory,'attachments');
 const folder=path.join(root,id);
 const manifest=path.join(folder,'manifest.json');
 if(fs.realpathSync(folder)!==path.join(fs.realpathSync(root),id)||fs.lstatSync(manifest).isSymbolicLink())throw new Error('附件目录无效');
 const data=JSON.parse(fs.readFileSync(manifest,'utf8'));
 if(data.version!==1||data.id!==id||!validName(data.name)||!Number.isSafeInteger(data.size)||data.size<0)throw new Error('附件信息无效');
 return {data,folder};
}
function resolveAttachment(directory,id){
 const {data,folder}=attachmentManifest(directory,id);
 const target=path.join(folder,'content',data.name);
 const canonicalRoot=fs.realpathSync(folder)+path.sep;
 const canonical=fs.realpathSync(target);
 if(!canonical.startsWith(canonicalRoot)||fs.lstatSync(target).isSymbolicLink()||!fs.statSync(canonical).isFile()||fs.statSync(canonical).size!==data.size)throw new Error('附件尚未同步或已发生变化');
 const name=validName(data.display_name)&&path.extname(data.display_name).toLowerCase()===path.extname(data.name).toLowerCase()?data.display_name:data.name;
 return {filename:canonical,name,size:data.size};
}
function resolveDeferredAttachment(directory,id){
 const {data,folder}=attachmentManifest(directory,id);
 const {platform,relative,mtime_ms:mtime,root_id:rootId}=data.source||{};
 if(!['wechat','wecom'].includes(platform)||typeof relative!=='string'||!Number.isSafeInteger(mtime))throw new Error('附件来源无效');
 const parts=relative.split('/');
 if(parts.some(part=>!part||part==='.'||part==='..'||/[\\:\x00-\x1f]/.test(part)))throw new Error('附件路径无效');
 const selection=path.join(directory,platform==='wechat'?'wechat-reading':'local-reading',platform==='wechat'?'account-path.txt':'profile-path.txt');
 const profile=fs.readFileSync(selection,'utf8').replace(/^\ufeff/,'').trim();
 if(!path.isAbsolute(profile))throw new Error('账号目录无效');
 const account=fs.realpathSync(platform==='wechat'?profile:path.dirname(profile));
 if(platform==='wechat'&&(parts.length<4||parts[0]!=='msg'||!['file','video'].includes(parts[1])))throw new Error('附件路径无效');
 const wecomVideo=platform==='wecom'&&data.source.cache==='video';
 if(wecomVideo&&path.extname(data.name).toLowerCase()!=='.mp4')throw new Error('视频路径无效');
 const wecomRoot=path.join(account,'Cache',wecomVideo?'Video':'File');
 const target=platform==='wechat'?path.join(account,...parts):path.join(wecomRoot,...parts);
 const filename=fs.realpathSync(target);
 const fixedRoot=platform==='wechat'?path.join(account,...parts.slice(0,2)):wecomRoot;
 const rootPath=platform==='wechat'?account:fixedRoot;
 const identityRoot=process.platform==='win32'?rootPath.toLowerCase():rootPath;
 if(createHash('sha256').update(identityRoot).digest('hex')!==rootId)throw new Error('附件不属于当前账号');
 if(!filename.startsWith(fixedRoot+path.sep)||fs.lstatSync(target).isSymbolicLink())throw new Error('附件路径无效');
 const info=fs.statSync(filename,{bigint:true});
 if(!info.isFile()||info.size!==BigInt(data.size)||info.mtimeNs/1000000n!==BigInt(mtime)||path.extname(filename).toLowerCase()!==path.extname(data.name).toLowerCase())throw new Error('客户端文件已移除或变化');
 return {filename,name:data.name,folder,size:data.size,mtime};
}
const openable=/\.(pdf|txt|csv|md|docx?|xlsx?|pptx?|odt|ods|odp|rtf|png|jpe?g|gif|webp|bmp|mp3|mp4|wav|zip|7z|rar)$/i;
module.exports={resolveAttachment,resolveDeferredAttachment,openable};
