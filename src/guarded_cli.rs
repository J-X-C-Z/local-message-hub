use crate::{events::EventStore, host, legacy_import, plugins::Plugins, profile_lock::{Attempts,Gate,GuardedExclusive}, store::Store};
use serde::Deserialize;
use serde_json::{Value,json};
use std::{error::Error,io::{self,BufRead,IsTerminal,Read,Write},net::{Ipv4Addr,SocketAddr,SocketAddrV4},path::Path,time::Instant,os::unix::fs::MetadataExt};
use zeroize::{Zeroize,Zeroizing};

const FRAME_LIMIT: u64 = 6300;

fn problem(code: &'static str) -> io::Error { io::Error::other(code) }

fn line(reader: &mut impl BufRead) -> io::Result<Option<Zeroizing<Vec<u8>>>> {
    let mut frame=Zeroizing::new(Vec::new());
    reader.take(FRAME_LIMIT+1).read_until(b'\n',&mut frame)?;
    if frame.is_empty() { return Ok(None); }
    if frame.len() as u64>FRAME_LIMIT || !frame.ends_with(b"\n") {
        return Err(problem("invalid guarded control frame"));
    }
    Ok(Some(frame))
}

fn require_pipe() -> io::Result<()> {
    if io::stdin().is_terminal() { return Err(problem("guarded commands require private piped stdin")); }
    let stat=rustix::fs::fstat(io::stdin()).map_err(|_|problem("guarded stdin unavailable"))?;
    if rustix::fs::FileType::from_raw_mode(stat.st_mode) != rustix::fs::FileType::Fifo {
        return Err(problem("guarded commands require private piped stdin"));
    }
    Ok(())
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Provision { passphrase:String }
impl Drop for Provision { fn drop(&mut self) { self.passphrase.zeroize(); } }

#[derive(Deserialize)]
#[serde(tag="command",rename_all="snake_case",deny_unknown_fields)]
enum Control {
    Unlock {passphrase:String}, Lock, Stop, IssueBrowserTicket, BrowserSessionStatus,
    #[serde(alias="revoke_browser_sessions")]
    RevokeBrowser,
}
impl Drop for Control {
    fn drop(&mut self) { if let Self::Unlock { passphrase }=self {passphrase.zeroize();} }
}

fn report(value: Value) -> io::Result<()> {
    let mut output=io::stdout().lock();
    writeln!(output,"{value}")?;
    output.flush()
}

pub fn provision(dir:&Path) -> Result<(),Box<dyn Error>> {
    require_pipe()?;
    let frame=line(&mut io::stdin().lock())?.ok_or_else(||problem("expected provisioning frame"))?;
    let request: Provision=serde_json::from_slice(&frame).map_err(|_|problem("invalid provisioning frame"))?;
    EventStore::init_guarded(dir,&request.passphrase)?;
    report(json!({"event":"initialized","mode":"guarded-read-only","locked":true}))?;
    Ok(())
}

pub fn import(source:&Path,directory:&Path,platform:&str,account:&str,confirmation:Option<&str>,snapshot:bool)
    -> Result<(),Box<dyn Error>> {
    require_pipe()?;
    let mut input=io::stdin().lock();
    let frame=line(&mut input)?.ok_or_else(||problem("expected guarded authorization frame"))?;
    if !input.fill_buf()?.is_empty() {
        return Err(problem("expected exactly one authorization frame and EOF").into());
    }
    drop(input);
    let credentials: Provision=serde_json::from_slice(&frame).map_err(|_|problem("invalid guarded authorization frame"))?;
    let lease=GuardedExclusive::acquire(directory)?;
    let authorized=lease.authorize_import(&credentials.passphrase)?;
    drop(credentials);
    drop(frame);
    let output=match (snapshot,confirmation) {
        (false,Some(tag))=>serde_json::to_value(legacy_import::guarded_commit(source,platform,account,tag,&authorized)?)?,
        (false,None)=>serde_json::to_value(legacy_import::guarded_preview(source,platform,account,&authorized)?)?,
        (true,Some(tag))=>serde_json::to_value(crate::guarded_snapshot::commit(source,platform,account,tag,&authorized)?)?,
        (true,None)=>serde_json::to_value(crate::guarded_snapshot::preview(source,platform,account,&authorized)?)?,
    };
    report(output)?;
    Ok(())
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct BackupCredentials { profile_passphrase:String, backup_passphrase:String }
impl Drop for BackupCredentials {
    fn drop(&mut self) {self.profile_passphrase.zeroize();self.backup_passphrase.zeroize();}
}

pub fn backup(source:&Path,target:&Path,scope:Option<(&str,&str)>)->Result<(),Box<dyn Error>> {
    backup_with_confirmation(source,target,scope,None)
}

fn valid_backup_identity(digest:&str)->bool {
    digest.len()==64 && digest.bytes().all(|b| b.is_ascii_digit() || matches!(b,b'a'..=b'f'))
}

pub fn backup_with_confirmation(source:&Path,target:&Path,scope:Option<(&str,&str)>,
    confirmation:Option<&str>)->Result<(),Box<dyn Error>> {
    if confirmation.is_some_and(|digest| scope.is_some() || !valid_backup_identity(digest)) {
        return Err(problem("invalid guarded backup confirmation").into());
    }
    require_pipe()?;
    let mut input=io::stdin().lock();
    let frame=line(&mut input)?.ok_or_else(||problem("expected guarded backup credential frame"))?;
    if !input.fill_buf()?.is_empty() {return Err(problem("expected one credential frame and EOF").into());}
    let credentials: BackupCredentials=serde_json::from_slice(&frame).map_err(|_|problem("invalid guarded backup credential frame"))?;
    drop(input);
    drop(frame);
    let result=if let Some((platform,account))=scope {
        crate::guarded_backup::create(source,target,platform,account,&credentials.profile_passphrase,&credentials.backup_passphrase)?
    } else {
        crate::guarded_backup::restore(source,target,&credentials.backup_passphrase,&credentials.profile_passphrase,confirmation)?
    };
    report(result)?;
    Ok(())
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct CatalogCredentials { backup_passphrase: String }
impl Drop for CatalogCredentials {
    fn drop(&mut self) { self.backup_passphrase.zeroize(); }
}

fn catalog_credentials()->Result<CatalogCredentials,Box<dyn Error>> {
    require_pipe()?;
    let mut input = io::stdin().lock();
    let frame = line(&mut input)?.ok_or_else(||problem("expected guarded catalog credential frame"))?;
    if !input.fill_buf()?.is_empty() {
        return Err(problem("expected one credential frame and EOF").into());
    }
    let credentials: CatalogCredentials = serde_json::from_slice(&frame)
        .map_err(|_|problem("invalid guarded catalog credential frame"))?;
    drop(input);
    drop(frame);
    Ok(credentials)
}

pub fn backup_preview(source:&Path,platform:&str,account:&str)->Result<(),Box<dyn Error>> {
    let credentials=catalog_credentials()?;
    let result=crate::guarded_backup::preview(source,&credentials.backup_passphrase,platform,account)?;
    drop(credentials);
    report(result)?;
    Ok(())
}

pub fn backup_restore_status(directory:&Path,identity:&str)->Result<(),Box<dyn Error>> {
    if !valid_backup_identity(identity) {return Err(problem("invalid guarded backup identity").into());}
    require_pipe()?;
    let mut input=io::stdin().lock();
    let frame=line(&mut input)?.ok_or_else(||problem("expected guarded authorization frame"))?;
    if !input.fill_buf()?.is_empty() {
        return Err(problem("expected exactly one authorization frame and EOF").into());
    }
    let credentials:Provision=serde_json::from_slice(&frame)
        .map_err(|_|problem("invalid guarded authorization frame"))?;
    drop(input);
    drop(frame);
    let lease=GuardedExclusive::acquire(directory)?;
    let authorized=lease.authorize_import(&credentials.passphrase)?;
    drop(credentials);
    let found=crate::guarded_snapshot::has_backup_receipt(&authorized,identity)?;
    report(json!({"event":"backup_restore_status","restore_receipt_present":found}))?;
    Ok(())
}

pub fn backup_catalog(directory: &Path, platform: &str, account: &str,
    copy: Option<(&str, &Path)>) -> Result<(), Box<dyn Error>> {
    let credentials=catalog_credentials()?;
    let result = crate::guarded_backup_catalog::inspect(directory, platform, account,
        &credentials.backup_passphrase, copy)?;
    drop(credentials);
    report(result)?;
    Ok(())
}

pub fn backup_retention(directory:&Path,platform:&str,account:&str,keep_latest:usize,target_bytes:Option<u64>)
    ->Result<(),Box<dyn Error>> {
    let policy=crate::guarded_backup_retention::Policy::new(keep_latest,target_bytes)?;
    let credentials=catalog_credentials()?;
    let plan=crate::guarded_backup_retention::preview(
        directory,platform,account,&credentials.backup_passphrase,policy)?;
    drop(credentials);
    report(serde_json::to_value(plan)?)?;
    Ok(())
}

pub fn backup_quarantine(directory:&Path,platform:&str,account:&str,action:&str,id:&str,
    confirmation:Option<&str>)->Result<(),Box<dyn Error>> {
    let action=crate::guarded_backup_quarantine::Action::parse(action)?;
    let credentials=catalog_credentials()?;
    let receipt=crate::guarded_backup_quarantine::operation(
        directory,platform,account,&credentials.backup_passphrase,action,id,confirmation)?;
    drop(credentials);
    report(serde_json::to_value(receipt)?)?;
    Ok(())
}

pub async fn serve(dir:&Path,grants:&Path,legacy:bool) -> Result<(),Box<dyn Error>> {
    serve_inner(dir,grants,legacy,None,false).await
}
pub async fn serve_browser(dir:&Path,grants:&Path,grant_id:&str,reader:bool) -> Result<(),Box<dyn Error>> {
    let stdout=rustix::fs::fstat(io::stdout()).map_err(|_|problem("browser control stdout unavailable"))?;
    if rustix::fs::FileType::from_raw_mode(stdout.st_mode)!=rustix::fs::FileType::Fifo {
        return Err(problem("browser lifecycle requires private piped stdout").into());
    }
    serve_inner(dir,grants,true,Some(grant_id),reader).await
}

async fn serve_inner(dir:&Path,grants:&Path,legacy:bool,grant_id:Option<&str>,reader:bool) -> Result<(),Box<dyn Error>> {
    use host::browser_session::{self,Browser};
    require_pipe()?;
    let grants_meta=std::fs::symlink_metadata(grants).map_err(|_|problem("private grants unavailable"))?;
    if !grants_meta.is_file() || grants_meta.mode()&0o077!=0 || grants_meta.nlink()!=1 {
        return Err(problem("expected a private regular grant file").into());
    }
    let lease=GuardedExclusive::acquire(dir)?;
    let archive=lease.archive().clone();
    let plugins=Plugins::load(grants).map_err(|_|problem("private grants unavailable"))?;
    let store=Store::open_live(archive.clone()).map_err(|_|problem("guarded archive unavailable"))?;
    let listener=tokio::net::TcpListener::bind(SocketAddrV4::new(Ipv4Addr::LOCALHOST,0)).await?;
    let SocketAddr::V4(address)=listener.local_addr()? else {return Err(problem("invalid guarded address").into());};
    let gate=Gate::default();
    let browser=grant_id.map(|id|plugins.browser_principal(id).map(Browser::new)
        .ok_or_else(||problem("browser requires an explicit opted-in exact grant"))).transpose()?;
    let app=match &browser {
        Some(auth)=>browser_session::router(address,archive.clone(),store,plugins,gate.clone(),auth.clone(),reader),
        None=>host::guarded_router(address,archive.clone(),store,plugins,gate.clone(),legacy),
    }.map_err(problem)?;
    let (send,mut receive)=tokio::sync::mpsc::channel(1);
    std::thread::spawn(move || {
        let mut reader=io::stdin().lock();
        loop {
            match line(&mut reader) {
                Ok(Some(bytes)) => {
                    let decoded=serde_json::from_slice::<Control>(&bytes).map_err(|_|problem("invalid guarded control frame"));
                    if send.blocking_send(decoded).is_err() {break;}
                }
                Ok(None) => break,
                Err(error) => {let _=send.blocking_send(Err(error));break;}
            }
        }
    });
    let mode=if reader {"guarded-browser-reader-read-only"} else if browser.is_some() {"guarded-browser-read-only"}
        else if legacy {"guarded-legacy-read-only"} else {"guarded-read-only"};
    let mut ready=json!({"event":"ready","origin":format!("http://{address}"),"mode":mode,"api_version":1,"locked":true});
    if reader { ready["reader_path"]=json!(host::reader_assets::INDEX); }
    report(ready)?;
    let http=std::future::IntoFuture::into_future(axum::serve(listener,app));
    tokio::pin!(http);
    let mut attempts=Attempts::default();
    let result = loop {
        tokio::select! {
            outcome=&mut http => break outcome,
            frame=receive.recv() => {
                let command=match frame {
                    None => break Ok(()),
                    Some(Ok(command)) => command,
                    Some(Err(error)) => break Err(error),
                };
                match &command {
                    Control::Stop => break Ok(()),
                    Control::Lock => {
                        gate.lock();
                        if let Some(auth)=&browser {auth.revoke();}
                        report(json!({"event":"locked"}))?;
                    }
                    Control::IssueBrowserTicket => {
                        let Some(auth)=&browser else {break Err(problem("browser control is unavailable"));};
                        let ticket=gate.generation().and_then(|generation|auth.issue(generation,Instant::now()));
                        if let Some(ticket)=ticket {
                            // This is intentionally the private parent pipe only.
                            let mut output=io::stdout().lock();
                            writeln!(output,"{{\"event\":\"browser_ticket\",\"ticket\":\"{}\",\"expires_in\":{}}}",
                                *ticket,browser_session::TICKET_TTL.as_secs())?;
                            output.flush()?;
                        } else { report(json!({"event":"browser_ticket_refused"}))?; }
                    }
                    Control::BrowserSessionStatus => {
                        if let Some(status)=browser.as_ref().and_then(|auth|auth.status(&gate)) {
                            report(json!({"event":"browser_session_status","status":status}))?;
                        } else {
                            report(json!({"event":"browser_session_status_unavailable"}))?;
                        }
                    }
                    Control::RevokeBrowser => {
                        let Some(auth)=&browser else {break Err(problem("browser control is unavailable"));};
                        auth.revoke();
                        report(json!({"event":"browser_revoked"}))?;
                    }
                    Control::Unlock{passphrase} => {
                        // Always revoke the current generation before a new unlock attempt.
                        gate.lock();
                        if let Some(auth)=&browser {auth.revoke();}
                        if attempts.blocked(Instant::now()) {
                            report(json!({"event":"unlock_refused","reason":"rate_limited"}))?;
                            continue;
                        }
                        let password=Zeroizing::new(passphrase.clone());
                        let owner=archive.clone();
                        let ok=tokio::task::spawn_blocking(move || owner.verify_guarded_password(&password))
                            .await.ok().and_then(Result::ok).unwrap_or(false);
                        if ok && gate.unlock() {
                            attempts.clear();
                            report(json!({"event":"unlocked"}))?;
                        } else {
                            attempts.failed(Instant::now());
                            report(json!({"event":"unlock_refused","reason":"invalid_or_unavailable"}))?;
                        }
                    }
                }
            }
        }
    };
    gate.lock();
    if let Some(auth)=&browser {auth.revoke();}
    result?;
    report(json!({"event":"stopped"}))?;
    Ok(())
}
