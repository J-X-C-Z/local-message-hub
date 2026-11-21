import {copyFileSync,cpSync,existsSync,mkdirSync,readdirSync} from 'node:fs';
import path from 'node:path';

const [binary,nodeDirectory,output='release-rust']=process.argv.slice(2);
if(!binary||!existsSync(binary))throw new Error('Pass the compiled Rust exe as the first argument.');
if(!nodeDirectory||!['node.exe','LICENSE'].every(file=>existsSync(path.join(nodeDirectory,file))))throw new Error('Pass the extracted official Node.js Windows x64 directory (node.exe and LICENSE) as the second argument.');
if(existsSync(output))throw new Error('Choose a new output directory so no existing runtime data is copied into the package.');
const buildRoot=path.join(path.dirname(binary),'build');
const loader=readdirSync(buildRoot).filter(name=>name.startsWith('webview2-com-sys-')).map(name=>path.join(buildRoot,name,'out','x64','WebView2Loader.dll')).find(existsSync);
if(!loader)throw new Error('Missing x64 WebView2Loader.dll in the Rust build output.');
await import('./build.mjs');
const resources=path.join(output,'resources');
mkdirSync(path.join(resources,'desktop'),{recursive:true});
cpSync(binary,path.join(output,'WecomInbox.exe'));
cpSync(loader,path.join(output,'WebView2Loader.dll'));
const dist=path.join(resources,'dist');
mkdirSync(dist);
copyFileSync(path.join('build','service.cjs'),path.join(resources,'service.cjs'));
for(const file of ['index.html','style.css','inbox-layout.css','integration.css','app.js','pane-resizer.js','view-performance.mjs','attachments-ui.js','integration-ui.js','rich-ui.js','emoji-map.js']){
 copyFileSync(path.join('build','dist',file),path.join(dist,file));
}
copyFileSync(path.join(nodeDirectory,'node.exe'),path.join(resources,'node.exe'));
copyFileSync(path.join(nodeDirectory,'LICENSE'),path.join(resources,'LICENSE-node.txt'));
for(const [name,files] of Object.entries({'local-reader':['WecomLocalReader.exe','LICENSE-wecom-agent.txt','LICENSE-Python.txt','LICENSE-PyCryptodome.txt','NOTICE.txt'],'wechat-reader':['WeChatLocalReader.exe','ffmpeg.exe','NOTICE.md','LICENSE-wechatauto.txt']})){
 const source=path.join('runtime',name);
 if(!existsSync(path.join(source,files[0])))continue;
 const target=path.join(resources,name);
 mkdirSync(target,{recursive:true});
 for(const file of files)if(existsSync(path.join(source,file)))copyFileSync(path.join(source,file),path.join(target,file));
}
for(const file of ['settings.html','settings.css','settings.js','mail.html','mail.css','mail-ui.js','campus.html','campus.css','campus-sources.css','campus-ui.js','aihot.html','aihot.css','aihot-ui.js']){
 cpSync(path.join('desktop',file),path.join(resources,'desktop',file));
}
console.log(path.resolve(output,'WecomInbox.exe'));
