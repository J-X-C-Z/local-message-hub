//! A separate, local operator workflow, not a plugin route or desktop IPC command.
use crate::{draft_review::{self,Decision},events::EventStore,plugins::AccountScope};
use serde::{Deserialize,Serialize};
use serde_json::json;
use std::{
    error::Error,
    io::{self,BufRead,IsTerminal,Read,Write},
    path::Path,
};
use zeroize::{Zeroize,Zeroizing};

const MAX_FRAME:u64=8192;
fn invalid(message:&'static str)->io::Error { io::Error::new(io::ErrorKind::InvalidInput,message) }

fn frame()->io::Result<Zeroizing<Vec<u8>>> {
    if io::stdin().is_terminal() || io::stdout().is_terminal() {
        return Err(invalid("supply private stdin/stdout pipes"));
    }
    #[cfg(any(target_os="linux",target_os="macos"))]
    {
        let input=rustix::fs::fstat(io::stdin()).map_err(|_|invalid("stdin unavailable"))?;
        let output=rustix::fs::fstat(io::stdout()).map_err(|_|invalid("stdout unavailable"))?;
        if [input.st_mode,output.st_mode].into_iter()
            .any(|mode|rustix::fs::FileType::from_raw_mode(mode as _)!=rustix::fs::FileType::Fifo) {
            return Err(invalid("supply private stdin/stdout pipes"));
        }
    }
    #[cfg(all(unix,not(any(target_os="linux",target_os="macos"))))]
    {
        use std::os::unix::fs::FileTypeExt;
        for path in ["/dev/fd/0","/dev/fd/1"] {
            if !std::fs::metadata(path).map_err(|_|invalid("private pipe unavailable"))?
                .file_type().is_fifo() {
                return Err(invalid("supply private stdin/stdout pipes"));
            }
        }
    }
    #[cfg(not(unix))]
    return Err(invalid("local review operator requires a Unix stdin pipe"));

    #[cfg(unix)]
    {
        let mut input=io::stdin().lock();
        let mut bytes=Zeroizing::new(Vec::new());
        input.by_ref().take(MAX_FRAME+1).read_until(b'\n',&mut bytes)?;
        if bytes.len() as u64>MAX_FRAME || !bytes.ends_with(b"\n") || !input.fill_buf()?.is_empty() {
            return Err(invalid("expected one frame and EOF"));
        }
        Ok(bytes)
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Provision { passphrase:String }
impl Drop for Provision {fn drop(&mut self){self.passphrase.zeroize();}}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Inspect { passphrase:String, scope:AccountScope, review_id:String }
impl Drop for Inspect {fn drop(&mut self){self.passphrase.zeroize();}}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Decide {
    passphrase:String, scope:AccountScope, review_id:String, challenge:String, decision:Decision,
}
impl Drop for Decide {fn drop(&mut self){self.passphrase.zeroize();self.challenge.zeroize();}}

fn scope_check(scope:&AccountScope)->io::Result<()> {
    if [&scope.platform,&scope.account_id].into_iter().any(|s|
        s.trim().is_empty() || s.len()>1024 || s.chars().any(char::is_control))
    { return Err(invalid("invalid local review scope")); }
    Ok(())
}

fn output(value:impl Serialize)->Result<(),Box<dyn Error>> {
    let mut out=io::stdout().lock();
    serde_json::to_writer(&mut out,&value)?;
    writeln!(out)?;
    out.flush()?;
    Ok(())
}

/// The operator's trusted launcher must supply private descriptors. The
/// passphrase and challenge are never accepted through argv, env, or HTTP.
pub fn run(directory:&Path,command:&str)->Result<(),Box<dyn Error>> {
    let bytes=frame()?;
    let archive=EventStore::open(directory)?;
    match command {
        "draft-review-init" => {
            let q:Provision=serde_json::from_slice(&bytes).map_err(|_|invalid("invalid provisioning frame"))?;
            drop(bytes);
            draft_review::provision(&archive,&q.passphrase)?;
            output(json!({"initialized":true,"mode":"local_review_only"}))
        }
        "draft-review-inspect" => {
            let q:Inspect=serde_json::from_slice(&bytes).map_err(|_|invalid("invalid inspection frame"))?;
            drop(bytes);
            scope_check(&q.scope)?;
            output(draft_review::inspect(&archive,&q.passphrase,&q.scope,&q.review_id)?)
        }
        "draft-review-decide" => {
            let q:Decide=serde_json::from_slice(&bytes).map_err(|_|invalid("invalid decision frame"))?;
            drop(bytes);
            scope_check(&q.scope)?;
            output(draft_review::decide(&archive,&q.passphrase,&q.scope,&q.review_id,&q.challenge,q.decision)?)
        }
        #[cfg(any(target_os="linux",target_os="macos"))]
        "draft-handoff-preview" => {
            use crate::draft_handoff::{self, PreviewInput};
            let q:PreviewInput=serde_json::from_slice(&bytes).map_err(|_|invalid("invalid handoff preview frame"))?;
            drop(bytes);
            output(draft_handoff::preview(&archive,&q)?)
        }
        #[cfg(any(target_os="linux",target_os="macos"))]
        "draft-handoff-pull" => {
            use crate::draft_handoff::{self, PullInput};
            let q:PullInput=serde_json::from_slice(&bytes).map_err(|_|invalid("invalid handoff approval frame"))?;
            drop(bytes);
            output(draft_handoff::pull(&archive,&q)?)
        }
        #[cfg(any(target_os="linux",target_os="macos"))]
        "draft-handoff-reconcile" => {
            use crate::draft_handoff_receipt::{self, Input};
            let q:Input=serde_json::from_slice(&bytes).map_err(|_|invalid("invalid signed receipt frame"))?;
            drop(bytes);
            output(draft_handoff_receipt::reconcile(&archive,&q)?)
        }
        _=>Err(invalid("invalid local review command").into()),
    }
}
