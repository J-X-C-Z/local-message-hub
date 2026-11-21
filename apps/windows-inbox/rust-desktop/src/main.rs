#![windows_subsystem = "windows"]
mod native;
mod server;

use base64::{Engine, engine::general_purpose::STANDARD};
use serde::Deserialize;
use serde_json::{Value, json};
use std::{
    collections::HashMap,
    fs,
    io::{self, BufRead, BufReader, Write},
    os::windows::process::CommandExt,
    path::{Path, PathBuf},
    process::{Child, ChildStdin, Command, Stdio},
    sync::{Arc, RwLock},
    thread,
    time::{Duration, Instant},
};
use tao::{
    dpi::LogicalSize,
    event::{Event, WindowEvent},
    event_loop::{ControlFlow, EventLoopBuilder, EventLoopProxy, EventLoopWindowTarget},
    platform::windows::WindowExtWindows,
    window::{Window, WindowBuilder},
};
use webview2_com::{
    Microsoft::Web::WebView2::Win32::*, PermissionRequestedEventHandler,
    WebResourceRequestedEventHandler,
};
use windows_core::{HSTRING, PWSTR};
use windows_sys::Win32::{
    Foundation::{ERROR_ALREADY_EXISTS, GetLastError, HWND},
    System::Threading::CreateMutexW,
};
use wry::{
    NewWindowResponse, PageLoadEvent, Rect, WebContext, WebView, WebViewBuilder, WebViewExtWindows,
    dpi::{LogicalPosition, PhysicalSize},
};

const HOME: &str = "https://aihot.news/";
const BRIDGE: &str = include_str!("bridge.js");
type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;

#[derive(Debug, Deserialize)]
struct Call {
    id: u64,
    action: String,
    #[serde(default)]
    args: Vec<Value>,
}
enum AppEvent {
    Call(&'static str, Call),
    Service(u64, Value),
    Ended(u64),
    Warm,
    NewsOpen(String),
    NewsPage(bool, String),
    SmokeStep,
    ShutdownDeadline,
}

struct ChildService {
    child: Child,
    input: ChildStdin,
}
impl ChildService {
    fn send(&mut self, value: Value) -> io::Result<()> {
        writeln!(self.input, "{value}")?;
        self.input.flush()
    }
}

fn random_key() -> Result<String> {
    let mut bytes = [0u8; 32];
    getrandom::fill(&mut bytes).map_err(|error| io::Error::other(error.to_string()))?;
    Ok(bytes.iter().map(|unit| format!("{unit:02x}")).collect())
}

fn is_https(value: &str) -> bool {
    url::Url::parse(value).is_ok_and(|address| {
        let Some(url::Host::Domain(host)) = address.host() else {
            return false;
        };
        address.scheme() == "https"
            && address.username().is_empty()
            && address.password().is_none()
            && address.port().is_none()
            && host.trim_end_matches('.').contains('.')
            && ![
                "localhost",
                "local",
                "lan",
                "internal",
                "home",
                "test",
                "invalid",
            ]
            .iter()
            .any(|suffix| host.trim_end_matches('.').rsplit('.').next() == Some(*suffix))
    })
}

fn attach_controls(view: &WebView, origin: &str, token: &str) -> Result<()> {
    let webview = view.webview();
    let filter = HSTRING::from(format!("{origin}/*"));
    let origin = origin.to_owned();
    let token = HSTRING::from(token);
    unsafe {
        webview.AddWebResourceRequestedFilter(&filter, COREWEBVIEW2_WEB_RESOURCE_CONTEXT_ALL)?;
        webview.add_WebResourceRequested(
            &WebResourceRequestedEventHandler::create(Box::new(move |_, args| {
                if let Some(args) = args {
                    let request = args.Request()?;
                    let mut address = PWSTR::null();
                    request.Uri(&mut address)?;
                    let value = webview2_com::take_pwstr(address);
                    if value
                        .strip_prefix(&origin)
                        .is_some_and(|rest| rest.starts_with('/'))
                    {
                        request
                            .Headers()?
                            .SetHeader(&HSTRING::from("X-Inbox-Desktop"), &token)?;
                    }
                }
                Ok(())
            })),
            &mut 0,
        )?;
    }
    Ok(())
}

fn deny_permissions(view: &WebView) -> Result<()> {
    unsafe {
        view.webview().add_PermissionRequested(
            &PermissionRequestedEventHandler::create(Box::new(|_, args| {
                if let Some(args) = args {
                    args.SetState(COREWEBVIEW2_PERMISSION_STATE_DENY)?;
                }
                Ok(())
            })),
            &mut 0,
        )?;
    }
    Ok(())
}

struct Surface {
    view: WebView,
    window: Window,
}
struct News {
    content: WebView,
    toolbar: WebView,
    window: Window,
    loading: bool,
    address: String,
}
struct State {
    views: HashMap<&'static str, Surface>,
    news: Option<News>,
    trusted: WebContext,
    web: WebContext,
    proxy: EventLoopProxy<AppEvent>,
    origin: String,
    token: String,
    workbench_key: String,
    service_origin: server::ServiceOrigin,
    resources: PathBuf,
    profile: PathBuf,
    service: Option<ChildService>,
    generation: u64,
    exiting: bool,
    restarting: bool,
    started: Instant,
    isolated: bool,
    smoke: bool,
    smoke_started: bool,
    smoke_news_link: bool,
    log_path: Option<PathBuf>,
}

impl State {
    fn log(&self, value: &str) {
        if let Some(file) = &self.log_path
            && let Ok(mut stream) = fs::OpenOptions::new().create(true).append(true).open(file)
        {
            let _ = writeln!(stream, "{} {value}", self.started.elapsed().as_millis());
        }
    }

    fn builder(&mut self, role: &'static str, page: &str) -> WebViewBuilder<'_> {
        let allowed = page.to_owned();
        let sender = page.to_owned();
        let proxy = self.proxy.clone();
        WebViewBuilder::new_with_web_context(&mut self.trusted)
            .with_devtools(false)
            .with_background_color((245, 248, 253, 255))
            .with_initialization_script_for_main_only(BRIDGE, true)
            .with_navigation_handler(move |address| address == allowed)
            .with_new_window_req_handler(|_, _| NewWindowResponse::Deny)
            .with_ipc_handler(move |request| {
                if request.uri().to_string() == sender
                    && request.body().len() <= 256 * 1024
                    && let Ok(call) = serde_json::from_str::<Call>(request.body())
                {
                    let _ = proxy.send_event(AppEvent::Call(role, call));
                }
            })
    }

    fn show(&mut self, role: &'static str, events: &EventLoopWindowTarget<AppEvent>) -> Result<()> {
        if let Some(surface) = self.views.get(role) {
            surface.window.set_visible(true);
            surface.window.set_minimized(false);
            surface.window.set_focus();
            return Ok(());
        }
        let (title, page, width, height): (&str, &str, f64, f64) = match role {
            "mail" => ("邮箱中心 · 企微收件台", "/desktop/mail.html", 1280., 850.),
            "campus" => ("校园资讯 · 企微收件台", "/desktop/campus.html", 1310., 880.),
            "settings" => (
                "接收设置 · 企微收件台",
                "/desktop/settings.html",
                740.,
                860.,
            ),
            _ => ("收件台", "/", 1450., 940.),
        };
        let window = WindowBuilder::new()
            .with_title(title)
            .with_inner_size(LogicalSize::new(width, height))
            .with_min_inner_size(LogicalSize::new(width.min(730.), height.min(560.)))
            .with_visible(false)
            .build(events)?;
        if role == "main" {
            window.set_visible(true);
            self.log("main-window");
        }
        let address = format!("{}{page}", self.origin);
        let view = self.builder(role, &address).build(&window)?;
        attach_controls(&view, &self.origin, &self.token)?;
        deny_permissions(&view)?;
        view.load_url(&address)?;
        window.set_visible(true);
        self.views.insert(role, Surface { view, window });
        match role {
            "mail" => self.log("mail-window"),
            "campus" => self.log("campus-window"),
            "settings" => self.log("settings-window"),
            _ => self.log("main-webview"),
        }
        Ok(())
    }

    fn news_bounds(window: &Window) -> (Rect, Rect) {
        let size = window.inner_size();
        let top = (62. * window.scale_factor()) as u32;
        (
            Rect {
                position: LogicalPosition::new(0, 0).into(),
                size: PhysicalSize::new(size.width, top).into(),
            },
            Rect {
                position: wry::dpi::PhysicalPosition::new(0, top as i32).into(),
                size: PhysicalSize::new(size.width, size.height.saturating_sub(top)).into(),
            },
        )
    }

    fn show_news(&mut self, events: &EventLoopWindowTarget<AppEvent>, visible: bool) -> Result<()> {
        if let Some(news) = &self.news {
            if visible {
                news.window.set_visible(true);
                news.window.set_minimized(false);
                news.window.set_focus();
                let _ = news.content.focus();
                self.log("aihot-shown");
            }
            return Ok(());
        }
        let window = WindowBuilder::new()
            .with_title("AI 热点 · 企微收件台")
            .with_inner_size(LogicalSize::new(1310., 880.))
            .with_min_inner_size(LogicalSize::new(740., 500.))
            .with_visible(false)
            .build(events)?;
        let (top, body) = Self::news_bounds(&window);
        let address = format!("{}/desktop/aihot.html", self.origin);
        let toolbar = self
            .builder("aihot", &address)
            .with_bounds(top)
            .build_as_child(&window)?;
        attach_controls(&toolbar, &self.origin, &self.token)?;
        let popup = self.proxy.clone();
        let progress = self.proxy.clone();
        let content = WebViewBuilder::new_with_web_context(&mut self.web)
            .with_bounds(body)
            .with_devtools(false)
            .with_background_color((249, 249, 246, 255))
            .with_navigation_handler(|address| is_https(&address))
            .with_new_window_req_handler(move |address, _| {
                if is_https(&address) {
                    let _ = popup.send_event(AppEvent::NewsOpen(address));
                }
                NewWindowResponse::Deny
            })
            .with_on_page_load_handler(move |phase, address| {
                let _ = progress.send_event(AppEvent::NewsPage(
                    matches!(phase, PageLoadEvent::Started),
                    address,
                ));
            })
            .build_as_child(&window)?;
        deny_permissions(&content)?;
        toolbar.load_url(&address)?;
        content.load_url(HOME)?;
        if visible {
            window.set_visible(true);
            self.log("aihot-shown");
        }
        self.news = Some(News {
            content,
            toolbar,
            window,
            loading: true,
            address: HOME.into(),
        });
        Ok(())
    }

    fn update_news(&self) {
        if let Some(news) = &self.news {
            let host = url::Url::parse(&news.address)
                .ok()
                .and_then(|value| value.host_str().map(str::to_owned))
                .unwrap_or_else(|| "aihot.news".into());
            let webview = news.content.webview();
            let (mut back, mut forward) = (false.into(), false.into());
            unsafe {
                let _ = webview.CanGoBack(&mut back);
                let _ = webview.CanGoForward(&mut forward);
            }
            let state = json!({"host":host,"loading":news.loading,"canBack":back.as_bool(),"canForward":forward.as_bool()});
            let _ = news
                .toolbar
                .evaluate_script(&format!("window.__inboxEvent?.('aihot-state',{state})"));
        }
    }

    fn start_service(&mut self) -> Result<()> {
        if self.service.is_some() {
            return Ok(());
        }
        self.generation += 1;
        let generation = self.generation;
        let node = self.resources.join("node.exe");
        let script = self.resources.join("service.cjs");
        let settings = self
            .read_vault("connection.bin")
            .ok()
            .flatten()
            .and_then(|value| serde_json::from_str::<Value>(&value).ok());
        let mut command = Command::new(node);
        command
            .arg(script)
            .env("WECOM_RUST_PIPE", "1")
            .env_remove("NODE_OPTIONS")
            .creation_flags(0x08000000)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null());
        let mut child = command.spawn()?;
        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| io::Error::other("Missing stdout"))?;
        let input = child
            .stdin
            .take()
            .ok_or_else(|| io::Error::other("Missing stdin"))?;
        let proxy = self.proxy.clone();
        thread::spawn(move || {
            for line in BufReader::new(stdout)
                .lines()
                .map_while(std::result::Result::ok)
            {
                if let Ok(value) = serde_json::from_str(&line) {
                    let _ = proxy.send_event(AppEvent::Service(generation, value));
                }
            }
            let _ = proxy.send_event(AppEvent::Ended(generation));
        });
        let mut service = ChildService { child, input };
        let local = self.resources.join("local-reader/WecomLocalReader.exe");
        let wechat = self.resources.join("wechat-reader/WeChatLocalReader.exe");
        service.send(json!({"type":"init","settings":settings,"dataDirectory":self.profile,"accessToken":self.workbench_key,
          "localReaderExecutable":if self.isolated {None}else{Some(local)},"wechatReaderExecutable":if self.isolated {None}else{Some(wechat)}}))?;
        self.service = Some(service);
        Ok(())
    }

    fn request_stop(&mut self) {
        if let Some(service) = &mut self.service {
            let _ = service.send(json!({"type":"shutdown"}));
        }
    }

    fn restart(&mut self) {
        if self.service.is_some() {
            self.restarting = true;
            self.request_stop();
        } else if self.start_service().is_err() {
            self.emit("main", "inbox-service-failed", Value::Null);
        }
    }

    fn handle_call(
        &mut self,
        role: &'static str,
        call: Call,
        events: &EventLoopWindowTarget<AppEvent>,
    ) {
        let action = call.action.as_str();
        if role == "main" {
            let result = match action {
                "show:mail" => Some(self.show("mail", events)),
                "show:campus" => Some(self.show("campus", events)),
                "show:settings" => Some(self.show("settings", events)),
                "show:aihot" => Some(self.show_news(events, true)),
                "main:ready" => {
                    self.log("main-dom");
                    None
                }
                _ => None,
            };
            if let Some(result) = result {
                if let Err(error) = result {
                    native::message(self.hwnd("main"), "企微收件台", &error.to_string(), false);
                }
                return;
            }
            if action == "main:ready" {
                return;
            }
            if self.smoke && action == "test:result" {
                self.log(
                    call.args
                        .first()
                        .and_then(Value::as_str)
                        .unwrap_or("test-missing"),
                );
                return;
            }
        }
        if role == "aihot" {
            if let Some(news) = &mut self.news {
                let webview = news.content.webview();
                match action {
                    "aihot:close" => news.window.set_visible(false),
                    "aihot:home" => {
                        if news.address != HOME {
                            let _ = news.content.load_url(HOME);
                        }
                    }
                    "aihot:back" => unsafe {
                        let _ = webview.GoBack();
                    },
                    "aihot:forward" => unsafe {
                        let _ = webview.GoForward();
                    },
                    "aihot:reload" => unsafe {
                        let _ = webview.Reload();
                    },
                    "aihot:ready" => self.update_news(),
                    _ => {}
                }
            }
            return;
        }
        if role == "settings" && action == "window:close" {
            self.views.remove("settings");
            return;
        }
        if role == "settings" && action == "service:restart" {
            self.restart();
            return;
        }
        let permitted = match role {
            "main" => matches!(
                action,
                "attachments:open"
                    | "attachments:save-as"
                    | "attachments:keep"
                    | "documents:open"
                    | "links:open"
            ),
            "settings" => matches!(
                action,
                "settings:load"
                    | "settings:save"
                    | "settings:select-wechat"
                    | "settings:attachment-mode"
                    | "settings:set-attachment-mode"
            ),
            "mail" => action.starts_with("mail:"),
            "campus" => action.starts_with("campus:"),
            _ => false,
        };
        if !permitted || call.id == 0 {
            return;
        }
        let sent=self.service.as_mut().is_some_and(|service|service.send(json!({"type":"call","role":role,"id":call.id,"action":action,"args":call.args})).is_ok());
        if !sent {
            self.reply(
                role,
                call.id,
                Value::Null,
                "本机接收服务尚未就绪，请稍后重试。",
            );
        }
    }

    fn hwnd(&self, role: &str) -> HWND {
        self.views
            .get(role)
            .or_else(|| self.views.get("main"))
            .map_or(std::ptr::null_mut(), |surface| {
                surface.window.hwnd() as HWND
            })
    }
    fn emit(&self, role: &str, event: &str, data: Value) {
        if let Some(surface) = self.views.get(role) {
            let _ = surface
                .view
                .evaluate_script(&format!("window.__inboxEvent?.({},{data})", json!(event)));
        }
    }
    fn reply(&self, role: &str, id: u64, value: Value, error: &str) {
        if let Some(surface) = self.views.get(role) {
            let _ = surface.view.evaluate_script(&format!(
                "window.__inboxReply?.({id},{value},{})",
                json!(error)
            ));
        }
    }
    fn vault(&self, filename: &str) -> io::Result<PathBuf> {
        if !matches!(
            filename,
            "connection.bin" | "mail-accounts.bin" | "callback-draft.bin"
        ) {
            return Err(io::Error::other("配置文件无效"));
        }
        Ok(self.profile.join(filename))
    }
    fn read_vault(&self, filename: &str) -> io::Result<Option<String>> {
        match fs::read(self.vault(filename)?) {
            Ok(bytes) => native::decrypt(&bytes).map(Some),
            Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(None),
            Err(error) => Err(error),
        }
    }
    fn native_call(&mut self, action: &str, args: &Value) -> Result<Value> {
        let arg = |index: usize| args.get(index).and_then(Value::as_str).unwrap_or_default();
        let text = arg(0);
        let hwnd = self.hwnd(if action == "confirm" || text == "json" {
            "mail"
        } else {
            "main"
        });
        Ok(match action {
            "vault-read" => json!(self.read_vault(text)?),
            "vault-write" => {
                let value = arg(1);
                serde_json::from_str::<Value>(value)?;
                native::write_vault(&self.vault(text)?, value)?;
                Value::Null
            }
            "decrypt" => json!(native::decrypt(&STANDARD.decode(text)?)?),
            "url" => {
                let address = url::Url::parse(text)?;
                if !matches!(address.scheme(), "http" | "https")
                    || address.host().is_none()
                    || !address.username().is_empty()
                    || address.password().is_some()
                {
                    return Err(io::Error::other("网页地址无效").into());
                }
                native::shell(hwnd, address.as_str(), false)?;
                Value::Null
            }
            "path" => {
                let file = Path::new(text);
                if !file.is_absolute() || !file.is_file() {
                    return Ok(json!("文件不存在"));
                }
                match native::shell(hwnd, file, false) {
                    Ok(()) => json!(""),
                    Err(error) => json!(error.to_string()),
                }
            }
            "reveal" => {
                let file = Path::new(text);
                if file.is_absolute() && file.is_file() {
                    native::shell(hwnd, file, true)?;
                }
                Value::Null
            }
            "pick" => match native::pick(hwnd, text, "") {
                Some(file) => json!({"canceled":false,"filePaths":[file]}),
                None => json!({"canceled":true,"filePaths":[]}),
            },
            "save" => match native::pick(hwnd, "save", text) {
                Some(file) => json!({"canceled":false,"filePath":file}),
                None => json!({"canceled":true}),
            },
            "confirm" => {
                json!({"response":if native::message(hwnd,"确认发送邮件",&format!("{text}\n\n{}",arg(1)),true){1}else{0}})
            }
            "clipboard" => {
                native::clipboard(hwnd, text)?;
                json!(true)
            }
            "hide" => {
                if let Some(surface) = self.views.get(text) {
                    surface.window.set_visible(false);
                }
                Value::Null
            }
            "platform" => {
                self.emit("main", "inbox-platform", json!(text));
                if let Some(surface) = self.views.get("main") {
                    surface.window.set_focus();
                }
                Value::Null
            }
            _ => return Err(io::Error::other("不支持的系统操作").into()),
        })
    }
    fn on_service(&mut self, data: Value) {
        let text = |key| data.get(key).and_then(Value::as_str).unwrap_or_default();
        match text("type") {
            "ready" => {
                let value = text("url");
                if let Some(port) = value
                    .strip_prefix("http://127.0.0.1:")
                    .and_then(|port| port.parse::<u16>().ok())
                {
                    if let Ok(mut service) = self.service_origin.write() {
                        *service = Some(format!("http://127.0.0.1:{port}"));
                    }
                    self.log("service-ready");
                    if let Some(main) = self.views.get("main") {
                        let _ = main
                            .view
                            .evaluate_script("document.getElementById('refresh-button')?.click()");
                    }
                    if self.smoke && !self.smoke_started {
                        self.smoke_started = true;
                        let proxy = self.proxy.clone();
                        thread::spawn(move || {
                            thread::sleep(Duration::from_millis(700));
                            let _ = proxy.send_event(AppEvent::SmokeStep);
                        });
                    }
                }
            }
            "reply" => {
                let value = data.get("value").cloned().unwrap_or_default();
                let id = data.get("id").and_then(Value::as_u64).unwrap_or_default();
                self.reply(text("role"), id, value.clone(), text("error"));
                if self.smoke && text("error").is_empty() {
                    match text("role") {
                        "settings"
                            if value.get("encryptionAvailable").and_then(Value::as_bool)
                                == Some(true) =>
                        {
                            self.log("test-settings")
                        }
                        "mail" if value["ok"] == true => self.log("test-mail"),
                        "campus" if value["ok"] == true => self.log("test-campus"),
                        _ => {}
                    }
                }
                if value.get("restart") == Some(&Value::Bool(true)) {
                    self.restart();
                }
            }
            "event" => self.emit(text("role"), text("event"), Value::Null),
            "native" => {
                let result = self.native_call(text("action"), &data["args"]);
                let response = match result {
                    Ok(value) => json!({"type":"native-result","id":data["id"],"value":value}),
                    Err(error) => {
                        json!({"type":"native-result","id":data["id"],"error":error.to_string()})
                    }
                };
                if let Some(service) = &mut self.service {
                    let _ = service.send(response);
                }
            }
            "fatal" => {
                self.log("service-failed");
                self.emit("main", "inbox-service-failed", Value::Null);
            }
            _ => {}
        }
    }

    fn smoke_step(&mut self, events: &EventLoopWindowTarget<AppEvent>) {
        if !self.smoke {
            return;
        }
        if let Some(main) = self.views.get("main") {
            let _=main.view.evaluate_script(r#"(async()=>{
                const report=label=>window.ipc.postMessage(JSON.stringify({id:0,action:'test:result',args:[label]}));
                try{
                 const response=await fetch('/api/inbox-preview');const preview=await response.json();
                 if(!response.ok||!preview.events.some(item=>item.message.Content==='Rust smoke message'))throw Error('preview');
                 report('test-preview');
                 for(let attempt=0;attempt<40;attempt++){
                  if(document.body.textContent.includes('Rust smoke message')){report('test-render');break;}
                  await new Promise(resolve=>setTimeout(resolve,50));
                 }
                 const stream=await fetch('/api/stream?after_id=0',{signal:AbortSignal.timeout(4000)});
                 const reader=stream.body.getReader();const decoder=new TextDecoder();let pending='';
                 while(!pending.includes('event:')){const part=await reader.read();if(part.done)break;pending+=decoder.decode(part.value,{stream:true});}
                 await reader.cancel();if(!stream.ok||!pending.includes('event:'))throw Error('stream');
                 report('test-stream');
                 window.desktopInbox.openSettings();window.desktopInbox.openCampus();window.desktopInbox.openMail();
                 document.getElementById('aihot-open').click();
                }catch(error){report('test-fail-'+error.message);}
            })()"#);
        }
        let _ = events;
    }
}

fn run() -> Result<()> {
    let started = Instant::now();
    let arguments: Vec<_> = std::env::args_os().skip(1).collect();
    let profile_arg = arguments
        .windows(2)
        .find(|pair| pair[0] == "--profile")
        .map(|pair| PathBuf::from(&pair[1]));
    let isolated = profile_arg.is_some();
    let profile = profile_arg.unwrap_or_else(|| {
        PathBuf::from(std::env::var_os("APPDATA").unwrap_or_default()).join("企微收件台")
    });
    if !profile.is_absolute() {
        return Err(io::Error::other("无效的本机数据目录").into());
    }
    if !isolated {
        unsafe {
            let mutex = CreateMutexW(
                std::ptr::null(),
                0,
                native::wide("Local\\WecomInboxRustApp").as_ptr(),
            );
            if mutex.is_null() {
                return Err(io::Error::last_os_error().into());
            }
            if GetLastError() == ERROR_ALREADY_EXISTS {
                return Ok(());
            }
        }
    }
    fs::create_dir_all(&profile)?;
    let executable = std::env::current_exe()?;
    let resources = executable
        .parent()
        .ok_or_else(|| io::Error::other("无法定位程序目录"))?
        .join("resources");
    let log_path = arguments
        .windows(2)
        .find(|pair| pair[0] == "--startup-log")
        .map(|pair| PathBuf::from(&pair[1]));
    let token = random_key()?;
    let workbench_key = random_key()?;
    let service_origin = Arc::new(RwLock::new(None));
    let origin = server::start(
        resources.clone(),
        token.clone(),
        workbench_key.clone(),
        service_origin.clone(),
        !isolated,
    )
    .map_err(|error| io::Error::other(error.to_string()))?;
    let events = EventLoopBuilder::<AppEvent>::with_user_event().build();
    let proxy = events.create_proxy();
    let smoke = isolated && arguments.iter().any(|arg| arg == "--smoke");
    let mut state = State {
        views: HashMap::new(),
        news: None,
        trusted: WebContext::new(Some(profile.join("rust-ui"))),
        web: WebContext::new(Some(profile.join("rust-aihot"))),
        proxy: proxy.clone(),
        origin,
        token,
        workbench_key,
        service_origin,
        resources,
        profile,
        service: None,
        generation: 0,
        exiting: false,
        restarting: false,
        started,
        isolated,
        smoke,
        smoke_started: false,
        smoke_news_link: false,
        log_path,
    };
    if state.start_service().is_err() {
        state.log("service-spawn-failed");
    }
    state.show("main", &events)?;
    if !isolated || arguments.iter().any(|arg| arg == "--preload-aihot") {
        thread::spawn(move || {
            thread::sleep(Duration::from_millis(1800));
            let _ = proxy.send_event(AppEvent::Warm);
        });
    }
    events.run(move |event, target, flow| {
        *flow = ControlFlow::Wait;
        match event {
            Event::UserEvent(AppEvent::Call(role, call)) if !state.exiting => {
                state.handle_call(role, call, target)
            }
            Event::UserEvent(AppEvent::Service(generation, value))
                if generation == state.generation =>
            {
                state.on_service(value)
            }
            Event::UserEvent(AppEvent::Ended(generation)) if generation == state.generation => {
                if let Some(mut service) = state.service.take() {
                    let _ = service.child.wait();
                }
                if let Ok(mut service) = state.service_origin.write() {
                    *service = None;
                }
                if state.exiting {
                    *flow = ControlFlow::Exit;
                } else if std::mem::take(&mut state.restarting) {
                    if state.start_service().is_err() {
                        state.emit("main", "inbox-service-failed", Value::Null);
                    }
                } else {
                    state.emit("main", "inbox-service-failed", Value::Null);
                }
            }
            Event::UserEvent(AppEvent::Warm) if !state.exiting => {
                let _ = state.show_news(target, false);
            }
            Event::UserEvent(AppEvent::SmokeStep) if !state.exiting => state.smoke_step(target),
            Event::UserEvent(AppEvent::ShutdownDeadline) if state.exiting => {
                if let Some(mut service) = state.service.take() {
                    let _ = service.child.kill();
                    let _ = service.child.wait();
                }
                *flow = ControlFlow::Exit;
            }
            Event::UserEvent(AppEvent::NewsOpen(address)) => {
                if let Some(news) = &state.news {
                    let _ = news.content.load_url(&address);
                }
            }
            Event::UserEvent(AppEvent::NewsPage(loading, address)) => {
                if !loading && address == HOME {
                    state.log("aihot-home");
                    if state.smoke && !state.smoke_news_link {
                        state.smoke_news_link = true;
                        if let Some(news) = &state.news {
                            let _ = news.content.evaluate_script(
                                r#"document.querySelector('a[target="_blank"][href="https://github.com/KKKKhazix/AIHOT"]')?.click();"#,
                            );
                        }
                    }
                }
                if state.smoke_news_link && loading && address == "https://github.com/KKKKhazix/AIHOT" {
                    state.log("test-aihot-inline-link");
                }
                if let Some(news) = &mut state.news {
                    news.loading = loading;
                    news.address = address;
                }
                state.update_news();
            }
            Event::WindowEvent {
                window_id,
                event: WindowEvent::CloseRequested,
                ..
            } => {
                if state
                    .news
                    .as_ref()
                    .is_some_and(|news| news.window.id() == window_id)
                {
                    if let Some(news) = &state.news {
                        news.window.set_visible(false);
                    }
                    return;
                }
                let role = state
                    .views
                    .iter()
                    .find(|(_, surface)| surface.window.id() == window_id)
                    .map(|(role, _)| *role);
                if role == Some("main") {
                    state.views.clear();
                    state.news = None;
                    state.exiting = true;
                    state.request_stop();
                    if state.service.is_none() {
                        *flow = ControlFlow::Exit;
                    } else {
                        let proxy = state.proxy.clone();
                        thread::spawn(move || {
                            thread::sleep(Duration::from_secs(6));
                            let _ = proxy.send_event(AppEvent::ShutdownDeadline);
                        });
                    }
                } else if let Some(role) = role {
                    state.views.remove(role);
                }
            }
            Event::WindowEvent {
                window_id,
                event: WindowEvent::Resized(_),
                ..
            } => {
                if let Some(news) = &state.news
                    && news.window.id() == window_id
                {
                    let (top, body) = State::news_bounds(&news.window);
                    let _ = news.toolbar.set_bounds(top);
                    let _ = news.content.set_bounds(body);
                }
            }
            _ => {}
        }
    })
}

fn main() {
    if let Err(error) = run() {
        native::message(
            std::ptr::null_mut(),
            "企微收件台启动失败",
            &error.to_string(),
            false,
        );
    }
}
