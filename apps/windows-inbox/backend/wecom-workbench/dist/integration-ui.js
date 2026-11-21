const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' }[c]));
async function api(url) {
  const response = await fetch(url, { cache: 'no-store', signal: AbortSignal.timeout(30000) });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || '接口请求失败');
  return data;
}
export async function initializeIntegration({ status, switchView, useSnapshot, onDatabaseAvailable }) {
  if(status.source==='local-database')return initializeLocalGroups(switchView);
  const stylesheet = document.createElement('link'); stylesheet.rel = 'stylesheet'; stylesheet.href = './integration.css'; document.head.append(stylesheet);
  const nav = document.createElement('button'); nav.className = 'nav-button'; nav.dataset.view = 'groups'; nav.setAttribute('aria-label', '真实群组'); nav.innerHTML = '<span aria-hidden="true">♧</span><span class="rail-label">群组</span>';
  document.querySelector('.rail-nav').append(nav);
  const panel = document.createElement('main'); panel.id = 'groups-view'; panel.className = 'alternate-view'; panel.hidden = true;
  panel.innerHTML = `<div class="page-heading"><div><span class="eyebrow">LIVE GROUPS</span><h1>真实群组</h1><p>读取授权范围内的客户群，或查询已配置的应用群。</p></div><button class="source-button" id="group-refresh">刷新群组</button></div><div class="live-scope-note">群资料与群消息使用不同接口。客户群和应用群可查询资料；群聊天记录需要另行接入会话内容存档。</div><div class="group-controls"><label>群组类型<select id="group-type"><option value="customer">客户群</option><option value="app">应用创建的群</option></select></label><label id="owner-field">群主 userid（可选）<input id="group-owner" placeholder="不填则按当前权限查询"></label></div><div id="group-error" class="live-error" role="status" hidden></div><div id="live-group-cards" class="contact-cards"></div><button id="groups-more" class="source-button" hidden>加载下一页</button><div id="group-detail" class="live-group-detail" hidden></div>`;
  document.querySelector('.main-app').append(panel);
  if (status.snapshot_available) {
    const option = document.createElement('option'); option.value = 'snapshot'; option.textContent = '客户端已读取的内部群'; document.getElementById('group-type').prepend(option); document.getElementById('group-type').value = 'snapshot'; document.getElementById('owner-field').hidden = true;
    const action = document.createElement('button'); action.className = 'source-button'; action.textContent = '查看客户端快照';
    action.onclick = async () => { await useSnapshot(); document.getElementById('connection-dialog').close(); switchView('messages'); };
    document.querySelector('.dialog-content').prepend(action);
  }
  const setup = document.createElement('section'); setup.className = 'local-connection-checklist';
  document.querySelector('.dialog-content').prepend(setup);
  let currentStatus = status;
  function renderStatus() {
    const checklist = currentStatus.checklist || {};
    setup.innerHTML = `<strong>接收通道配置</strong><div class="live-checks">${[['企业 ID',checklist.corp_id],['应用 AgentId',checklist.agent_id],['回调 Token',checklist.callback_token],['回调 AES Key',checklist.callback_aes_key],['消息数据库',currentStatus.live_available]].map(([name, ok]) => `<span class="${ok ? 'ready' : 'missing'}">${ok ? '✓' : '○'} ${name}</span>`).join('')}</div><p>只接收回调无需应用 Secret。配置 CorpID、AgentId、Token、AES Key，并在企业微信后台验证公网 HTTPS 回调。日常私聊和内部群消息需会话内容存档，尚未接入。</p><p>本机运行 <code>配置真实接入.ps1</code>，再运行 <code>启动真实接入.cmd</code>。应用详情和客户群查询才需要 Secret。</p><button class="source-button" id="verify-live-api">验证资料查询权限（可选）</button><p id="verify-result" role="status"></p>`;
    document.querySelector('#verify-live-api').onclick = async event => {
      event.target.disabled = true;
      try { const result = await api('/api/verify'); document.querySelector('#verify-result').textContent = `连接成功：${result.name}（AgentId ${result.agent_id}）`; }
      catch (error) { document.querySelector('#verify-result').textContent = error.message; }
      finally { event.target.disabled = false; }
    };
    document.querySelector('#live-option-text').textContent = currentStatus.live_available ? '已检测到消息数据库，可读取真实应用回调。' : '尚未收到消息：请完成配置及管理后台 HTTPS 回调验证。';
  }
  renderStatus();
  let nextCursor = ''; let loading = false; let loaded = false;
  const get = id => document.getElementById(id);
  const type = () => get('group-type').value;
  function showError(message) { get('group-error').textContent = message; get('group-error').hidden = !message; }
  async function loadGroups(append = false) {
    if (loading) return; loading = true; get('group-refresh').disabled = true; get('groups-more').disabled = true;
    const requestedType = type();
    showError(''); get('group-detail').hidden = true;
    if (!append) { get('live-group-cards').replaceChildren(); nextCursor = ''; }
    try {
      if (requestedType === 'snapshot') {
        const snapshot = await api('/api/client-snapshot'); loaded = true;
        const groups = snapshot.conversations.filter(c => c.type === 'group');
        for (const group of groups) {
          const card = document.createElement('button'); card.className = 'contact-card';
          card.innerHTML = `<div class="avatar group">群</div><h3>${escape(group.name)}</h3><p>${group.memberCount ?? '未知'} 位成员 · ${group.messages.length} 条可见内容</p><div class="contact-footer"><span>真实内部群 · 客户端快照</span><span>→</span></div>`;
          card.onclick = () => {
            const detail = get('group-detail'); detail.hidden = false;
            detail.innerHTML = `<h2>${escape(group.name)}</h2><p>${escape(group.description)}</p><div class="live-scope-note">客户端读取于 ${escape(new Date(snapshot.captured_at).toLocaleString('zh-CN'))}。非实时数据；${escape(snapshot.status_note)}。完整群成员未采集。</div>${group.messages.map(m => `<div class="live-group-detail"><strong>${escape(m.sender)}</strong><p>${escape(m.time_label || '原时间未显示')}</p><p>${escape(m.text || m.file?.name || '')}</p></div>`).join('')}`;
            detail.scrollIntoView({ behavior:'smooth', block:'start' });
          };
          get('live-group-cards').append(card);
        }
        get('groups-more').hidden = true; return;
      }
      const query = new URLSearchParams({ type: requestedType, cursor: append ? nextCursor : '', owner: get('group-owner').value.trim() });
      const result = await api(`/api/groups?${query}`);
      if (requestedType !== type()) return;
      loaded = true; nextCursor = result.next_cursor || '';
      if (!append && !result.groups.length) get('live-group-cards').innerHTML = `<div class="empty-state"><strong>暂无可读取的${requestedType === 'customer' ? '客户群' : '应用群'}</strong><p>${requestedType === 'customer' ? '检查客户联系 Secret、可见范围与群主权限。' : '请在 .env 的 WECOM_APP_CHAT_IDS 中填写应用创建的群 chatid。'}</p></div>`;
      for (const group of result.groups) {
        const card = document.createElement('button'); card.className = 'contact-card';
        card.innerHTML = `<div class="avatar group">群</div><h3>${escape(group.name)}</h3><p>${escape(group.error || (group.owner ? `群主 ${group.owner}` : '点击查询群名称与成员详情'))}</p><div class="contact-footer"><span>${requestedType === 'customer' ? '客户群' : '应用群'} · ${group.member_count === undefined ? '查看详情' : `${group.member_count} 位成员`}</span><span>→</span></div>`;
        card.onclick = async () => {
          card.disabled = true;
          try {
            const detail = await api(`/api/group?${new URLSearchParams({ type: requestedType, id: group.id })}`);
            const el = get('group-detail'); el.hidden = false;
            el.innerHTML = `<h2>${escape(detail.name)}</h2><p>群 ID：${escape(detail.id)} · 群主：${escape(detail.owner || '未知')}</p>${detail.notice ? `<p>群公告：${escape(detail.notice)}</p>` : ''}<div class="live-scope-note">${escape(detail.message_note)}</div><h3>成员（${detail.members.length}）</h3><div class="live-member-list">${detail.members.map(member => `<div><strong>${escape(member.name)}</strong><span>${escape(member.userid)}</span></div>`).join('') || '<p>无可见成员</p>'}</div>`;
            card.querySelector('h3').textContent = detail.name;
            el.scrollIntoView({ behavior: 'smooth', block: 'start' });
          } catch (error) { showError(error.message); }
          finally { card.disabled = false; }
        };
        get('live-group-cards').append(card);
      }
      get('groups-more').hidden = !nextCursor;
    } catch (error) { showError(error.message); get('groups-more').hidden = true; }
    finally { loading = false; get('group-refresh').disabled = false; get('groups-more').disabled = false; }
  }
  nav.onclick = () => { switchView('groups'); if (!loaded) loadGroups(); };
  get('group-refresh').onclick = () => loadGroups();
  get('groups-more').onclick = () => loadGroups(true);
  get('group-type').onchange = () => { get('owner-field').hidden = type() !== 'customer'; loaded = false; loadGroups(); };
  setInterval(async () => {
    if (document.hidden) return;
    try {
      const latest = await api('/api/status');
      if (JSON.stringify(latest.checklist) !== JSON.stringify(currentStatus.checklist) || latest.live_available !== currentStatus.live_available) { currentStatus = latest; renderStatus(); }
      if (latest.live_available) await onDatabaseAvailable();
    } catch { /* 消息轮询单独展示连接状态 */ }
  }, 5000);
}

function initializeLocalGroups(switchView){
  const stylesheet=document.createElement('link');stylesheet.rel='stylesheet';stylesheet.href='./integration.css';document.head.append(stylesheet);
  const nav=document.createElement('button');nav.className='nav-button';nav.dataset.view='groups';nav.setAttribute('aria-label','群组');nav.innerHTML='<span aria-hidden="true">♧</span><span class="rail-label">群组</span>';document.querySelector('.rail-nav').append(nav);
  const panel=document.createElement('main');panel.id='groups-view';panel.className='alternate-view';panel.hidden=true;
  panel.innerHTML='<div class="page-heading"><div><span class="eyebrow">MY GROUPS</span><h1>我的群组</h1><p>从当前账号的本地消息缓存中读取，点击群组查看消息。</p></div><button class="source-button" id="local-group-refresh">刷新群组</button></div><div class="live-scope-note">这里列出本机缓存中有消息的群会话。未缓存的历史记录和完整群成员名单不在此范围。</div><div class="contact-cards" id="local-group-cards"></div><p id="local-group-error" role="status"></p>';
  document.querySelector('.main-app').append(panel);
  document.querySelector('.dialog-content').innerHTML='<section class="setup-guide"><strong>本机接收已接入</strong><p>保持企业微信登录。数据库文件变化时自动读取新增消息，按真实会话归集。</p><p>无需企业管理员、CorpID、AgentId 或回调配置。当前账号选择与密钥仅保存在这台电脑，密钥使用 Windows 当前用户加密。</p><p>显示本机缓存中的记录；图片支持缓存原图或缩略图显示，附件支持打开；语音暂以摘要显示。企业微信升级后可能需要适配新版本。</p><p>只接收与展示，不提供发送功能。</p></section>';
  const card=document.querySelector('.integration-card');card.querySelector('strong').textContent='本机接收已接入';card.querySelector('p').textContent='私聊和群消息自动更新，保持企业微信登录即可。';card.querySelector('button').firstChild.textContent='查看接收说明';
  document.querySelector('.connection-overview > p').textContent='接收范围：当前账号在这台电脑上的消息缓存。客户端写入后自动更新。';
  document.querySelector('.workspace-avatar').textContent='企';document.querySelector('.workspace-avatar').title='我的企业微信';
  document.getElementById('desktop-settings-button')?.remove();
  async function load(){
    try{const data=await api('/api/local-groups?platform='+(window.inboxPlatform||'wechat'));document.getElementById('local-group-cards').innerHTML=data.groups.map(group=>`<button class="contact-card" data-conversation="${escape('conversation:'+group.id)}"><div class="avatar group">群</div><h3>${escape(group.name)}</h3><p>${group.messages} 条已缓存消息</p><div class="contact-footer"><span>查看群消息</span><span>→</span></div></button>`).join('')||'<p>本机尚无已缓存的群消息。</p>';document.getElementById('local-group-error').textContent='';}
    catch{document.getElementById('local-group-error').textContent='群组读取失败，请稍后重试。';}
  }
  nav.onclick=()=>{switchView('groups');load();};document.getElementById('local-group-refresh').onclick=load;
}
