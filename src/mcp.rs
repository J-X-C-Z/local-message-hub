use crate::{
    events::EventStore,
    mcp_incoming, mcp_metadata, mcp_scoped,
    plugins::{AccountScope, Permission, Plugin, PluginKind},
    snapshot::Snapshot,
    store::{Query, Store, StoreError},
};
use serde_json::{Map, Value, json};
use std::io::{self, BufRead, Write};

const MAX_INPUT_BYTES: usize = 64 * 1024;
const MAX_RESULT_BYTES: usize = 512 * 1024; // before duplicating into text and structuredContent
const MAX_LINE_BYTES: usize = 2 * 1024 * 1024;
const DEFAULT_PROTOCOL: &str = "2025-11-25";
const PROTOCOLS: &[&str] = &[DEFAULT_PROTOCOL, "2025-06-18", "2025-03-26", "2024-11-05"];
const PLATFORMS: &[&str] = &["wechat", "wecom", "qq", "telegram", "email"];

#[derive(Clone, Copy, PartialEq, Eq)]
enum Tool {
    Conversations,
    Messages,
    Search,
    Contacts,
}

#[derive(Clone, Copy)]
enum Rule {
    Platform { exact: bool },
    Text { min: usize, max: usize },
    Number { min: i64, max: i64 },
}

#[derive(Clone, Copy)]
struct Field {
    name: &'static str,
    required: bool,
    rule: Rule,
}

const PLATFORM: Field = Field {
    name: "platform",
    required: false,
    rule: Rule::Platform { exact: false },
};
const LIMIT: Field = Field {
    name: "limit",
    required: false,
    rule: Rule::Number { min: 1, max: 100 },
};
const OFFSET: Field = Field {
    name: "offset",
    required: false,
    rule: Rule::Number {
        min: 0,
        max: 100_000,
    },
};

const fn field(name: &'static str, required: bool, min: usize, max: usize) -> Field {
    Field {
        name,
        required,
        rule: Rule::Text { min, max },
    }
}

const BROWSE_FIELDS: &[Field] = &[PLATFORM, field("query", false, 0, 1000), LIMIT, OFFSET];
const CONTACT_FIELDS: &[Field] = &[
    PLATFORM,
    field("query", false, 0, 1000),
    LIMIT,
    OFFSET,
    field("account_id", false, 0, 256),
];
const MESSAGE_FIELDS: &[Field] = &[
    Field {
        required: true,
        rule: Rule::Platform { exact: true },
        ..PLATFORM
    },
    field("account_id", true, 1, 256),
    field("conversation_id", true, 1, 1024),
    field("query", false, 0, 2000),
    field("type", false, 0, 128),
    field("start", false, 0, 128),
    field("end", false, 0, 128),
    field("cursor", false, 0, 128),
    LIMIT,
    OFFSET,
];
const SEARCH_FIELDS: &[Field] = &[
    PLATFORM,
    field("account_id", false, 1, 256),
    field("conversation_id", false, 1, 1024),
    field("query", true, 1, 2000),
    field("type", false, 0, 128),
    field("start", false, 0, 128),
    field("end", false, 0, 128),
    field("cursor", false, 0, 128),
    LIMIT,
    OFFSET,
];
const TOOLS: &[Tool] = &[
    Tool::Conversations,
    Tool::Messages,
    Tool::Search,
    Tool::Contacts,
];

impl Tool {
    fn fields(self) -> &'static [Field] {
        match self {
            Self::Conversations => BROWSE_FIELDS,
            Self::Messages => MESSAGE_FIELDS,
            Self::Search => SEARCH_FIELDS,
            Self::Contacts => CONTACT_FIELDS,
        }
    }

    fn name(self) -> &'static str {
        match self {
            Self::Conversations => "list_conversations",
            Self::Messages => "list_messages",
            Self::Search => "search_messages",
            Self::Contacts => "list_contacts",
        }
    }

    fn permission(self) -> Permission {
        match self {
            Self::Conversations => Permission::Conversations,
            Self::Messages | Self::Search => Permission::Messages,
            Self::Contacts => Permission::Contacts,
        }
    }

    fn description(self) -> &'static str {
        match self {
            Self::Conversations => "List indexed conversations in the standalone snapshot.",
            Self::Messages => {
                "List messages from an exact indexed platform, account and conversation."
            }
            Self::Search => {
                "Search indexed message text. Optional account and conversation filters require an exact platform."
            }
            Self::Contacts => "List indexed contacts and groups in the standalone snapshot.",
        }
    }

    fn definition(self, annotated: bool, scope: Option<&AccountScope>) -> Value {
        let mut properties = Map::new();
        let mut required = Vec::new();
        for field in self.fields() {
            if field.required && !(scope.is_some() && matches!(field.name,"platform"|"account_id")) {
                required.push(field.name);
            }
            let schema = match field.rule {
                Rule::Text { min, max } => {
                    json!({ "type":"string", "minLength":min, "maxLength":max, "description":format!("Maximum {max} UTF-8 bytes.") })
                }
                Rule::Number { min, max } => json!({"type":"integer","minimum":min,"maximum":max}),
                Rule::Platform { exact } => {
                    let mut platforms = PLATFORMS.to_vec();
                    if !exact {
                        platforms.insert(0, "all");
                    }
                    json!({"type":"string","enum":platforms})
                }
            };
            let schema = match (scope,field.name) {
                (Some(scope),"platform") => json!({"type":"string","enum":[scope.platform]}),
                (Some(scope),"account_id") => json!({"type":"string","enum":[scope.account_id]}),
                (Some(_),"cursor") => json!({"type":"string","maxLength":2048}),
                _ => schema,
            };
            properties.insert(field.name.to_owned(), schema);
        }
        let description = match (scope,self) {
            (Some(_),Self::Search) => "Search indexed messages in the granted account only.",
            _ => self.description(),
        };
        let mut definition = json!({
            "name":self.name(), "description":description,
            "inputSchema":{"type":"object","additionalProperties":false,"required":required,"properties":properties}
        });
        if annotated {
            definition["annotations"] = json!({"readOnlyHint":true,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false});
        }
        definition
    }

    fn query(self, mut args: Map<String, Value>, scoped: bool) -> Result<Query, &'static str> {
        let fields = self.fields();
        if args
            .keys()
            .any(|name| !fields.iter().any(|f| f.name == name))
        {
            return Err("Unknown tool argument.");
        }
        for field in fields {
            match args.get(field.name) {
                None if field.required => return Err("A required tool argument is missing."),
                None => continue,
                Some(value) => {
                    let valid = match field.rule {
                        Rule::Text { min, max } => value.as_str().is_some_and(|v| {
                            let max = if scoped && field.name == "cursor" { 2048 } else { max };
                            v.len() <= max && v.len() >= min && (min == 0 || !v.trim().is_empty())
                        }),
                        Rule::Number { min, max } => {
                            value.as_i64().is_some_and(|v| (min..=max).contains(&v))
                        }
                        Rule::Platform { exact } => value
                            .as_str()
                            .is_some_and(|v| PLATFORMS.contains(&v) || (!exact && v == "all")),
                    };
                    if !valid {
                        return Err("Invalid tool argument type, length or range.");
                    }
                }
            }
        }
        if self == Self::Search {
            let exact_platform = args
                .get("platform")
                .and_then(Value::as_str)
                .is_some_and(|platform| PLATFORMS.contains(&platform));
            if args.contains_key("account_id") && !exact_platform
                || args.contains_key("conversation_id") && !args.contains_key("account_id")
            {
                return Err("Search filters require an exact platform and account scope.");
            }
        }
        if let Some(term) = args.remove("query") {
            args.insert("q".to_owned(), term);
        }
        args.entry("limit").or_insert(json!(50));
        serde_json::from_value(Value::Object(args)).map_err(|_| "Invalid tool arguments.")
    }

    fn call(self, store: &Store, query: &Query) -> Result<Value, StoreError> {
        match self {
            Self::Conversations => store.conversations(query),
            Self::Messages => store.messages(query),
            Self::Search => store.search(query),
            Self::Contacts => store.contacts(query),
        }
    }
}

pub(crate) fn bounded_content(value: &mut Value) {
    match value {
        Value::String(text) => {
            if text
                .get(..5)
                .is_some_and(|prefix| prefix.eq_ignore_ascii_case("data:"))
            {
                *text = "[inline media omitted]".into();
            } else if let Some((index, _)) = text.char_indices().nth(6000) {
                text.truncate(index);
                text.push_str("…[truncated]");
            }
        }
        Value::Array(items) => {
            items.truncate(100);
            items.iter_mut().for_each(bounded_content);
        }
        Value::Object(object) => {
            let overflow: Vec<_> = object.keys().skip(100).cloned().collect();
            for key in overflow {
                object.remove(&key);
            }
            for (key, item) in object.iter_mut() {
                let normalized = key.to_ascii_lowercase();
                if ["base64", "_bytes", "binary"]
                    .iter()
                    .any(|marker| normalized.contains(marker))
                    || [
                        "image_data",
                        "audio_data",
                        "video_data",
                        "attachment_data",
                        "thumbnail_data",
                    ]
                    .contains(&normalized.as_str())
                {
                    *item = json!("[binary data omitted]");
                } else {
                    bounded_content(item);
                }
            }
        }
        _ => {}
    }
}

fn error(id: Value, code: i64, message: &'static str) -> Value {
    json!({"jsonrpc":"2.0","id":id,"error":{"code":code,"message":message}})
}

fn result(id: Value, value: Value) -> Value {
    json!({"jsonrpc":"2.0","id":id,"result":value})
}

fn tool_error(id: Value, message: &'static str) -> Value {
    result(
        id,
        json!({"content":[{"type":"text","text":message}],"isError":true}),
    )
}

enum Frame {
    Line,
    Eof,
    Partial,
    Oversized,
}

/// Process one newline-delimited JSON-RPC frame without draining oversized input.
fn read_frame(reader: &mut impl BufRead, line: &mut Vec<u8>) -> io::Result<Frame> {
    line.clear();
    loop {
        let bytes = reader.fill_buf()?;
        if bytes.is_empty() {
            return Ok(if line.is_empty() {
                Frame::Eof
            } else {
                Frame::Partial
            });
        }
        let remaining = MAX_INPUT_BYTES - line.len();
        let newline = bytes
            .iter()
            .take(remaining + 1)
            .position(|&byte| byte == b'\n');
        let count = newline.unwrap_or(bytes.len());
        if count > remaining {
            return Ok(Frame::Oversized);
        }
        line.extend_from_slice(&bytes[..count]);
        reader.consume(count + usize::from(newline.is_some()));
        if newline.is_some() {
            return Ok(Frame::Line);
        }
    }
}

fn emit(output: &mut impl Write, value: &Value) -> io::Result<()> {
    // A successful result is bounded already, but JSON escaping of the text duplicate
    // can still expand it. Never send a partial oversize frame or include sensitive details.
    let mut line = serde_json::to_vec(value)?;
    if line.len() > MAX_LINE_BYTES {
        line = serde_json::to_vec(&tool_error(
            value["id"].clone(),
            "Result exceeds the response budget; use a smaller page.",
        ))?;
    }
    output.write_all(&line)?;
    output.write_all(b"\n")?;
    output.flush()
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Session {
    New,
    Initializing,
    Ready,
    Shutdown,
}

fn valid_id(id: &Value) -> bool {
    id.as_str().is_some_and(|id| id.len() <= 128) || id.as_i64().is_some() || id.as_u64().is_some()
}

enum Archive {
    Snapshot(Snapshot, Store),
    Live(EventStore),
}

/// Run detached snapshot MCP on pipes. CLI authenticates the supplied plugin.
pub fn serve(input: impl BufRead, output: impl Write, snapshot: Snapshot, store: Store, plugin: Plugin) -> io::Result<()> {
    serve_archive(input,output,Archive::Snapshot(snapshot,store),plugin)
}

/// Run only read-only incoming live MCP on pipes. No HTTP host is started.
pub fn serve_incoming(input: impl BufRead, output: impl Write, events: EventStore, plugin: Plugin) -> io::Result<()> {
    if !plugin.permissions.contains(&Permission::McpIncoming) {
        return Err(io::Error::new(io::ErrorKind::PermissionDenied,"MCP incoming grant required"));
    }
    serve_live(input,output,events,plugin)
}

/// Run an explicitly scoped native-live stdio grant without enabling HTTP.
pub fn serve_live(input: impl BufRead, output: impl Write, events: EventStore, plugin: Plugin) -> io::Result<()> {
    serve_archive(input,output,Archive::Live(events),plugin)
}

fn serve_archive(
    mut input: impl BufRead, mut output: impl Write, archive: Archive, plugin: Plugin,
) -> io::Result<()> {
    let scope = match plugin.kind {
        PluginKind::ScopedTool => plugin.account_scope.as_ref(),
        _ => None,
    };
    let incoming = plugin.permissions.contains(&Permission::McpIncoming);
    let note = plugin.permissions.contains(&Permission::McpConversationNote);
    let live_tool = incoming || note;
    let available = match &archive {
        Archive::Live(_) => scope.is_some() && live_tool && plugin.permissions.len() == 1,
        Archive::Snapshot(..) => !live_tool && (plugin.kind == PluginKind::Tool || scope.is_some()),
    };
    if !available {
        return Err(io::Error::new(io::ErrorKind::PermissionDenied,"MCP grant does not authorize this archive mode"));
    }
    let mut session = Session::New;
    let mut structured = true;
    let mut line = Vec::with_capacity(4096);
    loop {
        match read_frame(&mut input, &mut line)? {
            Frame::Eof => return Ok(()),
            Frame::Partial | Frame::Oversized => {
                emit(
                    &mut output,
                    &error(Value::Null, -32700, "Invalid or oversized JSON-RPC frame."),
                )?;
                return Err(io::Error::new(
                    io::ErrorKind::InvalidData,
                    "invalid MCP input frame",
                ));
            }
            Frame::Line => {}
        }
        let mut message = match serde_json::from_slice::<Value>(&line) {
            Ok(Value::Object(message)) => message,
            Ok(_) => {
                emit(&mut output, &error(Value::Null, -32600, "Invalid request."))?;
                continue;
            }
            Err(_) => {
                emit(&mut output, &error(Value::Null, -32700, "Parse error."))?;
                continue;
            }
        };
        let id = message.remove("id");
        let method = message.get("method").and_then(Value::as_str);
        if message.get("jsonrpc").and_then(Value::as_str) != Some("2.0")
            || method.is_none_or(str::is_empty)
            || id.as_ref().is_some_and(|value| !valid_id(value))
        {
            emit(&mut output, &error(Value::Null, -32600, "Invalid request."))?;
            continue;
        }
        let method = method.expect("method validated");
        let params = message.get("params");
        let Some(id) = id else {
            if method == "exit" && params.is_none_or(Value::is_object) {
                return Ok(());
            }
            if session == Session::Initializing
                && method == "notifications/initialized"
                && params.is_none_or(Value::is_object)
            {
                session = Session::Ready;
            }
            continue;
        };
        let response = match method {
            "initialize" if session == Session::New => {
                if let Some(requested) = params
                    .and_then(Value::as_object)
                    .and_then(|p| p.get("protocolVersion"))
                    .and_then(Value::as_str)
                {
                    let protocol = if PROTOCOLS.contains(&requested) {
                        requested
                    } else {
                        DEFAULT_PROTOCOL
                    };
                    session = Session::Initializing;
                    structured = matches!(protocol, DEFAULT_PROTOCOL | "2025-06-18");
                    let mut capabilities = json!({"tools":{"listChanged":false}});
                    if scope.is_some() && !live_tool {
                        capabilities["resources"] = json!({"listChanged":false,"subscribe":false});
                        if plugin.permissions.contains(&Permission::McpPrompts) {
                            capabilities["prompts"] = json!({"listChanged":false});
                        }
                    }
                    result(id,json!({"protocolVersion":protocol,"capabilities":capabilities,
                        "serverInfo":{"name":"local-message-hub-rs","version":env!("CARGO_PKG_VERSION")}}))
                } else {
                    error(id, -32602, "initialize requires a protocolVersion string.")
                }
            }
            "ping" if params.is_none_or(Value::is_object) => result(id, json!({})),
            "shutdown" if session == Session::Ready && params.is_none_or(Value::is_object) => {
                session = Session::Shutdown;
                result(id, Value::Null)
            }
            "exit" if params.is_none_or(Value::is_object) => {
                emit(&mut output, &result(id, Value::Null))?;
                return Ok(());
            }
            _ if session != Session::Ready => error(id, -32002, "MCP session is not ready."),
            "tools/list"
                if params.is_none_or(|value| {
                    value.as_object().is_some_and(|params| {
                        params.keys().all(|key| key == "_meta")
                            && params.get("_meta").is_none_or(Value::is_object)
                    })
                }) =>
            {
                if incoming {
                    emit(&mut output,&result(id,json!({"tools":[mcp_incoming::definition(scope.unwrap(),structured)]})))?;
                    continue;
                }
                if note {
                    emit(&mut output,&result(id,json!({"tools":[mcp_metadata::definition(scope.unwrap(),structured)]})))?;
                    continue;
                }
                let mut tools: Vec<_> = TOOLS
                    .iter()
                    .copied()
                    .filter(|tool| plugin.permissions.contains(&tool.permission()))
                    .map(|tool| tool.definition(structured,scope))
                    .collect();
                if let Some(scope) = scope.filter(|_| plugin.permissions.contains(&Permission::McpSources)) {
                    tools.push(mcp_scoped::status_tool(scope,structured));
                }
                result(id, json!({"tools":tools}))
            }
            "tools/list" | "ping" => error(id, -32602, "Invalid params."),
            "tools/call" => match &archive {
                Archive::Snapshot(snapshot,store) => call(id, params, snapshot, store, &plugin, scope, structured),
                Archive::Live(events) => call_live(id,params,events,&plugin,scope.unwrap(),structured),
            },
            "resources/list" | "resources/templates/list" | "resources/read" | "prompts/list" | "prompts/get"
                if scope.is_some() && !live_tool => {
                let Archive::Snapshot(snapshot,store) = &archive else { unreachable!() };
                match mcp_scoped::dispatch(method,params,snapshot,store,&plugin,scope.unwrap()) {
                    Ok(value) => result(id,value),
                    Err((code,message)) => error(id,code,message),
                }
            }
            _ => error(id, -32601, "Method not found."),
        };
        emit(&mut output, &response)?;
    }
}

fn call_live(id: Value, params: Option<&Value>, events: &EventStore,
    plugin: &Plugin, scope: &AccountScope, structured: bool) -> Value {
    let Some(params) = params.and_then(Value::as_object).filter(|p|
        p.keys().all(|key| matches!(key.as_str(),"name"|"arguments"|"_meta"))
            && p.get("_meta").is_none_or(Value::is_object)) else {
        return error(id,-32602,"Invalid tool call params.");
    };
    let note = plugin.permissions.contains(&Permission::McpConversationNote);
    let expected_tool = if note { mcp_metadata::TOOL } else { mcp_incoming::TOOL };
    if params.get("name").and_then(Value::as_str) != Some(expected_tool) {
        return error(id,-32602,"Unknown or ungranted tool.");
    }
    let empty=Map::new();
    let Some(args) = params.get("arguments").map_or(Some(&empty),Value::as_object) else {
        return error(id,-32602,"Tool arguments must be an object.");
    };
    let response = if note { mcp_metadata::call(args,events,scope) }
        else { mcp_incoming::call(args,events,plugin,scope) };
    match response {
        Ok(value) => tool_result(id,value,structured),
        Err(message) => tool_error(id,message),
    }
}

fn call(
    id: Value,
    params: Option<&Value>,
    snapshot: &Snapshot,
    store: &Store,
    plugin: &Plugin,
    scope: Option<&AccountScope>,
    structured: bool,
) -> Value {
    let Some(params) = params.and_then(Value::as_object).filter(|params| {
        params
            .keys()
            .all(|key| matches!(key.as_str(), "name" | "arguments" | "_meta"))
    }) else {
        return error(id, -32602, "Invalid tool call params.");
    };
    let Some(name) = params.get("name").and_then(Value::as_str) else {
        return error(id, -32602, "A tool name is required.");
    };
    let mut args = match params.get("arguments") {
        None => Map::new(),
        Some(Value::Object(arguments)) => arguments.clone(),
        _ => return error(id, -32602, "Tool arguments must be an object."),
    };
    if params.get("_meta").is_some_and(|value| !value.is_object()) {
        return error(id, -32602, "Invalid tool call metadata.");
    }
    if name == mcp_scoped::STATUS {
        let Some(scope) = scope.filter(|_| plugin.permissions.contains(&Permission::McpSources)) else {
            return error(id,-32602,"Unknown or ungranted tool.");
        };
        if !mcp_scoped::status_args(&args,scope) { return tool_error(id,"Invalid or ungranted account scope."); }
        return match mcp_scoped::status(snapshot,store,scope) {
            Some(value) => tool_result(id,value,structured),
            None => tool_error(id,"Local query failed."),
        };
    }
    let Some(tool) = TOOLS.iter().copied()
        .find(|tool| tool.name() == name && plugin.permissions.contains(&tool.permission())) else {
        return error(id, -32602, "Unknown or ungranted tool.");
    };
    if let Some(scope) = scope {
        if !matches!(tool,Tool::Messages|Tool::Search)
            || args.get("platform").is_some_and(|v| v.as_str() != Some(&scope.platform))
            || args.get("account_id").is_some_and(|v| v.as_str() != Some(&scope.account_id))
        {
            return tool_error(id,"Invalid or ungranted account scope.");
        }
        args.insert("platform".into(),json!(scope.platform));
        args.insert("account_id".into(),json!(scope.account_id));
    }
    let mut q = match tool.query(args,scope.is_some()) {
        Ok(q) => q,
        Err(message) => return tool_error(id, message),
    };
    q.account_scoped = scope.is_some();
    if snapshot.verify().is_err() {
        return tool_error(id, "Snapshot is no longer available.");
    }
    let mut value = match tool.call(store, &q) {
        Ok(value) => value,
        Err(StoreError::BadQuery(message)) => return tool_error(id, message),
        Err(StoreError::TooLarge) => {
            return tool_error(
                id,
                "Result exceeds the response budget; use a smaller page.",
            );
        }
        Err(_) => return tool_error(id, "Local query failed."),
    };
    if snapshot.verify().is_err() {
        return tool_error(id, "Snapshot is no longer available.");
    }
    if let Some(scope) = scope { mcp_scoped::redact_page(&mut value,scope); }
    tool_result(id,value,structured)
}

fn tool_result(id: Value, mut value: Value, structured: bool) -> Value {
    bounded_content(&mut value);
    let Ok(text) = serde_json::to_string(&value) else {
        return tool_error(id, "Local query failed.");
    };
    if text.len() > MAX_RESULT_BYTES {
        return tool_error(
            id,
            "Result exceeds the response budget; use a smaller page.",
        );
    }
    // The text and structured value already have owners. Move them into the
    // same wire objects rather than reserializing either through json!'s Value serializer.
    let mut response = json!({"content":[{"type":"text","text":null}],"isError":false});
    response["content"][0]["text"] = Value::String(text);
    if structured {
        response["structuredContent"] = value;
    }
    let mut envelope = json!({"jsonrpc":"2.0","id":null,"result":null});
    envelope["id"] = id;
    envelope["result"] = response;
    envelope
}
