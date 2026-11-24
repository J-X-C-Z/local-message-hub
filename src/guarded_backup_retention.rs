//! Read-only retention arithmetic over a fully authenticated offline inventory.
use crate::guarded_backup_catalog::{self as catalog,VerifiedBackup};
use serde::Serialize;
use std::path::Path;

#[derive(Debug,thiserror::Error)]
#[error("invalid guarded backup retention policy")]
pub(crate) struct PolicyError;

pub(crate) struct Policy {keep_latest:usize,target_bytes:Option<u64>}
impl Policy {
    pub(crate) fn new(keep_latest:usize,target_bytes:Option<u64>)->Result<Self,PolicyError> {
        let ceiling=(catalog::MAX_ENTRIES*crate::guarded_backup::MAX_FILE) as u64;
        if !(1..=catalog::MAX_ENTRIES).contains(&keep_latest)
            || target_bytes.is_some_and(|budget|budget>ceiling) {return Err(PolicyError);}
        Ok(Self{keep_latest,target_bytes})
    }
}

#[derive(Serialize)]
pub(crate) struct Plan {
    event:&'static str,
    format:&'static str,
    order:&'static str,
    manifest_sha256:String,
    keep_latest:usize,
    target_bytes:Option<u64>,
    current_bytes:u64,
    retained_bytes_if_review_applied:u64,
    over_target_bytes_if_review_applied:Option<u64>,
    protected:Vec<VerifiedBackup>,
    retained_for_budget:Vec<VerifiedBackup>,
    review_only:Vec<VerifiedBackup>,
    applies_changes:bool,
}

pub(crate) fn preview(directory:&Path,platform:&str,account:&str,passphrase:&str,policy:Policy)
    ->Result<Plan,catalog::StrictInventoryError> {
    let inventory=catalog::strict_inventory(directory,platform,account,passphrase)?;
    let eligible=inventory.backups.len().saturating_sub(policy.keep_latest);
    let current_bytes=inventory.backups.iter().map(|entry|entry.bytes).sum::<u64>();
    let mut retained_bytes=current_bytes;
    let mut protected=Vec::new();
    let mut retained_for_budget=Vec::new();
    let mut review_only=Vec::new();
    for (ordinal,backup) in inventory.backups.into_iter().enumerate() {
        if ordinal>=eligible {
            protected.push(backup);
        } else if policy.target_bytes.is_none_or(|budget|retained_bytes>budget) {
            retained_bytes-=backup.bytes;
            review_only.push(backup);
        } else {
            // Additional older backups need not be reviewed to reach the budget.
            retained_for_budget.push(backup);
        }
    }
    Ok(Plan {
        event:"guarded_backup_retention_plan",format:"LMHENC01",order:"manifest_oldest_first",
        manifest_sha256:inventory.manifest_sha256,keep_latest:policy.keep_latest,
        target_bytes:policy.target_bytes,current_bytes,retained_bytes_if_review_applied:retained_bytes,
        over_target_bytes_if_review_applied:policy.target_bytes.map(|budget|retained_bytes.saturating_sub(budget)),
        protected,retained_for_budget,review_only,applies_changes:false,
    })
}
