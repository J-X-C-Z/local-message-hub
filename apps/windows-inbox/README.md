# Rust 版 Windows 收件台

这是消息中枢之外的独立 Windows x64 应用。Rust 管理原生窗口、WebView2 和本地服务；消息工作台及接收服务仍使用 JavaScript，微信和企业微信本地读取器仍使用 Python。点击「AI 热点」会在应用内打开完整 AIHot 官网，符合条件的 HTTPS 新闻链接留在同一窗口，不经过摘要页。

## 构建和测试

从本目录执行。需要 Windows 10/11 x64、WebView2 Runtime、Node.js 24（含 npm）和 Rust x64 工具链。使用 GNU 工具链时，请将 MinGW 加入 `PATH`；如工作路径含非 ASCII 字符，可把 `CARGO_TARGET_DIR` 设为纯英文路径。

```powershell
npm ci
npm test
cargo build --locked --release --manifest-path rust-desktop/Cargo.toml
$target = if ($env:CARGO_TARGET_DIR) { $env:CARGO_TARGET_DIR } else { 'rust-desktop/target' }
npm run package -- (Join-Path $target 'release/wecom-inbox.exe') 'C:\path\to\node-v24-win-x64'
./scripts/smoke-rust.ps1
```

第二个打包参数指向从 [Node.js 官网](https://nodejs.org/) 解压的官方 Windows x64 发行目录，应含 `node.exe` 和 `LICENSE`。从 `release-rust/WecomInbox.exe` 启动；请保留同目录的 `WebView2Loader.dll` 和 `resources`。为防止混入旧文件，再次打包应指定一个新的输出目录。冒烟测试使用合成消息和独立临时配置，并访问公开 AIHot 网页；不要求真实账号。

默认包不包含本机聊天读取器。需要本地读取时，从干净的源码构建两个读取器，再运行 `npm run package`：

```powershell
py -3.14 -m venv .venv
./.venv/Scripts/python.exe -m pip install -r requirements-readers.txt
./scripts/build-readers.ps1 -Python ./.venv/Scripts/python.exe
```

读取器位于 `runtime/local-reader/` 和 `runtime/wechat-reader/`，可为微信读取器另备可信的 FFmpeg；不需要复制微信数据到仓库。读取器及依赖另行分发时，须附上完整的第三方许可证和声明。第三方表情图片不随源码分发，缺图时界面显示原表情文字。

## 本地数据边界

默认使用 `%APPDATA%\企微收件台`，与仓库根目录的消息中枢分开。不要把消息数据库、配置、登录会话、缓存、导出或附件放进仓库。若已有同名收件台在运行，请退出后再使用此应用，避免并行写入。独立试用可用 `--profile` 指定仓库之外的新绝对目录。
