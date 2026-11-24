use local_message_hub_rs::events::{EventStore, IngestRequest, NormalizedMessage};
use rusqlite::{Connection, OpenFlags};
use serde_json::{Value, json};
use std::{fs, path::Path, process::{Command, Output}};

fn temp() -> tempfile::TempDir {
    let dir = tempfile::tempdir().unwrap();
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(dir.path(),fs::Permissions::from_mode(0o700)).unwrap();
    }
    dir
}

fn run(command: &str, source: &Path, target: &Path, account: &str, confirmation: Option<&str>) -> Output {
    let mut cli = Command::new(env!("CARGO_BIN_EXE_local-message-hub-rs"));
    cli.arg(command).arg("--source").arg(source).arg("--directory").arg(target)
        .args(["--platform","qq","--account",account]);
    if let Some(code) = confirmation { cli.args(["--confirm",code]); }
    cli.output().unwrap()
}

fn success(output: Output) -> Value {
    assert!(output.status.success(),"{}", String::from_utf8_lossy(&output.stderr));
    serde_json::from_slice(&output.stdout).unwrap()
}
fn failed(output: Output, marker: &str) {
    assert!(!output.status.success());
    assert!(output.stdout.is_empty());
    assert!(String::from_utf8_lossy(&output.stderr).contains(marker));
}
fn preview(source: &Path, target: &Path, account: &str) -> String {
    let value = success(run("preview-legacy-json",source,target,account,None));
    assert_eq!(value.as_object().unwrap().len(),2);
    value["confirmation"].as_str().unwrap().into()
}

fn row(account: &str, id: &str, content: &str) -> Value {
    json!({"platform":"qq","account_id":account,"id":id,"conversation_id":"room","conversation_title":"有空来聊",
        "type":"text","text":content,"timestamp":1_724_123_456,"is_self":false,"sender_name":"同学",
        "sender_id":"sender","timestamp_text":"ignored","extra":{"bad_path":"/not-read"},
        "media_url":"https://example.invalid/do-not-fetch","resources":[{"path":"file://not-read"}]})
}
fn write(path: &Path, rows: Vec<Value>) {
    fs::write(path,serde_json::to_vec(&rows).unwrap()).unwrap();
}

fn read_rows(archive: &EventStore) -> Vec<(String,String,String,i64,bool,Option<String>)> {
    let db=Connection::open_with_flags(archive.path(), OpenFlags::SQLITE_OPEN_READ_ONLY).unwrap();
    let mut statement = db.prepare("SELECT account_id,id,text,timestamp,is_self,media_url FROM messages ORDER BY account_id,id").unwrap();
    statement.query_map([], |r| Ok((r.get(0)?,r.get(1)?,r.get(2)?,r.get(3)?,r.get(4)?,r.get(5)?))).unwrap()
        .collect::<rusqlite::Result<_>>().unwrap()
}

#[test]
fn exact_scope_stable_receipts_and_no_offline_events_across_restart() {
    let out=temp();
    let target=temp();
    let archive=EventStore::init(target.path()).unwrap();
    let source=out.path().join("private-origin.json");
    write(&source,vec![row("A","same-id","你好\n🦀")]);
    let bytes=fs::read(&source).unwrap();
    let store_before=fs::read(archive.path()).unwrap();
    let code_a=preview(&source,target.path(),"A");
    assert_eq!(fs::read(archive.path()).unwrap(),store_before);
    assert!(read_rows(&archive).is_empty());
    failed(run("import-legacy-json",&source,target.path(),"A",None),"Usage:");
    failed(run("import-legacy-json",&source,target.path(),"B",Some(&code_a)),"text-only legacy JSON");
    let another=temp();
    let another_archive=EventStore::init(another.path()).unwrap();
    failed(run("import-legacy-json",&source,another.path(),"A",Some(&code_a)),"exact preview");
    assert!(read_rows(&another_archive).is_empty());
    let receipt=success(run("import-legacy-json",&source,target.path(),"A",Some(&code_a)));
    assert_eq!(receipt,json!({"batch_id":code_a,"rows":1,"created":1,"unchanged":0}));
    assert_eq!(fs::read(&source).unwrap(),bytes);

    drop(archive);
    let archive=EventStore::open(target.path()).unwrap();
    assert_eq!(success(run("import-legacy-json",&source,target.path(),"A",Some(&code_a))),receipt);
    // Reformatting changes the confirmed bytes, but the unchanged row is not duplicated.
    fs::write(&source,serde_json::to_vec_pretty(&vec![row("A","same-id","你好\n🦀")]).unwrap()).unwrap();
    let formatted=preview(&source,target.path(),"A");
    assert_ne!(formatted,code_a);
    assert_eq!(success(run("import-legacy-json",&source,target.path(),"A",Some(&formatted))),
        json!({"batch_id":formatted,"rows":1,"created":0,"unchanged":1}));
    assert_eq!(archive.checkpoint().unwrap(),0);
    assert!(archive.replay(0,100).unwrap().items.is_empty());
    write(&source,vec![row("B","same-id","different scope")]);
    let code_b=preview(&source,target.path(),"B");
    assert_ne!(code_a,code_b);
    failed(run("import-legacy-json",&source,target.path(),"B",Some(&code_a)),"exact preview");
    success(run("import-legacy-json",&source,target.path(),"B",Some(&code_b)));
    assert_eq!(read_rows(&archive),vec![
        ("A".into(),"same-id".into(),"你好\n🦀".into(),1_724_123_456,false,None),
        ("B".into(),"same-id".into(),"different scope".into(),1_724_123_456,false,None),
    ]);
    assert_eq!(archive.checkpoint().unwrap(),0);
}

#[test]
fn changed_batch_and_existing_producer_conflicts_roll_back_entire_batch() {
    let out=temp();
    let target=temp();
    let archive=EventStore::init(target.path()).unwrap();
    let source=out.path().join("messages.json");
    write(&source,vec![row("A","same","old")]);
    let old=preview(&source,target.path(),"A");
    success(run("import-legacy-json",&source,target.path(),"A",Some(&old)));
    write(&source,vec![row("A","new","never applied"),row("A","same","new")]);
    failed(run("import-legacy-json",&source,target.path(),"A",Some(&old)),"exact preview");
    let conflict=preview(&source,target.path(),"A");
    failed(run("import-legacy-json",&source,target.path(),"A",Some(&conflict)),"import was not confirmed");
    assert_eq!(read_rows(&archive).iter().map(|r|r.2.as_str()).collect::<Vec<_>>(),vec!["old"]);

    let live=NormalizedMessage { platform:"qq".into(),account_id:"A".into(),conversation_id:"room".into(),
        message_id:"live".into(),title:None,sender_id:None,sender_name:None,timestamp:1,
        is_self:false,text:Some("live".into()) };
    let live_request=|| IngestRequest { idempotency_key:"live-1".into(),messages:vec![live.clone()] };
    assert!(archive.ingest("@offline/legacy-json-v1",live_request()).is_err());
    archive.ingest("fixture.producer",live_request()).unwrap();
    let checkpoint=archive.checkpoint().unwrap();
    write(&source,vec![row("A","also-new","rollback"),row("A","live","should not update")]);
    let conflict=preview(&source,target.path(),"A");
    failed(run("import-legacy-json",&source,target.path(),"A",Some(&conflict)),"import was not confirmed");
    assert_eq!(archive.checkpoint().unwrap(),checkpoint);
    assert_eq!(read_rows(&archive).iter().map(|r|r.2.as_str()).collect::<Vec<_>>(),vec!["live","old"]);
}

#[test]
fn invalid_shapes_and_unrelated_targets_fail_without_source_details() {
    let out=temp();
    let target=temp();
    let archive=EventStore::init(target.path()).unwrap();
    let source=out.path().join("private-export-name.json");
    let mut image=row("A","image","text");
    image["type"]=json!("image");
    let mut wrong_time=row("A","invalid","text");
    wrong_time["timestamp"]=json!("2024-01-01");
    for rows in [vec![],vec![row("A","good","OK"),row("B","leak","do not echo")],
        vec![row("A","same","old"),row("A","same","new")],
        vec![image],vec![wrong_time],(0..101).map(|i|row("A",&i.to_string(),"text")).collect()] {
        write(&source,rows);
        let output=run("preview-legacy-json",&source,target.path(),"A",None);
        assert!(!String::from_utf8_lossy(&output.stderr).contains("private-export-name"));
        assert!(!String::from_utf8_lossy(&output.stderr).contains("do not echo"));
        failed(output,"text-only legacy JSON");
    }
    fs::write(&source,vec![b' ';512*1024+1]).unwrap();
    failed(run("preview-legacy-json",&source,target.path(),"A",None),"export unavailable");
    write(&source,vec![row("A","ok","text")]);
    failed(run("preview-legacy-json",&source,out.path(),"A",None),"initialized native archive");
    assert!(read_rows(&archive).is_empty());
    assert_eq!(archive.checkpoint().unwrap(),0);
}

#[test]
#[ignore = "requires pinned stock Python hub.store and hub.exports on PYTHONPATH"]
fn stock_python_messages_export_roundtrips_through_offline_import() {
    let out=temp();
    let target=temp();
    let archive=EventStore::init(target.path()).unwrap();
    let source=out.path().join("fixture.json");
    let script=r#"
import pathlib,sys
from hub.store import ChatStore
from hub.exports import write_export
out=pathlib.Path(sys.argv[1])
store=ChatStore(out.with_name('legacy.sqlite3'))
store.ingest('qq','A',[{'id':'room','title':'真实格式'}],
    [{'id':'m1','conversation_id':'room','type':'text','timestamp':1724123456,'text':'首行\n下一行🦀','sender_id':'u1','sender_name':'学生','is_self':True}])
row=store.get_message('qq','A','m1')
assert row['account_id']=='A'
with out.open('wb') as dest:
    write_export([row],dest,'json')
store.close()
"#;
    let generated=Command::new("python3").args(["-B","-c",script]).arg(&source).output().unwrap();
    assert!(generated.status.success(),"{}",String::from_utf8_lossy(&generated.stderr));
    let code=preview(&source,target.path(),"A");
    success(run("import-legacy-json",&source,target.path(),"A",Some(&code)));
    assert_eq!(read_rows(&archive),vec![("A".into(),"m1".into(),"首行\n下一行🦀".into(),1_724_123_456,true,None)]);
    let db=Connection::open_with_flags(archive.path(), OpenFlags::SQLITE_OPEN_READ_ONLY).unwrap();
    let sender: (String,String) = db.query_row("SELECT sender_id,sender_name FROM messages WHERE account_id='A' AND id='m1'",[],
        |r| Ok((r.get(0)?,r.get(1)?))).unwrap();
    assert_eq!(sender,("u1".into(),"学生".into()));
    assert_eq!(archive.checkpoint().unwrap(),0);
}

#[test]
fn concurrent_same_preview_commits_one_batch_and_returns_a_shared_receipt() {
    let out=temp();
    let target=temp();
    let archive=EventStore::init(target.path()).unwrap();
    let source=out.path().join("overlap.json");
    write(&source,vec![row("A","m1","first"),row("A","m2","second")]);
    let code=preview(&source,target.path(),"A");
    std::thread::scope(|scope| {
        let first=scope.spawn(||run("import-legacy-json",&source,target.path(),"A",Some(&code)));
        let second=scope.spawn(||run("import-legacy-json",&source,target.path(),"A",Some(&code)));
        let first=success(first.join().unwrap());
        let second=success(second.join().unwrap());
        assert_eq!(first,second);
        assert_eq!(first,json!({"batch_id":code,"rows":2,"created":2,"unchanged":0}));
    });
    assert_eq!(read_rows(&archive).len(),2);
    assert_eq!(archive.checkpoint().unwrap(),0);
}
