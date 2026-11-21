import {pipeline} from 'node:stream';
import path from 'node:path';
import files from '../desktop/attachment-files.cjs';
import {open as openFile} from 'node:fs/promises';

function requestedRange(value,size){
 if(!value)return {start:0,end:size-1};
 const match=/^bytes=(\d*)-(\d*)$/.exec(value);
 if(!match||(!match[1]&&!match[2]))return null;
 const first=Number(match[1]),last=Number(match[2]);
 if(!Number.isSafeInteger(first)||!Number.isSafeInteger(last))return null;
 if(!match[1])return last>0?{start:Math.max(0,size-last),end:size-1}:null;
 return first<size&&(!match[2]||last>=first)?{start:first,end:match[2]?Math.min(last,size-1):size-1}:null;
}

export async function serveLocalTrack(req,res,{directory,id,kind}){
 let handle;
 try{
  let file;
  try{file=files.resolveAttachment(directory,id);}catch{file=files.resolveDeferredAttachment(directory,id);}
  const ext=path.extname(file.name).toLowerCase();
  const mime=kind==='audio'?{'.mp3':'audio/mpeg','.wav':'audio/wav'}[ext]:ext==='.mp4'?'video/mp4':null;
  if(!mime||path.extname(file.filename).toLowerCase()!==ext){res.writeHead(415).end();return;}
  handle=await openFile(file.filename,'r');
  const stat=await handle.stat({bigint:true}),size=Number(stat.size),header=Buffer.alloc(16);
  if(!stat.isFile()||!Number.isSafeInteger(size)||size<=16||size!==file.size||(file.mtime!==undefined&&stat.mtimeNs/1000000n!==BigInt(file.mtime))||
     (await handle.read(header,0,16,0)).bytesRead!==16){res.writeHead(415).end();return;}
  const valid=ext==='.mp4'?(header.toString('ascii',4,8)==='ftyp'&&header.readUInt32BE(0)>=16&&header.readUInt32BE(0)<size):ext==='.wav'?(header.toString('ascii',0,4)==='RIFF'&&header.toString('ascii',8,12)==='WAVE'):(header.toString('ascii',0,3)==='ID3'||(header[0]===255&&(header[1]&224)===224));
  if(!valid){res.writeHead(415).end();return;}
  if(res.destroyed)return;
  const headers={'Accept-Ranges':'bytes','Content-Type':mime,'X-Content-Type-Options':'nosniff'};
  const range=requestedRange(req.headers.range,size);
  if(!range){res.writeHead(416,{...headers,'Content-Range':`bytes */${size}`}).end();return;}
  const {start,end}=range;
  res.writeHead(req.headers.range?206:200,{...headers,'Content-Length':end-start+1,
   ...(req.headers.range?{'Content-Range':`bytes ${start}-${end}/${size}`}:{})});
  if(req.method==='HEAD'){res.end();return;}
  const stream=handle.createReadStream({start,end});
  handle=null; // stream owns the descriptor, including when playback is canceled
  pipeline(stream,res,()=>{});
 }catch{
  if(!res.destroyed){if(!res.headersSent)res.writeHead(404).end();else res.destroy();}
 }finally{if(handle)await handle.close().catch(()=>{});}
}

export async function serveLocalMedia(req,res,{directory,id}){
 let handle;
 try{
  let file;
  try{file=files.resolveAttachment(directory,id);}catch{
   file=files.resolveDeferredAttachment(directory,id);
   if(!/\.(?:jpe?g|png|gif|webp|bmp)$/i.test(file.name)){res.writeHead(415).end();return;}
  }
  handle=await openFile(file.filename,'r');
  const stat=await handle.stat({bigint:true}),size=Number(stat.size);
  if(!stat.isFile()||size!==file.size||(file.mtime!==undefined&&stat.mtimeNs/1000000n!==BigInt(file.mtime))){res.writeHead(404).end();return;}
  if(size>10*1024*1024){res.writeHead(413).end();return;}
  const prefix=Buffer.alloc(Math.min(size,26));
  const {bytesRead}=await handle.read(prefix,0,prefix.length,0);
  const body=prefix.subarray(0,bytesRead);
  let mime='';
  if(body.subarray(0,8).equals(Buffer.from([137,80,78,71,13,10,26,10])))mime='image/png';
  else if(/^GIF8[79]a$/.test(body.subarray(0,6).toString()))mime='image/gif';
  else if(body.subarray(0,3).equals(Buffer.from([255,216,255])))mime='image/jpeg';
  else if(body.subarray(0,4).toString()==='RIFF'&&body.subarray(8,12).toString()==='WEBP')mime='image/webp';
  else if (body.length >= 26 && body.subarray(0, 2).toString() === 'BM') {
   const pixelOffset = body.readUInt32LE(10);
   const headerSize = body.readUInt32LE(14);
   if (headerSize >= 12 && pixelOffset >= 14 + headerSize && pixelOffset < size) mime = 'image/bmp';
  }
  if(!mime){res.writeHead(415).end();return;}
  if(res.destroyed)return;
  res.writeHead(200,{'Content-Type':mime,'Content-Length':size,'Cache-Control':file.mtime===undefined?'private, max-age=86400':'no-store','X-Content-Type-Options':'nosniff'});
  if(req.method==='HEAD'){res.end();return;}
  const stream=handle.createReadStream({start:0,end:size-1});handle=null;
  pipeline(stream,res,()=>{});
 }catch{if(!res.destroyed){if(!res.headersSent)res.writeHead(404).end();else res.destroy();}}
 finally{if(handle)await handle.close().catch(()=>{});}
}
