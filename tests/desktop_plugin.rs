use local_message_hub_rs::plugins::generate_token;
use rusqlite::Connection;
use serde_json::{Value, json};
use std::{
    fs,
    io::{BufRead, BufReader, Read, Write},
    net::{SocketAddr, TcpStream},
    path::Path,
    process::{Child, Command, Stdio},
    sync::mpsc,
    time::Duration,
};

const REQUEST_TIMEOUT: Duration = Duration::from_secs(8);

struct Server(Child);
impl Drop for Server {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

fn start(snapshot: &Path, grants: &Path) -> (Server, SocketAddr) {
    let mut server = Server(
        Command::new(env!("CARGO_BIN_EXE_local-message-hub-rs"))
            .arg("serve")
            .arg("--snapshot")
            .arg(snapshot)
            .arg("--grants")
            .arg(grants)
            .stdout(Stdio::piped())
            .spawn()
            .unwrap(),
    );
    let mut stream = BufReader::new(server.0.stdout.take().unwrap());
    let (tx, rx) = mpsc::channel();
    std::thread::spawn(move || {
        let mut line = String::new();
        if stream.read_line(&mut line).is_ok() {
            let _ = tx.send(line);
        }
    });
    let ready: Value = serde_json::from_str(&rx.recv_timeout(REQUEST_TIMEOUT).unwrap()).unwrap();
    assert_eq!(ready["event"], "ready");
    assert_eq!(ready["api_version"], 1);
    let address: SocketAddr = ready["origin"]
        .as_str()
        .unwrap()
        .strip_prefix("http://")
        .unwrap()
        .parse()
        .unwrap();
    assert_eq!(address.ip().to_string(), "127.0.0.1");
    (server, address)
}

fn request_raw(
    address: SocketAddr,
    method: &str,
    path: &str,
    fields: &[(&str, &str)],
) -> (u16, String, String) {
    let mut client = TcpStream::connect_timeout(&address, REQUEST_TIMEOUT).unwrap();
    client.set_read_timeout(Some(REQUEST_TIMEOUT)).unwrap();
    client.set_write_timeout(Some(REQUEST_TIMEOUT)).unwrap();
    write!(client, "{method} {path} HTTP/1.1\r\nConnection: close\r\n").unwrap();
    if !fields
        .iter()
        .any(|(name, _)| name.eq_ignore_ascii_case("Host"))
    {
        write!(client, "Host: {address}\r\n").unwrap();
    }
    for (name, value) in fields {
        write!(client, "{name}: {value}\r\n").unwrap();
    }
    write!(client, "\r\n").unwrap();
    let mut response = String::new();
    client.read_to_string(&mut response).unwrap();
    let (headers, body) = response.split_once("\r\n\r\n").unwrap();
    let status = headers.split_whitespace().nth(1).unwrap().parse().unwrap();
    (status, headers.to_owned(), body.to_owned())
}

fn request(
    address: SocketAddr,
    method: &str,
    path: &str,
    fields: &[(&str, &str)],
) -> (u16, String, Value) {
    let (status, headers, body) = request_raw(address, method, path, fields);
    (status, headers, serde_json::from_str(&body).unwrap())
}

#[test]
fn native_desktop_plugin_handshake_reads_scoped_archive_fails_closed() {
    let directory = tempfile::tempdir().unwrap();
    let archive = directory.path().join("index.sqlite3");
    let db = Connection::open(&archive).unwrap();
    db.execute_batch(include_str!("fixtures/index-v9.sql"))
        .unwrap();
    db.close().unwrap();
    let before = fs::read(&archive).unwrap();
    let (desktop_token, desktop_hash) = generate_token().unwrap();
    let (tool_token, tool_hash) = generate_token().unwrap();
    let grants = directory.path().join("plugins.json");
    fs::write(&grants, json!({"version":1,"grants":[
        {"id":"smoke.desktop","kind":"desktop","permissions":["conversations.read","messages.read"],"token_sha256":desktop_hash},
        {"id":"smoke.contacts","kind":"tool","permissions":["contacts.read"],"token_sha256":tool_hash}
    ]}).to_string()).unwrap();
    let (_server, address) = start(&archive, &grants);
    let desktop_bearer = format!("Bearer {desktop_token}");
    let tool_bearer = format!("Bearer {tool_token}");
    let desktop = [("Authorization", desktop_bearer.as_str())];
    let tool = [("Authorization", tool_bearer.as_str())];
    assert_eq!(request(address, "GET", "/healthz", &[]).0, 200);
    assert_eq!(request(address, "GET", "/api/v1/manifest", &[]).0, 401);
    assert_eq!(
        request(
            address,
            "GET",
            "/api/v1/manifest",
            &[
                ("Authorization", desktop_bearer.as_str()),
                ("Authorization", tool_bearer.as_str())
            ]
        )
        .0,
        401
    );
    assert_eq!(
        request(
            address,
            "GET",
            "/api/v1/manifest",
            &[desktop[0], ("Origin", "https://example.invalid")]
        )
        .0,
        403
    );
    assert_eq!(
        request(
            address,
            "GET",
            "/api/v1/manifest",
            &[desktop[0], ("Host", "example.invalid")]
        )
        .0,
        403
    );

    let (status, headers, manifest) = request(address, "GET", "/api/v1/manifest", &desktop);
    assert_eq!(status, 200);
    assert!(
        headers
            .to_ascii_lowercase()
            .contains("cache-control: no-store")
    );
    assert_eq!(manifest["plugin"]["id"], "smoke.desktop");
    assert_eq!(manifest["plugin_protocol_version"], 1);
    assert_eq!(manifest["resources"]["contacts"], Value::Null);
    assert!(!manifest.to_string().contains(&desktop_token));
    let (status, _, openapi) = request(
        address,
        "GET",
        manifest["openapi"].as_str().unwrap(),
        &desktop,
    );
    assert_eq!(status, 200);
    assert_eq!(openapi["openapi"], "3.1.0");

    let (status, _, conversations) = request(
        address,
        "GET",
        "/api/v1/conversations?platform=wechat",
        &desktop,
    );
    assert_eq!(status, 200);
    assert_eq!(conversations["items"][0]["id"], "room");
    assert_eq!(request(address, "GET", "/api/v1/contacts", &desktop).0, 403);
    assert_eq!(request(address, "GET", "/api/v1/messages", &tool).0, 403);
    let (status, _, contacts) = request(
        address,
        "GET",
        "/api/v1/contacts?platform=wechat&account_id=owner",
        &tool,
    );
    assert_eq!(status, 200);
    assert_eq!(contacts["items"][0]["id"], "friend");
    let path = "/api/v1/messages?platform=wechat&account_id=owner&conversation_id=room&limit=1";
    let (status, _, first) = request(address, "GET", path, &desktop);
    assert_eq!(status, 200);
    assert_eq!(first["items"][0]["id"], "m3");
    let cursor = first["next_cursor"].as_str().unwrap();
    let (status, _, second) = request(address, "GET", &format!("{path}&cursor={cursor}"), &desktop);
    assert_eq!(status, 200);
    assert_eq!(second["items"][0]["id"], "m2");
    let changed_scope =
        format!("/api/v1/messages?platform=wechat&account_id=other&cursor={cursor}");
    assert_eq!(request(address, "GET", &changed_scope, &desktop).0, 400);
    let (status, _, found) = request(
        address,
        "GET",
        "/api/v1/search?platform=wechat&q=example",
        &desktop,
    );
    assert_eq!(status, 200);
    assert_eq!(found["total"], 2);
    assert_eq!(
        request(address, "POST", "/api/v1/messages", &desktop).0,
        405
    );
    let scope = "platform=wechat&account_id=owner&conversation_id=room";
    let quoted = format!("/api/v1/messages/quoted-context?{scope}&reference_message_id=m2");
    assert_eq!(request(address, "GET", &quoted, &tool).0, 403);
    let (status, _, context) = request(address, "GET", &quoted, &desktop);
    assert_eq!(status, 200);
    assert_eq!(context["target_message_id"], "m1");
    assert!(!context.to_string().contains("7654321"));
    let other = "/api/v1/messages/quoted-context?platform=wechat&account_id=other&conversation_id=room&reference_message_id=m2";
    assert_eq!(request(address, "GET", other, &desktop).0, 404);
    assert_eq!(
        request(
            address,
            "GET",
            &format!("{quoted}&source_message_id=7654321"),
            &desktop
        )
        .0,
        400
    );
    let middle = format!("/api/v1/messages/context?{scope}&message_id=m2&before=1&after=1");
    let (status, _, context) = request(address, "GET", &middle, &desktop);
    assert_eq!(status, 200);
    assert_eq!(context["target_index"], 1);
    assert_eq!(context["items"][1]["id"], "m2");
    // Superseding the safe directory mid-run must revoke continued data reads, not merely alter startup.
    fs::write(
        directory.path().join("settings.json"),
        r#"{"account_lock":{"salt":"source","digest":"opaque"}}"#,
    )
    .unwrap();
    let (status, _, denied) = request(address, "GET", path, &desktop);
    assert_eq!(status, 423);
    assert_eq!(denied, json!({"error":{"code":"snapshot_unavailable"}}));
    assert_eq!(fs::read(archive).unwrap(), before);
    assert!(!directory.path().join("index.sqlite3-wal").exists());
    assert!(!directory.path().join("index.sqlite3-shm").exists());
}

#[test]
fn standalone_closed_wal_mode_snapshot_leaves_no_sqlite_sidecars() {
    let directory = tempfile::tempdir().unwrap();
    let spaced = directory.path().join("档案 # %");
    fs::create_dir(&spaced).unwrap();
    let archive = spaced.join("index.sqlite3");
    let db = Connection::open(&archive).unwrap();
    db.execute_batch(include_str!("fixtures/index-v9.sql"))
        .unwrap();
    assert_eq!(
        db.query_row("PRAGMA journal_mode=WAL", [], |row| row.get::<_, String>(0))
            .unwrap(),
        "wal"
    );
    db.close().unwrap();
    let before = fs::read(&archive).unwrap();
    assert_eq!(&before[18..20], &[2, 2]);
    let guarded = local_message_hub_rs::snapshot::Snapshot::open(&archive).unwrap();
    let native = local_message_hub_rs::store::Store::open(guarded.path()).unwrap();
    assert_eq!(native.status().unwrap()["schema_version"], 9);
    assert_eq!(
        native.conversations(&Default::default()).unwrap()["total"],
        1
    );
    assert_eq!(fs::read(&archive).unwrap(), before);
    assert_eq!(fs::read_dir(&spaced).unwrap().count(), 1);
}

#[test]
fn exact_desktop_scope_reads_its_snapshot_quotes_without_writing_the_archive() {
    let directory = tempfile::tempdir().unwrap();
    let archive = directory.path().join("index.sqlite3");
    let db = Connection::open(&archive).unwrap();
    db.execute_batch(include_str!("fixtures/index-v9.sql")).unwrap();
    db.execute_batch(r#"
        INSERT INTO conversations(platform,account_id,id,title,last_timestamp) VALUES
          ('wechat','other','room','Foreign fixture',200);
        INSERT INTO messages(platform,account_id,id,conversation_id,timestamp,text,extra) VALUES
          ('wechat','other','m-parent','room',120,'foreign target','{"_hub_wechat_server_id":"7654321"}'),
          ('wechat','other','m2','room',121,'foreign quote','{"wechat_quote":{"target_server_id":"7654321"}}');
    "#).unwrap();
    db.close().unwrap();
    let before = fs::read(&archive).unwrap();
    let (token, hash) = generate_token().unwrap();
    let grants = directory.path().join("plugins.json");
    fs::write(&grants, json!({"version":1,"grants":[{
        "id":"snapshot.scoped","kind":"desktop","permissions":["messages.read","conversations.read","events.read"],
        "token_sha256":hash,"account_scope":{"platform":"wechat","account_id":"owner"}
    }]}).to_string()).unwrap();
    let (_server, addr) = start(&archive, &grants);
    let bearer = format!("Bearer {token}");
    let fields = [("Authorization", bearer.as_str())];
    let (status, _, manifest) = request(addr, "GET", "/api/v1/manifest", &fields);
    assert_eq!(status, 200);
    assert_eq!(manifest["plugin"]["account_scope"]["account_id"], "owner");
    assert_eq!(manifest["resources"]["event_checkpoint"], Value::Null);
    assert!(!manifest.to_string().contains(&hash));
    let [(init_status, _, output), (foreign_status, _, foreign)] = [
        request(addr, "GET", "/api/v1/messages/quoted-context?conversation_id=room&reference_message_id=m2", &fields),
        request(addr, "GET", "/api/v1/messages/quoted-context?account_id=other&conversation_id=room&reference_message_id=m2", &fields)
    ];
    assert_eq!(init_status, 200);
    assert_eq!(output["target_message_id"], "m1");
    assert!(!output.to_string().contains("foreign target"));
    assert_eq!((foreign_status, foreign), (404, json!({"error":{"code":"not_found"}})));
    assert_eq!(fs::read(&archive).unwrap(), before);
}

#[test]
fn desktop_can_open_projected_snapshot_and_read_with_the_existing_http_contract() {
    let source = tempfile::tempdir().unwrap();
    let index = source.path().join("index.sqlite3");
    let db = Connection::open(&index).unwrap();
    db.execute_batch(include_str!("fixtures/index-v9.sql")).unwrap();
    db.close().unwrap();
    let target = tempfile::tempdir().unwrap();
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(target.path(), fs::Permissions::from_mode(0o700)).unwrap();
    }
    let result = Command::new(env!("CARGO_BIN_EXE_local-message-hub-rs"))
        .arg("project-snapshot").arg("--source").arg(&index)
        .arg("--directory").arg(target.path()).output().unwrap();
    assert!(result.status.success(), "{}", String::from_utf8_lossy(&result.stderr));
    let (token, hash) = generate_token().unwrap();
    let grants = source.path().join("grants.json");
    fs::write(&grants, json!({"version":1,"grants":[{
        "id":"projected.scoped","kind":"desktop","permissions":["messages.read"],
        "token_sha256":hash,"account_scope":{"platform":"wechat","account_id":"owner"}
    }]}).to_string()).unwrap();
    let (_server, addr) = start(&target.path().join("index.sqlite3"), &grants);
    let bearer = format!("Bearer {token}");
    let fields = [("Authorization", bearer.as_str())];
    let (status, _, response) = request(addr, "GET", "/api/v1/messages?conversation_id=room", &fields);
    assert_eq!(status, 200);
    assert_eq!(response["total"], 3);
    assert_eq!(response["items"][0]["text"], "example second");
}

const EXPORT: &str = "/api/v1/messages/export";

fn export_grants(directory: &Path) -> (std::path::PathBuf, String, String) {
    let (token, hash) = generate_token().unwrap();
    let (reader, reader_hash) = generate_token().unwrap();
    let grants = directory.join("grants.json");
    fs::write(&grants, json!({"version":1,"grants":[
        {"id":"fixture.export","kind":"desktop","permissions":["messages.read","messages.export"],
         "token_sha256":hash,"account_scope":{"platform":"wechat","account_id":"owner"}},
        {"id":"fixture.reader","kind":"desktop","permissions":["messages.read"],
         "token_sha256":reader_hash,"account_scope":{"platform":"wechat","account_id":"owner"}}
    ]}).to_string()).unwrap();
    (grants, format!("Bearer {token}"), format!("Bearer {reader}"))
}

fn export_seed(path: &Path) {
    let db = Connection::open(path).unwrap();
    db.execute_batch(include_str!("fixtures/index-v9.sql")).unwrap();
    db.execute_batch(r#"
        INSERT INTO conversations(platform,account_id,id,title,last_timestamp) VALUES
          ('wechat','peer','room','foreign title',200),
          ('wechat','owner','another','other conversation',300),
          ('wechat','owner','empty','empty fixture',0);
        INSERT INTO messages(platform,account_id,id,conversation_id,timestamp,text) VALUES
          ('wechat','peer','m2','room',121,'do not include peer data'),
          ('wechat','owner','m4','another',141,'do not include other conversation');
        UPDATE messages SET media_url='https://media.invalid/do-not-include-locator',
          file_name='do-not-include-filename',
          extra='{"attachments":[{"path":"do-not-include-private-cache"}]}'
          WHERE platform='wechat' AND account_id='owner' AND id='m2';
    "#).unwrap();
    db.execute(
        "UPDATE messages SET sender_name=?1,text=?2 WHERE platform='wechat' AND account_id='owner' AND id='m3'",
        ("@公式", "  =计算, \"引号\"\r\n第二行"),
    ).unwrap();
    db.close().unwrap();
}

#[test]
fn export_uses_one_authorized_conversation_and_matches_safe_utf8_and_csv_shape() {
    let directory = tempfile::tempdir().unwrap();
    let source = directory.path().join("index.sqlite3");
    export_seed(&source);
    let before = fs::read(&source).unwrap();
    let (grants, owner, read_only) = export_grants(directory.path());
    let (_server, addr) = start(&source, &grants);
    let owner = [("Authorization", owner.as_str())];
    let reader = [("Authorization", read_only.as_str())];
    let path = format!("{EXPORT}?conversation_id=room");
    let (status, headers, data) = request(addr, "GET", &path, &owner);
    assert_eq!(status, 200);
    assert!(headers.contains("content-type: application/json; charset=utf-8"));
    assert!(headers.contains("content-disposition: attachment; filename=\"messages.json\""));
    assert!(headers.contains("cache-control: no-store"));
    assert_eq!(data.as_array().unwrap().len(), 3);
    assert_eq!(data[2], json!({
        "id":"m3","platform":"wechat","account_id":"owner","conversation_id":"room",
        "conversation_title":"Fixture conversation","sender_id":"friend","sender_name":"@公式",
        "timestamp":7,"type":"text","text":"  =计算, \"引号\"\r\n第二行","is_self":false,
        "timestamp_text":"1970-01-01 08:00:07"
    }));
    assert_eq!(data[0]["id"], "m1");
    assert_eq!(data[1]["id"], "m2");
    assert!(data[1].get("extra").is_none());
    assert!(data[1].get("file_name").is_none());
    assert!(!data.to_string().contains("do not include"));
    let (status, _, selected) = request(addr, "GET", &format!("{path}&start=7&end=7"), &owner);
    assert_eq!(status, 200);
    assert_eq!(selected.as_array().unwrap().len(), 2);
    assert_eq!(request(addr, "GET", &format!("{EXPORT}?conversation_id=empty"), &owner).2, json!([]));

    let (status, headers, csv) = request_raw(addr, "GET", &format!("{path}&format=csv"), &owner);
    assert_eq!(status, 200);
    assert!(headers.contains("content-type: text/csv; charset=utf-8"));
    assert!(headers.contains("content-disposition: attachment; filename=\"messages.csv\""));
    assert_eq!(csv, concat!(
        "时间,平台,账号,会话,发送者,发送者ID,类型,内容,媒体链接,文件名,附件,会话ID,消息ID\r\n",
        "1970-01-01 08:00:06,wechat,owner,Fixture conversation,,friend,text,earlier,,,[],room,m1\r\n",
        "1970-01-01 08:00:07,wechat,owner,Fixture conversation,,friend,text,example first,,,[],room,m2\r\n",
        "1970-01-01 08:00:07,wechat,owner,Fixture conversation,'@公式,friend,text,\"'  =计算, \"\"引号\"\"\r\n第二行\",,,[],room,m3\r\n",
    ));
    assert_eq!(request(addr, "GET", &path, &[]).0, 401);
    assert_eq!(request(addr, "GET", &path, &reader).0, 403);
    for invalid in ["account_id=peer","platform=all","platform=qq"] {
        let (status, _, response) = request(addr, "GET", &format!("{path}&{invalid}"), &owner);
        assert_eq!((status,response), (404,json!({"error":{"code":"not_found"}})));
    }
    // HTML is now a documented attachment format. Exercise it at the same
    // endpoint without sending its non-JSON body through the JSON helper.
    let (status, headers, html) =
        request_raw(addr, "GET", &format!("{path}&format=html"), &owner);
    assert_eq!(status, 200);
    assert!(headers.contains("content-type: text/html; charset=utf-8"));
    assert!(headers.contains("content-disposition: attachment; filename=\"messages.html\""));
    assert!(headers.contains("content-security-policy: sandbox; default-src 'none'"));
    assert!(html.contains("&quot;引号&quot;"));
    assert!(!html.contains("do not include"));
    for invalid in ["format=yaml", "format=json&format=csv", "start=8&end=7", "unknown=1"] {
        assert_eq!(request(addr, "GET", &format!("{path}&{invalid}"), &owner).0, 400);
    }
    let (_, _, manifest) = request(addr, "GET", "/api/v1/manifest", &owner);
    assert_eq!(manifest["resources"]["message_export"], EXPORT);
    let (_, _, manifest) = request(addr, "GET", "/api/v1/manifest", &reader);
    assert_eq!(manifest["resources"]["message_export"], Value::Null);
    assert_eq!(fs::read(&source).unwrap(), before);
}

#[test]
fn export_limits_fail_completely_instead_of_sending_partial_files() {
    let directory = tempfile::tempdir().unwrap();
    let source = directory.path().join("index.sqlite3");
    export_seed(&source);
    let db = Connection::open(&source).unwrap();
    db.execute_batch("
    INSERT INTO conversations(platform,account_id,id) VALUES
       ('wechat','owner','rows'),('wechat','owner','bytes'),('wechat','owner','cell');
    WITH RECURSIVE n(i) AS (VALUES (1) UNION ALL SELECT i+1 FROM n WHERE i<1001)
      INSERT INTO messages(platform,account_id,conversation_id,id,timestamp,text)
      SELECT 'wechat','owner','rows','r'||i,i,'tiny' FROM n;
    WITH RECURSIVE n(i) AS (VALUES (1) UNION ALL SELECT i+1 FROM n WHERE i<70)
      INSERT INTO messages(platform,account_id,conversation_id,id,timestamp,text)
      SELECT 'wechat','owner','bytes','b'||i,i,printf('%65536s','x') FROM n;
    INSERT INTO messages(platform,account_id,conversation_id,id,timestamp,text)
      VALUES ('wechat','owner','cell','too-large',1,printf('%65537s','x'));
    ").unwrap();
    db.close().unwrap();
    let (grants, owner, _) = export_grants(directory.path());
    let (_server, addr) = start(&source, &grants);
    let fields = [("Authorization", owner.as_str())];
    for conversation in ["rows","bytes","cell"] {
        let (status, headers, body) = request(addr, "GET", &format!("{EXPORT}?conversation_id={conversation}&format=csv"), &fields);
        assert_eq!((status,body),(413,json!({"error":{"code":"archive_response_too_large"}})));
        assert!(!headers.contains("content-disposition"));
    }
    let (status, _, data) = request(addr, "GET", &format!("{EXPORT}?conversation_id=rows&start=1&end=1000"), &fields);
    assert_eq!(status, 200);
    assert_eq!(data.as_array().unwrap().len(), 1000);
}

#[test]
#[ignore = "requires the pinned stock Python hub.exports module (PYTHONPATH)"]
fn safe_export_json_and_csv_match_the_pinned_python_renderer_bytes() {
    let directory = tempfile::tempdir().unwrap();
    let source = directory.path().join("index.sqlite3");
    export_seed(&source);
    let (grants, owner, _) = export_grants(directory.path());
    let (_server, addr) = start(&source, &grants);
    let fields = [("Authorization", owner.as_str())];
    let script = r#"
from hub.exports import write_export
import sys
def record(ident, timestamp, text, sender_name):
    return dict(id=ident,platform="wechat",account_id="owner",conversation_id="room",
        conversation_title="Fixture conversation",sender_id="friend",sender_name=sender_name,
        timestamp=timestamp,type="text",text=text,is_self=False)
fixture = [record('m1',6,'earlier',None), record('m2',7,'example first',None),
           record('m3',7,'  =计算, "引号"\r\n第二行','@公式')]
write_export(fixture, sys.stdout.buffer, sys.argv[1])
"#;
    for format in ["json","csv"] {
        let proc = Command::new("python3").args(["-B","-c",script,format]).output().unwrap();
        assert!(proc.status.success(), "{}", String::from_utf8_lossy(&proc.stderr));
        let (status, _, output) = request_raw(addr,"GET",&format!("{EXPORT}?conversation_id=room&format={format}"),&fields);
        assert_eq!(status, 200);
        assert_eq!(output.as_bytes(), proc.stdout.as_slice());
    }
}

#[test]
fn snapshot_projection_and_http_preserve_notes_and_linked_identity_across_colliding_keys() {
    let source = tempfile::tempdir().unwrap();
    let index = source.path().join("index.sqlite3");
    let db = Connection::open(&index).unwrap();
    db.execute_batch(include_str!("fixtures/index-v9.sql")).unwrap();
    db.execute_batch(r#"
        INSERT INTO conversations(platform,account_id,id,title,last_timestamp) VALUES
          ('wechat','peer','room','peer title',8),('qq','qq-owner','room','QQ title',9);
        INSERT INTO contacts(platform,account_id,id,name,kind) VALUES
          ('wechat','peer','friend','peer contact','friend'),('qq','qq-owner','friend','QQ contact','friend');
        INSERT INTO conversation_metadata VALUES
          ('wechat','owner','room','owner note','["owner-tag"]',1,7),
          ('wechat','peer','room','peer note','["peer-tag"]',0,8),
          ('qq','qq-owner','room','QQ note','["qq-tag"]',1,9);
        INSERT INTO contact_identities VALUES
          ('wechat','owner','friend','identity-linked',7),
          ('wechat','peer','friend','identity-distinct',8),
          ('qq','qq-owner','friend','identity-linked',9);
    "#).unwrap();
    db.close().unwrap();
    let before = fs::read(&index).unwrap();
    let target = tempfile::tempdir().unwrap();
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(target.path(), fs::Permissions::from_mode(0o700)).unwrap();
    }
    let result = Command::new(env!("CARGO_BIN_EXE_local-message-hub-rs"))
        .arg("project-snapshot").arg("--source").arg(&index)
        .arg("--directory").arg(target.path()).output().unwrap();
    assert!(result.status.success(), "{}", String::from_utf8_lossy(&result.stderr));
    let specs = [
        ("wechat", "owner", "owner note", "owner-tag", true, "identity-linked"),
        ("wechat", "peer", "peer note", "peer-tag", false, "identity-distinct"),
        ("qq", "qq-owner", "QQ note", "qq-tag", true, "identity-linked"),
    ];
    let mut grant_records = vec![];
    let mut bearers = vec![];
    for (platform,account,..) in specs {
        let (token,hash) = generate_token().unwrap();
        bearers.push(format!("Bearer {token}"));
        grant_records.push(json!({
            "id":format!("fixture.{account}"),"kind":"desktop","permissions":["conversations.read","contacts.read"],
            "token_sha256":hash,"account_scope":{"platform":platform,"account_id":account}
        }));
    }
    let grants = source.path().join("grants.json");
    fs::write(&grants, json!({"version":1,"grants":grant_records}).to_string()).unwrap();
    let (_server, addr) = start(&target.path().join("index.sqlite3"), &grants);
    for ((platform,account,note,tag,archived,identity),bearer) in specs.into_iter().zip(bearers) {
        let fields = [("Authorization",bearer.as_str())];
        let (status,_,conversations) = request(addr,"GET","/api/v1/conversations",&fields);
        assert_eq!(status,200);
        assert_eq!(conversations["total"],1);
        let room = &conversations["items"][0];
        assert_eq!(room["id"],"room");
        assert_eq!(room["platform"],platform);
        assert_eq!(room["account_id"],account);
        assert_eq!(room["note"],note);
        assert_eq!(room["tags"],json!([tag]));
        assert_eq!(room["archived"],archived);
        let (status,_,contacts) = request(addr,"GET","/api/v1/contacts",&fields);
        assert_eq!(status,200);
        assert_eq!(contacts["total"],1);
        assert_eq!(contacts["accounts"],json!([{"platform":platform,"account_id":account}]));
        assert_eq!(contacts["items"][0]["identity_id"],identity);
    }
    assert_eq!(fs::read(&index).unwrap(),before);
}

#[test]
fn metadata_permission_does_not_enable_writes_on_detached_snapshots() {
    let dir = tempfile::tempdir().unwrap();
    let snapshot = dir.path().join("index.sqlite3");
    let db = Connection::open(&snapshot).unwrap();
    db.execute_batch(include_str!("fixtures/index-v9.sql")).unwrap();
    db.close().unwrap();
    let original = fs::read(&snapshot).unwrap();
    let (token, hash) = generate_token().unwrap();
    let grants = dir.path().join("grants.json");
    fs::write(&grants, json!({"version":1,"grants":[{
        "id":"fixture.metadata","kind":"desktop","permissions":["conversations.read","conversations.metadata.write","metadata.audit.read"],
        "token_sha256":hash,"account_scope":{"platform":"wechat","account_id":"owner"}
    }]}).to_string()).unwrap();
    let (_server, addr) = start(&snapshot,&grants);
    let auth = format!("Bearer {token}");
    let headers = [("Authorization",auth.as_str())];
    let (_, _, manifest) = request(addr,"GET","/api/v1/manifest",&headers);
    assert_eq!(manifest["resources"]["conversation_metadata"],Value::Null);
    assert_eq!(manifest["resources"]["metadata_audit"],Value::Null);
    assert_eq!(request(addr,"POST","/api/v1/metadata/conversation",&headers).0,405);
    assert_eq!(request(addr,"GET","/api/v1/metadata/conversation?id=room",&headers).0,404);
    assert_eq!(fs::read(&snapshot).unwrap(),original);
}
