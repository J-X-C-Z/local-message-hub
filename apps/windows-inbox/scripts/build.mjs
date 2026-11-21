import {build} from 'esbuild';
import {mkdirSync,cpSync,readFileSync,writeFileSync,rmSync} from 'node:fs';
import path from 'node:path';

const root=process.cwd();const output=path.join(root,'build');mkdirSync(output,{recursive:true});
await build({entryPoints:['backend/service.mjs'],bundle:true,platform:'node',target:'node24',format:'cjs',outfile:'build/service.cjs',banner:{js:"const __bundleUrl = require('node:url').pathToFileURL(__filename).href;"},define:{'import.meta.url':'__bundleUrl'},logLevel:'warning'});
cpSync('backend/wecom-workbench/dist','build/dist',{recursive:true});
await build({entryPoints:['backend/campus.mjs'],bundle:true,platform:'node',target:'node24',format:'cjs',outfile:'build/campus.cjs',logLevel:'warning'});
// 打包环境不使用 .env / PowerShell 设置流程，配置由桌面进程注入。
let ui=readFileSync('build/dist/integration-ui.js','utf8');
ui=ui.replace('本机运行 <code>配置真实接入.ps1</code>，再运行 <code>启动真实接入.cmd</code>。应用详情和客户群查询才需要 Secret。','点击窗口上方“连接设置”或菜单“应用 → 连接设置”填写。应用详情和客户群查询才需要 Secret。');
writeFileSync('build/dist/integration-ui.js',ui);
let html=readFileSync('build/dist/index.html','utf8').replace('<title>企微 · 消息工作台</title>','<title>企微收件台 · 只接收消息</title>');
html=html.replace('在已有 MCP 服务中配置企业微信回调，启动回调接收服务。','在应用“连接设置”填写企业回调参数，保存后自动启动接收器。').replace('本机启动工作台：<code>node server.mjs</code>，通过环境变量 <code>WECOM_DB_PATH</code> 指定同一个消息数据库。','在企业微信管理后台验证可访问的 HTTPS 回调地址。').replace('打开终端显示的本地地址，选择“企业微信应用回调”。工作台通过长连接接收新消息。','保持本应用运行，新消息通过长连接自动呈现。');
writeFileSync('build/dist/index.html',html);
// 移除远程字体，使桌面界面无外部字体依赖。
let css=readFileSync('build/dist/style.css','utf8').replace(/^@import[^\r\n]*[\r\n]+/,'');writeFileSync('build/dist/style.css',css);
console.log('桌面接收服务与静态资源已构建。');
