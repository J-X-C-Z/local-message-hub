use local_message_hub_rs::{events::EventStore, store::{Query, Store}};
use rusqlite::Connection;
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::{fs, path::Path, process::{Command, Output}};

fn project(source: &Path, target: &Path) -> Output {
    Command::new(env!("CARGO_BIN_EXE_local-message-hub-rs"))
        .arg("project-snapshot").arg("--source").arg(source)
        .arg("--directory").arg(target)
        .output().unwrap()
}

fn private_directory() -> tempfile::TempDir {
    let t = tempfile::tempdir().unwrap();
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(t.path(), fs::Permissions::from_mode(0o700)).unwrap();
    }
    t
}

fn make_seed(path: &Path) -> Connection {
    let db = Connection::open(path).unwrap();
    db.execute_batch(include_str!("fixtures/index-v9.sql")).unwrap();
    db
}

fn reject_unchanged(source: &Path, target: &Path) {
    let before = fs::read(source).unwrap();
    assert!(!project(source, target).status.success());
    assert!(fs::read_dir(target).unwrap().next().is_none());
    assert_eq!(before, fs::read(source).unwrap());
}

#[test]
fn projects_fixed_schema_preserves_cursor_gaps_and_refuses_existing_target() {
    let source = private_directory();
    let source = source.path().join("index.sqlite3");
    let db = make_seed(&source);
    db.execute_batch("
        UPDATE messages SET rowid = 183 WHERE id = 'm3';
        INSERT INTO conversation_metadata VALUES('wechat','owner','room','synthetic note','[\"marker\"]',1,200);
        INSERT INTO contact_identities VALUES('wechat','owner','friend','synthetic identity',200);
        CREATE TABLE operation_log (synthetic_secret TEXT);
        INSERT INTO operation_log VALUES('marker-outbound-state-not-projected');
    ").unwrap();
    drop(db);
    let old = Store::open(&source).unwrap();
    let query = Query { platform:"wechat".into(), account_id:"owner".into(), conversation_id:"room".into(), limit:Some(1), ..Default::default() };
    let old_read = old.messages(&query).unwrap();
    let target = private_directory();
    let result = project(&source, target.path());
    assert!(result.status.success(), "{}", String::from_utf8_lossy(&result.stderr));
    let receipt: Value = serde_json::from_slice(&result.stdout).unwrap();
    assert_eq!(receipt["rows"]["messages"], 3);
    assert_eq!(receipt["rows"]["contact_identities"], 1);
    let destination = target.path().join("index.sqlite3");
    let projected = Store::open(&destination).unwrap();
    assert_eq!(projected.messages(&query).unwrap(), old_read);
    assert_eq!(projected.conversations(&Query::default()).unwrap(), old.conversations(&Query::default()).unwrap());
    assert_eq!(projected.contacts(&Query::default()).unwrap(), old.contacts(&Query::default()).unwrap());
    let bytes = fs::read(&destination).unwrap();
    assert_eq!(receipt["source_sha256"], hex::encode(Sha256::digest(fs::read(&source).unwrap())));
    assert_eq!(receipt["output_sha256"], hex::encode(Sha256::digest(&bytes)));
    assert!(!bytes.windows(b"marker-outbound-state-not-projected".len()).any(|x| x==b"marker-outbound-state-not-projected"));
    assert!(!project(&source, target.path()).status.success());
    assert_eq!(fs::read(&destination).unwrap(), bytes);
    assert_eq!(fs::read_dir(target.path()).unwrap().count(), 1);
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        assert_eq!(fs::metadata(&destination).unwrap().permissions().mode() & 0o077, 0);
    }
}

#[test]
fn refuses_non_hub_future_and_active_sources_without_publishing() {
    let root = private_directory();
    let target = private_directory();
    let source = root.path().join("index.sqlite3");
    fs::write(&source, b"synthetic not a sqlite database").unwrap();
    reject_unchanged(&source, target.path());
    fs::remove_file(&source).unwrap();

    let db = Connection::open(&source).unwrap();
    db.execute_batch("PRAGMA user_version=9; CREATE TABLE unrelated (id INTEGER);").unwrap();
    drop(db);
    reject_unchanged(&source, target.path());
    fs::remove_file(&source).unwrap();

    let db = make_seed(&source);
    db.pragma_update(None, "user_version", 10).unwrap();
    drop(db);
    reject_unchanged(&source, target.path());
    let db = Connection::open(&source).unwrap();
    db.pragma_update(None, "user_version", 9).unwrap();
    db.execute_batch("CREATE TABLE unknown_future_state (name TEXT);").unwrap();
    drop(db);
    reject_unchanged(&source, target.path());
    let db = Connection::open(&source).unwrap();
    db.execute_batch("DROP TABLE unknown_future_state; PRAGMA journal_mode=WAL; INSERT INTO contacts(platform,account_id,id) VALUES('qq','a','wal');").unwrap();
    assert!(root.path().join("index.sqlite3-wal").is_file());
    reject_unchanged(&source, target.path());
    drop(db);
    fs::write(root.path().join("settings.json"), b"{}").unwrap();
    reject_unchanged(&source, target.path());
}

#[test]
fn refuses_rust_owned_archive_and_failed_copy_leaves_no_visible_target() {
    let native = private_directory();
    EventStore::init(native.path()).unwrap();
    let disguised = private_directory();
    let renamed = disguised.path().join("index.sqlite3");
    fs::copy(native.path().join("native-v1.sqlite3"), &renamed).unwrap();
    let target = private_directory();
    reject_unchanged(&renamed, target.path());

    let root = private_directory();
    let source = root.path().join("index.sqlite3");
    let db = make_seed(&source);
    db.execute_batch("
         ALTER TABLE messages ADD COLUMN injected TEXT;
    ").unwrap();
    drop(db);
    reject_unchanged(&source, target.path());
}

#[test]
#[ignore = "requires the pinned Python hub.store module (PYTHONPATH)"]
fn projects_the_actual_python_online_backup_with_committed_wal_rows() {
    let root = private_directory();
    let detached = private_directory();
    let source = detached.path().join("index.sqlite3");
    let python = Command::new("python3").arg("-B").arg("-c").arg(r#"
from pathlib import Path
from hub.store import ChatStore
import sqlite3, sys
db = Path(sys.argv[1])
s = ChatStore(db)
s.ingest('wechat','fixture', [{'id':'room','title':'synthetic WAL fixture'}],
         [{'id':'wal-only','conversation_id':'room','text':'committed WAL message','timestamp':11}])
assert db.with_name(db.name + '-wal').stat().st_size > 32
with sqlite3.connect(db.as_uri() + '?mode=ro&immutable=1', uri=True) as base:
    # The original base file alone does not yet see this committed row.
    exists = base.execute("SELECT 1 FROM sqlite_schema WHERE name='messages'").fetchone()
    assert not exists or base.execute("SELECT 1 FROM messages WHERE id='wal-only'").fetchone() is None
s.backup_to(sys.argv[2])
s.close()
"#).arg(root.path().join("hub.sqlite3")).arg(&source).output().unwrap();
    assert!(python.status.success(), "{}", String::from_utf8_lossy(&python.stderr));
    let before = fs::read(&source).unwrap();
    let target = private_directory();
    let result = project(&source, target.path());
    assert!(result.status.success(), "{}", String::from_utf8_lossy(&result.stderr));
    assert_eq!(before, fs::read(&source).unwrap());
    let output = Store::open(target.path().join("index.sqlite3")).unwrap();
    let contents = output.messages(&Query { platform:"wechat".into(), account_id:"fixture".into(),
        conversation_id:"room".into(), ..Default::default() }).unwrap();
    assert_eq!(contents["items"][0]["text"], "committed WAL message");
    assert_eq!(output.status().unwrap()["fts_enabled"], false);
}

#[test]
fn target_never_contains_partial_output_after_late_copy_failure() {
    let source = private_directory();
    let source = source.path().join("index.sqlite3");
    let db = make_seed(&source);
    db.execute_batch("
        DROP TABLE conversation_metadata;
        CREATE TABLE conversation_metadata (
          platform TEXT NOT NULL, account_id TEXT NOT NULL, conversation_id TEXT NOT NULL,
          note TEXT, tags TEXT NOT NULL, archived INTEGER NOT NULL, updated_at INTEGER NOT NULL,
          PRIMARY KEY (platform, account_id, conversation_id)
        );
        INSERT INTO conversation_metadata VALUES ('wechat','owner','room',NULL,'[]',0,7);
    ").unwrap();
    drop(db);
    let target = private_directory();
    let before = fs::read(&source).unwrap();
    let result = project(&source, target.path());
    assert!(!result.status.success());
    assert!(String::from_utf8_lossy(&result.stderr).contains("could not validate or project"));
    assert!(fs::read_dir(target.path()).unwrap().next().is_none());
    assert_eq!(fs::read(&source).unwrap(), before);
}

#[test]
fn concurrent_projects_to_the_same_target_never_overwrite_or_publish_partial_data() {
    use std::sync::Barrier;
    let source = private_directory();
    let source = source.path().join("index.sqlite3");
    drop(make_seed(&source));
    let target = private_directory();
    let start = Barrier::new(2);
    let outcomes = std::thread::scope(|s| {
        let invoke = || {
            start.wait();
            project(&source, target.path()).status.success()
        };
        let first = s.spawn(invoke);
        let second = s.spawn(invoke);
        [first.join().unwrap(),second.join().unwrap()]
    });
    let successes = outcomes.iter().filter(|x| **x).count();
    assert!(successes <= 1);
    assert_eq!(successes, fs::read_dir(target.path()).unwrap().count());
    if successes == 1 {
        let output = Store::open(target.path().join("index.sqlite3")).unwrap();
        let expected = Store::open(&source).unwrap();
        assert_eq!(expected.conversations(&Query::default()).unwrap(), output.conversations(&Query::default()).unwrap());
    }
}

#[cfg(unix)]
#[test]
fn rejects_a_public_or_redirected_target_directory_before_writing() {
    use std::os::unix::fs::{PermissionsExt, symlink};
    let source = private_directory();
    let snapshot = source.path().join("index.sqlite3");
    drop(make_seed(&snapshot));
    let target = private_directory();
    fs::set_permissions(target.path(), fs::Permissions::from_mode(0o755)).unwrap();
    reject_unchanged(&snapshot, target.path());
    fs::set_permissions(target.path(), fs::Permissions::from_mode(0o700)).unwrap();
    let alias = source.path().join("alias");
    symlink(target.path(), &alias).unwrap();
    assert!(!project(&snapshot, &alias).status.success());
    assert!(fs::read_dir(target.path()).unwrap().next().is_none());
}
