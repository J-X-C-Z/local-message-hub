//! Native-live routing metadata only: no native OS delivery, scheduler or UI.
use super::{Client as Loopback, Credential, Error as LoopbackError, valid_identifier, hex64};
use crate::{desktop_sdk::{Connection,Mode},plugins::AccountScope};
use serde::{Deserialize,Serialize};
use std::{collections::BTreeSet,sync::Arc};
use zeroize::Zeroizing;

const PREFERENCES:&str="/api/v1/notifications/preferences";
const PULL:&str="/api/v1/notifications/pull";
const ACK:&str="/api/v1/notifications/ack";
const ACTIVATION_TARGET:&str="/api/v1/notifications/activation-target";
const MAX_POLICY:usize=320*1024;
const MAX_PAGE:usize=128*1024;
const MAX_IDS:usize=100;
const MAX_PULL:usize=50;

#[derive(Debug,Clone,Copy,PartialEq,Eq)]
pub enum Operation { SetPreferences, Acknowledge }

#[derive(Debug,thiserror::Error)]
pub enum Error {
    #[error(transparent)]
    Session(#[from] LoopbackError),
    #[error("notification preference revision changed; reload before editing")]
    RevisionConflict,
    #[error("notification pull state changed; no batch was returned")]
    DrainConflict,
    #[error("notification pull is temporarily unavailable")]
    DrainUnavailable,
    #[error("notification receipt was rejected; pull again from current state")]
    ReceiptRejected,
    #[error("notification receipt is stale; pull again from current state")]
    ReceiptStale,
    #[error("a post has no verified result ({0:?}); reconcile before another mutation")]
    OutcomeUnknown(Operation),
}

#[derive(Deserialize)]
struct Advertised {
    preferences:Option<String>,
    pull:Option<String>,
    ack:Option<String>,
    can_write:bool,
    activation_target:Option<String>,
    delivery:bool,
}

#[derive(Debug,Clone,Copy,Default,PartialEq,Eq,Serialize,Deserialize)]
#[serde(rename_all="snake_case")]
pub enum FilterMode { #[default] All, Allowlist }
#[derive(Debug,Clone,Copy,Default,PartialEq,Eq,Serialize,Deserialize)]
#[serde(rename_all="snake_case")]
pub enum Style { #[default] Normal, Silent }

#[derive(Clone,Default,PartialEq,Eq)]
pub struct Filters {
    pub mode:FilterMode,
    pub style:Style,
    pub muted_conversations:Vec<String>,
    pub allowed_conversations:Vec<String>,
}

#[derive(Clone,PartialEq,Eq,Serialize,Deserialize)]
#[serde(deny_unknown_fields)]
struct Policy {
    enabled:bool,
    mode:FilterMode,
    style:Style,
    muted_conversations:Vec<String>,
    allowed_conversations:Vec<String>,
}
impl Policy {
    fn filters(&self)->Filters {
        Filters{mode:self.mode,style:self.style,muted_conversations:self.muted_conversations.clone(),
            allowed_conversations:self.allowed_conversations.clone()}
    }
    fn valid(&self)->bool {
        [&self.muted_conversations,&self.allowed_conversations].into_iter().all(|ids|{
            let mut seen=BTreeSet::new();
            ids.len()<=MAX_IDS&&ids.iter().all(|id|valid_identifier(id,256)&&seen.insert(id))
        })
    }
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct SettingsWire {revision:i64,preferences:Policy}

/// A policy version scoped to this exact client session, not a grant.
/// Untrusted local conversation IDs are intentionally not Debug/serialized.
pub struct Settings {
    session:Arc<()>,
    revision:i64,
    preferences:Policy,
}
impl Settings {
    pub fn revision(&self)->i64 {self.revision}
    pub fn enabled(&self)->bool {self.preferences.enabled}
    pub fn filters(&self)->Filters {self.preferences.filters()}
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Match {pub conversation_id:String,pub style:Style}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct PullWire {
    enabled:bool,
    preference_revision:i64,
    has_more:bool,
    items:Vec<Match>,
    #[serde(default)]
    receipt:Option<Receipt>,
}
struct Receipt(Zeroizing<String>);
impl<'de> Deserialize<'de> for Receipt {
    fn deserialize<D:serde::Deserializer<'de>>(d:D)->Result<Self,D::Error>{
        String::deserialize(d).map(|s|Self(Zeroizing::new(s)))
    }
}
impl Receipt {
    fn valid(&self)->bool {
        let raw=self.0.as_str();
        !raw.is_empty()&&raw.len()<=192
            &&raw.bytes().all(|b|b.is_ascii_lowercase()||b.is_ascii_digit()||b==b':')
    }
}

/// Pending is meaningful even when `items` is empty: it can describe
/// filtered progress that requires explicit acknowledgment to advance.
pub enum Pull {
    Disabled {revision:i64},
    CaughtUp {revision:i64},
    Pending(Pending),
}
pub struct Pending {
    session:Arc<()>,
    revision:i64,
    items:Vec<Match>,
    has_more:bool,
    receipt:Receipt,
}
impl Pending {
    pub fn preference_revision(&self)->i64 {self.revision}
    pub fn items(&self)->&[Match] {&self.items}
    pub fn has_more_after_ack(&self)->bool {self.has_more}
}

#[derive(Debug,Clone,Copy,PartialEq,Eq)]
pub enum Acknowledgment { Advanced, AlreadyAcknowledged }

/// This client can use an installation's independently provisioned exact
/// Desktop grant without requiring archive-message read permissions or
/// generating any new grant. It has no arbitrary request or archive read API.
pub struct Client<'a> {
    wire:Loopback<'a>,
    session:Arc<()>,
    can_write:bool,
    can_pull:bool,
    can_resolve_target:bool,
}

/// Plain local routing metadata. Titles are display text, not launch arguments.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ActivationTarget {
    pub platform:String,
    pub account_id:String,
    pub conversation_id:String,
    pub conversation_title:Option<String>,
    pub conversation_kind:Option<String>,
}

impl<'a> Client<'a> {
    /// Takes ownership of an already-authorized secret from the trusted
    /// native caller. The exact manifest is validated before use.
    pub fn connect(connection:Connection,credential:Zeroizing<String>,grant_id:String,
        scope:AccountScope,is_current:impl FnMut(u32)->bool+'a)->Result<Self,Error> {
        if connection.mode()!=Mode::NativeLive {return Err(LoopbackError::Unsupported.into());}
        if !hex64(&credential){return Err(LoopbackError::Input.into());}
        let (wire,manifest)=Loopback::connect_exact(connection,Credential::Installed(credential),
            grant_id,scope,is_current)?;
        let perm=|value:&str|manifest.plugin.permissions.iter().any(|p|p==value);
        if !perm("notifications.read"){return Err(LoopbackError::Unsupported.into());}
        let advertised:Advertised=serde_json::from_value(manifest.local_notifications
            .ok_or(LoopbackError::Unsupported)?).map_err(|_|LoopbackError::Protocol)?;
        if advertised.delivery || advertised.preferences.as_deref()!=Some(PREFERENCES) {
            return Err(LoopbackError::Unsupported.into());
        }
        let can_write=advertised.can_write&&perm("notifications.write");
        let can_pull=can_write&&advertised.pull.as_deref()==Some(PULL)&&advertised.ack.as_deref()==Some(ACK);
        let can_resolve_target=can_pull&&perm("conversations.read")
            && advertised.activation_target.as_deref()==Some(ACTIVATION_TARGET);
        Ok(Self{wire,session:Arc::new(()),can_write,can_pull,can_resolve_target})
    }

    pub fn can_change_preferences(&self)->bool {self.can_write}
    pub fn can_pull_and_ack(&self)->bool {self.can_pull}

    /// Consume the old session. No old receipt or policy version can be
    /// submitted through the new client, even if the backend still accepts it.
    pub fn reconnect(self,new_connection:Connection,is_current:impl FnMut(u32)->bool+'a)
        ->Result<Self,Error> {
        let Loopback{grant,grant_id,scope,..}=self.wire;
        let Credential::Installed(credential)=grant else{return Err(LoopbackError::Identity.into());};
        Self::connect(new_connection,credential,grant_id,scope,is_current)
    }

    pub fn preferences(&mut self)->Result<Settings,Error> {
        let data:SettingsWire=self.wire.call(PREFERENCES,true,MAX_POLICY)?;
        self.settings(data)
    }
    fn settings(&mut self,data:SettingsWire)->Result<Settings,Error> {
        if data.revision<0||!data.preferences.valid(){return self.protocol();}
        Ok(Settings{session:self.session.clone(),revision:data.revision,preferences:data.preferences})
    }

    /// Change only routing filters, preserving the explicit local enable flag.
    pub fn set_filters(&mut self,current:&Settings,filters:Filters)->Result<Settings,Error> {
        self.replace_policy(current,Policy{enabled:current.enabled(),mode:filters.mode,style:filters.style,
            muted_conversations:filters.muted_conversations,allowed_conversations:filters.allowed_conversations})
    }

    /// Explicitly enable/disable local routing metadata. Never configures OS
    /// delivery, starts a polling loop, or modifies a provider setting.
    pub fn set_enabled(&mut self,current:&Settings,enabled:bool)->Result<Settings,Error> {
        let mut policy=current.preferences.clone();
        policy.enabled=enabled;
        self.replace_policy(current,policy)
    }

    fn replace_policy(&mut self,current:&Settings,desired:Policy)->Result<Settings,Error> {
        if !self.can_write {return Err(LoopbackError::Unsupported.into());}
        if !Arc::ptr_eq(&self.session,&current.session)||!desired.valid(){
            return Err(LoopbackError::Input.into());
        }
        #[derive(Serialize)]
        struct Change<'a>{revision:i64,preferences:&'a Policy}
        let input=Change{revision:current.revision,preferences:&desired};
        let response:SettingsWire=self.post(PREFERENCES,&input,MAX_POLICY,MAX_POLICY).map_err(|err|
            match err {
                LoopbackError::Status(409)=>Error::RevisionConflict,
                e=>write_error(e,Operation::SetPreferences),
            })?;
        if response.revision<current.revision || response.preferences!=desired
            || (desired!=current.preferences&&response.revision==current.revision) {
            self.wire.active=false;
            return Err(Error::OutcomeUnknown(Operation::SetPreferences));
        }
        self.settings(response)
    }

    /// Exactly one explicit fetch; no automatic drain, display, or ACK.
    pub fn pull(&mut self,limit:usize)->Result<Pull,Error> {
        if !self.can_pull {return Err(LoopbackError::Unsupported.into());}
        if !(1..=MAX_PULL).contains(&limit){return Err(LoopbackError::Input.into());}
        #[derive(Serialize)]
        struct Limit {limit:usize}
        let response:PullWire=self.post(PULL,&Limit{limit},4096,MAX_PAGE).map_err(|err|
            match err {
                LoopbackError::Status(409)=>Error::DrainConflict,
                LoopbackError::Status(503)=>Error::DrainUnavailable,
                e=>Error::Session(e),
            })?;
        if response.preference_revision<0 || response.items.len()>limit
            ||response.items.iter().any(|item|!valid_identifier(&item.conversation_id,256))
            ||(!response.enabled&&(!response.items.is_empty()||response.receipt.is_some()||response.has_more))
            ||(response.receipt.is_none()&&(!response.items.is_empty()||response.has_more))
            ||response.receipt.as_ref().is_some_and(|receipt|!receipt.valid()) {
            return self.protocol();
        }
        let revision=response.preference_revision;
        Ok(match response.receipt {
            Some(receipt)=>Pull::Pending(Pending{session:self.session.clone(),revision,
                items:response.items,has_more:response.has_more,receipt}),
            None if response.enabled=>Pull::CaughtUp{revision},
            None=>Pull::Disabled{revision},
        })
    }

    pub fn can_resolve_activation_target(&self)->bool {self.can_resolve_target}

    /// Resolve a caller-selected item from this session's page without
    /// acknowledging it, marking it read, or opening anything.
    pub fn resolve_activation_target(&mut self,page:&Pending,conversation_id:&str)
        ->Result<Option<ActivationTarget>,Error> {
        if !self.can_resolve_target{return Err(LoopbackError::Unsupported.into());}
        if !Arc::ptr_eq(&self.session,&page.session){return Err(Error::ReceiptStale);}
        if !valid_identifier(conversation_id,256)
            || !page.items.iter().any(|item|item.conversation_id==conversation_id) {
            return Err(LoopbackError::Input.into());
        }
        #[derive(Serialize)]
        struct Input<'a>{receipt:&'a str,conversation_id:&'a str}
        let target:ActivationTarget=match self.post(ACTIVATION_TARGET,
            &Input{receipt:page.receipt.0.as_str(),conversation_id},4096,8192) {
            Ok(target)=>target,
            Err(LoopbackError::Status(404))=>return Ok(None),
            Err(error)=>return Err(error.into()),
        };
        if target.platform!=self.wire.scope.platform || target.account_id!=self.wire.scope.account_id
            || target.conversation_id!=conversation_id
            || target.conversation_title.as_ref().is_some_and(|s|s.len()>1024||s.chars().any(char::is_control))
            || target.conversation_kind.as_deref().is_some_and(|k|!matches!(k,"private"|"group"|"direct")) {
            return self.protocol();
        }
        Ok(Some(target))
    }

    /// Explicit acknowledgment of a handled or intentionally skipped page.
    /// Passing the same current-session receipt again is an explicit replay,
    /// never an automatic retry or evidence of delivery/display.
    pub fn acknowledge(&mut self,page:&Pending)->Result<Acknowledgment,Error> {
        if !self.can_pull {return Err(LoopbackError::Unsupported.into());}
        if !Arc::ptr_eq(&self.session,&page.session){return Err(Error::ReceiptStale);}
        #[derive(Serialize)]
        struct Input<'a>{receipt:&'a str}
        #[derive(Deserialize)]
        #[serde(deny_unknown_fields)]
        struct ResultWire{acknowledged:bool,duplicate:bool}
        let response:ResultWire=self.post(ACK,&Input{receipt:page.receipt.0.as_str()},4096,1024)
            .map_err(|err|match err {
                LoopbackError::Status(400)=>Error::ReceiptRejected,
                LoopbackError::Status(409)=>Error::ReceiptStale,
                e=>write_error(e,Operation::Acknowledge),
            })?;
        if !response.acknowledged {
            self.wire.active=false;
            return Err(Error::OutcomeUnknown(Operation::Acknowledge));
        }
        Ok(if response.duplicate{Acknowledgment::AlreadyAcknowledged}else{Acknowledgment::Advanced})
    }

    fn protocol<T>(&mut self)->Result<T,Error> {
        self.wire.active=false;
        Err(LoopbackError::Protocol.into())
    }
    fn post<T:for<'de> Deserialize<'de>>(&mut self,target:&'static str,input:&impl Serialize,
        max_input:usize,max_output:usize)->Result<T,LoopbackError> {
        let body=Zeroizing::new(serde_json::to_vec(input).map_err(|_|LoopbackError::Input)?);
        if body.len()>max_input{return Err(LoopbackError::Input);}
        self.wire.call_json(target,true,Some(body.as_slice()),max_output)
    }
}

fn write_error(error:LoopbackError,operation:Operation)->Error {
    match error {
        LoopbackError::Transport | LoopbackError::Protocol | LoopbackError::Stale
        | LoopbackError::TooLarge | LoopbackError::Status(500..=599) => Error::OutcomeUnknown(operation),
        error=>Error::Session(error),
    }
}
