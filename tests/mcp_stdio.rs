use local_message_hub_rs::plugins::generate_token;
use rusqlite::Connection;
use serde_json::{Value, json};
use std::{
    fs,
    io::{BufRead, BufReader, Write},
    path::PathBuf,
    process::{Child, ChildStdin, Command, Stdio},
    sync::mpsc::{self, Receiver},
    time::{Duration, Instant},
};

const TIMEOUT: Duration = Duration::from_secs(8);
const TOOL: &str = "test.mcp";
const DESKTOP: &str = "test.desktop";
const CONTACTS: &str = "test.contacts";

struct Fixture {
    dir: tempfile::TempDir,
    archive: PathBuf,
    grants: PathBuf,
    token: String,
    desktop_token: String,
    contacts_token: String,
}

impl Fixture {
    fn new() -> Self {
        let dir = tempfile::tempdir().unwrap();
        let archive = dir.path().join("index.sqlite3");
        let db = Connection::open(&archive).unwrap();
        db.execute_batch(include_str!("fixtures/index-v9.sql"))
            .unwrap();
        db.close().unwrap();
        let grants = dir.path().join("plugins.json");
        let (token, sha) = generate_token().unwrap();
        let (desktop_token, desktop_sha) = generate_token().unwrap();
        let (contacts_token, contacts_sha) = generate_token().unwrap();
        fs::write(
            &grants,
            json!({"version":1,"grants":[
                {"id":TOOL,"kind":"tool","permissions":["messages.read","conversations.read"],"token_sha256":sha},
                {"id":DESKTOP,"kind":"desktop","permissions":["contacts.read"],"token_sha256":desktop_sha},
                {"id":CONTACTS,"kind":"tool","permissions":["contacts.read"],"token_sha256":contacts_sha}
            ]}).to_string(),
        ).unwrap();
        Self {
            dir,
            archive,
            grants,
            token,
            desktop_token,
            contacts_token,
        }
    }

    fn command(&self, plugin_id: &str, token: &str) -> Command {
        let mut command = Command::new(env!("CARGO_BIN_EXE_local-message-hub-rs"));
        command
            .args(["mcp", "--snapshot"])
            .arg(&self.archive)
            .arg("--grants")
            .arg(&self.grants)
            .args(["--plugin-id", plugin_id])
            .env("LMH_PLUGIN_TOKEN", token);
        command
    }
}

struct Proc {
    child: Child,
    input: ChildStdin,
    responses: Receiver<String>,
}

impl Proc {
    fn new(mut cli: Command) -> Self {
        let mut child = cli
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .unwrap();
        let input = child.stdin.take().unwrap();
        let reader = BufReader::new(child.stdout.take().unwrap());
        let (tx, responses) = mpsc::channel();
        std::thread::spawn(move || {
            for line in reader.lines() {
                let Ok(line) = line else { break };
                if tx.send(line).is_err() {
                    break;
                }
            }
        });
        Self {
            child,
            input,
            responses,
        }
    }

    fn send(&mut self, packet: Value) {
        writeln!(self.input, "{packet}").unwrap();
        self.input.flush().unwrap();
    }

    fn receive(&self) -> Value {
        serde_json::from_str(&self.responses.recv_timeout(TIMEOUT).unwrap()).unwrap()
    }

    fn rpc(&mut self, id: u32, method: &str, params: Value) -> Value {
        self.send(json!({"jsonrpc":"2.0","id":id,"method":method,"params":params}));
        let response = self.receive();
        assert_eq!(response["jsonrpc"], "2.0");
        assert_eq!(response["id"], id);
        response
    }

    fn initialize(&mut self, version: &str) -> Value {
        let r = self.rpc(1, "initialize", json!({
            "protocolVersion":version,"capabilities":{},"clientInfo":{"name":"fixture", "version":"1"}
        }));
        self.send(json!({"jsonrpc":"2.0","method":"notifications/initialized"}));
        r
    }

    fn call(&mut self, id: u32, tool: &str, args: Value) -> Value {
        self.rpc(id, "tools/call", json!({"name":tool,"arguments":args}))
    }

    fn wait(&mut self) -> std::process::ExitStatus {
        let start = Instant::now();
        loop {
            if let Some(status) = self.child.try_wait().unwrap() {
                return status;
            }
            assert!(start.elapsed() < TIMEOUT, "child did not exit");
            std::thread::sleep(Duration::from_millis(10));
        }
    }
}

impl Drop for Proc {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

fn structured(value: &Value) -> &Value {
    &value["result"]["structuredContent"]
}

#[test]
fn native_stdio_negotiates_scopes_and_rejects_cross_scope_cursor() {
    let fixture = Fixture::new();
    let before = fs::read(&fixture.archive).unwrap();
    let mut child = Proc::new(fixture.command(TOOL, &fixture.token));
    assert_eq!(
        child.rpc(0, "tools/list", json!({}))["error"]["code"],
        -32002
    );
    assert_eq!(
        child.initialize("2099-01-01")["result"]["protocolVersion"],
        "2025-11-25"
    );
    assert_eq!(child.rpc(2, "ping", json!({}))["result"], json!({}));
    let listed = child.rpc(3, "tools/list", json!({}));
    let names: Vec<_> = listed["result"]["tools"]
        .as_array()
        .unwrap()
        .iter()
        .map(|tool| tool["name"].as_str().unwrap())
        .collect();
    assert_eq!(
        names,
        ["list_conversations", "list_messages", "search_messages"]
    );
    assert_eq!(
        child.call(4, "list_contacts", json!({}))["error"]["code"],
        -32602
    );
    assert_eq!(
        child.call(5, "send_wechat_message", json!({}))["error"]["code"],
        -32602
    );
    assert_eq!(
        child.call(6, "list_conversations", json!({"query":"Fixture"}))["result"]["structuredContent"]
            ["total"],
        1
    );

    let first = child.call(
        7,
        "list_messages",
        json!({"platform":"wechat","account_id":"owner","conversation_id":"room","limit":1}),
    );
    let value = structured(&first);
    let cursor = value["next_cursor"].as_str().unwrap();
    assert_eq!(value["items"][0]["id"], "m3");
    let text = first["result"]["content"][0]["text"].as_str().unwrap();
    assert_eq!(serde_json::from_str::<Value>(text).unwrap(), *value);
    let second = child.call(8, "list_messages", json!({"platform":"wechat","account_id":"owner","conversation_id":"room","limit":1,"cursor":cursor}));
    assert_eq!(structured(&second)["items"][0]["id"], "m2");
    let crossed = child.call(9, "list_messages", json!({"platform":"wechat","account_id":"other","conversation_id":"room","limit":1,"cursor":cursor}));
    assert_eq!(crossed["result"]["isError"], true);
    assert!(crossed["result"].get("structuredContent").is_none());

    assert_eq!(
        child.call(
            10,
            "search_messages",
            json!({"query":"example","account_id":"owner"})
        )["result"]["isError"],
        true
    );
    assert_eq!(structured(&child.call(11, "search_messages", json!({"platform":"wechat","query":"example","account_id":"owner","conversation_id":"room"})))["total"], 2);
    for invalid in [
        json!({"query": "example", "limit": true}),
        json!({"query": "example", "q":"extra"}),
        json!({"query": 8}),
        json!({"query":"   "}),
        json!({"query":"example","offset":100001}),
    ] {
        let r = child.call(12, "search_messages", invalid);
        assert_eq!(r["result"]["isError"], true);
    }

    // A live profile created after login must immediately disable data calls.
    fs::write(
        fixture.dir.path().join("settings.json"),
        r#"{"account_lock":{"salt":"synthetic","digest":"synthetic"}}"#,
    )
    .unwrap();
    let locked = child.call(13, "list_conversations", json!({}));
    assert_eq!(locked["result"]["isError"], true);
    assert!(locked["result"].get("structuredContent").is_none());

    assert_eq!(child.rpc(14, "shutdown", json!({}))["result"], Value::Null);
    child.send(json!({"jsonrpc":"2.0","method":"exit"}));
    assert!(child.wait().success());
    assert_eq!(before, fs::read(&fixture.archive).unwrap());
    assert!(!fixture.archive.with_file_name("index.sqlite3-wal").exists());
    assert!(!fixture.archive.with_file_name("index.sqlite3-shm").exists());
}

#[test]
fn mcp_rejects_mismatched_identity_wrong_token_desktop_kind_and_live_profile() {
    let fixture = Fixture::new();
    for (id, token) in [
        (DESKTOP, fixture.token.as_str()),
        (TOOL, "not-a-token"),
        (DESKTOP, fixture.desktop_token.as_str()),
    ] {
        let output = fixture
            .command(id, token)
            .stdin(Stdio::null())
            .output()
            .unwrap();
        assert!(!output.status.success());
        assert!(output.stdout.is_empty());
        assert!(!String::from_utf8_lossy(&output.stderr).contains(token));
    }
    fs::write(fixture.dir.path().join("hub.sqlite3"), []).unwrap();
    let output = fixture
        .command(TOOL, &fixture.token)
        .stdin(Stdio::null())
        .output()
        .unwrap();
    assert!(!output.status.success());
    assert!(output.stdout.is_empty());
}

#[test]
fn mcp_oversized_input_terminates_while_writer_remains_open() {
    let fixture = Fixture::new();
    let mut child = Proc::new(fixture.command(TOOL, &fixture.token));
    assert_eq!(
        child.initialize("2025-06-18")["result"]["protocolVersion"],
        "2025-06-18"
    );
    // Keep stdin open and do not send a newline; an unbounded drain would hang.
    let oversized = vec![b'x'; 64 * 1024 + 1];
    child.input.write_all(&oversized).unwrap();
    child.input.flush().unwrap();
    let r = child.receive();
    assert_eq!(r["error"]["code"], -32700);
    assert_eq!(r["id"], Value::Null);
    assert!(!child.wait().success());
}

#[test]
fn mcp_contact_only_grant_and_old_protocol_use_text_result() {
    let fixture = Fixture::new();
    for (version, has_structured) in [
        ("2024-11-05", false),
        ("2025-03-26", false),
        ("2025-06-18", true),
    ] {
        let mut child = Proc::new(fixture.command(CONTACTS, &fixture.contacts_token));
        assert_eq!(
            child.initialize(version)["result"]["protocolVersion"],
            version
        );
        let listed = child.rpc(2, "tools/list", json!({"_meta":{}}));
        assert_eq!(listed["result"]["tools"].as_array().unwrap().len(), 1);
        assert_eq!(listed["result"]["tools"][0]["name"], "list_contacts");
        assert_eq!(
            listed["result"]["tools"][0].get("annotations").is_some(),
            has_structured
        );
        assert_eq!(
            child.call(3, "search_messages", json!({"query":"example"}))["error"]["code"],
            -32602
        );
        let response = child.call(
            4,
            "list_contacts",
            json!({"platform":"wechat","account_id":"owner","query":"Fixture"}),
        );
        assert_eq!(response["result"]["isError"], false);
        assert_eq!(
            response["result"].get("structuredContent").is_some(),
            has_structured
        );
        let text: Value =
            serde_json::from_str(response["result"]["content"][0]["text"].as_str().unwrap())
                .unwrap();
        assert_eq!(text["items"][0]["id"], "friend");
        if has_structured {
            assert_eq!(*structured(&response), text);
        }
        child.send(json!({"jsonrpc":"2.0","method":"exit"}));
        assert!(child.wait().success());
    }
}

#[test]
fn mcp_bounds_results_and_omits_inline_and_nested_binary_data() {
    let fixture = Fixture::new();
    let conn = Connection::open(&fixture.archive).unwrap();
    let mut statement = conn.prepare("INSERT INTO messages(platform,account_id,id,conversation_id,timestamp,type,text,media_url,extra)
                VALUES('wechat','owner',?,'room',100,'text',?,'DaTa:synthetic',?)").unwrap();
    let text = "响".repeat(7000);
    let extra =
        json!({"nested":{"binary_value":"synthetic-binary", "image_data":"synthetic-image"}})
            .to_string();
    for n in 0..52 {
        statement
            .execute(rusqlite::params![format!("big{n}"), &text, &extra])
            .unwrap();
    }
    drop(statement);
    conn.close().unwrap();
    let mut child = Proc::new(fixture.command(TOOL, &fixture.token));
    child.initialize("2025-11-25");
    let oversized = child.call(
        2,
        "list_messages",
        json!({"platform":"wechat","account_id":"owner","conversation_id":"room","limit":100}),
    );
    assert_eq!(oversized["result"]["isError"], true);
    assert!(oversized["result"].get("structuredContent").is_none());
    let one = child.call(
        3,
        "list_messages",
        json!({"platform":"wechat","account_id":"owner","conversation_id":"room","limit":1}),
    );
    let item = &structured(&one)["items"][0];
    assert_eq!(one["result"]["isError"], false);
    assert!(item["text"].as_str().unwrap().ends_with("…[truncated]"));
    assert_eq!(item["media_url"], "[inline media omitted]");
    assert_eq!(
        item["extra"]["nested"]["binary_value"],
        "[binary data omitted]"
    );
    assert_eq!(
        item["extra"]["nested"]["image_data"],
        "[binary data omitted]"
    );
    child.send(json!({"jsonrpc":"2.0","method":"exit"}));
    assert!(child.wait().success());
}

const SCOPED: &str = "test.scoped";
const SCOPED_MINIMAL: &str = "test.statsonly";
const SCOPED_CHAT: &str = "test.chatonly";
const SCOPED_ACCOUNT: &str = "owner";
const SPECIAL_ID: &str = "path/百分%";
const SPECIAL_URI: &str = "lmh://message/wechat/owner/path%2F%E7%99%BE%E5%88%86%25";

fn append_scoped(fixture: &Fixture) -> (String,String,String) {
    let (scoped,sha) = generate_token().unwrap();
    let (minimal,min_sha) = generate_token().unwrap();
    let (chat,chat_sha) = generate_token().unwrap();
    let mut cfg: Value = serde_json::from_slice(&fs::read(&fixture.grants).unwrap()).unwrap();
    let scope = json!({"platform":"wechat","account_id":SCOPED_ACCOUNT});
    cfg["grants"].as_array_mut().unwrap().extend([
        json!({"id":SCOPED,"kind":"scoped-tool","account_scope":scope,"token_sha256":sha,
            "permissions":["messages.read","mcp.sources.read","mcp.prompts.read"]}),
        json!({"id":SCOPED_MINIMAL,"kind":"scoped-tool","account_scope":scope,"token_sha256":min_sha,
            "permissions":["mcp.sources.read"]}),
        json!({"id":SCOPED_CHAT,"kind":"scoped-tool","account_scope":scope,"token_sha256":chat_sha,
            "permissions":["messages.read"]}),
    ]);
    fs::write(&fixture.grants,serde_json::to_vec(&cfg).unwrap()).unwrap();
    let db=Connection::open(&fixture.archive).unwrap();
    for (owner, body, id) in [("owner","synthetic needle good",SPECIAL_ID),("other","synthetic needle private foreign",SPECIAL_ID),
        ("other","unrelated private","only-other")] {
        db.execute("INSERT INTO messages(platform,account_id,id,conversation_id,timestamp,type,text,media_url,file_name,raw_type,extra)
            VALUES('wechat',?,?,'room',999,'text',?,'file://path-secret','attachment-secret','internal-code','{\"x\":\"extra-secret\"}')",
            (owner,id,body)).unwrap();
    }
    (scoped,minimal,chat)
}

fn resource_text(response: &Value) -> Value {
    serde_json::from_str(response["result"]["contents"][0]["text"].as_str().unwrap()).unwrap()
}
fn tool_text(response: &Value) -> Value {
    serde_json::from_str(response["result"]["content"][0]["text"].as_str().unwrap()).unwrap()
}

#[test]
fn scoped_stdio_discovers_and_reads_only_exact_account_under_all_supported_revisions() {
    let fixture=Fixture::new();
    let (scoped,_,_)=append_scoped(&fixture);
    for (version,has_structured) in [("2025-11-25",true),("2025-06-18",true),("2025-03-26",false),("2024-11-05",false)] {
        let mut p=Proc::new(fixture.command(SCOPED,&scoped));
        let initialized=p.initialize(version);
        assert_eq!(initialized["result"]["protocolVersion"],version);
        assert_eq!(initialized["result"]["capabilities"]["resources"]["subscribe"],false);
        assert_eq!(initialized["result"]["capabilities"]["prompts"]["listChanged"],false);
        let manifest=p.rpc(2,"tools/list",json!({}));
        let names:Vec<_>=manifest["result"]["tools"].as_array().unwrap().iter()
            .map(|tool| tool["name"].as_str().unwrap()).collect();
        assert_eq!(names,vec!["list_messages","search_messages","get_source_status"]);
        let resources=p.rpc(3,"resources/list",json!({}));
        assert_eq!(resources["result"]["resources"][0]["uri"],"lmh://sources");
        let templates=p.rpc(4,"resources/templates/list",json!({}));
        assert_eq!(templates["result"]["resourceTemplates"][0]["uriTemplate"],"lmh://message/{platform}/{account_id}/{message_id}");
        let source=p.rpc(5,"resources/read",json!({"uri":"lmh://sources"}));
        let source=resource_text(&source);
        assert_eq!(source["items"].as_array().unwrap().len(),1);
        assert_eq!(source["items"][0]["platform"],"wechat");
        assert_eq!(source["items"][0]["message_count"],4);
        let stats=p.call(6,"get_source_status",json!({}));
        assert_eq!(stats["result"].get("structuredContent").is_some(),has_structured);
        assert_eq!(tool_text(&stats)["items"][0]["message_count"],4);
        let found=p.call(7,"search_messages",json!({"query":"synthetic needle"}));
        assert_eq!(found["result"].get("structuredContent").is_some(),has_structured);
        assert_eq!(tool_text(&found)["total"],1);
        let result=p.rpc(8,"resources/read",json!({"uri":SPECIAL_URI}));
        assert_eq!(result["result"]["contents"][0]["uri"],SPECIAL_URI);
        let value=resource_text(&result);
        assert_eq!(value["id"],SPECIAL_ID);
        assert_eq!(value["text"],"synthetic needle good");
        assert_eq!(value["resource_uri"],SPECIAL_URI);
        assert!(value.get("extra").is_none() && value.get("media_url").is_none() && value.get("raw_type").is_none());
        let filtered=tool_text(&found)["items"][0].clone();
        assert_eq!(filtered["resource_uri"],SPECIAL_URI);
        let text=format!("{result}{filtered}");
        for suppressed in ["path-secret","attachment-secret","internal-code","extra-secret","private foreign"] {
            assert!(!text.contains(suppressed));
        }
        for foreign in [SPECIAL_URI.replace("/owner/","/other/"),SPECIAL_URI.replace("%2F","%2f"),SPECIAL_URI.replace("%25","%"),
            format!("{SPECIAL_URI}?x=1"),format!("{SPECIAL_URI}#test"),SPECIAL_URI.replace("%2F","/"),"lmh://message/wechat/owner/only-other".into()] {
            let rejected=p.rpc(9,"resources/read",json!({"uri":foreign}));
            assert_eq!(rejected["error"]["code"],-32602);
        }
        let attacked=p.call(10,"search_messages",json!({"query":"synthetic needle","account_id":"other"}));
        assert_eq!(attacked["result"]["isError"],true);
        let asked=p.rpc(11,"prompts/get",json!({"name":"inspect_attachment","arguments":{
            "platform":"wechat","account_id":"owner","message_id":SPECIAL_ID
        }}));
        let prompt=asked["result"]["messages"][0]["content"]["text"].as_str().unwrap();
        assert!(prompt.contains(SPECIAL_URI) && !prompt.contains("get_attachment") && !prompt.contains("path-secret"));
        let denied=p.rpc(12,"prompts/get",json!({"name":"inspect_attachment","arguments":{
            "platform":"wechat","account_id":"other","message_id":SPECIAL_ID
        }}));
        assert_eq!(denied["error"]["code"],-32602);
        let text=p.rpc(13,"prompts/get",json!({"name":"cross_platform_search","arguments":{
            "query":"\nSynthetic \"quoted\" text","platform":"wechat"
        }}));
        let text=text["result"]["messages"][0]["content"]["text"].as_str().unwrap();
        assert!(text.contains("untrusted") && text.contains("\\nSynthetic \\\"quoted\\\""));
        p.send(json!({"jsonrpc":"2.0","method":"exit"}));
        assert!(p.wait().success());
    }
}

#[test]
fn scoped_mcp_grants_are_opt_in_and_legacy_token_has_no_new_abilities() {
    let fixture=Fixture::new();
    let (_,minimal,chat)=append_scoped(&fixture);
    for (plugin,token) in [(SCOPED_MINIMAL,&minimal),(SCOPED_CHAT,&chat),(TOOL,&fixture.token)] {
        let mut p=Proc::new(fixture.command(plugin,token));
        let capabilities=p.initialize("2025-11-25")["result"]["capabilities"].clone();
        assert!(capabilities.get("prompts").is_none());
        let prompts=p.rpc(2,"prompts/list",json!({}));
        if plugin == TOOL {
            assert!(capabilities.get("resources").is_none());
            assert_eq!(prompts["error"]["code"],-32601);
        } else {
            assert_eq!(prompts["result"]["prompts"],json!([]));
            let not_granted=if plugin==SCOPED_MINIMAL { SPECIAL_URI } else { "lmh://sources" };
            assert_eq!(p.rpc(3,"resources/read",json!({"uri":not_granted}))["error"]["code"],-32602);
        }
    }
}

#[test]
fn scoped_mcp_grant_is_never_accepted_as_http_bearer() {
    use std::{io::Read, net::TcpStream};
    let fixture=Fixture::new();
    let (token,_,_)=append_scoped(&fixture);
    let mut process=Proc::new({
        let mut c=Command::new(env!("CARGO_BIN_EXE_local-message-hub-rs"));
        c.args(["serve","--snapshot"]).arg(&fixture.archive).arg("--grants").arg(&fixture.grants);
        c
    });
    let ready=process.receive();
    let origin=ready["origin"].as_str().unwrap().trim_start_matches("http://");
    let mut io=TcpStream::connect(origin).unwrap();
    io.set_read_timeout(Some(TIMEOUT)).unwrap();
    write!(io,"GET /api/v1/messages HTTP/1.1\r\nHost: {origin}\r\nAuthorization: Bearer {token}\r\nConnection: close\r\n\r\n").unwrap();
    let mut reply=String::new();
    io.read_to_string(&mut reply).unwrap();
    assert!(reply.starts_with("HTTP/1.1 401 "));
    assert!(!reply.contains("synthetic needle"));
    process.child.kill().unwrap();
}

const INCOMING: &str = "wait_for_new_messages";

struct LiveFixture {
    dir: tempfile::TempDir,
    grants: PathBuf,
    archive: local_message_hub_rs::events::EventStore,
    alpha: String,
    beta: String,
}
impl LiveFixture {
    fn new() -> Self {
        let dir=tempfile::tempdir().unwrap();
        let path=dir.path().join("native");
        fs::create_dir(&path).unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(&path,fs::Permissions::from_mode(0o700)).unwrap();
        }
        let archive=local_message_hub_rs::events::EventStore::init(&path).unwrap();
        let (alpha,alpha_hash)=generate_token().unwrap();
        let (beta,beta_hash)=generate_token().unwrap();
        let grants=dir.path().join("grants.json");
        fs::write(&grants,serde_json::to_vec(&json!({"version":1,"grants":[
            {"id":"live.alpha","kind":"scoped-tool","permissions":["mcp.incoming.read"],"token_sha256":alpha_hash,
            "account_scope":{"platform":"qq","account_id":"alpha"}},
            {"id":"live.beta","kind":"scoped-tool","permissions":["mcp.incoming.read"],"token_sha256":beta_hash,
            "account_scope":{"platform":"qq","account_id":"beta"}}
        ]})).unwrap()).unwrap();
        Self {dir,grants,archive,alpha,beta}
    }
    fn command_for(&self,account: &str,path: &std::path::Path,flag: &str) -> Command {
        let mut c=Command::new(env!("CARGO_BIN_EXE_local-message-hub-rs"));
        c.args(["mcp",flag]).arg(path).arg("--grants").arg(&self.grants)
            .arg("--plugin-id").arg(format!("live.{account}"))
            .env("LMH_PLUGIN_TOKEN",if account=="alpha" {&self.alpha} else {&self.beta});
        c
    }
    fn start(&self,account: &str,protocol: &str) -> Proc {
        let mut child=Proc::new(self.command_for(account,self.archive.path().parent().unwrap(),"--live"));
        let initialized=child.initialize(protocol);
        assert_eq!(initialized["result"]["protocolVersion"],protocol);
        assert!(initialized["result"]["capabilities"].get("resources").is_none());
        child
    }
    fn ingest(&self, account: &str, id: &str, batch: &str, is_self: bool, body: &str) {
        use local_message_hub_rs::events::{IngestRequest,NormalizedMessage};
        self.archive.ingest("fixture.synthetic",IngestRequest {idempotency_key:batch.into(),messages:vec![
            NormalizedMessage {platform:"qq".into(),account_id:account.into(),conversation_id:"shared-room".into(),
                message_id:id.into(),title:None,sender_id:None,sender_name:None,timestamp:123,is_self,text:Some(body.into())}
        ]}).unwrap();
    }
    fn offline(&self) {
        let input=self.dir.path().join("synthetic-legacy.json");
        fs::write(&input,serde_json::to_vec(&json!([{"platform":"qq","account_id":"alpha","conversation_id":"shared-room",
            "id":"imported","text":"offline secret body","is_self":false,"timestamp":124,"type":"text"}])).unwrap()).unwrap();
        let archive=self.archive.path().parent().unwrap();
        let cli=|action: &str| {
            let mut c=Command::new(env!("CARGO_BIN_EXE_local-message-hub-rs"));
            c.arg(action).arg("--source").arg(&input).arg("--directory").arg(archive).args(["--platform","qq","--account","alpha"]);
            c
        };
        let preview=cli("preview-legacy-json").output().unwrap();
        assert!(preview.status.success());
        let preview:Value=serde_json::from_slice(&preview.stdout).unwrap();
        assert!(cli("import-legacy-json").arg("--confirm").arg(preview["confirmation"].as_str().unwrap()).output().unwrap().status.success());
    }
}
fn poll(child: &mut Proc,id: u32,cursor: &Value,limit: usize) -> Value {
    tool_text(&child.call(id,INCOMING,json!({"after_cursor":cursor,"timeout_seconds":0,"limit":limit})))
}

#[test]
fn incoming_mcp_durable_scoped_paging_skips_self_revisions_and_offline_rows_without_cursor_gaps() {
    let f=LiveFixture::new();
    f.ingest("alpha","history","historic",false,"old body");
    f.ingest("beta","history","beta-historic",false,"foreign body");
    let mut a=f.start("alpha","2025-11-25");
    let mut b=f.start("beta","2024-11-05");
    let tools=a.rpc(2,"tools/list",json!({}));
    assert_eq!(tools["result"]["tools"].as_array().unwrap().len(),1);
    assert_eq!(tools["result"]["tools"][0]["name"],INCOMING);
    assert_eq!(a.call(2,"search_messages",json!({"query":"old"}))["error"]["code"],-32602);
    let handshake=a.call(3,INCOMING,json!({}));
    let state=tool_text(&handshake);
    assert_eq!(state["initialized"],true);
    assert_eq!(state["messages"],json!([]));
    assert_eq!(state["timed_out"],false);
    let initial=state["cursor"].clone();
    assert!(initial.as_str().unwrap().ends_with(":1"));
    let beta_state=b.call(3,INCOMING,json!({}));
    assert!(beta_state["result"].get("structuredContent").is_none());
    let beta_cursor=tool_text(&beta_state)["cursor"].clone();
    assert!(beta_cursor.as_str().unwrap().ends_with(":1"));
    assert_ne!(initial,beta_cursor);

    for args in [json!({"after_cursor":beta_cursor}),json!({"after_event_id":1}),json!({"after_cursor":"1"}),
        json!({"after_cursor":1}),json!({"account_id":"beta"}),json!({"platform":"all"}),
        json!({"timeout_seconds":31}),json!({"timeout_seconds":true}),json!({"limit":0}),json!({"limit":51}),
        json!({"after_cursor":initial.as_str().unwrap().replace(":1",":999")})] {
        assert_eq!(a.call(4,INCOMING,args)["result"]["isError"],true);
    }

    f.ingest("beta","shared-id","beta-new",false,"must not leak");
    f.ingest("alpha","history","revision",false,"revision must not appear");
    f.ingest("alpha","outgoing","self",true,"self must not appear");
    f.offline();
    let untouched=poll(&mut a,5,&initial,10);
    assert_eq!(untouched["cursor"],initial);
    assert_eq!(untouched["messages"],json!([]));
    assert_eq!(untouched["timed_out"],true);

    for (id,batch) in [("shared-id","alpha-a"),("next","alpha-b")] {
        f.ingest("alpha",id,batch,false,"body not exposed");
    }
    let page=poll(&mut a,6,&initial,1);
    assert_eq!(page["messages"][0]["id"],"shared-id");
    assert_eq!(page["messages"][0]["account_id"],"alpha");
    assert!(page["messages"][0].get("text").is_none());
    assert_eq!(page["has_more"],true);
    assert!(page["cursor"].as_str().unwrap().ends_with(":2"));
    drop(a);
    let mut a=f.start("alpha","2025-06-18");
    let page=poll(&mut a,7,&page["cursor"],1);
    assert_eq!(page["messages"][0]["id"],"next");
    assert_eq!(page["has_more"],false);
    assert!(page["cursor"].as_str().unwrap().ends_with(":3"));
    let foreign=poll(&mut b,7,&beta_cursor,50);
    assert_eq!(foreign["messages"][0]["account_id"],"beta");
    assert_eq!(foreign["messages"][0]["id"],"shared-id");

    let other=LiveFixture::new();
    let mut other_instance=Proc::new(f.command_for("alpha",other.archive.path().parent().unwrap(),"--live"));
    other_instance.initialize("2025-03-26");
    let bad=other_instance.call(8,INCOMING,json!({"after_cursor":initial,"timeout_seconds":0}));
    assert_eq!(bad["result"]["isError"],true);
}

#[test]
fn incoming_mcp_bounded_wait_observes_new_event_and_rejects_wrong_archive_mode() {
    let f=LiveFixture::new();
    let fixture=Fixture::new();
    assert!(!f.command_for("alpha",&fixture.archive,"--snapshot").output().unwrap().status.success());
    let mut p=f.start("alpha","2025-11-25");
    let initial=tool_text(&p.call(2,INCOMING,json!({})))["cursor"].clone();
    let before=Instant::now();
    let empty=tool_text(&p.call(3,INCOMING,json!({"after_cursor":initial,"timeout_seconds":1})));
    assert_eq!(empty["timed_out"],true);
    assert!(before.elapsed() >= Duration::from_millis(900) && before.elapsed() < Duration::from_secs(4));
    std::thread::scope(|s| {
        s.spawn(|| {
            std::thread::sleep(Duration::from_millis(100));
            f.ingest("alpha","during-wait","new",false,"hello");
        });
        let fresh=tool_text(&p.call(4,INCOMING,json!({"after_cursor":initial,"timeout_seconds":2})));
        assert_eq!(fresh["timed_out"],false);
        assert_eq!(fresh["messages"][0]["id"],"during-wait");
    });
}

#[test]
fn incoming_upgrade_backfills_only_native_created_and_preserves_previous_event_sequence() {
    use local_message_hub_rs::plugins::AccountScope;
    let f=LiveFixture::new();
    let db=Connection::open(f.archive.path()).unwrap();
    db.execute_batch("DROP TRIGGER native_account_incoming_record; DROP TABLE native_account_incoming;").unwrap();
    db.close().unwrap();
    f.ingest("alpha","a","a1",false,"first");
    f.ingest("alpha","a","a2",false,"revision");
    f.ingest("beta","a","b1",false,"foreign");
    f.offline();
    let scope=AccountScope{platform:"qq".into(),account_id:"alpha".into()};
    let original=f.archive.replay_for(Some(&scope),0,50).unwrap();
    assert_eq!(original.items.len(),2);
    let mut p=f.start("alpha","2025-11-25");
    let missing=p.call(2,INCOMING,json!({}));
    assert!(missing["result"]["content"][0]["text"].as_str().unwrap().contains("offline archive upgrade"));
    drop(p);
    for _ in 0..2 {
        let cli=Command::new(env!("CARGO_BIN_EXE_local-message-hub-rs"))
            .args(["upgrade-mcp-incoming","--directory"]).arg(f.archive.path().parent().unwrap()).output().unwrap();
        assert!(cli.status.success());
    }
    assert_eq!(f.archive.replay_for(Some(&scope),0,50).unwrap(),original);
    let mut p=f.start("alpha","2025-11-25");
    let initialized=tool_text(&p.call(3,INCOMING,json!({})));
    assert_eq!(initialized["messages"],json!([]));
    assert!(initialized["cursor"].as_str().unwrap().ends_with(":1"));
    f.ingest("alpha","after","after-upgrade",false,"add");
    let q=poll(&mut p,4,&initialized["cursor"],50);
    assert_eq!(q["messages"][0]["id"],"after");
    assert!(q["cursor"].as_str().unwrap().ends_with(":2"));
}

#[test]
fn incoming_sequence_and_desktop_journal_roll_back_on_aborted_ingest() {
    use local_message_hub_rs::events::{IngestRequest,NormalizedMessage};
    let f=LiveFixture::new();
    let mut p=f.start("alpha","2025-11-25");
    let cursor=tool_text(&p.call(2,INCOMING,json!({})))["cursor"].clone();
    let db=Connection::open(f.archive.path()).unwrap();
    db.execute_batch("CREATE TRIGGER fixture_rollback BEFORE INSERT ON native_ingest_receipts
        BEGIN SELECT RAISE(ABORT,'fixture transaction rolls back'); END;").unwrap();
    db.close().unwrap();
    let failed=f.archive.ingest("fixture.synthetic",IngestRequest {idempotency_key:"aborted".into(),messages:vec![
        NormalizedMessage {platform:"qq".into(),account_id:"alpha".into(),conversation_id:"room".into(),
            message_id:"never-committed".into(),title:None,sender_id:None,sender_name:None,timestamp:9,is_self:false,text:None}
    ]});
    assert!(failed.is_err());
    assert_eq!(f.archive.checkpoint().unwrap(),0);
    let data=poll(&mut p,3,&cursor,50);
    assert_eq!(data["cursor"],cursor);
    assert_eq!(data["messages"],json!([]));
}
