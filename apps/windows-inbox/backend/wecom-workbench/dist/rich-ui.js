import {EMOJI_IMAGES} from './emoji-map.js';

export function emojiText(text,escape){
 const input=String(text??'');let html='',last=0;
 for(const match of input.matchAll(/\[([^\]\r\n]{1,16})\]/g)){
  html+=escape(input.slice(last,match.index));
  const image=EMOJI_IMAGES[match[1]];
  html+=image?`<img class="inline-emoji" src="${image}" alt="${escape(match[0])}" title="${escape(match[0])}" loading="lazy">`:escape(match[0]);
  last=match.index+match[0].length;
 }
 return html+escape(input.slice(last));
}
export function validDocumentUrl(value){
 try{const url=new URL(value);return url.protocol==='https:'&&!url.username&&!url.password&&(!url.port||url.port==='443')&&['doc.weixin.qq.com','docs.qq.com','drive.weixin.qq.com'].includes(url.hostname)?url.href:null;}catch{return null;}
}
export function validWebUrl(value){
 if(typeof value!=='string'||value.length>4096||!/^https?:\/\/[^\s<>\\]+$/i.test(value))return null;
 try{const url=new URL(value);return url.hostname&&!url.username&&!url.password?url.href:null;}catch{return null;}
}
const webLink=(url,label,escape)=>`<a class="message-link" href="${escape(url)}" target="_blank" rel="noopener noreferrer" data-web-link>${escape(label)}</a>`;
export function cachedShareCard(code,content){
 const native=Number(code);
 if(!Number.isSafeInteger(native)||native%4294967296!==49)return null;
 const subtype=Math.floor(native/4294967296);
 const labels={3:'音乐',4:'视频分享',5:'网页链接',33:'小程序',36:'小程序',51:'视频号',87:'群公告'};
 const label=labels[subtype];
 if(!label||typeof content!=='string')return null;
 const first=content.match(/^\[[^\]\r\n]{1,20}\][ \t]*([^\r\n]*)(?:\r?\n|$)/);
 if(!first)return null;
 const lines=content.slice(first[0].length).trim().split(/\r?\n/);
 const url=subtype===5?validWebUrl(lines.at(-1)):null;
 if(url)lines.pop();
 return {label,title:first[1].slice(0,500)||label,description:lines.join('\n').slice(0,2000),url, hint:[33,36,51].includes(subtype)?'请在微信原聊天中打开':subtype===4?'视频分享，请在微信原聊天中打开':''};
}
export function linkedText(input,escape){
 const text=String(input??'');let last=0,html='';
 for(const match of text.matchAll(/https?:\/\/[^\s<>"“”‘’。，；！？、）】》]+/gi)){
  let shown=match[0].replace(/[.,;!]+$/,'');
  while(shown.endsWith(')')&&(shown.match(/\)/g)||[]).length>(shown.match(/\(/g)||[]).length)shown=shown.slice(0,-1);
  const url=validWebUrl(shown);
  if(!url||/[\w+:/]/.test(text.charAt(match.index-1)))continue;
  html+=emojiText(text.slice(last,match.index),escape)+webLink(url,shown,escape);last=match.index+shown.length;
 }
 return html+emojiText(text.slice(last),escape);
}
const quoteKinds=new Set(['text','image','voice','video','sticker','location','link','file','card','unavailable']);
const stringField=(value,max)=>typeof value==='string'?value.trim().slice(0,max):'';
export const referenceKey=value=>typeof value==='string'&&/^[a-f0-9]{32}$/.test(value)?value:'';
export function normalizeQuote(value){
 if(!value||!quoteKinds.has(value.kind))return null;
 return {sender:stringField(value.sender,200),text:stringField(value.text,2048),kind:value.kind,target:referenceKey(value.target)};
}

export function recordDetails(record,escape,options={},depth=0){
 const entries=Array.isArray(record?.entries)?record.entries.slice(0,450):[];
 if(!entries.length)return record?.summary?`<p>${linkedText(record.summary,escape)}</p><p class="media-placeholder">仅有摘要，逐条内容未缓存。</p>`:'<p>本机缓存没有可显示的聊天记录。</p>';
 const render=item=>{
  const url=validWebUrl(item.url),title=item.title?`<strong>${escape(item.title)}</strong>`:'',text=item.text?`<p>${linkedText(item.text,escape)}</p>`:'';
  const waiting=kind=>`<span class="media-placeholder">${kind} · 本机暂无缓存，请在微信原聊天记录里打开</span>`;
  let body='';
  if(item.kind==='图片')body=(item.image?.status==='ready'?richContent({image:item.image},{escapeHtml:escape}):waiting('图片'))+text;
  else if(item.record&&depth<4)body=`<details><summary>${escape(item.title||'展开转发记录')}（${item.record.entries?.length||0} 条）</summary>${recordDetails(item.record,escape,options,depth+1)}</details>`;
  else if(item.file&&options.attachmentCard)body=richContent({file:options.normalizeAttachment(item.file)},{...options,escapeHtml:escape});
  else if(item.video)body=richContent({video:item.video},{...options,icon:options.icon||(()=>''),escapeHtml:escape});
  else body=(url?webLink(url,item.title||'在浏览器打开 ↗',escape):title)+text+(item.kind==='文件'?`<p>${waiting('文件')}</p>`:'');
  return `<li><div class="record-meta"><strong>${escape(item.sender||item.kind||'消息')}</strong><span>${escape(item.time||'')}</span></div><div>${body||`<span class="media-placeholder">${escape(item.kind||'消息')} · 本机没有可显示的正文</span>`}</div></li>`;
 };
 const blocks=[];
 for(let first=0;first<entries.length;first+=60){const section=`<ol class="record-items">${entries.slice(first,first+60).map(render).join('')}</ol>`;blocks.push(first?`<details><summary>展开第 ${first+1}–${Math.min(first+60,entries.length)} 条</summary>${section}</details>`:section);}
 return blocks.join('')+(record.partial?'<p class="media-placeholder">记录过长，部分条目未展示。</p>':'');
}

export function quoteContent(quote,original,reply,{escapeHtml:escape}){
 if(!quote)return '';
 const media=quote.kind==='image'?original?.image:quote.kind==='sticker'?original?.sticker:null;
 const alt=quote.kind==='image'?'引用图片':'引用表情包';
 const thumbnail=media?.status==='ready'&&/^[a-f0-9]{64}$/.test(media.id||'')
  ? `<figure class="quote-media"><button type="button" class="image-open" data-image-preview="${media.id}" title="在收件台放大"><img data-local-image="${media.id}" src="/api/media?id=${media.id}" alt="${alt}" loading="lazy" decoding="async"></button><button type="button" class="image-load-error small-button" data-media-retry="${media.id}" hidden>预览失败，重试</button></figure>`:'';
 const sender=quote.sender||(original?.self?'我':original?.sender)||'';
 const excerpt=quote.kind==='unavailable'&&original?.text?original.text.slice(0,2000):quote.text;
 const jump=original?`<button type="button" class="quote-jump" data-quote-target="${escape(quote.target)}" data-quote-reply="${escape(reply.id)}">查看原消息</button>`:'';
 return `<blockquote class="message-quote" aria-label="引用消息"><div class="quote-header"><span>${sender?`引用 · ${escape(sender)}`:'引用消息'}</span>${jump}</div><div class="quote-excerpt">${linkedText(excerpt||'原消息内容不可用',escape)}</div>${thumbnail}</blockquote>`;
}

export function richContent(message,{escapeHtml:escape,icon,attachmentCard}){
 if(message.record){
  const record=message.record;
  return `<button type="button" class="record-card" data-chat-record="${escape(message.id)}"><strong>${escape(record.title||'合并转发的聊天记录')}</strong><span>${record.entries?.length?`查看 ${record.entries.length} 条记录`:'查看聊天记录'} ›</span></button>`;
 }
 if(message.articles?.length)return message.articles.map(article=>{
  let valid=false;try{const u=new URL(article.url);valid=u.protocol==='https:'&&u.hostname==='mp.weixin.qq.com'&&!u.username&&!u.password&&!u.port;}catch{}
  return `<div class="article-card"><strong>${escape(article.title)}</strong><p>${escape(article.description||'')}</p>${valid?webLink(article.url,'阅读公众号文章 ↗',escape):''}</div>`;
 }).join('');
 const video=message.video||(message.file&&/\.mp4$/i.test(message.file.name)?{...message.file,size:message.file.bytes}:null);
 if(video){
  const seconds=video.duration;
  const duration=Number.isFinite(seconds)&&seconds>0?`${Math.floor(seconds/60)}:${String(Math.floor(seconds%60)).padStart(2,'0')}`:'';
  const size=typeof video.size==='number'?`${(video.size/1048576).toFixed(1)} MB`:'';
  const playable=['ready','on_demand'].includes(video.status)&&/^[a-f0-9]{64}$/.test(video.id||'');
  return `<div class="file-card video-card"><span class="file-icon">${icon('file')}</span><div class="attachment-info"><strong>${escape(video.name||'视频消息')}</strong><small>${escape([duration,size].filter(Boolean).join(' · '))}</small><small data-attachment-status>${video.status==='ready'?'已保留本机副本':playable?'来自客户端缓存':'原视频未缓存，请在原聊天中下载'}</small>${playable?`<div class="attachment-actions"><button type="button" class="small-button" data-video-preview="${video.id}" data-video-name="${escape(video.name||'视频消息')}">播放视频</button><button type="button" class="small-button" data-attachment-save="${video.id}">另存为</button>${video.status==='on_demand'?` <button type="button" class="small-button" data-attachment-keep="${video.id}">保留离线副本</button>`:''}</div>`:''}</div></div>`;
 }
 if(message.file)return attachmentCard(message.file,{escapeHtml:escape,icon});
 if(message.image){
  const image=message.image;
  if(image.status==='ready'&&/^[a-f0-9]{64}$/.test(image.id||''))return `<figure class="message-image"><button type="button" class="image-open" data-image-preview="${image.id}" title="在收件台放大"><img class="chat-image" data-local-image="${image.id}" src="/api/media?id=${image.id}" alt="聊天图片" loading="lazy" decoding="async"></button><figcaption><span data-media-status>${image.quality==='preview'?'本机缩略图 · 原图尚未缓存':'已同步原图'}</span><button class="image-load-error small-button" data-media-retry="${image.id}" hidden>图片加载失败，重试</button></figcaption></figure>`;
  return '<span class="media-placeholder">[图片 · 等待客户端缓存]</span>';
 }
 if(message.sticker){
  const sticker=message.sticker;
  if(sticker.status==='ready'&&/^[a-f0-9]{64}$/.test(sticker.id||''))return `<figure class="message-image"><img class="message-sticker" data-local-image="${sticker.id}" src="/api/media?id=${sticker.id}" alt="表情包" loading="lazy"><button class="image-load-error small-button" data-media-retry="${sticker.id}" hidden>表情包加载失败，重试</button></figure>`;
  return `<span class="media-placeholder">[表情包 · ${sticker.status==='unsupported'?'暂不支持预览':'等待客户端缓存'}]</span>`;
 }
 if(message.document){
  const doc=message.document,url=validDocumentUrl(doc.url);
  if(url)return `<div class="file-card document-card"><span class="file-icon">${icon(doc.kind==='spreadsheet'?'grid':'file')}</span><div><strong>${escape(doc.title||'在线文档')}</strong><small>${doc.kind==='spreadsheet'?'在线表格':'在线文档'} · 企业微信</small></div><button class="small-button" data-document-open="${escape(url)}">打开</button></div>`;
 }
 if(message.card){
  const card=message.card,url=validWebUrl(card.url);
  return `<div class="article-card message-web-card"><small>${escape(card.label||'网页链接')}</small><strong>${url?webLink(url,card.title||'打开网页',escape):escape(card.title||'卡片消息')}</strong>${card.description?`<p>${linkedText(card.description,escape)}</p>`:''}${url?webLink(url,'在浏览器打开 ↗',escape):''}${card.hint?`<p class="media-placeholder">${escape(card.hint)}</p>`:''}</div>`;
 }
 return linkedText(message.text,escape);
}
