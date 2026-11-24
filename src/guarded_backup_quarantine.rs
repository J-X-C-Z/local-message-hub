//! Reversible catalog visibility, not physical file moves or secure deletion.
use crate::guarded_backup_catalog::{Catalog,Quarantined,State,id_valid};
use serde::Serialize;
use sha2::{Digest,Sha256};
use std::path::Path;

#[derive(Debug,thiserror::Error)]
pub(crate) enum Error {
    #[error("invalid quarantine action or confirmation")]
    Input,
    #[error("catalog unavailable or not fully verified for this scope")]
    Catalog,
    #[error("quarantine target or state is not applicable")]
    Conflict,
    #[error("confirmation no longer matches this catalog and action")]
    Stale,
    #[error("quarantine marker could not be published")]
    Storage,
}
#[derive(Clone,Copy,Serialize)]
#[serde(rename_all="snake_case")]
pub(crate) enum Action { Quarantine,Release }
impl Action {
    pub(crate) fn parse(value:&str)->Result<Self,Error> {
        match value {"quarantine"=>Ok(Self::Quarantine),"release"=>Ok(Self::Release),
            _=>Err(Error::Input)}
    }
}
#[derive(Serialize)]
pub(crate) struct Receipt {
    event:&'static str,
    action:Action,
    id:String,
    bytes:u64,
    manifest_sha256:String,
    confirmation:String,
    applied:bool,
    parent_synced:Option<bool>,
}

pub(crate) fn operation(directory:&Path,platform:&str,account:&str,passphrase:&str,
    action:Action,id:&str,confirmation:Option<&str>)->Result<Receipt,Error> {
    if !id_valid(id)||confirmation.is_some_and(|pin|!id_valid(pin)){return Err(Error::Input);}
    let catalog=Catalog::open(directory,confirmation.is_some()).map_err(|_|Error::Catalog)?;
    let inventory=catalog.strict_all(platform,account,passphrase).map_err(|_|Error::Catalog)?;
    let (ordinal,target)=inventory.backups.iter().enumerate().find(|(_,member)|member.id==id)
        .ok_or(Error::Conflict)?;
    let member=Quarantined{ordinal,id:id.to_owned(),bytes:target.bytes};
    let active=catalog.state.as_ref().and_then(|s|s.member.as_ref());
    match action {
        Action::Quarantine if active.is_none() && inventory.backups.len()>1=>{},
        Action::Release if active==Some(&member)=>{},
        _=>return Err(Error::Conflict),
    }
    let serialized=serde_json::to_vec(&("lmh-catalog-visibility-v1",catalog.root_identity(),
        platform,account,&inventory.manifest_sha256,catalog.state.as_ref().map(|s|&s.generation),
        &inventory.backups,action,&member)).map_err(|_|Error::Storage)?;
    let pin=hex::encode(Sha256::digest(serialized));
    let parent_synced=if let Some(confirm)=confirmation {
        if confirm!=pin{return Err(Error::Stale);}
        let mut generation=[0_u8;32];
        getrandom::fill(&mut generation).map_err(|_|Error::Storage)?;
        let desired=State{version:1,generation:hex::encode(generation),manifest_sha256:inventory.manifest_sha256.clone(),
            member:if matches!(action,Action::Quarantine){Some(member.clone())}else{None}};
        Some(catalog.write_state(&desired).map_err(|_|Error::Storage)?)
    } else {None};
    Ok(Receipt{event:"guarded_catalog_quarantine",action,id:id.to_owned(),bytes:target.bytes,
        manifest_sha256:inventory.manifest_sha256,confirmation:pin,applied:confirmation.is_some(),parent_synced})
}
