//! Narrow legacy request adapter onto the manual voice annotation store.
//! No audio resolution, model execution, writes, paths or network dependencies.
use crate::{events::EventStore,plugins::AccountScope,transcription::{self,Error,Target}};
use serde::{Deserialize,Serialize};

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Request {
    platform:String,
    account_id:String,
    conversation_id:String,
    message_id:String,
}

#[derive(Serialize)]
pub(crate) struct Status {
    available:bool,
    reason:&'static str,
    automatic:bool,
    manual_annotations_available:bool,
}

#[derive(Serialize)]
#[serde(untagged)]
pub(crate) enum Lookup {
    Found {
        available:bool,
        text:String,
        source:&'static str,
        automatic:bool,
        revision:i64,
        #[serde(skip_serializing_if="Option::is_none")]
        language:Option<String>,
    },
    Unavailable {
        available:bool,
        reason:&'static str,
        message:&'static str,
        automatic:bool,
    },
}

pub(crate) fn status(store:&EventStore)->Result<Status,Error> {
    store.require_ordinary()?;
    store.verify()?;
    Ok(Status{
        available:false,
        reason:"automatic_transcription_unavailable",
        automatic:false,
        manual_annotations_available:true,
    })
}

fn id(value:&str)->bool {
    !value.trim().is_empty() && value.len()<=1024 && value.chars().count()<=256
        && !value.chars().any(char::is_control)
}

pub(crate) fn lookup(store:&EventStore,scope:&AccountScope,q:Request)->Result<Lookup,Error> {
    // Bind scope before looking at any archive data. An unknown target or a
    // denied account does not identify the data behind the caller's selector.
    if scope.platform!=q.platform || scope.account_id!=q.account_id {
        return Err(Error::Missing);
    }
    if !id(&q.account_id) || !id(&q.conversation_id) || !id(&q.message_id){
        return Err(Error::Input);
    }
    let value=transcription::get(store,scope,Target{
        conversation_id:q.conversation_id,message_id:q.message_id,
    })?;
    store.verify()?;
    Ok(match value.into_annotation() {
        Some((revision,text,language))=>Lookup::Found{
            available:true,text,source:"manual_local_annotation",automatic:false,revision,language,
        },
        None=>Lookup::Unavailable{
            available:false,reason:"manual_annotation_not_found",
            message:"此语音没有本机手动转写。此接口不会自动转写或获取音频。",
            automatic:false,
        },
    })
}
