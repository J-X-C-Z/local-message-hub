const $=id=>document.getElementById(id);let selected=null;let latest={records:[]};let fetching=false;
const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function render(state){latest=state;$('state').textContent=state.status;$('last').textContent=state.lastChecked?'上次检查 '+new Date(state.lastChecked).toLocaleString('zh-CN'):'尚无读取记录';$('toggle').textContent=state.enabled?'停止定时读取':'开始定时读取';$('read').disabled=state.busy;$('count').textContent=state.records.length;if(!selected||!state.records.some(r=>r.id===selected))selected=state.records[0]?.id;
 $('history').innerHTML=state.records.map(record=>`<button class="record ${record.id===selected?'active':''}" data-id="${record.id}"><strong>${escape(new Date(record.capturedAt).toLocaleString('zh-CN'))}</strong><span>${escape(record.text||'窗口图像 · 文字识别不可用')}</span></button>`).join('')||'<div class="empty">读取到的变化画面会出现在这里。保持企业微信主窗口打开。</div>';
 const record=state.records.find(r=>r.id===selected);if(record){$('text').textContent=record.text||'此记录未能识别文字，可查看最近窗口图像。';$('record-time').textContent=new Date(record.capturedAt).toLocaleString('zh-CN');}
 if(state.image)$('image').src=state.image;else $('image').removeAttribute('src');
}
async function refresh(){if(fetching)return;fetching=true;try{render(await window.windowReader.state());}catch{$('state').textContent='读取服务暂时不可用';}finally{fetching=false;}}
$('read').onclick=async()=>{try{$('read').disabled=true;render(await window.windowReader.read());}catch{$('state').textContent='读取失败';}};
$('toggle').onclick=async()=>{try{render(latest.enabled?await window.windowReader.stop():await window.windowReader.start(Number($('interval').value)));}catch{$('state').textContent='无法启动定时读取';}};
$('history').onclick=event=>{const button=event.target.closest('[data-id]');if(button){selected=button.dataset.id;render(latest);}};
document.querySelectorAll('[data-tab]').forEach(button=>button.onclick=()=>{document.querySelectorAll('[data-tab]').forEach(b=>b.classList.toggle('active',b===button));$('text').hidden=button.dataset.tab!=='text';$('image-panel').hidden=button.dataset.tab!=='image';});
$('official').onclick=()=>window.windowReader.openOfficial();refresh();setInterval(refresh,1500);
