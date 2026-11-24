//! Explicitly confirmed note-only organization for an exact native stdio grant.
use crate::{
    events::EventStore,
    metadata::{self, ConversationWrite, Kind, MetaError, Target},
    plugins::AccountScope,
};
use serde_json::{Map, Value, json};

pub(crate) const TOOL: &str = "manage_conversation_note";

pub(crate) fn definition(scope: &AccountScope, annotated: bool) -> Value {
    let mut tool = json!({
        "name": TOOL,
        "description": "Get the metadata revision for one exact conversation, or replace its local note after explicit user approval. Does not send messages. An empty note clears it.",
        "inputSchema": {
            "type":"object","additionalProperties":false,
            "required":["operation","platform","account_id","conversation_id"],
            "properties":{
                "operation":{"enum":["revision","update"]},
                "platform":{"type":"string","const":scope.platform},
                "account_id":{"type":"string","const":scope.account_id},
                "conversation_id":{"type":"string","minLength":1,"maxLength":512},
                "expected_revision":{"type":"integer","minimum":0},
                "note":{"type":"string","maxLength":4096},
                "confirm":{"const":true}
            },
            "oneOf":[
                {"properties":{"operation":{"const":"revision"}},"maxProperties":4},
                {"properties":{"operation":{"const":"update"}},"required":["expected_revision","note","confirm"]}
            ]
        }
    });
    if annotated {
        tool["annotations"] = json!({"readOnlyHint":false,"destructiveHint":true,"idempotentHint":false,"openWorldHint":false});
    }
    tool
}

fn sanitized(error: MetaError) -> &'static str {
    match error {
        MetaError::Input | MetaError::Missing => "Invalid or unavailable conversation in this grant.",
        MetaError::Conflict => "Metadata changed; read its current revision and obtain approval again.",
        MetaError::Upgrade => "Metadata audit requires the explicit offline archive upgrade.",
        MetaError::Storage => "Conversation metadata unavailable.",
    }
}

pub(crate) fn call(args: &Map<String, Value>, events: &EventStore, scope: &AccountScope)
    -> Result<Value, &'static str>
{
    const INVALID: &str = "Invalid or ungranted conversation note arguments.";
    if args.get("platform").and_then(Value::as_str) != Some(scope.platform.as_str())
        || args.get("account_id").and_then(Value::as_str) != Some(scope.account_id.as_str())
    { return Err(INVALID); }

    let target = Target {
        id: args.get("conversation_id").and_then(Value::as_str).ok_or(INVALID)?.to_owned(),
        platform: scope.platform.clone(),
        account_id: scope.account_id.clone(),
    };
    match args.get("operation").and_then(Value::as_str) {
        Some("revision") if args.len() == 4 => {
            let row = metadata::state(events,scope,target,Kind::Conversation).map_err(sanitized)?;
            Ok(json!({"revision":row["revision"]}))
        }
        Some("update") if args.len() == 7 && args.get("confirm").and_then(Value::as_bool) == Some(true) => {
            let expected_revision = args.get("expected_revision").and_then(Value::as_i64)
                .filter(|rev| *rev >= 0).ok_or(INVALID)?;
            let note = args.get("note").and_then(Value::as_str)
                .filter(|note| note.len() <= 4096 * 4 && metadata::limited_text(note,4096,true))
                .ok_or(INVALID)?;
            let row = metadata::write_conversation(events,scope,ConversationWrite {
                target, expected_revision, note: Some(note.to_owned()), tags: None, archived: None,
            }).map_err(sanitized)?;
            Ok(json!({"changed":row["changed"],"revision":row["revision"]}))
        }
        _ => Err(INVALID),
    }
}
