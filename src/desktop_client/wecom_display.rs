//! Finalized local display metadata, separate from canonical conversation data.
use super::{Client, Error, MAX_TARGET};
use crate::{
    desktop_sdk::Mode,
    wecom_sync::{FROZEN_CONVERSATION_DISPLAY, display_conversation_id_valid},
};
use serde::Deserialize;

const MAX_REPLY:usize=4096;

#[derive(Clone,Copy,Debug,Deserialize,Eq,PartialEq)]
#[serde(rename_all="lowercase")]
pub enum Kind { Direct, Group }

/// Private text from the last finalized local rendered snapshot.
/// It is not a member list, authoritative display name, or refresh status.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Display {
    platform:String,
    account_id:String,
    conversation_id:String,
    pub source_title:String,
    pub kind:Kind,
    pub participants:u32,
}

impl Display {
    pub fn conversation_id(&self)->&str { &self.conversation_id }
}

impl Client<'_> {
    pub fn supports_wecom_rendered_display(&self)->bool {
        self.active && self.navigation.wecom_rendered_display
    }

    /// Explicit lookup of a single current indexed conversation under this
    /// exact Linux native-live grant. None is indistinguishable between no
    /// finalized display and no matching accessible conversation.
    /// Does not fall back to, or mutate, a canonical title.
    pub fn wecom_rendered_display(&mut self,conversation_id:&str)->Result<Option<Display>,Error>{
        if !self.navigation.wecom_rendered_display || self.connection.mode()!=Mode::NativeLive
            || self.scope.platform!="wecom" {
            return Err(Error::Unsupported);
        }
        if !display_conversation_id_valid(conversation_id) {return Err(Error::Input);}
        let mut query=url::form_urlencoded::Serializer::new(String::new());
        query.append_pair("conversation_id",conversation_id);
        let target=format!("{FROZEN_CONVERSATION_DISPLAY}?{}",query.finish());
        if target.len()>MAX_TARGET {return Err(Error::Input);}
        let row:Display=match self.call(&target,true,MAX_REPLY) {
            Ok(row)=>row,
            Err(Error::Status(404))=>return Ok(None),
            Err(error)=>return Err(error),
        };
        if row.platform!="wecom" || row.account_id!=self.scope.account_id
            || row.conversation_id!=conversation_id {
            self.active=false;
            return Err(Error::Identity);
        }
        let expected_kind=if row.participants>2 {Kind::Group} else {Kind::Direct};
        if row.source_title.trim().is_empty() || row.source_title.len()>512
            || row.source_title.chars().any(char::is_control) || row.participants>100_000
            || row.kind!=expected_kind {
            self.active=false;
            return Err(Error::Protocol);
        }
        Ok(Some(row))
    }
}
