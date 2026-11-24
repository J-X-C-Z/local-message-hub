#![cfg(target_os = "linux")]

use local_message_hub_rs::{
    desktop_client::{Error as LoopbackError, transcription::{Client, Error, Status}},
    desktop_sdk::{Launch, Mode},
    events::{EventStore, IngestRequest, NormalizedMessage},
    plugins::{AccountScope, generate_token},
};
use serde_json::json;
use std::{
    cell::RefCell,
    fs::{self, OpenOptions},
    io::{BufRead, BufReader, Read, Write},
    os::unix::fs::{OpenOptionsExt, PermissionsExt},
    path::Path,
    process::{Child, Command, Stdio},
    sync::mpsc,
    thread,
    time::{Duration, Instant},
};
use zeroize::Zeroizing;

struct Supervisor(RefCell<Child>);
impl Drop for Supervisor {
    fn drop(&mut self) {
        let child = self.0.get_mut();
        drop(child.stdin.take());
        let deadline = Instant::now() + Duration::from_secs(5);
        while matches!(child.try_wait(), Ok(None)) && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(20));
        }
        if matches!(child.try_wait(), Ok(None)) { let _ = child.kill(); }
        let _ = child.wait();
    }
}

fn setup(launch: &Launch, root: &Path) -> (Supervisor, Vec<u8>) {
    let mut child = Command::new(launch.executable()).args(launch.args())
        .current_dir(root).env_clear().env("HOME", root).env("TMPDIR", root)
        .stdin(Stdio::piped()).stdout(Stdio::piped()).spawn().unwrap();
    let stdout = child.stdout.take().unwrap();
    let supervisor = Supervisor(RefCell::new(child));
    let (tx, rx) = mpsc::channel();
    thread::spawn(move || {
        let mut output = BufReader::new(stdout);
        let mut line = Vec::new();
        (&mut output).take(1025).read_until(b'\n', &mut line).unwrap();
        let _ = tx.send(line);
        let _ = output.take(1025).read_until(b'\n', &mut Vec::new());
    });
    (supervisor, rx.recv_timeout(Duration::from_secs(8)).unwrap())
}

#[test]
fn managed_manual_transcript_exact_target_permissions_and_cas() {
    let tmp = tempfile::tempdir().unwrap();
    let archive = tmp.path().join("archive");
    fs::create_dir(&archive).unwrap();
    fs::set_permissions(&archive, fs::Permissions::from_mode(0o700)).unwrap();
    let store = EventStore::init(&archive).unwrap();
    let root = tmp.path();
    let row = |account: &str, conversation: &str, message: &str| NormalizedMessage {
        platform: "wechat".into(), account_id: account.into(), conversation_id: conversation.into(),
        message_id: message.into(), title: None, sender_id: None, sender_name: None,
        timestamp: 1720000000, is_self: false, text: Some("indexed-fixture-canary".into()),
    };
    store.ingest("voice.fixture", IngestRequest {
        idempotency_key: "fixture-voice-1".into(), messages: vec![
            row("one", "room", "same"), row("two", "room", "same"),
            row("one", "other", "text"), row("two", "room", "foreign-only"),
        ],
    }).unwrap();
    // Only the synthetic index needs voice rows; no audio resolver is set up.
    let db = rusqlite::Connection::open(archive.join("native-v1.sqlite3")).unwrap();
    assert_eq!(db.execute("UPDATE messages SET type='voice' WHERE id != 'text'", []).unwrap(), 3);
    drop(db);

    let (writer, writer_hash) = generate_token().unwrap();
    let (reader, reader_hash) = generate_token().unwrap();
    let (foreign, foreign_hash) = generate_token().unwrap();
    let (disabled, disabled_hash) = generate_token().unwrap();
    let grant = |id, account, token_sha256, permissions| json!({
        "id":id,"token_sha256":token_sha256,"kind":"desktop","permissions":permissions,
        "account_scope":{"platform":"wechat","account_id":account},
    });
    let registry = json!({"version":1,"grants":[
        grant("voice.editor","one",writer_hash,json!(["messages.read","voice.transcription.read","voice.transcription.write"])),
        grant("voice.reader","one",reader_hash,json!(["messages.read","voice.transcription.read"])),
        grant("voice.peer","two",foreign_hash,json!(["messages.read","voice.transcription.read","voice.transcription.write"])),
        grant("voice.disabled","one",disabled_hash,json!(["messages.read"])),
    ]});
    let grants = root.join("grants.json");
    OpenOptions::new().create_new(true).mode(0o600).write(true).open(&grants).unwrap()
        .write_all(registry.to_string().as_bytes()).unwrap();
    let binary = Path::new(env!("CARGO_BIN_EXE_local-message-hub-rs")).canonicalize().unwrap();
    let launch = Launch::new(&binary, Mode::NativeLive, &archive, &grants).unwrap();
    let (parent, frame) = setup(&launch, root);
    let conn = || launch.accept_ready(&frame).unwrap();
    let expected_pid = conn().backend_pid();
    let current = |pid| pid == expected_pid && matches!(parent.0.borrow_mut().try_wait(), Ok(None));
    let scope = |account: &str| AccountScope { platform: "wechat".into(), account_id: account.into() };

    let mut writer = Client::connect(conn(), Zeroizing::new(writer), "voice.editor".into(), scope("one"), current).unwrap();
    let mut reader = Client::connect(conn(), Zeroizing::new(reader), "voice.reader".into(), scope("one"), current).unwrap();
    let mut foreign = Client::connect(conn(), Zeroizing::new(foreign), "voice.peer".into(), scope("two"), current).unwrap();
    assert!(matches!(Client::connect(conn(), Zeroizing::new(disabled), "voice.disabled".into(), scope("one"), current),
        Err(Error::Session(LoopbackError::Unsupported))));
    assert!(writer.can_write());
    assert!(!reader.can_write());
    assert!(matches!(writer.get("other", "same"), Err(Error::MissingVoice)));
    assert!(matches!(writer.get("other", "text"), Err(Error::MissingVoice)));
    assert!(matches!(writer.get("room", "foreign-only"), Err(Error::MissingVoice)));

    let initial = writer.get("room", "same").unwrap();
    assert_eq!((initial.status(), initial.revision(), initial.text()), (Status::NotRecorded, 0, None));
    assert_eq!(writer.clear(&initial).unwrap().revision(), 0);
    assert!(matches!(foreign.clear(&initial), Err(Error::WrongSession)));
    let read_initial = reader.get("room", "same").unwrap();
    assert!(matches!(reader.set(&read_initial, "not writable", None), Err(Error::Session(LoopbackError::Unsupported))));
    assert!(matches!(writer.set(&initial, "  ", None), Err(Error::Session(LoopbackError::Input))));
    assert!(matches!(writer.set(&initial, "reviewed", Some("ZH_cn")), Err(Error::Session(LoopbackError::Input))));

    let annotation = writer.set(&initial, "人工核对 <plain>", Some("zh-CN")).unwrap();
    assert_eq!((annotation.conversation_id(), annotation.message_id()), ("room", "same"));
    assert_eq!((annotation.status(),annotation.revision(),annotation.text(),annotation.language()),
        (Status::Manual,1,Some("人工核对 <plain>"),Some("zh-CN")));
    assert_eq!(reader.get("room","same").unwrap().text(), annotation.text());
    assert_eq!(foreign.get("room","same").unwrap().status(), Status::NotRecorded);
    assert!(matches!(writer.clear(&initial), Err(Error::Conflict)));
    let unchanged = writer.set(&annotation, "人工核对 <plain>", Some("zh-CN")).unwrap();
    assert_eq!(unchanged.revision(), 1);
    let cleared = writer.clear(&unchanged).unwrap();
    assert_eq!((cleared.status(),cleared.revision(),cleared.text(),cleared.language()), (Status::Cleared,2,None,None));
    assert_eq!(reader.get("room","same").unwrap().revision(), 2);
    assert_eq!(writer.clear(&cleared).unwrap().revision(), 2);
    let db = rusqlite::Connection::open(archive.join("native-v1.sqlite3")).unwrap();
    let indexed: String = db.query_row("SELECT text FROM messages WHERE account_id='one' AND id='same'",
        [], |r| r.get(0)).unwrap();
    assert_eq!(indexed, "indexed-fixture-canary");
}
