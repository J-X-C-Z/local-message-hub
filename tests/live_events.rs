use local_message_hub_rs::plugins::generate_token;
use serde_json::{Value, json};
use std::{
    fs,
    io::{self, BufRead, BufReader, Read, Write},
    net::{SocketAddr, TcpStream},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::mpsc,
    time::Duration,
};

const TIMEOUT: Duration = Duration::from_secs(8);
const CHECKPOINT: &str = "/api/v1/events/checkpoint";
const STREAM: &str = "/api/v1/events/stream";
const INGEST: &str = "/api/v1/ingest";

struct Server {
    task: Child,
    address: SocketAddr,
}

impl Drop for Server {
    fn drop(&mut self) {
        let _ = self.task.kill();
        let _ = self.task.wait();
    }
}

impl Server {
    fn start(data: &Path, grants: &Path) -> Self {
        let mut task = binary()
            .args(["serve", "--live"])
            .arg(data)
            .arg("--grants")
            .arg(grants)
            .stdout(Stdio::piped())
            .spawn()
            .unwrap();
        let mut reader = BufReader::new(task.stdout.take().unwrap());
        let (send, receive) = mpsc::channel();
        std::thread::spawn(move || {
            let mut line = String::new();
            if reader.read_line(&mut line).is_ok() {
                let _ = send.send(line);
            }
        });
        let mut server = Self {
            task,
            address: SocketAddr::from(([127, 0, 0, 1], 0)),
        };
        let ready: Value = serde_json::from_str(&receive.recv_timeout(TIMEOUT).unwrap()).unwrap();
        assert_eq!(ready["event"], "ready");
        assert_eq!(ready["api_version"], 1);
        server.address = ready["origin"]
            .as_str()
            .unwrap()
            .strip_prefix("http://")
            .unwrap()
            .parse()
            .unwrap();
        assert!(server.address.ip().is_loopback());
        server
    }

    fn dial(
        &self,
        method: &str,
        path: &str,
        token: Option<&str>,
        headers: &[(&str, &str)],
        body: &str,
    ) -> (u16, Vec<String>, BufReader<TcpStream>) {
        let mut conn = TcpStream::connect_timeout(&self.address, TIMEOUT).unwrap();
        conn.set_read_timeout(Some(TIMEOUT)).unwrap();
        conn.set_write_timeout(Some(TIMEOUT)).unwrap();
        write!(
            conn,
            "{method} {path} HTTP/1.1\r\nHost: {}\r\nConnection: close\r\n",
            self.address
        )
        .unwrap();
        if let Some(token) = token {
            write!(conn, "Authorization: Bearer {token}\r\n").unwrap();
        }
        for (k, v) in headers {
            write!(conn, "{k}: {v}\r\n").unwrap();
        }
        if !body.is_empty() {
            write!(conn, "Content-Length: {}\r\n", body.len()).unwrap();
        }
        write!(conn, "\r\n{body}").unwrap();
        conn.flush().unwrap();
        let mut conn = BufReader::new(conn);
        let mut line = String::new();
        conn.read_line(&mut line).unwrap();
        let status = line.split_whitespace().nth(1).unwrap().parse().unwrap();
        let mut headers = Vec::new();
        loop {
            line.clear();
            assert_ne!(
                conn.read_line(&mut line).unwrap(),
                0,
                "EOF reading response headers"
            );
            if line == "\r\n" {
                break;
            }
            headers.push(line.trim().to_ascii_lowercase());
        }
        (status, headers, conn)
    }

    fn request(
        &self,
        method: &str,
        path: &str,
        token: Option<&str>,
        headers: &[(&str, &str)],
        body: &str,
    ) -> (u16, Value) {
        let (status, _, mut conn) = self.dial(method, path, token, headers, body);
        let mut value = Vec::new();
        conn.read_to_end(&mut value).unwrap();
        (status, serde_json::from_slice(&value).unwrap())
    }

    fn get(&self, path: &str, token: &str) -> (u16, Value) {
        self.request("GET", path, Some(token), &[], "")
    }

    fn ingest(&self, token: &str, body: &Value) -> (u16, Value) {
        self.request(
            "POST",
            INGEST,
            Some(token),
            &[("Content-Type", "application/json")],
            &body.to_string(),
        )
    }

    fn stream(&self, token: &str, cursor: Option<&str>) -> SseReader {
        let mut headers = vec![];
        if let Some(cursor) = cursor {
            headers.push(("Last-Event-ID", cursor));
        }
        let (status, headers, reader) = self.dial("GET", STREAM, Some(token), &headers, "");
        assert_eq!(status, 200);
        assert!(
            headers
                .iter()
                .any(|header| header == "content-type: text/event-stream")
        );
        assert!(
            headers
                .iter()
                .any(|header| header == "cache-control: no-store")
        );
        assert!(
            headers
                .iter()
                .any(|header| header == "transfer-encoding: chunked")
        );
        SseReader {
            reader,
            pending: Vec::new(),
        }
    }
}

struct SseReader {
    reader: BufReader<TcpStream>,
    pending: Vec<u8>,
}

impl SseReader {
    fn chunk(&mut self) -> io::Result<()> {
        let mut len = String::new();
        self.reader.read_line(&mut len)?;
        let len = usize::from_str_radix(len.trim(), 16)
            .map_err(|_| io::Error::new(io::ErrorKind::InvalidData, "invalid chunk length"))?;
        if len == 0 || len > 64 * 1024 {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "unexpected chunk size",
            ));
        }
        let mut chunk = vec![0; len + 2];
        self.reader.read_exact(&mut chunk)?;
        if &chunk[len..] != b"\r\n" {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "invalid chunk end",
            ));
        }
        self.pending.extend_from_slice(&chunk[..len]);
        Ok(())
    }

    fn event(&mut self) -> (String, String, Value) {
        loop {
            if let Some(end) = self.pending.windows(2).position(|window| window == b"\n\n") {
                let frame: Vec<_> = self.pending.drain(..end + 2).collect();
                let frame = String::from_utf8(frame).unwrap();
                let mut event = None;
                let mut id = None;
                let mut data = None;
                for line in frame.lines() {
                    if let Some(v) = line.strip_prefix("event: ") {
                        event = Some(v.to_owned());
                    }
                    if let Some(v) = line.strip_prefix("id: ") {
                        id = Some(v.to_owned());
                    }
                    if let Some(v) = line.strip_prefix("data: ") {
                        data = Some(serde_json::from_str(v).unwrap());
                    }
                }
                if let Some(event) = event {
                    return (event, id.unwrap(), data.unwrap());
                }
            } else {
                self.chunk().unwrap();
            }
        }
    }
}

fn binary() -> Command {
    Command::new(env!("CARGO_BIN_EXE_local-message-hub-rs"))
}

fn init(dir: &Path) {
    let output = binary()
        .args(["init-live", "--directory"])
        .arg(dir)
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
}

struct Fixture {
    _temp: tempfile::TempDir,
    live: PathBuf,
    grants: PathBuf,
    desktop: String,
    producer: String,
}

impl Fixture {
    fn new() -> Self {
        let tmp = tempfile::tempdir().unwrap();
        let live = tmp.path().join("rust-live");
        fs::create_dir(&live).unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(&live, fs::Permissions::from_mode(0o700)).unwrap();
        }
        init(&live);
        let grants = tmp.path().join("grants.json");
        let (desktop, desktop_hash) = generate_token().unwrap();
        let (producer, producer_hash) = generate_token().unwrap();
        fs::write(&grants, json!({"version":1,"grants":[
            {"id":"fixture.desktop","kind":"desktop","permissions":["events.read","messages.read","conversations.read"],"token_sha256":desktop_hash},
            {"id":"fixture.producer","kind":"producer","permissions":["messages.ingest"],"token_sha256":producer_hash}
        ]}).to_string()).unwrap();
        Self {
            _temp: tmp,
            live,
            grants,
            desktop,
            producer,
        }
    }

    fn start(&self) -> Server {
        Server::start(&self.live, &self.grants)
    }
}

fn batch(key: &str, text: &str) -> Value {
    json!({"idempotency_key":key,"messages":[{
        "platform":"wechat","account_id":"fixture","conversation_id":"room","message_id":"m-1",
        "title":"Synthetic title","sender_id":"synthetic-sender","sender_name":"Synthetic name",
        "is_self":false,"timestamp":1720000000,"text":text
    }]})
}

fn event_id(record: &Value) -> i64 {
    // Assert the complete wire allowlist, not just the absence of a field named text.
    let object = record.as_object().unwrap();
    assert_eq!(object.len(), 7);
    for field in [
        "event_id",
        "kind",
        "platform",
        "account_id",
        "conversation_id",
        "message_id",
        "timestamp",
    ] {
        assert!(object.contains_key(field));
    }
    record["event_id"].as_i64().unwrap()
}

#[test]
fn desktop_bootstrap_catches_gap_and_reconnects_without_replay_or_body_after_restart() {
    let fixture = Fixture::new();
    let server = fixture.start();
    let initial = server.get(CHECKPOINT, &fixture.desktop);
    assert_eq!(initial.0, 200);
    assert_eq!(initial.1["checkpoint"], 0);
    let zero = initial.1["cursor"].as_str().unwrap();
    assert_eq!(
        server
            .get(
                "/api/v1/messages?platform=wechat&account_id=fixture&conversation_id=room",
                &fixture.desktop
            )
            .1["total"],
        0
    );

    // This commit is deliberately between the initial read and the SSE attachment.
    let create = batch("try-create", "fixture body must NOT appear in any event");
    let receipt = server.ingest(&fixture.producer, &create);
    assert_eq!(receipt.0, 200);
    assert_eq!(receipt.1["created"], 1);
    assert_eq!(receipt.1["checkpoint"], 1);
    let mut old_stream = server.stream(&fixture.desktop, Some(zero));
    assert_eq!(
        old_stream.event(),
        ("checkpoint".to_owned(), zero.to_owned(), initial.1.clone())
    );
    let (name, first, data) = old_stream.event();
    assert_eq!(name, "message.created");
    assert_eq!(data["kind"], "created");
    assert_eq!(event_id(&data), 1);
    assert_eq!(data["message_id"], "m-1");

    // A tokenless cursor is not an account identity. Fresh desktop connections skip history.
    let mut fresh = server.stream(&fixture.desktop, None);
    assert_eq!(
        fresh.event(),
        (
            "checkpoint".into(),
            first.clone(),
            json!({"checkpoint":1,"cursor":first})
        )
    );
    drop(old_stream);
    let same_receipt = server.ingest(&fixture.producer, &create);
    assert_eq!(same_receipt, receipt);
    let conflicting = server.ingest(&fixture.producer, &batch("try-create", "different"));
    assert_eq!(
        (conflicting.0, &conflicting.1["error"]["code"]),
        (409, &json!("idempotency_conflict"))
    );
    let revise = batch("try-revise", "revised fixture body never sent on stream");
    assert_eq!(server.ingest(&fixture.producer, &revise).1["revised"], 1);
    let (name, second, revised) = fresh.event();
    assert_eq!(name, "message.revised");
    assert_eq!(revised["kind"], "revised");
    assert_eq!(event_id(&revised), 2);
    assert_eq!(revised["message_id"], "m-1");
    drop(fresh);

    let first_page = server.get(
        &format!("/api/v1/events?cursor={zero}&limit=1"),
        &fixture.desktop,
    );
    assert_eq!(first_page.0, 200);
    assert_eq!(event_id(&first_page.1["items"][0]), 1);
    assert_eq!(first_page.1["items"].as_array().unwrap().len(), 1);
    assert_eq!(first_page.1["has_more"], true);
    assert_eq!(first_page.1["next_cursor"], first);
    assert_eq!(first_page.1["checkpoint"], 2);
    drop(server);

    let server = fixture.start();
    let restart = server.get(CHECKPOINT, &fixture.desktop);
    assert_eq!(restart.1["cursor"], second);
    let tail = server.get(
        &format!("/api/v1/events?cursor={first}&limit=1"),
        &fixture.desktop,
    );
    assert_eq!(tail.1["items"].as_array().unwrap().len(), 1);
    assert_eq!(event_id(&tail.1["items"][0]), 2);
    assert_eq!(tail.1["next_cursor"], second);
    assert_eq!(tail.1["has_more"], false);
    let mut resumed = server.stream(&fixture.desktop, Some(&second));
    assert_eq!(
        resumed.event(),
        ("checkpoint".into(), second.clone(), restart.1)
    );
    let no_duplicate = server.ingest(&fixture.producer, &revise);
    assert_eq!(no_duplicate.1["checkpoint"], 2);
    let mut third = batch("next-msg", "synthetic next message");
    third["messages"][0]["message_id"] = json!("m-2");
    assert_eq!(server.ingest(&fixture.producer, &third).1["checkpoint"], 3);
    let (name, _, data) = resumed.event();
    assert_eq!(name, "message.created");
    assert_eq!(event_id(&data), 3); // would be 1 or 2 if an acknowledged record was replayed
    assert_eq!(data["message_id"], "m-2");
}

#[test]
fn rejects_stale_ambiguous_cursors_ungranted_ingest_and_strict_json_before_mutation() {
    let fixture = Fixture::new();
    let server = fixture.start();
    let checkpoint = server.get(CHECKPOINT, &fixture.desktop).1;
    let cursor = checkpoint["cursor"].as_str().unwrap();
    let generation = cursor.split(':').next().unwrap();
    let alien_archive = fixture.live.with_file_name("other");
    fs::create_dir(&alien_archive).unwrap();
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&alien_archive, fs::Permissions::from_mode(0o700)).unwrap();
    }
    init(&alien_archive);
    let alien = Server::start(&alien_archive, &fixture.grants);
    let alien_zero = alien.get(CHECKPOINT, &fixture.desktop).1;
    let alien_cursor = alien_zero["cursor"].as_str().unwrap();
    assert_ne!(cursor, alien_cursor);

    assert_eq!(server.request("GET", CHECKPOINT, None, &[], "").0, 401);
    assert_eq!(server.get(CHECKPOINT, &fixture.producer).0, 403);
    assert_eq!(server.get(STREAM, &fixture.producer).0, 403);
    assert_eq!(
        server.ingest(&fixture.desktop, &batch("denied", "no")).0,
        403
    );
    for bad in [
        format!("{generation}:1"),
        format!("{generation}:-1"),
        alien_cursor.into(),
        "0".into(),
    ] {
        let (status, result) = server.get(&format!("{STREAM}?cursor={bad}"), &fixture.desktop);
        assert_eq!(
            (status, &result["error"]["code"]),
            (400, &json!("bad_event_cursor"))
        );
        assert_eq!(
            server
                .get(&format!("/api/v1/events?cursor={bad}"), &fixture.desktop)
                .0,
            400
        );
    }
    assert_eq!(
        server
            .request(
                "GET",
                &format!("{STREAM}?cursor={cursor}"),
                Some(&fixture.desktop),
                &[("Last-Event-ID", cursor)],
                ""
            )
            .0,
        400
    );
    assert_eq!(
        server
            .request(
                "GET",
                STREAM,
                Some(&fixture.desktop),
                &[("Last-Event-ID", cursor), ("Last-Event-ID", cursor)],
                ""
            )
            .0,
        400
    );
    assert_eq!(
        server
            .get(
                &format!("/api/v1/events?cursor={cursor}&limit=101"),
                &fixture.desktop
            )
            .0,
        400
    );

    let mut malformed = batch("bad", "never committed");
    malformed["messages"][0]["unknown_field"] = json!("should be rejected");
    assert_eq!(server.ingest(&fixture.producer, &malformed).0, 400);
    let large = "x".repeat(530 * 1024 + 1);
    assert_eq!(
        server
            .request(
                "POST",
                INGEST,
                Some(&fixture.producer),
                &[("Content-Type", "application/json")],
                &large
            )
            .0,
        413
    );
    assert_eq!(
        server
            .request(
                "POST",
                INGEST,
                Some(&fixture.producer),
                &[("Content-Type", "text/plain")],
                "{}"
            )
            .0,
        415
    );
    assert_eq!(server.get(CHECKPOINT, &fixture.desktop).1, checkpoint);
}

fn add_scoped_desktop(fixture: &Fixture, id: &str, account: &str) -> String {
    let (token, token_hash) = generate_token().unwrap();
    let mut grants: Value = serde_json::from_slice(&fs::read(&fixture.grants).unwrap()).unwrap();
    grants["grants"].as_array_mut().unwrap().push(json!({
        "id":id,"kind":"desktop","token_sha256":token_hash,
        "permissions":["events.read","messages.read","contacts.read","conversations.read"],
        "account_scope":{"platform":"wechat","account_id":account}
    }));
    fs::write(&fixture.grants, grants.to_string()).unwrap();
    token
}

fn message_for(key: &str, account: &str, id: &str) -> Value {
    let mut batch = batch(key, &format!("synthetic payload from {account} {id}"));
    batch["messages"][0]["account_id"] = json!(account);
    batch["messages"][0]["message_id"] = json!(id);
    batch["messages"][0]["title"] = json!(format!("only {account}"));
    batch
}

#[test]
fn exact_desktop_scope_isolates_reads_replay_and_resume_after_restart() {
    let fixture = Fixture::new();
    let desktop = add_scoped_desktop(&fixture, "fixture.scoped", "fixture");
    let peer = add_scoped_desktop(&fixture, "fixture.peer", "other");
    let peer_same_account = add_scoped_desktop(&fixture, "fixture.same", "fixture");
    let mut server = fixture.start();
    let (_, initial) = server.get(CHECKPOINT, &desktop);
    let zero = initial["cursor"].as_str().unwrap();
    assert_eq!(initial["checkpoint"], 0);
    assert!(zero.starts_with("a-"));
    assert_eq!(
        server.get(&format!("/api/v1/events?cursor={zero}"), &peer).0,
        400
    );
    assert_eq!(
        server.get(&format!("/api/v1/events?cursor={zero}"), &peer_same_account).0,
        400
    );
    assert_eq!(
        server.get(&format!("/api/v1/events?cursor={zero}"), &fixture.desktop).0,
        400
    );

    // Same platform, conversation and message IDs across accounts.
    assert_eq!(server.ingest(&fixture.producer, &message_for("peer-1", "other", "m-1")).0, 200);
    assert_eq!(server.get(CHECKPOINT, &desktop).1, initial);
    assert_eq!(server.ingest(&fixture.producer, &message_for("own-1", "fixture", "m-1")).0, 200);
    let checkpoint_before_foreign = server.get(CHECKPOINT, &desktop).1;
    assert_eq!(checkpoint_before_foreign["checkpoint"], 1);
    assert_eq!(server.ingest(&fixture.producer, &message_for("peer-2", "other", "m-2")).0, 200);
    assert_eq!(server.get(CHECKPOINT, &desktop).1, checkpoint_before_foreign);
    assert_eq!(server.ingest(&fixture.producer, &message_for("own-2", "fixture", "m-2")).0, 200);
    assert_eq!(server.get(CHECKPOINT, &fixture.desktop).1["checkpoint"], 4);
    assert_eq!(server.get(CHECKPOINT, &desktop).1["checkpoint"], 2);

    // Contacts are not writable through normalized ingest; seed only disposable test data.
    let db = rusqlite::Connection::open(fixture.live.join("native-v1.sqlite3")).unwrap();
    db.execute(
        "INSERT INTO contacts(platform,account_id,id,name) VALUES
         ('wechat','fixture','same','fixture contact'),('wechat','other','same','foreign contact')",
        [],
    ).unwrap();
    drop(db);
    let path = "/api/v1/conversations";
    let (status, data) = server.get(path, &desktop);
    assert_eq!(status, 200);
    assert_eq!(data["total"], 1);
    assert_eq!(data["items"][0]["account_id"], "fixture");
    assert_eq!(data["items"][0]["title"], "only fixture");
    assert_eq!(server.get(&format!("{path}?account_id=other"), &desktop).0, 403);
    assert_eq!(server.get(&format!("{path}?platform=qq"), &desktop).0, 403);

    let (_, contacts) = server.get("/api/v1/contacts?platform=all", &desktop);
    assert_eq!(contacts["total"], 1);
    assert_eq!(contacts["accounts"], json!([{"platform":"wechat","account_id":"fixture"}]));
    assert_eq!(contacts["items"][0]["name"], "fixture contact");
    let (_, search) = server.get("/api/v1/search?q=synthetic&limit=1", &desktop);
    assert_eq!(search["total"], 2);
    assert_eq!(search["items"][0]["id"], "m-2");
    let search_cursor = search["next_cursor"].as_str().unwrap();
    assert_eq!(server.get(&format!("/api/v1/search?q=synthetic&limit=1&cursor={search_cursor}"), &peer).0, 400);
    let (_, search_next) = server.get(&format!("/api/v1/search?q=synthetic&limit=1&cursor={search_cursor}"), &desktop);
    assert_eq!(search_next["items"][0]["id"], "m-1");

    let (status, page) = server.get("/api/v1/messages?conversation_id=room&limit=1", &desktop);
    assert_eq!(status, 200);
    assert_eq!(page["total"], 2);
    assert_eq!(page["items"][0]["id"], "m-2");
    let page_cursor = page["next_cursor"].as_str().unwrap();
    use base64::{Engine as _, engine::general_purpose::URL_SAFE_NO_PAD};
    let cursor_payload: Value = serde_json::from_slice(&URL_SAFE_NO_PAD.decode(page_cursor).unwrap()).unwrap();
    assert_eq!(URL_SAFE_NO_PAD.decode(cursor_payload[1].as_str().unwrap()).unwrap(), b"m-2");
    // The cursor never discloses an archive row ID.
    let (_, next) = server.get(&format!("/api/v1/messages?conversation_id=room&limit=1&cursor={page_cursor}"), &desktop);
    assert_eq!(next["items"][0]["id"], "m-1");
    let (status, context) = server.get("/api/v1/messages/context?conversation_id=room&message_id=m-1", &desktop);
    assert_eq!(status, 200);
    assert!(context.to_string().contains("synthetic payload from fixture"));
    assert!(!context.to_string().contains("synthetic payload from other"));
    assert_eq!(server.get("/api/v1/messages/context?account_id=other&conversation_id=room&message_id=m-1", &desktop).0, 404);
    assert_eq!(server.get("/api/v1/messages/quoted-context?account_id=other&conversation_id=room&reference_message_id=m-1", &desktop).0, 404);

    let (_, replay) = server.get(&format!("/api/v1/events?cursor={zero}&limit=1"), &desktop);
    assert_eq!(replay["checkpoint"], 2);
    assert_eq!(replay["next_after"], 1);
    assert_eq!(replay["has_more"], true);
    assert_eq!(replay["items"][0]["account_id"], "fixture");
    let saved = replay["next_cursor"].as_str().unwrap().to_owned();
    drop(server);
    server = fixture.start();
    let mut stream = server.stream(&desktop, Some(&saved));
    assert_eq!(stream.event().1, saved);
    let (kind, cursor, data) = stream.event();
    assert_eq!(kind, "message.created");
    assert_eq!(cursor, format!("{}:2", zero.split_once(':').unwrap().0));
    assert_eq!(event_id(&data), 2);
    assert_eq!(data["account_id"], "fixture");
    assert_eq!(data["message_id"], "m-2");
}

#[test]
fn old_archive_scope_upgrade_preserves_legacy_checkpoint_and_backfills_before_new_events() {
    let fixture = Fixture::new();
    let desktop = add_scoped_desktop(&fixture, "fixture.scoped", "fixture");
    let db = rusqlite::Connection::open(fixture.live.join("native-v1.sqlite3")).unwrap();
    db.execute_batch("DROP TRIGGER native_account_event_record; DROP TABLE native_account_event_sequence;").unwrap();
    drop(db);
    let server = fixture.start();
    assert_eq!(server.get(CHECKPOINT, &desktop), (503, json!({"error":{"code":"account_event_upgrade_required"}})));
    let zero = server.get(CHECKPOINT, &fixture.desktop).1["cursor"].as_str().unwrap().to_owned();
    assert_eq!(server.ingest(&fixture.producer, &message_for("peer-1", "other", "m-1")).0, 200);
    assert_eq!(server.ingest(&fixture.producer, &message_for("own-1", "fixture", "m-1")).0, 200);
    let old_head = server.get(CHECKPOINT, &fixture.desktop).1;
    drop(server);
    for _ in 0..2 {
        let output = binary().args(["upgrade-event-scopes","--directory"]).arg(&fixture.live).output().unwrap();
        assert!(output.status.success(), "{}", String::from_utf8_lossy(&output.stderr));
    }
    let server = fixture.start();
    assert_eq!(server.get(CHECKPOINT, &fixture.desktop).1, old_head);
    assert_eq!(server.get(&format!("/api/v1/events?cursor={zero}"), &fixture.desktop).1["checkpoint"], 2);
    let one = server.get(CHECKPOINT, &desktop).1;
    assert_eq!(one["checkpoint"], 1);
    let one = one["cursor"].as_str().unwrap();
    assert_eq!(server.ingest(&fixture.producer, &message_for("own-2", "fixture", "m-2")).0, 200);
    let (_, replay) = server.get(&format!("/api/v1/events?cursor={one}"), &desktop);
    assert_eq!(replay["checkpoint"], 2);
    assert_eq!(replay["items"][0]["event_id"], 2);
    assert_eq!(replay["items"][0]["message_id"], "m-2");
}


#[test]
fn native_live_desktop_export_reads_committed_rows_from_only_its_granted_conversation() {
    let fixture = Fixture::new();
    let desktop = add_scoped_desktop(&fixture, "fixture.scoped-export", "fixture");
    let mut grants: Value = serde_json::from_slice(&fs::read(&fixture.grants).unwrap()).unwrap();
    grants["grants"].as_array_mut().unwrap().last_mut().unwrap()["permissions"]
        .as_array_mut().unwrap().push(json!("messages.export"));
    fs::write(&fixture.grants, grants.to_string()).unwrap();
    let server = fixture.start();
    assert_eq!(server.ingest(&fixture.producer, &message_for("peer-1", "other", "m-1")).0, 200);
    assert_eq!(server.ingest(&fixture.producer, &message_for("owner-1", "fixture", "m-1")).0, 200);
    let (status, body) = server.get("/api/v1/messages/export?conversation_id=room", &desktop);
    assert_eq!(status, 200);
    assert_eq!(body.as_array().unwrap().len(), 1);
    assert_eq!(body[0]["account_id"], "fixture");
    assert_eq!(body[0]["text"], "synthetic payload from fixture m-1");
    assert_eq!(server.get("/api/v1/messages/export?conversation_id=room", &fixture.desktop).0, 403);
}

const CONVERSATION_META: &str = "/api/v1/metadata/conversation";
const CONTACT_META: &str = "/api/v1/metadata/contact";
const META_AUDIT: &str = "/api/v1/metadata/audit";

fn metadata_grant(fixture: &Fixture, id: &str, account: &str, permissions: &[&str]) -> String {
    let (token, token_hash) = generate_token().unwrap();
    let mut grants: Value = serde_json::from_slice(&fs::read(&fixture.grants).unwrap()).unwrap();
    grants["grants"].as_array_mut().unwrap().push(json!({
        "id":id,"kind":"desktop","token_sha256":token_hash,"permissions":permissions,
        "account_scope":{"platform":"wechat","account_id":account}
    }));
    fs::write(&fixture.grants, grants.to_string()).unwrap();
    token
}

fn metadata_seed(fixture: &Fixture, server: &Server) {
    assert_eq!(server.ingest(&fixture.producer, &message_for("owner-1", "fixture", "m-1")).0, 200);
    assert_eq!(server.ingest(&fixture.producer, &message_for("peer-1", "other", "m-1")).0, 200);
    let db = rusqlite::Connection::open(fixture.live.join("native-v1.sqlite3")).unwrap();
    db.execute_batch("
        INSERT INTO contacts(platform,account_id,id,name) VALUES
          ('wechat','fixture','friend','own contact'),
          ('wechat','other','friend','other contact');
    ").unwrap();
}

fn metadata_post(server: &Server, route: &str, token: &str, body: Value) -> (u16,Value) {
    server.request("POST",route,Some(token),&[("Content-Type","application/json")],&body.to_string())
}

#[test]
fn metadata_edits_are_scoped_revisioned_durable_and_audited_without_values() {
    let fixture = Fixture::new();
    let editor = metadata_grant(&fixture,"meta.owner","fixture", &[
        "conversations.read","contacts.read","conversations.metadata.write","contacts.identity.write"
    ]);
    let auditor = metadata_grant(&fixture,"meta.audit","fixture", &["metadata.audit.read"]);
    let peer = metadata_grant(&fixture,"meta.peer","other", &[
        "conversations.read","conversations.metadata.write","contacts.read","contacts.identity.write","metadata.audit.read"
    ]);
    let server = fixture.start();
    metadata_seed(&fixture,&server);
    let old_checkpoint=server.get(CHECKPOINT,&fixture.desktop).1;
    let scope = json!({"id":"room"});
    let old = server.get(&format!("{CONVERSATION_META}?id=room"), &editor);
    assert_eq!(old, (200,json!({"revision":0,"metadata":{"note":"","tags":[],"archived":false}})));
    let create = json!({"target":scope,"expected_revision":0,
        "note":"private-note-marker","tags":[" work ","work"," 标签 "],"archived":true});
    let updated = metadata_post(&server,CONVERSATION_META,&editor,create.clone());
    assert_eq!(updated,(200,json!({"changed":true,"revision":1,
        "metadata":{"note":"private-note-marker","tags":["work","标签"],"archived":true}})));
    let retry = metadata_post(&server,CONVERSATION_META,&editor,create.clone());
    assert_eq!(retry.0,200);
    assert_eq!(retry.1["changed"],false);
    assert_eq!(retry.1["revision"],1);

    let conflict = metadata_post(&server,CONVERSATION_META,&editor,
        json!({"target":scope,"expected_revision":0,"note":"attempted-private-note"}));
    assert_eq!(conflict,(409,json!({"error":{"code":"metadata_revision_conflict"}})));
    assert!(!conflict.1.to_string().contains("private"));
    let foreign = metadata_post(&server,CONVERSATION_META,&editor,
        json!({"target":{"id":"room","account_id":"other"},"expected_revision":0,"note":"foreign attempt"}));
    assert_eq!(foreign,(404,json!({"error":{"code":"not_found"}})));
    assert_eq!(server.get(&format!("{CONVERSATION_META}?id=room&account_id=other"),&editor),foreign);
    assert_eq!(server.get(&format!("{CONVERSATION_META}?id=room"),&peer).1["revision"],0);
    assert_eq!(server.get(&format!("{CONVERSATION_META}?id=room"),&peer).1["metadata"]["note"],"");

    for body in [
        json!({"target":scope,"expected_revision":1,"tags":["  "]}),
        json!({"target":scope,"expected_revision":1,"tags":["x".repeat(65)]}),
        json!({"target":scope,"expected_revision":1,"tags":vec!["x";51]}),
        json!({"target":scope,"expected_revision":1,"note":"x".repeat(4097)}),
        json!({"target":scope,"expected_revision":1,"note":"synthetic\u{0000}control"}),
        json!({"target":scope,"expected_revision":1}),
    ] {
        assert_eq!(metadata_post(&server,CONVERSATION_META,&editor,body).0,400);
    }
    let large = json!({"target":scope,"expected_revision":1,"note":"x".repeat(70_000)});
    assert_eq!(metadata_post(&server,CONVERSATION_META,&editor,large).0,413);
    assert_eq!(metadata_post(&server,CONVERSATION_META,&auditor,create).0,403);
    assert_eq!(server.get(META_AUDIT,&editor).0,403);
    assert_eq!(server.get(&format!("{CONTACT_META}?id=friend"),&editor).1["revision"],0);
    let clear_null = json!({"target":{"id":"friend"},"expected_revision":0,"identity_id":null});
    assert_eq!(metadata_post(&server,CONTACT_META,&editor,clear_null),
        (200,json!({"changed":false,"revision":0,"identity_id":null})));
    assert_eq!(metadata_post(&server,CONTACT_META,&editor,json!({"target":{"id":"friend"},"expected_revision":0})).0,400);
    assert_eq!(metadata_post(&server,CONTACT_META,&editor,
        json!({"target":{"id":"friend"},"expected_revision":0,"identity_id":" explicit-link-marker "})),
        (200,json!({"changed":true,"revision":1,"identity_id":"explicit-link-marker"})));
    assert_eq!(server.get(&format!("{CONTACT_META}?id=friend"),&peer).1["identity_id"],Value::Null);
    assert_eq!(metadata_post(&server,CONTACT_META,&editor,
        json!({"target":{"id":"friend"},"expected_revision":1,"identity_id":null})),
        (200,json!({"changed":true,"revision":2,"identity_id":null})));
    assert_eq!(server.get(CHECKPOINT,&fixture.desktop).1,old_checkpoint);
    let page = server.get(&format!("{META_AUDIT}?limit=2"),&auditor);
    assert_eq!(page.0,200);
    assert_eq!(page.1["next_after"],2);
    assert_eq!(page.1["has_more"],true);
    assert_eq!(page.1["items"][0]["fields"],json!(["note","tags","archived"]));
    assert_eq!(page.1["items"][1]["operation"],"contact.link");
    for forbidden in ["private-note-marker","explicit-link-marker","attempted-private-note",&editor,&auditor] {
        assert!(!page.1.to_string().contains(forbidden));
    }
    assert_eq!(server.get(META_AUDIT,&peer).1["items"],json!([]));

    drop(server);
    let server = fixture.start();
    let last = server.get(&format!("{META_AUDIT}?after=2"),&auditor);
    assert_eq!(last.1["items"][0]["operation"],"contact.clear");
    assert_eq!(last.1["items"][0]["revision"],2);
    assert_eq!(last.1["has_more"],false);
    assert_eq!(server.get(&format!("{CONTACT_META}?id=friend"),&editor).1,
        json!({"revision":2,"identity_id":null}));
    let durable = server.get(&format!("{CONVERSATION_META}?id=room"),&editor);
    assert_eq!(durable.1["metadata"]["note"],"private-note-marker");
    assert_eq!(durable.1["revision"],1);
    let (_, regular) = server.get("/api/v1/conversations",&editor);
    assert_eq!(regular["items"][0]["note"],"private-note-marker");
}

#[test]
fn simultaneous_editors_using_same_revision_do_not_lose_a_write_or_double_audit() {
    let fixture = Fixture::new();
    let editor = metadata_grant(&fixture,"meta.race","fixture", &[
        "conversations.read","conversations.metadata.write","metadata.audit.read"
    ]);
    let server = fixture.start();
    metadata_seed(&fixture,&server);
    let start = std::sync::Barrier::new(2);
    let (mut statuses,winner) = std::thread::scope(|s| {
        let post = |note| {
            start.wait();
            let r = metadata_post(&server,CONVERSATION_META,&editor,
                json!({"target":{"id":"room"},"expected_revision":0,"note":note}));
            (r.0,note)
        };
        let first = s.spawn(move ||post("A"));
        let second = s.spawn(move ||post("B"));
        let (a,b) = (first.join().unwrap(),second.join().unwrap());
        ([a.0,b.0],if a.0==200 {a.1} else {b.1})
    });
    statuses.sort_unstable();
    assert_eq!(statuses,[200,409]);
    assert_eq!(server.get(&format!("{CONVERSATION_META}?id=room"),&editor).1["metadata"]["note"],winner);
    let audit = server.get(META_AUDIT,&editor).1;
    assert_eq!(audit["items"].as_array().unwrap().len(),1);
    assert_eq!(audit["items"][0]["revision"],1);
}

#[test]
fn offline_metadata_upgrade_preserves_both_event_cursors_and_existing_values() {
    let fixture = Fixture::new();
    let desktop = add_scoped_desktop(&fixture,"fixture.scoped-cursor","fixture");
    let editor = metadata_grant(&fixture,"meta.upgrade","fixture", &[
        "conversations.read","conversations.metadata.write","metadata.audit.read"
    ]);
    let db = rusqlite::Connection::open(fixture.live.join("native-v1.sqlite3")).unwrap();
    db.execute_batch("DROP TABLE native_metadata_audit;").unwrap();
    drop(db);
    let server = fixture.start();
    metadata_seed(&fixture,&server);
    let db = rusqlite::Connection::open(fixture.live.join("native-v1.sqlite3")).unwrap();
    db.execute_batch("INSERT INTO conversation_metadata VALUES('wechat','fixture','room','pre-upgrade note','[]',0,123);").unwrap();
    drop(db);
    let legacy = server.get(CHECKPOINT,&fixture.desktop).1;
    let scoped = server.get(CHECKPOINT,&desktop).1;
    assert_eq!(server.get(&format!("{CONVERSATION_META}?id=room"),&editor),
        (503,json!({"error":{"code":"metadata_upgrade_required"}})));
    drop(server);
    for _ in 0..2 {
        let output = binary().args(["upgrade-metadata","--directory"]).arg(&fixture.live).output().unwrap();
        assert!(output.status.success(),"{}",String::from_utf8_lossy(&output.stderr));
    }
    let server = fixture.start();
    assert_eq!(server.get(CHECKPOINT,&fixture.desktop).1,legacy);
    assert_eq!(server.get(CHECKPOINT,&desktop).1,scoped);
    let (_, data) = server.get(&format!("{CONVERSATION_META}?id=room"),&editor);
    assert_eq!(data["metadata"]["note"],"pre-upgrade note");
    assert_eq!(data["revision"],0);
    assert_eq!(server.get(META_AUDIT,&editor).1["items"],json!([]));
}

#[test]
fn failed_audit_insert_rolls_back_metadata_write_atomically() {
    let fixture = Fixture::new();
    let editor = metadata_grant(&fixture,"meta.rollback","fixture", &[
        "conversations.read","conversations.metadata.write","metadata.audit.read"
    ]);
    let server = fixture.start();
    metadata_seed(&fixture,&server);
    let db = rusqlite::Connection::open(fixture.live.join("native-v1.sqlite3")).unwrap();
    db.execute_batch("
        CREATE TRIGGER fixture_audit_write_failure BEFORE INSERT ON native_metadata_audit BEGIN
          SELECT RAISE(ABORT,'synthetic reject marker');
        END;
    ").unwrap();
    drop(db);
    let failure = metadata_post(&server,CONVERSATION_META,&editor,
        json!({"target":{"id":"room"},"expected_revision":0,"note":"do-not-persist-on-failure"}));
    assert_eq!(failure,(503,json!({"error":{"code":"metadata_store_unavailable"}})));
    let state = server.get(&format!("{CONVERSATION_META}?id=room"),&editor);
    assert_eq!(state,(200,json!({"revision":0,"metadata":{"note":"","tags":[],"archived":false}})));
    assert_eq!(server.get(META_AUDIT,&editor).1["items"],json!([]));
}

#[test]
#[ignore = "requires pinned Python stock hub.store (PYTHONPATH)"]
fn stock_python_metadata_and_identity_mutations_match_native_user_state() {
    let fixture = Fixture::new();
    let editor = metadata_grant(&fixture,"meta.oracle","fixture", &[
        "conversations.read","contacts.read","conversations.metadata.write","contacts.identity.write",
    ]);
    let server = fixture.start();
    metadata_seed(&fixture,&server);
    let script = r#"
from hub.store import ChatStore
import json, sys
s=ChatStore(sys.argv[1])
s.ingest('wechat','fixture',[{'id':'room','title':'room'}],
         [{'id':'m-1','conversation_id':'room','timestamp':1,'text':'fixture'}],
         [{'id':'friend','name':'fixture friend'}])
out=[
  s.update_conversation_metadata('wechat','fixture','room',note='中文 first',tags=[' tag ','主题','tag'],archived=True),
  s.update_conversation_metadata('wechat','fixture','room',note='second',archived=False),
  s.update_conversation_metadata('wechat','fixture','room',tags=[]),
  s.set_contact_identity('wechat','fixture','friend','  synthetic identity  '),
  s.remove_contact_identity('wechat','fixture','friend'),
]
print(json.dumps(out,ensure_ascii=False))
s.close()
"#;
    let output = Command::new("python3").args(["-B","-c",script])
        .arg(fixture._temp.path().join("legacy-oracle.sqlite3")).output().unwrap();
    assert!(output.status.success(),"{}",String::from_utf8_lossy(&output.stderr));
    let expected: Vec<Value> = serde_json::from_slice(&output.stdout).unwrap();
    let native = [
        metadata_post(&server,CONVERSATION_META,&editor,json!({
            "target":{"id":"room"},"expected_revision":0,"note":"中文 first","tags":[" tag ","主题","tag"],"archived":true
        })).1["metadata"].clone(),
        metadata_post(&server,CONVERSATION_META,&editor,json!({
            "target":{"id":"room"},"expected_revision":1,"note":"second","archived":false
        })).1["metadata"].clone(),
        metadata_post(&server,CONVERSATION_META,&editor,json!({
            "target":{"id":"room"},"expected_revision":2,"tags":[]
        })).1["metadata"].clone(),
        json!({"identity_id":metadata_post(&server,CONTACT_META,&editor,json!({
            "target":{"id":"friend"},"expected_revision":0,"identity_id":"  synthetic identity  "
        })).1["identity_id"]}),
        json!({"identity_id":metadata_post(&server,CONTACT_META,&editor,json!({
            "target":{"id":"friend"},"expected_revision":1,"identity_id":null
        })).1["identity_id"]}),
    ];
    assert_eq!(expected,native);
}

#[test]
fn legacy_native_context_and_contacts_keep_exact_scope_and_public_projection() {
    let fixture = Fixture::new();
    let basic = add_scoped_desktop(&fixture, "fixture.without-legacy", "fixture");
    let (legacy, hash) = generate_token().unwrap();
    let (context_only, context_hash) = generate_token().unwrap();
    let mut grants: Value = serde_json::from_slice(&fs::read(&fixture.grants).unwrap()).unwrap();
    let grant = |id, hash, permissions| json!({
        "id":id, "token_sha256":hash, "kind":"desktop", "permissions":permissions,
        "account_scope":{"platform":"wechat","account_id":"fixture"}
    });
    let allowed = grants["grants"].as_array_mut().unwrap();
    allowed.push(grant("fixture.legacy", hash,
        json!(["legacy.http.read","legacy.search.read","messages.read","conversations.read","contacts.read"])));
    allowed.push(grant("fixture.context-only", context_hash,
        json!(["legacy.http.read","messages.read","conversations.read"])));
    fs::write(&fixture.grants, grants.to_string()).unwrap();
    let server = fixture.start();
    for (key, account, id, timestamp) in [
        ("legacy-first", "fixture", "m-1", 1720000000),
        ("legacy-foreign", "other", "m-2", 1720000001),
        ("legacy-target", "fixture", "m-2", 1720000001),
        ("legacy-foreign-only", "other", "m-foreign-only", 1720000001),
        ("legacy-newer", "fixture", "m-3", 1720000002),
    ] {
        let mut row = message_for(key, account, id);
        row["messages"][0]["timestamp"] = json!(timestamp);
        assert_eq!(server.ingest(&fixture.producer, &row).0, 200);
    }
    // Seed canaries into the disposable index to detect display leaks as well
    // as searching a redacted contact alias without returning it.
    let db = rusqlite::Connection::open(fixture.live.join("native-v1.sqlite3")).unwrap();
    db.execute(
        "INSERT INTO contacts(platform,account_id,id,name,kind,avatar_url,extra)
         VALUES ('wechat','fixture','same','100% local','friend','private-avatar',?),
                ('wechat','other','same','100% foreign','friend',NULL,NULL),
                ('wechat','fixture','unmatched','1000 local','friend',NULL,NULL)",
        [r#"{"alias":"private-contact-alias"}"#],
    ).unwrap();
    db.execute(
        "UPDATE messages SET extra=?, media_url='private-media'
         WHERE account_id='fixture' AND id='m-2'",
        [r#"{"private":"private-message-extra"}"#],
    ).unwrap();
    drop(db);

    let ctx = "/api/context?platform=wechat&account_id=fixture&conversation_id=room&message_id=m-2&before=1&after=1";
    let (status, value) = server.get(ctx, &legacy);
    assert_eq!(status, 200);
    assert_eq!(value["total"], 3);
    assert_eq!(value["target_index"], 1);
    assert_eq!(value["has_more_before"], false);
    assert_eq!(value["has_more_after"], false);
    let items = value["items"].as_array().unwrap();
    assert_eq!(items.iter().map(|v| v["id"].as_str().unwrap()).collect::<Vec<_>>(), ["m-1", "m-2", "m-3"]);
    assert!(items.iter().all(|v| v["account_id"] == "fixture"));
    assert_eq!(items[1]["text"], "synthetic payload from fixture m-2");
    assert_eq!(items[1]["extra"], json!({}));
    assert_eq!(items[1]["media_url"], Value::Null);
    assert!(!value.to_string().contains("private-"));
    assert_eq!(server.get(ctx, &context_only).0, 200);
    assert_eq!(server.get(ctx, &basic).0, 403);
    assert_eq!(server.get(&ctx.replace("account_id=fixture", "account_id=other"), &legacy).0, 404);
    assert_eq!(server.get(&ctx.replace("conversation_id=room", "conversation_id=missing"), &legacy).0, 404);
    let (status, hits) = server.get("/api/search?platform=all&q=fixture%20m-2", &legacy);
    assert_eq!(status, 200);
    assert_eq!(hits["total"], 1);
    let hit = &hits["items"][0];
    let from_search = format!("/api/context?platform={}&account_id={}&message_id={}&before=1&after=1",
        hit["platform"].as_str().unwrap(), hit["account_id"].as_str().unwrap(), hit["id"].as_str().unwrap());
    assert_eq!(server.get(&from_search, &legacy), (200, value));
    assert_eq!(server.get(&from_search.replace("account_id=fixture", "account_id=other"), &legacy).0, 404);
    assert_eq!(server.get(&from_search.replace("message_id=m-2", "message_id=m-foreign-only"), &legacy).0, 404);
    assert_eq!(server.get(&format!("{from_search}&conversation_id="), &legacy).0, 400);
    assert_eq!(server.get(&format!("{from_search}&source_message_id=123456"), &legacy).0, 400);
    assert_eq!(server.get(&ctx.replace("platform=wechat", "platform=all"), &legacy).0, 400);

    let list = "/api/contacts?platform=all&q=100%25&kind=friend&limit=1";
    let (status, value) = server.get(list, &legacy);
    assert_eq!(status, 200);
    assert_eq!(value["total"], 1);
    assert_eq!(value["items"][0], json!({"id":"same","platform":"wechat","account_id":"fixture",
        "name":"100% local","kind":"friend","avatar_url":null,"extra":{}}));
    assert_eq!(server.get("/api/contacts?q=private-contact-alias", &legacy).1["total"], 0);
    assert_eq!(server.get("/api/contacts?account_id=other", &legacy).0, 403);
    assert_eq!(server.get(list, &basic).0, 403);
    assert_eq!(server.get(list, &context_only).0, 403);

    let ops = |token| {
        let (status, contract) = server.get("/api/v1/desktop/contract", token);
        assert_eq!(status, 200);
        contract["operations"].as_array().unwrap().to_owned()
    };
    let both = ops(&legacy);
    assert!(both.iter().any(|v| v["id"] == "legacy_message_context" && v["path"] == "/api/context"));
    assert!(both.iter().any(|v| v["id"] == "legacy_contacts" && v["path"] == "/api/contacts"));
    assert!(ops(&context_only).iter().all(|v| v["id"] != "legacy_contacts"));
    assert!(ops(&basic).iter().all(|v| v["id"] != "legacy_message_context" && v["id"] != "legacy_contacts"));
}

#[test]
fn legacy_organizer_reads_exact_revision_for_explicit_cas() {
    let fixture = Fixture::new();
    let editor = metadata_grant(&fixture, "legacy.editor", "fixture", &[
        "legacy.http.read", "conversations.read", "messages.read", "conversations.metadata.write",
    ]);
    let native_editor = metadata_grant(&fixture, "legacy.native-editor", "fixture", &[
        "conversations.read", "conversations.metadata.write",
    ]);
    let reader = metadata_grant(&fixture, "legacy.read-only", "fixture", &[
        "legacy.http.read", "conversations.read", "messages.read",
    ]);
    let peer = metadata_grant(&fixture, "legacy.peer", "other", &[
        "legacy.http.read", "conversations.read", "messages.read", "conversations.metadata.write",
    ]);
    let server = fixture.start();
    metadata_seed(&fixture, &server);
    let route = "/api/conversation-metadata";
    let path = format!("{route}?platform=wechat&account_id=fixture&conversation_id=room");
    let empty = json!({"revision":0,"metadata":{"note":"","tags":[],"archived":false}});
    assert_eq!(server.get(&path, &editor), (200, empty.clone()));
    assert_eq!(server.get(&path, &native_editor).0, 403);
    assert_eq!(server.get(&path, &reader).0, 403);
    assert_eq!(server.get(&path, &peer).0, 404);
    assert_eq!(server.request("GET", &path, None, &[("Cookie", "legacy=fake")], "").0, 401);
    assert_eq!(server.request("GET", &path, Some(&editor), &[("Origin", "https://example.invalid")], "").0, 403);
    assert_eq!(server.get(&path.replace("&account_id=fixture", ""), &editor).0, 400);
    assert_eq!(server.get(&path.replace("account_id=fixture", "account_id="), &editor).0, 404);
    assert_eq!(server.get(&path.replace("conversation_id=room", "conversation_id=%FF"), &editor).0, 400);
    assert_eq!(server.get(&format!("{path}&conversation_id=room"), &editor).0, 400);
    assert_eq!(server.get(&path.replace("conversation_id=room", "conversation_id=missing"), &editor).0, 404);

    let stale_revision = server.get(&path, &editor).1["revision"].as_i64().unwrap();
    let body = |revision, note: &str| json!({
        "platform":"wechat","account_id":"fixture","conversation_id":"room",
        "expected_revision":revision,"note":note,"tags":["manual"],"archived":true
    });
    let mut blind = body(stale_revision, "blind");
    blind.as_object_mut().unwrap().remove("expected_revision");
    assert_eq!(metadata_post(&server, route, &editor, blind).0, 400);
    // Two clients share the same reviewed revision. The native-only editor
    // retains its existing authorization for the old-named CAS write.
    assert_eq!(metadata_post(&server, route, &native_editor, body(stale_revision, "reviewed")), (
        200, json!({"changed":true,"revision":1,
            "metadata":{"note":"reviewed","tags":["manual"],"archived":true}})
    ));
    assert_eq!(metadata_post(&server, route, &editor, body(stale_revision, "would overwrite")).0, 409);
    let (status, current) = server.get(&path, &editor);
    assert_eq!(status, 200);
    assert_eq!(current, json!({"revision":1,"metadata":{"note":"reviewed","tags":["manual"],"archived":true}}));
    assert_eq!(server.get(&format!("{CONVERSATION_META}?id=room"), &native_editor), (200, current.clone()));
    assert_eq!(server.get(&path.replace("account_id=fixture", "account_id=other"), &peer), (200, empty));

    let mut patch = body(current["revision"].as_i64().unwrap(), "");
    patch.as_object_mut().unwrap().remove("tags");
    patch.as_object_mut().unwrap().remove("archived");
    assert_eq!(metadata_post(&server, route, &editor, patch), (
        200, json!({"changed":true,"revision":2,"metadata":{"note":"","tags":["manual"],"archived":true}})
    ));
    let discover = |token| {
        server.get("/api/v1/desktop/contract", token).1["operations"].as_array().unwrap()
            .iter().any(|op| op["id"] == "legacy_conversation_metadata" && op["path"] == route)
    };
    assert!(discover(&editor));
    assert!(!discover(&native_editor));
    assert!(!discover(&reader));
}

#[test]
fn legacy_native_pages_follow_archive_ties_without_reusing_other_cursors() {
    use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
    let fixture = Fixture::new();
    let permissions = ["legacy.http.read", "legacy.search.read", "messages.read", "conversations.read"];
    let owner = metadata_grant(&fixture, "pages.owner", "fixture", &permissions);
    let same_account = metadata_grant(&fixture, "pages.second-owner", "fixture", &permissions);
    let peer = metadata_grant(&fixture, "pages.peer", "other", &permissions);
    let mut server = fixture.start();
    for (key, account, id) in [
        ("page-first", "fixture", "z-old"),
        ("page-second", "fixture", "m-middle"),
        ("page-other-account", "other", "a-new"),
        ("page-third", "fixture", "a-new"),
    ] {
        let mut row = message_for(key, account, id);
        row["messages"][0]["text"] = json!("needle only");
        assert_eq!(server.ingest(&fixture.producer, &row).0, 200);
    }
    let path = "/api/messages?platform=wechat&account_id=fixture&conversation_id=room&q=needle&limit=1";
    let search = "/api/search?platform=wechat&account_id=fixture&conversation_id=room&q=needle&limit=1";
    let native = "/api/v1/messages?platform=wechat&account_id=fixture&conversation_id=room&q=needle&limit=1";
    let id = |page: &Value| page["items"][0]["id"].as_str().unwrap().to_string();
    let (status, first) = server.get(path, &owner);
    assert_eq!(status, 200);
    assert_eq!(id(&first), "a-new");
    assert_eq!(first["total"], 3);
    assert_eq!(first["has_more"], true);
    let cursor = first["next_cursor"].as_str().unwrap();
    let token: Value = serde_json::from_slice(&URL_SAFE_NO_PAD.decode(cursor).unwrap()).unwrap();
    assert_eq!(token[0], "live-legacy-messages-v1");
    assert!(token[2].is_string()); // normalized ID, never a local rowid
    assert_eq!(URL_SAFE_NO_PAD.decode(token[2].as_str().unwrap()).unwrap(), b"a-new");
    let second_path = format!("{path}&cursor={cursor}");
    let (status, second) = server.get(&second_path, &owner);
    assert_eq!(status, 200);
    assert_eq!(id(&second), "m-middle");
    assert_eq!(id(&server.get(&format!("{path}&offset=1"), &owner).1), "m-middle");
    let last = server.get(&format!("{path}&cursor={}",second["next_cursor"].as_str().unwrap()), &owner).1;
    assert_eq!(id(&last), "z-old");
    assert_eq!(last["next_cursor"], Value::Null);
    assert_eq!(last["total"], 3);
    assert_eq!(last["has_more"], false);
    assert_eq!(server.get(&second_path, &same_account).0, 400);
    assert_eq!(server.get(&second_path.replace("account_id=fixture", "account_id=other"), &peer).0, 400);
    assert_eq!(server.get(&second_path.replace("q=needle", "q=only"), &owner).0, 400);
    assert_eq!(server.get(&second_path.replace("conversation_id=room", "conversation_id=wrong"), &owner).0, 400);
    assert_eq!(server.get(&format!("{search}&cursor={cursor}"), &owner).0, 400);
    assert_eq!(server.get(&format!("{native}&cursor={cursor}"), &owner).0, 400);
    let raw_stock = URL_SAFE_NO_PAD.encode(b"[1720000000,4,\"0000000000000000\"]");
    assert_eq!(server.get(&format!("{path}&cursor={raw_stock}"), &owner).0, 400);
    let native_page = server.get(native, &owner).1;
    assert_eq!(id(&native_page), "z-old");
    assert_eq!(server.get(&format!("{path}&cursor={}",native_page["next_cursor"].as_str().unwrap()), &owner).0, 400);

    let found = server.get(search, &owner).1;
    assert_eq!(id(&found), "a-new");
    assert_eq!(found["total"], 3);
    let search_cursor = found["next_cursor"].as_str().unwrap();
    assert_eq!(id(&server.get(&format!("{search}&cursor={search_cursor}"), &owner).1), "m-middle");
    assert_eq!(server.get(&format!("{path}&cursor={search_cursor}"), &owner).0, 400);

    // Restart keeps the same archive identity and local tie order.
    drop(server);
    server = fixture.start();
    assert_eq!(server.get(&second_path, &owner), (200, second));
    // Changing the anchor's matching text invalidates the original cursor.
    let mut revised = message_for("page-revision", "fixture", "a-new");
    revised["messages"][0]["text"] = json!("no matching term");
    assert_eq!(server.ingest(&fixture.producer, &revised).0, 200);
    assert_eq!(server.get(&second_path, &owner).0, 400);
}
