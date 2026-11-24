#[cfg(any(target_os = "linux", target_os = "macos", target_os = "windows"))]
mod desktop_launcher;
#[cfg(target_os = "macos")]
mod desktop_macos;
#[cfg(target_os = "windows")]
mod desktop_windows;

use local_message_hub_rs::{
    events::EventStore,
    host, mcp, media_backup, migration,
    plugins::{Permission, PluginKind, Plugins, generate_token},
    snapshot::Snapshot,
    store::Store,
};
use serde_json::json;
use std::{
    error::Error,
    ffi::OsString,
    io::{self, Write},
    net::{Ipv4Addr, SocketAddr, SocketAddrV4},
    path::PathBuf,
};

const USAGE: &str = "Usage:
  local-message-hub-rs message-ingress-policy --directory DIRECTORY --archive-id ID (Linux)
  local-message-hub-rs set-message-ingress-policy --directory DIRECTORY --archive-id ID --expected-revision REV --state paused|open (Linux)
  local-message-hub-rs new-plugin-token
  local-message-hub-rs preview-live-media-link --directory <native-directory> --bundle <imported-directory> --platform <qq|wechat> --account <account> (Linux)
  local-message-hub-rs serve --live <native-directory> --grants <private-grants.json> --live-imported-media-link <private-link.json> (Linux; optional)
  local-message-hub-rs export-schedule-operator --directory <native-directory> --grants <private-grants> (Linux; private anonymous pipes)
  local-message-hub-rs serve-mcp-http --snapshot <index.sqlite3> --grants <private-grants.json> --plugin-id <id> --port <local-port> (Linux)
  local-message-hub-rs stage-portable-core <binary> <new-dir> <binary-sha256> <source-revision>
  local-message-hub-rs verify-portable-core <dir> <manifest-sha256>
  local-message-hub-rs (draft-review-init | draft-review-inspect | draft-review-decide) --directory <native-directory>
  local-message-hub-rs (draft-handoff-preview | draft-handoff-pull) --directory <native-directory> (Linux/macOS; private pipes, export only)
  local-message-hub-rs draft-handoff-reconcile --directory <native-directory> (Linux/macOS; private pipes, signed export acknowledgement only)
  local-message-hub-rs provider-manifest
  local-message-hub-rs provider-profiles --directory <native-directory> --adapter <adapter> --account-alias <alias>
  local-message-hub-rs provider-profile-create --directory <native-directory> --adapter <adapter> --account-alias <alias> --id <profile>
  local-message-hub-rs provider-profile-set --directory <native-directory> --adapter <adapter> --account-alias <alias> --id <profile> --revision <current> --intent <enabled|disabled>
  local-message-hub-rs provider-profile-retire --directory <native-directory> --adapter <adapter> --account-alias <alias> --id <disabled-profile> --revision <current>
  local-message-hub-rs inspect-media-backup --source <legacy-media.zip>
  local-message-hub-rs import-media-backup --source <legacy-media.zip> --directory <fresh-private-target>
  local-message-hub-rs project-snapshot --source <detached-index.sqlite3> --directory <empty-private-directory>
  local-message-hub-rs preview-campus-json --source <detached-articles.json> --directory <native-directory> --profile <local-profile>
  local-message-hub-rs import-campus-json --source <detached-articles.json> --directory <native-directory> --profile <local-profile> --confirm <preview-digest>
  local-message-hub-rs preview-campus-content --source <detached-content.json> --directory <native-directory> --profile <local-profile>
  local-message-hub-rs import-campus-content --source <detached-content.json> --directory <native-directory> --profile <local-profile> --confirm <preview-digest>
  local-message-hub-rs preview-imap-readonly --allow-network --host <dns-name> --endpoint <numeric-ip:port> --ca-der <trust-root.der> --leaf-sha256 <sha256> --mailbox INBOX --uidvalidity <epoch> --uid <uid> (Linux/macOS; private stdin pipe)
  local-message-hub-rs (preview-imap-import | import-imap-readonly) --allow-network --host <dns-name> --endpoint <numeric-ip:port> --ca-der <trust-root.der> --leaf-sha256 <sha256> --mailbox INBOX --uidvalidity <epoch> --uid <uid> --directory <native-directory> --account-sha256 <sha256> --after <previous-uid-or-0> [--confirm <receipt> for import only]
  local-message-hub-rs (preview-imap-batch | import-imap-batch) --allow-network --host <dns-name> --endpoint <numeric-ip:port> --ca-der <trust-root.der> --leaf-sha256 <sha256> --mailbox INBOX --uidvalidity <epoch> --uids <uid1,uid2,...> --directory <native-directory> --account-sha256 <sha256> --after <previous-uid-or-0> [--confirm <receipt> for import only]
  local-message-hub-rs preview-imap-fixture --directory <native-directory> --port <loopback-port> --ca-der <fixture-ca.der> --leaf-sha256 <sha256> --account <mailbox> --uidvalidity <epoch> --after <previous-uid-or-0> --uid <fetch-uid>
  local-message-hub-rs import-imap-fixture --directory <native-directory> --port <loopback-port> --ca-der <fixture-ca.der> --leaf-sha256 <sha256> --account <mailbox> --uidvalidity <epoch> --after <previous-uid-or-0> --uid <fetch-uid> --confirm <preview-digest>
  local-message-hub-rs preview-detached-voice-wav --source <imported-media-bundle> --platform <platform> --account <account> --conversation <id> --message <id> --attachment <id> (Linux)
  local-message-hub-rs export-detached-voice-wav --source <imported-media-bundle> --platform <platform> --account <account> --conversation <id> --message <id> --attachment <id> --confirm <preview-digest> (Linux; private pipe)
  local-message-hub-rs preview-detached-voice-result --bundle <imported-media-bundle> --directory <private-journal> --platform <platform> --account <account> --conversation <id> --message <id> --attachment <id> --source <private-result-json> (Linux)
  local-message-hub-rs import-detached-voice-result --bundle <imported-media-bundle> --directory <private-journal> --platform <platform> --account <account> --conversation <id> --message <id> --attachment <id> --source <private-result-json> --confirm <preview-digest> (Linux)
  local-message-hub-rs read-detached-voice-result --bundle <imported-media-bundle> --directory <private-journal> --platform <platform> --account <account> --conversation <id> --message <id> --attachment <id> (Linux; private pipe)
  local-message-hub-rs preview-voice-annotation --source <private-detached.json> --directory <native-directory> --platform <platform> --account <account>
  local-message-hub-rs import-voice-annotation --source <private-detached.json> --directory <native-directory> --platform <platform> --account <account> --confirm <preview-digest>
  local-message-hub-rs preview-eml --source <message.eml> --directory <native-directory> --account <mailbox>
  local-message-hub-rs import-eml --source <message.eml> --directory <native-directory> --account <mailbox> --confirm <preview-digest>
  local-message-hub-rs preview-qq-hub-csv --source <messages.csv> --directory <native-directory> --account <account> --self-sender <sender-id>
  local-message-hub-rs import-qq-hub-csv --source <messages.csv> --directory <native-directory> --account <account> --self-sender <sender-id> --confirm <preview-digest>
  local-message-hub-rs list-wecom-frozen-roster --directory <native-directory> --grants <private-grants.json> (Linux; private pipes)
  local-message-hub-rs inventory-wecom-frozen-snapshot --source <frozen-rendered.json> --directory <native-directory> --grants <private-grants.json> (Linux; private pipes)
  local-message-hub-rs inspect-wecom-frozen-snapshot --source <frozen-rendered.json> --directory <native-directory> --grants <private-grants.json> (Linux; private pipes)
  local-message-hub-rs read-wecom-frozen-outcome --source <frozen-rendered.json> --directory <native-directory> --grants <private-grants.json> (Linux; private pipes)
  local-message-hub-rs preflight-wecom-frozen-target --source <frozen-rendered.json> --directory <native-directory> --grants <private-grants.json> (Linux; private pipes)
  local-message-hub-rs (preview-claimed-wecom-sync | import-claimed-wecom-sync | status-claimed-wecom-sync) --source <detached.json> --directory <native-directory> --grants <private-grants.json> (Linux; private pipes)
  local-message-hub-rs wecom-sync-status --directory <native-directory> --account <account> --source-id <lineage>
  local-message-hub-rs preview-wecom-sync --source <rendered-batch.json> --directory <native-directory> --account <account> --source-id <lineage>
  local-message-hub-rs import-wecom-sync --source <rendered-batch.json> --directory <native-directory> --account <account> --source-id <lineage> --confirm <preview-digest>
  local-message-hub-rs preview-wecom-json --source <detached.json> --directory <native-directory> --account <account>
  local-message-hub-rs import-wecom-json --source <detached.json> --directory <native-directory> --account <account> --confirm <preview-digest>
  local-message-hub-rs preview-moments-json --source <detached.json> --directory <native-directory> --account <wechat-account>
  local-message-hub-rs import-moments-json --source <detached.json> --directory <native-directory> --account <wechat-account> --confirm <preview-digest>
  local-message-hub-rs inventory-wechat-legacy-export --source <approved-export.json> --grants <private-grants.json> (Linux; private pipes)
  local-message-hub-rs (preview-claimed-wechat | import-claimed-wechat | status-claimed-wechat) --source <operator-text.json> --directory <native-directory> --grants <private-grants.json> (Linux; private pipes)
  local-message-hub-rs preview-wechat-detached --source <operator-text.json> --directory <native-directory> --account <account> --namespace <source-id>
  local-message-hub-rs import-wechat-detached --source <operator-text.json> --directory <native-directory> --account <account> --namespace <source-id> --confirm <preview-digest>
  local-message-hub-rs preview-wechat-provider-page --source <detached.json> --account <account>
  local-message-hub-rs preview-telegram-json --source <result.json> --directory <native-directory> --account <user123>
  local-message-hub-rs import-telegram-json --source <result.json> --directory <native-directory> --account <user123> --confirm <preview-digest>
  local-message-hub-rs preview-legacy-json --source <messages.json> --directory <native-directory> --platform <platform> --account <account>
  local-message-hub-rs import-legacy-json --source <messages.json> --directory <native-directory> --platform <platform> --account <account> --confirm <preview-digest>
  local-message-hub-rs preview-guarded-legacy-json --source <messages.json> --directory <guarded-directory> --platform <platform> --account <account>
  local-message-hub-rs import-guarded-legacy-json --source <messages.json> --directory <guarded-directory> --platform <platform> --account <account> --confirm <preview-digest>
  local-message-hub-rs preview-guarded-v9 --source <index.sqlite3> --directory <empty-guarded-directory> --platform <platform> --account <account>
  local-message-hub-rs import-guarded-v9 --source <index.sqlite3> --directory <empty-guarded-directory> --platform <platform> --account <account> --confirm <preview-digest>
  local-message-hub-rs create-guarded-backup --directory <guarded-directory> --output <new-encrypted-file> --platform <platform> --account <account>
  local-message-hub-rs preview-guarded-backup --source <encrypted-file> --platform <platform> --account <account>
  local-message-hub-rs restore-guarded-backup --source <encrypted-file> --directory <new-guarded-directory> [--confirm <ciphertext-sha256>]
  local-message-hub-rs status-guarded-backup-restore --directory <guarded-directory> --ciphertext-sha256 <ciphertext-sha256>
  local-message-hub-rs list-guarded-backups --catalog <private-directory> --platform <platform> --account <account>
  local-message-hub-rs copy-guarded-backup --catalog <private-directory> --platform <platform> --account <account> --id <opaque-id> --output <new-encrypted-file>
  local-message-hub-rs guarded-backup-quarantine --catalog <private-directory> --platform <platform> --account <account> --action <quarantine|release> --id <opaque-id> [--confirm <preview-digest>]
  local-message-hub-rs plan-guarded-backup-retention --catalog <private-directory> --platform <platform> --account <account> --oldest-first --keep-latest <n> [--target-bytes <n>]
  local-message-hub-rs init-guarded-live --directory <empty-private-directory>
  local-message-hub-rs serve-guarded-browser --directory <guarded-native-directory> --grants <private-plugins.json> --grant-id <browser-grant-id> [--reader]
  local-message-hub-rs (serve-guarded | serve-guarded-legacy) --directory <guarded-native-directory> --grants <private-plugins.json>
  local-message-hub-rs init-live --directory <empty-private-directory> [--history-index] [--conversation-index]
  local-message-hub-rs upgrade-mcp-incoming --directory <existing-native-directory>
  local-message-hub-rs upgrade-event-scopes --directory <existing-native-directory>
  local-message-hub-rs upgrade-metadata --directory <existing-native-directory>
  local-message-hub-rs serve (--snapshot <index.sqlite3> | --live <native-directory> | --media-bundle <private-directory>) --grants <plugins.json> [--port <0..65535>]
  local-message-hub-rs desktop-supervise --snapshot <index.sqlite3> --grants <private-plugins.json> (Linux, macOS or Windows)
  local-message-hub-rs desktop-supervise --live <native-directory> --grants <private-plugins.json> (Linux, macOS or Windows; ordinary archive)
  local-message-hub-rs desktop-supervise --media-bundle <private-directory> --grants <private-plugins.json> (Linux only)
  local-message-hub-rs mcp (--snapshot <index.sqlite3> | --live <native-directory>) --grants <plugins.json> --plugin-id <id>
The MCP launcher supplies its tool plugin token through LMH_PLUGIN_TOKEN.";

fn invalid(message: &str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidInput, message)
}

#[derive(PartialEq, Eq)]
enum Mode {
    Http,
    Mcp,
    #[cfg(any(target_os = "linux", target_os = "macos", target_os = "windows"))]
    Desktop,
}

struct Config {
    archive: ArchiveConfig,
    grants: PathBuf,
    port: u16,
    plugin_id: Option<String>,
    live_imported_media_link: Option<PathBuf>,
    wechat_config: Option<PathBuf>,
    webui_dir: Option<PathBuf>,
}

enum ArchiveConfig {
    Snapshot(PathBuf),
    Live(PathBuf),
    #[cfg(target_os = "linux")]
    MediaBundle(PathBuf),
}

fn parse(mode: Mode, mut args: impl Iterator<Item = OsString>) -> io::Result<Config> {
    let (mut archive, mut grants, mut port, mut plugin_id) = (None, None, None, None);
    #[allow(unused_mut)]
    let mut live_imported_media_link = None;
    let mut wechat_config = None;
    let mut webui_dir = None;
    while let Some(key) = args.next() {
        let value = args.next().ok_or_else(|| invalid(USAGE))?;
        match key.to_str() {
            Some("--snapshot") if archive.is_none() => {
                archive = Some(ArchiveConfig::Snapshot(value.into()))
            }
            #[cfg(target_os = "linux")]
            Some("--media-bundle") if archive.is_none() && mode != Mode::Mcp => {
                archive = Some(ArchiveConfig::MediaBundle(value.into()))
            }
            Some("--live") if archive.is_none() => {
                archive = Some(ArchiveConfig::Live(value.into()))
            }
            Some("--grants") if grants.is_none() => grants = Some(PathBuf::from(value)),
            #[cfg(target_os = "linux")]
            Some("--live-imported-media-link") if mode == Mode::Http && live_imported_media_link.is_none() => {
                live_imported_media_link = Some(PathBuf::from(value));
            }
            Some("--wechat-config") if mode == Mode::Http && wechat_config.is_none() => {
                wechat_config = Some(PathBuf::from(value));
            }
            Some("--webui-dir") if mode == Mode::Http && webui_dir.is_none() => {
                webui_dir = Some(PathBuf::from(value));
            }
            Some("--port") if port.is_none() && mode == Mode::Http => {
                port = Some(
                    value
                        .to_str()
                        .and_then(|text| text.parse::<u16>().ok())
                        .ok_or_else(|| invalid(USAGE))?,
                )
            }
            Some("--plugin-id") if plugin_id.is_none() && mode == Mode::Mcp => {
                plugin_id = Some(value.into_string().map_err(|_| invalid(USAGE))?);
            }
            _ => return Err(invalid(USAGE)),
        }
    }
    if mode == Mode::Mcp && plugin_id.as_deref().is_none_or(str::is_empty) {
        return Err(invalid(USAGE));
    }
    if live_imported_media_link.is_some() && !matches!(&archive, Some(ArchiveConfig::Live(_))) {
        return Err(invalid(USAGE));
    }
    if wechat_config.is_some() && !matches!(&archive, Some(ArchiveConfig::Live(_))) {
        return Err(invalid(USAGE));
    }
    if webui_dir.is_some() && !matches!(&archive, Some(ArchiveConfig::Live(_))) {
        return Err(invalid(USAGE));
    }
    Ok(Config {
        archive: archive.ok_or_else(|| invalid(USAGE))?,
        grants: grants.ok_or_else(|| invalid(USAGE))?,
        port: port.unwrap_or(0),
        plugin_id,
        live_imported_media_link,
        wechat_config,
        webui_dir,
    })
}

async fn run_http(
    port: u16,
    archive: ArchiveConfig,
    plugins: Plugins,
    _desktop_child: bool,
    _live_imported_media_link: Option<PathBuf>,
    _live_owner:Option<tokio::sync::watch::Receiver<bool>>,
    wechat_config: Option<PathBuf>,
    webui_dir: Option<PathBuf>,
) -> Result<(), Box<dyn Error>> {
    let listener =
        tokio::net::TcpListener::bind(SocketAddrV4::new(Ipv4Addr::LOCALHOST, port)).await?;
    let SocketAddr::V4(address) = listener.local_addr()? else {
        return Err(io::Error::other("invalid socket family").into());
    };
    let (app, mode) = match archive {
        ArchiveConfig::Snapshot(path) => {
            let snapshot = Snapshot::open(path)?;
            let store = Store::open(snapshot.path())?;
            (
                host::router(address, snapshot, store, plugins).map_err(invalid)?,
                "snapshot-read-only",
            )
        }
        #[cfg(target_os = "linux")]
        ArchiveConfig::MediaBundle(directory) => {
            let (bundle, snapshot, store) = local_message_hub_rs::media_bundle::Bundle::open(&directory)
                .map_err(|_| invalid("media bundle unavailable"))?;
            (host::media_router(address, bundle, snapshot, store, plugins).map_err(invalid)?,
             "imported-media-read-only")
        }
        ArchiveConfig::Live(directory) => {
            let events = EventStore::open(&directory)?;
            let store = Store::open_live(events.clone())?;
            if let Some(wx_cfg) = wechat_config {
                let wx_events = events.clone();
                let wx_dir = directory.clone();
                std::thread::Builder::new()
                    .name("wechat-local-sync".to_string())
                    .spawn(move || {
                        local_message_hub_rs::wechat_local_sync::run_background_sync(
                            wx_cfg,
                            wx_dir,
                            wx_events,
                            std::time::Duration::from_secs(2),
                        );
                    })?;
            }
            #[cfg(target_os = "linux")]
            let app = if let Some(config) = _live_imported_media_link {
                let link = local_message_hub_rs::live_imported_media::Link::open(&events,&config)?;
                host::live_router_with_imported_media(address,events,store,plugins,link)
            } else {
                host::live_router(address,events,store,plugins)
            };
            #[cfg(not(target_os = "linux"))]
            let app = if let Some(dir) = webui_dir {
                host::live_router_with_webui(address, events, store, plugins, dir)
            } else {
                host::live_router(address, events, store, plugins)
            };
            (app.map_err(invalid)?,"native-live")
        }
    };
    #[cfg(any(target_os = "macos", target_os = "windows"))]
    let mut live_owner=_live_owner;
    #[cfg(any(target_os = "macos", target_os = "windows"))]
    let app=if let Some(owner)=live_owner.as_ref() {
        if *owner.borrow() || owner.has_changed().is_err(){return Ok(());}
        local_message_hub_rs::live_http::with_owner_drain(app,owner.clone())
    } else {app};
    #[cfg(target_os = "linux")]
    let mut interrupt = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::interrupt())?;
    println!(
        "{}",
        json!({"event":"ready","origin":format!("http://{address}"),"api_version":1,"mode":mode})
    );
    io::stdout().flush()?;
    axum::serve(listener, app)
        .with_graceful_shutdown(async move {
            #[cfg(target_os = "linux")]
            let _ = interrupt.recv().await;
            #[cfg(any(target_os = "macos", target_os = "windows"))]
            if let Some(owner)=live_owner.as_mut(){
                let _=owner.wait_for(|closed|*closed).await;
                return;
            }
            #[cfg(target_os = "windows")]
            if _desktop_child {
                // Snapshot children retain their immediate owner-watchdog exit.
                // Ordinary live has already selected bounded graceful drain above.
                std::future::pending::<()>().await;
            }
            #[cfg(not(target_os = "linux"))]
            let _ = tokio::signal::ctrl_c().await;
        })
        .await?;
    Ok(())
}

fn run(mut args: impl Iterator<Item = OsString>) -> Result<(), Box<dyn Error>> {
    let cmd = args.next();
    match cmd.as_deref().and_then(|value| value.to_str()) {
        Some("--help" | "-h") if args.next().is_none() => println!("{USAGE}"),
        Some("new-plugin-token") if args.next().is_none() => {
            let (token, token_sha256) =
                generate_token().map_err(|_| io::Error::other("OS randomness unavailable"))?;
            println!("{}", json!({"token":token,"token_sha256":token_sha256}));
        }
        #[cfg(target_os = "linux")]
        Some(command @ ("message-ingress-policy"|"set-message-ingress-policy"))=>{
            let mut take=|flag:&str|args.next().filter(|arg|arg==flag)
                .and_then(|_|args.next()).ok_or_else(||invalid(USAGE));
            let directory=PathBuf::from(take("--directory")?);
            let archive_id=take("--archive-id")?.into_string().map_err(|_|invalid(USAGE))?;
            let change=if command=="set-message-ingress-policy" {
                let revision=take("--expected-revision")?.to_str().and_then(|r|r.parse::<i64>().ok())
                    .ok_or_else(||invalid(USAGE))?;
                let paused=match take("--state")?.to_str() {
                    Some("paused")=>true,Some("open")=>false,_=>return Err(invalid(USAGE).into()),
                };
                Some((revision,paused))
            } else {None};
            if args.next().is_some(){return Err(invalid(USAGE).into());}
            local_message_hub_rs::ingress_policy::operator(&directory,&archive_id,change)?;
        }
        #[cfg(target_os = "linux")]
        Some("export-schedule-operator") => {
            let mut take = |flag:&str| args.next().filter(|arg|arg==flag)
                .and_then(|_|args.next()).ok_or_else(||invalid(USAGE));
            let directory=PathBuf::from(take("--directory")?);
            let grants=PathBuf::from(take("--grants")?);
            if args.next().is_some() {return Err(invalid(USAGE).into());}
            local_message_hub_rs::export_schedule_cli::run(&directory,&grants)?;
        }
        #[cfg(not(target_os = "linux"))]
        Some("export-schedule-operator") => {
            return Err(invalid("local export operator requires Linux private pipes").into());
        }
        Some(command @ ("stage-portable-core" | "verify-portable-core")) => {
            local_message_hub_rs::portable_package::command(command,args)?;
        }
        #[cfg(any(target_os="linux",target_os="macos"))]
        Some(command @ ("draft-handoff-preview" | "draft-handoff-pull" | "draft-handoff-reconcile")) => {
            let directory = args.next().filter(|flag| flag == "--directory").and_then(|_| args.next())
                .filter(|_| args.next().is_none()).ok_or_else(|| invalid(USAGE))?;
            local_message_hub_rs::draft_review_cli::run(&PathBuf::from(directory), command)?;
        }
        #[cfg(not(any(target_os="linux",target_os="macos")))]
        Some("draft-handoff-preview" | "draft-handoff-pull" | "draft-handoff-reconcile") => {
            return Err(invalid("local draft handoff requires Linux or macOS private pipes").into());
        }
        Some(command @ ("draft-review-init" | "draft-review-inspect" | "draft-review-decide")) => {
            let directory=args.next().filter(|s|s=="--directory").and_then(|_|args.next())
                .filter(|_|args.next().is_none()).map(PathBuf::from).ok_or_else(||invalid(USAGE))?;
            local_message_hub_rs::draft_review_cli::run(&directory,command)?;
        }
        Some("provider-manifest") => {
            if args.next().is_some() { return Err(invalid(USAGE).into()); }
            println!("{}", serde_json::to_string(&local_message_hub_rs::provider_manifest::read())?);
        }
        Some(command @ ("provider-profiles" | "provider-profile-create" | "provider-profile-set" | "provider-profile-retire")) => {
            use local_message_hub_rs::provider_profiles::{self, Adapter};
            let mut take = |flag: &str| args.next().filter(|arg| arg == flag)
                .and_then(|_| args.next()).ok_or_else(|| invalid(USAGE));
            let directory = PathBuf::from(take("--directory")?);
            let name = take("--adapter")?.into_string().map_err(|_| invalid(USAGE))?;
            let adapter = Adapter::parse(&name)?;
            let account = take("--account-alias")?.into_string().map_err(|_| invalid(USAGE))?;
            let output = if command == "provider-profiles" {
                if args.next().is_some() { return Err(invalid(USAGE).into()); }
                serde_json::to_string(&provider_profiles::list(&directory, adapter, &account)?)?
            } else {
                let id = take("--id")?.into_string().map_err(|_| invalid(USAGE))?;
                if matches!(command, "provider-profile-set" | "provider-profile-retire") {
                    let value = take("--revision")?;
                    let text = value.to_str().filter(|s| !s.is_empty() && s.len() <= 16
                        && s.bytes().all(|b| b.is_ascii_digit())).ok_or_else(|| invalid(USAGE))?;
                    let revision = text.parse::<i64>().map_err(|_| invalid(USAGE))?;
                    if command == "provider-profile-retire" {
                        if args.next().is_some() { return Err(invalid(USAGE).into()); }
                        serde_json::to_string(&provider_profiles::retire(&directory, adapter, &account, &id, revision)?)?
                    } else {
                        let enabled = match take("--intent")?.to_str() {
                            Some("enabled") => true,
                            Some("disabled") => false,
                            _ => return Err(invalid(USAGE).into()),
                        };
                        if args.next().is_some() { return Err(invalid(USAGE).into()); }
                        serde_json::to_string(&provider_profiles::set_intent(&directory, adapter, &account, &id, revision, enabled)?)?
                    }
                } else {
                    if args.next().is_some() { return Err(invalid(USAGE).into()); }
                    serde_json::to_string(&provider_profiles::create(&directory, adapter, &account, &id)?)?
                }
            };
            println!("{output}");
        }
        Some("inspect-media-backup") => {
            let Some(source) = args.next().filter(|key| key == "--source")
                .and_then(|_| args.next()).filter(|_| args.next().is_none()) else {
                return Err(invalid(USAGE).into());
            };
            println!("{}", serde_json::to_string(&media_backup::inspect(&PathBuf::from(source))?)?);
        }
        Some("import-media-backup") => {
            let Some(source) = args.next().filter(|key| key == "--source").and_then(|_| args.next()) else {
                return Err(invalid(USAGE).into());
            };
            let Some(directory) = args.next().filter(|key| key == "--directory")
                .and_then(|_| args.next()).filter(|_| args.next().is_none()) else {
                return Err(invalid(USAGE).into());
            };
            let result = media_backup::import(&PathBuf::from(source), &PathBuf::from(directory))?;
            println!("{}", serde_json::to_string(&result)?);
        }
        Some("project-snapshot") => {
            let Some(source) = args.next().filter(|key| key == "--source").and_then(|_| args.next()) else {
                return Err(invalid(USAGE).into());
            };
            let Some(directory) = args.next().filter(|key| key == "--directory")
                .and_then(|_| args.next()).filter(|_| args.next().is_none()) else {
                return Err(invalid(USAGE).into());
            };
            let result = migration::project_snapshot(&PathBuf::from(source), &PathBuf::from(directory))?;
            println!("{}", serde_json::to_string(&result)?);
        }
        Some(command @ ("preview-qq-hub-csv" | "import-qq-hub-csv")) => {
            let mut take = |name: &str| args.next().filter(|key| key == name)
                .and_then(|_|args.next()).ok_or_else(||invalid(USAGE));
            let source = PathBuf::from(take("--source")?);
            let directory = PathBuf::from(take("--directory")?);
            let account = take("--account")?.into_string().map_err(|_|invalid(USAGE))?;
            let sender = take("--self-sender")?.into_string().map_err(|_|invalid(USAGE))?;
            let output = if command == "import-qq-hub-csv" {
                let confirmation = take("--confirm")?.into_string().map_err(|_|invalid(USAGE))?;
                if args.next().is_some() { return Err(invalid(USAGE).into()); }
                serde_json::to_string(&local_message_hub_rs::qq_csv_import::commit(
                    &source,&directory,&account,&sender,&confirmation)?)?
            } else {
                if args.next().is_some() { return Err(invalid(USAGE).into()); }
                serde_json::to_string(&local_message_hub_rs::qq_csv_import::preview(
                    &source,&directory,&account,&sender)?)?
            };
            println!("{output}");
        }
        Some(command @ ("preview-campus-json" | "import-campus-json" | "preview-campus-content" | "import-campus-content")) => {
            use local_message_hub_rs::campus;
            let mut take = |name:&str|args.next().filter(|key|key==name)
                .and_then(|_|args.next()).ok_or_else(||invalid(USAGE));
            let source=PathBuf::from(take("--source")?);
            let directory=PathBuf::from(take("--directory")?);
            let profile=take("--profile")?.into_string().map_err(|_|invalid(USAGE))?;
            let result=if command.starts_with("import-") {
                let code=take("--confirm")?.into_string().map_err(|_|invalid(USAGE))?;
                if args.next().is_some(){return Err(invalid(USAGE).into());}
                if command=="import-campus-content" {
                    serde_json::to_string(&campus::enrichment::apply(&source,&directory,&profile,&code)?)?
                } else {
                    serde_json::to_string(&campus::commit(&source,&directory,&profile,&code)?)?
                }
            } else {
                if args.next().is_some(){return Err(invalid(USAGE).into());}
                if command=="preview-campus-content" {
                    serde_json::to_string(&campus::enrichment::preview(&source,&directory,&profile)?)?
                } else {
                    serde_json::to_string(&campus::preview(&source,&directory,&profile)?)?
                }
            };
            println!("{result}");
        }
        #[cfg(any(target_os = "linux", target_os = "macos"))]
        Some(command @ ("preview-imap-readonly" | "preview-imap-import" | "import-imap-readonly" |
                        "preview-imap-batch" | "import-imap-batch")) => {
            use local_message_hub_rs::{imap_readonly::{self, Settings}, imap_readonly_import::{self, Import, Batch}};
            let batch=matches!(command,"preview-imap-batch"|"import-imap-batch");
            if args.next().as_deref().is_none_or(|arg| arg != "--allow-network") {
                return Err(invalid("IMAP requires explicit --allow-network").into());
            }
            let mut take = |flag: &str| args.next().filter(|arg| arg == flag)
                .and_then(|_| args.next()).ok_or_else(|| invalid(USAGE));
            let hostname = take("--host")?.into_string().map_err(|_| invalid(USAGE))?;
            let endpoint = take("--endpoint")?.to_str()
                .and_then(|s| s.parse::<SocketAddr>().ok()).ok_or_else(|| invalid(USAGE))?;
            let ca = PathBuf::from(take("--ca-der")?);
            let pin = take("--leaf-sha256")?.into_string().map_err(|_| invalid(USAGE))?;
            let mailbox = take("--mailbox")?.into_string().map_err(|_| invalid(USAGE))?;
            let epoch_value = take("--uidvalidity")?;
            let uid_value = take(if batch {"--uids"}else{"--uid"})?;
            let number = |value: &std::ffi::OsStr, allow_zero: bool| value.to_str()
                .filter(|s| !s.is_empty() && s.len() <= 10
                    && (!s.starts_with('0') || (allow_zero && *s == "0"))
                    && s.bytes().all(|b| b.is_ascii_digit()))
                .and_then(|s| s.parse::<u32>().ok()).ok_or_else(|| invalid(USAGE));
            let uidvalidity = number(&epoch_value, false)?;
            let uids=if batch {
                let value=uid_value.to_str().filter(|v|v.len()<=11*imap_readonly::MAX_EXPLICIT_UIDS-1)
                    .ok_or_else(||invalid(USAGE))?;
                let parsed=value.split(',').take(imap_readonly::MAX_EXPLICIT_UIDS+1)
                    .map(|v|number(std::ffi::OsStr::new(v),false)).collect::<io::Result<Vec<_>>>()?;
                if !(2..=imap_readonly::MAX_EXPLICIT_UIDS).contains(&parsed.len()) {
                    return Err(invalid(USAGE).into());
                }
                parsed
            }else{vec![number(&uid_value,false)?]};
            let uid=uids[0];
            let transport = Settings { hostname: &hostname, endpoint, ca_der: &ca,
                leaf_sha256: &pin, mailbox: &mailbox, uidvalidity, uid };
            let output = if command == "preview-imap-readonly" {
                if args.next().is_some() { return Err(invalid(USAGE).into()); }
                serde_json::to_string(&imap_readonly::preview_from_private_stdin(&transport)?)?
            } else {
                let directory = PathBuf::from(take("--directory")?);
                let account = take("--account-sha256")?.into_string().map_err(|_| invalid(USAGE))?;
                let previous_uid = number(&take("--after")?, true)?;
                if batch {
                    let input=Batch{transport,account_sha256:&account,previous_uid,uids:&uids};
                    if command=="import-imap-batch" {
                        let confirmation=take("--confirm")?.into_string().map_err(|_|invalid(USAGE))?;
                        if args.next().is_some(){return Err(invalid(USAGE).into());}
                        serde_json::to_string(&imap_readonly_import::commit_batch(&directory,&input,&confirmation)?)?
                    }else{
                        if args.next().is_some(){return Err(invalid(USAGE).into());}
                        serde_json::to_string(&imap_readonly_import::preview_batch(&directory,&input)?)?
                    }
                }else {
                    let input = Import { transport, account_sha256: &account, previous_uid };
                    if command == "import-imap-readonly" {
                        let confirmation = take("--confirm")?.into_string().map_err(|_| invalid(USAGE))?;
                        if args.next().is_some() { return Err(invalid(USAGE).into()); }
                        serde_json::to_string(&imap_readonly_import::commit(&directory, &input, &confirmation)?)?
                    } else {
                        if args.next().is_some() { return Err(invalid(USAGE).into()); }
                        serde_json::to_string(&imap_readonly_import::preview(&directory, &input)?)?
                    }
                }
            };
            println!("{output}");
        }
        #[cfg(not(any(target_os = "linux", target_os = "macos")))]
        Some("preview-imap-readonly" | "preview-imap-import" | "import-imap-readonly" |
             "preview-imap-batch" | "import-imap-batch") => {
            return Err(invalid("IMAP read-only source requires Linux or macOS private credential pipes").into());
        }
        Some(command @ ("preview-imap-fixture" | "import-imap-fixture")) => {
            use local_message_hub_rs::imap_fixture::{self,Settings,Window};
            let mut take = |name: &str| args.next().filter(|key| key == name)
                .and_then(|_|args.next()).ok_or_else(||invalid(USAGE));
            let directory = PathBuf::from(take("--directory")?);
            let parse = |value: std::ffi::OsString| -> Result<u32,_> {
                value.into_string().ok().and_then(|v|v.parse().ok()).ok_or_else(||invalid(USAGE))
            };
            let port = u16::try_from(parse(take("--port")?)?).map_err(|_|invalid(USAGE))?;
            let ca = PathBuf::from(take("--ca-der")?);
            let pin = take("--leaf-sha256")?.into_string().map_err(|_|invalid(USAGE))?;
            let account = take("--account")?.into_string().map_err(|_|invalid(USAGE))?;
            let window = Window{uidvalidity:parse(take("--uidvalidity")?)?,after:parse(take("--after")?)?,
                uid:parse(take("--uid")?)?};
            let settings = Settings{port,ca_der:&ca,leaf_sha256:&pin,account:&account,window};
            let output = if command == "import-imap-fixture" {
                let confirmation = take("--confirm")?.into_string().map_err(|_|invalid(USAGE))?;
                if args.next().is_some() { return Err(invalid(USAGE).into()); }
                serde_json::to_string(&imap_fixture::commit(&directory,&settings,&confirmation)?)?
            } else {
                if args.next().is_some() { return Err(invalid(USAGE).into()); }
                serde_json::to_string(&imap_fixture::preview(&directory,&settings)?)?
            };
            println!("{output}");
        }
        #[cfg(target_os = "linux")]
        Some(command @ ("preview-detached-voice-wav" | "export-detached-voice-wav")) => {
            let mut take = |name: &str| args.next().filter(|key| key == name)
                .and_then(|_| args.next()).ok_or_else(|| invalid(USAGE));
            let source = PathBuf::from(take("--source")?);
            let mut text = |flag: &str| take(flag)?.into_string().map_err(|_| invalid(USAGE));
            let platform = text("--platform")?;
            let account = text("--account")?;
            let conversation = text("--conversation")?;
            let message = text("--message")?;
            let attachment = text("--attachment")?;
            let confirmation = if command == "export-detached-voice-wav" {
                Some(text("--confirm")?)
            } else { None };
            if args.next().is_some() { return Err(invalid(USAGE).into()); }
            local_message_hub_rs::detached_voice_wav::run(
                &source,&platform,&account,&conversation,&message,&attachment,confirmation.as_deref())?;
        }
        #[cfg(target_os = "linux")]
        Some(command @ ("preview-detached-voice-result" | "import-detached-voice-result" | "read-detached-voice-result")) => {
            let mut take=|name:&str|args.next().filter(|key|key==name)
                .and_then(|_|args.next()).ok_or_else(||invalid(USAGE));
            let bundle=PathBuf::from(take("--bundle")?);
            let directory=PathBuf::from(take("--directory")?);
            let mut text=|flag:&str|take(flag)?.into_string().map_err(|_|invalid(USAGE));
            let platform=text("--platform")?;
            let account=text("--account")?;
            let conversation=text("--conversation")?;
            let message=text("--message")?;
            let attachment=text("--attachment")?;
            let source=if command=="read-detached-voice-result"{None}else{Some(PathBuf::from(take("--source")?))};
            let confirmation=if command=="import-detached-voice-result"{
                Some(take("--confirm")?.into_string().map_err(|_|invalid(USAGE))?)
            }else{None};
            if args.next().is_some(){return Err(invalid(USAGE).into());}
            local_message_hub_rs::detached_voice_result::run(&bundle,&directory,&platform,&account,
                &conversation,&message,&attachment,source.as_deref(),confirmation.as_deref())?;
        }
        Some(command @ ("preview-voice-annotation" | "import-voice-annotation")) => {
            let mut take = |name: &str| args.next().filter(|key| key == name)
                .and_then(|_| args.next()).ok_or_else(|| invalid(USAGE));
            let source = PathBuf::from(take("--source")?);
            let directory = PathBuf::from(take("--directory")?);
            let platform = take("--platform")?.into_string().map_err(|_| invalid(USAGE))?;
            let account = take("--account")?.into_string().map_err(|_| invalid(USAGE))?;
            let confirmation = if command == "import-voice-annotation" {
                Some(take("--confirm")?.into_string().map_err(|_| invalid(USAGE))?)
            } else { None };
            if args.next().is_some() { return Err(invalid(USAGE).into()); }
            local_message_hub_rs::transcription_cli::run(
                &source,&directory,&platform,&account,confirmation.as_deref())?;
        }
        Some(command @ ("preview-eml" | "import-eml")) => {
            let mut take = |name: &str| args.next().filter(|key| key == name)
                .and_then(|_|args.next()).ok_or_else(||invalid(USAGE));
            let source = PathBuf::from(take("--source")?);
            let directory = PathBuf::from(take("--directory")?);
            let account = take("--account")?.into_string().map_err(|_|invalid(USAGE))?;
            let output = if command == "import-eml" {
                let confirmation = take("--confirm")?.into_string().map_err(|_|invalid(USAGE))?;
                if args.next().is_some() { return Err(invalid(USAGE).into()); }
                serde_json::to_string(&local_message_hub_rs::eml_import::commit(
                    &source,&directory,&account,&confirmation)?)?
            } else {
                if args.next().is_some() { return Err(invalid(USAGE).into()); }
                serde_json::to_string(&local_message_hub_rs::eml_import::preview(
                    &source,&directory,&account)?)?
            };
            println!("{output}");
        }
        #[cfg(target_os="linux")]
        Some("list-wecom-frozen-roster") => {
            let mut take=|name:&str|args.next().filter(|key|key==name)
                .and_then(|_|args.next()).ok_or_else(||invalid(USAGE));
            let directory=PathBuf::from(take("--directory")?);
            let grants=PathBuf::from(take("--grants")?);
            if args.next().is_some(){return Err(invalid(USAGE).into());}
            local_message_hub_rs::wecom_sync::frozen_roster_cli(&directory,&grants)?;
        }
        #[cfg(target_os="linux")]
        Some(command @ ("inventory-wecom-frozen-snapshot" | "inspect-wecom-frozen-snapshot" | "read-wecom-frozen-outcome" | "preflight-wecom-frozen-target" | "preview-claimed-wecom-sync" | "import-claimed-wecom-sync" | "status-claimed-wecom-sync")) => {
            let mut take=|name:&str|args.next().filter(|key|key==name)
                .and_then(|_|args.next()).ok_or_else(||invalid(USAGE));
            let source=PathBuf::from(take("--source")?);
            let directory=PathBuf::from(take("--directory")?);
            let grants=PathBuf::from(take("--grants")?);
            if args.next().is_some(){return Err(invalid(USAGE).into());}
            if command=="inventory-wecom-frozen-snapshot" {
                local_message_hub_rs::wecom_sync::inventory_frozen_snapshot_cli(&source,&directory,&grants)?;
            } else if command=="inspect-wecom-frozen-snapshot" {
                local_message_hub_rs::wecom_sync::inspect_frozen_snapshot_cli(&source,&directory,&grants)?;
            } else if command=="preflight-wecom-frozen-target" {
                local_message_hub_rs::wecom_sync::preflight_frozen_snapshot_target_cli(&source,&directory,&grants)?;
            } else if command=="read-wecom-frozen-outcome" {
                local_message_hub_rs::wecom_sync::frozen_snapshot_outcome_cli(&source,&directory,&grants)?;
            } else if command=="status-claimed-wecom-sync" {
                local_message_hub_rs::wecom_sync::claimed_local_status_cli(&source,&directory,&grants)?;
            } else {
                local_message_hub_rs::wecom_sync::claimed_local_cli(&source,&directory,&grants,
                    command=="import-claimed-wecom-sync")?;
            }
        }
        Some(command @ ("wecom-sync-status" | "preview-wecom-sync" | "import-wecom-sync")) => {
            use local_message_hub_rs::wecom_sync;
            let mut take=|name:&str|args.next().filter(|key|key==name)
                .and_then(|_|args.next()).ok_or_else(||invalid(USAGE));
            let source=if command=="wecom-sync-status" { None } else { Some(PathBuf::from(take("--source")?)) };
            let directory=PathBuf::from(take("--directory")?);
            let account=take("--account")?.into_string().map_err(|_|invalid(USAGE))?;
            let lineage=take("--source-id")?.into_string().map_err(|_|invalid(USAGE))?;
            let output=match (command,source.as_deref()) {
                ("wecom-sync-status",None)=>{
                    if args.next().is_some(){return Err(invalid(USAGE).into());}
                    serde_json::to_string(&wecom_sync::status(&directory,&account,&lineage)?)?
                }
                ("preview-wecom-sync",Some(source))=>{
                    if args.next().is_some(){return Err(invalid(USAGE).into());}
                    serde_json::to_string(&wecom_sync::preview(source,&directory,&account,&lineage)?)?
                }
                ("import-wecom-sync",Some(source))=>{
                    let confirmation=take("--confirm")?.into_string().map_err(|_|invalid(USAGE))?;
                    if args.next().is_some(){return Err(invalid(USAGE).into());}
                    serde_json::to_string(&wecom_sync::commit(source,&directory,&account,&lineage,&confirmation)?)?
                }
                _=>return Err(invalid(USAGE).into()),
            };
            println!("{output}");
        }
        Some(command @ ("preview-wecom-json" | "import-wecom-json")) => {
            use local_message_hub_rs::detached_platform::{import_wecom, preview_wecom};
            let mut take = |name: &str| args.next().filter(|key| key == name)
                .and_then(|_| args.next()).ok_or_else(|| invalid(USAGE));
            let source = PathBuf::from(take("--source")?);
            let directory = PathBuf::from(take("--directory")?);
            let account = take("--account")?.into_string().map_err(|_| invalid(USAGE))?;
            let output = if command == "import-wecom-json" {
                let confirmation = take("--confirm")?.into_string().map_err(|_| invalid(USAGE))?;
                if args.next().is_some() { return Err(invalid(USAGE).into()); }
                serde_json::to_string(&import_wecom(&source, &directory, &account, &confirmation)?)?
            } else {
                if args.next().is_some() { return Err(invalid(USAGE).into()); }
                serde_json::to_string(&preview_wecom(&source, &directory, &account)?)?
            };
            println!("{output}");
        }
        #[cfg(target_os="linux")]
        Some("inventory-wechat-legacy-export") => {
            let mut take=|name:&str|args.next().filter(|key|key==name)
                .and_then(|_|args.next()).ok_or_else(||invalid(USAGE));
            let source=PathBuf::from(take("--source")?);
            let grants=PathBuf::from(take("--grants")?);
            if args.next().is_some(){return Err(invalid(USAGE).into());}
            local_message_hub_rs::wechat_detached::inventory_legacy_export_cli(&source,&grants)?;
        }
        #[cfg(target_os="linux")]
        Some(command @ ("preview-claimed-wechat" | "import-claimed-wechat" | "status-claimed-wechat")) => {
            let mut take=|name:&str|args.next().filter(|key|key==name)
                .and_then(|_|args.next()).ok_or_else(||invalid(USAGE));
            let source=PathBuf::from(take("--source")?);
            let directory=PathBuf::from(take("--directory")?);
            let grants=PathBuf::from(take("--grants")?);
            if args.next().is_some(){return Err(invalid(USAGE).into());}
            if command=="status-claimed-wechat" {
                local_message_hub_rs::wechat_detached::claimed_local_status_cli(&source,&directory,&grants)?;
            } else {
                local_message_hub_rs::wechat_detached::claimed_local_cli(&source,&directory,&grants,
                    command=="import-claimed-wechat")?;
            }
        }
        Some(command @ ("preview-wechat-detached" | "import-wechat-detached")) => {
            use local_message_hub_rs::wechat_detached;
            let mut take = |name: &str| args.next().filter(|key| key == name)
                .and_then(|_| args.next()).ok_or_else(|| invalid(USAGE));
            let source = PathBuf::from(take("--source")?);
            let directory = PathBuf::from(take("--directory")?);
            let account = take("--account")?.into_string().map_err(|_| invalid(USAGE))?;
            let namespace = take("--namespace")?.into_string().map_err(|_| invalid(USAGE))?;
            let output = if command == "import-wechat-detached" {
                let confirmation = take("--confirm")?.into_string().map_err(|_| invalid(USAGE))?;
                if args.next().is_some() { return Err(invalid(USAGE).into()); }
                serde_json::to_string(&wechat_detached::commit(&source, &directory, &account, &namespace, &confirmation)?)?
            } else {
                if args.next().is_some() { return Err(invalid(USAGE).into()); }
                serde_json::to_string(&wechat_detached::preview(&source, &directory, &account, &namespace)?)?
            };
            println!("{output}");
        }
        Some(command @ ("preview-moments-json" | "import-moments-json")) => {
            use local_message_hub_rs::moments;
            let mut take=|name:&str|args.next().filter(|arg|arg==name)
                .and_then(|_|args.next()).ok_or_else(||invalid(USAGE));
            let source=PathBuf::from(take("--source")?);
            let directory=PathBuf::from(take("--directory")?);
            let account=take("--account")?.into_string().map_err(|_|invalid(USAGE))?;
            let output=if command=="import-moments-json" {
                let confirm=take("--confirm")?.into_string().map_err(|_|invalid(USAGE))?;
                if args.next().is_some(){return Err(invalid(USAGE).into());}
                serde_json::to_string(&moments::commit(&source,&directory,&account,&confirm)?)?
            } else {
                if args.next().is_some(){return Err(invalid(USAGE).into());}
                serde_json::to_string(&moments::preview(&source,&directory,&account)?)?
            };
            println!("{output}");
        }
        Some("preview-wechat-provider-page") => {
            let mut take = |name: &str| args.next().filter(|key| key == name)
                .and_then(|_| args.next()).ok_or_else(|| invalid(USAGE));
            let source = PathBuf::from(take("--source")?);
            let account = take("--account")?.into_string().map_err(|_| invalid(USAGE))?;
            if args.next().is_some() { return Err(invalid(USAGE).into()); }
            let preview = local_message_hub_rs::detached_platform::preview_wechat_provider_page(&source, &account)?;
            println!("{}", serde_json::to_string(&preview)?);
        }
        Some(command @ ("preview-telegram-json" | "import-telegram-json")) => {
            let mut take = |name: &str| args.next().filter(|key| key == name)
                .and_then(|_|args.next()).ok_or_else(||invalid(USAGE));
            let source = PathBuf::from(take("--source")?);
            let directory = PathBuf::from(take("--directory")?);
            let account = take("--account")?.into_string().map_err(|_|invalid(USAGE))?;
            let output = if command == "import-telegram-json" {
                let confirmation = take("--confirm")?.into_string().map_err(|_|invalid(USAGE))?;
                if args.next().is_some() { return Err(invalid(USAGE).into()); }
                serde_json::to_string(&local_message_hub_rs::telegram_import::commit(
                    &source,&directory,&account,&confirmation)?)?
            } else {
                if args.next().is_some() { return Err(invalid(USAGE).into()); }
                serde_json::to_string(&local_message_hub_rs::telegram_import::preview(&source,&directory,&account)?)?
            };
            println!("{output}");
        }
        Some(command @ ("preview-legacy-json" | "import-legacy-json")) => {
            let mut take = |name: &str| args.next().filter(|key| key == name)
                .and_then(|_| args.next()).ok_or_else(|| invalid(USAGE));
            let source = PathBuf::from(take("--source")?);
            let directory = PathBuf::from(take("--directory")?);
            let platform = take("--platform")?.into_string().map_err(|_| invalid(USAGE))?;
            let account = take("--account")?.into_string().map_err(|_| invalid(USAGE))?;
            let output = if command == "import-legacy-json" {
                let confirmation = take("--confirm")?.into_string().map_err(|_| invalid(USAGE))?;
                if args.next().is_some() { return Err(invalid(USAGE).into()); }
                serde_json::to_string(&local_message_hub_rs::legacy_import::commit(
                    &source,&directory,&platform,&account,&confirmation)?)?
            } else {
                if args.next().is_some() { return Err(invalid(USAGE).into()); }
                serde_json::to_string(&local_message_hub_rs::legacy_import::preview(
                    &source,&directory,&platform,&account)?)?
            };
            println!("{output}");
        }
        #[cfg(target_os = "linux")]
        Some(command @ ("preview-guarded-legacy-json" | "import-guarded-legacy-json" | "preview-guarded-v9" | "import-guarded-v9")) => {
            let mut take=|name:&str| args.next().filter(|key|key==name)
                .and_then(|_|args.next()).ok_or_else(||invalid(USAGE));
            let source=PathBuf::from(take("--source")?);
            let directory=PathBuf::from(take("--directory")?);
            let platform=take("--platform")?.into_string().map_err(|_|invalid(USAGE))?;
            let account=take("--account")?.into_string().map_err(|_|invalid(USAGE))?;
            let confirmation=if command.starts_with("import-") {
                Some(take("--confirm")?.into_string().map_err(|_|invalid(USAGE))?)
            } else { None };
            if args.next().is_some() { return Err(invalid(USAGE).into()); }
            local_message_hub_rs::guarded_cli::import(&source,&directory,&platform,&account,confirmation.as_deref(),command.ends_with("-v9"))?;
        }
        #[cfg(target_os = "linux")]
        Some("preview-guarded-backup") => {
            let mut take=|name:&str| args.next().filter(|key| key==name)
                .and_then(|_|args.next()).ok_or_else(||invalid(USAGE));
            let source=PathBuf::from(take("--source")?);
            let platform=take("--platform")?.into_string().map_err(|_|invalid(USAGE))?;
            let account=take("--account")?.into_string().map_err(|_|invalid(USAGE))?;
            if args.next().is_some() {return Err(invalid(USAGE).into());}
            local_message_hub_rs::guarded_cli::backup_preview(&source,&platform,&account)?;
        }
        #[cfg(target_os = "linux")]
        Some(command @ ("create-guarded-backup" | "restore-guarded-backup")) => {
            let mut take=|name:&str| args.next().filter(|key|key==name)
                .and_then(|_|args.next()).ok_or_else(||invalid(USAGE));
            let create=command=="create-guarded-backup";
            let source=PathBuf::from(take(if create {"--directory"} else {"--source"})?);
            let target=PathBuf::from(take(if create {"--output"} else {"--directory"})?);
            let scope=if create {
                Some((take("--platform")?.into_string().map_err(|_|invalid(USAGE))?,
                    take("--account")?.into_string().map_err(|_|invalid(USAGE))?))
            } else {None};
            let confirmation=match args.next() {
                None=>None,
                Some(flag) if !create && flag=="--confirm"=>Some(args.next().ok_or_else(||invalid(USAGE))?
                    .into_string().map_err(|_|invalid(USAGE))?),
                _=>return Err(invalid(USAGE).into()),
            };
            if args.next().is_some() {return Err(invalid(USAGE).into());}
            local_message_hub_rs::guarded_cli::backup_with_confirmation(&source,&target,
                scope.as_ref().map(|(p,a)|(p.as_str(),a.as_str())),confirmation.as_deref())?;
        }
        #[cfg(target_os = "linux")]
        Some("status-guarded-backup-restore") => {
            let mut take=|name:&str| args.next().filter(|key| key==name)
                .and_then(|_|args.next()).ok_or_else(||invalid(USAGE));
            let directory=PathBuf::from(take("--directory")?);
            let identity=take("--ciphertext-sha256")?.into_string().map_err(|_|invalid(USAGE))?;
            if args.next().is_some() {return Err(invalid(USAGE).into());}
            local_message_hub_rs::guarded_cli::backup_restore_status(&directory,&identity)?;
        }
        #[cfg(target_os = "linux")]
        Some(command @ ("list-guarded-backups" | "copy-guarded-backup")) => {
            let mut take=|name:&str| args.next().filter(|key|key==name)
                .and_then(|_|args.next()).ok_or_else(||invalid(USAGE));
            let directory=PathBuf::from(take("--catalog")?);
            let platform=take("--platform")?.into_string().map_err(|_|invalid(USAGE))?;
            let account=take("--account")?.into_string().map_err(|_|invalid(USAGE))?;
            let copy=if command=="copy-guarded-backup" {
                let id=take("--id")?.into_string().map_err(|_|invalid(USAGE))?;
                let output=PathBuf::from(take("--output")?);
                Some((id,output))
            } else {None};
            if args.next().is_some() {return Err(invalid(USAGE).into());}
            local_message_hub_rs::guarded_cli::backup_catalog(&directory,&platform,&account,
                copy.as_ref().map(|(id,path)|(id.as_str(),path.as_path())))?;
        }
        #[cfg(target_os = "linux")]
        Some("guarded-backup-quarantine") => {
            let mut take = |name: &str| args.next().filter(|key| key == name)
                .and_then(|_| args.next()).ok_or_else(|| invalid(USAGE));
            let directory = PathBuf::from(take("--catalog")?);
            let platform = take("--platform")?.into_string().map_err(|_| invalid(USAGE))?;
            let account = take("--account")?.into_string().map_err(|_| invalid(USAGE))?;
            let action = take("--action")?.into_string().map_err(|_| invalid(USAGE))?;
            let id = take("--id")?.into_string().map_err(|_| invalid(USAGE))?;
            let confirmation = match args.next() {
                None => None,
                Some(flag) if flag == "--confirm" => Some(args.next().ok_or_else(|| invalid(USAGE))?
                    .into_string().map_err(|_| invalid(USAGE))?),
                _ => return Err(invalid(USAGE).into()),
            };
            if args.next().is_some() { return Err(invalid(USAGE).into()); }
            local_message_hub_rs::guarded_cli::backup_quarantine(
                &directory,&platform,&account,&action,&id,confirmation.as_deref())?;
        }
        #[cfg(target_os = "linux")]
        Some("plan-guarded-backup-retention") => {
            let mut value = |key: &str| args.next().filter(|flag| flag == key)
                .and_then(|_| args.next()).ok_or_else(|| invalid(USAGE));
            let directory = PathBuf::from(value("--catalog")?);
            let platform = value("--platform")?.into_string().map_err(|_| invalid(USAGE))?;
            let account = value("--account")?.into_string().map_err(|_| invalid(USAGE))?;
            if args.next().is_none_or(|key| key != "--oldest-first") { return Err(invalid(USAGE).into()); }
            if args.next().is_none_or(|key| key != "--keep-latest") { return Err(invalid(USAGE).into()); }
            let keep_latest = args.next().and_then(|arg| arg.into_string().ok())
                .and_then(|arg| arg.parse::<usize>().ok()).ok_or_else(|| invalid(USAGE))?;
            let target_bytes = match args.next() {
                None => None,
                Some(key) if key == "--target-bytes" => Some(args.next().and_then(|arg| arg.into_string().ok())
                    .and_then(|arg| arg.parse::<u64>().ok()).ok_or_else(|| invalid(USAGE))?),
                _ => return Err(invalid(USAGE).into()),
            };
            if args.next().is_some() { return Err(invalid(USAGE).into()); }
            local_message_hub_rs::guarded_cli::backup_retention(&directory,&platform,&account,keep_latest,target_bytes)?;
        }
        #[cfg(target_os = "linux")]
        Some("init-guarded-live") => {
            let path=args.next().filter(|s|s=="--directory").and_then(|_|args.next())
                .filter(|_|args.next().is_none()).map(PathBuf::from).ok_or_else(||invalid(USAGE))?;
            local_message_hub_rs::guarded_cli::provision(&path)?;
        }
        #[cfg(target_os = "linux")]
        Some("serve-guarded-browser") => {
            let mut take=|name:&str|args.next().filter(|key|key==name)
                .and_then(|_|args.next()).ok_or_else(||invalid(USAGE));
            let dir=PathBuf::from(take("--directory")?);
            let grants=PathBuf::from(take("--grants")?);
            let id=take("--grant-id")?.into_string().map_err(|_|invalid(USAGE))?;
            let reader=match args.next() {
                None=>false,Some(flag) if flag=="--reader" && args.next().is_none()=>true,
                _=>return Err(invalid(USAGE).into()),
            };
            tokio::runtime::Builder::new_multi_thread().enable_all().build()?
                .block_on(local_message_hub_rs::guarded_cli::serve_browser(&dir,&grants,&id,reader))?;
        }
        #[cfg(target_os = "linux")]
        Some(command @ ("serve-guarded" | "serve-guarded-legacy")) => {
            let dir=args.next().filter(|s|s=="--directory").and_then(|_|args.next())
                .map(PathBuf::from).ok_or_else(||invalid(USAGE))?;
            let grants=args.next().filter(|s|s=="--grants").and_then(|_|args.next())
                .filter(|_|args.next().is_none()).map(PathBuf::from).ok_or_else(||invalid(USAGE))?;
            tokio::runtime::Builder::new_multi_thread().enable_all().build()?
                .block_on(local_message_hub_rs::guarded_cli::serve(&dir,&grants,command=="serve-guarded-legacy"))?;
        }
        Some(command @ ("init-live" | "upgrade-event-scopes" | "upgrade-metadata" | "upgrade-mcp-incoming")) => {
            let Some(directory) = args
                .next()
                .filter(|key| key == "--directory")
                .and_then(|_| args.next())
            else {
                return Err(invalid(USAGE).into());
            };
            let directory = PathBuf::from(directory);
            let state = if command == "init-live" {
                let mut indexes = local_message_hub_rs::events::ArchiveIndexes::default();
                for flag in &mut args {
                    if flag == "--history-index" && !indexes.history {
                        indexes.history = true;
                    } else if flag == "--conversation-index" && !indexes.conversations {
                        indexes.conversations = true;
                    } else {
                        return Err(invalid(USAGE).into());
                    }
                }
                (EventStore::init_with_indexes(&directory, indexes)?, "initialized")
            } else {
                if args.next().is_some() {
                    return Err(invalid(USAGE).into());
                }
                let archive = EventStore::open(&directory)?;
                if command == "upgrade-event-scopes" {
                    archive.upgrade_event_scopes()?;
                    (archive, "event_scopes_ready")
                } else if command == "upgrade-mcp-incoming" {
                    archive.upgrade_mcp_incoming()?;
                    (archive, "mcp_incoming_ready")
                } else {
                    archive.upgrade_metadata()?;
                    (archive, "metadata_ready")
                }
            };
            println!("{}", json!({"event":state.1,"mode":"native-live","archive_id":state.0.archive_id()}));
        }
        #[cfg(any(target_os = "linux", target_os = "macos", target_os = "windows"))]
        Some("desktop-supervise") => {
            let config = parse(Mode::Desktop, args)?;
            #[cfg(target_os = "macos")]
            desktop_macos::require_frontend_pipes()?;
            #[cfg(target_os = "windows")]
            desktop_windows::require_frontend_pipes()?;
            let (flag,path,mode) = match &config.archive {
                ArchiveConfig::Snapshot(path) => ("--snapshot",path,"snapshot-read-only"),
                ArchiveConfig::Live(path) => ("--live",path,"native-live"),
                #[cfg(target_os = "linux")]
                ArchiveConfig::MediaBundle(path) => ("--media-bundle",path,"imported-media-read-only"),
            };
            desktop_launcher::run(flag,path,&config.grants,mode)?;
        }
        #[cfg(any(target_os = "linux", target_os = "macos", target_os = "windows"))]
        Some("desktop-child") => {
            #[cfg(target_os = "linux")]
            {
                let parent = args.next().as_deref().and_then(|s| s.to_str())
                    .and_then(|s| s.parse().ok()).ok_or_else(|| invalid("invalid supervisor parent"))?;
                desktop_launcher::arm_parent_death(parent)?;
            }
            let config = parse(Mode::Desktop,args)?;
            #[cfg(target_os = "macos")]
            let live_owner=if matches!(&config.archive,ArchiveConfig::Live(_)){
                Some(desktop_macos::arm_live_owner_pipe()?)
            } else {desktop_macos::arm_owner_pipe()?; None};
            #[cfg(not(any(target_os = "macos", target_os = "windows")))]
            let live_owner=None;
            #[cfg(target_os = "windows")]
            let live_owner=match &config.archive {
                ArchiveConfig::Snapshot(path) => {
                    desktop_windows::local_file(path)?;
                    desktop_windows::arm_owner_pipe()?;
                    None
                }
                ArchiveConfig::Live(path) => {
                    desktop_windows::local_directory(path)?;
                    Some(desktop_windows::arm_live_owner_pipe()?)
                }
            };
            desktop_launcher::private_grants(&config.grants)?;
            let plugins = Plugins::load(config.grants)?;
            tokio::runtime::Builder::new_multi_thread().enable_all().build()?
                .block_on(run_http(config.port,config.archive,plugins,true,None,live_owner,config.wechat_config,config.webui_dir))?;
        }
        #[cfg(target_os="linux")]
        Some("serve-mcp-http") => {
            let mut take=|name:&str|args.next().filter(|arg|arg==name).and_then(|_|args.next()).ok_or_else(||invalid(USAGE));
            let snapshot=PathBuf::from(take("--snapshot")?);
            let grants=PathBuf::from(take("--grants")?);
            let id=take("--plugin-id")?.into_string().map_err(|_|invalid(USAGE))?;
            let port=take("--port")?.to_str().and_then(|s|s.parse::<u16>().ok()).ok_or_else(||invalid(USAGE))?;
            if args.next().is_some() {return Err(invalid(USAGE).into());}
            tokio::runtime::Builder::new_multi_thread().enable_all().build()?
                .block_on(local_message_hub_rs::mcp_http::serve(&snapshot,&grants,id,port))?;
        }
        #[cfg(target_os = "linux")]
        Some("preview-live-media-link") => {
            let mut take = |name: &str| args.next().filter(|key| key == name)
                .and_then(|_| args.next()).ok_or_else(|| invalid(USAGE));
            let directory = PathBuf::from(take("--directory")?);
            let bundle = PathBuf::from(take("--bundle")?);
            let platform = take("--platform")?.into_string().map_err(|_| invalid(USAGE))?;
            let account = take("--account")?.into_string().map_err(|_| invalid(USAGE))?;
            if args.next().is_some() { return Err(invalid(USAGE).into()); }
            let events = EventStore::open(&directory)?;
            println!("{}",local_message_hub_rs::live_imported_media::Link::preview(&events,&bundle,&platform,&account)?);
        }
        Some(command @ ("serve" | "mcp")) => {
            let mode = if command == "serve" {
                Mode::Http
            } else {
                Mode::Mcp
            };
            let config = parse(mode, args)?;
            let plugins = Plugins::load(config.grants)?;
            if let Some(id) = config.plugin_id {
                let plugin = std::env::var("LMH_PLUGIN_TOKEN")
                    .ok()
                    .and_then(|token| plugins.authenticate_token(&token))
                    .filter(|plugin| plugin.id == id && matches!(plugin.kind, PluginKind::Tool | PluginKind::ScopedTool))
                    .ok_or_else(|| {
                        io::Error::new(
                            io::ErrorKind::PermissionDenied,
                            "MCP tool plugin not authorized",
                        )
                    })?;
                let live_tool = plugin.permissions.contains(&Permission::McpIncoming)
                    || plugin.permissions.contains(&Permission::McpConversationNote);
                match config.archive {
                    ArchiveConfig::Snapshot(path) if !live_tool => {
                        let snapshot = Snapshot::open(path)?;
                        let store = Store::open(snapshot.path())?;
                        mcp::serve(io::stdin().lock(),io::stdout().lock(),snapshot,store,plugin)?;
                    }
                    ArchiveConfig::Live(path) if live_tool && plugin.kind == PluginKind::ScopedTool => {
                        let events = EventStore::open(&path)?;
                        mcp::serve_live(io::stdin().lock(),io::stdout().lock(),events,plugin)?;
                    }
                    _ => return Err(invalid("MCP grant does not authorize this archive mode").into()),
                }
            } else {
                tokio::runtime::Builder::new_multi_thread()
                    .enable_all()
                    .build()?
                    .block_on(run_http(config.port, config.archive, plugins, false, config.live_imported_media_link, None, config.wechat_config, config.webui_dir))?;
            }
        }
        Some("sync-wechat") => {
            let (mut live_dir, mut wx_cfg) = (None, None);
            while let Some(key) = args.next() {
                let value = args.next().ok_or_else(|| invalid(USAGE))?;
                match key.to_str() {
                    Some("--live") if live_dir.is_none() => live_dir = Some(PathBuf::from(value)),
                    Some("--wechat-config") if wx_cfg.is_none() => wx_cfg = Some(PathBuf::from(value)),
                    _ => return Err(invalid(USAGE).into()),
                }
            }
            let live_dir = live_dir.ok_or_else(|| invalid(USAGE))?;
            let wx_cfg = wx_cfg.ok_or_else(|| invalid(USAGE))?;
            let events = EventStore::open(&live_dir)?;
            let cache_dir = live_dir.parent().unwrap_or(&live_dir).join(".lmh-wechat-cache");
            let state_path = live_dir.parent().unwrap_or(&live_dir).join("wechat_sync_cursors.json");
            let mut syncer = local_message_hub_rs::wechat_local_sync::WeChatLocalSyncer::new(wx_cfg, cache_dir, state_path)
                .map_err(io::Error::other)?;
            let stats = syncer.sync_once(&events).map_err(io::Error::other)?;
            println!(
                "{}",
                json!({
                    "account": stats.account,
                    "sessions_checked": stats.sessions_checked,
                    "sessions_changed": stats.sessions_changed,
                    "created": stats.created,
                    "revised": stats.revised,
                    "unchanged": stats.unchanged
                })
            );
        }
        _ => return Err(invalid(USAGE).into()),
    }
    Ok(())
}

fn main() {
    if let Err(error) = run(std::env::args_os().skip(1)) {
        eprintln!("local-message-hub-rs: {error}");
        std::process::exit(1);
    }
}
