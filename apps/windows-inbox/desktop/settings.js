const form=document.getElementById('settings-form');
const result=document.getElementById('save-result');
function show(text,error=false){result.hidden=false;result.textContent=text;result.className=error?'error':'';}
function updateAddress(){document.getElementById('callback-url').textContent=`http://127.0.0.1:${form.elements.callbackPort.value || 8787}/wecom/callback`;}
document.getElementById('close-button').onclick=()=>window.close();
form.elements.callbackPort.oninput=updateAddress;
window.connectionSettings.load().then(data=>{
 for(const [key,value]of Object.entries(data.settings))if(form.elements[key])form.elements[key].value=value;
 document.getElementById('encryption-status').textContent=data.encryptionAvailable?'✓ 敏感连接参数使用 Windows 本机加密保存':'本机加密不可用，配置无法保存';
 if(!data.encryptionAvailable)document.getElementById('save-button').disabled=true;
 if(data.error)show(data.error,true);
 else if(data.generatedDraft)show('已自动生成 Token 和 EncodingAESKey，并加密保存为本机草稿。请从管理后台填写真实 CorpID、AgentId，并将相同回调参数配置到后台；当前尚未连接。');
 updateAddress();
}).catch(()=>show('读取本地配置失败。',true));
form.onsubmit=async event=>{
 event.preventDefault();const button=document.getElementById('save-button');button.disabled=true;show('正在保存本机配置并启动接收服务…');
 const value=Object.fromEntries(new FormData(form));value.callbackPort=Number(value.callbackPort);value.retentionDays=Number(value.retentionDays);value.callbackHost='127.0.0.1';
 try{const response=await window.connectionSettings.save(value);if(!response.ok){show(response.error,true);return;}
 show(response.receiverError?`配置已加密保存，但接收器未启动：${response.receiverError}`:'配置已加密保存，接收服务已启动。请在企业微信后台完成 HTTPS 回调验证；本地启动成功不代表已连接真实企业。',Boolean(response.receiverError));
 }catch{show('保存失败，请重新打开连接设置。',true);}finally{button.disabled=false;}
};
