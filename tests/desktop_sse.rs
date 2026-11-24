#![cfg(target_os = "linux")]

use local_message_hub_rs::{
    desktop_client::{Error, journal::{Client, Wait}},
    desktop_sdk::{Launch, Mode},
    events::{EventKind, EventStore, IngestRequest, NormalizedMessage},
    plugins::{AccountScope, generate_token},
};
use serde_json::json;
use std::{
    fs::{self, OpenOptions},
    io::{BufRead, BufReader, Write},
    os::unix::fs::{OpenOptionsExt, PermissionsExt},
    path::Path,
    process::{Child, Command, Stdio},
    sync::{Arc, atomic::{AtomicBool, Ordering}, mpsc},
    thread,
    time::{Duration, Instant},
};
use zeroize::Zeroizing;

struct Supervisor(Child);
impl Drop for Supervisor {
    fn drop(&mut self) {
        self.0.stdin.take(); // Owner EOF requests orderly drain.
        let deadline = Instant::now() + Duration::from_secs(4);
        loop {
            if matches!(self.0.try_wait(), Ok(Some(_))) { return; }
            if Instant::now() >= deadline {
                let _ = self.0.kill();
                let _ = self.0.wait();
                return;
            }
            thread::sleep(Duration::from_millis(20));
        }
    }
}

fn setup(launch: &Launch) -> (Supervisor, Vec<u8>) {
    let mut task = Supervisor(Command::new(launch.executable()).args(launch.args())
        .stdin(Stdio::piped()).stdout(Stdio::piped()).spawn().unwrap());
    let mut output = BufReader::new(task.0.stdout.take().unwrap());
    let (tx, rx) = mpsc::channel();
    thread::spawn(move || {
        let mut line = Vec::new();
        output.read_until(b'\n', &mut line).unwrap();
        let _ = tx.send(line);
        // Retain output while the supervisor may still publish its stop frame.
        let _ = output.read_until(b'\n', &mut Vec::new());
    });
    let frame = rx.recv_timeout(Duration::from_secs(8)).unwrap();
    (task, frame)
}

fn seed(store: &EventStore, account: &str, id: &str, request: &str, text: &str) {
    store.ingest("sse.synthetic", IngestRequest { idempotency_key: request.into(), messages: vec![
        NormalizedMessage { platform: "wechat".into(), account_id: account.into(), conversation_id: "room".into(),
            message_id: id.into(), title: None, sender_id: None, sender_name: None,
            timestamp: 1720000000, is_self: false, text: Some(text.into()) }
    ] }).unwrap();
}

#[test]
fn managed_sse_wait_applies_at_most_one_metadata_event_and_keeps_idle_or_old_cursor() {
    let tmp = tempfile::tempdir().unwrap();
    let archive = tmp.path().join("archive");
    fs::create_dir(&archive).unwrap();
    fs::set_permissions(&archive, fs::Permissions::from_mode(0o700)).unwrap();
    let store = EventStore::init(&archive).unwrap();
    let grants = tmp.path().join("grants.json");
    let (token, hash) = generate_token().unwrap();
    let (no_events, no_events_hash) = generate_token().unwrap();
    let grant = |id, token_sha256, permissions| json!({"id":id,"token_sha256":token_sha256,
        "kind":"desktop","permissions":permissions,"account_scope":{"platform":"wechat","account_id":"own"}});
    let config = json!({"version":1,"grants":[grant("sse.own",hash,json!(["events.read"])),
        grant("sse.no-event",no_events_hash,json!(["conversations.read"]))]});
    let mut file = OpenOptions::new().write(true).create_new(true).mode(0o600).open(&grants).unwrap();
    file.write_all(config.to_string().as_bytes()).unwrap();
    drop(file);
    let bin = Path::new(env!("CARGO_BIN_EXE_local-message-hub-rs"));
    let launch = Launch::new(bin, Mode::NativeLive, &archive, &grants).unwrap();
    let (_supervisor, frame) = setup(&launch);
    let scope = || AccountScope { platform: "wechat".into(), account_id: "own".into() };

    let mut second = Client::connect(launch.accept_ready(&frame).unwrap(), Zeroizing::new(token.clone()),
        "sse.own".into(), scope(), |_| true).unwrap();
    let alive = Arc::new(AtomicBool::new(true));
    let supervisor_current = Arc::clone(&alive);
    let connection = launch.accept_ready(&frame).unwrap();
    let backend_pid = connection.backend_pid();
    let mut reader = Client::connect(connection, Zeroizing::new(token),
        "sse.own".into(), scope(), move |pid| pid == backend_pid && supervisor_current.load(Ordering::SeqCst)).unwrap();
    assert!(matches!(Client::connect(launch.accept_ready(&frame).unwrap(), Zeroizing::new(no_events),
        "sse.no-event".into(), scope(), |_| true), Err(Error::Unsupported)));
    assert!(matches!(Client::connect(launch.accept_ready(&frame).unwrap(), Zeroizing::new("f".repeat(64)),
        "sse.own".into(), scope(), |_| true), Err(Error::Status(401))));

    let initial = reader.checkpoint().unwrap();
    let before = initial.cursor().as_str().to_string();
    assert!(matches!(second.wait_one(initial.cursor()), Err(Error::Input)));
    seed(&store, "other", "same", "sse-foreign", "foreign body");
    seed(&store, "own", "same", "sse-first", "first body");
    seed(&store, "own", "same", "sse-revision", "revision body");
    let Wait::Item { event, next_cursor } = reader.wait_one(initial.cursor()).unwrap() else { panic!("expected item") };
    assert_eq!((event.event_id, event.kind, event.message_id.as_str()), (1, EventKind::Created, "same"));
    assert_eq!(initial.cursor().as_str(), before);
    let duplicate = reader.replay(initial.cursor(), 2).unwrap();
    assert_eq!(duplicate.items.iter().map(|v| v.event_id).collect::<Vec<_>>(), [1, 2]);
    let Wait::Item { event, next_cursor: latest } = reader.wait_one(&next_cursor).unwrap() else { panic!("expected revision") };
    assert_eq!((event.event_id, event.kind, event.message_id.as_str()), (2, EventKind::Revised, "same"));
    let saved = latest.as_str().to_string();
    let timer = Instant::now();
    assert!(matches!(reader.wait_one(&latest), Ok(Wait::Idle)));
    assert!(timer.elapsed() >= Duration::from_millis(2700));
    assert!(timer.elapsed() < Duration::from_secs(6));
    assert_eq!(latest.as_str(), saved);
    assert!(reader.replay(&latest, 2).unwrap().items.is_empty());

    alive.store(false, Ordering::SeqCst);
    assert!(matches!(reader.wait_one(&latest), Err(Error::Stale)));
    assert_eq!(latest.as_str(), saved);
}
