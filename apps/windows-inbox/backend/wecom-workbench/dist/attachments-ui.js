export function normalizeAttachment(value){
 if(!value||typeof value.name!=='string')return undefined;
 return {id:/^[a-f0-9]{64}$/.test(value.id||'')?value.id:'',name:value.name,
  bytes:typeof value.size==='number'?value.size:null,type:String(value.type||'文件'),status:String(value.status||'unsupported')};
}
export function attachmentCard(file,{escapeHtml:escape,icon}){
 const size=file.bytes===null||file.bytes===undefined?(file.size||'大小未知'):file.bytes<1024?`${file.bytes} B`:file.bytes<1048576?`${(file.bytes/1024).toFixed(1)} KB`:`${(file.bytes/1048576).toFixed(1)} MB`;
 const labels={ready:'已同步到本机',on_demand:'按需打开 · 来自客户端缓存',pending:'等待客户端下载',incomplete:'文件尚未完整或已变化',ambiguous:'发现多个缓存版本，暂未同步',unsupported:'附件格式待适配',unreadable:'文件暂时不可读，等待重试'};
 const ready=['ready','on_demand'].includes(file.status)&&file.id;
 const preview=ready&&!(file.bytes>10*1024*1024)&&/\.(?:jpe?g|png|gif|webp|bmp)$/i.test(file.name);
 const audio=ready&&/\.(?:mp3|wav)$/i.test(file.name);
 const status=labels[file.status]||'等待同步';
 return `<div class="file-card attachment-card"><span class="file-icon">${icon('file')}</span><div class="attachment-info"><strong>${escape(file.name)}</strong><small>${escape(size)} · ${escape(file.type)}</small>${file.status?`<small data-attachment-status>${escape(status)}</small>`:''}${ready?`<div class="attachment-actions"><button type="button" class="small-button" ${audio?'data-audio-preview':preview?'data-image-preview':'data-attachment-open'}="${escape(file.id)}"${audio?` data-audio-name="${escape(file.name)}"`:''}>${audio?'播放':preview?'预览':'打开'}</button><button type="button" class="small-button" data-attachment-save="${escape(file.id)}">另存为</button>${file.status==='on_demand'?`<button type="button" class="small-button" data-attachment-keep="${escape(file.id)}">保留离线副本</button>`:''}</div>`:'<div class="attachment-actions"><button type="button" class="small-button" disabled>未就绪</button></div>'}</div></div>`;
}
