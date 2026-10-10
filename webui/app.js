'use strict';

const platforms = {
  all: {name: '全部平台', icon: 'layers'},
  wechat: {name: '微信', icon: 'message-circle'},
  wecom: {name: '企业微信', icon: 'briefcase-business'},
  qq: {name: 'QQ', icon: 'messages-square'},
  telegram: {name: 'Telegram', icon: 'send'}
};
const views = {
  chat: {name: '聊天记录', icon: 'messages-square', index: '01'},
  search: {name: '全局搜索', icon: 'search', index: '02'},
  contacts: {name: '联系人', icon: 'users-round', index: '03'},
  media: {name: '媒体库', icon: 'image', index: '04'},
  stats: {name: '统计概览', icon: 'chart-no-axes-column', index: '05'},
  sources: {name: '数据源', icon: 'database', index: '06'},
  imports: {name: '导入记录', icon: 'file-up', index: '07'},
  settings: {name: '设置', icon: 'settings-2', index: '08'}
};
const typeNames = {text: '文本', image: '图片', video: '视频', voice: '语音', file: '文件', link: '链接', system: '系统消息'};
const state = {
  platform: 'all', view: 'chat', sources: [], settings: {}, selected: null, contextMessage: null, contextMode: false,
  remembered: {}, conversations: [], conversationTotal: 0, cq: '', messages: [],
  messageTotal: 0, messageOffset: 0, messageCursor: null, messageHasMore: false, messageFilter: {q: '', type: '', start: '', end: ''},
  filters: {q: '', type: '', start: '', end: ''}, items: [], total: 0, page: 0, pageCursors: [null],
  loading: true, busy: false, error: '', stats: null, request: 0, showDates: false, managedContact: null,
  theme: localStorage.getItem('hub.theme') || 'light', lastSync: null, historyTask: null, backups: [], mcp: null, operations: [], importJobs: [], importFile: null, importPreview: null, importBusy: false, importPlatform: 'all', importAccount: ''
};
const esc = value => String(value ?? '').replace(/[&<>"']/g, x => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[x]));
const safeUrl = value => /^\/(?!\/)/.test(String(value || '')) ? String(value) : '';
const icon = (name, extra = '') => `<i data-lucide="${name}" class="icon ${extra}"></i>`;
const fmt = number => new Intl.NumberFormat('zh-CN').format(Number(number) || 0);
const dt = (stamp, dateOnly = false) => stamp ? new Intl.DateTimeFormat('zh-CN', {timeZone: 'Asia/Hong_Kong', month: '2-digit', day: '2-digit', ...(dateOnly ? {} : {hour: '2-digit', minute: '2-digit', hour12: false})}).format(new Date(stamp * 1000)) : '—';
const dateKey = stamp => new Intl.DateTimeFormat('en-CA', {timeZone: 'Asia/Hong_Kong', year: 'numeric', month: '2-digit', day: '2-digit'}).format(new Date(stamp * 1000));
const timeOnly = stamp => new Intl.DateTimeFormat('zh-CN', {timeZone: 'Asia/Hong_Kong', hour: '2-digit', minute: '2-digit', hour12: false}).format(new Date(stamp * 1000));
const badge = platform => `<span class="pill ${esc(platform)}">${esc(platforms[platform]?.name || platform)}</span>`;
const avatar = (name, platform = '', src = '', extra = '') => `<span class="avatar ${esc(platform)} ${extra}">${safeUrl(src) ? `<img src="${esc(safeUrl(src))}" alt="" loading="lazy">` : esc(Array.from(String(name || '?')).slice(0, 1).join(''))}</span>`;
const iconButton = (name, action, title, extra = '') => `<button type="button" class="icon-btn ${extra}" data-action="${action}" title="${esc(title)}" aria-label="${esc(title)}" ${extra.split(/\s+/).includes('disabled') ? 'disabled aria-disabled="true"' : ''}>${icon(name)}</button>`;
const sourceState = source => ({connected: '已连接', partial: '部分可用', syncing: '同步中', indexing: '索引中', connecting: '连接中', login_required: '待登录', disconnected: '未连接', error: '连接失败'}[source?.state] || '未连接');
const statusDot = source => ['connected', 'partial'].includes(source?.state) ? 'connected' : ['syncing', 'indexing', 'connecting'].includes(source?.state) ? 'syncing' : source?.state === 'error' ? 'error' : '';
const typeOptions = selected => `<option value="">全部类型</option>${Object.entries(typeNames).map(([key, name]) => `<option value="${key}" ${selected === key ? 'selected' : ''}>${name}</option>`).join('')}`;

async function api(path, params, options = {}) {
  const query = params ? new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== '')).toString() : '';
  const response = await fetch(path + (query ? '?' + query : ''), {...options, headers: {'Content-Type': 'application/json', ...options.headers}});
  if (!response.ok) {
    const problem = await response.json().catch(() => ({}));
    throw new Error(problem.error || `请求失败 (${response.status})`);
  }
  return response.json();
}

function icons() { window.lucide?.createIcons({attrs: {'stroke-width': 1.65}}); }
function toast(message, error = false) {
  const node = document.createElement('div');
  node.className = 'toast' + (error ? ' error' : '');
  node.innerHTML = `${icon(error ? 'circle-alert' : 'circle-check')}<span>${esc(message)}</span>`;
  document.querySelector('#toasts').append(node);
  icons();
  setTimeout(() => node.remove(), error ? 6500 : 3500);
}

function setTheme(theme) {
  state.theme = theme;
  localStorage.setItem('hub.theme', theme);
  const isDark = theme === 'dark' || theme === 'system' && matchMedia('(prefers-color-scheme: dark)').matches;
  document.body.dataset.theme = isDark ? 'dark' : 'light';
}

function renderShell() {
  const mainNav = ['chat', 'search', 'contacts', 'media', 'stats'];
  document.querySelector('#app').innerHTML = `<div class="app-shell">
    <header class="topbar">
      <a class="brand" href="#chat" aria-label="消息中枢"><img src="/assets/hub.svg" alt=""><div><div class="brand-name">消息中枢</div><div class="brand-sub">LOCAL ARCHIVE / 001</div></div></a>
      <span class="brand-divider"></span>
      <div class="platform-switch" role="group" aria-label="聊天平台">${Object.entries(platforms).map(([id, p]) => {
        const source = state.sources.find(x => x.id === id);
        return `<button class="platform-button ${state.platform === id ? 'active' : ''}" data-platform="${id}" aria-pressed="${state.platform === id}">${icon(p.icon)}<span>${p.name}</span>${id !== 'all' ? `<span class="dot ${statusDot(source)}" title="${sourceState(source)}"></span>` : ''}</button>`;
      }).join('')}</div>
      <div class="top-actions"><span class="local-tag">${icon('shield-check')}<span>本机数据</span></span><button class="btn" data-action="sync" ${state.busy ? 'disabled' : ''}>${icon('refresh-cw', state.busy ? 'spin' : '')}<span class="sync-text">同步</span></button>${iconButton(document.body.dataset.theme === 'dark' ? 'sun' : 'moon', 'theme', '切换明暗主题')}</div>
    </header>
    <div class="body-grid">
      <aside class="sidebar"><div class="nav-section"><div class="nav-label">档案 / ARCHIVE</div>${mainNav.map(id => navItem(id)).join('')}</div><div class="nav-section"><div class="nav-label">管理 / MANAGE</div>${['imports', 'sources', 'settings'].map(id => navItem(id)).join('')}</div><div class="sidebar-sources">${renderMiniSources()}</div><div class="sidebar-footer">${avatar('本')}<div><div class="small">本地工作区</div><div class="label">WINDOWS / LOCAL</div></div></div></aside>
      <main class="content" id="content" aria-label="${views[state.view].name}"></main>
    </div>
    <footer class="footer"><span id="footer-status">${footerStatus()}</span><span>UTC+08:00 <span class="muted">/</span> 本地档案</span></footer>
  </div>`;
  renderView();
}
function navItem(id) { return `<button class="nav-item ${state.view === id ? 'active' : ''}" data-view="${id}" title="${views[id].name}" aria-current="${state.view === id ? 'page' : 'false'}">${icon(views[id].icon)}<span>${views[id].name}</span></button>`; }
function renderMiniSources() { return ['wechat', 'wecom', 'qq'].map(id => {const s = state.sources.find(x => x.id === id); return `<button class="source-mini" data-platform="${id}" style="width:100%" title="${sourceState(s)}"><span><span class="dot ${statusDot(s)}"></span>${platforms[id].name}</span><span class="mono small">${s?.message_count ? fmt(s.message_count) : '—'}</span></button>`;}).join(''); }
function footerStatus() { return state.busy ? '正在同步本地记录…' : state.sources.some(x => x.state === 'indexing' || x.state === 'syncing') ? '正在建立本地搜索索引…' : `${state.sources.filter(x => ['connected', 'partial'].includes(x.state)).length} / 3 数据源已连接${state.lastSync ? ' · 更新于 ' + dt(state.lastSync) : ''}`; }
function head(extra = '') {const view = views[state.view];return `<div class="view-head"><div><div class="eyebrow">${view.index} / ${platforms[state.platform].name}</div><h1>${view.name}</h1></div><div class="head-actions">${extra}</div></div>`;}
function empty(name, text = '', action = '', symbol = 'inbox') { return `<div class="empty">${icon(symbol)}<h3>${esc(name)}</h3>${text ? `<p>${esc(text)}</p>` : ''}${action}</div>`; }
function loading() { return '<div class="loading-lines" aria-label="加载中">' + '<div class="skeleton"></div>'.repeat(7) + '</div>'; }
function errorStrip() { return state.error ? `<div class="error-strip">${icon('circle-alert')}<span>${esc(state.error)}</span><button class="btn" data-action="reload">重试</button></div>` : ''; }

function renderView() {
  const content = document.querySelector('#content');
  if (!content) return;
  content.setAttribute('aria-label', views[state.view].name);
  if (state.view === 'chat') content.innerHTML = renderChat();
  else if (state.view === 'sources') content.innerHTML = renderSources();
  else if (state.view === 'imports') content.innerHTML = renderImports();
  else if (state.view === 'settings') content.innerHTML = renderSettings();
  else if (state.view === 'stats') content.innerHTML = renderStats();
  else content.innerHTML = renderListing();
  icons();
  if (state.view === 'chat' && state.selected && !state.loading && !state.messageFilter.q && !state.messageOffset) {
    const messages = content.querySelector('.messages');
    if (messages) {
      const target = state.contextMessage && [...content.querySelectorAll('[data-message-id]')].find(node => node.dataset.messageId === state.contextMessage);
      if (target) target.scrollIntoView({block: 'center'});
      else messages.scrollTop = messages.scrollHeight;
    }
  }
}

function renderChat() {
  const selected = state.selected;
  const source = state.sources.find(x => x.id === state.platform);
  return `${errorStrip()}<div class="chat-workspace ${selected ? 'has-selection' : ''}">
    <section class="conversation-pane"><div class="pane-head"><div><h1>聊天记录</h1><span class="badge">${fmt(state.conversationTotal)}</span></div><form id="conversation-search" class="search-field">${icon('search')}<input aria-label="搜索会话" name="q" placeholder="搜索会话或联系人" value="${esc(state.cq)}" autocomplete="off"></form></div><div class="list-summary"><span>${platforms[state.platform].name}</span><span>最近消息</span></div><div class="conversation-list">${state.loading && !state.conversations.length ? loading() : state.conversations.length ? state.conversations.map((c, i) => renderConversation(c, i)).join('') : empty('暂无会话', source?.error || '等待本地数据源连接或索引完成', '<button class="btn" data-view="sources">'+icon('database')+'数据源</button>')}</div></section>
    <section class="chat-pane">${selected ? renderConversationContent(selected) : empty('选择一段对话', '从左侧会话列表打开聊天记录', '', 'messages-square')}</section>
    <aside class="inspector">${selected ? renderInspector(selected) : `<div class="label">会话详情 / DETAILS</div>${empty('暂无选择', '', '', 'panel-right')}`}</aside>
  </div>`;
}
function sameConversation(a, b) { return a && b && a.id === b.id && a.platform === b.platform && a.account_id === b.account_id; }
function renderConversation(c, index) {
  return `<button class="conversation-row ${sameConversation(c, state.selected) ? 'selected' : ''}" data-conversation="${index}">${avatar(c.title, c.platform, c.avatar_url)}<div class="row-body"><div class="row-title"><strong>${esc(c.title || c.id)}</strong><time>${dt(c.last_timestamp, true)}</time></div><div class="row-preview">${esc(c.last_message || '暂无文本消息')}</div><div class="row-meta">${badge(c.platform)}${c.kind === 'group' ? `<span class="small muted">${icon('users-round')} 群聊</span>` : ''}<span class="small muted">${fmt(c.message_count)} 条</span></div></div></button>`;
}
function renderConversationContent(c) {
  const f = state.messageFilter;
  const rows = [...state.messages].reverse();
  let lastDay = '';
  const messages = rows.map(m => {const day = dateKey(m.timestamp || 1);const divider = day !== lastDay ? `<div class="day-divider">${esc(day)}</div>` : '';lastDay = day;return divider + renderMessage(m);}).join('');
  return `<div class="chat-titlebar">${iconButton('chevron-left', 'back', '返回会话列表', 'mobile-back')}${avatar(c.title, c.platform, c.avatar_url)}<div class="title-text"><h2>${esc(c.title)}</h2><div class="chat-subtitle">${badge(c.platform)}<span>${c.kind === 'group' ? '群聊' : '私聊'}</span><span>· ${fmt(state.messageTotal || c.message_count)} 条消息</span></div></div><div class="chat-tools">${iconButton('download', 'export', '导出当前会话')}${iconButton('panel-right', 'details', '会话详情')}</div></div>
    <form id="message-search" class="message-filters"><div class="search-field">${icon('search')}<input name="q" aria-label="搜索当前会话" placeholder="搜索当前会话" value="${esc(f.q)}"></div><select name="type" aria-label="消息类型">${typeOptions(f.type)}</select>${iconButton('calendar-days', 'dates', '日期筛选')}<button class="icon-btn" type="submit" title="查询" aria-label="查询">${icon('arrow-right')}</button>${state.showDates ? `<div class="date-filters"><span>起止日期</span><input type="date" name="start" aria-label="开始日期" value="${esc(f.start)}"><span>至</span><input type="date" name="end" aria-label="结束日期" value="${esc(f.end)}"></div>` : ''}</form>
    <div class="messages" aria-label="消息列表">${state.loading ? loading() : `${!state.contextMode && state.messageHasMore ? `<button class="history-more" data-action="older">${icon('chevron-up')}加载更早消息 · ${fmt(state.messages.length)} / ${fmt(state.messageTotal)}</button>` : ''}${messages || empty('没有匹配的消息', f.q || f.type || f.start || f.end ? '调整筛选条件后重试' : '')}`}</div>
    <div class="chat-bottom"><span>${icon('lock-keyhole')}本地记录 · 只读</span><span class="mono">${fmt(state.messages.length)} / ${fmt(state.messageTotal)} RECORDS</span>${iconButton('arrow-down', 'latest', '跳到最新消息')}</div>`;
}
function renderMessage(m) {
  if (m.type === 'system') return `<div class="system-message ${m.id === state.contextMessage ? 'context-target' : ''}" data-message-id="${esc(m.id)}">${highlight(m.text)}</div>`;
  const mediaUrl = safeUrl(m.media_url);
  const roaming = m.extra?.roaming_resources || [];
  let attachment = '';
  if (mediaUrl && m.type === 'image') attachment = `<img class="media-img" src="${esc(mediaUrl)}" alt="聊天图片" loading="lazy" data-media-url="${esc(mediaUrl)}" data-media-type="image">`;
  else if (mediaUrl && m.type === 'video') attachment = `<video src="${esc(mediaUrl)}" controls preload="metadata"></video>`;
  else if (mediaUrl && m.type === 'voice') attachment = `<audio src="${esc(mediaUrl)}" controls preload="none"></audio>`;
  else if (m.type === 'file' && !roaming.length) attachment = `<${mediaUrl ? 'a href="' + esc(mediaUrl) + '" download' : 'div'} class="file-attachment">${icon('file-text')}<span>${esc(m.file_name || m.text || '文件')}${mediaUrl ? '' : iconButton('download', 'resolve-media:' + encodeURIComponent(m.id), '读取文件')}</span></${mediaUrl ? 'a' : 'div'}>`;
  else if (['image', 'video', 'voice'].includes(m.type) && !roaming.length) attachment = `<div class="file-attachment">${icon({image: 'image', video: 'video', voice: 'audio-lines'}[m.type])}<span>${typeNames[m.type]} <span class="muted small">${m.media_available === false ? '· 本机未缓存' : '· 打开时读取'}</span>${iconButton('eye', 'resolve-media:' + encodeURIComponent(m.id), '读取媒体')}</span></div>`;
  if (roaming.length) attachment += `<div class="roaming-resources">${roaming.map((resource, index) => {const rawKind = String(resource.type || 'file').toLowerCase();const kind = ({audio: 'voice', photo: 'image', picture: 'image', images: 'image', videos: 'video', files: 'file'}[rawKind] || rawKind);const url = safeUrl(resource.media_url);const name = resource.name || typeNames[kind] || '附件';if (url && kind === 'image') return `<a class="roaming-image-link" href="${esc(url)}" target="_blank" rel="noreferrer"><img class="media-img" src="${esc(url)}" alt="${esc(name)}" loading="lazy"></a>`;if (url && kind === 'video') return `<video src="${esc(url)}" controls preload="metadata"></video>`;if (url && kind === 'voice') return `<audio src="${esc(url)}" controls preload="none"></audio>`;if (url) return `<a class="roaming-resource" href="${esc(url)}" download>${icon('file-text')}${esc(name)}</a>`;const glyph = icon({image: 'image', video: 'video', voice: 'audio-lines', file: 'file-text'}[kind] || 'paperclip');return resource.available === false ? `<span class="roaming-resource unavailable" title="附件未能下载到本机">${glyph}${esc(name)}<small>未缓存</small></span>` : `<button type="button" class="roaming-resource" data-action="resolve-media:${encodeURIComponent(m.id)}:${index}">${glyph}${esc(name)}</button>`;}).join('')}</div>`;
  const text = attachment ? (m.type === 'file' && !roaming.length) || /^\[.+\]$/.test(m.text || '') ? '' : `<div>${highlight(m.text)}</div>` : highlight(m.text || '[' + (typeNames[m.type] || '消息') + ']');
  return `<div class="message ${m.is_self ? 'self' : ''} ${m.id === state.contextMessage ? 'context-target' : ''}" data-message-id="${esc(m.id)}">${avatar(m.sender_name || (m.is_self ? '我' : '?'))}<div class="message-body"><div class="message-meta"><span>${esc(m.sender_name || (m.is_self ? '我' : m.sender_id || '未知发送人'))}${m.extra?.roaming_metadata?.recalled ? ' · 已撤回' : ''}</span><time>${timeOnly(m.timestamp || 1)}</time></div><div class="bubble">${attachment}${text}</div></div></div>`;
}
function highlight(text) {
  const escaped = esc(text);
  const q = state.view === 'chat' ? state.messageFilter.q : state.filters.q;
  if (!q) return escaped;
  return escaped.replace(new RegExp(esc(q).replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi'), value => `<mark>${value}</mark>`);
}
function renderInspector(c) {
  const senders = [...new Map(state.messages.filter(x => x.sender_id).map(x => [x.sender_id, x.sender_name || x.sender_id])).values()].slice(0, 7);
  return `<div class="label">会话详情 / DETAILS</div><div class="inspector-profile">${avatar(c.title, c.platform, c.avatar_url, 'large')}<h3>${esc(c.title)}</h3>${badge(c.platform)}</div><dl class="inspector-dl"><dt>会话类型</dt><dd>${c.kind === 'group' ? '群聊' : '私聊'}</dd><dt>消息数量</dt><dd class="mono">${fmt(state.messageTotal || c.message_count)}</dd>${c.member_count ? `<dt>群成员</dt><dd>${fmt(c.member_count)} 人</dd>` : ''}<dt>最近消息</dt><dd>${dt(c.last_timestamp)}</dd><dt>数据来源</dt><dd>本机 · ${platforms[c.platform].name}</dd></dl><form id="conversation-metadata-form" class="inspector-section metadata-form"><h3>本地整理</h3><label>备注<textarea name="note" maxlength="4096" rows="3">${esc(c.note || '')}</textarea></label><label>标签<input name="tags" maxlength="1024" value="${esc((c.tags || []).join(', '))}" placeholder="用逗号分隔"></label><label class="check-label"><input type="checkbox" name="archived" ${c.archived ? 'checked' : ''}>归档会话</label><button class="btn" type="submit">${icon('save')}保存整理信息</button></form><div class="inspector-section"><h3>当前消息中的成员</h3><div class="member-list">${senders.map(name => `<div class="member-row">${avatar(name)}<span class="truncate">${esc(name)}</span></div>`).join('') || '<span class="muted small">暂无成员信息</span>'}</div></div>${c.platform === 'qq' && c.kind !== 'group' ? `<button class="btn" data-action="qq-history">${icon('history')}导入漫游历史</button>` : ''}<button class="btn" data-action="export">${icon('download')}导出会话</button><div class="inspector-note">${icon('shield-check')}原始聊天数据库保持只读</div>`;
}

function renderListing() {
  const f = state.filters;
  const isSearch = state.view === 'search';
  const extra = isSearch ? iconButton('download', 'export', '导出搜索结果') : '';
  const filter = `<form id="listing-search" class="filter-row"><div class="search-field">${icon('search')}<input name="q" aria-label="${isSearch ? '搜索所有聊天记录' : '搜索' + views[state.view].name}" placeholder="${isSearch ? '搜索所有聊天记录…' : '搜索' + views[state.view].name}" value="${esc(f.q)}" autocomplete="off"></div>${state.view !== 'contacts' ? `<select name="type" aria-label="消息类型">${typeOptions(f.type)}</select>` : ''}${isSearch ? `<label>日期</label><input type="date" name="start" aria-label="开始日期" value="${esc(f.start)}"><input type="date" name="end" aria-label="结束日期" value="${esc(f.end)}">` : ''}<button class="btn primary" type="submit">${icon('search')}查询</button></form>`;
  let body;
  if (state.loading) body = loading();
  else if (!state.items.length) body = empty(isSearch && !f.q ? '搜索你的聊天档案' : '没有匹配的记录', isSearch && !f.q ? '关键词、时间与消息类型' : '可调整筛选条件或查看数据源连接状态', '', isSearch ? 'search' : views[state.view].icon);
  else if (state.view === 'contacts') body = `<table class="contact-table"><thead><tr><th>联系人</th><th>来源</th><th>类型</th><th>统一身份</th><th></th></tr></thead><tbody>${state.items.map((c, i) => `<tr><td><button class="contact-cell" data-contact="${i}" title="打开聊天记录">${avatar(c.name, c.platform, c.avatar_url)}<strong>${esc(c.name)}</strong></button></td><td>${badge(c.platform)}</td><td class="muted small">${c.kind === 'group' ? '群聊' : '联系人'}</td><td class="mono small">${esc(c.identity_id || '—')}</td><td>${iconButton('contact-round', `manage-contact:${i}`, c.identity_id ? '编辑身份关联' : '关联统一身份')}</td></tr>`).join('')}</tbody></table>`;
  else if (state.view === 'media') body = `<div class="view-scroll"><div class="media-grid">${state.items.map((m, i) => `<button class="media-tile" data-item="${i}"><div class="media-preview">${m.type === 'image' && safeUrl(m.media_url) ? `<img src="${esc(safeUrl(m.media_url))}" alt="聊天图片" loading="lazy">` : icon({video: 'video', voice: 'audio-lines', file: 'file-text', image: 'image'}[m.type] || 'file')}</div><div class="media-caption"><strong class="truncate">${esc(m.file_name || m.conversation_title || typeNames[m.type])}</strong><div>${badge(m.platform)}<span>${dt(m.timestamp, true)}</span></div></div></button>`).join('')}</div></div>`;
  else body = state.items.map((m, i) => `<article class="result-row" data-item="${i}" tabindex="0">${avatar(m.conversation_title, m.platform)}<div><div class="result-heading"><strong>${esc(m.conversation_title || m.conversation_id)}</strong>${badge(m.platform)}<span class="small muted">${typeNames[m.type] || '消息'}</span></div><p>${highlight(m.text || '[' + (typeNames[m.type] || '消息') + ']')}</p><div class="sender">${esc(m.sender_name || (m.is_self ? '我' : '未知发送人'))}</div></div><time>${dt(m.timestamp)}</time></article>`).join('');
  return `${head(extra)}${errorStrip()}${filter}<div class="results-summary"><span>${state.view === 'search' && !f.q ? '全部平台可联合查询' : fmt(state.total) + ' 条记录'}</span><span>${platforms[state.platform].name}</span></div><div class="view-scroll flat">${body}${state.total > 60 ? pager() : ''}</div>`;
}
function pager() {const hasNext = state.view === 'search' ? !!state.pageCursors[state.page + 1] : (state.page + 1) * 60 < state.total;return `<div class="pager">${iconButton('chevron-left', 'page-prev', '上一页', state.page === 0 ? 'disabled' : '')}<span>${state.page + 1} / ${Math.ceil(state.total / 60)}</span>${iconButton('chevron-right', 'page-next', '下一页', hasNext ? '' : 'disabled')}</div>`;}
function renderSources() {
  return `${head('<button class="btn" data-action="sync">' + icon('refresh-cw') + '同步全部</button>')}<div class="view-scroll view-enter"><div class="section-title"><h2>已集成的数据源</h2><span class="label">3 PLATFORMS</span></div><div class="source-list">${['wechat', 'wecom', 'qq'].map(id => {
    const s = state.sources.find(x => x.id === id) || {id, state: 'disconnected'};
    return `<section class="source-row"><div class="source-logo ${id}">${icon(platforms[id].icon)}</div><div><div class="source-name"><h2>${platforms[id].name}</h2><span class="pill"><span class="dot ${statusDot(s)}"></span>${sourceState(s)}</span></div><div class="source-detail"><span>${id === 'qq' ? '内置 NapCat / QQ Chat Exporter' : id === 'wechat' ? '内置本地数据库读取' : '内置企业微信读取'}</span>${s.version ? `<span class="mono">${esc(s.version)}</span>` : ''}</div><div class="source-detail"><span>${fmt(s.conversation_count)} 个会话</span><span>${fmt(s.message_count)} 条已索引消息</span>${s.last_sync ? `<span>更新 ${dt(s.last_sync)}</span>` : ''}</div>${s.error ? `<div class="source-error">${esc(s.error)}</div>` : ''}${s.progress ? `<div class="source-detail mono">${esc(s.progress)}</div>` : ''}</div><div class="head-actions">${id === 'qq' ? `<button class="btn ${s.state === 'login_required' ? 'accent' : ''}" data-action="qq-login">${icon('qr-code')}登录</button>` : ''}${iconButton('refresh-cw', 'sync:' + id, '同步' + platforms[id].name)}${iconButton('settings-2', 'configure:' + id, '配置' + platforms[id].name)}</div></section>`;
  }).join('')}</div><div class="source-description">${icon('lock-keyhole')}<span>索引、缓存与连接配置保存在本机。原始数据库只读。</span></div></div>`;
}
function renderImports() {
  const report = state.importPreview;
  const sample = report?.sample?.map(item => `<div class="import-sample"><span>${badge(item.platform)}</span><span>${esc(item.sender_name || '未知发送者')} · ${esc(item.conversation_id)}</span><time>${dt(item.timestamp)}</time><p>${esc(item.text || '[' + item.type + ']')}</p></div>`).join('') || '';
  const errors = report?.errors?.map(item => `<div class="import-error">第 ${fmt(item.row)} 行：${esc(item.error)}</div>`).join('') || '';
  const warnings = report?.warnings?.map(item => `<div class="import-warning">第 ${fmt(item.row)} 行：${esc(item.warning)}</div>`).join('') || '';
  const jobs = state.importJobs.map(job => {
    const active = ['queued', 'running'].includes(job.state);
    const labels = {queued: '排队中', running: '导入中', completed: '已完成', cancelled: '已取消', failed: '失败'};
    const percent = job.valid ? Math.floor(job.processed * 100 / job.valid) : 100;
    return `<div class="backup-row import-job"><div><strong>${esc(job.filename)}</strong><span class="small muted">${labels[job.state] || job.state} · ${fmt(job.processed)} / ${fmt(job.valid)} 条 · ${new Intl.DateTimeFormat('zh-CN', {dateStyle: 'medium', timeStyle: 'short'}).format(new Date(job.created_at * 1000))}</span>${active ? `<progress max="100" value="${percent}"></progress>` : ''}${job.error ? `<span class="small error-text">${esc(job.error)}</span>` : ''}</div>${active ? iconButton('x', `cancel-import:${encodeURIComponent(job.id)}`, '取消导入') : `<span class="small muted">${fmt(job.messages)} 条处理</span>`}</div>`;
  }).join('') || '<p class="muted small">暂无导入任务</p>';
  return `${head()}<div class="view-scroll view-enter import-view"><section class="settings-group"><h2>导入 JSON / CSV</h2><p class="muted small">支持消息中枢 JSON/CSV，以及 Telegram Desktop 单个会话 JSON 导出。Telegram 记录为导入数据，不提供原生登录或自动同步。</p><div class="import-fields"><label>默认平台<select id="import-platform">${['all','wechat','wecom','qq','telegram'].map(id => `<option value="${id}" ${state.importPlatform === id ? 'selected' : ''}>${platforms[id].name}</option>`).join('')}</select></label><label>默认账号<input id="import-account" type="text" maxlength="128" placeholder="Telegram 可留空以使用导出账号标识" value="${esc(state.importAccount)}"></label></div><label class="btn import-picker">${icon('paperclip')}选择 JSON 或 CSV 文件<input id="import-file" type="file" accept=".json,.csv,application/json,text/csv"></label><span class="small muted import-filename">${esc(state.importFile?.name || '尚未选择文件')}</span><div class="import-actions"><button class="btn primary" data-action="preview-import" ${!state.importFile || state.importBusy ? 'disabled' : ''}>${icon('scan-search')}预览校验</button><button class="btn" data-action="commit-import" ${!report?.valid || state.importBusy ? 'disabled' : ''}>${icon('database-zap')}创建导入任务</button></div>${report ? `<div class="import-report"><div class="history-metrics"><span>${fmt(report.total)} 条记录</span><span>${fmt(report.valid)} 条有效</span><span>${fmt(report.invalid)} 条待修复</span></div>${warnings}${sample}${errors}${report.invalid > (report.errors?.length || 0) ? `<p class="muted small">其余错误已省略，最多显示 100 条。</p>` : ''}</div>` : ''}</section><section class="settings-group"><h2>导入任务</h2><div class="backup-list">${jobs}</div></section></div>`;
}
async function uploadImport(preview) {
  if (!state.importFile || state.importBusy) return;
  state.importPlatform = document.querySelector('#import-platform')?.value || state.importPlatform;
  state.importAccount = document.querySelector('#import-account')?.value || '';
  state.importBusy = true;renderView();
  const platform = state.importPlatform;
  const account = state.importAccount;
  const format = state.importFile.name.toLowerCase().endsWith('.csv') ? 'csv' : state.importPlatform === 'telegram' ? 'telegram' : 'json';
  const query = new URLSearchParams({format, platform, account, filename: state.importFile.name});
  try {
    const response = await fetch(`/api/imports${preview ? '/preview' : ''}?${query}`, {method: 'POST', body: state.importFile, headers: {'Content-Type': 'application/octet-stream'}});
    const result = await response.json();if (!response.ok) throw new Error(result.error || '导入失败');
    state.importPreview = result;
    if (!preview) {state.importFile = null;toast('导入任务已创建');await refreshImportJobs();}
    else toast(`校验完成：${fmt(result.valid)} 条有效记录`);
  } catch (error) {toast(error.message, true);}
  finally {state.importBusy = false;renderView();}
}
async function refreshImportJobs(render = true) {
  const result = await api('/api/imports');
  state.importJobs = result.items || [];
  if (render && state.view === 'imports') renderView();
}
async function cancelImportJob(id) {
  try {await api(`/api/imports/${encodeURIComponent(id)}/cancel`, null, {method: 'POST', body: '{}'});await refreshImportJobs();toast('已请求取消导入');}
  catch (error) {toast(error.message, true);}
}
function renderSettingsBase() {
  return `${head()}<div class="view-scroll view-enter"><section class="settings-group"><h2>外观</h2><div class="setting-row"><div><strong>主题</strong><p>工作区配色</p></div><div class="segmented">${[['light', '浅色'], ['dark', '深色'], ['system', '系统']].map(([id, name]) => `<button data-theme-value="${id}" class="${state.theme === id ? 'active' : ''}">${name}</button>`).join('')}</div></div></section><section class="settings-group"><h2>同步</h2><div class="setting-row"><div><strong>自动增量同步</strong><p>读取已连接平台的新消息</p></div><button class="toggle ${state.settings.auto_sync !== false ? 'on' : ''}" role="switch" aria-label="自动增量同步" aria-checked="${state.settings.auto_sync !== false}" data-action="toggle-sync"></button></div><div class="setting-row"><div><strong>同步间隔</strong><p>分钟</p></div><select id="sync-interval" aria-label="同步间隔">${[1, 3, 5, 10, 30].map(v => `<option value="${v}" ${Number(state.settings.sync_interval || 5) === v ? 'selected' : ''}>${v}</option>`).join('')}</select></div></section><section class="settings-group"><h2>数据与隐私</h2><div class="setting-row"><div><strong>存储位置</strong><p class="mono">${esc(state.settings.storage_dir || '.local')}</p></div><span class="pill">${icon('hard-drive')}本机</span></div><div class="setting-row"><div><strong>访问地址</strong><p class="mono">${esc(location.origin)}</p></div><span class="pill">仅本机</span></div><div class="setting-row"><div><strong>搜索索引</strong><p>长关键词使用本地三元字符索引，短词回退字面搜索</p></div><span class="pill">${state.settings.fts_enabled ? 'FTS5 trigram' : 'LIKE 回退'}</span></div><div class="setting-row"><div><strong>原始聊天数据库</strong><p>查询与导出使用本地副本</p></div><span class="pill">只读</span></div></section><section class="settings-group"><h2>已集成项目</h2>${[['WeFlow', '5.0.0 / CC BY-NC-SA 4.0'], ['QQ Chat Exporter', '6.3.2 / GPL-3.0'], ['企业微信本地读取器', '只读 / 本地快照'], ['统一索引与导出', '本地 SQLite / MCP']].map(([name, value]) => `<div class="setting-row"><strong>${name}</strong><span class="small muted mono">${value}</span></div>`).join('')}</section></div>`;
}
function renderSettings() {
  const backups = state.backups.map(item => `<div class="backup-row"><div><strong>${esc(item.id)}</strong><span class="small muted">${new Intl.DateTimeFormat('zh-CN', {dateStyle: 'medium', timeStyle: 'short'}).format(new Date(item.created_at * 1000))} · ${fmtBytes(item.size)}</span></div><button class="icon-btn" data-action="download-backup:${encodeURIComponent(item.id)}" title="下载备份" aria-label="下载备份">${icon('download')}</button></div>`).join('') || '<p class="muted small">尚无备份</p>';
  const backupSection = `<section class="settings-group"><h2>索引库备份</h2><p class="muted small backup-note">备份仅包含消息中枢索引数据库，不包含第三方原始数据库、账号密钥或媒体缓存。</p><div class="backup-actions"><button class="btn primary" data-action="create-backup">${icon('archive')}创建备份</button><label class="btn">${icon('upload')}选择 SQLite 备份<input id="restore-backup-file" type="file" accept=".sqlite3,.db,application/vnd.sqlite3,application/x-sqlite3"></label><button class="btn" data-action="restore-backup">恢复所选</button></div><div class="backup-list">${backups}</div></section>`;
  const operations = (state.mcp?.operations || []).map(item => `<div class="backup-row"><div><strong>${esc(item.tool)}</strong><span class="small muted">${dt(item.timestamp)}</span></div><span class="pill ${item.status === 'success' ? 'connected' : 'error'}">${item.status === 'success' ? '完成' : '失败'}</span></div>`).join('') || '<p class="muted small">暂无 MCP 调用记录</p>';
  const management = state.operations.map(item => `<div class="backup-row"><div><strong>${esc(({ 'conversation_metadata.update': '会话整理', 'contact_identity.link': '关联统一身份', 'contact_identity.unlink': '解除身份关联' })[item.action] || item.action)}</strong><span class="small muted">${esc(item.entity_type)} · ${dt(item.timestamp)}</span></div><span class="pill">本地记录</span></div>`).join('') || '<p class="muted small">暂无管理操作</p>';
  const mcp = state.mcp;
  const mcpSection = `<section class="settings-group"><div class="section-title"><h2>MCP 服务</h2><span class="pill"><span class="dot ${mcp?.available ? 'connected' : 'error'}"></span>${mcp?.available ? '可用' : '不可用'}</span></div><div class="setting-row"><div><strong>连接方式</strong><p>${esc(mcp?.transport || 'stdio')} · ${esc(mcp?.lifecycle || '读取状态中')}</p></div><span class="pill">客户端按需启动</span></div><div class="setting-row"><div><strong>允许整理元数据</strong><p>启用后 MCP 可修改本地备注、标签、归档和身份关联；聊天内容仍只读</p></div><button class="toggle ${state.settings.mcp_write_enabled === true ? 'on' : ''}" role="switch" aria-label="允许 MCP 修改本地元数据" aria-checked="${state.settings.mcp_write_enabled === true}" data-action="toggle-mcp-writes"></button></div><div class="setting-row"><div><strong>当前权限</strong><p>${mcp?.write_enabled ? '可写入消息中枢元数据，不会修改聊天消息' : '只读查询索引与导入任务状态'}；更改权限后需重启 MCP 客户端进程</p></div><span class="pill">${mcp?.write_enabled ? '元数据写入' : '只读'}</span></div><div class="mcp-config-head"><strong>Hermes 配置</strong><button class="btn" data-action="copy-mcp-config" ${mcp?.config_text ? '' : 'disabled'}>${icon('copy')}复制配置</button></div><pre class="mcp-config">${esc(mcp?.config_text || '正在读取本机 MCP 配置…')}</pre><div class="section-title mcp-activity-title"><h3>最近调用</h3><span class="label">仅记录工具名与结果，不保存查询内容</span></div><div class="backup-list">${operations}</div></section>`;
  const managementSection = `<section class="settings-group"><div class="section-title"><h2>本地管理记录</h2><span class="label">不记录备注或查询内容</span></div><div class="backup-list">${management}</div></section>`;
  const deleteSection = `<section class="settings-group danger-zone"><h2>清除本地索引</h2><p class="muted small">删除消息中枢索引、导入暂存、索引备份和 QQ 漫游缓存。平台原始数据、账号配置及同目录其他文件会保留。正在运行的任务需先停止。</p><button class="btn danger" data-action="delete-local-data">${icon('trash-2')}清除本地数据</button></section>`;
  return renderSettingsBase().replace('<section class="settings-group"><h2>已集成项目</h2>', backupSection + deleteSection + mcpSection + managementSection + '<section class="settings-group"><h2>已集成项目</h2>');
}
function fmtBytes(value) {const n = Number(value) || 0;return n < 1024 * 1024 ? `${fmt(Math.ceil(n / 1024))} KB` : `${(n / (1024 * 1024)).toFixed(1)} MB`;}
async function createBackup(button) {
  button.disabled = true;
  try {await api('/api/backups', null, {method: 'POST', body: '{}'});state.backups = (await api('/api/backups')).items || [];renderView();toast('索引数据库备份已创建');}
  catch (e) {toast(e.message, true);button.disabled = false;}
}
async function restoreBackup(button) {
  const file = document.querySelector('#restore-backup-file')?.files?.[0];
  if (!file) {toast('先选择一个 SQLite 备份文件', true);return;}
  if (file.size > 8 * 1024 * 1024 * 1024) {toast('备份文件超过 8 GiB 限制', true);return;}
  if (!window.confirm(`将用“${file.name}”替换当前索引数据库。此操作不会更改第三方原始数据库，确定继续吗？`)) return;
  button.disabled = true;
  try {
    const response = await fetch('/api/backups/restore', {method: 'POST', body: file, headers: {'Content-Type': 'application/octet-stream'}});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || '恢复失败');
    state.backups = (await api('/api/backups')).items || [];state.selected = null;state.messages = [];renderView();toast('索引数据库已恢复');
  } catch (e) {toast(e.message, true);button.disabled = false;}
}
function renderStats() {
  const s = state.stats;
  if (!s) return head() + (state.loading ? loading() : empty('暂无统计', state.error));
  const daily = (s.daily || []).slice(-45);
  const max = Math.max(1, ...daily.map(x => x.count));
  const counts = s.per_type || {};
  const overview = s.overview || {};
  const jobState = {queued: '排队中', running: '导入中', completed: '已完成', cancelled: '已取消', failed: '失败'};
  const recent = (overview.recent_imports || []).map(job => `<div class="backup-row"><div><strong>${esc(job.filename)}</strong><span class="small muted">${jobState[job.state] || esc(job.state)} · ${fmt(job.processed)} / ${fmt(job.valid)} 条</span></div><span class="small muted">${dt(job.created_at)}</span></div>`).join('') || '<p class="muted small">暂无导入任务</p>';
  const sources = (overview.sources || []).map(source => `<div class="backup-row"><div><strong><span class="dot ${statusDot(source)}"></span> ${platforms[source.id]?.name || esc(source.id)}</strong><span class="small muted">${sourceState(source)} · ${fmt(source.message_count)} 条 · ${fmt(source.conversation_count)} 个会话</span></div><span class="small muted">${source.error ? esc(source.error) : source.last_sync ? dt(source.last_sync) : '尚未同步'}</span></div>`).join('') || '<p class="muted small">数据源状态不可用</p>';
  return `${head()}<div class="view-scroll view-enter"><div class="metrics">${[['已索引消息', s.message_count, 'message-square'], ['会话', s.conversation_count, 'messages-square'], ['联系人', s.contact_count, 'users-round'], ['媒体消息', s.media_count, 'image']].map(([name, value]) => `<div class="metric"><div class="label">${name}</div><strong>${fmt(value)}</strong><div class="small">${platforms[state.platform].name}</div></div>`).join('')}</div><div class="analytics-grid overview-grid"><section><div class="section-title"><h2>数据源</h2><span class="label">索引 ${fmtBytes(overview.index_bytes)}</span></div>${sources}<button class="btn" data-view="sources">${icon('database')}管理数据源</button></section><section><div class="section-title"><h2>最近导入</h2><button class="btn" data-view="imports">全部任务</button></div>${recent}</section></div><div class="analytics-grid"><section><div class="section-title"><h2>消息活动</h2><span class="label">最近 ${daily.length} 个活跃日</span></div>${daily.length ? `<div class="activity-chart">${daily.map(d => `<div class="chart-bar" style="height:${Math.max(2, d.count / max * 100)}%" title="${esc(d.date)} · ${fmt(d.count)} 条"></div>`).join('')}</div><div class="chart-labels"><span>${daily[0].date}</span><span>${daily.at(-1).date}</span></div>` : empty('暂无消息活动')}</section><section><h2>消息类型</h2><div class="type-breakdown">${Object.entries(typeNames).map(([key, name]) => `<div class="type-row"><span>${name}</span><div class="type-bar"><div style="width:${Math.min(100, Number(counts[key] || 0) / Math.max(1, s.message_count) * 100)}%"></div></div><span class="mono muted" style="text-align:right">${fmt(counts[key] || 0)}</span></div>`).join('')}</div></section></div></div>`;
}

async function loadView() {
  const request = ++state.request;
  state.loading = true; state.error = '';
  renderView();
  try {
    if (state.view === 'chat') {
      const result = await api('/api/conversations', {platform: state.platform, q: state.cq, limit: 300});
      if (request !== state.request) return;
      state.conversations = result.items; state.conversationTotal = result.total;
      const selected = state.conversations.find(c => sameConversation(c, state.selected));
      if (selected) state.selected = selected;
      if (!state.selected && result.items.length && !matchMedia('(max-width:650px)').matches) state.selected = result.items[0];
      if (state.selected && state.contextMessage) {
        const context = await api('/api/context', {platform: state.selected.platform, account_id: state.selected.account_id, message_id: state.contextMessage, before: 8, after: 8});
        if (request === state.request) {state.messages = context.items;state.messageTotal = context.total;state.messageOffset = 0;state.messageCursor = null;state.messageHasMore = false;state.contextMode = true;}
      } else if (state.selected) await loadMessages(false, request);
    } else if (['contacts', 'media', 'search'].includes(state.view)) {
      const params = {...state.filters, platform: state.platform, limit: 60,
        ...(state.view === 'search' ? {cursor: state.pageCursors[state.page] || undefined} : {offset: state.page * 60})};
      const result = state.view === 'search' && !state.filters.q ? {items: [], total: 0} : await api('/api/' + state.view, params);
      if (request !== state.request) return;
      state.items = result.items; state.total = result.total;
      if (state.view === 'search') state.pageCursors[state.page + 1] = result.next_cursor || null;
    } else if (state.view === 'stats') {
      const result = await api('/api/stats', {platform: state.platform});
      if (request !== state.request) return;
      state.stats = result;
    } else if (state.view === 'sources') await refreshSources(false);
    else if (state.view === 'settings') {
      const [result, mcp, operations] = await Promise.all([api('/api/backups'), api('/api/mcp'), api('/api/operations')]);
      if (request !== state.request) return;
      state.backups = result.items || []; state.mcp = mcp; state.operations = operations.items || [];
    } else if (state.view === 'imports') await refreshImportJobs(false);
  } catch (error) { if (request === state.request) state.error = error.message; }
  finally { if (request === state.request) {state.loading = false; renderView();} }
}
async function loadMessages(append = false, request = state.request) {
  const c = state.selected;
  if (!c) return;
  const result = await api('/api/messages', {platform: c.platform, account_id: c.account_id, conversation_id: c.id, ...state.messageFilter, limit: 100,
    ...(append && state.messageCursor ? {cursor: state.messageCursor} : {offset: append ? state.messages.length : 0})});
  if (request !== state.request || !sameConversation(c, state.selected)) return;
  state.messages = append ? [...state.messages, ...result.items] : result.items;
  state.messageTotal = result.total; state.messageOffset = append ? state.messages.length : 0;state.messageCursor = result.next_cursor || null;state.messageHasMore = result.has_more;
}
async function selectConversation(c) {
  state.selected = c; state.contextMessage = null;state.contextMode = false;state.remembered[state.platform] = c;
  state.messages = []; state.messageOffset = 0; state.messageCursor = null;state.messageHasMore = false; state.messageFilter = {q: '', type: '', start: '', end: ''};
  state.loading = true; state.error = ''; renderView();
  const request = ++state.request;
  try { await loadMessages(false, request); } catch (e) {state.error = e.message;}
  if (request === state.request) {state.loading = false; renderView();}
}
async function refreshSources(render = true) {
  const result = await api('/api/sources');
  state.sources = result.sources || result;
  state.lastSync = Math.max(0, ...state.sources.map(x => x.last_sync || 0));
  if (render) {
    const mini = document.querySelector('.sidebar-sources'); if (mini) mini.innerHTML = renderMiniSources();
    document.querySelectorAll('[data-platform]').forEach(el => {const source = state.sources.find(s => s.id === el.dataset.platform);const dot = el.querySelector('.dot');if (dot) {dot.className = 'dot ' + statusDot(source);dot.title = sourceState(source);}});
    const footer = document.querySelector('#footer-status'); if (footer) footer.textContent = footerStatus();
    if (state.view === 'sources') renderView();
    icons();
  }
}
async function sync(platform = state.platform) {
  if (state.busy) return;
  state.busy = true; renderShell();
  try {
    const result = await api('/api/sync', null, {method: 'POST', body: JSON.stringify({platform})});
    toast(result.message || '已开始同步');
    await refreshSources();
  } catch (e) {toast(e.message, true);}
  finally {state.busy = false; renderShell();}
}
function modal(title, body, footer = '', wide = false, focus = true) {
  document.querySelector('#overlay').innerHTML = `<div class="overlay-backdrop"><section class="dialog ${wide ? 'wide' : ''}" role="dialog" aria-modal="true" aria-label="${esc(title)}"><div class="dialog-head"><h2>${esc(title)}</h2>${iconButton('x', 'close-modal', '关闭')}</div><div class="dialog-body">${body}</div>${footer ? `<div class="dialog-foot">${footer}</div>` : ''}</section></div>`;
  icons();
  if (focus) setTimeout(() => document.querySelector('.dialog input, .dialog button')?.focus(), 0);
}
function closeModal() { document.querySelector('#overlay').innerHTML = ''; }
function qqHistoryPanel(job = null, values = {}) {
  const today = new Date();
  const end = values.end || today.toISOString().slice(0, 10);
  const startDate = new Date(today);startDate.setDate(startDate.getDate() - 29);
  const start = values.start || startDate.toISOString().slice(0, 10);
  if (!job) return `<form id="qq-history-form"><div class="field"><label for="qq-history-start">开始日期</label><input id="qq-history-start" type="date" name="start" value="${esc(start)}" required></div><div class="field"><label for="qq-history-end">结束日期</label><input id="qq-history-end" type="date" name="end" value="${esc(end)}" required></div><div class="field"><label for="qq-history-messages">消息上限</label><input id="qq-history-messages" type="number" name="max_messages" min="1" max="100000" value="50000" required></div><div class="field"><label for="qq-history-queries">扫描查询预算</label><input id="qq-history-queries" type="number" name="max_queries" min="1" max="100000" value="50000" required></div><p class="history-warning">仅限 QQ 私聊漫游；附件保存在本机。受 QQ 漫游可用性和扫描预算影响，无法证明服务端记录完整。</p></form>`;
  const scan = job.scan || {};
  return `<div class="qq-history-task"><div class="history-progress"><span class="dot ${['starting', 'running', 'cancel_requested'].includes(job.state) ? 'syncing' : job.state === 'failed' ? 'error' : 'connected'}"></span><strong>${esc(job.progress || job.state)}</strong></div><div class="history-metrics"><span>${fmt(job.message_count)} 条已写入本机</span><span>${fmt(scan.scannedDays || 0)} 天已扫描</span><span>${fmt(scan.messageCount || 0)} 条发现</span></div>${job.partial || scan.partial ? `<p class="history-warning">扫描预算已用尽或漫游结果不完整；当前可获取内容已导入。</p>` : ''}${job.error ? `<p class="history-warning">${esc(job.error)}</p>` : ''}${['starting', 'running', 'cancel_requested'].includes(job.state) ? '<p class="muted small">正在保留已有记录；可以取消后停止继续扫描。</p>' : '<p class="muted small">结果范围：' + esc(job.start || '') + ' 至 ' + esc(job.end || '') + '</p>'}</div>`;
}
async function showQQHistory() {
  const c = state.selected;
  if (!c || c.platform !== 'qq' || c.kind === 'group') {toast('请选择 QQ 私聊会话');return;}
  const existing = await api('/api/qq/history-import').catch(() => ({state: 'idle'}));
  if (['starting', 'running', 'cancel_requested'].includes(existing.state)) {
    state.historyTask = existing;
    renderQQHistoryTask();
    pollQQHistory();
    return;
  }
  state.historyTask = null;
  modal('导入 QQ 漫游历史', qqHistoryPanel(), `<button class="btn" data-action="close-modal">取消</button><button class="btn primary" data-action="qq-history-start">${icon('history')}开始导入</button>`);
}
async function startQQHistory(button) {
  const form = document.querySelector('#qq-history-form');
  if (!form?.reportValidity()) return;
  button.disabled = true;
  const values = Object.fromEntries(new FormData(form));
  try {
    state.historyTask = await api('/api/qq/history-import', null, {method: 'POST', body: JSON.stringify({...values, conversation_id: state.selected.id, account_id: state.selected.account_id})});
    renderQQHistoryTask();
    pollQQHistory();
  } catch (e) {toast(e.message, true);button.disabled = false;}
}
function renderQQHistoryTask() {
  const job = state.historyTask;
  if (!job || !document.querySelector('#qq-history-form, .qq-history-task')) return;
  const active = ['starting', 'running', 'cancel_requested'].includes(job.state);
  modal('QQ 漫游历史', qqHistoryPanel(job), active ? `<button class="btn" data-action="close-modal">后台运行</button><button class="btn" data-action="qq-history-cancel">${icon('x')}取消导入</button>` : '<button class="btn primary" data-action="close-modal">完成</button>', false, false);
}
async function pollQQHistory() {
  while (state.historyTask && ['starting', 'running', 'cancel_requested'].includes(state.historyTask.state)) {
    await new Promise(resolve => setTimeout(resolve, 1000));
    try {state.historyTask = await api('/api/qq/history-import');} catch {continue;}
    renderQQHistoryTask();
  }
  if (state.historyTask && ['completed', 'partial'].includes(state.historyTask.state)) {
    toast(state.historyTask.state === 'partial' ? 'QQ 历史已导入，但结果可能不完整' : 'QQ 漫游历史已导入');
    await refreshSources(false);
    if (state.view === 'chat' && state.selected?.platform === 'qq') await loadView();
  } else if (state.historyTask?.state === 'failed') toast(state.historyTask.error || 'QQ 历史导入失败', true);
}
async function cancelQQHistory(button) {
  button.disabled = true;
  try {await api('/api/qq/history-import/cancel', null, {method: 'POST', body: '{}'});state.historyTask = {...state.historyTask, state: 'cancel_requested', progress: '正在请求停止'};renderQQHistoryTask();}
  catch (e) {toast(e.message, true);button.disabled = false;}
}
function showExport() {
  const title = state.view === 'chat' ? state.selected?.title : '搜索结果';
  if (!title) {toast('请先选择会话');return;}
  modal('导出 ' + title, `<form id="export-form"><div class="export-details"><span>${esc(title)}</span><span>${state.view === 'chat' ? fmt(state.messageTotal) : fmt(state.total)} 条匹配消息</span></div><div class="field"><label>文件格式</label><div class="format-options">${[['json', 'JSON'], ['html', 'HTML'], ['csv', 'CSV'], ['md', 'Markdown'], ['txt', 'TXT']].map(([id, name], index) => `<label class="format-option"><input type="radio" name="format" value="${id}" ${!index ? 'checked' : ''}>${name}</label>`).join('')}</div></div></form>`, '<button class="btn" data-action="close-modal">取消</button><button class="btn primary" data-action="download-export">' + icon('download') + '导出</button>');
}
async function downloadExport(button) {
  button.disabled = true;
  const format = new FormData(document.querySelector('#export-form')).get('format');
  const params = state.view === 'chat' ? {platform: state.selected.platform, account_id: state.selected.account_id, conversation_id: state.selected.id, ...state.messageFilter, format} : {platform: state.platform, ...state.filters, format};
  try {
    const response = await fetch('/api/export?' + new URLSearchParams(params));
    if (!response.ok) throw new Error((await response.json()).error || '导出失败');
    const blob = await response.blob();
    const href = URL.createObjectURL(blob);
    const link = document.createElement('a');link.href = href;link.download = `消息档案-${new Date().toISOString().slice(0, 10)}.${format}`;
    document.body.append(link);link.click();link.remove();setTimeout(() => URL.revokeObjectURL(href), 10000);
    closeModal();toast('导出完成');
  } catch (e) {toast(e.message, true);button.disabled = false;}
}
function showConfigure(id) {
  const directory = id !== 'qq' ? `<div class="field"><label for="data-directory">数据目录</label><input id="data-directory" name="data_dir" placeholder="留空使用自动检测" autocomplete="off"><small>${id === 'wecom' ? '首次配置请填写本机绝对路径；已配置时留空保留当前值。' : '留空保留当前路径；填写时使用本机绝对路径。'}</small></div>` : '';
  const wecom = id === 'wecom' ? `<div class="field"><label for="database-key">数据库密钥</label><input id="database-key" name="key" type="password" placeholder="留空使用本机已保存的密钥" autocomplete="new-password"><small>密钥仅保存在本机配置。</small></div><div class="field"><label for="user-slot-id">账号槽位标识</label><input id="user-slot-id" name="user_slot_id" placeholder="首次配置必填" autocomplete="off"><small>留空时保留已保存的标识。</small></div>` : '';
  const key = id === 'wecom' ? wecom : `<div class="field"><label>内置运行环境</label><span class="small muted mono">${id === 'qq' ? 'QQ Chat Exporter 6.3.2 / Windows x64' : '微信 4.x / 自动读取逐库密钥'}</span></div>`;
  modal('配置 ' + platforms[id].name, `<form id="configure-form" data-source="${id}">${directory}${key}</form>`, '<button class="btn" data-action="close-modal">取消</button><button class="btn primary" data-action="save-config">' + icon('check') + '保存并同步</button>');
}
async function saveConfig(button) {
  button.disabled = true;
  const form = document.querySelector('#configure-form');
  try {
    await api('/api/source/' + form.dataset.source + '/configure', null, {method: 'POST', body: JSON.stringify(Object.fromEntries(new FormData(form)))});
    closeModal();toast('本机配置已保存');await sync(form.dataset.source);
  } catch (e) {toast(e.message, true);button.disabled = false;}
}
async function qqLogin(button) {
  if (button) button.disabled = true;
  try {
    const result = await api('/api/qq/start', null, {method: 'POST', body: JSON.stringify({refresh: Boolean(document.querySelector('.qq-login'))})});
    if (/^data:image\/png;base64,[A-Za-z0-9+/=]+$/.test(result.qr_image || '')) {
      modal('QQ 登录', `<div class="qq-login"><img src="${result.qr_image}" alt="QQ 登录二维码"><p>使用手机 QQ 扫码登录</p><span class="muted small" id="qq-login-phase">等待扫码</span></div>`, `<button class="btn" data-action="qq-login">${icon('refresh-cw')}重新获取二维码</button>`);
    } else toast(result.message || 'QQ 正在连接');
    await refreshSources();
  } catch (e) {toast(e.message, true);}
  finally {if (button) button.disabled = false;}
}
function previewMedia(url, type, name = '') {
  if (!safeUrl(url)) {toast('本机未缓存此媒体');return;}
  const markup = type === 'image' ? `<img class="preview-media" src="${esc(url)}" alt="${esc(name || '聊天图片')}">` : type === 'video' ? `<video class="preview-media" src="${esc(url)}" controls autoplay></video>` : type === 'voice' ? `<audio src="${esc(url)}" controls autoplay></audio>` : `<a class="btn" href="${esc(url)}" download>${icon('download')}下载 ${esc(name || '文件')}</a>`;
  modal(name || typeNames[type] || '媒体', markup, '', true);
}
async function resolveMedia(message, resourceIndex = null) {
  try {
    const result = await api('/api/media/resolve', {platform: message.platform, account_id: message.account_id, message_id: message.id, conversation_id: message.conversation_id, resource_index: resourceIndex});
    if (!result.media_url) {toast(result.message || '本机未缓存此媒体');return;}
    if (resourceIndex !== null) {
      const resource = message.extra?.roaming_resources?.[resourceIndex];
      if (!resource) {toast('漫游附件索引已变化，请刷新会话', true);return;}
      resource.media_url = result.media_url;
      renderView();
      const rawKind = String(resource.type || 'file').toLowerCase();
      const kind = ({audio: 'voice', photo: 'image', picture: 'image', images: 'image', videos: 'video', files: 'file'}[rawKind] || rawKind);
      previewMedia(result.media_url, kind, resource.name);
      return;
    }
    message.media_url = result.media_url;
    renderView();previewMedia(result.media_url, message.type, message.file_name);
  } catch (e) {toast(e.message, true);}
}
async function saveSettings(patch) {
  try {state.settings = await api('/api/settings', null, {method: 'POST', body: JSON.stringify(patch)});if ('mcp_write_enabled' in patch) state.mcp = await api('/api/mcp');renderView();toast('设置已保存');}
  catch (e) {toast(e.message, true);}
}
async function deleteLocalData() {
  if (!window.confirm('清除消息中枢索引、导入暂存、索引备份和 QQ 漫游缓存？平台原始数据、账号配置和其他文件会保留。')) return;
  if (window.prompt('输入“清除本地索引”以继续') !== '清除本地索引') {toast('确认文本不匹配，未删除任何数据');return;}
  try {
    const result = await api('/api/data/delete', null, {method: 'POST', body: JSON.stringify({confirmation: 'DELETE_LOCAL_INDEX'})});
    state.selected = null;state.messages = [];state.conversations = [];state.items = [];state.total = 0;
    state.backups = [];state.importJobs = [];state.mcp = await api('/api/mcp');state.operations = [];
    state.settings = await api('/api/settings');
    await refreshSources(false);await loadView();
    toast(`已清除 ${fmt(result.deleted.length)} 项；平台原始数据、账号配置及其他文件已保留`);
  } catch (e) {toast(e.message, true);}
}
async function saveConversationMetadata(form) {
  const values = new FormData(form);
  try {
    const metadata = await api('/api/conversation-metadata', null, {method: 'POST', body: JSON.stringify({
      platform: state.selected.platform, account_id: state.selected.account_id, conversation_id: state.selected.id,
      note: values.get('note'), tags: String(values.get('tags') || '').split(',').map(tag => tag.trim()).filter(Boolean),
      archived: values.has('archived')
    })});
    state.selected = {...state.selected, ...metadata};
    const current = state.conversations.find(item => sameConversation(item, state.selected));
    if (current) Object.assign(current, metadata);
    renderView();toast('本地整理信息已保存');
  } catch (e) {toast(e.message, true);}
}
function manageContactIdentity(contact) {
  state.managedContact = contact;
  modal('统一身份关联', `<form id="contact-identity-form"><p class="muted small">${esc(contact.name)} · ${platforms[contact.platform]?.name || esc(contact.platform)}</p><label>身份标识<input name="identity_id" maxlength="256" value="${esc(contact.identity_id || '')}" placeholder="例如：同一人的统一 ID" required></label><p class="muted small">不同平台上属于同一人的联系人可填写相同标识。</p><button class="btn primary" type="submit">${icon('link')}保存关联</button></form>`, contact.identity_id ? `<button class="btn" data-action="unlink-contact-identity">${icon('unlink')}解除关联</button>` : '');
}

document.addEventListener('click', async event => {
  const target = event.target.closest('button, [data-item], [data-media-url]');
  if (!target) {if (event.target.classList.contains('overlay-backdrop')) closeModal();return;}
  if (target.dataset.platform) {
    state.remembered[state.platform] = state.selected;
    state.platform = target.dataset.platform;state.selected = state.remembered[state.platform] || null;state.contextMessage = null;state.contextMode = false;
    state.messages = [];state.messageCursor = null;state.messageHasMore = false;state.items = [];state.page = 0;state.pageCursors = [null];renderShell();await loadView();return;
  }
  if (target.dataset.view) {state.view = target.dataset.view;state.items = [];state.filters = {q: '', type: '', start: '', end: ''};state.page = 0;state.pageCursors = [null];renderShell();await loadView();return;}
  if (target.dataset.themeValue) {setTheme(target.dataset.themeValue);renderShell();return;}
  if (target.dataset.conversation !== undefined) {await selectConversation(state.conversations[Number(target.dataset.conversation)]);return;}
  if (target.dataset.contact !== undefined) {
    const contact = state.items[Number(target.dataset.contact)];
    const result = await api('/api/conversations', {platform: contact.platform, q: contact.name, limit: 1000}).catch(() => null);
    const exact = result?.items.find(c => c.account_id === contact.account_id && c.id === contact.id);
    const named = result?.items.filter(c => c.account_id === contact.account_id && c.title === contact.name) || [];
    const conversation = exact || (named.length === 1 ? named[0] : null);
    if (!conversation) {toast('该联系人暂无本机聊天记录');return;}
    state.view = 'chat';state.platform = contact.platform;state.cq = '';state.selected = conversation;state.contextMessage = null;state.contextMode = false;renderShell();await loadView();return;
  }
  if (target.dataset.mediaUrl) {previewMedia(target.dataset.mediaUrl, target.dataset.mediaType);return;}
  if (target.dataset.item !== undefined) {
    const item = state.items[Number(target.dataset.item)];
    if (state.view === 'media') {item.media_url ? previewMedia(item.media_url, item.type, item.file_name) : await resolveMedia(item);}
    else {state.view = 'chat';state.platform = item.platform;state.cq = '';state.selected = {id: item.conversation_id, platform: item.platform, account_id: item.account_id, title: item.conversation_title, kind: item.conversation_kind, message_count: 0};state.contextMessage = item.id;state.contextMode = true;state.messageFilter = {q: '', type: '', start: '', end: ''};renderShell();await loadView();}
    return;
  }
  const action = target.dataset.action;
  if (!action) return;
  if (action === 'theme') {setTheme(document.body.dataset.theme === 'dark' ? 'light' : 'dark');renderShell();}
  else if (action === 'sync' || action.startsWith('sync:')) await sync(action.includes(':') ? action.split(':')[1] : state.platform);
  else if (action === 'back') {state.selected = null;state.contextMessage = null;state.contextMode = false;renderView();}
  else if (action === 'reload') await loadView();
  else if (action === 'dates') {state.showDates = !state.showDates;renderView();}
  else if (action === 'older') {const previous = document.querySelector('.messages');const height = previous?.scrollHeight;await loadMessages(true);renderView();const next = document.querySelector('.messages');if (next && height) next.scrollTop = next.scrollHeight - height;}
  else if (action === 'latest') {const node = document.querySelector('.messages');if (node) node.scrollTop = node.scrollHeight;}
  else if (action === 'details' && state.selected) modal('会话详情', renderInspector(state.selected));
  else if (action === 'export') showExport();
  else if (action === 'download-export') await downloadExport(target);
  else if (action === 'close-modal') closeModal();
  else if (action.startsWith('configure:')) showConfigure(action.split(':')[1]);
  else if (action === 'save-config') await saveConfig(target);
  else if (action === 'qq-login') await qqLogin(target);
  else if (action === 'qq-history') showQQHistory();
  else if (action === 'qq-history-start') await startQQHistory(target);
  else if (action === 'qq-history-cancel') await cancelQQHistory(target);
  else if (action.startsWith('resolve-media:')) {const encoded = action.slice('resolve-media:'.length);const split = encoded.lastIndexOf(':');const resourceIndex = split < 0 ? null : Number(encoded.slice(split + 1));const id = decodeURIComponent(split < 0 ? encoded : encoded.slice(0, split));const message = state.messages.find(m => m.id === id);if (message) await resolveMedia(message, Number.isInteger(resourceIndex) ? resourceIndex : null);}
  else if (action === 'page-prev' && state.page > 0) {state.page--;await loadView();}
  else if (action === 'page-next' && (state.view === 'search' ? !!state.pageCursors[state.page + 1] : (state.page + 1) * 60 < state.total)) {state.page++;await loadView();}
  else if (action === 'toggle-sync') await saveSettings({auto_sync: state.settings.auto_sync === false});
  else if (action === 'toggle-mcp-writes') await saveSettings({mcp_write_enabled: state.settings.mcp_write_enabled !== true});
  else if (action === 'create-backup') await createBackup(target);
  else if (action === 'delete-local-data') await deleteLocalData();
  else if (action === 'restore-backup') await restoreBackup(target);
  else if (action === 'copy-mcp-config') {try {await navigator.clipboard.writeText(state.mcp?.config_text || '');toast('Hermes MCP 配置已复制');} catch {toast('剪贴板不可用，请手动复制配置', true);}}
  else if (action.startsWith('manage-contact:')) manageContactIdentity(state.items[Number(action.split(':')[1])]);
  else if (action === 'unlink-contact-identity' && state.managedContact) {try {await api('/api/contact-identities/unlink', null, {method: 'POST', body: JSON.stringify({platform: state.managedContact.platform, account_id: state.managedContact.account_id, contact_id: state.managedContact.id})});closeModal();toast('已解除统一身份关联');await loadView();} catch (e) {toast(e.message, true);}}
  else if (action === 'preview-import') await uploadImport(true);
  else if (action === 'commit-import') await uploadImport(false);
  else if (action.startsWith('cancel-import:')) await cancelImportJob(decodeURIComponent(action.slice('cancel-import:'.length)));
  else if (action.startsWith('download-backup:')) location.href = '/api/backups/' + decodeURIComponent(action.slice('download-backup:'.length));
});
document.addEventListener('error', event => {
  if (!(event.target instanceof HTMLImageElement)) return;
  const image = event.target;
  if (image.closest('.avatar')) image.replaceWith(document.createTextNode('?'));
  else image.replaceWith(document.createTextNode('图片暂不可用'));
}, true);
document.addEventListener('submit', async event => {
  event.preventDefault();
  const form = event.target;
  if (form.id === 'conversation-search') {state.cq = new FormData(form).get('q');state.selected = null;await loadView();}
  else if (form.id === 'conversation-metadata-form') await saveConversationMetadata(form);
  else if (form.id === 'contact-identity-form' && state.managedContact) {
    try {await api('/api/contact-identities', null, {method: 'POST', body: JSON.stringify({platform: state.managedContact.platform, account_id: state.managedContact.account_id, contact_id: state.managedContact.id, identity_id: new FormData(form).get('identity_id')})});closeModal();toast('统一身份已关联');await loadView();}
    catch (e) {toast(e.message, true);}
  }
  else if (form.id === 'listing-search') {state.filters = {...state.filters, ...Object.fromEntries(new FormData(form))};state.page = 0;state.pageCursors = [null];await loadView();}
  else if (form.id === 'message-search') {state.messageFilter = {...state.messageFilter, ...Object.fromEntries(new FormData(form))};state.messageOffset = 0;state.messageCursor = null;state.messageHasMore = false;state.loading = true;renderView();try {await loadMessages();} catch (e) {state.error = e.message;}finally {state.loading = false;renderView();}}
});
document.addEventListener('change', async event => {
  if (event.target.id === 'sync-interval') await saveSettings({sync_interval: Number(event.target.value)});
  if (event.target.id === 'import-file') {state.importFile = event.target.files?.[0] || null;state.importPreview = null;renderView();}
  if (event.target.id === 'import-platform') {state.importPlatform = event.target.value;state.importPreview = null;renderView();}
  if (event.target.id === 'import-account') {state.importAccount = event.target.value;state.importPreview = null;}
  if (event.target.closest('#message-search') && event.target.name === 'type') event.target.form.requestSubmit();
});
document.addEventListener('input', event => {
  if (event.target.id === 'import-account') {state.importAccount = event.target.value;state.importPreview = null;}
});
let conversationTimer;
document.addEventListener('input', event => {
  if (event.target.closest('#conversation-search')) {clearTimeout(conversationTimer);const input = event.target;conversationTimer = setTimeout(async () => {const value = input.value;state.cq = value;const result = await api('/api/conversations', {platform: state.platform, q: value, limit: 300}).catch(() => null);if (!result || value !== state.cq) return;state.conversations = result.items;state.conversationTotal = result.total;const list = document.querySelector('.conversation-list');if (list) list.innerHTML = result.items.map(renderConversation).join('') || empty('没有匹配的会话');icons();}, 250);}
});
document.addEventListener('keydown', event => {
  if (event.key === 'Escape') closeModal();
  if (event.key === 'Enter' && event.target.dataset.item !== undefined) event.target.click();
  if (event.key === 'Tab' && document.querySelector('.dialog')) {
    const elements = [...document.querySelector('.dialog').querySelectorAll('button:not(:disabled), input, select, a[href], textarea')];
    const first = elements[0], last = elements.at(-1);
    if (event.shiftKey && document.activeElement === first) {event.preventDefault();last?.focus();}
    else if (!event.shiftKey && document.activeElement === last) {event.preventDefault();first?.focus();}
  }
});
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {if (state.theme === 'system') setTheme('system');});

async function init() {
  const openQQ = location.hash === '#qq-login';
  if (openQQ) state.view = 'sources';
  setTheme(state.theme);renderShell();
  try {await Promise.all([refreshSources(false), api('/api/settings').then(settings => {state.settings = settings;})]);}
  catch (e) {state.error = e.message;}
  renderShell();await loadView();
  if (openQQ) await qqLogin();
  let previousCount = state.sources.reduce((a, s) => a + (s.message_count || 0), 0);
  setInterval(async () => {
    try {
      await refreshSources();
      if (state.view === 'imports' && state.importJobs.some(job => ['queued', 'running'].includes(job.state))) await refreshImportJobs();
      if (document.querySelector('.qq-login')) {
        const login = await api('/api/qq/login-status').catch(() => null);
        const label = document.querySelector('#qq-login-phase');
        if (label && login) label.textContent = ({waiting_qrcode: '等待扫码', qrcode_scanned: '请在手机 QQ 确认登录', initializing: '登录成功，正在连接记录服务', offline: '二维码已失效，请重新获取', generating_qrcode: '正在生成二维码'}[login.phase] || '正在验证登录');
        if (login?.online || state.sources.find(s => s.id === 'qq')?.state === 'connected') {
          closeModal();toast('QQ 已登录，正在同步真实记录');await sync('qq');
        }
      }
      const count = state.sources.reduce((a, s) => a + (s.message_count || 0), 0);
      if (count !== previousCount && !state.busy && !document.querySelector('.dialog')) {
        previousCount = count;
        if (state.view !== 'settings' && state.view !== 'sources') await loadView();
      }
    } catch { /* A transient polling failure does not discard visible records. */ }
  }, 5000);
}
init();
