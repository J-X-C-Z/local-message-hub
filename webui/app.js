'use strict';

const platforms = {
  all: {name: '全部平台', icon: 'layers'},
  wechat: {name: '微信', icon: 'message-circle'},
  wecom: {name: '企业微信', icon: 'briefcase-business'},
  qq: {name: 'QQ', icon: 'messages-square'},
  telegram: {name: 'Telegram', icon: 'send'},
  email: {name: '邮件', icon: 'mail'}
};
const views = {
  chat: {name: '聊天记录', icon: 'messages-square', index: '01'},
  search: {name: '全局搜索', icon: 'search', index: '02'},
  contacts: {name: '联系人', icon: 'users-round', index: '03'},
  media: {name: '媒体库', icon: 'image', index: '04'},
  stats: {name: '统计概览', icon: 'chart-no-axes-column', index: '05'},
  sources: {name: '数据源', icon: 'database', index: '06'},
  imports: {name: '导入记录', icon: 'file-up', index: '07'},
  settings: {name: '设置', icon: 'settings-2', index: '08'},
  moments: {name: '朋友圈', icon: 'images', index: '09'},
  campus: {name: '中大学生信息站', icon: 'graduation-cap', index: '10'}
};
const typeNames = {text: '文本', image: '图片', sticker: '表情', video: '视频', voice: '语音', file: '文件', link: '链接', location: '位置', system: '系统消息'};
const qqResources = extra => Array.isArray(extra?.roaming_resources) ? extra.roaming_resources
  : Array.isArray(extra?.media) ? extra.media : [];
function normalizeMediaType(value) {
  const raw = String(value || 'file').trim().toLowerCase();
  return ({audio: 'voice', photo: 'image', picture: 'image', images: 'image', videos: 'video', files: 'file',
    '图片': 'image', '语音': 'voice', '视频': 'video', '文件': 'file'}[raw] || raw);
}
let notificationStream = null;
let accountLockTransition = false;
const MESSAGE_WINDOW_LIMIT = 500;
let sourcePollTimer = null;
let sourcePollBusy = false;
let messageLoadSequence = 0;
const state = {
  platform: 'all', view: 'chat', sources: [], settings: {}, selected: null, contextMessage: null, contextMode: false,
  remembered: {}, conversations: [], conversationTotal: 0, cq: '', messages: [],
  messageTotal: 0, messageOffset: 0, messageCursor: null, messageHasMore: false, messageFilter: {q: '', type: '', start: '', end: ''},
  filters: {q: '', type: '', kind: '', start: '', end: '', source: 'all', account_scope: '', conversation_id: ''}, items: [], total: 0, accountScopes: [], page: 0, pageCursors: [null],
  loading: true, busy: false, error: '', stats: null, statsTab: 'overview', privateProfiles: [], groupProfiles: [], groupMemberLists: new Map(), relationshipProfiles: [], annualReport: null, reportYear: Number(new Intl.DateTimeFormat('en', {timeZone: 'Asia/Hong_Kong', year: 'numeric'}).format(new Date())), reportYearScope: 'year', request: 0, showDates: false, managedContact: null, selectedMedia: new Map(), imageExportTier: 'best', resourceJob: null, exportBundleJob: null, voiceTranscripts: new Map(), wechatRoster: [], groupRosterKey: '', groupRosterRequest: 0,
  theme: localStorage.getItem('hub.theme') || 'light', lastSync: null, backups: [], pendingSends: [], sendReview: null, qqSendReadiness: null, wechatSendReadiness: null, wecomBotIdentity: null, pairReport: null, mcp: null, operations: [], importJobs: [], importFile: null, importPreview: null, importBusy: false, importPlatform: 'all', importAccount: '', moments: {items: [], total: 0}, momentFilter: {q: '', username: '', start: '', end: '', account_id: ''}, momentOffset: 0, momentExporting: false, momentExportFormat: 'json', qqForwardResources: new Map(), qqForwardObjectUrls: new Set()
};
state.selfSentDaily = null;
state.campus = {section: 'selected', briefing: {}, items: [], total: 0, sources: [], profile: {}, refresh: {}, updates: {}, offset: 0,
  filters: {q: '', category: '', source_id: '', campus: '', relevant: '1', bookmarked: ''}};
let campusPollTimer = null;
let lastChatView = 'chat';
const campusSections = {
  selected: {name: '精选', icon: 'sparkles', description: '根据你的学生资料与关注主题，筛选可核对的校园信息。'},
  all: {name: '全部动态', icon: 'rss', description: '按时间浏览已归档信息，同一事件的重复报道合并展示。'},
  hot: {name: '热点', icon: 'flame', description: '最近 48 小时内的事件，按独立来源与报道数量排序。'},
  daily: {name: '校园日报', icon: 'calendar-days', description: '按北京时间的发布日期整理，每条摘要均可回到原始报道。'},
  topics: {name: '主题', icon: 'hash', description: '围绕奖学金、竞赛、选课与其他关注主题阅读。'},
  bookmarks: {name: '收藏', icon: 'bookmark', description: '你保存的校园信息，随时回看原文与正文。'},
  sources: {name: '管理来源', icon: 'panels-top-left', description: '控制参与采集的公开来源与本机公众号。'},
  pipeline: {name: '采集与处理', icon: 'workflow', description: '查看采集、正文、事件合并与模型评分的实际进度。'},
  services: {name: '学生服务', icon: 'graduation-cap', description: '学校与学院的官方服务入口，需登录的信息请到原系统查看。'},
  profile: {name: '学生资料', icon: 'user-round', description: '调整校区、培养层次与关注关键词，让精选更贴近你的需要。'}
};
let mediaPreviewController = null;
const esc = value => String(value ?? '').replace(/[&<>"']/g, x => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[x]));
const safeUrl = value => /^\/(?![\\/])/.test(String(value || '')) && !String(value || '').includes('\\') ? String(value) : '';
const avatarCacheUrl = value => {
  const raw = String(value || '');
  if (/^\/api\/avatar(?:\?|$)/.test(raw)) return raw;
  if (!raw || raw.length > 2048 || raw !== raw.trim()) return '';
  if (/^hub-avatar:\/\/(?:wechat|wecom|qq)\/[a-f0-9]{64}$/.test(raw)) {
    return '/api/avatar?url=' + encodeURIComponent(raw);
  }
  try {
    const parsed = new URL(raw);
    const hosts = new Set(['q.qlogo.cn', 'q1.qlogo.cn', 'p.qlogo.cn', 'thirdqq.qlogo.cn', 'wx.qlogo.cn', 'wework.qpic.cn']);
    if (parsed.protocol !== 'https:' || !hosts.has(parsed.hostname) || parsed.username ||
        parsed.password || (parsed.port && parsed.port !== '443') || parsed.hash ||
        !parsed.pathname.startsWith('/')) return '';
    return '/api/avatar?url=' + encodeURIComponent(raw);
  } catch { return ''; }
};
const icon = (name, extra = '') => `<i data-lucide="${name}" class="icon ${extra}"></i>`;
const numberFormatter = new Intl.NumberFormat('zh-CN');
const dateFormatter = new Intl.DateTimeFormat('zh-CN', {timeZone: 'Asia/Hong_Kong', month: '2-digit', day: '2-digit'});
const dateTimeFormatter = new Intl.DateTimeFormat('zh-CN', {timeZone: 'Asia/Hong_Kong', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false});
const dayFormatter = new Intl.DateTimeFormat('en-CA', {timeZone: 'Asia/Hong_Kong', year: 'numeric', month: '2-digit', day: '2-digit'});
const timeFormatter = new Intl.DateTimeFormat('zh-CN', {timeZone: 'Asia/Hong_Kong', hour: '2-digit', minute: '2-digit', hour12: false});
const fmt = number => numberFormatter.format(Number(number) || 0);
const csvCell = value => {
  let text = String(value ?? '');
  if (/^[=+\-@\t\r]/.test(text)) text = "'" + text;
  return '"' + text.replace(/"/g, '""') + '"';
};
const downloadCsv = (rows, filename) => {
  const blob = new Blob(['\uFEFF' + rows.map(row => row.map(csvCell).join(',')).join('\r\n')], {type: 'text/csv;charset=utf-8'});
  const href = URL.createObjectURL(blob);
  const link = document.createElement('a');link.href = href;link.download = filename;
  document.body.append(link);link.click();link.remove();setTimeout(() => URL.revokeObjectURL(href), 10000);
};
const dt = (stamp, dateOnly = false) => stamp ? (dateOnly ? dateFormatter : dateTimeFormatter).format(new Date(stamp * 1000)) : '—';
const dateKey = stamp => dayFormatter.format(new Date(stamp * 1000));
const timeOnly = stamp => timeFormatter.format(new Date(stamp * 1000));
const responseDuration = seconds => {
  if (seconds == null) return '暂无';
  const value = Math.max(0, Math.round(Number(seconds) || 0));
  if (value < 60) return `${value} 秒`;
  if (value < 3600) return `${Math.floor(value / 60)} 分钟`;
  const hours = Math.floor(value / 3600), minutes = Math.floor(value % 3600 / 60);
  return minutes ? `${hours} 小时 ${minutes} 分钟` : `${hours} 小时`;
};
const badge = platform => `<span class="pill ${esc(platform)}">${esc(platforms[platform]?.name || platform)}</span>`;
const avatarSource = value => avatarCacheUrl(value);
const avatar = (name, platform = '', src = '', extra = '') => {
  const image = avatarSource(src);
  return `<span class="avatar ${esc(platform)} ${extra}">${esc(Array.from(String(name || '?')).slice(0, 1).join(''))}${image ? `<img class="avatar-image" src="${esc(image)}" alt="" loading="lazy">` : ''}</span>`;
};
const iconButton = (name, action, title, extra = '') => `<button type="button" class="icon-btn ${extra}" data-action="${action}" title="${esc(title)}" aria-label="${esc(title)}" ${extra.split(/\s+/).includes('disabled') ? 'disabled aria-disabled="true"' : ''}>${icon(name)}</button>`;
const sourceState = source => ({connected: '已连接', partial: '部分可用', syncing: '同步中', indexing: '索引中', connecting: '连接中', login_required: source?.id === 'qq' ? '待配置本地数据库' : '待登录', locked: '待解密', key_required: '待解密', unsupported: '格式暂不支持', not_found: '未找到数据库', disconnected: '未连接', error: '连接失败'}[source?.state] || '未连接');
const statusDot = source => ['connected', 'partial'].includes(source?.state) ? 'connected' : ['syncing', 'indexing', 'connecting'].includes(source?.state) ? 'syncing' : source?.state === 'error' ? 'error' : '';
let sendComposeRevision = 0;
let sendComposeController = null;
function invalidateSendCompose() {
  sendComposeRevision++;
  sendComposeController?.abort();
  sendComposeController = null;
}
const typeOptions = selected => {
  const mediaTypes = state.filters.source === 'qq-cache'
    ? ['image', 'video', 'voice', 'file']
    : ['image', 'sticker', 'video', 'voice', 'file'];
  const types = Object.entries(typeNames).filter(([key]) => state.view !== 'media' || mediaTypes.includes(key));
  return `<option value="">全部类型</option>${types.map(([key, name]) => `<option value="${key}" ${selected === key ? 'selected' : ''}>${name}</option>`).join('')}`;
};

async function api(path, params, options = {}) {
  if (accountLockTransition) throw new Error('应用已锁定');
  const query = params ? new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== '')).toString() : '';
  const response = await fetch(path + (query ? '?' + query : ''), {...options, headers: {'Content-Type': 'application/json', ...options.headers}});
  if (accountLockTransition) throw new Error('应用已锁定');
  if (!response.ok) {
    const problem = await response.json().catch(() => ({}));
    if (problem.code === 'account_locked') {returnToLockScreen();throw new Error('应用已锁定');}
    throw new Error(problem.error || `请求失败 (${response.status})`);
  }
  return response.json();
}

function icons() {
  if (!window.lucide) return;
  window.lucide.createIcons({attrs: {'stroke-width': 1.65}});
  document.querySelectorAll('svg[data-lucide]').forEach(node => node.removeAttribute('data-lucide'));
}
function toast(message, error = false) {
  if (accountLockTransition) return;
  const node = document.createElement('div');
  node.className = 'toast' + (error ? ' error' : '');
  node.innerHTML = `${icon(error ? 'circle-alert' : 'circle-check')}<span>${esc(message)}</span>`;
  document.querySelector('#toasts').append(node);
  icons();
  setTimeout(() => node.remove(), error ? 6500 : 3500);
}
function returnToLockScreen() {
  if (accountLockTransition) return;
  accountLockTransition = true;
  notificationStream?.close();notificationStream = null;
  document.querySelector?.('#app')?.replaceChildren();
  document.querySelector?.('#toasts')?.replaceChildren();
  document.querySelector?.('#overlay')?.replaceChildren();
  location.reload();
}

function setTheme(theme) {
  state.theme = theme;
  localStorage.setItem('hub.theme', theme);
  const isDark = theme === 'dark' || theme === 'system' && matchMedia('(prefers-color-scheme: dark)').matches;
  document.body.dataset.theme = isDark ? 'dark' : 'light';
}

function renderShell() {
  if (accountLockTransition) return;
  const enabled = id => state.settings.capabilities?.[id]?.enabled !== false;
  const station = state.view === 'campus';
  const mainNav = ['chat', 'search', 'contacts', 'media', 'moments', 'stats'].filter(id => enabled(id));
  const manageNav = ['imports', 'sources', 'settings'].filter(id => id !== 'imports' || enabled('imports'));
  document.querySelector('#app').innerHTML = `<div class="app-shell ${station ? 'station-shell' : ''}">
    <header class="topbar">
      <a class="brand" href="#chat" aria-label="消息中枢"><img src="/assets/hub.svg" alt=""><div><div class="brand-name">消息中枢</div><div class="brand-sub">LOCAL ARCHIVE / 001</div></div></a>
      <span class="brand-divider"></span>
      <div class="platform-switch" role="group" aria-label="聊天平台与信息站">${Object.entries(platforms).map(([id, p]) => {
        const source = state.sources.find(x => x.id === id);
        return `<button class="platform-button ${!station && state.platform === id ? 'active' : ''}" data-platform="${id}" aria-pressed="${!station && state.platform === id}">${icon(p.icon)}<span>${p.name}</span>${id !== 'all' ? `<span class="dot ${statusDot(source)}" title="${sourceState(source)}"></span>` : ''}</button>`;
      }).join('')}${enabled('campus') ? `<button class="platform-button station-switch ${station ? 'active' : ''}" data-station="selected" aria-pressed="${station}">${icon('graduation-cap')}<span>信息站</span></button>` : ''}</div>
      <div class="top-actions"><span class="local-tag">${icon('shield-check')}<span>本机数据</span></span><button class="btn" data-action="sync" ${state.busy || !enabled('sync') ? 'disabled aria-disabled="true"' : ''}>${icon('refresh-cw', state.busy ? 'spin' : '')}<span class="sync-text">同步</span></button>${iconButton(document.body.dataset.theme === 'dark' ? 'sun' : 'moon', 'theme', '切换明暗主题')}</div>
    </header>
    <div class="body-grid">
      <aside class="sidebar" aria-label="${station ? '信息站导航' : '聊天档案导航'}">${station ? renderCampusNavigation() : `<div class="nav-section"><div class="nav-label">档案 / ARCHIVE</div>${mainNav.map(id => navItem(id)).join('')}</div><div class="nav-section"><div class="nav-label">管理 / MANAGE</div>${manageNav.map(id => navItem(id)).join('')}</div><div class="sidebar-sources">${renderMiniSources()}</div>`}<div class="sidebar-footer">${avatar(station ? '中' : '本')}<div><div class="small">${station ? '中大学生信息站' : '本地工作区'}</div><div class="label">${station ? 'SYSU / CAMPUS' : 'WINDOWS / LOCAL'}</div></div></div></aside>
      <main class="content" id="content" aria-label="${views[state.view].name}"></main>
    </div>
    <footer class="footer"><span id="footer-status">${footerStatus()}</span><span>UTC+08:00 <span class="muted">/</span> 本地档案</span></footer>
  </div>`;
  renderView();
}
function navItem(id) { return `<button class="nav-item ${state.view === id ? 'active' : ''}" data-view="${id}" title="${views[id].name}" aria-current="${state.view === id ? 'page' : 'false'}">${icon(views[id].icon)}<span>${views[id].name}</span></button>`; }
function renderCampusNavigation() {
  return [['阅读', ['selected', 'all', 'hot', 'daily', 'topics', 'bookmarks']], ['管理', ['sources', 'pipeline']], ['我的校园', ['services', 'profile']]].map(([label, ids]) => `<nav class="nav-section" aria-label="${label}"><div class="nav-label">${label}</div>${ids.map(id => `<button class="nav-item ${state.campus.section === id ? 'active' : ''}" data-station="${id}" title="${campusSections[id].name}" aria-current="${state.campus.section === id ? 'page' : 'false'}">${icon(campusSections[id].icon)}<span>${campusSections[id].name}</span></button>`).join('')}</nav>`).join('');
}
function campusRoute(hash) {
  const match = /^#campus(?:\/([a-z]+))?$/.exec(hash);
  return match && (!match[1] || Object.hasOwn(campusSections, match[1])) ? match[1] || 'selected' : '';
}
async function navigateCampus(section, updateHash = true) {
  if (!Object.hasOwn(campusSections, section) || state.settings.capabilities?.campus?.enabled === false) return;
  if (state.view !== 'campus') lastChatView = state.view;
  clearMediaSelection();closeModal();
  state.view = 'campus';state.campus.section = section;state.campus.offset = 0;state.campus.items = [];
  if (updateHash && typeof history !== 'undefined') history.replaceState(null, '', '#campus/' + section);
  renderShell();await loadView();
}
function renderMiniSources() { return ['wechat', 'wecom', 'qq', 'email'].map(id => {const s = state.sources.find(x => x.id === id); return `<button class="source-mini" data-platform="${id}" style="width:100%" title="${sourceState(s)}"><span><span class="dot ${statusDot(s)}"></span>${platforms[id].name}</span><span class="mono small">${s?.message_count ? fmt(s.message_count) : '—'}</span></button>`;}).join(''); }
function footerStatus() { return state.busy ? '正在同步本地记录…' : state.sources.some(x => x.state === 'indexing' || x.state === 'syncing') ? '正在建立本地搜索索引…' : `${state.sources.filter(x => ['connected', 'partial'].includes(x.state)).length} / 4 数据源已连接${state.lastSync ? ' · 更新于 ' + dt(state.lastSync) : ''}`; }
function head(extra = '') {const view = views[state.view];return `<div class="view-head"><div><div class="eyebrow">${view.index} / ${platforms[state.platform].name}</div><h1>${view.name}</h1></div><div class="head-actions">${extra}</div></div>`;}
function empty(name, text = '', action = '', symbol = 'inbox') { return `<div class="empty">${icon(symbol)}<h3>${esc(name)}</h3>${text ? `<p>${esc(text)}</p>` : ''}${action}</div>`; }
function loading() { return '<div class="loading-lines" aria-label="加载中">' + '<div class="skeleton"></div>'.repeat(7) + '</div>'; }
function errorStrip() { return state.error ? `<div class="error-strip">${icon('circle-alert')}<span>${esc(state.error)}</span><button class="btn" data-action="reload">重试</button></div>` : ''; }

function renderView() {
  if (accountLockTransition) return;
  const content = document.querySelector('#content');
  if (!content) return;
  revokeQQForwardObjectUrls();
  pruneQQForwardResources();
  content.setAttribute('aria-label', views[state.view].name);
  if (state.view === 'chat') content.innerHTML = renderChat();
  else if (state.view === 'moments') content.innerHTML = renderMoments();
  else if (state.view === 'campus') content.innerHTML = renderCampus();
  else if (state.view === 'sources') content.innerHTML = renderSources();
  else if (state.view === 'imports') content.innerHTML = renderImports();
  else if (state.view === 'settings') content.innerHTML = renderSettings();
  else if (state.view === 'stats') content.innerHTML = renderStats();
  else content.innerHTML = renderListing();
  icons();
  if (state.view === 'chat' && state.selected && !state.loading && !state.messageFilter.q && state.messageOffset <= 100) {
    const messages = content.querySelector('.messages');
    if (messages) {
      const target = state.contextMessage && [...content.querySelectorAll('[data-message-id]')].find(node => node.dataset.messageId === state.contextMessage);
      if (target) target.scrollIntoView({block: 'center'});
      else messages.scrollTop = messages.scrollHeight;
    }
  }
}
function pruneQQForwardResources() {
  const visibleForwardKeys = state.view === 'chat' && state.selected
    ? new Set(state.messages.map(qqForwardResourceKey).filter(Boolean)) : new Set();
  for (const [key, row] of state.qqForwardResources) if (!visibleForwardKeys.has(key)) {
    row.controller?.abort();
    state.qqForwardResources.delete(key);
  }
}

function renderMoments() {
  const f = state.momentFilter;
  const accounts = state.moments.accounts || [];
  const accountPicker = accounts.length > 1 ? `<select id="moments-account" name="account_id" aria-label="朋友圈微信账号">${accounts.map(account => `<option value="${esc(account)}" ${(f.account_id || state.moments.account_id) === account ? 'selected' : ''}>${esc(account)}</option>`).join('')}</select>` : '';
  const filter = `<form id="moments-search" class="filter-row">${accountPicker}<div class="search-field">${icon('search')}<input name="q" aria-label="搜索朋友圈正文" placeholder="搜索朋友圈正文…" value="${esc(f.q)}"></div><input name="username" aria-label="发布者微信号" placeholder="发布者微信号" value="${esc(f.username)}"><label>日期</label><input type="date" name="start" aria-label="开始日期" value="${esc(f.start)}"><input type="date" name="end" aria-label="结束日期" value="${esc(f.end)}"><button class="btn primary" type="submit">${icon('search')}查询</button></form>`;
  const posts = (state.moments.items || []).map(post => {
    const likes = post.likes || [];
    const comments = post.comments || [];
    const media = (post.media || []).map(item => {
      const renderItem = (resource, label) => {
        const src = safeUrl(resource?.media_url);
        const isVideo = resource?.type === 'video';
        const content = src ? isVideo
          ? `<video controls preload="none" src="${esc(src)}" aria-label="${esc(label)}"></video>`
          : `<img src="${esc(src)}" alt="${esc(label)}" loading="lazy">`
          : `<span>${icon(isVideo ? 'video' : 'image')}${esc(label)} · 本机缓存未命中</span>`;
        return `<div class="moment-media">${content}</div>`;
      };
      return `${renderItem(item, item.type === 'video' ? '朋友圈视频' : '朋友圈图片')}${item.livePhoto ? renderItem(item.livePhoto, '朋友圈实况视频') : ''}`;
    }).join('');
    return `<article class="moment-row"><header>${avatar(post.nickname || post.username, 'wechat', post.avatar_url)}<div><strong>${esc(post.nickname || post.username)}</strong><div class="small muted">${esc(post.username)}</div></div><time>${dt(post.createTime)}</time></header>${post.contentDesc ? `<p class="moment-content">${esc(post.contentDesc)}</p>` : ''}${media ? `<div class="moment-media-list">${media}</div>` : ''}${post.location ? `<div class="small muted">${esc([post.location.poiName, post.location.city, post.location.country].filter(Boolean).join(' · '))}</div>` : ''}${likes.length || comments.length ? `<details class="moment-interactions"><summary>${fmt(likes.length)} 个赞 · ${fmt(comments.length)} 条评论</summary>${likes.length ? `<div class="small muted">赞：${likes.map(esc).join('、')}</div>` : ''}${momentCommentsMarkup(comments)}</details>` : ''}</article>`;
  }).join('');
  const page = Math.floor(state.momentOffset / 30) + 1;
  const pager = `<div class="pager"><span>${fmt(state.moments.total)} 条 · 第 ${page} 页</span><div><button class="btn" data-action="moments-prev" ${state.momentOffset <= 0 ? 'disabled' : ''}>${icon('chevron-left')}上一页</button><button class="btn" data-action="moments-next" ${state.momentOffset + 30 >= state.moments.total ? 'disabled' : ''}>下一页${icon('chevron-right')}</button></div></div>`;
  const emptyState = state.loading ? loading() : state.moments.reason === 'moments_account_required' ? empty('选择一个微信账号', '已有多个本机朋友圈索引；请选择账号后查看，内容不会跨账号合并。') : empty('暂无朋友圈记录', '确认微信已登录、已完成本机数据同步，并在设置中启用朋友圈模块。');
  const exportButton = `<select aria-label="朋友圈导出格式" id="moments-export-format">${[['json','JSON'],['csv','CSV'],['md','Markdown'],['txt','TXT'],['html','HTML'],['xlsx','Excel / XLSX']].map(([id,name]) => `<option value="${id}" ${state.momentExportFormat === id ? 'selected' : ''}>${name}</option>`).join('')}</select><button class="btn" data-action="moments-export" ${state.loading || state.momentExporting || !state.moments.total || !state.moments.available ? 'disabled aria-disabled="true"' : ''}>${icon(state.momentExporting ? 'loader-circle' : 'download', state.momentExporting ? 'spin' : '')}<span>${state.momentExporting ? '正在导出' : '导出筛选结果'}</span></button>`;
  return `${head(exportButton)}${filter}<div class="results-summary"><span>只读本机 sns.db 与已缓存媒体 · 不进行网络请求</span><span>${fmt(state.moments.total)} 条</span></div><div class="view-scroll">${posts || emptyState}${state.moments.total > 30 ? pager : ''}</div>`;
}

function momentCommentsMarkup(comments = []) {
  const byId = new Map();
  for (const comment of comments) {
    if (comment.id != null && !byId.has(String(comment.id))) byId.set(String(comment.id), comment);
  }
  return comments.map(comment => {
    const parent = comment.refCommentId == null ? null : byId.get(String(comment.refCommentId));
    const reply = parent && parent !== comment
      ? `<span class="moment-reply">回复 ${esc(parent.nickname || parent.username || '评论')}：</span> `
      : '';
    return `<p class="moment-comment"><strong>${esc(comment.nickname || comment.username || '评论')}</strong> ${reply}${esc(comment.content)}</p>`;
  }).join('');
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
  const canSend = c.platform === 'qq' && state.settings.capabilities?.qq_send?.enabled === true ||
    c.platform === 'wecom' && state.settings.capabilities?.wecom_bot_send?.enabled === true && state.wecomBotIdentity?.configured ||
    c.platform === 'wechat' && state.settings.capabilities?.wechat_send?.enabled === true;
  const canSyncConversation = c.platform === 'qq' && state.settings.capabilities?.sync?.enabled !== false;
  return `<div class="chat-titlebar">${iconButton('chevron-left', 'back', '返回会话列表', 'mobile-back')}${avatar(c.title, c.platform, c.avatar_url)}<div class="title-text"><h2>${esc(c.title)}</h2><div class="chat-subtitle">${badge(c.platform)}<span>${c.kind === 'group' ? '群聊' : '私聊'}</span><span>· ${fmt(state.messageTotal || c.message_count)} 条消息</span></div></div><div class="chat-tools">${canSyncConversation ? iconButton('refresh-cw', 'sync-conversation', '仅重新同步此 QQ 会话') : ''}${iconButton('images', 'conversation-media', '查看此会话媒体')}${iconButton('download', 'export', '导出当前会话')}${canSend ? iconButton('send', 'prepare-send', c.platform === 'wecom' ? '准备企微机器人发送' : c.platform === 'wechat' ? '准备微信文本发送' : '准备 QQ 文本发送') : ''}${iconButton('panel-right', 'details', '会话详情')}</div></div>
    <form id="message-search" class="message-filters"><div class="search-field">${icon('search')}<input name="q" aria-label="搜索当前会话" placeholder="搜索当前会话" value="${esc(f.q)}"></div><select name="type" aria-label="消息类型">${typeOptions(f.type)}</select>${iconButton('calendar-days', 'dates', '日期筛选')}<button class="icon-btn" type="submit" title="查询" aria-label="查询">${icon('arrow-right')}</button>${state.showDates ? `<div class="date-filters"><span>起止日期</span><input type="date" name="start" aria-label="开始日期" value="${esc(f.start)}"><span>至</span><input type="date" name="end" aria-label="结束日期" value="${esc(f.end)}"></div>` : ''}</form>
    <div class="messages" aria-label="消息列表">${state.loading ? loading() : `${!state.contextMode && state.messageHasMore ? `<button class="history-more" data-action="older">${icon('chevron-up')}加载更早消息 · ${fmt(state.messages.length)} / ${fmt(state.messageTotal)}</button>` : ''}${messages || empty('没有匹配的消息', f.q || f.type || f.start || f.end ? '调整筛选条件后重试' : '')}`}</div>
    <div class="chat-bottom"><span>${icon('lock-keyhole')}本地记录 · 只读</span><span class="mono">${fmt(state.messages.length)} / ${fmt(state.messageTotal)} RECORDS</span>${canSend ? `<button class="btn send-compose-action" data-action="prepare-send">${icon('send')}准备发送</button>` : ''}${iconButton('arrow-down', 'latest', '跳到最新消息')}</div>`;
}
function renderMessage(m) {
  if (m.type === 'system') return `<div class="system-message ${m.id === state.contextMessage ? 'context-target' : ''}" data-message-id="${esc(m.id)}">${highlight(m.text)}</div>`;
  const mediaUrl = safeUrl(m.media_url);
  const unsupportedWechatEmoji = m.platform === 'wechat' && ['47', '11000'].includes(String(m.raw_type || ''));
  const roaming = m.platform === 'qq' ? qqResources(m.extra) : m.extra?.roaming_resources || [];
  const resources = (roaming.length ? roaming : (m.platform === 'wecom' ? (m.extra?.attachments || []) : []))
    .map((resource, index) => ({resource, index}))
    .filter(({resource}) => resource?.quoted !== true);
  const voiceKey = JSON.stringify([m.platform, m.account_id, m.id]);
  const voiceTranscript = m.type === 'voice' ? state.voiceTranscripts.get(voiceKey) : null;
  const voiceEnabled = state.settings.capabilities?.voice_transcription?.enabled === true;
  const voiceControl = m.type === 'voice' ? `<div class="voice-transcription">${voiceEnabled ? `<button type="button" class="voice-transcribe-button" data-action="transcribe-voice:${encodeURIComponent(m.id)}" ${voiceTranscript?.state === 'loading' ? 'disabled aria-busy="true"' : ''}>${icon(voiceTranscript?.state === 'loading' ? 'loader-circle' : 'audio-lines')}${voiceTranscript?.state === 'loading' ? '正在本机转写…' : '本机转写'}</button>` : `<button type="button" class="voice-transcribe-button muted" data-action="open-voice-settings">${icon('audio-lines')}转写已关闭 · 打开设置</button>`}${voiceTranscript?.message ? `<span class="voice-transcript-status ${voiceTranscript.state === 'error' || voiceTranscript.state === 'unavailable' ? 'error' : ''}" role="status">${esc(voiceTranscript.message)}</span>` : ''}${voiceTranscript?.transcript ? `<p class="voice-transcript-text">${esc(voiceTranscript.transcript)}</p>` : ''}</div>` : '';
  let attachment = '';
  if (mediaUrl && m.type === 'image' && m.media_mime_type === 'application/x-wechat-wxgf') attachment = `<a class="file-attachment" href="${esc(mediaUrl)}" download>${icon('image')}<span>微信图片 · WXGF 原始文件 <small>无法预览 · 下载原件</small></span></a>`;
  else if (mediaUrl && m.type === 'image') attachment = `<img class="media-img" src="${esc(mediaUrl)}" alt="聊天图片" loading="lazy" data-media-url="${esc(mediaUrl)}" data-media-type="image">`;
  else if (mediaUrl && m.type === 'video') attachment = `<video src="${esc(mediaUrl)}" controls preload="none"></video>`;
  else if (mediaUrl && m.type === 'voice') attachment = `<audio src="${esc(mediaUrl)}" controls preload="none"></audio>`;
  else if (m.type === 'location') {const place = m.extra?.location || {};const lat = Number(place.latitude);const lng = Number(place.longitude);const coords = Number.isFinite(lat) && lat >= -90 && lat <= 90 && Number.isFinite(lng) && lng >= -180 && lng <= 180 ? `${lat.toFixed(5)}, ${lng.toFixed(5)}` : '';attachment = `<div class="wechat-location-card"><strong>${icon('map-pin')}${esc(place.title || '位置')}</strong>${place.address ? `<span>${esc(place.address)}</span>` : ''}${coords ? `<small>${coords}</small>` : ''}</div>`;}
  else if (m.type === 'file' && !resources.length) attachment = `<${mediaUrl ? 'a href="' + esc(mediaUrl) + '" download' : 'div'} class="file-attachment">${icon('file-text')}<span>${esc(m.file_name || m.text || '文件')}${mediaUrl ? '' : iconButton('download', 'resolve-media:' + encodeURIComponent(m.id), '读取文件')}</span></${mediaUrl ? 'a' : 'div'}>`;
  else if (unsupportedWechatEmoji && mediaUrl) attachment = `<img class="media-img" src="${esc(mediaUrl)}" alt="微信表情" loading="lazy" data-media-url="${esc(mediaUrl)}" data-media-type="image">`;
  else if (unsupportedWechatEmoji) attachment = `<div class="file-attachment" title="只读取当前账号已存在的表情缓存">${icon('smile')}<span>表情 <span class="muted small">· 仅本机缓存</span>${iconButton('eye', 'resolve-media:' + encodeURIComponent(m.id), '读取表情缓存')}</span></div>`;
  else if (['image', 'video', 'voice'].includes(m.type) && !resources.length) attachment = `<div class="file-attachment">${icon({image: 'image', video: 'video', voice: 'audio-lines'}[m.type])}<span>${typeNames[m.type]} <span class="muted small">${m.media_available === false ? '· 本机未缓存' : '· 打开时读取'}</span>${iconButton('eye', 'resolve-media:' + encodeURIComponent(m.id), '读取媒体')}</span></div>`;
  if (resources.length) attachment += `<div class="roaming-resources">${resources.map(({resource, index}) => {const kind = normalizeMediaType(resource.type);const url = safeUrl(resource.media_url);const name = resource.name || resource.n || typeNames[kind] || '附件';const size = Number(resource.size);const sizeLabel = Number.isFinite(size) && size > 0 ? ` <small>${esc(size < 1024 ? `${size} B` : size < 1048576 ? `${(size / 1024).toFixed(1)} KB` : `${(size / 1048576).toFixed(1)} MB`)}</small>` : '';if (url && kind === 'image') return `<a class="roaming-image-link" href="${esc(url)}" target="_blank" rel="noreferrer"><img class="media-img" src="${esc(url)}" alt="${esc(name)}" loading="lazy"></a>`;if (url && kind === 'video') return `<video src="${esc(url)}" controls preload="none"></video>`;if (url && kind === 'voice') return `<audio src="${esc(url)}" controls preload="none"></audio>`;if (url) return `<a class="roaming-resource" href="${esc(url)}" download>${icon('file-text')}${esc(name)}${sizeLabel}</a>`;const glyph = icon({image: 'image', video: 'video', voice: 'audio-lines', file: 'file-text'}[kind] || 'paperclip');return resource.available === false ? `<span class="roaming-resource unavailable" title="附件未能下载到本机">${glyph}${esc(name)}${sizeLabel}<small>未缓存</small></span>` : `<button type="button" class="roaming-resource" data-action="resolve-media:${encodeURIComponent(m.id)}:${index}" title="${esc(typeNames[kind] || '附件')} · ${esc(name)}">${glyph}<span>${esc(name)}${sizeLabel}</span></button>`;}).join('')}</div>`;
  const richContent = m.platform === 'qq' ? renderQQRoamingContent(m,
    roaming.length > 0 || (Array.isArray(m.extra?.media) && m.extra.media.length > 0)) : '';
  const quoteData = m.platform === 'wechat' && m.extra?.wechat_quote && typeof m.extra.wechat_quote === 'object'
    ? m.extra.wechat_quote : null;
  const quotePreview = quoteData?.preview && safeUrl(quoteData.preview.media_url)
    ? quoteData.preview.type === 'image'
      ? `<img class="quote-media-preview" src="${esc(quoteData.preview.media_url)}" alt="引用图片" loading="lazy">`
      : quoteData.preview.type === 'video'
        ? `<video class="quote-media-preview" src="${esc(quoteData.preview.media_url)}" controls preload="none"></video>`
        : quoteData.preview.type === 'voice'
          ? `<audio class="quote-media-preview" src="${esc(quoteData.preview.media_url)}" controls preload="none"></audio>` : ''
    : '';
  const quoteContent = quoteData ? `${quoteData.sender ? `<strong>${esc(quoteData.sender)}</strong>` : ''}<span>${esc(quoteData.content || '[引用消息]')}</span>${quotePreview}` : '';
  const quote = quoteData ? quoteData.target_message_id
    ? `<div class="qq-rich-reply quote-preview">${quoteContent}<button type="button" class="quote-jump" data-action="open-wechat-quote" aria-label="查看原引用消息">查看原消息</button></div>`
    : `<blockquote class="qq-rich-reply">${quoteContent}</blockquote>` : '';
  const text = richContent || (attachment ? (m.type === 'file' && !roaming.length) || /^\[.+\]$/.test(m.text || '') ? '' : `<div>${highlight(m.text)}</div>` : highlight(m.text || '[' + (typeNames[m.type] || '消息') + ']'));
  return `<div class="message ${m.is_self ? 'self' : ''} ${m.id === state.contextMessage ? 'context-target' : ''}" data-message-id="${esc(m.id)}">${avatar(m.sender_name || (m.is_self ? '我' : '?'), m.platform, m.sender_avatar_url)}<div class="message-body"><div class="message-meta"><span>${esc(m.sender_name || (m.is_self ? '我' : m.sender_id || '未知发送人'))}${m.extra?.roaming_metadata?.recalled ? ' · 已撤回' : ''}</span><time>${timeOnly(m.timestamp || 1)}</time></div><div class="bubble">${attachment}${quote}${text}${voiceControl}</div></div></div>`;
}

function renderQQRoamingContent(message, hasResources) {
  const elements = message.extra?.roaming_content?.elements;
  if (!Array.isArray(elements) || !elements.length) return '';
  let forwardResourceIndex = 0;
  let forwardCardIndex = 0;
  const forwardCardCount = elements.filter(element => element?.type === 'forward').length;
  const text = value => typeof value === 'string' ? value.trim() : '';
  const lines = elements.slice(0, 100).map(element => {
    if (!element || typeof element !== 'object') return '';
    const data = element.data && typeof element.data === 'object' ? element.data : {};
    switch (element.type) {
      case 'text': return esc(text(data.text));
      case 'at': return `<span class="qq-rich-mention">@${esc(text(data.name) || text(data.uid) || '全体成员')}</span>`;
      case 'face': return esc(text(data.name) || text(data.text) || '[表情]');
      case 'market_face': return hasResources ? '' : esc(text(data.name) || text(data.text) || '[原创表情]');
      case 'reply': {
        const sender = data.sender && typeof data.sender === 'object' ? text(data.sender.name) : text(data.senderName);
        const content = text(data.content) || text(data.text) || text(data.summary);
        const resources = Array.isArray(data.resources) ? data.resources.slice(0, 50).map(resource => {
          if (!resource || typeof resource !== 'object' || !Number.isInteger(resource.resource_index) ||
              resource.resource_index < 0 || resource.resource_index >= 101) return '';
          const type = text(resource.type);
          const name = text(resource.name) || typeNames[type] || '附件';
          const size = Number(resource.size);
          const sizeLabel = Number.isFinite(size) && size > 0 ? ` · ${esc(fmtBytes(size))}` : '';
          const action = `resolve-media:${encodeURIComponent(message.id)}:${resource.resource_index}`;
          return `<button type="button" class="qq-rich-placeholder" data-action="${esc(action)}">${esc(typeNames[type] || '附件')} · ${esc(name)}${sizeLabel} · 读取本机缓存</button>`;
        }).filter(Boolean).join('') : '';
        return `<blockquote class="qq-rich-reply">${sender ? `<strong>${esc(sender)}</strong>` : ''}${content ? `<span>${esc(content)}</span>` : '<span>[回复内容]</span>'}${resources}</blockquote>`;
      }
      case 'json': case 'link': case 'ark': {
        const title = text(data.title) || text(data.summary) || '分享';
        const description = text(data.description);
        return `<div class="qq-rich-card"><strong>${esc(title)}</strong>${description ? `<span>${esc(description)}</span>` : ''}</div>`;
      }
      case 'wallet':
        return `<div class="qq-rich-card"><strong>红包 / 钱包消息</strong>${text(data.summary) ? `<span>${esc(text(data.summary))}</span>` : ''}</div>`;
      case 'live_gift':
        return `<div class="qq-rich-card"><strong>群礼物</strong>${text(data.summary) ? `<span>${esc(text(data.summary))}</span>` : ''}</div>`;
      case 'forward': {
        const isLastForward = ++forwardCardIndex === forwardCardCount;
        const title = text(data.title) || '合并转发';
        let forwardHasResources = false;
        const messages = Array.isArray(data.messages) ? data.messages.slice(0, 10).map(item => {
          if (!item || typeof item !== 'object') return '';
          const sender = item.sender && typeof item.sender === 'object'
            ? text(item.sender.name) || text(item.sender.uin) : text(item.sender);
          const content = item.content && typeof item.content === 'object'
            ? text(item.content.text) : text(item.content);
          const resources = item.content && typeof item.content === 'object' && Array.isArray(item.content.resources)
            ? item.content.resources.slice(0, 10).map(resource => {
              if (!resource || typeof resource !== 'object') return '';
              forwardHasResources = true;
              const type = text(resource.type) || '附件';
              const name = text(resource.name) || type;
              const size = Number(resource.size);
              const sizeLabel = Number.isFinite(size) && size >= 0
                ? ` · ${size < 1024 ? `${size} B` : size < 1048576 ? `${(size / 1024).toFixed(1)} KB` : `${(size / 1048576).toFixed(1)} MB`}` : '';
              const loaded = qqForwardResourceAt(message, forwardResourceIndex++);
              if (loaded?.blob && /^image\//i.test(loaded.mime_type || loaded.blob.type || '')) {
                const url = URL.createObjectURL(loaded.blob);state.qqForwardObjectUrls.add(url);
                return `<img class="qq-forward-image" src="${esc(url)}" alt="${esc(loaded.name || name)}" loading="lazy">`;
              }
              if (loaded?.blob && /^video\/(?:mp4|webm)$/i.test(loaded.mime_type)) {
                const url = URL.createObjectURL(loaded.blob);state.qqForwardObjectUrls.add(url);
                return `<video class="qq-forward-video" controls preload="metadata" aria-label="${esc(loaded.name || name)}"><source src="${esc(url)}" type="${esc(loaded.mime_type)}"></video><button type="button" class="qq-rich-placeholder qq-forward-download" data-action="qq-forward-resource:${loaded.index}">下载 · ${esc(loaded.name || name)}</button>`;
              }
              if (loaded?.error) return `<span class="qq-rich-placeholder error">附件 · ${esc(loaded.type)} · ${esc(loaded.name)} · 读取失败 <button type="button" data-action="qq-forward-resource:${loaded.index}">重试</button></span>`;
              if (loaded && !loaded.blob) return `<button type="button" class="qq-rich-placeholder qq-forward-download" data-action="qq-forward-resource:${loaded.index}">附件 · ${esc(loaded.type)} · ${esc(loaded.name)}${loaded.size ? ` · ${esc(fmtBytes(loaded.size))}` : ''} · 下载</button>`;
              if (loaded?.blob) return `<span class="qq-rich-placeholder">附件 · ${esc(loaded.type)} · ${esc(loaded.name)} · 已读取</span>`;
              return `<span class="qq-rich-placeholder">附件 · ${esc(type)} · ${esc(name)}${sizeLabel} · 未读取</span>`;
            }).filter(Boolean).join('') : '';
          return `${content ? `<span>${esc(`${sender ? `${sender}：` : ''}${content}`)}</span>` : ''}${resources}`;
        }).filter(Boolean) : [];
        const preview = Array.isArray(data.preview) ? data.preview.slice(0, 5).map(text).filter(Boolean) : [];
        const forwardSummary = text(data.summary) || text(data.content);
        const rows = messages.length ? messages
          : preview.length ? preview.map(row => `<span>${esc(row)}</span>`)
            : forwardSummary ? [`<span>${esc(forwardSummary)}</span>`] : [];
        const key = qqForwardResourceKey(message);
        const status = key ? state.qqForwardResources.get(key) : null;
        const extras = isLastForward && status?.status === 'loaded'
          ? status.items.slice(forwardResourceIndex).map((item, offset) => renderQQForwardResource(message, item, forwardResourceIndex + offset)).join('') : '';
        const summary = isLastForward && status?.status === 'loaded'
          ? `<small class="qq-forward-summary">已列出 ${fmt(status.items.length)} 项${status.truncated ? ' · 服务端结果已截断' : ''}</small>` : '';
        const control = !forwardHasResources ? '' : status?.status === 'loading'
          ? '<button type="button" class="qq-forward-read" disabled aria-busy="true">正在读取附件…</button>'
            : status?.status === 'error'
            ? `<button type="button" class="qq-forward-read" data-action="qq-forward-read">读取失败 · 重试</button><span class="qq-forward-error">${esc(status.error || '附件读取失败')}</span>`
            : status?.status === 'loaded'
              ? '' : '<button type="button" class="qq-forward-read" data-action="qq-forward-read">读取附件</button>';
        return `<div class="qq-rich-card"><strong>${esc(title)}</strong>${rows.join('')}${extras}${summary}${control}</div>`;
      }
      case 'location': {
        const name = text(data.name) || text(data.title) || '位置';
        const address = text(data.address);
        return `<div class="qq-rich-card"><strong>位置 · ${esc(name)}</strong>${address ? `<span>${esc(address)}</span>` : ''}</div>`;
      }
      case 'system': return `<span class="qq-rich-system">${esc(text(data.text) || text(data.content))}</span>`;
      case 'image': case 'video': case 'audio': case 'file':
        return hasResources ? '' : `<span class="qq-rich-placeholder">[${{image: '图片', video: '视频', audio: '语音', file: '文件'}[element.type]}]</span>`;
      default: return esc(text(data.text) || text(data.summary) || text(data.content));
    }
  }).filter(Boolean);
  return lines.length ? `<div class="qq-rich-content">${lines.join('')}</div>` : '';
}

function qqForwardResourceKey(message) {
  if (!message || message.platform !== 'qq' || !message.account_id || !message.conversation_id || message.id == null) return '';
  return JSON.stringify([message.account_id, message.conversation_id, message.id]);
}
function qqForwardResourceAt(message, index) {
  const key = qqForwardResourceKey(message);
  const row = key ? state.qqForwardResources.get(key) : null;
  if (!row || row.status !== 'loaded') return null;
  return row.items[index] ? {...row.items[index], index} : null;
}
function renderQQForwardResource(message, item, index) {
  if (item.error) return `<span class="qq-rich-placeholder error">附件 · ${esc(item.type)} · ${esc(item.name)} · 读取失败 <button type="button" data-action="qq-forward-resource:${index}">重试</button></span>`;
  if (item.blob && /^image\//i.test(item.mime_type || item.blob.type || '')) {
    const url = URL.createObjectURL(item.blob);state.qqForwardObjectUrls.add(url);
    return `<img class="qq-forward-image" src="${esc(url)}" alt="${esc(item.name)}" loading="lazy">`;
  }
  if (item.blob && /^video\/(?:mp4|webm)$/i.test(item.mime_type)) {
    const url = URL.createObjectURL(item.blob);state.qqForwardObjectUrls.add(url);
    return `<video class="qq-forward-video" controls preload="metadata" aria-label="${esc(item.name)}"><source src="${esc(url)}" type="${esc(item.mime_type)}"></video><button type="button" class="qq-rich-placeholder qq-forward-download" data-action="qq-forward-resource:${index}">下载 · ${esc(item.name)}</button>`;
  }
  if (item.blob) return `<span class="qq-rich-placeholder">附件 · ${esc(item.type)} · ${esc(item.name)} · 已读取</span>`;
  return `<button type="button" class="qq-rich-placeholder qq-forward-download" data-action="qq-forward-resource:${index}">附件 · ${esc(item.type)} · ${esc(item.name)}${item.size ? ` · ${esc(fmtBytes(item.size))}` : ''} · 下载</button>`;
}
function revokeQQForwardObjectUrls() {
  for (const url of state.qqForwardObjectUrls) URL.revokeObjectURL(url);
  state.qqForwardObjectUrls.clear();
}
async function fetchQQForwardResource(row, signal) {
  row.loading = true;row.error = '';
  try {
    const response = await fetch(`/api/qq/forward-resources/${encodeURIComponent(row.handle)}`, {signal});
    if (!response.ok) {
      const problem = await response.json().catch(() => ({}));
      throw new Error(problem.error || `读取失败 (${response.status})`);
    }
    row.blob = await response.blob();
  } catch (error) {if (error.name !== 'AbortError') row.error = error.message || '读取失败';}
  finally {row.loading = false;}
}
async function forEachWithLimit(items, limit, worker) {
  let index = 0;
  await Promise.all(Array.from({length: Math.min(limit, items.length)}, async () => {
    while (index < items.length) await worker(items[index++]);
  }));
}
async function loadQQForwardResources(message) {
  const key = qqForwardResourceKey(message);
  if (!key || state.qqForwardResources.get(key)?.status === 'loading') return;
  const row = {status: 'loading', items: [], controller: new AbortController()};state.qqForwardResources.set(key, row);renderView();
  try {
    const result = await api('/api/qq/forward-resources', null, {method: 'POST', body: JSON.stringify({
      account_id: message.account_id, conversation_id: message.conversation_id, message_id: message.id
    }), signal: row.controller.signal});
    const received = Array.isArray(result.items) ? result.items : [];
    row.items = received.slice(0, 50).map(item => ({
      handle: item.handle, type: String(item.type || 'file'), name: String(item.name || item.type || '附件'),
      mime_type: String(item.mime_type || ''), size: Number(item.size) || 0, blob: null, error: '', loading: false
    })).filter(item => typeof item.handle === 'string' && item.handle);
    row.truncated = result.truncated === true || received.length > row.items.length || received.length > 50;
    if (row.controller.signal.aborted) return;
    if (!row.items.length) throw new Error('没有可读取的附件');
    row.status = 'loaded';
    const previews = row.items.filter(item => /^image(?:\/|$)/i.test(item.mime_type || item.type) || /^video\/(?:mp4|webm)$/i.test(item.mime_type));
    await forEachWithLimit(previews, 4, item => fetchQQForwardResource(item, row.controller.signal));
  } catch (error) {if (!row.controller.signal.aborted) {row.status = 'error';row.error = error.message || '附件读取失败';}}
  renderView();
}
async function downloadQQForwardResource(row, button) {
  button.disabled = true;
  const message = state.messages.find(item => item.platform === 'qq' && state.qqForwardResources.get(qqForwardResourceKey(item))?.items.includes(row));
  const parent = message && state.qqForwardResources.get(qqForwardResourceKey(message));
  if (!row.blob) await fetchQQForwardResource(row, parent?.controller.signal);
  if (row.blob) {
    const url = URL.createObjectURL(row.blob);state.qqForwardObjectUrls.add(url);
    const link = document.createElement('a');link.href = url;link.download = row.name || '附件';
    document.body.append(link);link.click();link.remove();
    button.disabled = false;
  } else renderView();
}

async function transcribeVoice(message) {
  const key = JSON.stringify([message.platform, message.account_id, message.id]);
  cacheVoiceTranscript(key, {state: 'loading'});
  renderView();
  try {
    const result = await api('/api/voice/transcribe', null, {method: 'POST', body: JSON.stringify({
      platform: message.platform, account_id: message.account_id,
      message_id: message.id, conversation_id: message.conversation_id
    })});
    cacheVoiceTranscript(key, result.available
      ? {state: 'done', transcript: result.transcript || ''}
      : {state: 'unavailable', message: result.message || '本机语音转写不可用。'});
  } catch (error) {
    cacheVoiceTranscript(key, {state: 'error', message: error.message});
  }
  renderView();
}
function cacheVoiceTranscript(key, value) {
  state.voiceTranscripts.delete(key);
  state.voiceTranscripts.set(key, value);
  while (state.voiceTranscripts.size > 100) state.voiceTranscripts.delete(state.voiceTranscripts.keys().next().value);
}
function highlight(text) {
  const escaped = esc(text);
  const q = state.view === 'chat' ? state.messageFilter.q : state.filters.q;
  if (!q) return escaped;
  return escaped.replace(new RegExp(esc(q).replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi'), value => `<mark>${value}</mark>`);
}
function renderInspector(c) {
  const senders = [...new Map(state.messages.filter(x => x.sender_id).map(x => [x.sender_id, x])).values()].slice(0, 7);
  const annual = state.settings.capabilities?.annual_reports;
  const canSend = c.platform === 'qq' && state.settings.capabilities?.qq_send?.enabled === true ||
    c.platform === 'wecom' && state.settings.capabilities?.wecom_bot_send?.enabled === true && state.wecomBotIdentity?.configured ||
    c.platform === 'wechat' && state.settings.capabilities?.wechat_send?.enabled === true;
  const filters = notificationFilters();
  const notificationKey = conversationNotificationKey(c);
  const muted = (filters.muted_conversations || []).includes(notificationKey);
  const allowed = (filters.allowed_conversations || []).includes(notificationKey);
  const notificationsOn = state.settings.capabilities?.notifications?.enabled === true;
  const rosterAction = ['wechat', 'wecom', 'qq'].includes(c.platform) && c.kind === 'group'
    ? `<button class="btn" data-action="conversation-roster">${icon('users-round')}查看完整群成员</button>` : '';
  return `<div class="label">会话详情 / DETAILS</div><div class="inspector-profile">${avatar(c.title, c.platform, c.avatar_url, 'large')}<h3>${esc(c.title)}</h3>${badge(c.platform)}</div>${c.kind !== 'group' && annual?.available !== false && annual?.enabled !== false ? `<button class="btn pair-report-action" data-action="pair-report">${icon('chart-no-axes-column')}生成双人报告</button>` : ''}${canSend ? `<button class="btn send-compose-action" data-action="prepare-send">${icon('send')}${c.platform === 'wecom' ? '准备机器人发送' : c.platform === 'wechat' ? '准备微信文本发送' : '准备 QQ 文本发送'}</button>` : ''}${rosterAction}<dl class="inspector-dl"><dt>会话类型</dt><dd>${c.kind === 'group' ? '群聊' : '私聊'}</dd><dt>消息数量</dt><dd class="mono">${fmt(state.messageTotal || c.message_count)}</dd>${c.member_count ? `<dt>群成员</dt><dd>${fmt(c.member_count)} 人</dd>` : ''}<dt>最近消息</dt><dd>${dt(c.last_timestamp)}</dd><dt>数据来源</dt><dd>本机 · ${platforms[c.platform].name}</dd></dl>${notificationsOn ? `<div class="notification-conversation-controls"><button class="btn" data-action="notification-allow">${icon(allowed ? 'bell-minus' : 'bell-plus')}${allowed ? '移出通知白名单' : '加入通知白名单'}</button><button class="btn" data-action="notification-mute">${icon(muted ? 'bell-ring' : 'bell-off')}${muted ? '取消此会话静音' : '静音此会话通知'}</button></div>` : ''}<form id="conversation-metadata-form" class="inspector-section metadata-form"><h3>本地整理</h3><label>备注<textarea name="note" maxlength="4096" rows="3">${esc(c.note || '')}</textarea></label><label>标签<input name="tags" maxlength="1024" value="${esc((c.tags || []).join(', '))}" placeholder="用逗号分隔"></label><label class="check-label"><input type="checkbox" name="archived" ${c.archived ? 'checked' : ''}>归档会话</label><button class="btn" type="submit">${icon('save')}保存整理信息</button></form><div class="inspector-section"><h3>当前消息中的成员</h3><div class="member-list">${senders.map(item => `<div class="member-row">${avatar(item.sender_name || item.sender_id, c.platform, item.sender_avatar_url)}<span class="truncate">${esc(item.sender_name || item.sender_id)}</span></div>`).join('') || '<span class="muted small">暂无成员信息</span>'}</div></div><button class="btn" data-action="export">${icon('download')}导出会话</button><div class="inspector-note">${icon('shield-check')}原始聊天数据库保持只读</div>`;
}

function renderConversationRoster(members, platform, query = '') {
  const needle = query.trim().toLocaleLowerCase();
  const rows = members.filter(member => [member.name, member.nickname, member.remark, member.id]
    .some(value => String(value || '').toLocaleLowerCase().includes(needle)));
  return `<p class="muted small">${fmt(rows.length)} / ${fmt(members.length)} 位成员</p><div class="member-list roster-list">${rows.map(member => `<div class="member-row">${avatar(member.name || member.id, platform, member.avatar_url)}<span class="truncate"><strong>${esc(member.name || member.id)}</strong><small class="roster-id">${esc(member.id)}</small></span>${member.role === 'owner' || member.is_owner ? '<span class="pill">群主</span>' : member.role === 'admin' ? '<span class="pill">管理员</span>' : ''}</div>`).join('') || '<span class="muted small">没有匹配成员</span>'}</div>`;
}

async function showConversationRoster() {
  const conversation = state.selected;
  if (!conversation || !['wechat', 'wecom', 'qq'].includes(conversation.platform) || conversation.kind !== 'group') return;
  const rosterKey = conversationNotificationKey(conversation);
  const request = ++state.groupRosterRequest;
  state.wechatRoster = [];
  state.groupRosterKey = '';
  const platformName = platforms[conversation.platform]?.name || conversation.platform;
  modal(`${platformName}群成员`, '<div class="muted small">正在读取本机群成员…</div>', '', true, false);
  try {
    const result = await api('/api/conversation-members', {
      platform: conversation.platform, account_id: conversation.account_id,
      conversation_id: conversation.id, limit: 1000
    });
    if (request !== state.groupRosterRequest) return;
    if (!state.selected || conversationNotificationKey(state.selected) !== rosterKey) {
      state.wechatRoster = [];
      state.groupRosterKey = '';
      modal(`${platformName}群成员`, '<p class="muted">当前会话已切换，请重新打开群成员名单。</p>', '<button class="btn" data-action="close-modal">关闭</button>', true, false);
      return;
    }
    state.wechatRoster = result.items || [];
    state.groupRosterKey = rosterKey;
    modal(`${platformName}群成员 · ${fmt(result.total)}`, `<form id="group-roster-export-form"><div class="field"><label>导出格式</label><div class="format-options"><label class="format-option"><input type="radio" name="format" value="csv" checked>CSV</label><label class="format-option"><input type="radio" name="format" value="json">JSON</label></div></div></form><label class="field">搜索群成员<input id="conversation-roster-search" type="search" placeholder="昵称、备注或账号 ID"></label><div id="conversation-roster-results">${renderConversationRoster(state.wechatRoster, conversation.platform)}</div>`, '<button class="btn" data-action="close-modal">关闭</button><button class="btn primary" data-action="download-group-roster">' + icon('download') + '导出名单</button>', true, false);
  } catch (error) {
    if (request === state.groupRosterRequest) modal(`${platformName}群成员`, `<p class="muted">${esc(error.message)}</p>`);
  }
}

function conversationNotificationKey(conversation) {
  return JSON.stringify([conversation.platform, conversation.account_id, conversation.id]);
}

function notificationFilters() {
  return state.settings.notification_filters || {platforms: ['wechat', 'wecom', 'qq'], mode: 'all', allowed_conversations: [], muted_conversations: []};
}

function selectedAccountScope() {
  try {
    const [platform, account_id] = JSON.parse(state.filters.account_scope || 'null');
    return platforms[platform] && typeof account_id === 'string' ? {platform, account_id} : null;
  } catch { return null; }
}

function renderListing() {
  const f = state.filters;
  const isSearch = state.view === 'search';
  const extra = isSearch ? iconButton('download', 'export', '导出搜索结果') : state.view === 'contacts' ? iconButton('download', 'export-contacts', '导出联系人') : '';
  const momentsEnabled = state.settings.capabilities?.moments?.enabled === true;
  const qqCacheView = state.view === 'media' && f.source === 'qq-cache';
  const accountScope = selectedAccountScope();
  const sourceAccountReady = accountScope?.platform === 'wechat';
  const accountFilter = ['contacts', 'media'].includes(state.view) ? `<select name="account_scope" aria-label="${state.view === 'media' ? '媒体账号' : '联系人账号'}"><option value="">全部账号</option>${state.accountScopes.map(account => {const key = JSON.stringify([account.platform, account.account_id]);return `<option value="${esc(key)}" ${accountScope?.platform === account.platform && accountScope?.account_id === account.account_id ? 'selected' : ''}>${esc(platforms[account.platform]?.name || account.platform)} · ${esc(account.account_id)}</option>`;}).join('')}</select>` : '';
  const filter = `<form id="listing-search" class="filter-row">${f.source === 'wechat-source' && state.view === 'media' ? '' : `<div class="search-field">${icon('search')}<input name="q" aria-label="${isSearch ? '搜索所有聊天记录' : '搜索' + views[state.view].name}" placeholder="${isSearch ? '搜索所有聊天记录…' : '搜索' + views[state.view].name + (qqCacheView ? '文件名' : '')}" value="${esc(f.q)}" autocomplete="off"></div>`}${accountFilter}${state.view === 'contacts' ? `<select name="kind" aria-label="联系人类型"><option value="" ${!f.kind ? 'selected' : ''}>全部联系人类型</option><option value="official" ${f.kind === 'official' ? 'selected' : ''}>公众号</option></select>` : ''}${state.view === 'media' ? `<select name="source" aria-label="媒体来源"><option value="all" ${f.source === 'all' ? 'selected' : ''}>全部媒体</option><option value="chat" ${f.source === 'chat' ? 'selected' : ''}>聊天附件</option><option value="wechat-source" ${f.source === 'wechat-source' ? 'selected' : ''} ${sourceAccountReady ? '' : 'disabled'}>微信数据库媒体 ${sourceAccountReady ? '' : '（需选微信账号）'}</option><option value="moments" ${f.source === 'moments' ? 'selected' : ''} ${momentsEnabled ? '' : 'disabled'}>朋友圈</option><option value="qq-cache" ${f.source === 'qq-cache' ? 'selected' : ''}>QQ 缓存文件</option></select>${!qqCacheView ? `<input name="conversation_id" aria-label="精确会话 ID" placeholder="会话 ID（留空浏览全部微信会话）" value="${esc(f.conversation_id || '')}">` : ''}` : ''}${state.view !== 'contacts' ? `<select name="type" aria-label="消息类型">${typeOptions(f.type)}</select>` : ''}${(isSearch || state.view === 'media') ? `<label>日期</label><input type="date" name="start" aria-label="开始日期" value="${esc(f.start)}"><input type="date" name="end" aria-label="结束日期" value="${esc(f.end)}">` : ''}<button class="btn primary" type="submit">${icon('search')}查询</button></form>`;
  let body;
  if (state.loading) body = loading();
  else if (!state.items.length) body = empty(isSearch && !f.q ? '搜索你的聊天档案' : '没有匹配的记录', state.error || (isSearch && !f.q ? '关键词、时间与消息类型' : '可调整筛选条件或查看数据源连接状态'), '', isSearch ? 'search' : views[state.view].icon);
  else if (state.view === 'contacts') body = `<table class="contact-table"><thead><tr><th>联系人</th><th>来源</th><th>类型</th><th>统一身份</th><th></th></tr></thead><tbody>${state.items.map((c, i) => {const profile = c.extra || {};const alternate = [...new Set([profile.remark, profile.nickname, profile.nick_name, profile.alias].filter(value => value && value !== c.name))].join(' · ');const contactKind = c.kind === 'group' ? '群聊' : c.kind === 'official' ? ({subscription: '订阅号', service: '服务号', enterprise: '企业号'}[profile.official_account_kind] || '公众号') : '联系人';return `<tr><td><button class="contact-cell" data-contact="${i}" title="打开聊天记录">${avatar(c.name, c.platform, c.avatar_url)}<span class="contact-names"><strong>${esc(c.name)}</strong>${alternate ? `<small>${esc(alternate)}</small>` : ''}</span></button></td><td>${badge(c.platform)}</td><td class="muted small">${contactKind}</td><td class="mono small">${esc(c.identity_id || '—')}</td><td>${iconButton('contact-round', `manage-contact:${i}`, c.identity_id ? '编辑身份关联' : '关联统一身份')}</td></tr>`;}).join('')}</tbody></table>`;
  else if (state.view === 'media') body = `<div class="view-scroll"><div class="media-grid">${mediaEntries().map(renderMediaTile).join('')}</div></div>`;
  else body = state.items.map((m, i) => `<article class="result-row" data-item="${i}" tabindex="0">${avatar(m.conversation_title, m.platform, m.conversation_avatar_url)}<div><div class="result-heading"><strong>${esc(m.conversation_title || m.conversation_id)}</strong>${badge(m.platform)}<span class="small muted">${typeNames[m.type] || '消息'}</span></div><p>${highlight(m.text || '[' + (typeNames[m.type] || '消息') + ']')}</p><div class="sender">${avatar(m.sender_name || (m.is_self ? '我' : '未知发送人'), m.platform, m.sender_avatar_url)}${esc(m.sender_name || (m.is_self ? '我' : '未知发送人'))}</div></div><time>${dt(m.timestamp)}</time></article>`).join('');
  const resourceToolbar = state.view === 'media' ? renderResourceToolbar() : '';
  const resultSummary = state.view === 'search' && !f.q ? '全部平台可联合查询' : state.view === 'media' ? `${fmt(state.total)} 个附件 · 本页 ${fmt(mediaEntries().length)} 项` : `${fmt(state.total)} 条记录`;
  return `${head(extra)}${errorStrip()}${filter}<div class="results-summary"><span>${resultSummary}</span><span>${accountScope ? `${platforms[accountScope.platform].name} · ${accountScope.account_id}` : platforms[state.platform].name}</span></div>${resourceToolbar}<div class="view-scroll flat">${body}${state.total > 60 ? pager() : ''}</div>`;
}
function mediaEntries() {
  return state.items.flatMap(message => {
    if (message.media_entry) {
      const resource = message.media_entry.resource;
      const resourceIndex = message.media_entry.resource_index;
      const type = normalizeMediaType(message.media_entry.type || message.type);
      return [{message, resource, resourceIndex, type, resourceKind: message.media_entry.resource_kind || message.resource_kind || 'message',
        name: resource?.name || resource?.n || message.file_name || typeNames[type] || '附件'}];
    }
    const resources = message.platform === 'qq' ? qqResources(message.extra)
      : message.extra?.roaming_resources?.length
        ? message.extra.roaming_resources
      : message.platform === 'wecom' && Array.isArray(message.extra?.attachments)
        ? message.extra.attachments
        : [];
    if (resources.length) return resources.map((resource, resourceIndex) => {
      const type = normalizeMediaType(resource.type);
      return {message, resource, resourceIndex, type, name: resource.name || typeNames[type] || '附件'};
    });
    return [{message, resource: null, resourceIndex: null, type: message.type,
      resourceKind: message.extra?.resource_kind || message.resource_kind || 'message',
      name: message.platform === 'wechat' && ['47', '11000'].includes(String(message.raw_type || ''))
        ? '微信表情（仅本机缓存）' : message.file_name || typeNames[message.type] || '附件'}];
  });
}
async function openMessageContext(message) {
  state.view = 'chat';state.platform = message.platform;state.cq = '';
  state.selected = {id: message.conversation_id, platform: message.platform, account_id: message.account_id, title: message.conversation_title, kind: message.conversation_kind, message_count: 0};
  state.contextMessage = message.id;state.contextMode = true;state.messageFilter = {q: '', type: '', start: '', end: ''};
  renderShell();await loadView();
}
async function openWechatQuote(referenceMessageId) {
  const conversation = state.selected;
  if (!conversation || conversation.platform !== 'wechat') return;
  try {
    const context = await api('/api/quoted-message', {platform: 'wechat', account_id: conversation.account_id,
      conversation_id: conversation.id, reference_message_id: referenceMessageId, before: 8, after: 8});
    if (!sameConversation(conversation, state.selected)) return;
    await openMessageContext({id: context.target_message_id, platform: 'wechat',
      account_id: conversation.account_id, conversation_id: conversation.id,
      conversation_title: conversation.title, conversation_kind: conversation.kind});
  } catch (error) {toast(error.message || '引用消息未在本机会话中唯一匹配', true);}
}
function mediaEntryKey(entry) {
  const message = entry.message;
  if (entry.resourceKind === 'moment') return JSON.stringify(['moment', message.account_id, message.post_id, message.media_index, message.variant]);
  if (entry.resourceKind === 'qq_cache') return JSON.stringify(['qq_cache', message.account_id, message.extra?.resource_id || message.id]);
  if (entry.resourceKind === 'wechat_source') return JSON.stringify(['wechat_source', message.account_id,
    message.conversation_id, message.extra?.shard, message.extra?.local_id]);
  return JSON.stringify([message.platform, message.account_id, message.id, entry.resourceIndex]);
}
function mediaEntryAvailable(entry) {
  if (entry.resourceKind === 'moment') return Boolean(entry.resource?.media_url);
  return entry.resource?.available !== false && entry.message.media_available !== false;
}
function renderMediaTile(entry, i) {
  const message = entry.message;
  const resource = entry.resource;
  const cachedEmoji = message.platform === 'wechat' && ['47', '11000'].includes(String(message.raw_type || ''));
  const imageUrl = safeUrl(entry.type === 'video'
    ? resource?.media_poster_url || message.media_poster_url : resource?.media_url || message.media_url);
  const image = (['image', 'video'].includes(entry.type) || cachedEmoji) && imageUrl;
  const key = mediaEntryKey(entry);
  const available = mediaEntryAvailable(entry);
  const size = Number(resource?.size ?? message.extra?.size);
  const sizeLabel = entry.resourceKind === 'qq_cache' && Number.isFinite(size) && size > 0
    ? `<small>${esc(fmtBytes(size))}</small>` : '';
  const canOpenChat = message.conversation_id && message.id != null && !['moment', 'qq_cache', 'wechat_source'].includes(entry.resourceKind);
  const mimeType = entry.type === 'video'
    ? resource?.media_poster_mime_type || message.media_poster_mime_type
    : resource?.mime_type || message.extra?.mime_type || message.media_mime_type;
  const preview = image ? `<img src="${esc(localMediaPreviewUrl(imageUrl, mimeType))}" alt="${esc(entry.name)}" loading="lazy">`
    : icon(cachedEmoji ? 'smile' : {video: 'video', voice: 'audio-lines', file: 'file-text', image: 'image'}[entry.type] || 'file');
  const hint = entry.type === 'video' && imageUrl
    ? '<span class="media-hint">视频封面</span>'
    : entry.resourceIndex !== null || ['moment', 'qq_cache'].includes(entry.resourceKind)
    ? '<span class="media-hint">本机附件 · 点击预览</span>' : '';
  const title = cachedEmoji ? 'title="只读取当前账号已存在的表情缓存"' : '';
  const unavailable = cachedEmoji && !available ? '<small class="muted">本机未缓存表情</small>' : '';
  const sender = message.sender_name || message.sender_avatar_url
    ? `<div class="media-sender">${avatar(message.sender_name || '发送者', message.platform, message.sender_avatar_url, 'media-avatar')}<span class="truncate">${esc(message.sender_name || '发送者')}</span></div>` : '';
  const canLoadBestWechatImage = message.platform === 'wechat' && entry.type === 'image'
    && ['message', 'wechat_source'].includes(entry.resourceKind) && entry.resourceIndex === null && !cachedEmoji
    && message.conversation_id && message.id != null;
  const actions = [
    entry.type === 'video' && imageUrl ? iconButton('image', `video-poster:${i}`, '查看视频封面') : '',
    canLoadBestWechatImage ? iconButton('maximize-2', `wechat-best-image:${i}`, '读取本机缓存中的最佳画质') : '',
    canOpenChat ? iconButton('message-square-text', `open-media-chat:${i}`, '查看所在聊天') : '',
  ].filter(Boolean).join('');
  return `<article class="media-tile" data-media-preview-tile="${i}"><button type="button" class="media-open" data-item="${i}" ${entry.resourceIndex !== null ? `data-resource-index="${entry.resourceIndex}"` : ''} ${available ? '' : 'disabled'} aria-label="预览${esc(typeNames[entry.type] || '附件')}" ${title}><div class="media-preview">${preview}${hint}</div><div class="media-caption"><strong class="truncate">${esc(entry.name || message.conversation_title || typeNames[entry.type])}</strong><div>${badge(message.platform)}<span>${dt(message.timestamp, true)}</span></div>${sender}${sizeLabel}${unavailable}</div></button>${actions ? `<div class="media-chat-link">${actions}</div>` : ''}<label class="media-select"><input type="checkbox" data-media-select="${i}" ${state.selectedMedia.has(key) ? 'checked' : ''} ${available ? '' : 'disabled'}><span>加入批量导出</span></label></article>`;
}
function cancelMediaPreviews() {
  mediaPreviewController?.observer?.disconnect();
  mediaPreviewController?.abort();
  mediaPreviewController = null;
}
async function startMediaPreviews(request = state.request) {
  cancelMediaPreviews();
  if (state.view !== 'media' || state.loading || request !== state.request) return;
  const pending = mediaEntries().map((entry, index) => ({entry, index})).filter(({entry}) =>
    (entry.resourceKind === 'wechat_source' && entry.message.platform === 'wechat' && ['image', 'video', 'sticker'].includes(entry.type) ||
      entry.resourceKind === 'qq_cache' && entry.message.platform === 'qq' && entry.type === 'image' ||
      entry.message.platform === 'wechat' && ['image', 'video'].includes(entry.type) &&
      (entry.resourceKind || 'message') === 'message' && entry.resourceIndex === null &&
      !['47', '11000'].includes(String(entry.message.raw_type || '')) ||
      entry.message.platform === 'wecom' && entry.type === 'image' &&
      (entry.resourceKind || 'message') === 'message' && entry.resourceIndex !== null ||
      entry.message.platform === 'qq' && entry.type === 'image' &&
      (entry.resourceKind || 'message') === 'message' && entry.resourceIndex !== null ||
      entry.message.platform === 'qq' && entry.type === 'video' && entry.message.extra?.local_database === true &&
      (entry.resourceKind || 'message') === 'message') &&
    !safeUrl(entry.type === 'video'
      ? entry.resource?.media_poster_url || entry.message.media_poster_url
      : entry.resource?.media_url || entry.message.media_url) &&
    (entry.type === 'video' || entry.message.media_available !== false));
  if (!pending.length) return;
  const controller = new AbortController();
  const queue = [];
  const queued = new Set();
  let active = 0;
  let observer;
  mediaPreviewController = controller;
  const resolve = async ({entry, index}) => {
      const message = entry.message;
      try {
        const params = entry.resourceKind === 'wechat_source'
          ? {resource_kind: 'wechat_source', platform: 'wechat', account_id: message.account_id,
            conversation_id: message.conversation_id, shard: message.extra.shard,
            local_id: String(message.extra.local_id),
            ...(entry.type === 'sticker' ? {media_type: 'sticker'}
              : entry.type === 'video' ? {video_variant: 'poster'} : {image_tier: 'mid'})}
          : entry.resourceKind === 'qq_cache'
          ? {resource_kind: 'qq_cache', platform: 'qq', account_id: message.account_id,
            resource_id: message.extra?.resource_id || message.id}
          : {platform: message.platform, account_id: message.account_id, message_id: message.id,
            conversation_id: message.conversation_id,
            ...(entry.resourceIndex !== null ? {resource_index: entry.resourceIndex} : {}),
            ...(entry.type === 'video' ? {video_variant: 'poster'}
              : entry.resourceIndex === null ? {image_tier: 'mid'} : {})};
        const result = await api('/api/media/resolve', params, {signal: controller.signal});
        if (controller.signal.aborted || request !== state.request || state.view !== 'media') return;
        const url = safeUrl(result.media_url);
        if (url) {
          if (entry.type === 'video') {
            const target = entry.resource || message;
            target.media_poster_url = url;
            target.media_poster_mime_type = result.mime_type;
          } else if (entry.resourceIndex !== null) {
            entry.resource.media_url = url;
            entry.resource.mime_type = result.mime_type || entry.resource.mime_type;
          }
          else {
            message.media_url = url;
            message.media_mime_type = result.mime_type || message.media_mime_type;
          }
          const preview = document.querySelector(`.media-tile[data-media-preview-tile="${index}"] .media-preview`);
          if (preview) preview.innerHTML = `<img src="${esc(localMediaPreviewUrl(url, result.mime_type))}" alt="${esc(entry.name)}" loading="lazy">${entry.type === 'video' ? '<span class="media-hint">视频封面</span>' : ''}`;
          if (entry.type === 'video') {
            const tile = document.querySelector(`.media-tile[data-media-preview-tile="${index}"]`);
            if (tile?.outerHTML) tile.outerHTML = renderMediaTile(entry, index);
          }
        } else if (entry.type !== 'video') {
          message.media_available = false;
          const tile = document.querySelector(`.media-tile[data-media-preview-tile="${index}"]`);
          const button = tile?.querySelector('.media-open');
          const checkbox = tile?.querySelector('[data-media-select]');
          if (button) button.disabled = true;
          if (checkbox) checkbox.disabled = true;
        }
      } catch (error) {
        if (error.name === 'AbortError' || controller.signal.aborted) return;
      } finally {
        active--;
        drain();
      }
  };
  const drain = () => {
    while (!controller.signal.aborted && active < 3 && queue.length) {
      active++;
      void resolve(queue.shift());
    }
  };
  observer = new IntersectionObserver((observed) => {
    if (controller.signal.aborted || request !== state.request || state.view !== 'media') return;
    for (const item of observed) {
      if (!item.isIntersecting) continue;
      const index = Number(item.target?.dataset?.mediaPreviewTile);
      const candidate = pending.find(({index: candidateIndex}) => candidateIndex === index);
      if (!candidate || queued.has(index)) continue;
      queued.add(index);
      observer.unobserve(item.target);
      queue.push(candidate);
    }
    drain();
  }, {rootMargin: '240px'});
  controller.observer = observer;
  for (const {index} of pending) {
    const tile = document.querySelector(`.media-tile[data-media-preview-tile="${index}"]`);
    if (tile) observer.observe(tile);
  }
}
async function loadBestWechatImage(index) {
  const entry = mediaEntries()[index];
  if (!entry || entry.message.platform !== 'wechat' || entry.type !== 'image'
      || !['message', 'wechat_source'].includes(entry.resourceKind) || entry.resourceIndex !== null
      || ['47', '11000'].includes(String(entry.message.raw_type || ''))) return;
  const request = state.request;
  const key = mediaEntryKey(entry);
  try {
    const result = await api('/api/media/resolve', entry.resourceKind === 'wechat_source' ? {
      resource_kind: 'wechat_source', platform: 'wechat', account_id: entry.message.account_id,
      conversation_id: entry.message.conversation_id, shard: entry.message.extra.shard,
      local_id: String(entry.message.extra.local_id), image_tier: 'best',
    } : {
      platform: 'wechat', account_id: entry.message.account_id,
      message_id: entry.message.id, conversation_id: entry.message.conversation_id,
      image_tier: 'best',
    });
    const currentEntry = mediaEntries()[index];
    if (request !== state.request || state.view !== 'media'
        || !currentEntry || mediaEntryKey(currentEntry) !== key) return;
    const url = safeUrl(result.media_url);
    if (!url) {toast('本机未缓存更高画质图片');return;}
    entry.message.media_url = url;
    entry.message.media_mime_type = result.mime_type || entry.message.media_mime_type;
    const preview = document.querySelector(`.media-tile[data-media-preview-tile="${index}"] .media-preview`);
    if (preview) preview.innerHTML = `<img src="${esc(localMediaPreviewUrl(url, result.mime_type))}" alt="${esc(entry.name)}" loading="lazy">`;
  } catch (error) {toast(error.message || '无法读取本机最佳画质', true);}
}
function resourceExportItems(entries) {
  return entries.map(entry => {
    const message = entry.message;
    if (entry.resourceKind === 'moment') return {resource_kind: 'moment', platform: 'wechat', account_id: message.account_id,
      post_id: message.post_id, media_index: message.media_index, variant: message.variant || 'original'};
    if (entry.resourceKind === 'wechat_source') return {resource_kind: 'wechat_source', platform: 'wechat',
      account_id: message.account_id, conversation_id: message.conversation_id,
      shard: message.extra.shard, local_id: Number(message.extra.local_id),
      ...(entry.type === 'sticker' ? {media_type: 'sticker'}
        : {image_tier: entry.type === 'image' ? state.imageExportTier : 'best'})};
    if (entry.resourceKind === 'qq_cache') return {resource_kind: 'qq_cache', platform: 'qq',
      account_id: message.account_id, resource_id: message.extra?.resource_id || message.id};
    const item = {platform: message.platform, account_id: message.account_id, message_id: message.id,
      conversation_id: message.conversation_id};
    if (entry.resourceIndex !== null) item.resource_index = entry.resourceIndex;
    if (message.platform === 'wechat' && entry.resourceKind === 'message' && entry.type === 'image'
        && entry.resourceIndex === null) item.image_tier = state.imageExportTier;
    return item;
  });
}
function clearMediaSelection() {
  state.selectedMedia.clear();
}
function renderResourceToolbar() {
  const job = state.resourceJob;
  const running = job && ['queued', 'running', 'cancelling'].includes(job.state);
  const entries = mediaEntries();
  const selected = state.selectedMedia.size;
  const selectable = entries.filter(mediaEntryAvailable);
  const hasWechatImage = [...state.selectedMedia.values()].some(entry => entry.message.platform === 'wechat'
    && ['message', 'wechat_source'].includes(entry.resourceKind) && entry.type === 'image' && entry.resourceIndex === null);
  const allSelected = selectable.length > 0 && selectable.every(entry => state.selectedMedia.has(mediaEntryKey(entry)));
  const progress = job ? `<span class="resource-progress-label">${job.state === 'completed' ? '已完成' : job.state === 'failed' ? '失败' : job.state === 'cancelled' ? '已取消' : '打包中'} ${fmt(job.completed || 0)} / ${fmt(job.total || 0)} · 成功 ${fmt(job.exported || 0)} · 跳过 ${fmt(job.failed || 0)}</span><progress max="${Math.max(1, Number(job.total) || 1)}" value="${Math.min(Number(job.completed) || 0, Number(job.total) || 0)}"></progress>` : '';
  return `<div class="resource-toolbar"><div class="resource-toolbar-copy"><strong>${selected ? `已选 ${fmt(selected)} 项` : '批量附件'}</strong><span>${progress || '可跨页选择；最多 100 项。仅导出平台读取器可取得的本机附件'}</span></div><div class="resource-toolbar-actions">${hasWechatImage ? `<label class="resource-quality">微信图片<select id="media-image-tier" aria-label="微信图片导出质量"><option value="best" ${state.imageExportTier === 'best' ? 'selected' : ''}>最佳可用</option><option value="mid" ${state.imageExportTier === 'mid' ? 'selected' : ''}>中图</option><option value="thumb" ${state.imageExportTier === 'thumb' ? 'selected' : ''}>缩略图</option><option value="original" ${state.imageExportTier === 'original' ? 'selected' : ''}>仅原图</option></select></label>` : ''}<button class="btn" data-action="resource-select-page" ${state.loading || !selectable.length ? 'disabled aria-disabled="true"' : ''}>${icon(allSelected ? 'list-minus' : 'list-checks')}${allSelected ? '清空本页' : '选择本页'}</button>${running ? `<button class="btn" data-action="resource-export-cancel" ${job.state === 'cancelling' ? 'disabled aria-disabled="true"' : ''}>${icon('x')}取消</button>` : `<button class="btn primary" data-action="resource-export" ${!selected || selected > 100 || state.loading ? 'disabled aria-disabled="true"' : ''}>${icon('archive')}导出所选</button>`}</div></div>`;
}
function refreshResourceToolbar() {
  const toolbar = document.querySelector('.resource-toolbar');
  if (toolbar) {toolbar.outerHTML = renderResourceToolbar();icons();}
}
function usesCursorPaging() {return state.view === 'search' || (state.view === 'media' && state.filters.source === 'wechat-source');}
function pager() {const hasNext = usesCursorPaging() ? !!state.pageCursors[state.page + 1] : (state.page + 1) * 60 < state.total;return `<div class="pager">${iconButton('chevron-left', 'page-prev', '上一页', state.page === 0 ? 'disabled' : '')}<span>${state.page + 1} / ${Math.ceil(state.total / 60)}</span>${iconButton('chevron-right', 'page-next', '下一页', hasNext ? '' : 'disabled')}</div>`;}
function renderSources() {
  const syncEnabled = state.settings.capabilities?.sync?.enabled !== false;
  return `${head('<button class="btn" data-action="sync" ' + (syncEnabled ? '' : 'disabled aria-disabled="true"') + '>' + icon('refresh-cw') + '同步全部</button>')}<div class="view-scroll view-enter"><div class="section-title"><h2>已集成的数据源</h2><span class="label">4 PLATFORMS</span></div><div class="source-list">${['wechat', 'wecom', 'qq', 'email'].map(id => {
    const s = state.sources.find(x => x.id === id) || {id, state: 'disconnected'};
    const discovery = s.key_discovery || {};
    const canDiscover = id === 'qq' && ['key_required', 'key_invalid'].includes(s.error_code);
    const hasScanCounts = discovery.state !== 'running' &&
      ['processes', 'tested', 'read_mib'].every(key => Number.isInteger(discovery[key]));
    const scanCounts = hasScanCounts
      ? `<div class="source-detail"><span>扫描 ${fmt(discovery.processes)} 个 QQ 进程</span><span>验证 ${fmt(discovery.tested)} 个候选</span><span>读取 ${fmt(discovery.read_mib)} MiB</span></div>` : '';
    return `<section class="source-row"><div class="source-logo ${id}">${icon(platforms[id].icon)}</div><div><div class="source-name"><h2>${platforms[id].name}</h2><span class="pill"><span class="dot ${statusDot(s)}"></span>${sourceState(s)}</span></div><div class="source-detail"><span>${id === 'email' ? 'IMAP 邮箱同步 · EML / MBOX 归档导入' : id === 'qq' ? '内置 QQ 本地数据库读取' : id === 'wechat' ? '内置本地数据库读取' : '内置企业微信读取'}</span>${id === 'email' && s.email_config?.configured ? `<span>${esc(s.email_config.username)} · ${esc(s.email_config.folder || 'INBOX')}</span>` : ''}${s.version ? `<span class="mono">${esc(s.version)}</span>` : ''}</div>${id === 'wechat' && s.image_key_configured ? '<div class="source-detail"><span>图片解密密钥 · 已在本机加密保存</span></div>' : ''}${id === 'wechat' && s.avatar_database?.message ? `<div class="source-detail"><span>头像库 · ${esc(s.avatar_database.message)}</span></div>` : ''}${id === 'qq' && Number.isInteger(s.database_count) ? `<div class="source-detail"><span>${fmt(s.database_count)} 个本地数据库</span></div>` : ''}<div class="source-detail"><span>${fmt(s.conversation_count)} 个会话</span><span>${fmt(s.message_count)} 条已索引消息</span>${s.last_sync ? `<span>更新 ${dt(s.last_sync)}</span>` : ''}</div>${s.error ? `<div class="source-error">${esc(s.error)}</div>` : ''}${canDiscover && discovery.message ? `<div class="source-detail">${esc(discovery.message)}</div>` : ''}${canDiscover ? scanCounts : ''}${s.progress ? `<div class="source-detail mono">${esc(s.progress)}</div>` : ''}</div><div class="head-actions">${canDiscover ? `<button class="btn" data-action="discover-qq-key" ${discovery.state === 'running' || !syncEnabled ? 'disabled aria-disabled="true"' : ''}>${icon(discovery.state === 'running' ? 'loader-circle' : 'key-round')}${discovery.state === 'running' ? '正在只读查找' : '只读发现密钥'}</button>` : ''}${iconButton('refresh-cw', 'sync:' + id, '同步' + platforms[id].name, syncEnabled ? '' : 'disabled')}${iconButton('settings-2', 'configure:' + id, '配置' + platforms[id].name, syncEnabled ? '' : 'disabled')}</div></section>`;
  }).join('')}</div><div class="source-description">${icon('lock-keyhole')}<span>索引、缓存与连接配置保存在本机。原始数据库与邮箱只读。</span></div></div>`;
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
  return `${head()}<div class="view-scroll view-enter import-view"><section class="settings-group"><h2>导入消息与邮件归档</h2><p class="muted small">支持消息中枢 JSON/CSV，Telegram Desktop 单个会话 JSON 导出，以及 EML / MBOX 邮件归档（自动识别为邮件平台）。Telegram 记录为导入数据，不提供原生登录或自动同步。</p><div class="import-fields"><label>默认平台<select id="import-platform">${['all','wechat','wecom','qq','telegram','email'].map(id => `<option value="${id}" ${state.importPlatform === id ? 'selected' : ''}>${platforms[id].name}</option>`).join('')}</select></label><label>默认账号<input id="import-account" type="text" maxlength="128" placeholder="Telegram 可留空以使用导出账号标识" value="${esc(state.importAccount)}"></label></div><label class="btn import-picker">${icon('paperclip')}选择 JSON、CSV、EML 或 MBOX 文件<input id="import-file" type="file" accept=".json,.csv,.eml,.mbox,application/json,text/csv,message/rfc822,application/mbox"></label><span class="small muted import-filename">${esc(state.importFile?.name || '尚未选择文件')}</span><div class="import-actions"><button class="btn primary" data-action="preview-import" ${!state.importFile || state.importBusy ? 'disabled' : ''}>${icon('scan-search')}预览校验</button><button class="btn" data-action="commit-import" ${!report?.valid || state.importBusy ? 'disabled' : ''}>${icon('database-zap')}创建导入任务</button></div>${report ? `<div class="import-report"><div class="history-metrics"><span>${fmt(report.total)} 条记录</span><span>${fmt(report.valid)} 条有效</span><span>${fmt(report.invalid)} 条待修复</span></div>${warnings}${sample}${errors}${report.invalid > (report.errors?.length || 0) ? `<p class="muted small">其余错误已省略，最多显示 100 条。</p>` : ''}</div>` : ''}</section><section class="settings-group"><h2>导入任务</h2><div class="backup-list">${jobs}</div></section></div>`;
}
async function uploadImport(preview) {
  if (!state.importFile || state.importBusy) return;
  state.importPlatform = document.querySelector('#import-platform')?.value || state.importPlatform;
  state.importAccount = document.querySelector('#import-account')?.value || '';
  state.importBusy = true;renderView();
  const file = state.importFile;
  const selectedPlatform = state.importPlatform;
  const extension = file.name.split('.').pop().toLowerCase();
  const mailArchive = ['eml', 'mbox'].includes(extension);
  const platform = mailArchive && state.importPlatform === 'all' ? 'email' : state.importPlatform;
  const account = state.importAccount;
  const format = mailArchive ? extension : extension === 'csv' ? 'csv' : state.importPlatform === 'telegram' ? 'telegram' : 'json';
  const query = new URLSearchParams({format, platform, account, filename: state.importFile.name});
  try {
    const response = await fetch(`/api/imports${preview ? '/preview' : ''}?${query}`, {method: 'POST', body: state.importFile, headers: {'Content-Type': 'application/octet-stream'}});
    const result = await response.json();if (!response.ok) throw new Error(result.error || '导入失败');
    if (preview && (file !== state.importFile || selectedPlatform !== state.importPlatform || account !== state.importAccount)) return;
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
  const notificationBrowserSupported = 'Notification' in window;
  const capabilities = Object.entries(state.settings.capabilities || {}).map(([id, item]) => {
    const browserUnavailable = id === 'notifications' && !notificationBrowserSupported;
    const description = browserUnavailable
      ? '当前浏览器不支持桌面通知，关闭功能仍可使用。'
      : item.available ? (item.core ? '核心功能，始终可用' : '关闭只隐藏入口并拒绝调用，不会删除已索引数据') : item.reason;
    const disabled = item.core || (browserUnavailable && !item.enabled);
    return `<div class="setting-row capability-row"><div><strong>${esc(item.name)}</strong><p>${esc(description)}</p></div>${item.available ? `<button class="toggle ${item.enabled ? 'on' : ''}" role="switch" aria-label="${esc(item.name)}" aria-checked="${item.enabled}" data-action="capability:${esc(id)}" ${disabled ? 'disabled aria-disabled="true"' : ''}></button>` : `<span class="pill unavailable">未开放</span>`}</div>`;
  }).join('');
  const filters = notificationFilters();
  const notificationAvailable = state.settings.capabilities?.notifications?.available === true;
  const accountSafety = state.settings.capabilities?.account_safety;
  const lockSetup = accountSafety?.enabled && !state.settings.account_lock_enabled
    ? `<form id="account-lock-setup" class="account-lock-form"><label>新口令<input name="passphrase" type="password" minlength="12" maxlength="256" autocomplete="new-password" required></label><label>确认口令<input name="confirmation" type="password" minlength="12" maxlength="256" autocomplete="new-password" required></label><button class="btn primary" type="submit">${icon('lock-keyhole')}设置口令并启用锁定</button></form>`
    : accountSafety?.enabled ? `<div class="setting-row"><div><strong>口令锁</strong><p>已设置 · 闲置 15 分钟后自动锁定</p></div><button class="btn" data-action="account-lock-now">${icon('lock')}立即锁定</button></div><form id="account-lock-disable" class="account-lock-form"><label>当前口令<input name="current_passphrase" type="password" autocomplete="current-password" required></label><button class="btn danger" type="submit">${icon('lock-open')}关闭口令锁</button></form>` : '';
  const bot = state.wecomBotIdentity || {};
  const qqSendEnabled = state.settings.capabilities?.qq_send?.enabled === true;
  const qqReady = state.qqSendReadiness;
  const qqReadyText = !qqReady ? (qqSendEnabled ? '尚未检查' : '发送功能已关闭') : qqReady.state === 'online'
    ? `已登录 · ${qqReady.nickname ? `${esc(qqReady.nickname)} · ` : ''}QQ ${esc(qqReady.account_id)}`
    : ({capability_disabled: '发送功能已关闭', not_configured: 'NapCat OneBot 尚未配置',
      unreachable: '本机 NapCat 端点不可达', unauthorized: '端点可达，但本机认证未通过',
      api_unavailable: '端点可达，但登录信息暂不可用', identity_unavailable: '端点可达，未返回登录账号',
      invalid_response: '端点返回了无法识别的状态'}[qqReady.state] || '状态未知');
  const qqReadinessSettings = `<section class="settings-group"><h2>QQ 发送通道</h2><p class="muted small">检查只会向本机 NapCat 请求登录身份，不会发送消息；此检查不会自动运行。</p><div class="setting-row"><div><strong>${esc(qqReadyText)}</strong><p>${qqReady?.endpoint_reachable ? 'OneBot 本机端点可达' : qqReady?.endpoint_configured ? '端点已配置' : '尚未确认端点状态'}${qqReady?.checked_at ? ` · ${dt(qqReady.checked_at)}` : ''}</p></div><button class="btn" data-action="qq-send-readiness">${icon('activity')}检查本机状态</button></div></section>`;
  const wechatSendEnabled = state.settings.capabilities?.wechat_send?.enabled === true;
  const wechatReady = state.wechatSendReadiness;
  const wechatReadyText = !wechatReady ? (wechatSendEnabled ? '尚未检查' : '发送功能已关闭')
    : ({capability_disabled: '发送功能已关闭', window_not_found: '未找到微信窗口',
      client_logged_out: '微信客户端未登录，请先在这台电脑上登录',
      account_unavailable: '找到窗口，但无法确认本机登录账号', unavailable: '本机环境不可用'}[wechatReady.state]
      || (wechatReady.state === 'ready' ? `客户端与账号已就绪 · 本机微信 ${esc(wechatReady.account_id || '')}` : '状态未知'));
  const wechatSendReadinessSettings = `<section class="settings-group"><h2>个人微信发送通道</h2><p class="muted small">检查只读取微信窗口与本机登录账号，不会发送消息；此检查不会自动运行。发送前需打开并唯一识别目标会话。发送时会真实操作微信客户端界面，风控风险由使用者承担。</p><div class="setting-row"><div><strong>${wechatReadyText}</strong><p>${wechatReady?.input_available ? '输入桌面可用' : '输入桌面可能已锁定'}${wechatReady?.checked_at ? ` · ${dt(wechatReady.checked_at)}` : ''}</p></div><button class="btn" data-action="wechat-send-readiness">${icon('activity')}检查本机状态</button></div></section>`;
  const botEnabled = state.settings.capabilities?.wecom_bot_send?.enabled === true;
  const botSettings = `<section class="settings-group"><h2>企业微信机器人身份</h2><p class="muted small">这是机器人账号，不是当前登录员工。每条消息都须在本机单独审核。密钥由 Windows 当前用户 DPAPI 加密保存，不会回显。</p>${botEnabled ? `<div class="setting-row"><div><strong>${esc(bot.identity || '企业微信智能机器人（非当前员工身份）')}</strong><p>${bot.configured ? `已配置 · ID ${esc(bot.bot_id)}` : '尚未配置'}</p></div><span class="pill ${bot.configured ? 'connected' : ''}">${bot.configured ? '本机凭据' : '未配置'}</span></div><form id="wecom-bot-credentials-form" class="account-lock-form"><label>机器人 ID<input name="bot_id" maxlength="128" value="${esc(bot.bot_id || '')}" autocomplete="off" required></label><label>机器人密钥<input name="secret" type="password" minlength="8" maxlength="512" autocomplete="new-password" required></label><button class="btn primary" type="submit">${icon('lock-keyhole')}加密保存机器人凭据</button>${bot.configured ? `<button class="btn danger" type="button" data-action="remove-wecom-bot-credentials">${icon('trash-2')}移除凭据</button>` : ''}</form>` : '<p class="muted small">先在上方启用“企业微信机器人消息发送”，再配置凭据。模块关闭时，机器人身份信息与配置接口均不可访问。</p><button class="btn danger" type="button" data-action="remove-wecom-bot-credentials">移除本机保存的凭据</button>'}</section>`;
  const accountSafetySettings = botSettings + (accountSafety?.enabled ? `<section class="settings-group"><h2>账号安全</h2>${lockSetup}<p class="muted small">锁定本机 WebUI 与 HTTP API。独立配置的 MCP stdio 客户端不受此锁控制。</p></section>` : '');
  const notificationsEnabled = state.settings.capabilities?.notifications?.enabled === true;
  const notificationSettings = notificationAvailable ? `<section class="settings-group"><h2>桌面通知</h2><p class="muted small">仅通知平台与“收到一条新消息”，不显示正文；首次连接会忽略已有记录。</p><div class="notification-settings"><label>接收范围<select id="notification-mode" aria-label="通知接收范围" ${notificationsEnabled ? '' : 'disabled'}><option value="all" ${filters.mode !== 'allowlist' ? 'selected' : ''}>全部所选平台（静音项除外）</option><option value="allowlist" ${filters.mode === 'allowlist' ? 'selected' : ''}>仅白名单会话（静音项仍排除）</option></select></label><label>提示样式<select id="notification-style" aria-label="桌面通知提示样式" ${notificationsEnabled ? '' : 'disabled'}><option value="normal" ${filters.style !== 'silent' ? 'selected' : ''}>标准提示</option><option value="silent" ${filters.style === 'silent' ? 'selected' : ''}>静音提示</option></select></label><div class="notification-platforms">${[['wechat','微信'],['wecom','企业微信'],['qq','QQ']].map(([id,name]) => `<label class="check-label"><input type="checkbox" data-action="notification-platform:${id}" ${(filters.platforms || []).includes(id) ? 'checked' : ''} ${notificationsEnabled ? '' : 'disabled'}>${name}</label>`).join('')}</div><p class="muted small">白名单可在会话详情中管理；静音名单始终优先。</p><button class="btn" type="button" data-action="notification-test" ${notificationsEnabled ? '' : 'disabled'}>${icon('bell')}发送测试通知</button></div></section>` : '';
  return `${head()}<div class="view-scroll view-enter"><section class="settings-group"><h2>功能模块</h2>${capabilities}</section>${qqReadinessSettings}${state.settings.capabilities?.wechat_send?.available ? wechatSendReadinessSettings : ''}${accountSafetySettings}${notificationSettings}<section class="settings-group"><h2>外观</h2><div class="setting-row"><div><strong>主题</strong><p>工作区配色</p></div><div class="segmented">${[['light', '浅色'], ['dark', '深色'], ['system', '系统']].map(([id, name]) => `<button data-theme-value="${id}" class="${state.theme === id ? 'active' : ''}">${name}</button>`).join('')}</div></div></section><section class="settings-group"><h2>同步</h2><div class="setting-row"><div><strong>自动增量同步</strong><p>读取已连接平台的新消息</p></div><button class="toggle ${state.settings.auto_sync !== false ? 'on' : ''}" role="switch" aria-label="自动增量同步" aria-checked="${state.settings.auto_sync !== false}" data-action="toggle-sync"></button></div><div class="setting-row"><div><strong>同步间隔</strong><p>分钟</p></div><select id="sync-interval" aria-label="同步间隔">${[1, 3, 5, 10, 30].map(v => `<option value="${v}" ${Number(state.settings.sync_interval || 5) === v ? 'selected' : ''}>${v}</option>`).join('')}</select></div></section><section class="settings-group"><h2>数据与隐私</h2><div class="setting-row"><div><strong>存储位置</strong><p class="mono">${esc(state.settings.storage_dir || '.local')}</p></div><span class="pill">${icon('hard-drive')}本机</span></div><div class="setting-row"><div><strong>访问地址</strong><p class="mono">${esc(location.origin)}</p></div><span class="pill">仅本机</span></div><div class="setting-row"><div><strong>搜索索引</strong><p>长关键词使用本地三元字符索引，短词回退字面搜索</p></div><span class="pill">${state.settings.fts_enabled ? 'FTS5 trigram' : 'LIKE 回退'}</span></div><div class="setting-row"><div><strong>原始聊天数据库</strong><p>查询与导出使用本地副本</p></div><span class="pill">只读</span></div></section><section class="settings-group"><h2>已集成项目</h2>${[['WeFlow', '5.0.0 / CC BY-NC-SA 4.0'], ['QQ Chat Exporter', '6.3.2 / GPL-3.0'], ['企业微信本地读取器', '只读 / 本地快照'], ['统一索引与导出', '本地 SQLite / MCP']].map(([name, value]) => `<div class="setting-row"><strong>${name}</strong><span class="small muted mono">${value}</span></div>`).join('')}</section></div>`;
}
function renderSettings() {
  const backups = state.backups.map(item => `<div class="backup-row"><div><strong>${esc(item.id)}</strong><span class="small muted">${new Intl.DateTimeFormat('zh-CN', {dateStyle: 'medium', timeStyle: 'short'}).format(new Date(item.created_at * 1000))} · ${fmtBytes(item.size)}</span></div><button class="icon-btn" data-action="download-backup:${encodeURIComponent(item.id)}" title="下载备份" aria-label="下载备份">${icon('download')}</button></div>`).join('') || '<p class="muted small">尚无备份</p>';
  const backupSection = `<section class="settings-group"><h2>本地备份</h2><p class="muted small backup-note">默认备份索引数据库。可选加入索引引用的 QQ 漫游附件；不包含微信/企微源缓存、原始数据库、账号密钥或配置。</p><div class="backup-actions"><label class="check-label"><input id="backup-include-media" type="checkbox">包含已缓存 QQ 附件</label><button class="btn primary" data-action="create-backup">${icon('archive')}创建备份</button><label class="btn">${icon('upload')}选择备份文件<input id="restore-backup-file" type="file" accept=".sqlite3,.db,.zip,application/zip,application/vnd.sqlite3,application/x-sqlite3"></label><button class="btn" data-action="restore-backup">恢复所选</button></div><div class="backup-list">${backups}</div></section>`;
  const operations = (state.mcp?.operations || []).map(item => `<div class="backup-row"><div><strong>${esc(item.tool)}</strong><span class="small muted">${dt(item.timestamp)}</span></div><span class="pill ${item.status === 'success' ? 'connected' : 'error'}">${item.status === 'success' ? '完成' : '失败'}</span></div>`).join('') || '<p class="muted small">暂无 MCP 调用记录</p>';
  const autoStatus = state.settings.auto_export_status || {};
  const autoConfig = autoStatus.config || state.settings.auto_export_config || {};
  const autoEnabled = state.settings.capabilities?.auto_export?.enabled === true;
  const autoLabel = ({idle: '待机', running: '运行中', cancelling: '正在取消', completed: '已完成', cancelled: '已取消', failed: '失败'})[autoStatus.state] || '尚无记录';
  const autoExportSection = `<section class="settings-group"><div class="section-title"><h2>自动导出</h2><span class="pill ${autoStatus.running ? 'connected' : ''}">${autoLabel}</span></div><form id="auto-export-form" class="auto-export-form"><label>平台<select name="platform">${[['all','全部平台'],['wechat','微信'],['wecom','企业微信'],['qq','QQ'],['telegram','Telegram'],['email','邮件']].map(([id,name])=>`<option value="${id}" ${autoConfig.platform === id ? 'selected' : ''}>${name}</option>`).join('')}</select></label><label>格式<select name="format">${[['json','JSON'],['csv','CSV'],['md','Markdown'],['txt','TXT'],['html','HTML'],['xlsx','Excel / XLSX'],['sql','SQL'],['chatlab','ChatLab JSON'],['chatlab-jsonl','ChatLab JSONL']].map(([id,name])=>`<option value="${id}" ${autoConfig.format === id ? 'selected' : ''}>${name}</option>`).join('')}</select></label><label>间隔<select name="interval_hours">${[1,3,6,12,24,168].map(value=>`<option value="${value}" ${Number(autoConfig.interval_hours || 24) === value ? 'selected' : ''}>${value === 168 ? '7 天' : `${value} 小时`}</option>`).join('')}</select></label><label class="auto-export-path">本机输出目录<input name="output_dir" value="${esc(autoConfig.output_dir || '')}" required autocomplete="off" spellcheck="false"></label><div class="auto-export-actions"><label class="check-label"><input type="checkbox" name="enabled" ${autoConfig.enabled ? 'checked' : ''}>定期导出</label><button class="btn" type="submit" ${!autoEnabled ? 'disabled' : ''}>${icon('save')}保存计划</button><button class="btn primary" type="button" data-action="auto-export-run" ${!autoEnabled || autoStatus.running ? 'disabled' : ''}>${icon('download')}立即导出</button>${autoStatus.running ? `<button class="btn danger" type="button" data-action="auto-export-cancel">${icon('x')}取消任务</button>` : ''}</div></form><div class="setting-row"><div><strong>上次执行</strong><p>${autoConfig.last_run_at ? `${dt(autoConfig.last_run_at)} · ${esc(autoConfig.last_status)}${autoConfig.last_file ? ` · ${esc(autoConfig.last_file)}` : ''}` : '尚未执行'}${autoStatus.last_error ? ` · ${esc(autoStatus.last_error)}` : ''}</p></div><span class="pill">最多 ${fmt(autoStatus.max_messages || 250000)} 条 / 次</span></div><p class="muted small">仅从本地索引生成文件，不访问平台源库或网络；功能开关关闭时调度暂停。</p></section>`;
  const management = state.operations.map(item => `<div class="backup-row"><div><strong>${esc(({ 'conversation_metadata.update': '会话整理', 'contact_identity.link': '关联统一身份', 'contact_identity.unlink': '解除身份关联' })[item.action] || item.action)}</strong><span class="small muted">${esc(item.entity_type)} · ${dt(item.timestamp)}</span></div><span class="pill">本地记录</span></div>`).join('') || '<p class="muted small">暂无管理操作</p>';
  const sendHistory = state.pendingSends.map(item => renderSendRequest(item,
    item.platform === 'wechat' ? state.settings.capabilities?.wechat_send?.enabled === true :
      item.platform === 'wecom_bot' ? state.settings.capabilities?.wecom_bot_send?.enabled === true :
      state.settings.capabilities?.qq_send?.enabled === true)).join('') || '<p class="muted small">暂无发送记录</p>';
  const hasSendCapability = state.settings.capabilities?.qq_send?.available || state.settings.capabilities?.wecom_bot_send?.available || state.settings.capabilities?.wechat_send?.available;
  const sendApprovalSection = hasSendCapability ? `<section class="settings-group"><div class="section-title"><h2>发送审核记录</h2><span class="label">MCP 只可准备请求；最终投递必须本机审批</span></div><p class="muted small">每条消息单独确认。机器人身份与员工账号分离；结果未知时请先在对应客户端核实，系统不会自动重试。</p><div class="send-request-list">${sendHistory}</div></section>` : '';
  const mcp = state.mcp;
  const mcpEnabled = mcp?.enabled !== false;
  const mcpSection = `<section class="settings-group"><div class="section-title"><h2>MCP 服务</h2><span class="pill"><span class="dot ${mcp?.available && mcpEnabled ? 'connected' : 'error'}"></span>${!mcpEnabled ? '已关闭' : mcp?.available ? '可用' : '不可用'}</span></div><div class="setting-row"><div><strong>连接方式</strong><p>${esc(mcp?.transport || 'stdio')} · ${esc(mcp?.lifecycle || '读取状态中')}</p></div><span class="pill">客户端按需启动</span></div><div class="setting-row"><div><strong>允许整理元数据</strong><p>启用后 MCP 可修改本地备注、标签、归档和身份关联；聊天内容仍只读</p></div><button class="toggle ${state.settings.mcp_write_enabled === true ? 'on' : ''}" role="switch" aria-label="允许 MCP 修改本地元数据" aria-checked="${state.settings.mcp_write_enabled === true}" data-action="toggle-mcp-writes"></button></div><div class="setting-row"><div><strong>当前权限</strong><p>${mcp?.write_enabled ? '可写入消息中枢元数据，不会修改聊天消息' : '只读查询索引与导入任务状态'}；QQ 发送请求必须在本机审批，MCP 不可批准或投递；更改权限后需重启 MCP 客户端进程</p></div><span class="pill">${mcp?.write_enabled ? '元数据写入' : '只读'}</span></div><div class="mcp-config-head"><strong>Hermes 配置</strong><button class="btn" data-action="copy-mcp-config" ${mcp?.config_text ? '' : 'disabled'}>${icon('copy')}复制配置</button></div><pre class="mcp-config">${esc(mcp?.config_text || '正在读取本机 MCP 配置…')}</pre><div class="section-title mcp-activity-title"><h3>最近调用</h3><span class="label">仅记录工具名与结果，不保存查询内容</span></div><div class="backup-list">${operations}</div></section>`;
  const managementSection = `<section class="settings-group"><div class="section-title"><h2>本地管理记录</h2><span class="label">不记录备注或查询内容</span></div><div class="backup-list">${management}</div></section>`;
  const deleteSection = `<section class="settings-group danger-zone"><h2>清除本地索引</h2><p class="muted small">删除消息中枢索引、导入暂存、索引备份和 QQ 漫游缓存。平台原始数据、账号配置及同目录其他文件会保留。正在运行的任务需先停止。</p><button class="btn danger" data-action="delete-local-data">${icon('trash-2')}清除本地数据</button></section>`;
  return renderSettingsBase().replace('<section class="settings-group"><h2>已集成项目</h2>', sendApprovalSection + autoExportSection + backupSection + deleteSection + mcpSection + managementSection + '<section class="settings-group"><h2>已集成项目</h2>');
}
function renderSendRequest(item, canApprove) {
  const labels = {pending_approval: '待审批', sending: '投递中', sent: '已发送', failed: '发送失败', unknown: '结果未知', rejected: '已拒绝'};
  const pending = item.status === 'pending_approval';
  const status = labels[item.status] || item.status;
  const action = pending && canApprove ? `<button class="icon-btn" data-action="review-send:${esc(item.request_id)}" title="审核发送请求" aria-label="审核发送请求">${icon('clipboard-check')}</button>` : `<span class="pill ${item.status === 'sent' ? 'connected' : item.status === 'unknown' || item.status === 'failed' ? 'error' : ''}">${esc(status)}</span>`;
  const bot = item.platform === 'wecom_bot';
  const wechat = item.platform === 'wechat';
  const detail = item.status === 'unknown' ? `请先在 ${bot ? '企业微信机器人会话' : wechat ? '微信' : 'QQ'} 中核实，避免重复发送。` : item.error_code ? `错误代码：${esc(item.error_code)}` : item.message_id ? `${wechat ? '本机微信' : bot ? '机器人' : 'QQ'} 消息 ID：${esc(item.message_id)}` : '';
  const content = item.attachment ? `附件 · ${esc(item.attachment.filename)} · ${fmtBytes(item.attachment.size)}` : esc(item.text);
  return `<div class="send-request-row"><div><strong>${bot ? '企微机器人身份' : wechat ? '本机微信身份' : 'QQ'} ${esc(item.conversation_type === 'group' ? '群聊' : '私聊')} · ${esc(item.conversation_id)}</strong><span class="small muted">${wechat ? '账号' : bot ? '机器人' : '账号'} ${esc(item.account_id)} · ${dt(item.created_at)} · ${esc(status)}</span><p>${content}</p>${detail ? `<span class="small muted">${detail}</span>` : ''}</div>${action}</div>`;
}
function fmtBytes(value) {const n = Number(value) || 0;return n < 1024 * 1024 ? `${fmt(Math.ceil(n / 1024))} KB` : `${(n / (1024 * 1024)).toFixed(1)} MB`;}
async function createBackup(button) {
  button.disabled = true;
  const includeMedia = document.querySelector('#backup-include-media')?.checked === true;
  try {await api('/api/backups', null, {method: 'POST', body: JSON.stringify({include_media: includeMedia})});state.backups = (await api('/api/backups')).items || [];renderView();toast(includeMedia ? '索引与可用 QQ 附件备份已创建' : '索引数据库备份已创建');}
  catch (e) {toast(e.message, true);button.disabled = false;}
}
async function restoreBackup(button) {
  const file = document.querySelector('#restore-backup-file')?.files?.[0];
  if (!file) {toast('先选择一个备份文件', true);return;}
  if (file.size > 8 * 1024 * 1024 * 1024) {toast('备份文件超过 8 GiB 限制', true);return;}
  const mediaBundle = file.name.toLowerCase().endsWith('.zip');
  const description = mediaBundle ? '将替换当前索引，并把备份中的 Hub 管理 QQ 附件合并到本地漫游缓存' : '将用此文件替换当前索引数据库';
  if (!window.confirm(`${description}。此操作不会更改第三方原始数据库、账号配置或源媒体缓存，确定继续吗？`)) return;
  button.disabled = true;
  try {
    const response = await fetch('/api/backups/restore', {method: 'POST', body: file, headers: {'Content-Type': 'application/octet-stream'}});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || '恢复失败');
    state.backups = (await api('/api/backups')).items || [];state.selected = null;state.messages = [];renderView();toast('索引数据库已恢复');
  } catch (e) {toast(e.message, true);button.disabled = false;}
}
function renderGroupProfileRows(profiles = state.groupProfiles) {
  return profiles.map(item => {
    const key = JSON.stringify([item.platform, item.account_id, item.conversation_id]);
    const full = state.groupMemberLists.get(key);
    const members = full?.expanded && full.members ? full.members : (item.members || []).slice(0, 12);
    const expand = Number(item.observed_member_count) > 12;
    const memberLimitNotice = full?.expanded && full.members && Number(item.observed_member_count) > full.members.length
      ? `<small class="muted">显示 ${fmt(full.members.length)} / ${fmt(item.observed_member_count)} 位已观察成员，单次最多 5,000</small>` : '';
    return `<tr><td><strong>${esc(item.title || item.conversation_id)}</strong><div class="small muted">${badge(item.platform)} · ${fmt(item.member_count)} 位成员 · 已观察 ${fmt(item.observed_member_count)}</div></td><td>${fmt(item.message_count)}</td><td>${fmt(item.sent_count)} / ${fmt(item.received_count)}</td><td>${dt(item.last_timestamp, true)}</td><td><details class="group-members" ${full?.loading || full?.expanded ? 'open' : ''}><summary>成员排行</summary><ol>${members.map(member => `<li><span>${esc(member.name || member.sender_id)}</span><span>${fmt(member.message_count)}</span></li>`).join('') || '<li>暂无成员消息</li>'}</ol>${memberLimitNotice}${expand ? `<button type="button" class="group-members-toggle" data-action="group-members:${encodeURIComponent(key)}" ${full?.loading ? 'disabled aria-busy="true"' : ''}>${full?.loading ? '正在读取…' : full?.expanded ? '收起完整排行' : `查看完整排行 · ${fmt(item.observed_member_count)} 人`}</button>` : ''}${full?.error ? `<small class="error">${esc(full.error)}</small>` : ''}</details></td></tr>`;
  }).join('');
}
async function toggleGroupMembers(key) {
  const cached = state.groupMemberLists.get(key);
  if (cached?.loading) return;
  if (cached?.members) {state.groupMemberLists.set(key, {...cached, expanded: !cached.expanded});renderView();return;}
  const [platform, account_id, conversation_id] = JSON.parse(key);
  const request = state.request;
  state.groupMemberLists.set(key, {loading: true, expanded: true});renderView();
  try {
    const result = await api('/api/analytics/groups', {platform, account_id, conversation_id, limit: 1, member_limit: 5000});
    if (request !== state.request || state.view !== 'stats' || state.statsTab !== 'groups') return;
    const item = result.items?.[0];
    if (!item || item.platform !== platform || item.account_id !== account_id || item.conversation_id !== conversation_id) throw new Error('没有读取到该群的完整排行');
    state.groupMemberLists.set(key, {members: item.members || [], expanded: true});
  } catch (error) {
    if (request !== state.request || state.view !== 'stats' || state.statsTab !== 'groups') return;
    state.groupMemberLists.set(key, {error: error.message, expanded: true});
  }
  renderView();
}
function renderStats() {
  const s = state.stats;
  if (!s) return head() + (state.loading ? loading() : empty('暂无统计', state.error));
  const capability = state.statsTab === 'annual' ? 'annual_reports' : state.statsTab === 'relationships' ? 'relationship_footprint' : 'advanced_analytics';
  const cap = state.settings.capabilities?.[capability];
  const enabled = cap?.available !== false && cap?.enabled !== false;
  const tabs = [['overview', '概览'], ['private', '私聊画像'], ['groups', '群聊分析'], ['daily', '每日发送'], ['annual', '年度报告'], ['relationships', '关系足迹']];
  const tabBar = `<div class="stats-tabs" role="tablist" aria-label="统计视图">${tabs.map(([id, title]) => `<button role="tab" aria-selected="${state.statsTab === id}" class="${state.statsTab === id ? 'active' : ''}" data-action="stats-tab:${id}">${title}</button>`).join('')}</div>`;
  if (state.statsTab !== 'overview' && !enabled) return `${head()}<div class="view-scroll view-enter">${tabBar}${empty('此功能已关闭', cap?.reason || '可在设置中开启对应分析模块。', '<button class="btn" data-view="settings">打开设置</button>', 'chart-no-axes-column')}</div>`;
  if (state.statsTab === 'private') {
    const rows = state.privateProfiles.map(item => `<tr><td><strong>${esc(item.title || item.conversation_id)}</strong><div class="small muted">${badge(item.platform)} · ${esc(item.kind)}</div></td><td>${fmt(item.message_count)}</td><td>${fmt(item.sent_count)} / ${fmt(item.received_count)}</td><td>${Math.round((item.sent_ratio || 0) * 100)}%</td><td>${fmt(item.media_count)}</td><td>${dt(item.last_timestamp, true)}</td><td><button class="icon-btn" data-action="pair-report-profile" data-platform="${esc(item.platform)}" data-account="${esc(item.account_id)}" data-conversation="${esc(item.conversation_id)}" data-title="${esc(item.title || item.conversation_id)}" title="查看双人报告" aria-label="查看双人报告">${icon('chart-no-axes-column')}</button></td></tr>`).join('');
    return `${head()}<div class="view-scroll view-enter">${tabBar}<div class="section-title stats-section-title"><div><h2>私聊会话画像</h2><span class="label">按消息量排序 · ${platforms[state.platform].name}</span></div></div>${state.loading ? loading() : rows ? `<div class="table-scroll"><table class="analytics-table"><thead><tr><th>会话</th><th>消息</th><th>发出 / 收到</th><th>发出占比</th><th>媒体</th><th>最近活动</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>` : empty('暂无私聊消息')}</div>`;
  }
  if (state.statsTab === 'groups') {
    const rows = renderGroupProfileRows();
    return `${head()}<div class="view-scroll view-enter">${tabBar}<div class="section-title stats-section-title"><div><h2>群聊活跃度</h2><span class="label">基于已索引消息中的发言成员 · ${platforms[state.platform].name}</span></div></div>${state.loading ? loading() : rows ? `<div class="table-scroll"><table class="analytics-table"><thead><tr><th>群聊</th><th>消息</th><th>自己发出 / 其他成员</th><th>最近活动</th><th>成员</th></tr></thead><tbody>${rows}</tbody></table></div>` : empty('暂无群聊消息')}</div>`;
  }
  if (state.statsTab === 'relationships') {
    const rows = state.relationshipProfiles.map((item, index) => `<tr><td><strong>${esc(item.title || item.conversation_id)}</strong><div class="small muted">${badge(item.platform)} · ${item.kind === 'group' ? '群聊' : '私聊'}</div></td><td>${fmt(item.message_count)}</td><td>${fmt(item.sent_count)} / ${fmt(item.received_count)}</td><td>${fmt(item.active_days)}</td><td>${fmt(item.media_count)}</td><td>${dt(item.last_timestamp, true)}</td><td><button class="icon-btn" data-action="relationship-timeline:${index}" title="查看按日活动" aria-label="查看按日活动">${icon('calendar-days')}</button></td></tr>`).join('');
    return `${head()}<div class="view-scroll view-enter">${tabBar}<div class="section-title stats-section-title"><div><h2>会话关系足迹</h2><span class="label">只显示互动计数，不包含消息正文 · ${platforms[state.platform].name}</span></div></div>${state.loading ? loading() : rows ? `<div class="table-scroll"><table class="analytics-table"><thead><tr><th>会话</th><th>消息</th><th>发出 / 收到</th><th>活跃日</th><th>媒体</th><th>最近活动</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>` : empty('暂无可汇总的会话')}</div>`;
  }
  if (state.statsTab === 'daily') return `${head()}<div class="view-scroll view-enter">${tabBar}<div class="section-title stats-section-title"><div><h2>每日自身发送强度比例</h2><span class="label">仅统计本人发送 · ${platforms[state.platform].name}${state.platform === 'all' ? ' · 全平台汇总' : ''}</span></div><span class="label">${state.selfSentDaily ? `全期日均 ${Number(state.selfSentDaily.baseline || 0).toFixed(1)} 条/天 · 共 ${fmt(state.selfSentDaily.total_messages || 0)} 条` : ''}</span></div>${state.selfSentDaily ? renderSelfSentDailyChart(state.selfSentDaily) : state.loading ? loading() : empty('暂无自身发送记录')}</div>`;
  if (state.statsTab === 'annual') return renderAnnualReport(s, tabBar);
  const daily = (s.daily || []).slice(-45);
  const max = Math.max(1, ...daily.map(x => x.count));
  const counts = s.per_type || {};
  const overview = s.overview || {};
  const jobState = {queued: '排队中', running: '导入中', completed: '已完成', cancelled: '已取消', failed: '失败'};
  const recent = (overview.recent_imports || []).map(job => `<div class="backup-row"><div><strong>${esc(job.filename)}</strong><span class="small muted">${jobState[job.state] || esc(job.state)} · ${fmt(job.processed)} / ${fmt(job.valid)} 条</span></div><span class="small muted">${dt(job.created_at)}</span></div>`).join('') || '<p class="muted small">暂无导入任务</p>';
  const sources = (overview.sources || []).map(source => `<div class="backup-row"><div><strong><span class="dot ${statusDot(source)}"></span> ${platforms[source.id]?.name || esc(source.id)}</strong><span class="small muted">${sourceState(source)} · ${fmt(source.message_count)} 条 · ${fmt(source.conversation_count)} 个会话</span></div><span class="small muted">${source.error ? esc(source.error) : source.last_sync ? dt(source.last_sync) : '尚未同步'}</span></div>`).join('') || '<p class="muted small">数据源状态不可用</p>';
  return `${head()}<div class="view-scroll view-enter">${tabBar}<div class="metrics">${[['已索引消息', s.message_count, 'message-square'], ['会话', s.conversation_count, 'messages-square'], ['联系人', s.contact_count, 'users-round'], ['媒体消息', s.media_count, 'image']].map(([name, value]) => `<div class="metric"><div class="label">${name}</div><strong>${fmt(value)}</strong><div class="small">${platforms[state.platform].name}</div></div>`).join('')}</div><div class="analytics-grid overview-grid"><section><div class="section-title"><h2>数据源</h2><span class="label">索引 ${fmtBytes(overview.index_bytes)}</span></div>${sources}<button class="btn" data-view="sources">${icon('database')}管理数据源</button></section><section><div class="section-title"><h2>最近导入</h2><button class="btn" data-view="imports">全部任务</button></div>${recent}</section></div><div class="analytics-grid"><section><div class="section-title"><h2>消息活动</h2><span class="label">最近 ${daily.length} 个活跃日</span></div>${daily.length ? `<div class="activity-chart">${daily.map(d => `<div class="chart-bar" style="height:${Math.max(2, d.count / max * 100)}%" title="${esc(d.date)} · ${fmt(d.count)} 条"></div>`).join('')}</div><div class="chart-labels"><span>${daily[0].date}</span><span>${daily.at(-1).date}</span></div>` : empty('暂无消息活动')}</section><section><h2>消息类型</h2><div class="type-breakdown">${Object.entries(typeNames).map(([key, name]) => `<div class="type-row"><span>${name}</span><div class="type-bar"><div style="width:${Math.min(100, Number(counts[key] || 0) / Math.max(1, s.message_count) * 100)}%"></div></div><span class="mono muted" style="text-align:right">${fmt(counts[key] || 0)}</span></div>`).join('')}</div></section></div></div>`;
}

function renderSelfSentDailyChart(distribution) {
  const baseline = Number(distribution.baseline || 0);
  const series = (distribution.items || []).map((item, index, all) => {
    const ratio = baseline > 0 ? Number((item.count / baseline * 100).toFixed(1)) : 0;
    const start = Math.max(0, index - 6);
    const ratios = all.slice(start, index + 1).map(day =>
      baseline > 0 ? Number((day.count / baseline * 100).toFixed(1)) : 0);
    const movingAverage = Number((ratios.reduce((sum, value) => sum + value, 0) / ratios.length).toFixed(1));
    return {date: item.date, count: item.count, ratio, movingAverage};
  }).slice(-31);
  if (!series.length) return empty('暂无自身发送记录');
  const max = Math.max(100, ...series.map(item => Math.max(item.ratio, item.movingAverage))) * 1.1;
  const y = value => 138 - value / max * 120;
  const slot = 620 / series.length;
  const bars = series.map((item, index) => `<rect x="${(index * slot + slot * .2).toFixed(1)}" y="${y(item.ratio).toFixed(1)}" width="${(slot * .55).toFixed(1)}" height="${Math.max(0, 138 - y(item.ratio)).toFixed(1)}"><title>${esc(item.date)} · ${fmt(item.count)} 条 · ${item.ratio.toFixed(1)}%</title></rect>`).join('');
  const trend = series.map((item, index) => `${(index * slot + slot / 2).toFixed(1)},${y(item.movingAverage).toFixed(1)}`).join(' ');
  return `<svg class="self-sent-chart" viewBox="0 0 620 170" role="img" aria-label="最近 ${series.length} 天每日自身发送量相对全期日均比例，绿色柱为单日比例，蓝线为七日均线，虚线为百分之一百基线"><line class="self-sent-baseline" x1="0" x2="620" y1="${y(100).toFixed(1)}" y2="${y(100).toFixed(1)}"></line>${bars}<polyline class="self-sent-trend" points="${trend}"></polyline></svg><div class="chart-labels"><span>${esc(series[0].date)}</span><span>${esc(series.at(-1).date)}</span></div><p class="muted small self-sent-note">绿色柱：当日发送量 ÷ 全期日均；蓝线：7 日均线；虚线：100% 基线</p>`;
}

function hourlyChartLabel(hourly) {
  return `24 小时消息分布，时区 UTC+08:00；${hourly.map(item => `${String(item.hour).padStart(2, '0')}:00 ${fmt(item.count)} 条`).join('；')}`;
}

function renderAnnualReport(stats, tabBar) {
  const report = state.annualReport;
  const monthly = report?.monthly || [];
  const max = Math.max(1, ...monthly.map(item => item.count));
  const contacts = (report?.top_contacts || []).map(item => `<tr><td><strong>${esc(item.name)}</strong><div class="small muted">${badge(item.platform)}</div></td><td>${fmt(item.count)}</td></tr>`).join('');
  const rangeLabel = report?.year == null ? '全部时间' : `${report.year} 年`;
  const hourly = Array.from({length: 24}, (_, hour) => ({hour, count: report?.hourly?.find(item => item.hour === hour)?.count || 0}));
  const hourlyMax = Math.max(1, ...hourly.map(item => item.count));
  const accessibleHourlyLabel = hourlyChartLabel(hourly);
  const weekdays = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];
  const heatmap = report?.activity_heatmap || [];
  const heatmapMax = Math.max(1, ...heatmap.flatMap(day => day || []));
  const heatmapHtml = heatmap.length === 7 ? `<div class="annual-heatmap" role="img" aria-label="按星期和小时统计的消息热图，时区 UTC+08:00">${heatmap.map((day, weekday) => `<div class="annual-heatmap-row"><span>${weekdays[weekday]}</span>${Array.from({length: 24}, (_, hour) => { const count = day?.[hour] || 0; const level = count ? Math.min(4, Math.ceil(count / heatmapMax * 4)) : 0; return `<i data-level="${level}" title="${weekdays[weekday]} ${String(hour).padStart(2, '0')}:00 · ${fmt(count)} 条" aria-label="${weekdays[weekday]} ${String(hour).padStart(2, '0')}:00 ${fmt(count)} 条"></i>`; }).join('')}</div>`).join('')}</div>` : '';
  const streak = report?.longest_streak;
  const streakHtml = streak ? `<p class="report-highlight">最长连续活跃 <strong>${fmt(streak.days)} 天</strong><span class="muted small">${esc(streak.start_date)} 至 ${esc(streak.end_date)}</span></p>` : '';
  return `${head()}<div class="view-scroll view-enter">${tabBar}<div class="annual-toolbar"><label for="report-year-scope">范围</label><select id="report-year-scope" aria-label="年度报告范围"><option value="all" ${state.reportYearScope === 'all' ? 'selected' : ''}>全部时间</option><option value="year" ${state.reportYearScope !== 'all' ? 'selected' : ''}>单年</option></select><label for="report-year" ${state.reportYearScope === 'all' ? 'hidden' : ''}>年份</label><input id="report-year" type="number" min="1970" max="9999" value="${esc(state.reportYear)}" ${state.reportYearScope === 'all' ? 'hidden disabled' : ''}><button class="btn primary" data-action="load-annual-report">${icon('chart-no-axes-column')}生成报告</button>${report ? `<button class="btn" data-action="download-annual-report">${icon('download')}导出 CSV</button>` : ''}<span class="label">${platforms[state.platform].name}${state.platform === 'all' ? ' · 全平台汇总' : ''}</span></div>${state.loading ? loading() : report ? `<div class="metrics annual-metrics">${[['消息总数', report.total_messages, 'messages-square'], ['主动消息', report.sent_messages, 'send'], ['活跃天数', report.active_days, 'calendar-days'], ['主动占比', `${Math.round((report.sent_ratio || 0) * 100)}%`, 'chart-pie']].map(([label, value, glyph]) => `<div class="metric"><div class="label">${icon(glyph)}${label}</div><strong>${typeof value === 'number' ? fmt(value) : value}</strong><div class="small">${rangeLabel}</div></div>`).join('')}</div><div class="analytics-grid report-grid"><section><div class="section-title"><h2>月度消息</h2><span class="label">${report.busiest_day ? `最活跃日 ${esc(report.busiest_day.date)} · ${fmt(report.busiest_day.count)} 条` : '暂无消息'}</span></div>${monthly.length ? `<div class="month-chart">${monthly.map(item => `<div class="month-row"><span>${String(item.month).padStart(2, '0')} 月</span><div class="type-bar"><div style="width:${Math.max(1, item.count / max * 100)}%"></div></div><span>${fmt(item.count)}</span></div>`).join('')}</div>` : empty('所选范围没有消息')}</section><section><div class="section-title"><h2>时段与类型</h2><span class="label">UTC+08:00</span></div><p class="report-highlight">最活跃时段 <strong>${report.busiest_hour ? `${String(report.busiest_hour.hour).padStart(2, '0')}:00` : '—'}</strong></p><div class="hour-chart" role="img" aria-label="${esc(accessibleHourlyLabel)}">${hourly.map(item => `<div class="hour-bar" aria-hidden="true" style="height:${Math.max(2, item.count / hourlyMax * 100)}%" title="${String(item.hour).padStart(2, '0')}:00 · ${fmt(item.count)} 条"></div>`).join('')}</div><div class="chart-labels"><span>00:00</span><span>06:00</span><span>12:00</span><span>18:00</span><span>23:00</span></div>${streakHtml}<div class="type-breakdown">${(report.message_types || []).map(item => `<div class="type-row"><span>${esc(typeNames[item.type] || item.type)}</span><div class="type-bar"><div style="width:${Math.min(100, item.count / Math.max(1, report.total_messages) * 100)}%"></div></div><span class="mono muted">${fmt(item.count)}</span></div>`).join('') || '<span class="muted small">暂无消息类型数据</span>'}</div><p class="muted small report-range">${report.first_timestamp ? `${dt(report.first_timestamp, true)} 至 ${dt(report.last_timestamp, true)}` : ''}</p></section></div>${heatmapHtml ? `<section class="report-heatmap"><div class="section-title"><h2>活跃热图</h2><span class="label">星期 × 小时 · UTC+08:00</span></div>${heatmapHtml}</section>` : ''}<section class="report-contacts"><div class="section-title"><h2>年度来信联系人</h2><span class="label">按收到消息数 · 前 10</span></div>${contacts ? `<div class="table-scroll"><table class="analytics-table"><thead><tr><th>联系人</th><th>收到消息</th></tr></thead><tbody>${contacts}</tbody></table></div>` : '<p class="muted small">暂无来信联系人</p>'}</section>` : empty('尚未生成年度报告', state.error || '选择范围后生成基于本机索引的报告')}</div>`;
}

function downloadAnnualReport() {
  const report = state.annualReport;
  if (!report) return;
  const rows = [
    ['类别', '项目', '数量'],
    ['概览', '平台', platforms[state.platform]?.name || state.platform],
    ['概览', '范围', report.year == null ? '全部时间' : report.year],
    ['概览', '消息总数', report.total_messages],
    ['概览', '主动消息', report.sent_messages],
    ['概览', '收到消息', report.received_messages],
    ['概览', '活跃天数', report.active_days],
    ['概览', '主动占比', report.sent_ratio],
    ['概览', '首次消息', report.first_timestamp ? dt(report.first_timestamp) : ''],
    ['概览', '最近消息', report.last_timestamp ? dt(report.last_timestamp) : ''],
    ['概览', '最活跃日', report.busiest_day?.date || ''],
    ['概览', '最活跃日消息数', report.busiest_day?.count ?? 0],
    ['概览', '最活跃时段', report.busiest_hour ? `${String(report.busiest_hour.hour).padStart(2, '0')}:00` : ''],
    ['概览', '最活跃时段消息数', report.busiest_hour?.count ?? 0],
    ['活跃热图', '最长连续活跃日', report.longest_streak?.days ?? 0],
    ['活跃热图', '连续活跃起始日', report.longest_streak?.start_date || ''],
    ['活跃热图', '连续活跃结束日', report.longest_streak?.end_date || ''],
    ...(report.monthly || []).map(item => ['月份', `${item.month}月`, item.count]),
    ...(report.hourly || []).map(item => ['时段', `${String(item.hour).padStart(2, '0')}:00`, item.count]),
    ...(report.activity_heatmap || []).flatMap((day, weekday) => day.map((count, hour) => ['星期时段', `${['周日', '周一', '周二', '周三', '周四', '周五', '周六'][weekday]} ${String(hour).padStart(2, '0')}:00`, count])),
    ...(report.message_types || []).map(item => ['类型', typeNames[item.type] || item.type, item.count]),
    ...(report.top_contacts || []).map(item => ['来信联系人', `${platforms[item.platform]?.name || item.platform} · ${item.name}`, item.count])
  ];
  downloadCsv(rows, `年度报告-${report.year == null ? '全部时间' : report.year}-${state.platform}.csv`);
}
function downloadPairReport() {
  const report = state.pairReport;
  if (!report) return;
  const rows = [
    ['类别', '项目', '数量'],
    ['会话', '平台', platforms[report.platform]?.name || report.platform],
    ['会话', '名称', report.conversation_title],
    ['会话', '范围', report.year == null ? '全部时间' : report.year],
    ['双方', '对话对象', report.counterpart_name || '对方'],
    ['双方', '对象标识', report.counterpart_id || ''],
    ['概览', '消息总数', report.total_messages],
    ['概览', '我发出', report.sent_messages],
    ['概览', '对方消息', report.counterpart_messages],
    ['概览', '活跃天数', report.active_days],
    ['概览', '主动占比', report.sent_ratio],
    ['回复', '回复次数', report.response_count ?? 0],
    ['回复', '平均回复时长（秒）', report.response_avg_seconds ?? ''],
    ['回复', '最快回复时长（秒）', report.response_fastest_seconds ?? ''],
    ['概览', '首次消息', report.first_timestamp ? dt(report.first_timestamp) : ''],
    ['概览', '最近消息', report.last_timestamp ? dt(report.last_timestamp) : ''],
    ...(report.hourly || []).map(item => ['时段', `${String(item.hour).padStart(2, '0')}:00`, item.count]),
    ...(report.message_types || []).map(item => ['类型', typeNames[item.type] || item.type, item.count])
  ];
  downloadCsv(rows, `双人报告-${report.platform}-${report.year == null ? '全部时间' : report.year}.csv`);
}

const campusOptions = ['南校园', '北校园', '东校园', '珠海校区', '深圳校区'];
function campusUrl(value) {
  try {
    const url = new URL(value);
    return url.protocol === 'https:' && !url.username && !url.password && (!url.port || url.port === '443') &&
      (url.hostname === 'sysu.edu.cn' || url.hostname.endsWith('.sysu.edu.cn') ||
        url.hostname === 'mp.weixin.qq.com' && (url.pathname === '/s' || url.pathname.startsWith('/s/'))) ? url.href : '';
  } catch { return ''; }
}
function campusDate(value) {
  if (!value) return '发布日期未提供';
  const date = new Date(typeof value === 'number' ? value * 1000 : value);
  return Number.isNaN(date.getTime()) ? '发布日期未提供' : dayFormatter.format(date);
}
function campusLink(url, label) {
  const href = campusUrl(url);
  return href ? `<a href="${esc(href)}" target="_blank" rel="noopener noreferrer">${esc(label)}${icon('external-link')}</a>` : `<span>${esc(label)}</span>`;
}
function campusContentLabel(item) {
  return ({title_only: '仅标题，待补充内容', fetch_error: '全文获取失败，保留摘要', full_text: '已获取全文', blocked: '访问受限，保留摘要', failed: '全文获取失败，保留摘要', error: '全文获取失败，保留摘要'}[item.content_status] || (item.content_error || item.fetch_error ? '全文获取失败，保留摘要' : '仅摘要，待获取全文'));
}
function campusUpdateStatus() {
  const u = state.campus.updates, r = state.campus.refresh, counts = u.content_counts || {};
  const time = value => value ? new Date(typeof value === 'number' ? value * 1000 : value).toLocaleString('zh-CN') : '暂无';
  return `${u.auto_updates_enabled ? `自动更新已开启 · 每 ${fmt(u.interval_minutes)} 分钟` : '自动更新未开启'} · ${r.state === 'running' ? '正在更新' : u.auto_updates_enabled ? '等待下一次检查' : '已暂停'} · 最近检查 ${time(u.last_check)} · 下次检查 ${u.auto_updates_enabled ? time(u.next_due) : '未安排'} · 已有全文 ${fmt(counts.full_text)} 篇 · 待获取 ${fmt(counts.pending ?? r.backlog)} 篇${r.body_success || r.body_failed ? ` · 最近补充正文 ${fmt(r.body_success)} 篇，未取得 ${fmt(r.body_failed)} 篇` : ''}${u.last_error ? ' · 最近更新未完成，请查看来源状态' : ''}`;
}
let campusArticleRequest = 0;
function renderCampusArticle(item) {
  modal(item.title || '校园文章', `<article id="campus-article" data-item-id="${esc(item.id)}"><p class="small muted">${esc(item.source_name)} · ${esc(campusContentLabel(item))}</p>${campusLink(item.url, '打开原文')}<div class="campus-article-content">${esc(item.content_status === 'full_text' ? item.content || item.summary || '' : item.summary || '暂无摘要，请打开原文查看。')}</div>${item.content_status !== 'full_text' ? `<p class="small muted">尚未取得完整正文，以上为已归档摘要。访问受限的文章可在微信中打开原文。</p><button class="btn primary" data-action="campus-fetch-article" data-id="${esc(item.id)}">${item.content_error || item.fetch_error ? '重试全文' : '获取全文'}</button>` : ''}</article>`, '', true);
}
async function showCampusArticle(id, fetchBody = false, button = null) {
  if (button?.disabled) return;
  if (button) {button.disabled = true;button.textContent = '正在获取…';}
  const sequence = ++campusArticleRequest, request = state.request;
  if (!fetchBody) modal('校园文章', '<p id="campus-article-loading" role="status">正在读取已归档内容…</p>');
  const isCurrent = () => sequence === campusArticleRequest && request === state.request && state.view === 'campus' && (fetchBody ? document.querySelector('#overlay')?.innerHTML.includes(`data-item-id="${esc(id)}"`) : document.querySelector('#overlay')?.innerHTML.includes('campus-article-loading'));
  try {
    const item = await api('/api/campus/items/' + encodeURIComponent(id) + (fetchBody ? '/fetch' : ''), null, fetchBody ? {method: 'POST', body: '{}'} : {});
    if (!isCurrent()) return;
    renderCampusArticle(item);
    const cached = state.campus.items.find(value => String(value.representative_id || value.id) === id);
    if (cached) {if (cached.representative_id) {cached.material_status = item.content_status;cached.read = item.read;cached.bookmarked = item.bookmarked;} else Object.assign(cached, item);}
    if (fetchBody) {renderView();toast(item.content_status === 'full_text' ? '全文已归档' : '暂未获取全文，已保留摘要', item.content_status !== 'full_text');}
  } catch (error) {
    if (isCurrent()) {
      if (!fetchBody) modal('校园文章', '<p role="alert">读取失败，请稍后重试。</p>');
      toast(error.message, true);
    }
  } finally {if (button) {button.disabled = false;button.textContent = '重试全文';}}
}
function showCampusUpdates() {
  const u = state.campus.updates;
  modal('自动增量更新', `<form id="campus-updates-form" class="metadata-form"><p class="small muted">中枢运行期间，定时同步所选公众号已出现在本机消息索引中的新文章，并刷新公开来源。只补充尚未归档的文章和可获取的正文，不扫描公众号全部历史。</p><label class="check-label"><input type="checkbox" name="auto_updates_enabled" ${u.auto_updates_enabled ? 'checked' : ''}>开启自动更新</label><label>更新间隔（分钟）<input type="number" name="interval_minutes" min="1" max="1440" required value="${esc(u.interval_minutes || 15)}"></label><label class="check-label"><input type="checkbox" name="fetch_full_text" ${u.fetch_full_text !== false ? 'checked' : ''}>自动获取公众号全文</label><label>每轮全文获取上限<input type="number" name="batch_limit" min="1" max="20" required value="${esc(u.batch_limit || 20)}"></label><button type="submit" class="btn primary">保存更新设置</button></form>`);
}
async function saveCampusUpdates(form) {
  if (!form.reportValidity()) return;
  const values = new FormData(form), button = form.querySelector('[type="submit"]');
  if (button.disabled) return;
  const payload = {auto_updates_enabled: !!values.get('auto_updates_enabled'), interval_minutes: Number(values.get('interval_minutes')), fetch_full_text: !!values.get('fetch_full_text'), batch_limit: Number(values.get('batch_limit'))};
  if (!Number.isInteger(payload.interval_minutes) || payload.interval_minutes < 1 || payload.interval_minutes > 1440 || !Number.isInteger(payload.batch_limit) || payload.batch_limit < 1 || payload.batch_limit > 20) {toast('请填写有效的更新间隔和获取上限', true);return;}
  button.disabled = true;
  try {
    state.campus.updates = await api('/api/campus/updates', null, {method: 'POST', body: JSON.stringify(payload)});
    closeModal();toast('自动更新设置已保存');
    if (state.view === 'campus') {renderView();scheduleCampusPoll();}
  } catch (error) {toast(error.message, true);}
  finally {button.disabled = false;}
}
function campusScoringLabel(model) {
  return ({'westlake-hpc/deepseek-flash': 'DeepSeek', 'westlake-hpc/glm-flash': 'GLM'}[model] || model || '模型');
}
function renderCampusScoring(item) {
  const s = item.scoring;
  if (!s || s.status === 'disabled') return '';
  const basis = s.material_basis === 'full_text' ? '依据正文（最多12000字符）' : '依据现有摘要';
  if (s.status !== 'completed') return `<div class="station-score-status" role="status">${esc(({pending: '等待双模型评分', error: '评分未完成，等待重试', insufficient: '内容不足，待补充正文后评分'}[s.status] || '等待评分'))}${s.status === 'error' && s.error ? `<span>${esc(s.error)}</span>` : ''}</div>`;
  return `<details class="station-score"><summary><strong>综合 ${fmt(s.score)} 分</strong><span>西湖双模型 · ${basis} · ${s.selected ? '达到精选标准' : '未达到精选标准'}</span></summary><div class="station-score-models">${(s.models || []).map(model => `<div><strong>${esc(campusScoringLabel(model.model))} ${fmt(model.score)} 分</strong><span>${model.eligible ? '适用当前学生资料' : '不适用当前学生资料'}</span><p>${(model.reasons || []).map(esc).join(' · ')}</p></div>`).join('')}</div></details>`;
}
function renderCampusScoringPanel(scoring = {}) {
  return `<section class="station-panel"><h2>西湖双模型评分</h2><p>DeepSeek 与 GLM 独立阅读公开通知，分别评估奖学金、竞赛、选课等信息与你的相关性。两个模型均认为适用，且平均分达到 70 分时进入精选。</p><p class="small">只发送公开通知及校区、院系、培养层次、关注主题。全文与摘要分别标注，只有标题的材料等待补充内容。</p><div class="station-counts campus-scoring-status" role="status"><span>${scoring.enabled ? scoring.running ? '正在评分' : '自动评分已开启' : '模型评分未开启，当前使用本地规则'}</span>${[['已完成', scoring.completed], ['待评分', scoring.pending], ['待重试', scoring.error], ['内容不足', scoring.insufficient]].map(([label, count]) => `<span>${label} <strong>${fmt(count)}</strong></span>`).join('')}</div>${scoring.last_error ? `<p class="error-text">${esc(scoring.last_error)}</p>` : ''}<div class="station-scoring-actions"><button class="btn" data-action="campus-scoring-toggle" aria-pressed="${!!scoring.enabled}">${scoring.enabled ? '暂停模型评分' : '开启模型评分'}</button><button class="btn primary" data-action="campus-scoring-run" ${!scoring.enabled || scoring.running ? 'disabled' : ''}>${icon('refresh-cw', scoring.running ? 'spin' : '')}${scoring.running ? '正在评分' : '立即评分'}</button></div></section>`;
}
async function updateCampusScoring(button, run = false) {
  if (button.disabled) return;
  const request = state.request;
  button.disabled = true;
  try {
    const scoring = await api('/api/campus/scoring' + (run ? '/run' : ''), null, {method: 'POST', body: JSON.stringify(run ? {} : {enabled: !state.campus.briefing?.pipeline?.scoring?.enabled})});
    if (state.view !== 'campus' || request !== state.request) return;
    state.campus.briefing ||= {};state.campus.briefing.pipeline ||= {};state.campus.briefing.pipeline.scoring = scoring;
    toast(run ? '评分任务已启动' : scoring.enabled ? '已开启西湖双模型评分' : '模型评分已暂停');
    await loadView();
  } catch (error) {toast(error.message, true);}
  finally {button.disabled = false;}
}
function renderCampus() {
  const c = state.campus, section = c.section, page = campusSections[section], b = c.briefing || {}, f = c.filters;
  const running = c.refresh.state === 'running';
  const refreshButton = `<button class="btn primary" data-action="campus-refresh" ${running ? 'disabled' : ''}>${icon('refresh-cw', running ? 'spin' : '')}${running ? '正在刷新' : '刷新公开信息'}</button>`;
  const topics = b.topics || [];
  const topicName = id => topics.find(topic => topic.id === id)?.name || id;
  const topicsBar = topics.length ? `<div class="station-topics" aria-label="主题筛选"><button class="station-chip ${!f.topic ? 'active' : ''}" data-campus-topic="">全部主题</button>${topics.map(topic => `<button class="station-chip ${f.topic === topic.id ? 'active' : ''}" data-campus-topic="${esc(topic.id)}">${esc(topic.name)}<span>${fmt(topic.count)}</span></button>`).join('')}</div>` : '';
  const search = `<form id="campus-search" class="station-search"><div class="search-field">${icon('search')}<input name="q" aria-label="搜索校园信息" placeholder="搜索通知、奖学金、竞赛、选课…" value="${esc(f.q)}" maxlength="200"></div>${section === 'daily' ? `<input type="date" name="date" aria-label="日报日期" value="${esc(f.date || dateKey(Date.now() / 1000))}">` : ''}<button class="btn" type="submit">搜索</button></form>`;
  const items = c.items.map(item => {
    const id = item.representative_id || item.id, reports = item.reports || [], reasons = item.reasons || item.relevance_reasons || [];
    return `<article class="campus-card station-card ${item.read ? 'is-read' : ''}" data-event-id="${esc(item.id)}"><div class="station-time"><time>${esc(campusDate(item.published_at))}</time>${item.future_date ? '<span class="station-date-note">来源日期待核实</span>' : ''}</div><div class="station-card-body"><div class="campus-meta"><span>${esc(reports[0]?.source_name || item.source_name || '已归档来源')}</span>${section === 'hot' ? `<span class="station-heat">${icon('flame')}${fmt(item.heat)} · ${fmt(item.hot_source_count ?? item.source_count)} 个来源</span>` : ''}<span class="station-material">${esc(campusContentLabel({content_status: item.material_status || item.content_status}))}</span></div><h2>${campusLink(item.url, item.title)}</h2>${item.summary ? `<p class="station-summary">${esc(item.summary)}</p>` : ''}${item.topics?.length ? `<div class="station-tags">${item.topics.map(topic => `<button data-campus-topic="${esc(topic)}"># ${esc(topicName(topic))}</button>`).join('')}</div>` : ''}${renderCampusScoring(item)}${reasons.length && (!item.scoring || item.scoring.status === 'disabled') ? `<div class="campus-reasons">${icon('list-filter')}${section === 'selected' ? '入选依据' : '信息依据'}：${reasons.map(esc).join(' · ')}</div>` : ''}${reports.length ? `<details class="station-reports"><summary>${fmt(item.report_count || reports.length)} 篇报道 · ${fmt(item.source_count || 1)} 个来源</summary>${reports.map(report => `<div><span>${esc(report.source_name)}</span>${campusLink(report.url, report.title)}<button class="btn" data-action="campus-article" data-id="${esc(report.id)}">阅读存档</button></div>`).join('')}${(item.provenance || []).filter(origin => !reports.some(report => report.source_id === origin.source_id)).map(origin => `<div><span>${esc(origin.source_name)}</span>${(origin.urls || []).map((url, index) => campusLink(url, '来源原文' + (index ? ' ' + (index + 1) : ''))).join(' · ')}</div>`).join('')}</details>` : ''}<div class="campus-card-actions"><button class="btn" data-action="campus-article" data-id="${esc(id)}">${icon('book-open')}阅读全文</button><button class="btn" data-action="campus-bookmark" data-id="${esc(id)}" aria-pressed="${!!item.bookmarked}">${icon('bookmark')}${item.bookmarked ? '已收藏' : '收藏'}</button><button class="btn" data-action="campus-read" data-id="${esc(id)}" aria-pressed="${!!item.read}">${icon('check')}${item.read ? '标为未读' : '标为已读'}</button></div></div></article>`;
  }).join('');
  const counts = b.counts || {};
  const sourceCards = c.sources.filter(s => !f.q || (s.name + ' ' + (s.category_name || '')).includes(f.q)).map(s => {
    const collect = s.kind === 'feed' || s.kind === 'local_wechat';
    return `<article class="station-source-card"><div class="station-source-heading"><span class="station-source-icon">${icon(s.kind === 'local_wechat' ? 'message-circle' : collect ? 'rss' : 'external-link')}</span><div><h2>${campusLink(s.url, s.name)}</h2><span class="small muted">${esc(s.tier_label || (s.kind === 'local_wechat' ? '已关注公众号' : '学校公开来源'))} · ${esc(s.category_name || s.category || '校园信息')}</span></div>${collect ? `<button class="btn" data-action="campus-source-toggle" data-id="${esc(s.id)}" aria-pressed="${s.enabled !== false}">${s.enabled === false ? '启用采集' : '已启用'}</button>` : '<span class="pill">服务入口</span>'}</div><div class="station-source-meta"><span>${fmt(s.item_count)} 条存档</span><span>${s.last_check ? esc(campusDate(s.last_check)) : '尚未检查'}</span><span>${s.enabled === false ? '已暂停采集' : ({ok: '已更新', success: '已更新', error: '获取失败', empty: '暂无条目', blocked: '访问受限'}[s.status] || (collect ? '等待更新' : '前往官方页面'))}</span></div>${s.error ? `<p class="error-text">${esc(s.error)}</p>` : ''}</article>`;
  }).join('');
  let body;
  if (section === 'sources') body = `${search}<div class="station-source-actions"><button class="btn" data-action="campus-wechat-accounts">${icon('users-round')}选择本机公众号</button><button class="btn" data-action="campus-wechat">${icon('link')}导入文章链接</button></div><div class="station-source-grid">${sourceCards || empty('没有匹配的来源', '调整搜索词后重试。')}</div>`;
  else if (section === 'pipeline') body = `<div class="station-pipeline">${(b.pipeline?.stages || []).map(stage => `<div>${icon('check-check')}<strong>${fmt(stage.count)}</strong><span>${esc(stage.name)}</span></div>`).join('')}</div><section class="station-panel"><h2>处理方式</h2><p>公开通知与本机公众号 → 正文提取 → 重复事件合并 → 主题归类 → ${b.pipeline?.scoring?.enabled ? '西湖双模型独立评分' : '学生资料规则筛选'}。</p><p class="small muted">${esc(b.pipeline?.description || '摘要来自已归档内容，当前使用本地规则。')}</p></section>${renderCampusScoringPanel(b.pipeline?.scoring)}<section class="station-panel"><h2>自动增量更新</h2><div class="campus-update-status small muted" role="status">${esc(campusUpdateStatus())}</div><button class="btn" data-action="campus-updates">${icon('settings-2')}更新设置</button><div class="campus-status" role="status">${running ? `正在检查 ${fmt(c.refresh.processed)} / ${fmt(c.refresh.total)} 个来源` : '当前没有正在运行的刷新任务'}${c.refresh.errors?.length ? ` · ${fmt(c.refresh.errors.length)} 个来源未完成` : ''}</div></section><div class="station-counts">${[['完整正文', counts.full_text], ['仅摘要', counts.summary_only], ['全文待重试', counts.fetch_error], ['日期未提供', counts.unknown_date]].map(([label, count]) => `<span>${esc(label)} <strong>${fmt(count)}</strong></span>`).join('')}</div>`;
  else if (section === 'services') body = `<div class="station-service-grid">${c.sources.filter(s => !['feed', 'local_wechat', 'manual'].includes(s.kind)).map(s => `<article class="station-panel"><h2>${campusLink(s.url, s.name)}</h2><p class="small muted">${esc(s.category_name || s.category || '校园服务')} · ${esc(s.scope === 'all' ? '全校' : s.scope || '全校')}</p></article>`).join('') || empty('暂无服务入口')}</div>`;
  else if (section === 'profile') body = `<section class="station-panel station-profile"><span class="station-source-icon">${icon('graduation-cap')}</span><h2>${esc(c.profile.faculty || '完善学生资料')}</h2><p>${esc(c.profile.level || '未填写培养层次')} · ${esc(c.profile.campus || '未填写校区')}${c.profile.next_campus ? ' → ' + esc(c.profile.next_campus) : ''}</p><div class="station-topics">${(c.profile.keywords || []).map(word => `<span class="station-chip">${esc(word)}</span>`).join('')}</div><button class="btn primary" data-action="campus-profile">${icon('pencil')}编辑学生资料</button><p class="small muted">资料保存在本机；启用模型评分时，仅发送校区、院系、培养层次与关注主题，无需学号与登录凭据。</p></section>`;
  else body = `${search}${topicsBar}${section === 'daily' ? `<div class="station-daily"><span>${icon('calendar-days')}${esc(b.date || f.date || dateKey(Date.now() / 1000))} 校园日报</span><span class="small muted">北京时间 · ${fmt(c.total)} 个事件 · 摘录自来源原文</span></div>${b.available_dates?.length ? `<div class="station-topics">${b.available_dates.slice(0, 8).map(date => `<button class="station-chip ${date === (f.date || b.date) ? 'active' : ''}" data-campus-date="${esc(date)}">${esc(date)}</button>`).join('')}</div>` : ''}` : ''}<div class="station-result-count">${fmt(c.total)} 个事件${section === 'selected' ? b.pipeline?.scoring?.enabled ? ' · 西湖 DeepSeek 与 GLM 独立评分后精选' : ' · 按学生资料规则筛选，非人工或 AI 评级' : section === 'hot' ? ' · 最近 48 小时，多来源优先' : ' · 同一事件合并展示'}</div><section class="station-timeline" aria-label="校园信息列表">${state.loading ? loading() : items || empty('暂无符合条件的信息', section === 'selected' ? '当前没有达到精选标准的信息，可在全部动态中查看评分进度，或调整学生资料。' : '可调整主题、日期或搜索词，或刷新已接入来源。', `<button class="btn" data-station="all">查看全部动态</button>`, 'graduation-cap')}</section><div class="campus-pagination"><button class="btn" data-action="campus-prev" ${c.offset === 0 || state.loading ? 'disabled' : ''}>上一页</button><span class="small muted">第 ${Math.floor(c.offset / 50) + 1} 页</span><button class="btn" data-action="campus-next" ${c.offset + 50 >= c.total || state.loading ? 'disabled' : ''}>下一页</button></div>`;
  return `<div class="view-head station-head"><div><div class="eyebrow">中山大学 · 学生信息站</div><h1>${page.name}</h1><p class="subtitle">${page.description}</p></div><div class="head-actions">${section !== 'pipeline' ? `<button class="btn" data-station="pipeline">${icon('activity')}采集状态</button>` : ''}${refreshButton}</div></div><div class="view-scroll view-enter campus-view">${errorStrip()}${body}<p class="campus-coverage station-coverage">覆盖范围以已接入来源为准，不代表学校全部网页；通知适用范围与截止时间请核对原文。</p></div>`;
}

function showCampusProfile() {
  const p = state.campus.profile;
  const options = (values, value) => `<option value="">未填写</option>` + values.map(v => `<option value="${esc(v)}" ${v === value ? 'selected' : ''}>${esc(v)}</option>`).join('');
  modal('学生资料', `<form id="campus-profile-form" class="metadata-form"><p class="muted small">资料保存在本机；启用模型评分时，仅发送校区、院系、培养层次与关注主题，无需填写学号或登录凭据。</p><label>所在校区<select name="campus">${options(campusOptions, p.campus)}</select></label><label>下一阶段校区<select name="next_campus">${options(campusOptions, p.next_campus)}</select></label><label>院系<input name="faculty" maxlength="100" value="${esc(p.faculty)}"></label><label>培养层次<select name="level">${options(['本科', '硕士', '博士'], p.level)}</select></label><label>入学年份<input name="year" type="number" min="1900" max="2100" value="${esc(p.year)}"></label><label>关注关键词（逗号分隔）<input name="keywords" maxlength="1000" placeholder="奖学金, 实习, 讲座" value="${esc((p.keywords || []).join(', '))}"></label><button type="submit" class="btn primary">保存资料</button></form>`);
}
function showCampusWechat() {
  modal('导入公众号文章', `<form id="campus-wechat-form" class="metadata-form"><p class="muted small">粘贴中大相关公众号的公开文章链接，归档后可搜索和收藏。仅支持 mp.weixin.qq.com/s 链接，不登录微信、不绕过验证码，也不扫描公众号全部历史。</p><label>公开文章链接<input name="url" type="url" required maxlength="2048" placeholder="https://mp.weixin.qq.com/s/…"></label><button type="submit" class="btn primary">导入文章</button></form>`);
}
let campusAccountsRequest = 0;
async function showCampusWechatAccounts() {
  const sequence = ++campusAccountsRequest;
  const request = state.request;
  modal('本机公众号', '<div id="campus-wechat-accounts-loading" role="status">正在查询本机已索引的中大相关公众号…</div>');
  try {
    const result = await api('/api/campus/wechat-accounts');
    if (sequence !== campusAccountsRequest || request !== state.request ||
        !document.querySelector('#overlay')?.innerHTML.includes('campus-wechat-accounts-loading')) return;
    const items = result.items || [];
    modal('本机公众号', `<form id="campus-wechat-accounts-form" class="metadata-form"><p class="muted small">以下账号来自本机已索引的微信联系人，包含中大及学院相关公众号。勾选后导入本机缓存的文章标题、摘要和原文链接，不代表已获取文章全文或公众号全部历史；不会使用微信登录凭据，也不请求远程文章。</p>
      ${items.length ? `<div class="campus-wechat-accounts">${items.map(item => `<label class="campus-wechat-account"><input type="checkbox" name="contact_ids" value="${esc(item.id)}" data-account-id="${esc(item.account_id)}" ${item.selected ? 'checked' : ''}><span><strong>${esc(item.name)}</strong><small>${esc(item.alias || item.id)} · 本机缓存 ${item.article_count == null ? '数量未提供' : fmt(item.article_count) + ' 篇'}</small><small>微信账号：${esc(item.account_id)}</small></span></label>`).join('')}</div><button type="submit" class="btn primary">导入所选公众号的缓存文章</button>` : '<p class="muted">尚未找到已索引的中大相关公众号，请先同步微信联系人和本机消息。</p>'}</form>`);
  } catch (error) {
    if (sequence === campusAccountsRequest && request === state.request) toast(error.message, true);
  }
}
async function importCampusWechatAccounts(form) {
  const selected = [...form.querySelectorAll('input[name="contact_ids"]:checked')];
  if (!selected.length) {toast('请至少选择一个公众号', true);return;}
  const button = form.querySelector('[type="submit"]');
  if (button.disabled) return;
  button.disabled = true;
  const accounts = new Map();
  for (const input of selected) {
    const ids = accounts.get(input.dataset.accountId) || [];
    ids.push(input.value);accounts.set(input.dataset.accountId, ids);
  }
  try {
    let added = 0;
    for (const [account_id, contact_ids] of accounts) {
      const result = await api('/api/campus/wechat-accounts/import', null,
        {method: 'POST', body: JSON.stringify({account_id, contact_ids})});
      added += Number(result.added) || 0;
    }
    closeModal();toast(`已导入所选公众号缓存，新增 ${fmt(added)} 篇标题、摘要与链接`);
    state.campus.offset = 0;
    if (state.view === 'campus') await loadView();
  } catch (error) {toast(error.message, true);}
  finally {button.disabled = false;}
}
async function importCampusWechat(form) {
  if (!form.reportValidity()) return;
  const url = new FormData(form).get('url').trim();
  let parsed;
  try {parsed = new URL(url);} catch {}
  if (!campusUrl(url) || parsed?.hostname !== 'mp.weixin.qq.com') {toast('请填写公开的 mp.weixin.qq.com/s 文章链接', true);return;}
  const button = form.querySelector('[type="submit"]');
  if (button.disabled) return;
  button.disabled = true;
  try {
    await api('/api/campus/wechat', null, {method: 'POST', body: JSON.stringify({url})});
    closeModal();toast('公众号文章已导入');state.campus.offset = 0;
    if (state.view === 'campus') await loadView();
  } catch (error) {toast(error.message, true);}
  finally {button.disabled = false;}
}
function scheduleCampusPoll() {
  if (campusPollTimer !== null) clearTimeout(campusPollTimer);
  campusPollTimer = null;
  const scoring = state.campus.briefing?.pipeline?.scoring;
  if (state.view !== 'campus' || (!state.campus.updates.auto_updates_enabled && state.campus.refresh.state !== 'running' && !scoring?.enabled && !scoring?.running) || accountLockTransition) return;
  const request = state.request;
  campusPollTimer = setTimeout(async () => {
    if (state.view !== 'campus' || request !== state.request) return;
    try {
      const previous = state.campus.refresh;
      const [result, updates, nextScoring] = await Promise.all([api('/api/campus/refresh'), api('/api/campus/updates'), scoring?.enabled || scoring?.running ? api('/api/campus/scoring') : null]);
      if (state.view !== 'campus' || request !== state.request) return;
      state.campus.refresh = result;state.campus.updates = updates;
      if (nextScoring) {
        state.campus.briefing.pipeline.scoring = nextScoring;
        if (['enabled', 'running', 'completed', 'pending', 'error', 'insufficient', 'last_error'].some(key => nextScoring[key] !== scoring[key])) {await loadView();return;}
      }
      if (result.state !== 'running' && (previous.state === 'running' || previous.finished_at !== result.finished_at)) {await loadView();return;}
      const updateStatus = document.querySelector('.campus-update-status');
      if (updateStatus) updateStatus.textContent = campusUpdateStatus();
      const status = document.querySelector('.campus-status');
      if (status && result.state === 'running') status.textContent = `正在检查 ${fmt(result.processed)} / ${fmt(result.total)} 个来源；新增 ${fmt(result.added)} 条`;
      scheduleCampusPoll();
    } catch (error) {
      if (state.view === 'campus' && request === state.request) {state.error = error.message;renderView();}
    }
  }, state.campus.refresh.state === 'running' || scoring?.running ? 1500 : 5000);
}
async function refreshCampus() {
  if (state.campus.refresh.state === 'running') return;
  const request = state.request;
  try {
    const result = await api('/api/campus/refresh', null, {method: 'POST', body: '{}'});
    if (state.view !== 'campus' || request !== state.request) return;
    state.campus.refresh = result;renderView();scheduleCampusPoll();
    if (result.state !== 'running') await loadView();
  } catch (error) {toast(error.message, true);}
}
async function setCampusItemState(id, field) {
  const item = state.campus.items.find(item => String(item.representative_id || item.id) === id);
  if (!item) return;
  const request = state.request;
  try {
    await api('/api/campus/' + (item.representative_id ? 'events/' : 'items/') + encodeURIComponent(id) + '/state', null,
      {method: 'POST', body: JSON.stringify({[field]: !item[field]})});
    if (state.view === 'campus' && request === state.request) await loadView();
  } catch (error) {toast(error.message, true);}
}
async function saveCampusProfile(form) {
  if (!form.reportValidity()) return;
  const values = Object.fromEntries(new FormData(form));
  const profile = {...values, keywords: values.keywords.split(/[,，]/).map(value => value.trim()).filter(Boolean)};
  try {
    state.campus.profile = await api('/api/campus/profile', null, {method: 'POST', body: JSON.stringify(profile)});
    closeModal();toast('学生资料已保存');state.campus.offset = 0;
    if (state.view === 'campus') await loadView();
  } catch (error) {toast(error.message, true);}
}

async function loadView() {
  if (campusPollTimer !== null) clearTimeout(campusPollTimer);
  campusPollTimer = null;
  cancelMediaPreviews();
  const request = ++state.request;
  releaseInactiveViewData();
  state.loading = true; state.error = '';
  if (state.view === 'stats') {
    if (state.statsTab === 'private') state.privateProfiles = [];
    if (state.statsTab === 'groups') state.groupProfiles = [];
    if (state.statsTab === 'daily') state.selfSentDaily = null;
    if (state.statsTab === 'relationships') state.relationshipProfiles = [];
    if (state.statsTab === 'annual') state.annualReport = null;
  }
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
    } else if (state.view === 'campus') {
      const section = state.campus.section;
      const [result, sources, profile, refresh, updates] = await Promise.all([
        api('/api/campus/briefing', {section: ['services', 'profile'].includes(section) ? 'all' : section, q: state.campus.filters.q, topic: state.campus.filters.topic,
          date: section === 'daily' ? state.campus.filters.date || dateKey(Date.now() / 1000) : '', limit: 50, offset: state.campus.offset}),
        api('/api/campus/sources'), api('/api/campus/profile'), api('/api/campus/refresh'), api('/api/campus/updates')
      ]);
      if (request !== state.request) return;
      Object.assign(state.campus, {briefing: result, items: result.items || [], total: result.total || 0, sources: result.sources || sources.items || [], profile, refresh, updates});
      scheduleCampusPoll();
    } else if (state.view === 'moments') {
      const result = await api('/api/moments', {...state.momentFilter, limit: 30, offset: state.momentOffset});
      if (request !== state.request) return;
      state.moments = result;
    } else if (['contacts', 'media', 'search'].includes(state.view)) {
      const cursorPaging = usesCursorPaging();
      const params = {...state.filters, platform: state.platform, limit: 60,
        ...(cursorPaging ? {cursor: state.pageCursors[state.page] || undefined} : {offset: state.page * 60})};
      if (['contacts', 'media'].includes(state.view)) {
        delete params.account_scope;
        const scope = selectedAccountScope();
        if (scope) {params.platform = scope.platform;params.account_id = scope.account_id;}
      }
      const [result, accounts] = await Promise.all([
        state.view === 'search' && !state.filters.q ? {items: [], total: 0} : api('/api/' + state.view, params),
        state.view === 'media' ? api('/api/accounts') : null
      ]);
      if (request !== state.request) return;
      if (accounts) state.accountScopes = accounts.items || [];
      state.items = result.items; state.total = result.total;
      if (state.view === 'contacts') state.accountScopes = result.accounts || [];
      if (cursorPaging) state.pageCursors[state.page + 1] = result.next_cursor || null;
      if (state.view === 'media' && state.filters.source === 'qq-cache' && !result.available) {
        state.error = '此缓存目录暂不可用；本地数据库附件可在“聊天附件”中浏览';
      }
    } else if (state.view === 'stats') {
      const capability = state.statsTab === 'annual' ? 'annual_reports' : state.statsTab === 'relationships' ? 'relationship_footprint' : 'advanced_analytics';
      const available = state.settings.capabilities?.[capability]?.available !== false && state.settings.capabilities?.[capability]?.enabled !== false;
      const tab = state.statsTab;
      const routes = {private: '/api/analytics/private', groups: '/api/analytics/groups',
        annual: '/api/reports/annual', relationships: '/api/analytics/relationships', daily: '/api/analytics/self-sent-daily'};
      const [result, detail] = await Promise.all([
        api('/api/stats', {platform: state.platform}),
        available && tab !== 'overview' ? api(routes[tab], {platform: state.platform,
          ...(tab === 'annual' ? {year: state.reportYearScope === 'all' ? null : state.reportYear}
            : ['private', 'groups', 'relationships'].includes(tab) ? {limit: 500, ...(tab === 'groups' ? {member_limit: 12} : {})} : {})}) : null
      ]);
      if (request !== state.request) return;
      state.stats = result;
      if (detail) {
        if (tab === 'private') state.privateProfiles = detail.items || [];
        else if (tab === 'groups') {state.groupMemberLists.clear();state.groupProfiles = detail.items || [];}
        else if (tab === 'annual') state.annualReport = detail;
        else if (tab === 'relationships') state.relationshipProfiles = detail.items || [];
        else if (tab === 'daily') state.selfSentDaily = detail;
      }
    } else if (state.view === 'sources') await refreshSources(false);
    else if (state.view === 'settings') {
      const botIdentityRequest = state.settings.capabilities?.wecom_bot_send?.enabled === true
        ? api('/api/wecom-bot/identity') : Promise.resolve(null);
      const [result, mcp, operations, botIdentity, ...queues] = await Promise.all([
        api('/api/backups'), api('/api/mcp'), api('/api/operations'), botIdentityRequest,
        state.settings.capabilities?.qq_send?.enabled === true ? api('/api/sends', {status: 'all', limit: 100}) : {items: []},
        state.settings.capabilities?.wecom_bot_send?.enabled === true ? api('/api/wecom-bot-sends', {status: 'all', limit: 100}) : {items: []},
        state.settings.capabilities?.wechat_send?.enabled === true ? api('/api/wechat-sends', {status: 'all', limit: 100}) : {items: []}
      ]);
      if (request !== state.request) return;
      state.backups = result.items || []; state.mcp = mcp; state.operations = operations.items || []; state.wecomBotIdentity = botIdentity;
      state.pendingSends = queues.flatMap(queue => queue.items || []).sort((a, b) => b.created_at - a.created_at);
    } else if (state.view === 'imports') await refreshImportJobs(false);
  } catch (error) { if (request === state.request) state.error = error.message; }
  finally { if (request === state.request) {state.loading = false; renderView();if (state.view === 'media') startMediaPreviews(request);} }
}
const delay = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));
async function startResourceExport() {
  const items = resourceExportItems([...state.selectedMedia.values()]);
  if (!items.length) return;
  state.resourceJob = {state: 'queued', total: items.length, completed: 0, exported: 0, failed: 0};
  refreshResourceToolbar();
  try {
    let job = await api('/api/resource-jobs', null, {method: 'POST', body: JSON.stringify({items})});
    state.resourceJob = job; refreshResourceToolbar();
    while (['queued', 'running', 'cancelling'].includes(job.state)) {
      await delay(500);
      job = await api('/api/resource-jobs/' + encodeURIComponent(job.id));
      state.resourceJob = job;
      if (state.view === 'media') refreshResourceToolbar();
    }
    if (job.state === 'completed' && job.download_url) {
      clearMediaSelection();
      toast(`附件已打包：成功 ${fmt(job.exported)} 项，未取得 ${fmt(job.failed)} 项`);
      location.href = job.download_url;
    } else if (job.state === 'cancelled') toast('附件导出已取消');
    else toast(`没有可导出的本机附件（${fmt(job.failed || 0)} 项未取得）`, true);
  } catch (error) {
    state.resourceJob = null;
    toast(error.message || '附件导出失败', true);
  }
  if (state.view === 'media') renderView();
}
async function cancelResourceExport() {
  const job = state.resourceJob;
  if (!job?.id) return;
  try {
    state.resourceJob = await api('/api/resource-jobs/' + encodeURIComponent(job.id) + '/cancel', null,
      {method: 'POST'});
    if (state.view === 'media') refreshResourceToolbar();
  } catch (error) {toast(error.message || '取消任务失败', true);}
}
async function loadMessages(append = false, request = state.request) {
  const sequence = ++messageLoadSequence;
  const c = state.selected;
  if (!c) return;
  const result = await api('/api/messages', {platform: c.platform, account_id: c.account_id, conversation_id: c.id, ...state.messageFilter, limit: 100,
    ...(append && state.messageCursor ? {cursor: state.messageCursor} : {offset: append ? state.messageOffset : 0})});
  if (sequence !== messageLoadSequence || request !== state.request || !sameConversation(c, state.selected)) return;
  state.messageOffset = (append ? state.messageOffset : 0) + result.items.length;
  state.messages = append ? state.messages.concat(result.items).slice(-MESSAGE_WINDOW_LIMIT) : result.items;
  state.messageTotal = result.total;state.messageCursor = result.next_cursor || null;state.messageHasMore = result.has_more;
}
function releaseInactiveViewData() {
  if (state.view !== 'chat') {state.messages = [];state.conversations = [];state.voiceTranscripts.clear();}
  if (!['contacts', 'media', 'search'].includes(state.view)) state.items = [];
  if (state.view !== 'moments') state.moments = {items: [], total: 0};
  if (state.view !== 'campus') state.campus.items = [];
  if (state.view !== 'stats') {
    state.stats = null;state.privateProfiles = [];state.groupProfiles = [];
    state.relationshipProfiles = [];state.annualReport = null;state.selfSentDaily = null;
    state.groupMemberLists.clear();
  }
}
async function selectConversation(c) {
  state.voiceTranscripts.clear();
  state.selected = c; state.contextMessage = null;state.contextMode = false;state.remembered[state.platform] = c;
  state.messages = []; state.messageOffset = 0; state.messageCursor = null;state.messageHasMore = false; state.messageFilter = {q: '', type: '', start: '', end: ''};
  state.loading = true; state.error = ''; renderView();
  const request = ++state.request;
  try { await loadMessages(false, request); } catch (e) {if (request === state.request) state.error = e.message;}
  if (request === state.request) {state.loading = false; renderView();}
}
async function refreshSources(render = true) {
  const result = await api('/api/sources');
  const sources = result.sources || result;
  if (state.view === 'sources') {
    const local = await api('/api/qq/local-status').catch(() => null);
    const qq = sources.find(source => source.id === 'qq');
    if (qq && local?.mode === 'local_database') {
      qq.database_count = local.database_count;
      qq.error_code = local.error_code;
      qq.key_discovery = local.key_discovery;
      if (!['syncing', 'indexing'].includes(qq.state) && !local.available) {
        qq.state = local.state;
        qq.error = local.message;
      }
    }
  }
  const changed = JSON.stringify(sources) !== JSON.stringify(state.sources);
  if (changed) state.sources = sources;
  state.lastSync = Math.max(0, ...state.sources.map(x => x.last_sync || 0));
  if (render && changed) {
    const mini = document.querySelector('.sidebar-sources'); if (mini) mini.innerHTML = renderMiniSources();
    document.querySelectorAll('[data-platform]').forEach(el => {const source = state.sources.find(s => s.id === el.dataset.platform);const dot = el.querySelector('.dot');if (dot) {dot.className = 'dot ' + statusDot(source);dot.title = sourceState(source);}});
    const footer = document.querySelector('#footer-status'); if (footer) footer.textContent = footerStatus();
    if (state.view === 'sources') renderView();
    icons();
  }
}
async function sync(platform = state.platform, conversationId = null) {
  if (state.busy) return;
  state.busy = true; renderShell();
  try {
    const body = {platform};
    if (conversationId) body.conversation_id = conversationId;
    const result = await api('/api/sync', null, {method: 'POST', body: JSON.stringify(body)});
    toast(result.message || '已开始同步');
    await refreshSources();
  } catch (e) {toast(e.message, true);}
  finally {state.busy = false; renderShell();}
}
function modal(title, body, footer = '', wide = false, focus = true) {
  invalidateSendCompose();
  document.querySelector('#overlay').innerHTML = `<div class="overlay-backdrop"><section class="dialog ${wide ? 'wide' : ''}" role="dialog" aria-modal="true" aria-label="${esc(title)}"><div class="dialog-head"><h2>${esc(title)}</h2>${iconButton('x', 'close-modal', '关闭')}</div><div class="dialog-body">${body}</div>${footer ? `<div class="dialog-foot">${footer}</div>` : ''}</section></div>`;
  icons();
  if (focus) setTimeout(() => document.querySelector('.dialog input, .dialog button')?.focus(), 0);
}
function closeModal() { invalidateSendCompose();if (state.exportBundleJob?.id && ['queued', 'running', 'cancelling'].includes(state.exportBundleJob.state)) cancelExportBundle(true);document.querySelector('#overlay').innerHTML = '';state.wechatRoster = [];state.groupRosterKey = '';state.groupRosterRequest++;state.managedContact = null; }
async function showPairReport(conversation, year = null) {
  if (!conversation || !conversation.platform || conversation.platform === 'all') {toast('请先选择具体平台的私聊会话', true);return;}
  if (conversation.kind === 'group') {toast('双人报告仅适用于私聊会话', true);return;}
  try {
    const report = await api('/api/reports/pair', {platform: conversation.platform, account_id: conversation.account_id, conversation_id: conversation.id, year});
    const hourly = Array.from({length: 24}, (_, hour) => ({hour, count: report.hourly?.find(item => item.hour === hour)?.count || 0}));
    const max = Math.max(1, ...hourly.map(item => item.count));
    const accessibleHourlyLabel = hourlyChartLabel(hourly);
    const types = (report.message_types || []).map(item => `<div class="type-row"><span>${esc(typeNames[item.type] || item.type)}</span><div class="type-bar"><div style="width:${Math.min(100, item.count / Math.max(1, report.total_messages) * 100)}%"></div></div><span class="mono muted">${fmt(item.count)}</span></div>`).join('');
    state.pairReport = report;
    const selectedYear = report.year == null ? '' : report.year;
    const body = `<form id="pair-report-range-form" class="annual-toolbar"><label for="pair-report-scope">范围</label><select id="pair-report-scope" name="scope" aria-label="双人报告范围"><option value="all" ${report.year == null ? 'selected' : ''}>全部时间</option><option value="year" ${report.year != null ? 'selected' : ''}>单年</option></select><label id="pair-report-year-label" for="pair-report-year" ${report.year == null ? 'hidden' : ''}>年份</label><input id="pair-report-year" name="year" type="number" min="1970" max="9999" value="${selectedYear}" ${report.year == null ? 'hidden disabled' : ''}></form><p class="report-subtitle">${badge(report.platform)} ${esc(report.conversation_title || conversation.title)} · ${report.year == null ? '全部时间' : `${report.year} 年`}</p><div class="metrics pair-metrics">${[['消息总数', report.total_messages], ['我发出', report.sent_messages], ['对方消息', report.counterpart_messages], ['活跃天数', report.active_days], ['平均回复', responseDuration(report.response_avg_seconds)], ['最快回复', responseDuration(report.response_fastest_seconds)]].map(([name, value]) => `<div class="metric"><div class="label">${name}</div><strong>${typeof value === 'number' ? fmt(value) : esc(value)}</strong></div>`).join('')}</div><p class="report-highlight">对话对象 <strong>${esc(report.counterpart_name || '对方')}</strong> <span class="muted">· 主动占比 ${Math.round((report.sent_ratio || 0) * 100)}% · 有效回复 ${fmt(report.response_count || 0)} 次</span></p><div class="section-title"><h3>消息时段</h3><span class="label">UTC+08:00</span></div><div class="hour-chart" role="img" aria-label="${esc(accessibleHourlyLabel)}">${hourly.map(item => `<div class="hour-bar" aria-hidden="true" style="height:${Math.max(2, item.count / max * 100)}%" title="${String(item.hour).padStart(2, '0')}:00 · ${fmt(item.count)} 条"></div>`).join('')}</div><div class="chart-labels"><span>00:00</span><span>06:00</span><span>12:00</span><span>18:00</span><span>23:00</span></div><div class="type-breakdown pair-types">${types || '<span class="muted small">暂无消息类型数据</span>'}</div>${report.first_timestamp ? `<p class="muted small report-range">${dt(report.first_timestamp, true)} 至 ${dt(report.last_timestamp, true)}</p>` : ''}`;
    modal('双人报告', body, `<button class="btn" type="submit" form="pair-report-range-form">应用范围</button><button class="btn" data-action="download-pair-report">${icon('download')}导出 CSV</button>`, true);
  } catch (error) {toast(error.message, true);}
}
async function showRelationshipTimeline(conversation) {
  if (!conversation) return;
  try {
    const params = {
      platform: conversation.platform, account_id: conversation.account_id,
      conversation_id: conversation.conversation_id, limit: 366
    };
    const [result, achievements] = await Promise.all([
      api('/api/analytics/relationships/timeline', params),
      api('/api/analytics/relationships/achievements', params)
    ]);
    const days = [...(result.items || [])].reverse();
    const max = Math.max(1, ...days.map(item => item.message_count));
    const timeline = days.length ? `<p class="muted small">只包含日期、消息数量和媒体数量，不显示消息正文。</p><div class="activity-chart">${days.map(item => `<div class="chart-bar" style="height:${Math.max(2, item.message_count / max * 100)}%" title="${esc(item.date)} · ${fmt(item.message_count)} 条 · 媒体 ${fmt(item.media_count)}"></div>`).join('')}</div><div class="chart-labels"><span>${esc(days[0].date)}</span><span>${esc(days.at(-1).date)}</span></div><div class="table-scroll"><table class="analytics-table"><thead><tr><th>日期</th><th>消息</th><th>发出 / 收到</th><th>媒体</th></tr></thead><tbody>${[...days].reverse().map(item => `<tr><td>${esc(item.date)}</td><td>${fmt(item.message_count)}</td><td>${fmt(item.sent_count)} / ${fmt(item.received_count)}</td><td>${fmt(item.media_count)}</td></tr>`).join('')}</tbody></table></div>` : empty('所选会话暂无活动记录');
    const achievementSection = achievements.available ? `<div class="section-title relationship-achievement-title"><h3>客观里程碑</h3><span class="label">${esc(achievements.coverage)}</span></div><div class="relationship-achievement-list">${achievements.items.map(item => `<div class="relationship-achievement"><div class="relationship-achievement-copy"><strong>${esc(item.title)}</strong><span class="small muted">${esc(item.description)}</span></div><span class="relationship-achievement-state ${item.unlocked ? 'unlocked' : ''}">${item.unlocked ? '已达成' : `${fmt(item.current)} / ${fmt(item.threshold)}`}</span><div class="type-bar"><div style="width:${Math.min(100, Math.round(item.current / item.threshold * 100))}%"></div></div></div>`).join('')}</div>` : `<p class="muted small">${esc(achievements.reason || '当前会话不支持关系成就')}</p>`;
    const body = `${achievementSection}<div class="section-title relationship-achievement-title"><h3>按日互动</h3><span class="label">最近 ${days.length} 个活跃日 · UTC+08</span></div>${timeline}`;
    modal('按日互动足迹 · ' + (conversation.title || conversation.conversation_id), body, '', true);
  } catch (error) {toast(error.message, true);}
}
function sendRouteBase(platform) {
  if (platform === 'wechat') return '/api/wechat-sends';
  return platform === 'wecom_bot' ? '/api/wecom-bot-sends' : '/api/sends';
}
function showSendComposer() {
  const conversation = state.selected;
  if (!conversation || !['qq', 'wecom', 'wechat'].includes(conversation.platform)) return;
  const type = conversation.kind === 'group' ? 'group' : 'private';
  const isBot = conversation.platform === 'wecom';
  const isWeChat = conversation.platform === 'wechat';
  if (isWeChat && state.settings.capabilities?.wechat_send?.enabled !== true) {
    toast('请先在设置中启用“个人微信文本发送”', true);return;
  }
  if (isBot && (!state.wecomBotIdentity?.configured || state.settings.capabilities?.wecom_bot_send?.enabled !== true)) {
    toast('请先在设置中配置机器人身份并启用发送能力', true);return;
  }
  const wechatTargetPattern = '[A-Za-z0-9_-]{1,64}(@chatroom)?';
  modal(isBot ? '准备企微机器人发送' : isWeChat ? '准备个人微信文本发送' : '准备 QQ 文本发送', `<form id="send-compose-form" data-send-platform="${isBot ? 'wecom_bot' : isWeChat ? 'wechat' : 'qq'}"><p class="muted small">请求先进入本机审批队列，不会立即发送。${isBot ? '发送方是企业微信智能机器人，不是当前员工。请填写机器人可达的成员 ID 或群聊 ID，并核对权限。' : isWeChat ? '发送方是当前登录的本机微信账号。批准后会通过姓名窗口操作已打开的聊天窗口，请先在微信中打开目标会话。' : '发送方是当前登录的本机 QQ 账号。'}</p>${isBot ? `<div class="pill">${esc(state.wecomBotIdentity.identity)}</div><input type="hidden" name="account_id" value="${esc(state.wecomBotIdentity.bot_id)}"><label>消息类型<select name="media_type"><option value="markdown">Markdown 文本</option><option value="image">图片</option><option value="file">文件</option><option value="voice">AMR 语音</option><option value="video">视频</option></select></label>` : isWeChat ? `<label>本机微信账号<input name="account_id" pattern="[A-Za-z0-9_-]{1,64}" maxlength="64" value="${esc(conversation.account_id)}" required></label>` : `<label>QQ 账号<input name="account_id" inputmode="numeric" pattern="[0-9]{5,20}" maxlength="20" value="${esc(conversation.account_id)}" required></label>`}<label>${isBot ? (type === 'group' ? '机器人群聊 ID' : '企业微信成员 ID') : '接收目标'}<input name="conversation_id" ${isWeChat ? `pattern="${wechatTargetPattern}" maxlength="73"` : isBot ? 'pattern="[A-Za-z0-9_@.-]{1,128}" maxlength="128"' : 'pattern="[A-Za-z0-9_.-]{1,64}" maxlength="64"'} value="${esc(conversation.id)}" required></label><p class="muted small" data-send-target-hint>${isBot || isWeChat ? '' : '可填本地会话 UID（u_&hellip;）或 QQ 号 / 群号；本机会先解析成号码再进入审批。'}</p><input type="hidden" name="conversation_type" value="${type}">${isBot ? '<label data-media-file-label hidden>本机附件<input name="media_file" type="file" accept="image/jpeg,image/png,image/gif,image/webp" disabled></label>' : ''}<label data-send-text-label>${isBot ? '文本' : '文本'}<textarea name="text" maxlength="4000" rows="5" ${isBot ? '' : 'required'}></textarea></label></form>`, '<button class="btn" data-action="close-modal">取消</button><button class="btn primary" type="submit" form="send-compose-form">' + icon('clipboard-plus') + '加入待审批</button>');
  const form = document.querySelector('#send-compose-form');
  if (form) form.dataset.sendComposeRevision = String(sendComposeRevision);
}
function reviewSend(requestId) {
  const request = state.pendingSends.find(item => item.request_id === requestId) ||
    (state.sendReview?.request_id === requestId ? state.sendReview : null);
  if (!request) {toast('发送请求不存在或已处理', true);return;}
  state.sendReview = request;
  const isBot = request.platform === 'wecom_bot';
  const isWeChat = request.platform === 'wechat';
  const attachment = request.attachment;
  const attachmentUrl = `/api/wecom-bot-sends/${encodeURIComponent(request.request_id)}/attachment-preview`;
  const attachmentPreview = !isBot || !attachment ? '' : attachment.media_type === 'image'
    ? `<img class="send-review-image" src="${attachmentUrl}" alt="待发送图片：${esc(attachment.filename)}">`
    : attachment.media_type === 'video'
      ? `<video class="send-review-video" src="${attachmentUrl}" controls preload="metadata" aria-label="待发送视频：${esc(attachment.filename)}"></video>`
      : `<a class="btn send-review-download" href="${attachmentUrl}" download>${icon('download')}${attachment.media_type === 'voice' ? '下载 AMR 语音后核验' : '下载文件后核验'}</a>`;
  modal(isBot ? '审核企微机器人发送' : isWeChat ? '审核个人微信发送' : '审核 QQ 发送', `<div class="send-review"><dl><dt>发送身份</dt><dd>${isBot ? '企业微信智能机器人（非当前员工身份）' : isWeChat ? '当前登录的本机微信账号（GUI 拟人操作）' : '当前登录的本机 QQ 账号'}</dd><dt>机器人 / 账号</dt><dd class="mono">${esc(request.account_id)}</dd><dt>类型</dt><dd>${request.conversation_type === 'group' ? '群聊' : '私聊'}</dd><dt>目标标识</dt><dd class="mono">${esc(request.conversation_id)}</dd>${request.source_conversation_id && request.source_conversation_id !== request.conversation_id ? `<dt>本地会话</dt><dd class="mono">${esc(request.source_conversation_id)}</dd>` : ''}<dt>请求 ID</dt><dd class="mono">${esc(request.request_id)}</dd></dl>${attachment ? `<div class="send-review-content"><span class="label">待发送附件 · ${esc(attachment.media_type)}</span><p>${esc(attachment.filename)} · ${fmt(attachment.size)} bytes</p>${attachmentPreview}</div>` : `<div class="send-review-content"><span class="label">将要发送的完整文本</span><pre>${esc(request.text)}</pre></div>`}<p class="history-warning">${isBot ? '批准后将以机器人身份发送，不代表当前员工。' : isWeChat ? '批准后将操作本机微信窗口实际发送，只支持纯文本；同名联系人或未打开的会话会直接拒绝。发送前会清空目标输入框内未发送的草稿。' : '批准后将通过当前登录的本机 QQ 账号发送。'}请核对发送身份、目标与内容。发送超时会记录为“状态未知”，不会自动重试。</p></div>`, `<button class="btn" data-action="close-modal">稍后处理</button><button class="btn danger" data-action="reject-send:${esc(request.request_id)}">${icon('x')}拒绝</button><button class="btn primary" data-action="approve-send:${esc(request.request_id)}">${icon('send')}确认并发送</button>`, true);
}
async function approveSend(button, requestId) {
  const request = state.sendReview?.request_id === requestId ? state.sendReview : state.pendingSends.find(item => item.request_id === requestId);
  const base = sendRouteBase(request?.platform);
  const identity = request?.platform === 'wecom_bot' ? '企微机器人（非当前员工）' : request?.platform === 'wechat' ? '本机微信' : 'QQ';
  const content = request?.attachment ? `附件 ${request.attachment.filename}` : '这条完整文本';
  if (!request || !window.confirm(`最后确认：以${identity}身份向${request.conversation_type === 'group' ? '群' : '用户'} ${request.conversation_id} 发送${content}？${request.platform === 'wechat' ? '\n\n微信目标输入框中的未发送草稿会被清空。请先确认可清除。' : ''}`)) return;
  button.disabled = true;
  try {
    const challenge = await api(`${base}/${encodeURIComponent(requestId)}/approval-challenge`);
    const result = await api(`${base}/${encodeURIComponent(requestId)}/approve`, null,
      {method: 'POST', body: JSON.stringify({approval_token: challenge.token})});
    closeModal();state.sendReview = null;
    toast(result.status === 'sent' ? (request.platform === 'wecom_bot' ? '企微机器人消息已发送' : request.platform === 'wechat' ? '微信消息已发送' : 'QQ 消息已发送') : result.status === 'unknown' ? '发送状态未知；为避免重复投递未自动重试' : `发送状态：${result.status}`, result.status !== 'sent');
    if (state.view === 'settings') await loadView();
  } catch (error) {toast(error.message, true);button.disabled = false;}
}
async function rejectSend(button, requestId) {
  const request = state.sendReview?.request_id === requestId ? state.sendReview : state.pendingSends.find(item => item.request_id === requestId);
  const base = sendRouteBase(request?.platform);
  button.disabled = true;
  try {
    await api(`${base}/${encodeURIComponent(requestId)}/reject`, null,
      {method: 'POST', body: JSON.stringify({confirmation: 'REJECT_SEND'})});
    closeModal();state.sendReview = null;toast('发送请求已拒绝');
    if (state.view === 'settings') await loadView();
  } catch (error) {toast(error.message, true);button.disabled = false;}
}
async function prepareSend(form) {
  const revision = form.dataset.sendComposeRevision;
  const isCurrent = () => revision === String(sendComposeRevision) &&
    document.querySelector('#send-compose-form') === form;
  if (!isCurrent()) return;
  const values = Object.fromEntries(new FormData(form));
  const platform = form.dataset.sendPlatform || 'qq';
  const base = sendRouteBase(platform);
  const controller = new AbortController();
  sendComposeController = controller;
  try {
    let attachment;
    if (platform === 'wecom_bot' && values.media_type !== 'markdown') {
      const file = form.elements.media_file.files[0];
      if (!file) throw new Error('请选择要发送的本机附件');
      const uploaded = await fetch('/api/wecom-bot-sends/attachment', {method: 'POST',
        headers: {'Content-Type': 'application/octet-stream', 'X-Local-Filename': encodeURIComponent(file.name),
          'X-Local-Media-Type': values.media_type}, body: file, signal: controller.signal});
      if (!isCurrent()) return;
      const detail = await uploaded.json().catch(() => ({}));
      if (!uploaded.ok) throw new Error(detail.error || '附件上传失败');
      attachment = detail;
    }
    if (!isCurrent()) return;
    const request = await api(base + '/prepare', null, {method: 'POST', signal: controller.signal, body: JSON.stringify({
      account_id: values.account_id, conversation_type: values.conversation_type,
      conversation_id: values.conversation_id, text: values.text || '', attachment,
      request_id: crypto.randomUUID().replaceAll('-', '')
    })});
    if (!isCurrent()) return;
    closeModal();state.sendReview = request;
    if (state.view === 'settings') state.pendingSends = [request, ...state.pendingSends.filter(item => item.request_id !== request.request_id)];
    reviewSend(request.request_id);
  } catch (error) {if (isCurrent() && error.name !== 'AbortError') toast(error.message, true);}
  finally {if (sendComposeController === controller) sendComposeController = null;}
}
function showExport() {
  const title = state.view === 'chat' ? state.selected?.title : '搜索结果';
  if (!title) {toast('请先选择会话');return;}
  const mediaEnabled = state.settings?.capabilities?.media?.enabled === true;
  modal('导出 ' + title, `<form id="export-form"><div class="export-details"><span>${esc(title)}</span><span>${state.view === 'chat' ? fmt(state.messageTotal) : fmt(state.total)} 条匹配消息</span></div><div class="field"><label>文件格式</label><div class="format-options">${[['json', 'JSON'], ['html', 'HTML'], ['csv', 'CSV'], ['md', 'Markdown'], ['txt', 'TXT'], ['xlsx', 'Excel / XLSX'], ['sql', 'SQL'], ['chatlab', 'ChatLab JSON'], ['chatlab-jsonl', 'ChatLab JSONL']].map(([id, name], index) => `<label class="format-option"><input type="radio" name="format" value="${id}" ${!index ? 'checked' : ''}>${name}</label>`).join('')}</div></div><label class="format-option"><input type="checkbox" name="include_media" ${mediaEnabled ? '' : 'disabled'}>附带已解析媒体${mediaEnabled ? '' : '（媒体能力已关闭）'}</label><p class="muted small">仅打包本机已解析的媒体；未取得项会列入 manifest。</p><p id="export-bundle-status" class="muted small" hidden></p></form>`, '<button class="btn" data-action="close-modal">取消</button><button class="btn primary" data-action="download-export">' + icon('download') + '导出</button>');
}
async function downloadExport(button) {
  button.disabled = true;
  const format = new FormData(document.querySelector('#export-form')).get('format');
  const params = state.view === 'chat' ? {platform: state.selected.platform, account_id: state.selected.account_id, conversation_id: state.selected.id, ...state.messageFilter, format} : {platform: state.platform, ...state.filters, format};
  try {
    if (document.querySelector('#export-form [name="include_media"]')?.checked) {
      const query = state.view === 'chat'
        ? {platform: state.selected.platform, account_id: state.selected.account_id, conversation_id: state.selected.id, ...state.messageFilter, format}
        : {platform: state.platform, q: state.filters.q || '', type: state.filters.type || '', start: state.filters.start || '', end: state.filters.end || '', format};
      await downloadExportBundle(button, query);
      return;
    }
    const response = await fetch('/api/export?' + new URLSearchParams(params));
    if (!response.ok) throw new Error((await response.json()).error || '导出失败');
    const blob = await response.blob();
    const href = URL.createObjectURL(blob);
    const extension = ({'chatlab-jsonl': 'jsonl', xlsx: 'xlsx'})[format] || format;
    const link = document.createElement('a');link.href = href;link.download = `消息档案-${new Date().toISOString().slice(0, 10)}.${extension}`;
    document.body.append(link);link.click();link.remove();setTimeout(() => URL.revokeObjectURL(href), 10000);
    closeModal();toast('导出完成');
  } catch (e) {toast(e.message, true);button.disabled = false;}
}
async function downloadExportBundle(button, query) {
  const status = document.querySelector('#export-bundle-status');
  status.hidden = false;status.textContent = '正在准备会话导出…';
  const job = await api('/api/export/bundle', null, {method: 'POST', body: JSON.stringify({query})});
  state.exportBundleJob = job;
  let current = job;
  while (['queued', 'running', 'cancelling'].includes(current.state)) {
    status.textContent = current.total ? `正在打包附件 ${fmt(current.completed)} / ${fmt(current.total)}，成功 ${fmt(current.exported)}，未取得 ${fmt(current.failed)}` : '正在生成所选格式的导出文件…';
    await delay(500);
    current = await api('/api/resource-jobs/' + encodeURIComponent(job.id));
    state.exportBundleJob = current;
  }
  if (current.state === 'completed' && current.download_url) {
    state.exportBundleJob = null;
    closeModal();toast(`会话包已完成：成功 ${fmt(current.exported)} 项，未取得 ${fmt(current.failed)} 项`);
    location.href = current.download_url;
  } else if (current.state === 'cancelled') {
    state.exportBundleJob = null;status.textContent = '会话包已取消';button.disabled = false;
  } else {
    state.exportBundleJob = null;
    throw new Error(current.error === '会话导出附件超过单次 100 项上限，请缩小筛选范围' ? current.error : `会话包生成失败：${current.error || '请缩小筛选范围后重试'}`);
  }
}
async function cancelExportBundle(silent = false) {
  const job = state.exportBundleJob;
  if (!job?.id) return;
  state.exportBundleJob = {...job, state: 'cancelling'};
  try {
    await api('/api/resource-jobs/' + encodeURIComponent(job.id) + '/cancel', null, {method: 'POST'});
  } catch (error) {if (!silent) toast(error.message || '取消会话包失败', true);}
}
function showContactExport() {
  modal('导出联系人', '<form id="contact-export-form"><div class="field"><label>文件格式</label><div class="format-options"><label class="format-option"><input type="radio" name="format" value="csv" checked>CSV</label><label class="format-option"><input type="radio" name="format" value="json">JSON</label></div><p class="muted small">导出当前平台与搜索条件下的全部匹配联系人；临时头像链接不会写入文件。</p></div></form>', '<button class="btn" data-action="close-modal">取消</button><button class="btn primary" data-action="download-contact-export">' + icon('download') + '导出</button>');
}
async function downloadContactExport(button) {
  button.disabled = true;
  const format = new FormData(document.querySelector('#contact-export-form')).get('format');
  const scope = selectedContactScope();
  const params = new URLSearchParams({platform: scope?.platform || state.platform,
    account_id: scope?.account_id || '', q: state.filters.q || '', kind: state.filters.kind || '', format});
  try {
    const response = await fetch('/api/export/contacts?' + params);
    if (!response.ok) throw new Error((await response.json()).error || '联系人导出失败');
    const blob = await response.blob();
    const href = URL.createObjectURL(blob);
    const link = document.createElement('a');link.href = href;link.download = `联系人-${new Date().toISOString().slice(0, 10)}.${format}`;
    document.body.append(link);link.click();link.remove();setTimeout(() => URL.revokeObjectURL(href), 10000);
    closeModal();toast('联系人导出完成');
  } catch (error) {toast(error.message, true);button.disabled = false;}
}
async function downloadGroupRoster(button) {
  const conversation = state.selected;
  if (!conversation || !state.groupRosterKey || conversationNotificationKey(conversation) !== state.groupRosterKey) {
    toast('当前会话已变化，请重新打开群成员名单', true);
    return;
  }
  button.disabled = true;
  const format = new FormData(document.querySelector('#group-roster-export-form')).get('format');
  const params = new URLSearchParams({platform: conversation.platform, account_id: conversation.account_id,
    conversation_id: conversation.id, format});
  try {
    const response = await fetch('/api/export/group-members?' + params);
    if (!response.ok) throw new Error((await response.json()).error || '群成员导出失败');
    const href = URL.createObjectURL(await response.blob());
    const link = document.createElement('a');link.href = href;link.download = `群成员-${new Date().toISOString().slice(0, 10)}.${format}`;
    document.body.append(link);link.click();link.remove();setTimeout(() => URL.revokeObjectURL(href), 10000);
    closeModal();toast('群成员导出完成');
  } catch (error) {toast(error.message, true);button.disabled = false;}
}
async function exportMoments(button) {
  state.momentExporting = true;
  button.disabled = true;
  button.setAttribute('aria-disabled', 'true');
  button.innerHTML = `${icon('loader-circle', 'spin')}<span>正在导出</span>`;
  try {
    const query = new URLSearchParams({...Object.fromEntries(Object.entries(state.momentFilter).filter(([, value]) => value)), format: state.momentExportFormat});
    const response = await fetch('/api/moments/export?' + query.toString());
    if (!response.ok) {
      const problem = await response.json().catch(() => ({}));
      throw new Error(problem.error || `导出失败 (${response.status})`);
    }
    const blob = await response.blob();
    const href = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = href;
    link.download = `朋友圈档案-${new Date().toISOString().slice(0, 10)}.${state.momentExportFormat}`;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(href), 10000);
    toast('朋友圈筛选结果已导出');
  } catch (error) {
    toast(error.message || '朋友圈导出失败', true);
  } finally {
    state.momentExporting = false;
    renderView();
  }
}
function showConfigure(id) {
  if (id === 'email') {
    const config = state.sources.find(source => source.id === 'email')?.email_config || {};
    modal('配置邮件', `<form id="configure-form" data-source="email"><div class="field"><label for="email-provider">邮箱类型</label><select id="email-provider" name="provider">${[['imap','通用 IMAP'],['gmail','Gmail'],['outlook','Outlook / Microsoft 365']].map(([value,label]) => `<option value="${value}" ${config.provider === value ? 'selected' : ''}>${label}</option>`).join('')}</select></div><div class="field"><label for="email-host">IMAP 服务器</label><input id="email-host" name="host" value="${esc(config.host || '')}" placeholder="通用 IMAP 必填；Gmail / Outlook 留空使用默认服务器" autocomplete="off"></div><div class="field"><label for="email-port">TLS 端口</label><input id="email-port" name="port" type="number" min="1" max="65535" value="${esc(config.port || 993)}" required></div><div class="field"><label for="email-username">邮箱账号</label><input id="email-username" name="username" type="email" value="${esc(config.email || config.username || '')}" required autocomplete="off"></div><div class="field"><label for="email-auth">认证方式</label><select id="email-auth" name="auth"><option value="password" ${config.auth !== 'oauth2' ? 'selected' : ''}>应用密码 / 授权码</option><option value="oauth2" ${config.auth === 'oauth2' ? 'selected' : ''}>OAuth2 访问令牌</option></select></div><div class="field"><label for="email-password">应用密码 / 授权码</label><input id="email-password" name="password" type="password" autocomplete="off" placeholder="留空保留已保存凭据"><small>使用邮箱服务商提供的应用密码或 IMAP 授权码。</small></div><div class="field"><label for="email-token">OAuth2 访问令牌</label><input id="email-token" name="access_token" type="password" autocomplete="off" placeholder="留空保留已保存凭据"><small>令牌需已授予 IMAP 权限；当前不会自动刷新，过期后需重新填写。</small></div><div class="field"><label for="email-folder">邮件文件夹</label><input id="email-folder" name="folder" value="${esc(config.folder || 'INBOX')}" required autocomplete="off"></div><p class="muted small">凭据不会回显；仅同步读取邮件，不发送邮件或更改已读状态。</p></form>`, '<button class="btn" data-action="close-modal">取消</button><button class="btn primary" data-action="save-config">' + icon('check') + '保存并同步</button>');
    return;
  }
  const directory = `<div class="field"><label for="data-directory">数据目录</label><input id="data-directory" name="data_dir" placeholder="留空保留配置或自动检测" autocomplete="off"><small>${id === 'wecom' ? '首次配置请填写本机绝对路径；已配置时留空保留当前值。' : '填写时使用本机绝对路径；留空保留已配置目录，尚未配置时自动检测。'}</small></div>`;
  const wecom = id === 'wecom' ? `<div class="field"><label for="database-key">数据库密钥</label><input id="database-key" name="key" type="password" placeholder="留空使用本机已保存的密钥" autocomplete="new-password"><small>密钥仅保存在本机配置。</small></div><div class="field"><label for="user-slot-id">账号槽位标识</label><input id="user-slot-id" name="user_slot_id" placeholder="首次配置必填" autocomplete="off"><small>留空时保留已保存的标识。</small></div>` : '';
  const key = id === 'wecom' ? wecom : id === 'qq' ? `<div class="field"><label for="database-key">数据库密钥</label><input id="database-key" name="key" type="password" placeholder="留空保留本机已保存的密钥" autocomplete="new-password"><small>密钥仅保存在本机，不会回显。未解密时不会启动 QQ 或请求扫码。</small></div>` : `<div class="field"><label for="image-key">图片 AES 密钥</label><input id="image-key" name="image_key" type="password" minlength="16" maxlength="16" pattern="[ -~]{16}" placeholder="已有密钥则留空保持不变" autocomplete="new-password"><small>16 位可打印 ASCII。必须能解开本机已有图片缓存；使用 Windows 当前用户 DPAPI 加密保存，按微信账号隔离。不会读取微信进程内存，也不会修改微信文件。</small></div>`;
  modal('配置 ' + platforms[id].name, `<form id="configure-form" data-source="${id}">${directory}${key}</form>`, '<button class="btn" data-action="close-modal">取消</button><button class="btn primary" data-action="save-config">' + icon('check') + '保存并同步</button>');
}
async function saveConfig(button) {
  const form = document.querySelector('#configure-form');
  if (!form.reportValidity()) return;
  button.disabled = true;
  try {
    const config = Object.fromEntries(new FormData(form));
    if (form.dataset.source === 'email') {config.email = config.username;if (!config.host) delete config.host;}
    await api('/api/source/' + form.dataset.source + '/configure', null, {method: 'POST', body: JSON.stringify(config)});
    closeModal();toast('本机配置已保存');await sync(form.dataset.source);
  } catch (e) {toast(e.message, true);button.disabled = false;}
}
async function discoverQQKey() {
  const message = '将只读扫描当前 Windows 用户运行中的 QQ 进程内存，最多 180 秒。请保持 QQ 已打开并登录。不会调试、注入或关闭 QQ；只有通过本地数据库校验的密钥会保存到本机配置，并立即开始同步本机 QQ 历史记录。密钥和聊天内容不会发送到网络服务。继续？';
  if (!window.confirm(message)) return;
  try {
    await api('/api/qq/key-discovery', null, {method: 'POST', body: JSON.stringify({confirm: true})});
    await refreshSources();
    for (let attempt = 0; attempt < 190; attempt++) {
      await new Promise(resolve => setTimeout(resolve, 1000));
      const result = await api('/api/qq/key-discovery');
      if (attempt % 4 === 3 || result.state !== 'running') await refreshSources();
      if (result.state !== 'running') {
        toast(result.message || (result.state === 'completed' ? 'QQ 本机密钥已验证' : '只读密钥发现已结束'), result.state !== 'completed');
        return;
      }
    }
    toast('只读查找仍在运行；可稍后查看数据源状态');
  } catch (error) {
    toast(error.message || 'QQ 只读密钥发现失败', true);
  }
}
function localMediaPreviewUrl(url, mimeType) {
  return url.startsWith('/local-media/') && ['image/heic', 'image/heif', 'image/tiff'].includes(String(mimeType || '').toLowerCase())
    ? `${url}?preview=jpeg` : url;
}
function previewMedia(url, type, name = '', mimeType = '') {
  if (!safeUrl(url)) {toast('本机未缓存此媒体');return;}
  const kind = type === 'audio' ? 'voice' : type;
  const wma = kind === 'voice' && /\.wma$/i.test(String(name || ''));
  const silk = kind === 'voice' && /\.silk$/i.test(String(name || ''));
  const convertedImage = kind === 'image' && ['image/heic', 'image/heif', 'image/tiff'].includes(String(mimeType).toLowerCase());
  const markup = kind === 'image' ? `<img class="preview-media" src="${esc(localMediaPreviewUrl(url, mimeType))}" alt="${esc(name || '聊天图片')}">${convertedImage ? `<a class="btn" href="${esc(url)}" download="${esc(name || 'image')}">${icon('download')}下载原件</a>` : ''}` : kind === 'video' ? `<video class="preview-media" src="${esc(url)}" controls autoplay></video>` : silk ? `<a class="btn" href="${esc(url)}" download>${icon('download')}下载 ${esc(name)}</a>` : kind === 'voice' ? `<audio src="${esc(url)}" controls autoplay></audio>${wma ? `<a class="btn" href="${esc(url)}" download>${icon('download')}下载 ${esc(name || 'WMA 音频')}</a>` : ''}` : `<a class="btn" href="${esc(url)}" download>${icon('download')}下载 ${esc(name || '文件')}</a>`;
  modal(name || typeNames[kind] || '媒体', markup, '', true);
}
async function resolveMedia(message, resourceIndex = null) {
  try {
    const qqCache = message.extra?.resource_kind === 'qq_cache' || message.resource_kind === 'qq_cache';
    const wechatSource = message.extra?.resource_kind === 'wechat_source' || message.resource_kind === 'wechat_source';
    const result = await api('/api/media/resolve', wechatSource
      ? {resource_kind: 'wechat_source', platform: 'wechat', account_id: message.account_id,
        conversation_id: message.conversation_id, shard: message.extra.shard,
        local_id: String(message.extra.local_id),
        ...(message.type === 'sticker' ? {media_type: 'sticker'}
          : message.type === 'video' ? {video_variant: 'poster'} : {image_tier: 'mid'})}
      : qqCache
      ? {resource_kind: 'qq_cache', platform: 'qq', account_id: message.account_id,
        resource_id: message.extra?.resource_id || message.id}
      : {platform: message.platform, account_id: message.account_id, message_id: message.id, conversation_id: message.conversation_id, resource_index: resourceIndex});
    if (!result.media_url) {message.media_available = false;renderView();toast(result.message || '本机未缓存此媒体');return;}
    if (qqCache) {
      message.media_url = result.media_url;
      message.media_mime_type = result.mime_type || message.extra?.mime_type;
      message.file_name = result.download_name || message.file_name;
      renderView();previewMedia(result.media_url, message.type, message.file_name, message.media_mime_type);return;
    }
    if (resourceIndex !== null) {
      const resources = message.extra?.roaming_resources || message.extra?.attachments;
      const resource = message.media_entry?.resource_index === resourceIndex
        ? message.media_entry.resource : resources?.[resourceIndex];
      if (!resource) {toast('附件索引已变化，请刷新会话', true);return;}
      resource.media_url = result.media_url;
      resource.mime_type = result.mime_type || resource.mime_type;
      if (message.media_entry) message.media_entry.resource = resource;
      renderView();
      const kind = normalizeMediaType(resource.type);
      previewMedia(result.media_url, kind, resource.name || resource.n, result.mime_type);
      return;
    }
    message.media_url = result.media_url;
    message.media_mime_type = result.mime_type;
    message.media_available = true;
    renderView();previewMedia(result.media_url,
      result.mime_type === 'application/x-wechat-wxgf' ? 'file' : message.type,
      result.download_name || message.file_name || (result.mime_type === 'application/x-wechat-wxgf' ? '微信图片.wxgf' : ''),
      result.mime_type);
  } catch (e) {toast(e.message, true);}
}
async function saveSettings(patch) {
  try {state.settings = await api('/api/settings', null, {method: 'POST', body: JSON.stringify(patch)});if ('mcp_write_enabled' in patch) state.mcp = await api('/api/mcp');syncAccountLockPolling();syncNotificationStream();renderView();toast('设置已保存');}
  catch (e) {toast(e.message, true);}
}
async function saveWeComBotCredentials(form) {
  const values = Object.fromEntries(new FormData(form));
  try {
    state.wecomBotIdentity = await api('/api/wecom-bot/identity', null,
      {method: 'POST', body: JSON.stringify(values)});
    form.reset();
    renderView();
    toast('机器人凭据已使用 Windows DPAPI 加密保存');
  } catch (error) {toast(error.message, true);}
}
async function removeWeComBotCredentials() {
  if (!window.confirm('移除本机保存的企业微信机器人密钥？待审批请求将无法投递。')) return;
  try {
    state.wecomBotIdentity = await api('/api/wecom-bot/identity/remove', null, {method: 'POST',
      body: JSON.stringify({confirmation: 'REMOVE_WECOM_BOT_CREDENTIALS'})});
    renderView();toast('机器人凭据已移除');
  } catch (error) {toast(error.message, true);}
}
async function lockAccountNow() {
  try {
    await api('/api/account-safety/lock', null, {method: 'POST', body: '{}'});
    returnToLockScreen();
  } catch (error) {toast(error.message, true);}
}
async function disableAccountLock(form) {
  const values = Object.fromEntries(new FormData(form));
  try {
    await api('/api/account-safety/disable', null,
      {method: 'POST', body: JSON.stringify(values)});
    location.reload();
  } catch (error) {toast(error.message, true);}
}
function syncNotificationStream() {
  const enabled = state.settings.capabilities?.notifications?.enabled === true;
  if (!enabled) {
    notificationStream?.close();notificationStream = null;return;
  }
  if (notificationStream) return;
  const stream = new EventSource('/api/events/messages');
  notificationStream = stream;
  stream.addEventListener('error', async () => {
    if (notificationStream !== stream || !state.settings.account_lock_enabled) return;
    try {
      const status = await api('/api/account-safety/status');
      if (status.locked) returnToLockScreen();
    } catch { /* A transient event-stream failure is not evidence that the app was locked. */ }
  });
  stream.addEventListener('message', event => {
    if (accountLockTransition || notificationStream !== stream) return;
    let message;
    try {message = JSON.parse(event.data);} catch {return;}
    if (!notificationAllowsMessage(message)) return;
    if (!document.hidden) return;
    api('/api/notifications/message', null, {method: 'POST',
      body: JSON.stringify({event_id: Number(event.lastEventId)})}).catch(() => {});
  });
}
function notificationAllowsMessage(message) {
  if (!message || !platforms[message.platform] ||
      typeof message.account_id !== 'string' || typeof message.conversation_id !== 'string') return false;
  const filters = notificationFilters();
  if (!(filters.platforms || []).includes(message.platform)) return false;
  const key = JSON.stringify([message.platform, message.account_id, message.conversation_id]);
  if ((filters.muted_conversations || []).includes(key)) return false;
  return filters.mode !== 'allowlist' || (filters.allowed_conversations || []).includes(key);
}
function refreshNotificationPermission() {
  if (!document.hidden) syncNotificationStream();
}
document.addEventListener('visibilitychange', refreshNotificationPermission);
window.addEventListener('focus', refreshNotificationPermission);
function sendTestNotification() {
  if (state.settings.capabilities?.notifications?.enabled !== true) {
    toast('先启用桌面通知再发送测试', true);return;
  }
  api('/api/notifications/test', null, {method: 'POST', body: '{}'})
    .then(() => toast('测试通知已提交给 Windows，是否显示请查看桌面'))
    .catch(error => toast(error.message, true));
}
async function openNotificationTarget(token) {
  const target = await api('/api/notifications/activate', null,
    {method: 'POST', body: JSON.stringify({token})});
  if (!platforms[target.platform] || !target.account_id || !target.conversation_id) {
    throw new Error('通知会话目标无效');
  }
  state.view = 'chat';state.platform = target.platform;state.cq = '';
  state.selected = {id: target.conversation_id, account_id: target.account_id,
    platform: target.platform, title: target.conversation_title,
    kind: target.conversation_kind};
  state.contextMessage = null;state.contextMode = false;
  renderShell();await loadView();
}
async function toggleNotificationCapability(enabled) {
  await saveSettings({capabilities: {notifications: enabled}});
  renderShell();
}
async function toggleNotificationPlatform(platform, checked) {
  const filters = notificationFilters();
  const platforms = new Set(filters.platforms || []);
  if (checked) platforms.add(platform); else platforms.delete(platform);
  await saveSettings({notification_filters: {...filters, platforms: [...platforms]}});
}
async function toggleConversationNotifications(conversation) {
  const filters = notificationFilters();
  const key = conversationNotificationKey(conversation);
  const muted = new Set(filters.muted_conversations || []);
  if (muted.has(key)) muted.delete(key); else muted.add(key);
  await saveSettings({notification_filters: {...filters, muted_conversations: [...muted]}});
}
async function toggleConversationAllowlist(conversation) {
  const filters = notificationFilters();
  const key = conversationNotificationKey(conversation);
  const allowed = new Set(filters.allowed_conversations || []);
  if (allowed.has(key)) allowed.delete(key); else allowed.add(key);
  await saveSettings({notification_filters: {
    ...filters,
    mode: allowed.size ? 'allowlist' : 'all',
    allowed_conversations: [...allowed]
  }});
}
async function saveAutoExport(form) {
  const values = Object.fromEntries(new FormData(form));
  values.enabled = new FormData(form).get('enabled') === 'on';
  values.interval_hours = Number(values.interval_hours);
  try {
    state.settings.auto_export_status = await api('/api/auto-export/configure', null,
      {method: 'POST', body: JSON.stringify(values)});
    renderView();
    toast('自动导出计划已保存');
  } catch (error) {toast(error.message, true);}
}
async function autoExportAction(action) {
  try {
    state.settings.auto_export_status = await api(`/api/auto-export/${action}`, null,
      {method: 'POST', body: '{}'});
    renderView();
    if (action === 'run') {
      toast('本机导出任务已开始');
      while (state.settings.auto_export_status?.running) {
        await new Promise(resolve => setTimeout(resolve, 700));
        state.settings.auto_export_status = await api('/api/auto-export');
        if (state.view === 'settings') renderView();
      }
      if (state.view === 'settings') renderView();
    } else toast('已请求取消导出');
  } catch (error) {toast(error.message, true);}
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
async function checkQQSendReadiness() {
  try {
    state.qqSendReadiness = await api('/api/qq/send-readiness');
    renderView();
  } catch (error) {toast(error.message, true);}
}
async function checkWeChatSendReadiness() {
  try {
    state.wechatSendReadiness = await api('/api/wechat-send-readiness');
    renderView();
  } catch (error) {toast(error.message, true);}
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
  if (event.target.closest('[data-media-select]')) return;
  const target = event.target.closest('button, input[data-action], [data-item], [data-media-url]');
  if (!target) {if (event.target.classList.contains('overlay-backdrop')) closeModal();return;}
  if (target.dataset.station) {await navigateCampus(target.dataset.station);return;}
  if (target.dataset.campusTopic !== undefined) {state.campus.filters.topic = target.dataset.campusTopic;state.campus.offset = 0;await loadView();return;}
  if (target.dataset.campusDate) {state.campus.filters.date = target.dataset.campusDate;state.campus.offset = 0;await loadView();return;}
  if (target.dataset.platform) {
    clearMediaSelection();
    if (state.view === 'campus') {state.view = lastChatView;closeModal();if (typeof history !== 'undefined') history.replaceState(null, '', '#' + state.view);}
    state.remembered[state.platform] = state.selected;
    state.platform = target.dataset.platform;state.filters.account_scope = '';state.selected = state.remembered[state.platform] || null;state.contextMessage = null;state.contextMode = false;
    state.messages = [];state.messageCursor = null;state.messageHasMore = false;state.items = [];state.page = 0;state.pageCursors = [null];renderShell();await loadView();return;
  }
  if (target.dataset.view) {clearMediaSelection();state.view = target.dataset.view;state.items = [];state.filters = {q: '', type: '', kind: '', start: '', end: '', source: 'all', account_scope: '', conversation_id: ''};state.page = 0;state.pageCursors = [null];renderShell();await loadView();return;}
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
  if (target.dataset.mediaUrl) {previewMedia(target.dataset.mediaUrl, target.dataset.mediaType, '', target.dataset.mediaMime);return;}
  if (target.dataset.item !== undefined) {
    const item = state.view === 'media' ? mediaEntries()[Number(target.dataset.item)] : state.items[Number(target.dataset.item)];
    if (state.view === 'media') {
      const mediaUrl = safeUrl(item.resource?.media_url || item.message.media_url);
      if (mediaUrl) previewMedia(mediaUrl, item.type, item.name, item.resource?.mime_type || item.message.extra?.mime_type || item.message.media_mime_type);
      else if (item.resourceKind === 'moment') toast('本机未缓存此朋友圈媒体');
      else await resolveMedia(item.message, item.resourceIndex);
    }
    else await openMessageContext(item);
    return;
  }
  const action = target.dataset.action;
  if (!action) return;
  if (action.startsWith('video-poster:')) {
    const entry = mediaEntries()[Number(action.split(':')[1])];
    if (entry?.type === 'video') previewMedia(entry.resource?.media_poster_url || entry.message.media_poster_url,
      'image', '视频封面', entry.resource?.media_poster_mime_type || entry.message.media_poster_mime_type);
    return;
  }
  else if (action === 'qq-forward-read' || action.startsWith('qq-forward-resource:')) {
    const messageId = target.closest('.message')?.dataset.messageId;
    const message = state.messages.find(item => String(item.id) === String(messageId));
    if (message) {
      if (action === 'qq-forward-read') await loadQQForwardResources(message);
      else {
        const row = state.qqForwardResources.get(qqForwardResourceKey(message));
        const item = row?.items[Number(action.slice('qq-forward-resource:'.length))];
        if (item) await downloadQQForwardResource(item, target);
      }
    }
  }
  else if (action === 'open-wechat-quote') {
    await openWechatQuote(target.closest('.message')?.dataset.messageId);
  }
  else if (action === 'conversation-media' && state.selected) {clearMediaSelection();state.view = 'media';state.platform = state.selected.platform;state.filters = {q: '', type: '', kind: '', start: '', end: '', source: 'chat', account_scope: JSON.stringify([state.selected.platform, state.selected.account_id]), conversation_id: state.selected.id};state.items = [];state.page = 0;state.pageCursors = [null];renderShell();await loadView();}
  else if (action.startsWith('open-media-chat:')) {const entry = mediaEntries()[Number(action.slice('open-media-chat:'.length))];if (entry?.message?.conversation_id && entry.message.id != null) await openMessageContext(entry.message);}
  else if (action.startsWith('wechat-best-image:')) await loadBestWechatImage(Number(action.slice('wechat-best-image:'.length)));
  if (action === 'theme') {setTheme(document.body.dataset.theme === 'dark' ? 'light' : 'dark');renderShell();}
  else if (action === 'prepare-send') showSendComposer();
  else if (action.startsWith('review-send:')) reviewSend(action.slice('review-send:'.length));
  else if (action.startsWith('approve-send:')) await approveSend(target, action.slice('approve-send:'.length));
  else if (action.startsWith('reject-send:')) await rejectSend(target, action.slice('reject-send:'.length));
  else if (action.startsWith('stats-tab:')) {state.statsTab = action.slice('stats-tab:'.length);state.error = '';await loadView();}
  else if (action.startsWith('group-members:')) await toggleGroupMembers(decodeURIComponent(action.slice('group-members:'.length)));
  else if (action === 'load-annual-report') {
    if (state.reportYearScope !== 'all') {
      const year = Number(document.querySelector('#report-year')?.value);
      if (!Number.isInteger(year) || year < 1970 || year > 9999) {toast('年份需在 1970 至 9999 之间', true);return;}
      state.reportYear = year;
    }
    await loadView();
  }
  else if (action === 'download-annual-report') downloadAnnualReport();
  else if (action === 'download-pair-report') downloadPairReport();
  else if (action === 'campus-article') await showCampusArticle(target.dataset.id);
  else if (action === 'campus-fetch-article') await showCampusArticle(target.dataset.id, true, target);
  else if (action === 'campus-updates') showCampusUpdates();
  else if (action === 'campus-scoring-toggle') await updateCampusScoring(target);
  else if (action === 'campus-scoring-run') await updateCampusScoring(target, true);
  else if (action === 'campus-profile') showCampusProfile();
  else if (action === 'campus-wechat') showCampusWechat();
  else if (action === 'campus-wechat-accounts') await showCampusWechatAccounts();
  else if (action === 'campus-refresh') await refreshCampus();
  else if (action === 'campus-source-toggle') {
    const source = state.campus.sources.find(item => item.id === target.dataset.id);
    if (!source) return;
    const request = state.request;target.disabled = true;
    try {await api('/api/campus/sources/' + encodeURIComponent(source.id), null, {method: 'POST', body: JSON.stringify({enabled: source.enabled === false})});if (state.view === 'campus' && request === state.request) await loadView();}
    catch (error) {toast(error.message, true);} finally {target.disabled = false;}
  }
  else if (action === 'campus-bookmark') await setCampusItemState(target.dataset.id, 'bookmarked');
  else if (action === 'campus-read') await setCampusItemState(target.dataset.id, 'read');
  else if (action === 'campus-prev') {state.campus.offset = Math.max(0, state.campus.offset - 50);await loadView();}
  else if (action === 'campus-next') {state.campus.offset += 50;await loadView();}
  else if (action === 'moments-prev') {state.momentOffset = Math.max(0, state.momentOffset - 30);await loadView();}
  else if (action === 'moments-next') {state.momentOffset += 30;await loadView();}
  else if (action === 'moments-export') await exportMoments(target);
  else if (action === 'pair-report' && state.selected) await showPairReport(state.selected);
  else if (action === 'conversation-roster') await showConversationRoster();
  else if (action === 'download-group-roster') await downloadGroupRoster(target);
  else if (action === 'pair-report-profile') await showPairReport({platform: target.dataset.platform, account_id: target.dataset.account, id: target.dataset.conversation, title: target.dataset.title, kind: 'private'});
  else if (action.startsWith('relationship-timeline:')) await showRelationshipTimeline(state.relationshipProfiles[Number(action.slice('relationship-timeline:'.length))]);
  else if (action === 'sync' || action.startsWith('sync:')) await sync(action.includes(':') ? action.split(':')[1] : state.platform);
  else if (action === 'sync-conversation' && state.selected?.platform === 'qq') await sync('qq', state.selected.id);
  else if (action === 'back') {state.selected = null;state.contextMessage = null;state.contextMode = false;renderView();}
  else if (action === 'reload') await loadView();
  else if (action === 'dates') {state.showDates = !state.showDates;renderView();}
  else if (action === 'older') {const previous = document.querySelector('.messages');const height = previous?.scrollHeight;await loadMessages(true);renderView();const next = document.querySelector('.messages');if (next && height) next.scrollTop = next.scrollHeight - height;}
  else if (action === 'latest') {if (state.messageOffset > state.messages.length || state.contextMode) {state.contextMessage = null;state.contextMode = false;await loadMessages(false);renderView();}const node = document.querySelector('.messages');if (node) node.scrollTop = node.scrollHeight;}
  else if (action === 'details' && state.selected) modal('会话详情', renderInspector(state.selected));
  else if (action === 'export') showExport();
  else if (action.startsWith('transcribe-voice:')) {const id = decodeURIComponent(action.slice('transcribe-voice:'.length));const message = state.messages.find(item => item.id === id);if (message) await transcribeVoice(message);}
  else if (action === 'open-voice-settings') {state.view = 'settings';renderShell();await loadView();}
  else if (action === 'export-contacts') showContactExport();
  else if (action === 'download-contact-export') await downloadContactExport(target);
  else if (action === 'download-export') await downloadExport(target);
  else if (action === 'close-modal') closeModal();
  else if (action.startsWith('configure:')) showConfigure(action.split(':')[1]);
  else if (action === 'save-config') await saveConfig(target);
  else if (action === 'discover-qq-key') await discoverQQKey();
  else if (action === 'qq-send-readiness') await checkQQSendReadiness();
  else if (action === 'wechat-send-readiness') await checkWeChatSendReadiness();
  else if (action.startsWith('resolve-media:')) {const encoded = action.slice('resolve-media:'.length);const split = encoded.lastIndexOf(':');const resourceIndex = split < 0 ? null : Number(encoded.slice(split + 1));const id = decodeURIComponent(split < 0 ? encoded : encoded.slice(0, split));const message = state.messages.find(m => m.id === id);if (message) await resolveMedia(message, Number.isInteger(resourceIndex) ? resourceIndex : null);}
  else if (action === 'page-prev' && state.page > 0) {state.page--;await loadView();}
  else if (action === 'page-next' && (usesCursorPaging() ? !!state.pageCursors[state.page + 1] : (state.page + 1) * 60 < state.total)) {state.page++;await loadView();}
  else if (action === 'toggle-sync') await saveSettings({auto_sync: state.settings.auto_sync === false});
  else if (action === 'account-lock-now') await lockAccountNow();
  else if (action === 'remove-wecom-bot-credentials') await removeWeComBotCredentials();
  else if (action === 'toggle-mcp-writes') await saveSettings({mcp_write_enabled: state.settings.mcp_write_enabled !== true});
  else if (action === 'notification-allow' && state.selected) await toggleConversationAllowlist(state.selected);
  else if (action === 'notification-mute' && state.selected) await toggleConversationNotifications(state.selected);
  else if (action.startsWith('notification-platform:')) await toggleNotificationPlatform(action.slice('notification-platform:'.length), target.checked);
  else if (action === 'notification-test') sendTestNotification();
  else if (action === 'capability:notifications') {await toggleNotificationCapability(state.settings.capabilities?.notifications?.enabled !== true);if (state.view === 'settings') await loadView();}
  else if (action.startsWith('capability:')) {const id = action.slice('capability:'.length);await saveSettings({capabilities: {[id]: !state.settings.capabilities?.[id]?.enabled}});renderShell();if (state.view === 'settings') await loadView();}
  else if (action === 'auto-export-run') await autoExportAction('run');
  else if (action === 'auto-export-cancel') await autoExportAction('cancel');
  else if (action === 'resource-select-page') {
    const entries = mediaEntries();
    const selectable = entries.filter(mediaEntryAvailable);
    const allSelected = selectable.length > 0 && selectable.every(entry => state.selectedMedia.has(mediaEntryKey(entry)));
    if (allSelected) selectable.forEach(entry => state.selectedMedia.delete(mediaEntryKey(entry)));
    else {
      for (const entry of selectable) {
        const key = mediaEntryKey(entry);
        if (!state.selectedMedia.has(key) && state.selectedMedia.size >= 100) {toast('批量导出最多选择 100 项', true);break;}
    if (mediaEntryAvailable(entry)) state.selectedMedia.set(key, entry);
      }
    }
    refreshResourceToolbar();
  }
  else if (action === 'resource-export') await startResourceExport();
  else if (action === 'resource-export-cancel') await cancelResourceExport();
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
document.addEventListener('change', event => {
  if (event.target.matches('#send-compose-form [name="media_type"]')) {
    const form = event.target.form, isMedia = event.target.value !== 'markdown';
    const fileLabel = form.querySelector('[data-media-file-label]'), file = form.elements.media_file;
    const textLabel = form.querySelector('[data-send-text-label]'), text = textLabel.querySelector('textarea');
    fileLabel.hidden = !isMedia;file.disabled = !isMedia;file.required = isMedia;
    if (isMedia) text.value = '';
    text.disabled = isMedia;text.required = !isMedia;textLabel.hidden = isMedia;
    file.accept = ({image: '.jpg,.jpeg,.png,.gif,.webp', file: '.pdf,.txt,.csv,.md,.log,.zip,.docx,.xlsx,.pptx', voice: '.amr', video: '.mp4,.m4v,.mov,.webm'})[event.target.value] || '';
    return;
  }
  if (event.target.id === 'report-year-scope') {
    state.reportYearScope = event.target.value === 'all' ? 'all' : 'year';
    const year = document.querySelector('#report-year');
    const label = document.querySelector('label[for="report-year"]');
    if (year) {year.hidden = state.reportYearScope === 'all';year.disabled = state.reportYearScope === 'all';}
    if (label) label.hidden = state.reportYearScope === 'all';
    return;
  }
  if (event.target.id === 'pair-report-scope') {
    const all = event.target.value === 'all';
    const year = document.querySelector('#pair-report-year');
    const label = document.querySelector('#pair-report-year-label');
    if (year) {year.hidden = all;year.disabled = all;}
    if (label) label.hidden = all;
    return;
  }
  if (event.target.matches('#media-image-tier')) {
    state.imageExportTier = ['best', 'mid', 'thumb', 'original'].includes(event.target.value) ? event.target.value : 'best';
    return;
  }
  const checkbox = event.target.closest('[data-media-select]');
  if (!checkbox) return;
  const entry = mediaEntries()[Number(checkbox.dataset.mediaSelect)];
  if (!entry || !mediaEntryAvailable(entry)) return;
  const key = mediaEntryKey(entry);
  if (checkbox.checked && state.selectedMedia.size >= 100) {checkbox.checked = false;toast('批量导出最多选择 100 项', true);return;}
  if (checkbox.checked) state.selectedMedia.set(key, entry); else state.selectedMedia.delete(key);
  refreshResourceToolbar();
});
document.addEventListener('error', event => {
  if (!(event.target instanceof HTMLImageElement)) return;
  const image = event.target;
  if (image.closest('.avatar')) image.remove();
  else image.replaceWith(document.createTextNode('图片暂不可用'));
}, true);
document.addEventListener('submit', async event => {
  event.preventDefault();
  const form = event.target;
  if (form.id === 'campus-updates-form') {await saveCampusUpdates(form);return;}
  if (form.id === 'campus-search') {Object.assign(state.campus.filters, Object.fromEntries(new FormData(form)));state.campus.offset = 0;await loadView();return;}
  if (form.id === 'campus-profile-form') {await saveCampusProfile(form);return;}
  if (form.id === 'campus-wechat-form') {await importCampusWechat(form);return;}
  if (form.id === 'campus-wechat-accounts-form') {await importCampusWechatAccounts(form);return;}
  if (form.id === 'pair-report-range-form') {
    const values = Object.fromEntries(new FormData(form));
    const report = state.pairReport;
    if (!report) return;
    const year = values.scope === 'year' ? Number(values.year) : null;
    if (values.scope === 'year' && (!Number.isInteger(year) || year < 1970 || year > 9999)) {toast('年份需在 1970 至 9999 之间', true);return;}
    await showPairReport({platform: report.platform, account_id: report.account_id,
      id: report.conversation_id, title: report.conversation_title, kind: 'private'}, year);
  }
  else if (form.id === 'send-compose-form') await prepareSend(form);
  else if (form.id === 'wecom-bot-credentials-form') await saveWeComBotCredentials(form);
  else if (form.id === 'auto-export-form') await saveAutoExport(form);
  else if (form.id === 'account-lock-setup') {
    const values = Object.fromEntries(new FormData(form));
    if (values.passphrase !== values.confirmation) {toast('两次口令不一致', true);return;}
    try {await api('/api/account-safety/setup', null, {method: 'POST', body: JSON.stringify({passphrase: values.passphrase})});location.reload();}
    catch (error) {toast(error.message, true);}
  }
  else if (form.id === 'account-lock-disable') await disableAccountLock(form);
  if (form.id === 'conversation-search') {state.cq = new FormData(form).get('q');state.selected = null;await loadView();}
  else if (form.id === 'conversation-metadata-form') await saveConversationMetadata(form);
  else if (form.id === 'contact-identity-form' && state.managedContact) {
    try {await api('/api/contact-identities', null, {method: 'POST', body: JSON.stringify({platform: state.managedContact.platform, account_id: state.managedContact.account_id, contact_id: state.managedContact.id, identity_id: new FormData(form).get('identity_id')})});closeModal();toast('统一身份已关联');await loadView();}
    catch (e) {toast(e.message, true);}
  }
  else if (form.id === 'listing-search') {clearMediaSelection();state.filters = {...state.filters, ...Object.fromEntries(new FormData(form))};if (state.filters.source === 'wechat-source') {state.filters.q = '';if (selectedAccountScope()?.platform !== 'wechat') {state.filters.source = 'chat';toast('微信源媒体需要先选择一个微信账号', true);}if (!['', 'image', 'sticker', 'video', 'voice', 'file'].includes(state.filters.type)) state.filters.type = '';}state.page = 0;state.pageCursors = [null];await loadView();}
  else if (form.id === 'moments-search') {state.momentFilter = Object.fromEntries(new FormData(form));state.momentOffset = 0;await loadView();}
  else if (form.id === 'message-search') {
    const request = ++state.request;
    state.messageFilter = {...state.messageFilter, ...Object.fromEntries(new FormData(form))};
    state.messageOffset = 0;state.messageCursor = null;state.messageHasMore = false;
    state.loading = true;state.error = '';renderView();
    try {await loadMessages(false, request);}
    catch (e) {if (request === state.request) state.error = e.message;}
    finally {if (request === state.request) {state.loading = false;renderView();}}
  }
});
document.addEventListener('change', async event => {
  if (event.target.id === 'moments-account') {
    state.momentFilter = {...state.momentFilter, account_id: event.target.value};
    state.momentOffset = 0;
    await loadView();
  }
  if (event.target.id === 'moments-export-format') state.momentExportFormat = event.target.value;
  if (event.target.id === 'sync-interval') await saveSettings({sync_interval: Number(event.target.value)});
  if (event.target.id === 'notification-mode') await saveSettings({notification_filters: {...notificationFilters(), mode: event.target.value}});
  if (event.target.id === 'notification-style') await saveSettings({notification_filters: {...notificationFilters(), style: event.target.value}});
  if (event.target.id === 'import-file') {state.importFile = event.target.files?.[0] || null;state.importPreview = null;renderView();}
  if (event.target.id === 'import-platform') {state.importPlatform = event.target.value;state.importPreview = null;renderView();}
  if (event.target.id === 'import-account') {state.importAccount = event.target.value;state.importPreview = null;const commit = document.querySelector('[data-action="commit-import"]');if (commit) commit.disabled = true;}
  if (event.target.closest('#message-search') && event.target.name === 'type') event.target.form.requestSubmit();
});
document.addEventListener('input', event => {
  if (event.target.id === 'import-account') {state.importAccount = event.target.value;state.importPreview = null;const commit = document.querySelector('[data-action="commit-import"]');if (commit) commit.disabled = true;}
  if (event.target.id === 'conversation-roster-search') {
    const results = document.querySelector('#conversation-roster-results');
    if (results) results.innerHTML = renderConversationRoster(state.wechatRoster, state.selected?.platform || 'wechat', event.target.value);
  }
});
let conversationTimer;
let conversationSearchSequence = 0;
document.addEventListener('input', event => {
  if (event.target.closest('#conversation-search')) {
    clearTimeout(conversationTimer);
    const input = event.target, platform = state.platform, request = state.request;
    const sequence = ++conversationSearchSequence;
    const value = input.value;state.cq = value;
    conversationTimer = setTimeout(async () => {
      if (state.view !== 'chat' || platform !== state.platform || request !== state.request) return;
      const result = await api('/api/conversations', {platform, q: value, limit: 300}).catch(() => null);
      if (!result || sequence !== conversationSearchSequence || value !== state.cq || platform !== state.platform || request !== state.request || state.view !== 'chat') return;
      state.conversations = result.items;state.conversationTotal = result.total;
      const list = document.querySelector('.conversation-list');
      if (list) list.innerHTML = result.items.map(renderConversation).join('') || empty('没有匹配的会话');
      icons();
    }, 250);
  }
});
document.addEventListener('error', event => {
  if (event.target?.matches?.('img.avatar-image')) event.target.remove();
}, true);
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
  const notificationToken = /^#notification=([A-Za-z0-9_-]{32})$/.exec(location.hash)?.[1] || '';
  if (notificationToken) history.replaceState(null, '', location.pathname + location.search + '#chat');
  const initialView = location.hash.slice(1);
  const section = campusRoute(location.hash);
  state.view = section ? 'campus' : initialView === 'qq-login' ? 'sources' : Object.hasOwn(views, initialView) ? initialView : 'chat';
  if (section) state.campus.section = section;
  setTheme(state.theme);renderShell();
  try {await Promise.all([refreshSources(false), api('/api/settings').then(settings => {state.settings = settings;})]);}
  catch (e) {state.error = e.message;}
  syncAccountLockPolling();
  if (state.settings.account_lock_enabled) noteAccountActivity();
  renderShell();await loadView();syncNotificationStream();
  if (notificationToken) {
    try {await openNotificationTarget(notificationToken);}
    catch (error) {toast(error.message || '通知会话已失效', true);}
  }
  let previousCount = state.sources.reduce((a, s) => a + (s.message_count || 0), 0);
  sourcePollTimer = setInterval(async () => {
    if (sourcePollBusy || document.hidden) return;
    sourcePollBusy = true;
    try {
      await refreshSources();
      if (state.view === 'imports' && state.importJobs.some(job => ['queued', 'running'].includes(job.state))) await refreshImportJobs();
      const count = state.sources.reduce((a, s) => a + (s.message_count || 0), 0);
      if (count !== previousCount && !state.busy && !document.querySelector('.dialog')) {
        previousCount = count;
        if (state.view !== 'settings' && state.view !== 'sources') await loadView();
      }
    } catch { /* A transient polling failure does not discard visible records. */ }
    finally { sourcePollBusy = false; }
  }, 5000);
}
init();
window.addEventListener('pagehide', () => {
  clearInterval(sourcePollTimer);sourcePollTimer = null;
  clearInterval(accountLockPollTimer);accountLockPollTimer = null;
  notificationStream?.close();notificationStream = null;
  if (state.settings.account_lock_enabled) document.querySelector('#app')?.replaceChildren();
});
window.addEventListener('pageshow', event => {if (event.persisted) location.reload();});
window.addEventListener('hashchange', async () => {
  const section = campusRoute(location.hash);
  if (section) {await navigateCampus(section, false);return;}
  const view = location.hash.slice(1);
  if (Object.hasOwn(views, view) && state.view !== view) {state.view = view;closeModal();renderShell();await loadView();}
});
let lastActivityPing = 0;
let accountIdleTimer;
let accountLockPollTimer = null;
document.addEventListener('pointerdown', noteAccountActivity, {passive: true});
document.addEventListener('keydown', noteAccountActivity);
document.addEventListener('touchstart', noteAccountActivity, {passive: true});
document.addEventListener('wheel', noteAccountActivity, {passive: true});
document.addEventListener('visibilitychange', checkAccountLockOnResume);
function syncAccountLockPolling() {
  if (state.settings.account_lock_enabled !== true) {
    clearInterval(accountLockPollTimer);accountLockPollTimer = null;return;
  }
  if (!accountLockPollTimer) accountLockPollTimer = setInterval(checkAccountLockStatus, 2000);
}
async function checkAccountLockStatus() {
  if (state.settings.account_lock_enabled !== true || accountLockTransition) return;
  try {
    const status = await api('/api/account-safety/status');
    if (status.locked) returnToLockScreen();
  } catch { /* A transient status failure is not evidence that another tab locked the account. */ }
}
async function checkAccountLockOnResume() {
  if (document.visibilityState !== 'visible' || state.settings.account_lock_enabled !== true ||
      accountLockTransition) return;
  await checkAccountLockStatus();
}
function noteAccountActivity() {
  if (state.settings.account_lock_enabled !== true) return;
  clearTimeout(accountIdleTimer);
  accountIdleTimer = setTimeout(returnToLockScreen,
    Number(state.settings.account_lock_idle_timeout_seconds || 900) * 1000);
  pingAccountActivity();
}
function pingAccountActivity() {
  if (state.settings.account_lock_enabled !== true) return;
  const now = Date.now();
  if (now - lastActivityPing < 60_000) return;
  lastActivityPing = now;
  api('/api/account-safety/activity', null, {method: 'POST', body: '{}'}).catch(error => {
    if (error.message === '应用已锁定') returnToLockScreen();
  });
}
