use crate::{events::{EventError,EventStore},plugins::{AccountScope,Plugin}};
use serde_json::{Map,Value,json};
use sha2::{Digest,Sha256};
use std::{thread,time::{Duration,Instant}};

pub(crate) const TOOL: &str = "wait_for_new_messages";
const POLL: Duration = Duration::from_millis(250);

pub(crate) fn definition(scope: &AccountScope, annotated: bool) -> Value {
    let mut val = json!({"name":TOOL,
        "description":"Initialize or briefly wait for newly indexed incoming creations in the granted native account. Returns metadata only. Pass the issued cursor back; never marks anything read.",
        "inputSchema":{"type":"object","additionalProperties":false,"properties":{
            "platform":{"type":"string","enum":[scope.platform]},
            "after_cursor":{"type":"string","maxLength":64},"timeout_seconds":{"type":"integer","minimum":0,"maximum":30},
            "limit":{"type":"integer","minimum":1,"maximum":50}}}});
    if annotated {
        val["annotations"] = json!({"readOnlyHint":true,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false});
    }
    val
}

fn cursor_tag(archive: &EventStore, plugin: &Plugin, scope: &AccountScope) -> String {
    let bytes = serde_json::to_vec(&("lmh-incoming-v1",archive.archive_id(),&plugin.id,&scope.platform,&scope.account_id))
        .expect("strings serialize");
    format!("i-{}",hex::encode(&Sha256::digest(bytes)[..16]))
}

fn parse_cursor(raw: &str, namespace: &str) -> Option<i64> {
    let (tag,id) = raw.split_once(':')?;
    if tag != namespace || id.is_empty() || id.len() > 19 || !id.bytes().all(|b| b.is_ascii_digit())
        || (id.len() > 1 && id.starts_with('0'))
    { return None; }
    id.parse().ok()
}

fn number(args: &Map<String,Value>, field: &str, min: u64, max: u64, default: u64) -> Option<u64> {
    args.get(field).map_or(Some(default),Value::as_u64).filter(|n| (min..=max).contains(n))
}

pub(crate) fn call(args: &Map<String,Value>, archive: &EventStore, plugin: &Plugin,
    scope: &AccountScope) -> Result<Value,&'static str> {
    const INVALID: &str = "Invalid or ungranted incoming wait arguments.";
    const CURSOR: &str = "Invalid incoming cursor; initialize this native feed again.";
    if !args.keys().all(|key| matches!(key.as_str(),"platform"|"after_cursor"|"timeout_seconds"|"limit"))
        || args.get("platform").is_some_and(|v| v.as_str() != Some(&scope.platform))
    { return Err(INVALID); }
    let timeout = Duration::from_secs(number(args,"timeout_seconds",0,30,15).ok_or(INVALID)?);
    let limit = number(args,"limit",1,50,20).ok_or(INVALID)? as usize;
    let namespace = cursor_tag(archive,plugin,scope);
    let after = args.get("after_cursor").map(|cursor| cursor.as_str()
        .filter(|s| s.len() <= 64).and_then(|raw| parse_cursor(raw,&namespace)).ok_or(CURSOR)).transpose()?;
    let deadline = Instant::now() + timeout;
    loop {
        let page = archive.incoming(scope,after,limit).map_err(|error| match error {
            EventError::BadRequest(_) => CURSOR,
            EventError::IncomingUnavailable => "Incoming feed requires the explicit offline archive upgrade.",
            _ => "Incoming feed unavailable.",
        })?;
        let cursor = format!("{namespace}:{}",page.next_after);
        if after.is_none() {
            return Ok(json!({"cursor":cursor,"messages":[],"timed_out":false,"initialized":true,"reset":false,"has_more":false}));
        }
        if !page.items.is_empty() {
            let messages: Vec<_> = page.items.into_iter().map(|entry| json!({
                "id":entry.message_id,"platform":entry.platform,"account_id":entry.account_id,
                "conversation_id":entry.conversation_id,"timestamp":entry.timestamp,"is_self":false,
            })).collect();
            return Ok(json!({"cursor":cursor,"messages":messages,"timed_out":false,"initialized":false,"reset":false,"has_more":page.has_more}));
        }
        let remaining = deadline.saturating_duration_since(Instant::now());
        if remaining.is_zero() {
            return Ok(json!({"cursor":cursor,"messages":[],"timed_out":true,"initialized":false,"reset":false,"has_more":false}));
        }
        thread::sleep(POLL.min(remaining));
    }
}
