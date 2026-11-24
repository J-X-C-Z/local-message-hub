//! Actual managed Desktop client gate. The fixture writer is in-process; all
//! observations/changes by Desktop use public typed loopback clients.
#![cfg(target_os = "linux")]

use local_message_hub_rs::{
    desktop_client::{
        self, Client, Search, Window, imports, journal, metadata,
        text_export::{TextFilters, TextFormat},
    },
    desktop_sdk::{Connection, DesktopGrant, GrantOptions, Launch, Mode},
    events::{EventKind, EventStore, IngestRequest, NormalizedMessage},
    plugins::{AccountScope, Plugins, generate_token},
};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    cell::RefCell,
    fs,
    io::{BufRead, BufReader, Read, Write},
    os::unix::fs::{DirBuilderExt, OpenOptionsExt, PermissionsExt},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::mpsc::{Receiver, sync_channel},
    thread,
    time::{Duration, Instant},
};
use zeroize::Zeroizing;

const WAIT: Duration = Duration::from_secs(8);
const ROOM: &str = "fixture-room";
const MAIN: &str = "fixture-a";
const OTHER: &str = "fixture-b";
const LOOKUP: &str = "literal %_ café";
const READER: &str = "fixture.reader";
const EDITOR: &str = "fixture.editor";
const PEER: &str = "fixture.peer";

fn scope(account: &str) -> AccountScope {
    AccountScope { platform: "wechat".into(), account_id: account.into() }
}

fn fixture_token() -> (Zeroizing<String>, String) {
    let (token, digest) = generate_token().expect("fixture secrets");
    (Zeroizing::new(token), digest)
}

fn write_grants(
    root: &Path,
    reader: &DesktopGrant,
    editor_hash: String,
    peer_hash: String,
) -> PathBuf {
    let generated = reader.write_new(root, "reader.json").expect("generated reader");
    let reader_config: Value = serde_json::from_slice(&fs::read(generated).unwrap()).unwrap();
    let installed = |id, account, digest: String| json!({
        "id": id, "kind": "desktop", "account_scope": scope(account),
        "token_sha256": digest, "permissions": [
            "conversations.read", "messages.read", "conversations.metadata.write", "events.read"
        ]
    });
    let registry = json!({"version":1,"grants":[
        reader_config["grants"][0],
        installed(EDITOR, MAIN, editor_hash),
        installed(PEER, OTHER, peer_hash)
    ]});
    let bytes = serde_json::to_vec(&registry).unwrap();
    Plugins::parse(&bytes).expect("exact managed fixture registry");
    let path = root.join("registry.json");
    fs::OpenOptions::new().create_new(true).write(true).mode(0o600)
        .open(&path).unwrap().write_all(&bytes).unwrap();
    path
}

struct Supervisor {
    child: RefCell<Child>,
    frames: Receiver<Vec<u8>>,
    launch: Launch,
    ready: Vec<u8>,
    backend: u32,
}

impl Supervisor {
    fn start(binary: &Path, root: &Path, archive: &Path, grants: &Path) -> Self {
        let launch = Launch::new(binary, Mode::NativeLive, archive, grants).unwrap();
        let mut child = Command::new(launch.executable()).args(launch.args())
            .current_dir(root).env_clear().env("HOME", root).env("TMPDIR", root)
            .stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null())
            .spawn().expect("owned fixture supervisor");
        let (send, frames) = sync_channel(2);
        let stdout = child.stdout.take().unwrap();
        thread::spawn(move || {
            let mut input = BufReader::new(stdout);
            loop {
                let mut frame = Vec::new();
                let result = (&mut input).take(1025).read_until(b'\n', &mut frame);
                if !matches!(result, Ok(1..=1024)) || !frame.ends_with(b"\n")
                    || send.send(frame).is_err() { break; }
            }
        });
        // Own the child before awaiting readiness so any error also cleans up.
        let mut guard = Self {
            child: RefCell::new(child), frames, launch, ready: Vec::new(), backend: 0,
        };
        guard.ready = guard.frames.recv_timeout(WAIT).expect("owned readiness");
        guard.backend = guard.connection().backend_pid();
        guard
    }

    fn connection(&self) -> Connection { self.launch.accept_ready(&self.ready).unwrap() }

    fn current(&self, pid: u32) -> bool {
        // Identity is the owned supervisor and exact frame generation, not a PID lookup.
        pid == self.backend && self.child.borrow_mut().try_wait().is_ok_and(|r| r.is_none())
    }

    fn stop(&self) {
        let mut child = self.child.borrow_mut();
        child.stdin.take().unwrap().write_all(b"stop\n").unwrap();
        let done: Value = serde_json::from_slice(&self.frames.recv_timeout(WAIT).unwrap()).unwrap();
        assert_eq!(done, json!({"event":"stopped","forced":false}));
        let deadline = Instant::now() + WAIT;
        loop {
            if let Some(status) = child.try_wait().unwrap() {
                assert!(status.success());
                return;
            }
            assert!(Instant::now() < deadline, "supervisor failed to stop");
            thread::sleep(Duration::from_millis(10));
        }
    }
}

impl Drop for Supervisor {
    fn drop(&mut self) {
        let child = self.child.get_mut();
        // Closing custody pipe lets the native supervisor stop its own children.
        drop(child.stdin.take());
        let deadline = Instant::now() + WAIT;
        while matches!(child.try_wait(), Ok(None)) && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(10));
        }
        if matches!(child.try_wait(), Ok(None)) { let _ = child.kill(); }
        let _ = child.wait();
    }
}

fn candidate_binary() -> PathBuf {
    const CARGO_BINARY: &str = env!("CARGO_BIN_EXE_local-message-hub-rs");
    let supplied = std::env::var_os("LMH_MANAGED_TEST_BINARY");
    let digest = std::env::var_os("LMH_MANAGED_TEST_SHA256");
    let (path, expected) = match (supplied, digest) {
        (None, None) => return Path::new(CARGO_BINARY).canonicalize().unwrap(),
        (Some(path), Some(digest)) => (PathBuf::from(path), digest),
        _ => panic!("configure both the trusted fixture binary and its independent digest"),
    };
    assert!(path.is_absolute());
    let expected = expected.to_str().unwrap();
    assert!(expected.len() == 64 && expected.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)));
    let path = path.canonicalize().unwrap();
    let mut handle = fs::File::open(&path).unwrap();
    assert!(handle.metadata().unwrap().is_file());
    let mut magic = [0; 4];
    handle.read_exact(&mut magic).unwrap();
    assert_eq!(&magic, b"\x7fELF");
    let mut hasher = Sha256::new();
    hasher.update(magic);
    let mut buf = [0; 65536];
    loop {
        let read = handle.read(&mut buf).unwrap();
        if read == 0 { break; }
        hasher.update(&buf[..read]);
    }
    assert_eq!(hex::encode(hasher.finalize()), expected, "independent fixture executable pin");
    path
}

fn message(account: &str, room: &str, id: &str, offset: i64, text: &str) -> NormalizedMessage {
    NormalizedMessage {
        platform: "wechat".into(), account_id: account.into(), conversation_id: room.into(),
        message_id: id.into(), title: Some(format!("合成对话 {room}")), sender_id: Some("synthetic-sender".into()),
        sender_name: Some("Synthetic sender".into()), timestamp: 1_700_000_000 + offset,
        is_self: false, text: Some(text.into()),
    }
}

fn ingest(store: &EventStore, idempotency_key: &str, rows: Vec<NormalizedMessage>) {
    let count = rows.len();
    let receipt = store.ingest("fixture.normalized", IngestRequest {
        idempotency_key: idempotency_key.into(), messages: rows,
    }).unwrap();
    assert_eq!(receipt.created, count);
    assert_eq!(receipt.revised + receipt.unchanged, 0);
}

fn editor<'a>(proc: &'a Supervisor, token: &str, id: &str, account: &str) -> metadata::Client<'a> {
    metadata::Client::connect(proc.connection(), Zeroizing::new(token.to_owned()),
        id.into(), scope(account), |pid| proc.current(pid)).unwrap()
}

fn journal<'a>(proc: &'a Supervisor, token: &str, id: &str, account: &str) -> journal::Client<'a> {
    journal::Client::connect(proc.connection(), Zeroizing::new(token.to_owned()),
        id.into(), scope(account), |pid| proc.current(pid)).unwrap()
}

#[test]
fn managed_native_client_search_edit_and_resume_without_foreign_scope() {
    let fixture = tempfile::Builder::new().permissions(fs::Permissions::from_mode(0o700)).tempdir().unwrap();
    let root = fixture.path().canonicalize().unwrap();
    let archive = root.join("native");
    fs::DirBuilder::new().mode(0o700).create(&archive).unwrap();
    let store = EventStore::init(&archive).unwrap();
    let native = candidate_binary();
    let reader = DesktopGrant::generate(Mode::NativeLive, READER, &scope(MAIN), GrantOptions::default()).unwrap();
    let (editor_key, editor_hash) = fixture_token();
    let (peer_key, peer_hash) = fixture_token();
    let grants = write_grants(&root, &reader, editor_hash, peer_hash);

    ingest(&store, "initial-main", vec![
        message(MAIN, ROOM, "before", 1, "before"),
        message(MAIN, ROOM, "shared-id", 2, LOOKUP),
        message(MAIN, ROOM, "after", 3, "after"),
    ]);
    ingest(&store, "initial-other", vec![
        message(OTHER, ROOM, "shared-id", 2, "literal %_ café foreign account"),
        message(OTHER, "other-only", "not-visible", 4, "literal %_ café foreign scope"),
    ]);

    let first = Supervisor::start(&native, &root, &archive, &grants);
    assert!(matches!(
        Client::connect(first.connection(), &reader, READER.into(), scope(OTHER), |pid| first.current(pid)),
        Err(desktop_client::Error::Identity)
    ));
    assert!(matches!(
        metadata::Client::connect(first.connection(), reader.with_token(|key| Zeroizing::new(key.to_owned())),
            READER.into(), scope(MAIN), |pid| first.current(pid)),
        Err(metadata::Error::Session(desktop_client::Error::Unsupported))
    ));

    let mut navigation = Client::connect(first.connection(), &reader, READER.into(), scope(MAIN),
        |pid| first.current(pid)).unwrap();
    assert_eq!(navigation.conversations(10, 0).unwrap().total, 1);
    assert!(navigation.lookup_conversation("other-only").unwrap().is_none());
    let found = navigation.search_messages(Search {
        text: LOOKUP, conversation_id: None, cursor: None, limit: 10,
    }).unwrap();
    assert_eq!(found.total, 1);
    assert!(!found.has_more);
    let hit = &found.items[0];
    assert_eq!((hit.message.id.as_str(), hit.message.account_id.as_str()), ("shared-id", MAIN));
    assert_eq!(hit.text_preview.as_deref(), Some(LOOKUP));
    let context = navigation.message_context(&hit.message.conversation_id, &hit.message.id,
        Window { before: 1, after: 1 }).unwrap();
    assert_eq!(context.items.iter().map(|item| item.message.id.as_str()).collect::<Vec<_>>(),
        ["before", "shared-id", "after"]);
    assert_eq!(context.target_index, 1);
    assert_eq!(context.items[context.target_index].text.as_deref(), Some(LOOKUP));

    let mut edit = editor(&first, &editor_key, EDITOR, MAIN);
    let mut second_editor = editor(&first, &editor_key, EDITOR, MAIN);
    let mut foreign_editor = editor(&first, &peer_key, PEER, OTHER);
    let blank = edit.conversation(ROOM).unwrap();
    let stale = second_editor.conversation(ROOM).unwrap();
    let foreign_blank = foreign_editor.conversation(ROOM).unwrap();
    assert_eq!((blank.revision(), stale.revision(), foreign_blank.revision()), (0, 0, 0));
    assert!(matches!(edit.conversation("other-only"), Err(metadata::Error::Missing)));
    assert!(matches!(edit.save(&foreign_blank, metadata::Patch {
        note: Some("foreign handle"), ..Default::default()
    }), Err(metadata::Error::WrongSession)));
    let tags = [" synthetic ".to_owned(), "synthetic".to_owned()];
    let saved = match edit.save(&blank, metadata::Patch {
        note: Some("合成备注"), tags: Some(&tags), archived: Some(true),
    }).unwrap() {
        metadata::Saved::Changed(state) => state,
        metadata::Saved::Unchanged(_) => panic!("explicit initial edit failed to advance"),
    };
    assert_eq!((saved.revision(), saved.note(), saved.archived()), (1, "合成备注", true));
    assert_eq!(saved.tags(), ["synthetic"]);
    assert!(matches!(second_editor.save(&stale, metadata::Patch {
        note: Some("stale overwrite"), ..Default::default()
    }), Err(metadata::Error::Conflict)));
    assert!(matches!(edit.save(&saved, metadata::Patch {
        note: Some("合成备注"), ..Default::default()
    }).unwrap(), metadata::Saved::Unchanged(state) if state.revision() == 1));
    assert_eq!(foreign_editor.conversation(ROOM).unwrap().note(), "");

    let mut events = journal(&first, &editor_key, EDITOR, MAIN);
    let head = events.checkpoint().unwrap();
    let saved_cursor = head.cursor().as_str().to_owned();
    let foreign_cursor = journal(&first, &peer_key, PEER, OTHER)
        .checkpoint().unwrap().cursor().as_str().to_owned();
    assert!(matches!(events.restore_cursor(&head, &foreign_cursor), Err(desktop_client::Error::Input)));
    let at_head = events.replay(head.cursor(), 2).unwrap();
    assert!(at_head.items.is_empty() && !at_head.has_more);

    first.stop();
    assert!(matches!(navigation.conversations(1, 0), Err(desktop_client::Error::Stale)));
    ingest(&store, "between-main", vec![message(MAIN, ROOM, "gap-a", 11, "private gap one")]);
    ingest(&store, "between-foreign", vec![message(OTHER, ROOM, "foreign-gap", 12, "foreign gap")]);
    ingest(&store, "between-main-2", vec![message(MAIN, ROOM, "gap-b", 13, "private gap two")]);

    let next = Supervisor::start(&native, &root, &archive, &grants);
    let mut fresh_navigation = navigation.reconnect(next.connection(), |pid| next.current(pid)).unwrap();
    assert_eq!(fresh_navigation.conversations(10, 0).unwrap().total, 1);
    let mut fresh_editor = edit.reconnect(next.connection(), |pid| next.current(pid)).unwrap();
    assert!(matches!(fresh_editor.save(&saved, metadata::Patch {
        note: Some("retry forbidden"), ..Default::default()
    }), Err(metadata::Error::WrongSession)));
    let current = fresh_editor.conversation(ROOM).unwrap();
    assert_eq!((current.revision(), current.note(), current.archived()), (1, "合成备注", true));
    assert_eq!(current.tags(), ["synthetic"]);

    let mut resumed = journal(&next, &editor_key, EDITOR, MAIN);
    let current_head = resumed.checkpoint().unwrap();
    assert!(matches!(resumed.replay(head.cursor(), 1), Err(desktop_client::Error::Input)));
    assert!(matches!(resumed.restore_cursor(&current_head, &foreign_cursor), Err(desktop_client::Error::Input)));
    let resume = resumed.restore_cursor(&current_head, &saved_cursor).unwrap();
    let page = resumed.replay(&resume, 1).unwrap();
    assert!(page.has_more);
    assert_eq!(page.items.len(), 1);
    assert_eq!((page.items[0].message_id.as_str(), &page.items[0].kind), ("gap-a", &EventKind::Created));
    let last = resumed.replay(&page.next_cursor, 1).unwrap();
    assert!(!last.has_more);
    assert_eq!(last.items.len(), 1);
    assert_eq!((last.items[0].message_id.as_str(), &last.items[0].kind), ("gap-b", &EventKind::Created));
    assert_eq!(last.items[0].event_id, page.items[0].event_id + 1);
    assert_eq!(last.next_cursor.as_str(), current_head.cursor().as_str());
    assert!(resumed.replay(&last.next_cursor, 1).unwrap().items.is_empty());
    next.stop();
}

#[test]
fn managed_campus_reader_and_editor_are_separately_scoped_and_versioned() {
    use local_message_hub_rs::{campus, desktop_client::campus::{CampusClient, Filter, StateChanges}};
    let binary=candidate_binary();
    let root=tempfile::tempdir().unwrap();
    let archive=root.path().join("campus-native");
    fs::DirBuilder::new().mode(0o700).create(&archive).unwrap();
    EventStore::init(&archive).unwrap();
    let same_url="https://www.sysu.edu.cn/info/9999/88.htm";
    let foreign_url="https://www.sysu.edu.cn/info/9999/99.htm";
    let source=root.path().join("fixture-campus-load.json");
    let seed=|profile:&str,articles:Vec<(&str,&str)>| {
        let articles=articles.into_iter().map(|(url,title)|json!({
            "url":url,"title":title,"published_at":"2026-11-23","summary":"Synthetic notice",
            "source_id":"fixture","source_name":"Fixture","category":"teaching",
            "content":format!("Body of {title}")
        })).collect::<Vec<_>>();
        fs::write(&source,serde_json::to_vec(&json!({
            "version":1,"profile":profile,"articles":articles
        })).unwrap()).unwrap();
        let preview=serde_json::to_value(campus::preview(&source,&archive,profile).unwrap()).unwrap();
        campus::commit(&source,&archive,profile,preview["confirmation"].as_str().unwrap()).unwrap();
    };
    seed("primary",vec![(same_url,"Visible synthetic %_")]);
    seed("secondary",vec![(same_url,"Foreign duplicate"),(foreign_url,"Foreign only")]);
    let (reader, reader_hash)=fixture_token();
    let (editor, editor_hash)=fixture_token();
    let (misbound, misbound_hash)=fixture_token();
    let grants=json!({"version":1,"grants":[
        {"id":"campus.read","kind":"campus-reader","campus_profile":"primary",
            "permissions":["campus.read"],"token_sha256":reader_hash},
        {"id":"campus.edit","kind":"campus-editor","campus_profile":"primary",
            "permissions":["campus.read","campus.bookmarks.write"],"token_sha256":editor_hash},
        {"id":"campus.foreign","kind":"campus-reader","campus_profile":"secondary",
            "permissions":["campus.read"],"token_sha256":misbound_hash}
    ]});
    let path=root.path().join("campus-registry.json");
    let bytes=serde_json::to_vec(&grants).unwrap();
    Plugins::parse(&bytes).unwrap();
    fs::OpenOptions::new().create_new(true).write(true).mode(0o600).open(&path)
        .unwrap().write_all(&bytes).unwrap();
    let child=Supervisor::start(&binary,root.path(),&archive,&path);
    assert!(matches!(CampusClient::connect(child.connection(),misbound,"campus.foreign","primary",
        |pid|child.current(pid)),Err(desktop_client::Error::Identity)));
    let mut read=CampusClient::connect(child.connection(),reader,"campus.read","primary",
        |pid|child.current(pid)).unwrap();
    let mut edit=CampusClient::connect(child.connection(),editor,"campus.edit","primary",
        |pid|child.current(pid)).unwrap();

    assert!(!read.can_edit_state());
    assert!(edit.can_edit_state());
    let list=read.articles(Filter{text:Some("%_"),limit:2,..Filter::default()}).unwrap();
    assert_eq!(list.total,1);
    assert_eq!(list.items[0].title,"Visible synthetic %_");
    let id=list.items[0].id.clone();
    let digest=read.digest("2026-11-23",2).unwrap();
    assert_eq!(digest.total,1);
    assert_eq!(digest.items[0].id,id);
    assert_eq!(read.article(&id).unwrap().content,"Body of Visible synthetic %_");
    let foreign_id=hex::encode(Sha256::digest(foreign_url.as_bytes()));
    assert!(matches!(read.article(&foreign_id),Err(desktop_client::Error::Status(404))));
    let state=read.article_state(&id).unwrap();
    assert_eq!(state.revision,0);
    assert!(!state.bookmarked && !state.read);
    let change=StateChanges{expected_revision:0,bookmarked:Some(true),read:Some(true)};
    assert!(matches!(read.change_article_state(&id,change),Err(desktop_client::Error::Unsupported)));
    assert_eq!(read.article_state(&id).unwrap().revision,0);
    let edited=edit.change_article_state(&id,change).unwrap();
    assert!(edited.read && edited.bookmarked);
    assert_eq!(edited.revision,1);
    assert!(matches!(edit.change_article_state(&id,change),Err(desktop_client::Error::Status(409))));
    let no_op=StateChanges{expected_revision:1,bookmarked:None,read:Some(true)};
    assert_eq!(edit.change_article_state(&id,no_op).unwrap().revision,1);
    assert_eq!(read.article_state(&id).unwrap().revision,1);
    child.stop();
    assert!(matches!(read.article(&id),Err(desktop_client::Error::Stale)));
}


// A separate, explicitly provisioned importer never receives read/export
// capability. A generated baseline reader and an opt-in export reader use
// the actual launch/typed capability handshake, not a raw transport helper.
#[test]
fn managed_detached_text_import_search_export_and_receipt_recovery() {
    const IMPORTER: &str = "fixture.importer";
    const FOREIGN_IMPORTER: &str = "fixture.foreign-importer";
    const EXPORTER: &str = "fixture.exporter";
    const NEEDLE: &str = "selected literal %_ café";
    const BIG_ROOM: &str = "fixture-large-export";

    let fixture = tempfile::Builder::new().permissions(fs::Permissions::from_mode(0o700))
        .tempdir().unwrap();
    let root = fixture.path().canonicalize().unwrap();
    let archive = root.join("native");
    fs::DirBuilder::new().mode(0o700).create(&archive).unwrap();
    let store = EventStore::init(&archive).unwrap();
    let native = candidate_binary();

    let reader = DesktopGrant::generate(Mode::NativeLive, READER, &scope(MAIN),
        GrantOptions::default()).unwrap();
    let exporter = DesktopGrant::generate(Mode::NativeLive, EXPORTER, &scope(MAIN),
        GrantOptions { export_text: true, ..Default::default() }).unwrap();
    let installed = |grant: &DesktopGrant, name: &str| -> Value {
        let data: Value = serde_json::from_slice(
            &fs::read(grant.write_new(&root, name).unwrap()).unwrap()).unwrap();
        data["grants"][0].clone()
    };
    let operator = |id, account, digest| json!({
        "id":id, "kind":"desktop", "account_scope":scope(account),
        "permissions":["imports.local.manage"], "token_sha256":digest
    });
    let (import_token, import_digest) = fixture_token();
    let (foreign_token, foreign_digest) = fixture_token();
    let registry = serde_json::to_vec(&json!({"version":1,"grants":[
        installed(&reader, "reader.json"),
        installed(&exporter, "exporter.json"),
        operator(IMPORTER, MAIN, import_digest),
        operator(FOREIGN_IMPORTER, OTHER, foreign_digest)
    ]})).unwrap();
    Plugins::parse(&registry).unwrap();
    let grants = root.join("private-grants.json");
    fs::OpenOptions::new().create_new(true).write(true).mode(0o600)
        .open(&grants).unwrap().write_all(&registry).unwrap();

    // Same archive keys and search text under an independent foreign account.
    // This seed exercises boundaries without requiring a source/provider.
    ingest(&store, "other-account-seed", vec![
        message(OTHER, ROOM, "shared-id", 2, &format!("{NEEDLE} foreign canary")),
    ]);
    let rows = |account, selected| json!([
        {"platform":"wechat","account_id":account,"conversation_id":ROOM,
         "conversation_title":"Exact synthetic import","id":"shared-id",
         "sender_id":"synthetic-sender","type":"text","timestamp":1_700_000_002,
         "is_self":false,"text":selected},
        {"platform":"wechat","account_id":account,"conversation_id":ROOM,
         "conversation_title":"Exact synthetic import","id":"excluded-by-filter",
         "sender_id":"synthetic-self","type":"text","timestamp":1_700_000_003,
         "is_self":true,"text":"owned row outside selected text"}
    ]);
    let exact = serde_json::to_vec(&rows(MAIN, NEEDLE)).unwrap();
    let changed = serde_json::to_vec(&rows(MAIN, "unapproved different bytes")).unwrap();
    let foreign = serde_json::to_vec(&rows(OTHER, NEEDLE)).unwrap();
    let key = imports::RequestId::parse("fixture-selected-import").unwrap();
    let selected = || TextFilters {
        conversation_id: ROOM, format: TextFormat::Json, keyword: Some(NEEDLE),
        message_type: Some("text"), start: Some("2023-11-15"), end: Some("2023-11-15"),
    };

    let first = Supervisor::start(&native, &root, &archive, &grants);
    let mut importer = imports::Client::connect(first.connection(),
        Zeroizing::new(import_token.to_string()), IMPORTER.into(), scope(MAIN),
        |pid| first.current(pid)).unwrap();
    let mut peer = imports::Client::connect(first.connection(),
        Zeroizing::new(foreign_token.to_string()), FOREIGN_IMPORTER.into(), scope(OTHER),
        |pid| first.current(pid)).unwrap();
    let mut index = Client::connect(first.connection(), &reader, READER.into(), scope(MAIN),
        |pid| first.current(pid)).unwrap();
    assert!(matches!(
        imports::Client::connect(first.connection(), reader.with_token(|tok| Zeroizing::new(tok.to_owned())),
            READER.into(), scope(MAIN), |pid| first.current(pid)),
        Err(imports::Error::Session(desktop_client::Error::Unsupported))
    ));
    assert!(matches!(
        Client::connect_installed_reader(first.connection(), Zeroizing::new(import_token.to_string()),
            IMPORTER.into(), scope(MAIN), |pid| first.current(pid)),
        Err(desktop_client::Error::Identity)
    ));
    assert!(matches!(index.download_filtered_text_export(selected()), Err(desktop_client::Error::Unsupported)));

    assert!(importer.supports_stock_json_preflight() && importer.supports_stock_json_stage());
    let checked = importer.preflight_stock_json(&exact).unwrap();
    assert_eq!((checked.total, checked.valid, checked.invalid), (2, 2, 0));
    assert!(checked.errors.is_empty());
    assert!(importer.preflight_stock_json(&foreign).is_err());
    assert!(importer.list(5, None).unwrap().items().is_empty());

    // The typed native preview uses JSON/base64 rather than the stock
    // octet-stream route. A separate account keeps the stock job assertions
    // below unchanged while verifying replay, scope, and no ingest.
    let preview_key = imports::RequestId::parse("typed-preview-fixture._-").unwrap();
    let prepared_preview = peer.preview(&preview_key, &foreign).unwrap();
    assert_eq!((prepared_preview.summary().phase(), prepared_preview.summary().rows()),
        (imports::Phase::Prepared, 2));
    let preview_id = prepared_preview.summary().job_id();
    assert!(matches!(importer.status(preview_id), Err(imports::Error::Missing)));
    assert!(peer.preview(&preview_key, &[]).is_err());
    assert_eq!(peer.preview(&preview_key, &foreign).unwrap().summary().job_id().as_str(),
        preview_id.as_str());
    assert_eq!(peer.cancel(&prepared_preview).unwrap().summary().phase(), imports::Phase::Cancelled);

    // Discard the staging response as a caller would if it had not retained
    // the returned ID. Explicitly recover from the same exact bytes and key.
    drop(importer.stage_stock_json(&key, &exact).unwrap());
    let prepared = importer.stage_stock_json(&key, &exact).unwrap();
    assert_eq!((prepared.summary().phase(), prepared.summary().rows()), (imports::Phase::Prepared, 2));
    let job = prepared.summary().job_id().clone();
    let reviewed = Zeroizing::new(prepared.confirmation_digest().to_owned());
    assert!(matches!(importer.stage_stock_json(&key, &changed), Err(imports::Error::Conflict)));
    assert!(matches!(peer.status(&job), Err(imports::Error::Missing)));
    assert!(matches!(prepared.approve(&"0".repeat(64)), Err(imports::Error::Approval)));
    assert_eq!(index.conversations(10, 0).unwrap().total, 0);
    let approval = importer.status(&job).unwrap().approve(&reviewed).unwrap();

    first.stop();
    assert!(matches!(importer.commit(&approval), Err(imports::Error::Session(desktop_client::Error::Stale))));
    let second = Supervisor::start(&native, &root, &archive, &grants);
    let mut importer = importer.reconnect(second.connection(), |pid| second.current(pid)).unwrap();
    assert_eq!(importer.status(&job).unwrap().summary().phase(), imports::Phase::Prepared);
    // A local synthetic approval precedes the only actual apply. Intentionally
    // ignore the response and prove durable recovery by read-only status.
    drop(importer.commit(&approval).unwrap());
    second.stop();

    let third = Supervisor::start(&native, &root, &archive, &grants);
    let mut importer = importer.reconnect(third.connection(), |pid| third.current(pid)).unwrap();
    let recovered = importer.status(&job).unwrap();
    let receipt = imports::Receipt { created: 2, unchanged: 0 };
    assert_eq!(recovered.summary().phase(), imports::Phase::Committed);
    assert_eq!(recovered.summary().receipt(), Some(receipt));
    assert_eq!(importer.commit(&approval).unwrap().summary().receipt(), Some(receipt));
    assert_eq!(importer.list(5, None).unwrap().items().len(), 1);

    let mut index = index.reconnect(third.connection(), |pid| third.current(pid)).unwrap();
    let hit = index.search_messages(Search {
        text: NEEDLE, conversation_id: None, cursor: None, limit: 10,
    }).unwrap();
    assert_eq!(hit.total, 1);
    let exact_hit = &hit.items[0].message;
    assert_eq!((exact_hit.account_id.as_str(), exact_hit.id.as_str()), (MAIN, "shared-id"));
    let context = index.message_context(&exact_hit.conversation_id, &exact_hit.id,
        Window {before: 0, after: 1}).unwrap();
    assert_eq!(context.items[context.target_index].text.as_deref(), Some(NEEDLE));
    assert_eq!(context.items.len(), 2);

    let mut export = Client::connect(third.connection(), &exporter, EXPORTER.into(), scope(MAIN),
        |pid| third.current(pid)).unwrap();
    assert!(export.supports_filtered_text_export());
    let bounded = |client: &mut Client<'_>| {
        let bytes = client.download_filtered_text_export(selected()).unwrap();
        let rows: Vec<Value> = serde_json::from_slice(&bytes).unwrap();
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0]["id"], "shared-id");
        assert_eq!(rows[0]["account_id"], MAIN);
        assert_eq!(rows[0]["text"], NEEDLE);
    };
    bounded(&mut export);

    // 72 x 60 KiB exceeds the 4 MiB serializer ceiling while each seed
    // batch stays under 512 KiB. No truncated attachment may be returned.
    let long = "x".repeat(60 * 1024);
    for batch in 0..9 {
        ingest(&store, &format!("big-export-seed-{batch}"),
            (0..8).map(|item| message(MAIN, BIG_ROOM, &format!("big-{batch}-{item}"),
                10 + batch * 8 + item, &long)).collect());
    }
    assert!(matches!(export.download_filtered_text_export(TextFilters {
        conversation_id: BIG_ROOM, format: TextFormat::Json, keyword: None,
        message_type: None, start: None, end: None,
    }), Err(desktop_client::Error::Status(413))));
    bounded(&mut export);
    assert_eq!(fs::read(grants).unwrap(), registry);
    third.stop();
}

#[test]
fn managed_notification_preferences_pull_ack_and_reconnect() {
    use local_message_hub_rs::desktop_client::notifications::{
        Acknowledgment, Client as NotificationClient, Error, FilterMode, Filters, Pull, Style,
    };

    let binary = candidate_binary();
    let fixture = tempfile::Builder::new()
        .permissions(fs::Permissions::from_mode(0o700)).tempdir().unwrap();
    let root = fixture.path().canonicalize().unwrap();
    let archive = root.join("notification-native");
    fs::DirBuilder::new().mode(0o700).create(&archive).unwrap();
    let store = EventStore::init(&archive).unwrap();
    let (editor, editor_hash) = fixture_token();
    let (reader, reader_hash) = fixture_token();
    let (peer, peer_hash) = fixture_token();
    let grants = json!({"version":1,"grants":[
        {"id":"notification.edit","kind":"desktop","account_scope":scope(MAIN),
            "permissions":["notifications.read","notifications.write","conversations.read"],
            "token_sha256":editor_hash},
        {"id":"notification.read","kind":"desktop","account_scope":scope(MAIN),
            "permissions":["notifications.read"],"token_sha256":reader_hash},
        {"id":"notification.peer","kind":"desktop","account_scope":scope(OTHER),
            "permissions":["notifications.read","notifications.write","conversations.read"],
            "token_sha256":peer_hash}
    ]});
    let grants_path = root.join("notification-registry.json");
    let bytes = serde_json::to_vec(&grants).unwrap();
    Plugins::parse(&bytes).unwrap();
    fs::OpenOptions::new().create_new(true).write(true).mode(0o600)
        .open(&grants_path).unwrap().write_all(&bytes).unwrap();

    const MUTED: &str = "muted-room";
    const EXCLUDED: &str = "excluded-room";
    ingest(&store, "notification-history", vec![message(MAIN, ROOM, "old", 1, "old")]);
    let first = Supervisor::start(&binary, &root, &archive, &grants_path);
    assert!(matches!(NotificationClient::connect(first.connection(),
        Zeroizing::new(peer.to_string()), "notification.peer".into(), scope(MAIN),
        |pid| first.current(pid)), Err(Error::Session(desktop_client::Error::Identity))));
    let mut read = NotificationClient::connect(first.connection(), reader,
        "notification.read".into(), scope(MAIN), |pid| first.current(pid)).unwrap();
    let mut edit = NotificationClient::connect(first.connection(), editor,
        "notification.edit".into(), scope(MAIN), |pid| first.current(pid)).unwrap();
    let read_settings = read.preferences().unwrap();
    assert_eq!(read_settings.revision(), 0);
    assert!(!read.can_change_preferences() && !read.can_pull_and_ack());
    assert!(matches!(read.set_enabled(&read_settings, true),
        Err(Error::Session(desktop_client::Error::Unsupported))));
    assert!(matches!(read.pull(1), Err(Error::Session(desktop_client::Error::Unsupported))));
    assert!(edit.can_change_preferences() && edit.can_pull_and_ack()
        && edit.can_resolve_activation_target());

    let empty = edit.preferences().unwrap();
    assert_eq!(empty.revision(), 0);
    assert!(!empty.enabled());
    assert!(matches!(edit.pull(1).unwrap(), Pull::Disabled {revision: 0}));
    let filtered = edit.set_filters(&empty, Filters {
        mode: FilterMode::Allowlist, style: Style::Silent,
        muted_conversations: vec![MUTED.into()], allowed_conversations: vec![MUTED.into(), ROOM.into()],
    }).unwrap();
    assert!(!filtered.enabled());
    assert!(matches!(edit.set_enabled(&empty, true), Err(Error::RevisionConflict)));
    let enabled = edit.set_enabled(&filtered, true).unwrap();
    assert_eq!(enabled.revision(), 2);
    // Enabling starts at the current head, not previously ingested history.
    assert!(matches!(edit.pull(1).unwrap(), Pull::CaughtUp {revision: 2}));
    assert_eq!(read.preferences().unwrap().revision(), 0);

    // Foreign uses the same raw conversation/message IDs as the eligible row.
    ingest(&store, "notification-eligible", vec![
        message(MAIN, MUTED, "mute", 2, "muted takes precedence over allow"),
        message(OTHER, ROOM, "overlap", 3, "foreign"),
        message(MAIN, ROOM, "overlap", 4, "eligible"),
        message(MAIN, EXCLUDED, "empty-after-restart", 5, "excluded"),
    ]);
    let Pull::Pending(muted) = edit.pull(1).unwrap() else {panic!("muted scan must have a receipt");};
    assert!(muted.items().is_empty() && muted.has_more_after_ack());
    let Pull::Pending(still_muted) = edit.pull(1).unwrap() else {panic!("pull must not advance");};
    assert!(still_muted.items().is_empty());
    assert_eq!(edit.acknowledge(&muted).unwrap(), Acknowledgment::Advanced);
    assert_eq!(edit.acknowledge(&still_muted).unwrap(), Acknowledgment::AlreadyAcknowledged);

    let Pull::Pending(before_change) = edit.pull(1).unwrap() else {panic!("eligible item missing");};
    assert_eq!(before_change.items().len(), 1);
    assert_eq!(before_change.items()[0].conversation_id, ROOM);
    assert_eq!(before_change.items()[0].style, Style::Silent);
    let mut filters = enabled.filters();
    filters.style = Style::Normal;
    let changed = edit.set_filters(&enabled, filters).unwrap();
    assert_eq!(changed.revision(), 3);
    assert!(matches!(edit.acknowledge(&before_change), Err(Error::ReceiptStale)));

    let Pull::Pending(eligible) = edit.pull(1).unwrap() else {panic!("unacknowledged must replay");};
    assert_eq!(eligible.items().len(), 1);
    assert_eq!(eligible.items()[0].conversation_id, ROOM);
    assert_eq!(eligible.items()[0].style, Style::Normal);
    assert!(matches!(edit.resolve_activation_target(&eligible, EXCLUDED),
        Err(Error::Session(desktop_client::Error::Input))));
    let target = edit.resolve_activation_target(&eligible, ROOM).unwrap().unwrap();
    assert_eq!(target.platform, "wechat");
    assert_eq!(target.account_id, MAIN);
    assert_eq!(target.conversation_id, ROOM);
    let Pull::Pending(after_resolve) = edit.pull(1).unwrap() else {panic!("resolve must not advance");};
    assert_eq!(after_resolve.items().len(), 1);
    assert_eq!(after_resolve.items()[0].conversation_id, ROOM);
    assert_eq!(edit.acknowledge(&eligible).unwrap(), Acknowledgment::Advanced);

    let Pull::Pending(pre_restart) = edit.pull(1).unwrap() else {panic!("filtered item must advance by ACK");};
    assert!(pre_restart.items().is_empty() && !pre_restart.has_more_after_ack());
    first.stop();
    let second = Supervisor::start(&binary, &root, &archive, &grants_path);
    let mut restored = edit.reconnect(second.connection(), |pid| second.current(pid)).unwrap();
    assert!(matches!(restored.acknowledge(&pre_restart), Err(Error::ReceiptStale)));
    assert!(matches!(restored.set_enabled(&changed, false),
        Err(Error::Session(desktop_client::Error::Input))));
    let restored_policy = restored.preferences().unwrap();
    assert!(restored_policy.enabled());
    assert_eq!(restored_policy.revision(), 3);
    let Pull::Pending(recovered) = restored.pull(1).unwrap() else {panic!("durable ACK frontier missing");};
    assert!(recovered.items().is_empty() && !recovered.has_more_after_ack());
    assert_eq!(restored.acknowledge(&recovered).unwrap(), Acknowledgment::Advanced);
    assert!(matches!(restored.pull(1).unwrap(), Pull::CaughtUp {revision: 3}));
    second.stop();
}
