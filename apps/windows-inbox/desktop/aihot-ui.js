const toolbar=document.getElementById('toolbar');
const host=document.getElementById('host');
const status=document.getElementById('status');
const error=document.getElementById('error');
const waiting=document.getElementById('waiting');

for(const button of document.querySelectorAll('[data-command]')){
 button.addEventListener('click',()=>window.aiHotReader.command(button.dataset.command));
}
window.aiHotReader.onState(state=>{
 toolbar.classList.toggle('loading',state.loading);
 host.textContent=state.host;
 host.title=state.host;
 status.textContent=state.message||(state.loading?'正在加载…':'');
 status.title=status.textContent;
 document.getElementById('back').disabled=!state.canBack;
 document.getElementById('forward').disabled=!state.canForward;
 error.hidden=!state.failed;
 waiting.hidden=!state.waiting;
 document.getElementById('error-detail').textContent=state.failed?state.message:'';
});
window.aiHotReader.command('ready');
