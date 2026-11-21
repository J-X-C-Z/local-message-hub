import { initializeIntegration } from './integration-ui.js';
import {normalizeAttachment,attachmentCard} from './attachments-ui.js';
import {normalizeQuote,quoteContent,recordDetails,referenceKey,richContent,cachedShareCard} from './rich-ui.js';
import { LiveConversationIndex, messagePage, quoteOriginal, conversationSummary, conversationExport, nextRender, dayKey as dateKey } from './view-performance.mjs';
const paths = {
  message: '<path d="M21 11.5a8.3 8.3 0 0 1-.8 3.6 8.5 8.5 0 0 1-7.7 4.9 8.3 8.3 0 0 1-3.6-.8L3 21l1.8-5.9a8.3 8.3 0 0 1-.8-3.6 8.5 8.5 0 0 1 4.9-7.7 8.3 8.3 0 0 1 3.6-.8h.5a8.5 8.5 0 0 1 8 8v.5Z"/><path d="M8 10h8M8 14h5"/>',
  contacts: '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M22 21v-2a4 4 0 0 0-3-3.9M16 3.1a4 4 0 0 1 0 7.8"/><circle cx="9" cy="7" r="4"/>',
  grid: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  link: '<path d="m10 13 4-4M9 15l-2 2a4 4 0 0 1-6-6l4-4a4 4 0 0 1 6 0m2 2 2-2a4 4 0 0 1 6 6l-4 4a4 4 0 0 1-6 0" transform="translate(1 0)"/>',
  refresh: '<path d="M20 7a9 9 0 0 0-15-2L2 8m0-5v5h5M4 17a9 9 0 0 0 15 2l3-3m0 5v-5h-5"/>',
  database: '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/>',
  'check-double': '<path d="m1 12 4 4L15 6m-5 10 3 3L23 9"/>',
  search: '<circle cx="10.5" cy="10.5" r="7"/><path d="m16 16 5 5"/>',
  shield: '<path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3Z"/><path d="m8 12 3 3 5-6"/>',
  pin: '<path d="m16 3 5 5-5 2-3 6-2-2-7 7m5-9-2-2 6-3 3-4Z"/>',
  panel: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M15 4v16m3-12h0m0 4h0"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10h.01"/>',
  edit: '<path d="m16 3 5 5-12 12H4v-5L16 3Z"/><path d="m13 6 5 5"/>',
  send: '<path d="m22 2-7 20-4-9-9-4L22 2ZM22 2 11 13"/>',
  chart: '<path d="M4 3v18h17M8 16v-5m5 5V7m5 9V3"/>',
  download: '<path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/>',
  mail: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 6 9-6"/>',
  'chevron-right': '<path d="m9 5 7 7-7 7"/>',
  'chevron-left': '<path d="m15 5-7 7 7 7"/>',
  'arrow-right': '<path d="M4 12h16m-6-6 6 6-6 6"/>',
  close: '<path d="m6 6 12 12M6 18 18 6"/>',
  file: '<path d="M14 2H5v20h14V7l-5-5Z"/><path d="M14 2v6h5M8 12h8m-8 4h5"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  bell: '<path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4"/>',
};
const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, character => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[character]));
const icon = name => `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">${paths[name] || paths.message}</svg>`;
function hydrateIcons(root = document) { root.querySelectorAll('[data-icon]').forEach(el => { el.innerHTML = icon(el.dataset.icon); }); }
hydrateIcons();

const today = new Date();
const at = (hour, minute, dayOffset = 0) => { const date = new Date(today); date.setDate(date.getDate() + dayOffset); date.setHours(hour, minute, 0, 0); return date.getTime(); };
const demoConversations = [
  { id: 'project', name: '产品研发协作群', initials: 'group', color: 'group', type: 'group', role: '内部群聊', memberCount: 12, department: '产品与研发', description: '把好想法，变成好产品。', pinned: true, unread: 3, messages: [
    { id: 'p1', sender: '林晓', initials: '晓', color: 'teal', time: at(9, 30), text: '早上好，大家 ☀️\n新版工作台的交互方案已经整理好了，今天可以一起过一下。' },
    { id: 'p2', sender: '林晓', initials: '晓', color: 'teal', time: at(9, 31), file: { name: '消息工作台 · 产品方案.pdf', size: '2.4 MB', type: 'PDF 文档' } },
    { id: 'p3', sender: '陈远', initials: '远', color: 'slate', time: at(9, 34), text: '收到，我先看看。消息列表的未读状态这次也会一起优化吗？' },
    { id: 'p4', sender: '我', self: true, time: at(9, 36), text: '会的，这次会统一优化消息列表、会话搜索和未读提醒。\n下午 3 点我们一起对一下细节。', read: '本地演示消息' },
    { id: 'p5', sender: '林晓', initials: '晓', color: 'teal', time: at(9, 38), text: '好的，我把需要讨论的几个点标注出来，大家可以提前看一下 👍' },
    { id: 'p6', system: true, text: '以上为只读演示消息', time: at(9, 39) },
  ] },
  { id: 'lin', name: '林晓', initials: '晓', color: 'teal', type: 'person', role: '产品设计师', department: '产品设计部', description: '保持好奇，让设计更进一步。', pinned: true, unread: 2, messages: [
    { id: 'l1', sender: '林晓', time: at(9, 10), text: '工作台的设计稿我更新好了，消息详情区也加进去了。' },
    { id: 'l2', sender: '我', self: true, time: at(9, 12), text: '收到，我稍后看一下，辛苦啦。', read: '本地演示消息' },
    { id: 'l3', sender: '林晓', time: at(9, 42), text: '下午评审用这个版本就好，有问题随时找我～' },
  ] },
  { id: 'chen', name: '陈远', initials: '远', color: 'slate', type: 'person', role: '前端工程师', department: '研发中心', description: '认真写代码，也认真生活。', unread: 0, messages: [
    { id: 'c1', sender: '陈远', time: at(9, 0), text: '消息接口已联调完成，可以在本地工作台里看真实回调数据了。' },
    { id: 'c2', sender: '我', self: true, time: at(9, 3), text: '很好，我们先用测试应用验证一下。', read: '本地演示消息' },
    { id: 'c3', sender: '陈远', time: at(9, 18), text: '没问题，联调环境已准备好。' },
  ] },
  { id: 'design', name: '设计灵感小组', initials: 'group', color: 'purple', type: 'group', memberCount: 8, role: '内部群聊', department: '产品设计部', description: '分享灵感，碰撞新的可能。', unread: 1, messages: [
    { id: 'd1', sender: '周予安', initials: '予', color: 'rose', time: at(8, 45), text: '分享一组最近整理的界面参考。更轻的分割线和适当的留白，阅读起来会舒服很多。' },
    { id: 'd2', sender: '林晓', initials: '晓', color: 'teal', time: at(9, 5), text: '很喜欢这组蓝灰色搭配，安静但不冷淡。' },
  ] },
  { id: 'zhou', name: '周予安', initials: '予', color: 'rose', type: 'person', role: '项目经理', department: '项目管理部', description: '让每一件事，都有清晰的进展。', unread: 0, messages: [
    { id: 'z1', sender: '周予安', time: at(17, 25, -1), text: '本周项目进度已更新，明天有空可以一起确认一下排期。' },
    { id: 'z2', sender: '我', self: true, time: at(17, 28, -1), text: '好的，明早确认后给你反馈。', read: '本地演示消息' },
  ] },
  { id: 'assistant', name: '应用通知', initials: 'bell', color: 'service', type: 'service', role: '应用助手', department: '系统服务', description: '工作动态，及时送达。', unread: 0, messages: [
    { id: 'a1', sender: '应用通知', time: at(8, 30), text: '欢迎来到企微消息工作台。\n\n这里可以集中查看应用消息、搜索会话与管理本地未读状态。点击右上角「数据源」，了解如何连接企业微信应用回调。' },
  ] },
];

function readLocal(key, fallback) { try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; } }
let preferenceSaveTimer;
function flushPreferences(){clearTimeout(preferenceSaveTimer);try{localStorage.setItem('wecom-workbench-state-v1',JSON.stringify(state.localState));}catch{toast('浏览器存储不可用，本次更改仅保留在当前页面');}}
function saveLocal(key, value) {
  if(key==='wecom-workbench-state-v1'){clearTimeout(preferenceSaveTimer);preferenceSaveTimer=setTimeout(flushPreferences,200);return;}
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { toast('浏览器存储不可用，本次更改仅保留在当前页面'); }
}
const demo = structuredClone(demoConversations);
const savedMessages = readLocal('wecom-workbench-demo-messages-v1', {});
for (const conversation of demo) {
}

const platformSelection=new URLSearchParams(location.search).get('platform')==='wecom'?'wecom':'wechat';
const state = {
  platform:platformSelection, official:false,
  mode: window.desktopInbox?'live':'demo', liveAvailable: false, conversations: window.desktopInbox?[]:demo, selected: window.desktopInbox?'':'project', filter: 'all', search: '', view: 'messages',
  localState: readLocal('wecom-workbench-state-v1', {}), drafts: {}, cursor: 0, updateCursor: null, lastSync: null,
};
let toastTimer;
let historyLoad = null;
let historyIncomplete = false;
let messageStream = null;
let receiverStatus = null;
let streamConnected = false;
let streamError = '';
let retentionTimer;
let avatars = new Map();
let socialRevision = null;
let momentOffset = null;
let momentsRequest = null;
let messageRenderSignature='';
let messageWindow={id:null,start:null};
let renderFrame=0,renderPending=false;
function requestRender(){renderPending=true;if(document.hidden||renderFrame)return;renderFrame=requestAnimationFrame(()=>{renderFrame=0;renderPending=false;renderAll();});}
const selectedConversation = () => state.conversations.find(c => c.id === state.selected);
const preference = c => state.localState[`${state.mode}:${c.id}`] || {};
const pinned = c => preference(c).pinned ?? c.pinned ?? false;
const unread = c => preference(c).unread ?? c.unread ?? 0;
function setPreference(conversation, value) {
  state.localState[`${state.mode}:${conversation.id}`] = { ...preference(conversation), ...value };
  saveLocal('wecom-workbench-state-v1', state.localState);
}
function avatarContent(c) {
  const glyph = ['group', 'bell'].includes(c.initials) ? icon(c.initials === 'group' ? 'contacts' : 'bell') : escapeHtml(c.initials || (c.name || '?').slice(0, 1));
  const id = c.avatar || avatars.get(c.avatarKey || c.id?.replace(/^conversation:/, ''));
  return /^[a-f0-9]{64}$/.test(id || '') ? `<span class="avatar-fallback">${glyph}</span><img class="avatar-photo" src="/api/media?id=${id}" alt="" loading="lazy" decoding="async">` : glyph;
}
function avatarHtml(c, className = '') {
  return `<div class="avatar ${escapeHtml(c.color || 'slate')} ${className}">${avatarContent(c)}</div>`;
}
function applyAvatar(element, c, extra = '') {
  element.className = `avatar ${c.color || 'slate'} ${extra}`;
  const html = avatarContent(c);
  if (element.innerHTML !== html) element.innerHTML = html;
}
const timeFormat=new Intl.DateTimeFormat('zh-CN',{hour:'2-digit',minute:'2-digit',hour12:false});
const dayFormat=new Intl.DateTimeFormat('zh-CN',{month:'long',day:'numeric'});
function timeLabel(timestamp) { return timestamp==null||!Number.isFinite(new Date(timestamp).getTime())?'—':timeFormat.format(new Date(timestamp)); }
function dayLabel(timestamp) {
  if (dateKey(timestamp) === dateKey(Date.now())) return '今天';
  if (dateKey(timestamp) === dateKey(Date.now() - 86400000)) return '昨天';
  return dayFormat.format(timestamp);
}
function shortTime(timestamp) { if (!timestamp) return '时间未显示'; return dateKey(timestamp) === dateKey(Date.now()) ? timeLabel(timestamp) : dayLabel(timestamp); }
function preview(message) { return message?.file ? `[文件] ${message.file.name}` : message?.document ? `[${message.document.kind==='spreadsheet'?'在线表格':'在线文档'}] ${message.document.title}` : message?.sticker ? '[表情包]' : message?.text?.replace(/\n/g, ' ') || message?.quote?.text?.replace(/\n/g, ' ') || '暂无消息'; }
function searchPreview(message) { return message?[preview(message),message.quote?.sender,message.quote?.text,message.card?.description,message.record?.summary,...(message.record?.entries||[]).map(row=>`${row.sender||''} ${row.text||''}`)].filter(Boolean).join('\n'):''; }
function lastMessage(c) { return conversationSummary(c).last || c.messages.at(-1); }
function sortedConversations() { return state.conversations.map(c=>({c,pin:Number(pinned(c)),time:lastMessage(c)?.time||0})).sort((a,b)=>b.pin-a.pin||b.time-a.time).map(row=>row.c); }

function renderList() {
  const query = state.search.trim().toLowerCase();
  const list = sortedConversations().filter(c => (state.filter !== 'unread' || unread(c) > 0) && (state.filter !== 'pinned' || pinned(c)) && (!query || (searchResults?.query===query?searchResults.matches.has(c.id):c.name.toLowerCase().includes(query))));
  $('#all-count').textContent = state.conversations.length;
  $('#unread-count').textContent = state.conversations.filter(c => unread(c) > 0).length;
  $('#conversation-total').textContent = `${state.conversations.length} 段会话 · ${state.mode === 'snapshot' ? '客户端快照' : state.mode === 'demo' ? '演示工作区' : '应用回调消息'}`;
  const listElement=$('#conversation-list');
  const html=list.length ? list.map(c => {
    const last = lastMessage(c);
    return `<button class="conversation ${c.id === state.selected ? 'selected' : ''}" data-conversation="${escapeHtml(c.id)}" ${c.id === state.selected ? 'aria-current="true"' : ''}>${avatarHtml(c)}<div class="conv-content"><div class="conv-top"><strong class="conv-title">${escapeHtml(c.name)}</strong><time class="conv-time">${last ? escapeHtml(shortTime(last.time)) : ''}</time></div><div class="conv-bottom"><span class="conv-preview">${escapeHtml(c.type === 'group' && last && !last.self ? `${last.sender}：${preview(last)}` : preview(last))}</span>${unread(c) ? `<span class="unread-badge">${Math.min(unread(c), 99)}</span>` : ''}</div></div>${pinned(c) ? `<span class="pin-mark">${icon('pin')}</span>` : ''}</button>`;
  }).join('') : `<div class="empty-state">${icon('search')}<strong>${query ? searchResults?.query!==query?'正在搜索消息…':'没有找到相关会话' : state.filter === 'unread' ? '消息都读完了' : state.filter === 'pinned' ? '还没有置顶会话' : '暂无会话'}</strong><p>${query ? '试试联系人名称或消息里的关键词' : state.mode === 'live' ? '保持客户端登录，收到的消息会显示在这里' : '新的消息会显示在这里'}</p></div>`;
  if(listHtml!==html){listHtml=html;listElement.innerHTML=html;}
}
let listHtml;
let searchResults=null, searchRun=0;

async function searchConversations(){
  const query=state.search.trim().toLowerCase(),run=++searchRun,platform=state.platform,official=state.official,mode=state.mode;
  if(!query){searchResults=null;renderList();return;}
  const matches=new Set();
  const matchesSame=()=>run===searchRun&&platform===state.platform&&official===state.official&&mode===state.mode&&query===state.search.trim().toLowerCase();
  if(searchResults?.query!==query){renderList();$('#conversation-total').textContent='正在搜索本机消息…';}
  for(const c of state.conversations){
    if(!matchesSame())return;
    if(c.name.toLowerCase().includes(query)){matches.add(c.id);continue;}
    const length=c.messages.length;
    let start=performance.now();
    for(let i=length-1;i>=0;i--){
      if(searchPreview(c.messages[i]).toLowerCase().includes(query)){matches.add(c.id);break;}
      if(i%128===0&&performance.now()-start>=8){
        await (globalThis.scheduler?.yield?.()??new Promise(resolve=>setTimeout(resolve,0)));
        if(!matchesSame())return;
        start=performance.now();
      }
    }
  }
  if(matchesSame()){searchResults={query,matches};renderList();updateReceiveStatus();}
}

function renderMessages(c) {
  const scroll = $('#message-scroll');
  if (!c) { messageRenderSignature='';messageWindow={id:null,start:null};scroll.innerHTML = `<div class="empty-state">${icon('message')}<strong>还没有会话</strong><p>应用回调消息到达后会自动显示</p></div>`; return; }
  const changedConversation=messageWindow.id!==`${state.mode}:${c.id}`;
  if(changedConversation)messageWindow={id:`${state.mode}:${c.id}`,start:null};
  const page=messagePage(c.messages,messageWindow.start);
  const signature=[state.mode,c.id,c.revision??c.messages.length,c.messages.at(-1)?.id,page.first,page.end].join(':');
  if(signature===messageRenderSignature)return;
  const previousScroll=scroll.scrollTop,atBottom=scroll.scrollHeight-scroll.scrollTop-scroll.clientHeight<80;
  messageRenderSignature=signature;
  const controls=c.messages.length>100?`<div class="history-controls"><button class="small-button" data-history="older" ${page.first===0?'disabled':''}>更早 100 条</button><span>第 ${page.first+1}–${page.end} 条 / 共 ${c.messages.length} 条</span><button class="small-button" data-history="newer" ${page.end>=c.messages.length?'disabled':''}>较新 100 条</button><button class="small-button" data-history="latest">最新</button></div>`:'';
  let lastDay = '';
  scroll.innerHTML = controls+page.messages.map(m => {
    let divider = '';
    const messageDay = m.time ? dateKey(m.time) : 'unknown';
    if (messageDay !== lastDay) { lastDay = messageDay; divider = `<div class="date-divider">${escapeHtml(m.time ? dayLabel(m.time) : '客户端可见内容 · 部分时间未显示')}</div>`; }
    const anchor=`id="chat-message-${escapeHtml(m.id)}" tabindex="-1"`;
    if (m.system) return `${divider}<div class="system-event" ${anchor}><span>${escapeHtml(m.text)}</span></div>`;
    const sender = m.self ? { name: '我', initials: '我', color: 'slate', avatarKey: m.avatarKey } : { name: m.sender || c.name, initials: m.initials || (c.type === 'group' ? m.sender?.slice(0, 1) : c.initials), color: m.color || c.color, avatarKey: m.avatarKey };
    const original=quoteOriginal(c,m.quote?.target,m.id);
    const content = richContent(m,{escapeHtml,icon,attachmentCard}) + quoteContent(m.quote,original,m,{escapeHtml}) + (m.transcription_note ? `<p class="transcription-note">${escapeHtml(m.transcription_note)}</p>` : '');
    return `${divider}<article class="message ${m.self ? 'self' : ''}" ${anchor}>${avatarHtml(sender)}<div class="message-body"><div class="message-meta"><span>${escapeHtml(sender.name)}</span><time ${m.time ? `datetime="${new Date(m.time).toISOString()}"` : ''}>${escapeHtml(m.time_label || (m.time ? timeLabel(m.time) : '原时间未显示'))}</time></div><div class="bubble">${content}</div>${m.self ? `<div class="message-read">${escapeHtml(m.read || (state.mode === 'snapshot' ? '客户端已发送消息 · 快照' : '本机已发送记录'))}</div>` : ''}</div></article>`;
  }).join('');
  requestAnimationFrame(() => { if(messageRenderSignature!==signature)return;scroll.scrollTop=changedConversation||atBottom&&messageWindow.start===null?scroll.scrollHeight:previousScroll; });
}

function renderChat() {
  const c = selectedConversation();
  $('#chat-title').textContent = c?.name || '选择一段会话';
  $('#chat-subtitle').innerHTML = c ? `${state.mode === 'demo' ? '<i class="online-dot"></i>' : ''}${escapeHtml(state.mode === 'live' ? '应用回调 · 按发送人归集' : c.type === 'group' ? `${c.memberCount} 位成员 · 内部协作` : `${c.department} · ${c.role}`)}` : '等待应用回调';
  applyAvatar($('#chat-avatar'), c || { name: '企', initials: 'bell', color: 'service' });
  $('#pin-button').classList.toggle('is-pinned', Boolean(c && pinned(c)));
  $('#pin-button').setAttribute('aria-label', c && pinned(c) ? '取消置顶此会话' : '置顶此会话');
  renderMessages(c);
  if(!state.localSource)renderDetails(c);
  else if($('#details-panel').getClientRects().length)renderDetailStats(c);
  if (state.mode === 'snapshot') {
    $('#chat-subtitle').textContent = `客户端快照 · ${c?.type === 'group' ? `${c.memberCount ?? '未知'} 位群成员` : '真实会话'} · 只读`;
    $('#chat-notice-text').textContent = `采集于 ${new Date(state.snapshot.captured_at).toLocaleString('zh-CN')} · 非实时同步 · ${state.snapshot.status_note}`;
    $('#detail-tag').textContent = c?.type === 'group' ? '真实内部群 · 快照' : '真实会话 · 快照';
    $('#detail-info').innerHTML = [
      ['来源', '企业微信客户端界面'], ['采集时间', new Date(state.snapshot.captured_at).toLocaleString('zh-CN')],
      ['覆盖范围', '仅查看到的可见消息'], ['状态', state.snapshot.status_note],
    ].map(([key,value]) => `<div class="detail-row"><dt>${escapeHtml(key)}</dt><dd>${escapeHtml(value)}</dd></div>`).join('');
  }
}

function renderDetailStats(c){
  const data=c&&conversationSummary(c);
  $('#stat-total').textContent=data?.count||0;
  $('#stat-today').textContent=data?.days.get(dateKey(Date.now()))||0;
}

function renderDetails(c) {
  if(!$('#details-panel').getClientRects().length)return;
  const fallback = { name: '等待连接', initials: 'bell', color: 'service' };
  applyAvatar($('#detail-avatar'), c || fallback, 'profile-avatar');
  $('#detail-name').textContent = c?.name || '等待第一条消息';
  $('#detail-tag').textContent = state.mode === 'live' ? '应用消息' : c?.role || '工作区';
  $('#detail-description').textContent = c?.description || '企业微信推送给应用的消息，按发送人归集。';
  $('#detail-info').innerHTML = [
    [state.mode === 'live' ? '发送人 ID' : '所属部门', c?.department || '—'],
    ['会话类型', state.mode === 'live' ? '应用回调会话' : c?.type === 'group' ? '内部群聊' : c?.type === 'service' ? '应用服务' : '成员会话'],
    ['数据来源', state.mode === 'demo' ? '本地演示' : '企业微信回调'],
    ['当前状态', state.mode === 'demo' ? '演示预览' : state.lastSync ? '已同步' : '等待同步'],
  ].map(([label, value], index) => `<div class="detail-row"><dt>${escapeHtml(label)}</dt><dd class="${index === 3 ? 'good' : ''}">${escapeHtml(value)}</dd></div>`).join('');
  renderDetailStats(c);
}

function renderContacts() {
  const query = $('#contact-search').value.trim().toLowerCase();
  const people = state.conversations.filter(c => c.type === 'person' && `${c.name} ${c.department}`.toLowerCase().includes(query));
  $('#contact-cards').innerHTML = people.length ? people.map(c => `<button class="contact-card" data-conversation="${escapeHtml(c.id)}">${avatarHtml(c)}<h3>${escapeHtml(c.name)}</h3><p>${escapeHtml(c.role)} · ${escapeHtml(c.department)}</p><div class="contact-footer"><span>查看会话</span>${icon('arrow-right')}</div></button>`).join('') : `<div class="empty-state">${icon('contacts')}<strong>暂无匹配联系人</strong><p>${state.mode === 'live' ? '这里按已接收消息的发送人显示，不是完整企业通讯录' : '试试其他名称'}</p></div>`;
}

function renderOverview() {
  let total=0,todayCount=0;
  const day=dateKey(Date.now());
  for(const conversation of state.conversations){const stats=conversationSummary(conversation);total+=stats.count;todayCount+=stats.days.get(day)||0;}
  const stats = [['message', total, '消息总数'], ['clock', todayCount, '今日消息'], ['contacts', state.conversations.length, '会话数量'], ['mail', state.conversations.filter(c => unread(c)).length, '未读会话']];
  $('#overview-stats').innerHTML = stats.map(([name, value, label]) => `<div class="overview-stat"><span class="stat-icon">${icon(name)}</span><div><strong>${value}</strong><small>${label}</small></div></div>`).join('');
  $('#recent-conversations').innerHTML = sortedConversations().slice(0, 4).map(c => `<button class="recent-row" data-conversation="${escapeHtml(c.id)}">${avatarHtml(c)}<div><strong>${escapeHtml(c.name)}</strong><p>${escapeHtml(preview(lastMessage(c)))}</p></div><span>${lastMessage(c) ? shortTime(lastMessage(c).time) : ''}</span></button>`).join('') || '<div class="empty-state">暂无会话</div>';
  $('#overview-connection-title').textContent = state.mode === 'demo' ? '演示工作区' : '企业微信应用回调';
  $('#overview-connection-description').textContent = state.mode === 'demo' ? '使用只读示例消息预览工作台' : `上次收到消息 ${state.lastSync ? timeLabel(state.lastSync) : '—'} · 长连接推送`;
}

function renderAll() {
  if(state.view==='messages'){renderList();renderChat();}
  else if(state.view==='contacts')renderContacts();
  else if(state.view==='overview')renderOverview();
  $('#workspace-name').textContent = state.mode === 'demo' ? '星河科技' : '企业微信应用';
  $('#sync-label').textContent = state.mode === 'demo' ? '只读演示' : '等待接入';
  $('.sync-label').className = `sync-label ${state.mode === 'live' ? 'live' : ''}`;
  $('#inbox-footer-text').textContent = state.mode === 'demo' ? '演示工作区 · 内容仅供预览' : '应用回调 · 真实数据 · 只读';
  if (state.mode === 'snapshot') {
    $('#workspace-name').textContent = '我的企业微信';
    $('#sync-label').textContent = '客户端快照'; $('.sync-label').className = 'sync-label';
    $('#inbox-footer-text').textContent = '客户端快照 · 本机保存 · 非实时';
    $('#overview-connection-title').textContent = '企业微信客户端快照';
    $('#overview-connection-description').textContent = `采集 ${new Date(state.snapshot.captured_at).toLocaleString('zh-CN')} · 不自动采集`;
  }
  updateReceiveStatus();
}

function selectConversation(id) {
  if (!state.conversations.some(c => c.id === id)) return;
  state.selected = id;
  setPreference(selectedConversation(), { unread: 0 });
  switchView('messages');
  $('#messages-view').classList.add('mobile-chat');
}
function switchView(view) {
  if (view !== 'moments' && state.view === 'moments') momentsRequest?.abort();
  state.view = view;
  for (const value of ['messages', 'contacts', 'overview', 'groups', 'moments']) { const panel = $(`#${value}-view`); if (panel) panel.hidden = value !== view; }
  $$('.nav-button[data-view]').forEach(button => button.classList.toggle('active', button.dataset.view === view));
  if (view === 'messages') {renderList();renderChat();}
  if (view === 'contacts') renderContacts();
  if (view === 'overview') renderOverview();
  if (view === 'moments'&&!momentsRequest) loadMoments();
  updateReceiveStatus();
}
function momentCard(post) {
  const media = post.media || [];
  const images = media.filter(item => item.kind === 'image' && /^[a-f0-9]{64}$/.test(item.id || ''));
  const missing = media.length - images.length;
  const photos = images.map(item => `<button type="button" class="moment-photo" data-image-preview="${item.id}" aria-label="放大朋友圈图片"><img src="/api/media?id=${item.id}" alt="朋友圈图片" loading="lazy" decoding="async"></button>`).join('');
  const date = new Date(post.time * 1000);
  return `<article class="moment-card">${avatarHtml({name:post.author,avatar:post.avatar})}<div class="moment-body"><header><strong>${escapeHtml(post.author)}</strong><time>${escapeHtml(Number.isFinite(date.getTime()) ? date.toLocaleString('zh-CN') : '时间未显示')}</time></header>${post.text ? `<p class="moment-text">${escapeHtml(post.text)}</p>` : ''}${post.title ? `<p class="moment-title">${escapeHtml(post.title)}</p>` : ''}${photos ? `<div class="moment-photos">${photos}</div>` : ''}${missing ? `<p class="moment-note">${missing} 项媒体暂无本机预览</p>` : ''}${post.likes?.length || post.comments?.length ? `<div class="moment-interactions">${post.likes?.length ? `<p>赞：${escapeHtml(post.likes.join('、'))}</p>` : ''}${(post.comments || []).map(comment => `<p><strong>${escapeHtml(comment.author)}</strong>：${escapeHtml(comment.text)}</p>`).join('')}</div>` : ''}</div></article>`;
}
async function loadMoments(more = false) {
  if (state.platform !== 'wechat' || !window.desktopInbox || (more && momentOffset === null)) return;
  momentsRequest?.abort();
  const request = momentsRequest = new AbortController();
  const offset = more ? momentOffset : 0;
  $('#moments-more').disabled = true;
  $('#moments-status').textContent = '正在读取本机朋友圈…';
  try {
    const response = await fetch(`/api/wechat/moments?offset=${offset}`, { signal: AbortSignal.any([request.signal, AbortSignal.timeout(5000)]) });
    if (!response.ok) throw new Error('读取失败');
    const {posts, ready, next_offset} = await response.json();
    request.signal.throwIfAborted();
    const feed = $('#moments-feed');
    if (!more) feed.replaceChildren();
    feed.insertAdjacentHTML('beforeend', posts.map(momentCard).join(''));
    momentOffset = next_offset;
    $('#moments-more').hidden = momentOffset === null;
    $('#moments-status').textContent = !ready ? '朋友圈缓存尚未就绪。打开微信朋友圈后，可稍后刷新。' : feed.children.length ? '' : '本机暂无可显示的朋友圈动态。';
  } catch {
    if (!request.signal.aborted) $('#moments-status').textContent = '暂时无法读取本机朋友圈，请重试。';
  } finally {
    if (momentsRequest === request) { momentsRequest = null; $('#moments-more').disabled = false; }
  }
}
async function updateAvatars(revision) {
  if (!window.desktopInbox || revision === socialRevision) return;
  socialRevision = revision;
  if (!revision) { avatars.clear(); messageRenderSignature = ''; requestRender(); return; }
  try {
    const response = await fetch('/api/wechat/avatars', {signal:AbortSignal.timeout(5000)});
    if (!response.ok) throw new Error('读取失败');
    const data = await response.json();
    if (socialRevision !== revision) return;
    avatars = new Map(data.avatars);
    messageRenderSignature = '';
    requestRender();
  } catch { if (socialRevision === revision) socialRevision = null; }
}
function toast(text) { clearTimeout(toastTimer); $('#toast').textContent = text; $('#toast').hidden = false; toastTimer = setTimeout(() => { $('#toast').hidden = true; }, 3300); }
function openConnection() { $('#connection-dialog').showModal(); }

async function useSnapshot() {
  stopStream();
  const response = await fetch('/api/client-snapshot', { cache: 'no-store', signal: AbortSignal.timeout(5000) });
  if (!response.ok) throw new Error('客户端快照读取失败');
  const snapshot = await response.json();
  state.snapshot = snapshot; state.mode = 'snapshot'; state.conversations = snapshot.conversations;
  if (!selectedConversation()) state.selected = state.conversations[0]?.id || '';
  renderAll();
}

function normalizeEvent(event) {
  const m = event.message || {};
  const type = String(m.MsgType || 'event');
  const rawTime = Number(m.CreateTime) * 1000;
  const quote = normalizeQuote(m.Quote);
  let text;
  if (type === 'text') text = String(m.Content ?? '').trim() ? String(m.Content) : quote ? '' : '[无可显示的文字]';
  else if (type === 'event') text = `[应用事件] ${m.Event || '未知事件'}${m.EventKey ? `\n事件参数：${m.EventKey}` : ''}${m.ChangeType ? `\n变更类型：${m.ChangeType}` : ''}`;
  else if (type === 'image') text = `[图片消息]\n媒体 ID：${m.MediaId || '未提供'}\n需要通过企业微信媒体接口获取原文件。`;
  else if (type === 'voice') text = `[语音消息]${m.Recognition ? `\n${m.Recognition}` : '\n暂不提供语音播放'}`;
  else if (type === 'video') text = '[视频消息]\n暂不提供视频播放';
  else if (type === 'location') text = `[位置消息] ${m.Label || ''}\n${m.Location_X || ''}, ${m.Location_Y || ''}`;
  else if (type === 'link') text = `[链接消息] ${m.Title || ''}\n${m.Description || ''}\n${m.Url || ''}`;
  else text = `[${type} 消息]\n${m.Content || '当前仅展示消息类型，可导出会话查看原始字段。'}`;
  const account = m.Platform === 'wechat' ? /^wechat:([^:]+):/.exec(m.ConversationId)?.[1] : null;
  const avatarKey = account && m.FromUserName?.startsWith('wechat:') ? `wechat:${account}:${m.FromUserName.slice(7)}` : '';
  const card=m.Card||(type==='link'?{title:m.Title,description:m.Description,url:m.Url}:null)||(m.Platform==='wechat'&&!m.Articles?.length&&cachedShareCard(m.NativeTypeCode,text))||null;
  return { id: `live-${event.id}`, sequence: event.id, avatarKey, quote, referenceKey:referenceKey(m.MessageReference), self:m.IsSelf===true, articles:m.Articles, record:m.ChatRecord, card, system:m.System===true, file:normalizeAttachment(m.Attachment), video:m.Video, document:m.Document, sticker:m.Sticker, image:m.Image, sender: String(m.SenderName || m.FromUserName || '系统事件'), time: Number.isFinite(rawTime) && rawTime > 0 && rawTime <= 8640000000000000 ? rawTime : event.received_at, text, raw: m };
}
function eventConversationId(message={}){return message.ConversationId?`conversation:${message.ConversationId}`:`sender:${message.FromUserName||'系统事件'}`;}
const liveIndex=new LiveConversationIndex({normalize:normalizeEvent,key:eventConversationId,create:(event,m,id)=>{
      const colors = ['teal', 'slate', 'purple', 'amber', 'rose'];
      let hash = 0; for (const char of m.sender) hash = (hash + char.codePointAt(0)) % 5;
      const local=event.message?.Source==='local-database';
      const name=event.message?.ConversationName||m.sender;
      const type=event.message?.ConversationType||'person';
      return { id, platform:event.message?.Platform==='wechat'?'wechat':'wecom', official:event.message?.ConversationType==='official', name:name.replace(/^\[微信\] /,''), initials:type==='group'?'group':name.replace(/^\[微信\] /,'').slice(0,1), color: colors[hash], type, role:local?'本机真实会话':'应用消息发送人', department:local?(event.message?.Platform==='wechat'?'微信本机缓存':'企业微信本机缓存'):m.sender, description:local?'从当前账号本地缓存读取，消息变化后自动更新。':'按回调 userid 归集。', unread: 0 };
}});
function buildLiveConversations() {state.conversations=[...liveIndex.groups.values()].filter(c=>c.platform===state.platform&&(state.platform!=='wechat'||c.official===state.official));}
function disconnectStream(){
  messageStream?.close();messageStream=null;streamConnected=false;streamError='';
  clearInterval(retentionTimer);
}
function stopStream() {
  historyLoad?.abort(); historyLoad = null;
  historyIncomplete=false;disconnectStream();
}
function updateLiveView() {
  buildLiveConversations();
  if (!selectedConversation()) state.selected = sortedConversations()[0]?.id || '';
  if(state.search.trim())queueSearch(120);
  requestRender();
}
function expireLiveEvents() {
  if(state.mode==='live'&&liveIndex.expire(Date.now()-(state.retentionDays||30)*86400000))updateLiveView();
}
function updateReceiveStatus() {
  const label = $('#receive-status');
  if (state.mode === 'snapshot') { label.textContent = '历史客户端快照 · 非实时 · 不提供发送'; return; }
  if (state.mode === 'demo') { label.textContent = '只读演示 · 不提供发送'; return; }
  if(state.localSource){
    const wechat = state.platform === 'wechat';
    const receiverError = wechat ? receiverStatus?.wechat?.error : receiverStatus?.error;
    const healthy = streamConnected && !streamError && !receiverError && (wechat ? receiverStatus?.wechat?.status === '微信本机监听中' : receiverStatus?.receiver_running);
    const fallback = wechat ? receiverStatus?.wechat?.fallback : receiverStatus?.fallback;
    const text = streamError || (!streamConnected ? '消息通道重连中' : receiverError || (healthy && fallback ? '本机消息自动补查中' : '') || (wechat && receiverStatus?.wechat?.status) || (healthy ? '本机消息监听中' : '正在读取本机消息'));
    $('#sync-label').textContent=text;$('.sync-label').className=`sync-label ${healthy?'live':'error'}`;
    $('#workspace-name').textContent=state.platform==='wechat'?(state.official?'微信 · 公众号':'微信'):'企业微信';
    $('#conversation-total').textContent=state.search.trim()&&searchResults?.query!==state.search.trim().toLowerCase()?'正在搜索本机消息…':`${state.conversations.length} 段会话 · 本机真实消息`;
    $('#chat-subtitle').textContent=`${selectedConversation()?.type==='group'?'群聊':selectedConversation()?.type==='service'?'应用/文档通知':'私聊'} · 本机缓存 · 只读`;
    $('#chat-notice-text').textContent=`${text} · ${historyLoad?'正在补齐较早消息，可先查看会话':historyIncomplete?'历史加载中断，点击刷新重试':'新增消息写入本机后自动更新'}`;
    $('#inbox-footer-text').textContent='本机数据库 · 自动更新 · 只接收';
    $('#detail-tag').textContent='本机真实会话';
    $('#detail-description').textContent='图片显示已缓存的原图或缩略图；PDF 等附件支持打开。在线表格与表情已接入，语音暂以摘要显示。';
    $('#detail-info').innerHTML=[['会话类型',selectedConversation()?.type==='group'?'群聊':'私聊/应用'],['来源',state.platform==='wechat'?'微信本机数据库':'企业微信本机数据库'],['更新方式','文件变化自动读取'],['当前状态',text]].map(([key,value])=>`<div class="detail-row"><dt>${escapeHtml(key)}</dt><dd>${escapeHtml(value)}</dd></div>`).join('');
    $('#overview-connection-title').textContent=state.platform==='wechat'?'微信本机接收':'企业微信本机接收';
    $('#overview-connection-description').textContent='文件变化触发读取 · 只显示本机已缓存记录';
    label.textContent=`${text} · 不提供发送`;document.querySelector('.workspace-avatar').textContent=state.platform==='wechat'?'微':'企';document.querySelector('.inbox-heading h1').textContent=state.official?'公众号':'消息';return;
  }
  const text = streamError || (!streamConnected ? '消息通道重连中' : !receiverStatus?.receiver_running ? '等待回调接收服务' : !receiverStatus.last_verified_at ? '接收服务运行中 · 等待后台回调验证' : '实时接收 · 回调已验证');
  $('#sync-label').textContent = text;
  $('.sync-label').className = `sync-label ${streamConnected && !streamError && receiverStatus?.receiver_running && receiverStatus.last_verified_at ? 'live' : 'error'}`;
  $('#chat-notice-text').textContent = `${text} · 仅接收发给应用的消息；员工私聊和内部群需另行接入会话存档`;
  label.textContent = `${text} · 只接收消息`;
}
function startStream() {
  disconnectStream();
  receiverStatus = null;
  const updateQuery = state.updateCursor === null ? '' : `&after_update_id=${state.updateCursor}`;
  messageStream = new EventSource(`/api/stream?after_id=${state.cursor}${updateQuery}`);
  retentionTimer = setInterval(expireLiveEvents, 60000);
  messageStream.onopen = () => { streamConnected = true; updateReceiveStatus(); };
  messageStream.onerror = () => { streamConnected = false; receiverStatus = null; streamError = ''; updateReceiveStatus(); };
  messageStream.addEventListener('receiver-status', event => {
    receiverStatus = JSON.parse(event.data); streamError = ''; updateReceiveStatus();
    const revision = receiverStatus.wechat?.social_revision || '';
    if (state.view === 'moments' && socialRevision !== revision) {
      $('#moments-feed').replaceChildren();
      momentOffset = null;
      $('#moments-more').hidden = true;
      loadMoments();
    }
    updateAvatars(revision);
  });
  messageStream.addEventListener('receiver-error', event => {
    streamError = JSON.parse(event.data).error; updateReceiveStatus();
  });
  messageStream.addEventListener('message-updates',event=>{
    if(state.mode!=='live')return;
    const data = JSON.parse(event.data);
    state.updateCursor = Math.max(state.updateCursor, data.next_after_update_id);
    if (!data.events.length) return;
    // Some revised rows may arrive before their background history page.
    // Keep the live version and never mark a revision as a new unread message.
    liveIndex.ingest(data.events);
    if(openRecordId&&data.events.some(event=>`live-${event.id}`===openRecordId))renderOpenRecord();
    state.lastSync=Date.now();updateLiveView();
  });
  messageStream.addEventListener('messages', event => {
    if (state.mode !== 'live') return;
    const data = JSON.parse(event.data);
    for (const item of liveIndex.ingest(data.events,{replace:false})) {
      const conversationId = eventConversationId(item.message);
      const key = `live:${conversationId}`;
      const platform = item.message.Platform === 'wechat' ? 'wechat' : 'wecom';
      const isVisible = platform === state.platform && conversationId === state.selected && state.view === 'messages' && !document.hidden && $('#message-scroll').getClientRects().length;
      if (item.message.System !== true && item.message.IsSelf !== true && !isVisible) {
        state.localState[key] = { ...state.localState[key], unread: (state.localState[key]?.unread || 0) + 1 };
      }
    }
    state.cursor = Math.max(state.cursor, data.next_after_id || 0);
    if (state.updateCursor === null) state.updateCursor = data.next_after_update_id;
    state.lastSync = Date.now(); streamConnected = true;
    updateLiveView();
    saveLocal('wecom-workbench-state-v1', state.localState);
  });
}
async function loadLiveHistory(signal,through) {
  let cursor=0,more=through>0,lastPaint=performance.now();
  while(more) {
    signal.throwIfAborted();
    const response = await fetch(`/api/messages?after_id=${cursor}&through_id=${through}&limit=600`, { signal: AbortSignal.any([signal, AbortSignal.timeout(8000)]), cache: 'no-store' });
    if (!response.ok) throw new Error('历史消息读取失败');
    const data = await response.json();
    signal.throwIfAborted();
    for(let start=0;start<data.events.length;start+=100){
      signal.throwIfAborted();
      liveIndex.ingest(data.events.slice(start,start+100),{replace:false});
      if(start+100<data.events.length&&performance.now()-lastPaint>16){
        if(performance.now()-lastPaint>350){updateLiveView();lastPaint=performance.now();}
        await (globalThis.scheduler?.yield?.()??new Promise(resolve=>setTimeout(resolve,0)));
      }
    }
    if(data.has_more&&data.next_after_id<=cursor)throw new Error('历史消息游标无效');
    cursor=data.next_after_id;
    more = data.has_more === true;
    if(performance.now()-lastPaint>350){updateLiveView();lastPaint=performance.now();}
    if(more)await new Promise(resolve=>setTimeout(resolve,8));
  }
  state.lastSync = Date.now();
}
async function useLive() {
  if (!state.localBackend) { toast('请运行本地工作台服务后连接实时接收'); return; }
  stopStream();
  const previousSelection=state.mode==='live'?state.selected:'';
  state.mode='live';state.selected=previousSelection;state.cursor=0;state.updateCursor=null;
  liveIndex.clear();state.conversations=[];
  const loading = historyLoad = new AbortController();
  updateReceiveStatus();
  try {
    if(state.liveAvailable){
      const response=await fetch('/api/inbox-preview',{signal:AbortSignal.any([loading.signal,AbortSignal.timeout(8000)]),cache:'no-store'});
      if(!response.ok)throw new Error('会话预览读取失败');
      const data=await response.json();loading.signal.throwIfAborted();
      liveIndex.ingest(data.events,{replace:false});
      state.cursor=data.history_through_id;state.updateCursor=data.update_watermark;
    }
    updateLiveView();startStream();$('#connection-dialog').close();
    await loadLiveHistory(loading.signal,state.cursor);
  }catch{
    if(!loading.signal.aborted){historyIncomplete=true;toast('历史消息读取中断，点击刷新可重试');}
  }finally{
    if(historyLoad===loading){historyLoad=null;updateLiveView();}
  }
}
function useDemo() {
  stopStream();
  state.mode = 'demo'; state.conversations = demo; state.selected = demo[0].id; state.search = ''; searchRun++;searchResults=null; $('#search-input').value = '';
  renderAll(); $('#connection-dialog').close();
}

const imageDialog=$('#image-dialog'),largeImage=$('#large-chat-image'),imageNotice=$('#image-preview-status');
largeImage.addEventListener('load',()=>{imageNotice.hidden=true;largeImage.hidden=false;});
largeImage.addEventListener('error',()=>{imageNotice.hidden=false;imageNotice.textContent='图片预览失败，可能是本机缓存已清理或格式不受支持。';largeImage.hidden=true;});
imageDialog.addEventListener('close',()=>largeImage.removeAttribute('src'));
imageDialog.addEventListener('click',event=>{if(event.target===imageDialog)imageDialog.close();});
const videoDialog=$('#video-dialog'),videoPlayer=$('#chat-video-player'),videoNotice=$('#video-preview-status'),videoExternal=$('#video-external');
videoPlayer.addEventListener('loadedmetadata',()=>{if(videoDialog.open){videoPlayer.hidden=false;videoNotice.hidden=true;}});
videoPlayer.addEventListener('error',()=>{
 if(!videoDialog.open)return;
 videoPlayer.hidden=true;videoNotice.hidden=false;videoExternal.hidden=false;
 videoNotice.textContent='无法播放：原视频可能已从本机缓存移除，或当前播放器不支持这个编码。';
});
videoDialog.addEventListener('close',()=>{videoPlayer.pause();videoPlayer.removeAttribute('src');videoPlayer.load();videoExternal.removeAttribute('data-attachment-open');});
const audioDialog=$('#audio-dialog'),audioPlayer=$('#chat-audio-player'),audioNotice=$('#audio-preview-status'),audioExternal=$('#audio-external');
const recordDialog=$('#record-dialog');
let openRecordId=null;
recordDialog.addEventListener('close',()=>{openRecordId=null;});
function renderOpenRecord(){
  const record=(state.mode==='live'?liveIndex.events.get(Number(openRecordId?.slice(5)))?.message:selectedConversation()?.messages.find(m=>m.id===openRecordId))?.record;
  if(!record)return false;
  const body=$('#record-dialog-body'), expanded=[...body.querySelectorAll('details')].map(el=>el.open);
  const scroll=recordDialog.scrollTop;
  $('#record-dialog-title').textContent=record.title||'合并转发的聊天记录';
  const html=recordDetails(record,escapeHtml,{normalizeAttachment,attachmentCard,icon});
  if(body.innerHTML!==html){body.innerHTML=html;[...body.querySelectorAll('details')].forEach((el,i)=>{el.open=!!expanded[i];});recordDialog.scrollTop=scroll;}
  return true;
}
audioPlayer.addEventListener('loadedmetadata',()=>{if(audioDialog.open){audioPlayer.hidden=false;audioNotice.hidden=true;}});
audioPlayer.addEventListener('error',()=>{
 if(!audioDialog.open)return;
 audioPlayer.hidden=true;audioNotice.hidden=false;audioExternal.hidden=false;
 audioNotice.textContent='当前音频无法播放，请确认微信里仍能打开这个文件。';
});
audioDialog.addEventListener('close',()=>{audioPlayer.pause();audioPlayer.removeAttribute('src');audioPlayer.load();audioExternal.removeAttribute('data-attachment-open');});

const imageSrc=(id)=>`/api/media?id=${id}`;
function watchPreview(dialog,element,onSlow){
  let timer;
  const clear=()=>clearTimeout(timer);
  for(const name of ['load','loadedmetadata','error'])element.addEventListener(name,clear);
  dialog.addEventListener('close',clear);
  return ()=>{clear();timer=setTimeout(()=>{if(dialog.open)onSlow();},4500);};
}
const watchImage=watchPreview(imageDialog,largeImage,()=>{imageNotice.hidden=false;imageNotice.textContent='本机图片读取较慢，可以关闭此窗口后重试。';});
const watchVideo=watchPreview(videoDialog,videoPlayer,()=>{videoNotice.hidden=false;videoNotice.textContent='视频读取较慢，也可以用系统播放器打开。';videoExternal.hidden=false;});
const watchAudio=watchPreview(audioDialog,audioPlayer,()=>{audioNotice.hidden=false;audioNotice.textContent='音频读取较慢，也可以用系统播放器打开。';audioExternal.hidden=false;});

document.addEventListener('click', async event => {
  const audioButton=event.target.closest('[data-audio-preview]');
  if(audioButton){
    const id=audioButton.dataset.audioPreview;
    if(!/^[a-f0-9]{64}$/.test(id||''))return;
    $('#audio-dialog-title').textContent=audioButton.dataset.audioName||'音频播放';
    audioPlayer.hidden=true;audioExternal.hidden=true;audioExternal.dataset.attachmentOpen=id;
    audioNotice.hidden=false;audioNotice.textContent='正在载入本机音频…';
    if(!audioDialog.open)audioDialog.showModal();
    watchAudio();
    audioPlayer.src=`/api/audio?id=${id}`;
    audioPlayer.play().catch(()=>{});
    return;
  }
  const videoButton=event.target.closest('[data-video-preview]');
  if(videoButton){
    const id=videoButton.dataset.videoPreview;
    if(!/^[a-f0-9]{64}$/.test(id||''))return;
    $('#video-dialog-title').textContent=videoButton.dataset.videoName||'视频播放';
    videoPlayer.hidden=true;videoExternal.hidden=true;videoExternal.dataset.attachmentOpen=id;
    videoNotice.hidden=false;videoNotice.textContent='正在载入本机视频…';
    if(!videoDialog.open)videoDialog.showModal();
    watchVideo();
    videoPlayer.src=`/api/video?id=${id}`;
    videoPlayer.play().catch(()=>{});
    return;
  }
  const web=event.target.closest('a[data-web-link]');
  if(web){
    if(!window.desktopInbox?.openWebLink)return;
    event.preventDefault();
    try{const result=await window.desktopInbox.openWebLink(web.href);if(!result.ok)toast(result.error);}
    catch{toast('无法打开浏览器');}
    return;
  }
  const imageButton=event.target.closest('[data-image-preview]');
  if(imageButton){
    const id=imageButton.dataset.imagePreview;
    if(!/^[a-f0-9]{64}$/.test(id||''))return;
    const thumbnail=imageButton.querySelector('img');
    largeImage.alt=thumbnail?.alt||'聊天图片';largeImage.hidden=true;
    imageNotice.hidden=false;imageNotice.textContent='正在载入本机图片…';
    if(!imageDialog.open)imageDialog.showModal();watchImage();largeImage.src=imageSrc(id);
    return;
  }
  const recordButton=event.target.closest('[data-chat-record]');
  if(recordButton){
    openRecordId=recordButton.dataset.chatRecord;
    $('#record-dialog-body').replaceChildren();
    if(renderOpenRecord()&&!recordDialog.open)recordDialog.showModal();
    return;
  }
  const quoteJump=event.target.closest('[data-quote-target]');
  if(quoteJump){
    const c=selectedConversation();
    const original=c&&quoteOriginal(c,referenceKey(quoteJump.dataset.quoteTarget),quoteJump.dataset.quoteReply);
    if(!original){toast('原消息不在当前会话历史中');return;}
    const index=c.messages.indexOf(original),page=messagePage(c.messages,messageWindow.start);
    if(index<page.first||index>=page.end){
      messageWindow.start=Math.min(Math.floor(index/100)*100,page.latest);renderMessages(c);
    }
    requestAnimationFrame(()=>{
      if(selectedConversation()?.id!==c.id)return;
      const target=document.getElementById(`chat-message-${original.id}`);
      target?.focus({preventScroll:true});target?.scrollIntoView({block:'center'});
    });
    return;
  }
  const keepAttachment=event.target.closest('[data-attachment-keep]');
  if(keepAttachment){
    if(!window.desktopInbox?.keepAttachment){toast('请在桌面收件台中保留附件');return;}
    keepAttachment.disabled=true;
    try{
      const result=await window.desktopInbox.keepAttachment(keepAttachment.dataset.attachmentKeep);
      if(!result.ok){toast(result.error);return;}
      const status=keepAttachment.closest('.file-card')?.querySelector('[data-attachment-status]');
      if(status)status.textContent='已保留离线副本';
      keepAttachment.remove();
      toast('已保留离线副本');
    }catch{toast('未能保留附件');}finally{keepAttachment.disabled=false;}
    return;
  }
  const saveAttachment=event.target.closest('[data-attachment-save]');
  if(saveAttachment){
    if(!window.desktopInbox?.saveAttachment){toast('请在桌面收件台中另存附件');return;}
    saveAttachment.disabled=true;
    try{
      const result=await window.desktopInbox.saveAttachment(saveAttachment.dataset.attachmentSave);
      if(result.ok)toast('附件已保存');else if(!result.canceled)toast(result.error);
    }catch{toast('未能另存附件');}finally{saveAttachment.disabled=false;}
    return;
  }
  const retry=event.target.closest('[data-media-retry]');
  if(retry){const id=retry.dataset.mediaRetry;if(/^[a-f0-9]{64}$/.test(id)){const img=retry.closest('figure')?.querySelector('[data-local-image]');if(img)img.src=`/api/media?id=${id}&retry=${Date.now()}`;}return;}
  const documentCard=event.target.closest('[data-document-open]');
  if(documentCard){
    if(!window.desktopInbox?.openDocument){toast('请在桌面收件台中打开在线表格');return;}
    try{const result=await window.desktopInbox.openDocument(documentCard.dataset.documentOpen);if(!result.ok)toast(result.error);}catch{toast('在线文档打开失败');}return;
  }
  const attachment=event.target.closest('[data-attachment-open]');
  if(attachment){
    if(!window.desktopInbox?.openAttachment){toast('请在桌面收件台中打开附件');return;}
    attachment.disabled=true;
    const label=attachment.textContent;attachment.textContent='正在打开…';
    try{const result=await window.desktopInbox.openAttachment(attachment.dataset.attachmentOpen);if(!result.ok)toast(result.error);else if(result.revealed)toast('已在文件夹中显示附件');}
    catch{toast('附件打开失败，请确认文件已同步');}
    finally{attachment.disabled=false;attachment.textContent=label;}return;
  }
  const history=event.target.closest('[data-history]');
  if(history){const c=selectedConversation();if(!c)return;const page=messagePage(c.messages,messageWindow.start);messageWindow.start=history.dataset.history==='latest'?null:history.dataset.history==='older'?Math.max(0,page.first-100):Math.min(page.latest,page.first+100);if(messageWindow.start===page.latest)messageWindow.start=null;messageRenderSignature='';renderMessages(c);requestAnimationFrame(()=>{$('#message-scroll').scrollTop=history.dataset.history==='latest'?$('#message-scroll').scrollHeight:0;});return;}
  const conversation = event.target.closest('[data-conversation]');
  if (conversation) selectConversation(conversation.dataset.conversation);
});
document.addEventListener('error',event=>{
 const img=event.target;if(!(img instanceof HTMLImageElement))return;
 if(img.classList.contains('avatar-photo')){img.remove();return;}
 if(img.classList.contains('inline-emoji')){const fallback=document.createElement('span');fallback.className='emoji-fallback';fallback.textContent=img.alt||'[表情]';img.replaceWith(fallback);return;}
 if(img.dataset.localImage){const figure=img.closest('figure');const retry=figure?.querySelector('[data-media-retry]');const status=figure?.querySelector('[data-media-status]');if(retry)retry.hidden=false;if(status)status.hidden=true;img.title=`${img.alt}暂时无法加载`;}
},true);
document.addEventListener('load',event=>{const img=event.target;if(img instanceof HTMLImageElement&&img.dataset.localImage){const figure=img.closest('figure');const retry=figure?.querySelector('[data-media-retry]');const status=figure?.querySelector('[data-media-status]');if(retry)retry.hidden=true;if(status)status.hidden=false;img.removeAttribute('title');}},true);
$$('.nav-button[data-view]').forEach(button => button.addEventListener('click', () => {
  if(button.dataset.view==='messages'&&state.official)showOfficial(false);
  else switchView(button.dataset.view);
}));
$$('[data-filter]').forEach(button => button.addEventListener('click', () => {
  state.filter = button.dataset.filter;
  $$('[data-filter]').forEach(tab => { const active = tab === button; tab.classList.toggle('active', active); tab.setAttribute('aria-selected', String(active)); });
  renderList();
}));
let searchTimer;
function queueSearch(delay=80){clearTimeout(searchTimer);searchTimer=setTimeout(searchConversations,delay);}
$('#search-input').addEventListener('input', event => { state.search = event.target.value;++searchRun;searchResults=null;if(!state.search.trim()){renderList();updateReceiveStatus();}queueSearch(); });
$('#contact-search').addEventListener('input', renderContacts);
$('#pin-button').addEventListener('click', () => { const c = selectedConversation(); if (!c) return; const value = !pinned(c); setPreference(c, { pinned: value }); renderList(); $('#pin-button').classList.toggle('is-pinned', value); $('#pin-button').setAttribute('aria-label', value ? '取消置顶此会话' : '置顶此会话'); toast(value ? '已置顶此会话' : '已取消置顶'); });
$('#mark-all-button').addEventListener('click', () => { state.conversations.forEach(c => setPreference(c, { unread: 0 })); renderList(); toast('所有会话已标为已读'); });
$('#unread-button').addEventListener('click', () => { const c = selectedConversation(); if (!c) return; setPreference(c, { unread: 1 }); renderList(); toast('已标记为未读'); });
$('#details-button').addEventListener('click', () => {
  if (window.innerWidth <= 1190) $('#details-panel').classList.toggle('mobile-open');
  else $('#messages-view').classList.toggle('no-details');
  if(state.localSource&&$('#details-panel').getClientRects().length)renderDetailStats(selectedConversation());
  else if(!state.localSource)renderDetails(selectedConversation());
});
$('#back-button').addEventListener('click', () => $('#messages-view').classList.remove('mobile-chat'));
$('#refresh-button').addEventListener('click', async () => { if(window.desktopInbox&&!state.localBackend){await initializeBackend();return;} if (state.mode === 'snapshot') { try { await useSnapshot(); toast('已重新载入本机快照；未自动读取企业微信'); } catch(error) { toast(error.message); } } else if (state.mode === 'live') { if(historyLoad&&state.updateCursor===null)return;if(historyIncomplete){await useLive();return;}expireLiveEvents();startStream();updateReceiveStatus();toast('正在重新连接消息通道'); } else { renderAll(); toast('演示消息已刷新'); } });
for (const id of ['source-button', 'connect-card-button', 'overview-source-button', 'overview-connect-button']) $(`#${id}`).addEventListener('click', openConnection);
$('#demo-mode-button').addEventListener('click', useDemo);
$('#live-mode-button').addEventListener('click', useLive);
$('#overview-messages-button').addEventListener('click', () => switchView('messages'));
$('#profile-button').addEventListener('click', () => toast(state.mode === 'demo' ? '当前为演示工作区，消息不会发送到企业微信' : '当前连接本地应用回调，网页仅提供消息读取'));
$('#export-button').addEventListener('click', async event => {
  const button=event.currentTarget;
  if(button.disabled)return;
  const c = selectedConversation(); if (!c) { toast('当前没有可导出的会话'); return; }
  const source=state.mode;
  button.disabled=true;button.textContent='正在导出…';toast('正在准备会话文件，可以继续查看消息');
  try{
    await nextRender();
    const blob = await conversationExport(c,source);
    const link = document.createElement('a'); link.href = URL.createObjectURL(blob); link.download = `会话-${c.name.replace(/[\\/:*?"<>|]/g, '_')}.json`; link.click(); setTimeout(() => URL.revokeObjectURL(link.href), 1000); toast('会话已生成 JSON 文件');
  }catch{toast('无法导出会话，请重试');}finally{button.disabled=false;button.textContent='导出会话';}
});
document.addEventListener('keydown', event => {
  if (event.key === '/' && !['INPUT', 'TEXTAREA'].includes(document.activeElement.tagName) && !$('#connection-dialog').open) { event.preventDefault(); switchView('messages'); $('#messages-view').classList.remove('mobile-chat'); $('#search-input').focus(); }
  if (event.key === 'Escape') $('#details-panel').classList.remove('mobile-open');
});
$('#connection-dialog').addEventListener('click', event => { if (event.target === $('#connection-dialog')) { const rect = event.target.getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) event.target.close(); } });

let bootstrapInFlight=null;
function initializeBackend(){
 if(bootstrapInFlight)return bootstrapInFlight;
 bootstrapInFlight=(async()=>{
  const attempts=window.desktopInbox?4:1;
  for(let attempt=0;attempt<attempts;attempt++){
   try {
    if(window.desktopInbox){$('#sync-label').textContent='正在连接本机消息';$('#receive-status').textContent='正在连接，已保存的消息即将呈现';}
    const response = await fetch('/api/status', { signal: AbortSignal.timeout(4000), cache: 'no-store' });
    if (!response.ok || !response.headers.get('content-type')?.includes('application/json')) throw new Error('本机服务暂未就绪');
    const status = await response.json();
    state.localBackend = status.local_backend === true;
    state.liveAvailable = status.live_available === true;
    state.retentionDays = status.retention_days;
    state.localSource=status.source==='local-database';
    $('#live-option-text').textContent = state.liveAvailable ? '已检测到本地数据库，可读取真实应用回调。' : '尚未检测到回调数据库，请先配置并启动 MCP 回调。';
    if (status.local_backend) {
      // 本机真实接入中不自动混入演示会话，未配置时显示空状态和接入清单。
      state.mode = 'live'; state.conversations = []; state.selected = ''; renderAll();
      $('#sync-label').textContent = status.live_available ? '读取回调' : '等待接入';
      if(!status.local_backend)await initializeIntegration({ status, switchView, useSnapshot, onDatabaseAvailable: async () => { state.liveAvailable = true; } });
    }
    if (state.localBackend) await useLive();
    return true;
   } catch {
    if(attempt+1<attempts){await new Promise(resolve=>setTimeout(resolve,Math.min(250*2**attempt,1000)));continue;}
    if(window.desktopInbox){$('#sync-label').textContent='连接未完成，点击刷新重试';$('.sync-label').className='sync-label error';$('#receive-status').textContent='本机消息服务尚未连接，可点击右上角刷新重试';}
    return false;
   }
  }
 })().finally(()=>{bootstrapInFlight=null;});
 return bootstrapInFlight;
}
renderAll();
initializeBackend();
document.addEventListener('visibilitychange',()=>{if(!document.hidden){expireLiveEvents();if(renderPending)requestRender();}});
window.addEventListener('pagehide',()=>{stopStream();flushPreferences();});

function choosePlatform(platform){
 if(platform==='mail'){window.desktopInbox?.openMail();return;}
 window.inboxPlatform=platform;state.platform=platform;state.official=false;state.selected='';state.search='';searchRun++;searchResults=null;document.querySelector('#search-input').value='';
 if(state.mode==='live')buildLiveConversations();state.selected=sortedConversations()[0]?.id||'';
 document.querySelectorAll('[data-platform]').forEach(b=>b.classList.toggle('active',b.dataset.platform===platform));
 document.querySelector('#official-button').hidden=platform!=='wechat';
 document.querySelector('#official-button').classList.remove('active');
 document.querySelector('[data-view="moments"]').hidden = platform !== 'wechat' || !window.desktopInbox;
 switchView('messages');
 window.desktopInbox?.setPlatform(platform);
}
const platformBar=document.createElement('div');platformBar.className='platform-bar';platformBar.setAttribute('aria-label','收件台分区');
platformBar.innerHTML='<button data-platform="wechat">微信</button><button data-platform="wecom">企业微信</button><button data-platform="mail">邮箱</button>';
document.querySelector('.topbar').prepend(platformBar);
platformBar.querySelectorAll('button').forEach(b=>b.onclick=()=>choosePlatform(b.dataset.platform));
const officialButton=document.createElement('button');officialButton.id='official-button';officialButton.className='nav-button';officialButton.innerHTML='<span aria-hidden="true">▤</span><span class="rail-label">公众号</span>';officialButton.hidden=state.platform!=='wechat';document.querySelector('.rail-nav').prepend(officialButton);
document.querySelector('[data-view="moments"]').hidden = state.platform !== 'wechat' || !window.desktopInbox;
$('#moments-refresh').addEventListener('click',()=>loadMoments());
$('#moments-more').addEventListener('click',()=>loadMoments(true));
function showOfficial(value){state.official=value;searchRun++;searchResults=null;if(state.mode==='live')buildLiveConversations();state.selected=sortedConversations()[0]?.id||'';officialButton.classList.toggle('active',value);switchView('messages');if(state.search.trim())queueSearch(0);}
officialButton.onclick=()=>showOfficial(!state.official);
platformBar.querySelector(`[data-platform="${state.platform}"]`).classList.add('active');
window.desktopInbox?.onPlatform(choosePlatform);

window.inboxPlatform=state.platform;

// Compact inbox shell: only reading actions are visible in the main workspace.
const exportAction=document.querySelector('#export-button');exportAction.className='source-button';exportAction.textContent='导出会话';document.querySelector('.chat-actions').append(exportAction);
const settingsAction=document.querySelector('#connection-button');settingsAction.setAttribute('aria-label','接收设置');settingsAction.title='接收设置';settingsAction.innerHTML=icon('shield')+'<span class="rail-label">设置</span>';settingsAction.onclick=()=>window.desktopInbox?.openSettings();
document.querySelector('.brand').onclick=event=>{event.preventDefault();switchView('overview');};
