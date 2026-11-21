use std::{
    fs,
    io::{self, Read, Write},
    path::Path,
    sync::{
        Arc, RwLock,
        atomic::{AtomicUsize, Ordering},
    },
    thread,
    time::Duration,
};
use tiny_http::{Header, Request, Response, Server};

pub type ServiceOrigin = Arc<RwLock<Option<String>>>;

fn header(name: &str, value: &str) -> Header {
    Header::from_bytes(name.as_bytes(), value.as_bytes()).expect("valid header")
}

fn text(request: Request, status: u16, value: &str) {
    let _ = request.respond(
        Response::from_string(value)
            .with_status_code(status)
            .with_header(header("Content-Type", "application/json; charset=utf-8")),
    );
}

pub fn start(
    resources: std::path::PathBuf,
    token: String,
    access_key: String,
    service: ServiceOrigin,
    prefer_fixed_port: bool,
) -> Result<String, Box<dyn std::error::Error + Send + Sync>> {
    let server = if prefer_fixed_port {
        Server::http("127.0.0.1:49188").or_else(|_| Server::http("127.0.0.1:0"))?
    } else {
        Server::http("127.0.0.1:0")?
    };
    let address = server.server_addr().to_string();
    let origin = format!("http://{address}");
    let public = origin.clone();
    let agent = ureq::Agent::config_builder()
        .proxy(None)
        .max_redirects(0)
        .timeout_connect(Some(Duration::from_secs(2)))
        .http_status_as_error(false)
        .build()
        .new_agent();
    thread::spawn(move || {
        let resources = Arc::new(resources);
        let token = Arc::new(token);
        let access_key = Arc::new(access_key);
        let origin = Arc::new(public);
        let payload_count = Arc::new(AtomicUsize::new(0));
        for request in server.incoming_requests() {
            if payload_count.fetch_add(1, Ordering::AcqRel) >= 24 {
                payload_count.fetch_sub(1, Ordering::AcqRel);
                text(request, 503, r#"{"error":"本地服务繁忙"}"#);
                continue;
            }
            let (resources, token, access_key, origin, service, agent, payload_count) = (
                resources.clone(),
                token.clone(),
                access_key.clone(),
                origin.clone(),
                service.clone(),
                agent.clone(),
                payload_count.clone(),
            );
            thread::spawn(move || {
                serve(
                    request,
                    &resources,
                    &token,
                    &access_key,
                    &origin,
                    service,
                    &agent,
                );
                payload_count.fetch_sub(1, Ordering::AcqRel);
            });
        }
    });
    Ok(origin)
}

fn serve(
    request: Request,
    resources: &Path,
    token: &str,
    access_key: &str,
    origin: &str,
    service: ServiceOrigin,
    agent: &ureq::Agent,
) {
    let value = |name: &'static str| {
        request
            .headers()
            .iter()
            .find(|item| item.field.equiv(name))
            .map(|item| item.value.as_str())
    };
    if value("Host") != origin.strip_prefix("http://")
        || value("X-Inbox-Desktop") != Some(token)
        || value("Sec-Fetch-Site") == Some("cross-site")
        || value("Origin").is_some_and(|address| address != origin)
    {
        text(request, 403, r#"{"error":"仅允许桌面应用访问"}"#);
        return;
    }
    let method = request.method().as_str();
    if !matches!(method, "GET" | "HEAD") {
        text(request, 405, r#"{"error":"只读服务"}"#);
        return;
    }
    let target = request.url();
    if target.starts_with("/api/") {
        let selected = service.read().ok().and_then(|entry| entry.clone());
        let Some(upstream) = selected else {
            text(request, 503, r#"{"error":"正在连接本机消息"}"#);
            return;
        };
        let uri = format!("{upstream}{target}");
        let Ok(mut forwarded) = http::Request::builder()
            .method(method)
            .uri(uri)
            .header("X-Workbench-Key", access_key)
            .body(())
        else {
            text(request, 400, "{}");
            return;
        };
        for name in ["last-event-id", "range", "if-range"] {
            if let Some(candidate) =
                value(name).and_then(|raw| http::HeaderValue::from_str(raw).ok())
            {
                forwarded.headers_mut().insert(name, candidate);
            }
        }
        let Ok(response) = agent.run(forwarded) else {
            text(request, 503, r#"{"error":"本机消息服务尚未就绪"}"#);
            return;
        };
        let (details, body) = response.into_parts();
        let bytes = details
            .headers
            .get("content-length")
            .and_then(|value| value.to_str().ok())
            .and_then(|value| value.parse().ok());
        let headers = details
            .headers
            .iter()
            .filter_map(|(name, value)| {
                if matches!(
                    name.as_str(),
                    "connection" | "transfer-encoding" | "content-length" | "keep-alive"
                ) {
                    return None;
                }
                Header::from_bytes(name.as_str(), value.as_bytes()).ok()
            })
            .collect();
        let mut body = body.into_with_config().limit(u64::MAX).reader();
        if details.status.is_success()
            && details
                .headers
                .get("content-type")
                .is_some_and(|value| value.as_bytes().starts_with(b"text/event-stream"))
        {
            let _ = stream_response(request, &mut body);
            return;
        }
        let routed = Response::new(details.status.as_u16().into(), headers, body, bytes, None);
        let _ = request.respond(routed);
        return;
    }
    let local = target.split('?').next().unwrap_or_default();
    let slug = local.trim_start_matches('/');
    let desktop_files = [
        "settings.html",
        "settings.css",
        "settings.js",
        "mail.html",
        "mail.css",
        "mail-ui.js",
        "campus.html",
        "campus.css",
        "campus-sources.css",
        "campus-ui.js",
        "aihot.html",
        "aihot.css",
        "aihot-ui.js",
    ];
    let main_files = [
        "index.html",
        "style.css",
        "inbox-layout.css",
        "app.js",
        "pane-resizer.js",
        "view-performance.mjs",
        "attachments-ui.js",
        "integration-ui.js",
        "integration.css",
        "rich-ui.js",
        "emoji-map.js",
    ];
    let file = if let Some(name) = slug
        .strip_prefix("desktop/")
        .filter(|name| desktop_files.contains(name))
    {
        resources.join("desktop").join(name)
    } else if slug.is_empty()
        || main_files.contains(&slug)
        || slug
            .strip_prefix("emoji/")
            .and_then(|name| name.strip_suffix(".png"))
            .is_some_and(|value| {
                value.len() == 16 && value.bytes().all(|unit| unit.is_ascii_hexdigit())
            })
    {
        resources
            .join("dist")
            .join(if slug.is_empty() { "index.html" } else { slug })
    } else {
        text(request, 404, "{}");
        return;
    };
    let Ok(bytes) = fs::read(&file) else {
        text(request, 404, "{}");
        return;
    };
    let mime = match file.extension().and_then(|extension| extension.to_str()) {
        Some("html") => "text/html; charset=utf-8",
        Some("css") => "text/css; charset=utf-8",
        Some("js" | "mjs") => "text/javascript; charset=utf-8",
        Some("png") => "image/png",
        _ => "application/octet-stream",
    };
    let _=request.respond(Response::from_data(bytes).with_header(header("Content-Type",mime)).with_header(header("X-Content-Type-Options","nosniff")).with_header(header("Content-Security-Policy","default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; object-src 'none'; form-action 'self'")));
}

fn stream_response(request: Request, body: &mut impl Read) -> io::Result<()> {
    let mut output = request.into_writer();
    output.write_all(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream; charset=utf-8\r\nCache-Control: no-cache, no-transform\r\nX-Content-Type-Options: nosniff\r\nTransfer-Encoding: chunked\r\n\r\n")?;
    output.flush()?;
    let mut buffer = [0_u8; 8192];
    loop {
        let length = body.read(&mut buffer)?;
        if length == 0 {
            break;
        }
        write!(output, "{length:x}\r\n")?;
        output.write_all(&buffer[..length])?;
        output.write_all(b"\r\n")?;
        output.flush()?;
    }
    output.write_all(b"0\r\n\r\n")?;
    output.flush()
}
