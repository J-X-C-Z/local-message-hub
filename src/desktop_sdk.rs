//! Local Rust desktop onboarding. Does not spawn a process or send requests.
use crate::plugins::{AccountScope, Permission, PluginKind, Plugins};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    ffi::{OsStr, OsString},
    fs, io::Write,
    net::{Ipv4Addr, SocketAddrV4},
    path::{Component, Path, PathBuf},
};
use zeroize::Zeroizing;

const MAX_READY: usize = 1024;
const API_VERSION: u8 = 1;

#[derive(Debug,thiserror::Error)]
pub enum Error {
    #[error("unsupported desktop supervisor platform or mode")]
    Unsupported,
    #[error("invalid local desktop path")]
    Path,
    #[error("invalid desktop grant")]
    Grant,
    #[error("desktop grant entropy unavailable")]
    Random,
    #[error("desktop grant file could not be published")]
    Publish,
    #[error("invalid desktop supervisor readiness")]
    Readiness,
}

#[derive(Debug,Clone,Copy,PartialEq,Eq)]
pub enum Platform { Linux, MacOs, Windows }
impl Platform {
    pub fn current() -> Option<Self> {
        if cfg!(target_os="linux") { Some(Self::Linux) }
        else if cfg!(target_os="macos") { Some(Self::MacOs) }
        else if cfg!(target_os="windows") { Some(Self::Windows) }
        else { None }
    }

    pub fn supports(self, mode: Mode) -> bool {
        mode == Mode::Snapshot || self == Self::Linux
            || (matches!(self, Self::MacOs | Self::Windows) && mode == Mode::NativeLive)
    }
}

#[derive(Debug,Clone,Copy,PartialEq,Eq)]
pub enum Mode { Snapshot, NativeLive, ImportedMedia }
impl Mode {
    fn ready_name(self) -> &'static str {
        match self {
            Self::Snapshot => "snapshot-read-only",
            Self::NativeLive => "native-live",
            Self::ImportedMedia => "imported-media-read-only",
        }
    }

    fn archive_option(self) -> &'static str {
        match self { Self::Snapshot=>"--snapshot",Self::NativeLive=>"--live",
            Self::ImportedMedia=>"--media-bundle" }
    }

    fn archive_is_directory(self) -> bool { self != Self::Snapshot }
}

/// A validated supervisor invocation for the current OS. Paths are operator-
/// supplied and must live in an already trusted tree. Not an executable check,
/// a signature verification, or a substitute for ACLs.
pub struct Launch {
    executable: PathBuf,
    args: [OsString;5],
    mode: Mode,
}
impl Launch {
    pub fn new(executable: &Path, mode: Mode, archive: &Path, grants: &Path) -> Result<Self,Error> {
        let platform=Platform::current().ok_or(Error::Unsupported)?;
        if !platform.supports(mode) { return Err(Error::Unsupported); }
        checked_path(executable,false)?;
        checked_path(archive,mode.archive_is_directory())?;
        let grant_meta=checked_path(grants,false)?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::MetadataExt;
            if grant_meta.mode()&0o077 != 0 || grant_meta.nlink()!=1 { return Err(Error::Path); }
        }
        #[cfg(not(unix))]
        let _=grant_meta;
        Ok(Self { executable:executable.to_owned(), mode,
            args:["desktop-supervise".into(),mode.archive_option().into(),
                archive.as_os_str().to_owned(),"--grants".into(),grants.as_os_str().to_owned()] })
    }

    pub fn executable(&self) -> &Path { &self.executable }
    pub fn args(&self) -> impl ExactSizeIterator<Item=&OsStr> {
        self.args.iter().map(OsString::as_os_str)
    }

    /// Validate one LF-terminated frame read from *this* trusted supervisor.
    /// The caller is responsible for enforcing a deadline while reading it.
    pub fn accept_ready(&self, line: &[u8]) -> Result<Connection,Error> {
        Connection::from_ready(self.mode,line)
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Ready { event:String, origin:String, api_version:u8, mode:String, backend_pid:u32 }

#[derive(Debug)]
pub struct Connection { address:SocketAddrV4, backend_pid:u32, mode:Mode }
impl Connection {
    fn from_ready(mode:Mode,line:&[u8]) -> Result<Self,Error> {
        let payload=line.strip_suffix(b"\n").ok_or(Error::Readiness)?;
        if line.len()>MAX_READY || payload.contains(&b'\n') || payload.contains(&b'\r') {
            return Err(Error::Readiness);
        }
        let frame:Ready=serde_json::from_slice(payload).map_err(|_|Error::Readiness)?;
        let address:SocketAddrV4=frame.origin.strip_prefix("http://")
            .and_then(|origin|origin.parse().ok()).ok_or(Error::Readiness)?;
        if frame.event!="ready" || frame.api_version!=API_VERSION || frame.mode!=mode.ready_name()
            || frame.backend_pid==0 || *address.ip()!=Ipv4Addr::LOCALHOST || address.port()==0
            || frame.origin!=format!("http://{address}") {
            return Err(Error::Readiness);
        }
        Ok(Self{address,backend_pid:frame.backend_pid,mode})
    }

    pub fn address(&self) -> SocketAddrV4 { self.address }
    pub fn backend_pid(&self) -> u32 { self.backend_pid }
    pub fn mode(&self) -> Mode { self.mode }

    /// A closed endpoint vocabulary. This does not evaluate the bearer's permissions.
    pub fn url(&self, endpoint:Endpoint) -> Result<String,Error> {
        let path=endpoint.path(self.mode)?;
        Ok(format!("http://{}{path}",self.address))
    }
}

#[derive(Clone,Copy,Debug)]
pub enum Endpoint {
    Health, Manifest, OpenApi, Conversations, Messages, Search, MessageContext,
    QuotedMessageContext, Export, Media, Preferences,
}
impl Endpoint {
    fn path(self,mode:Mode) -> Result<&'static str,Error> {
        Ok(match self {
            Self::Health=>"/healthz",
            Self::Manifest=>"/api/v1/manifest",
            Self::OpenApi=>"/api/v1/openapi.json",
            Self::Conversations=>"/api/v1/conversations",
            Self::Messages=>"/api/v1/messages",
            Self::Search if mode!=Mode::ImportedMedia=>"/api/v1/search",
            Self::MessageContext if mode!=Mode::ImportedMedia=>"/api/v1/messages/context",
            Self::QuotedMessageContext if mode!=Mode::ImportedMedia=>"/api/v1/messages/quoted-context",
            Self::Export=>"/api/v1/messages/export",
            Self::Media if mode==Mode::ImportedMedia=>"/api/v1/messages/media",
            Self::Preferences if mode==Mode::NativeLive=>"/api/v1/client/preferences",
            _=>return Err(Error::Unsupported),
        })
    }
}

/// Fixed optional capabilities. Basic conversation/message reads are always
/// granted. Write, provider, legacy, notification and ingestion grants are not produced.
#[derive(Default,Clone,Copy)]
pub struct GrantOptions {
    pub export_text:bool,
    pub imported_media:bool,
    pub client_preferences:bool,
}

#[derive(Serialize)]
struct Grant<'a> {
    id:&'a str, kind:PluginKind, permissions:Vec<Permission>,
    account_scope:&'a AccountScope, token_sha256:String,
}
#[derive(Serialize)]
struct Config<'a> { version:u8, grants:[Grant<'a>;1] }

/// The token is not printable or serializable. Keep it with the authorized
/// desktop process and hand it to an HTTP client only for this managed origin.
pub struct DesktopGrant { token:Zeroizing<String>, config:Vec<u8> }
impl DesktopGrant {
    pub fn generate(mode:Mode,id:&str,scope:&AccountScope,opts:GrantOptions) -> Result<Self,Error> {
        let platform=Platform::current().ok_or(Error::Unsupported)?;
        if !platform.supports(mode) || (opts.imported_media && mode!=Mode::ImportedMedia)
            || (opts.client_preferences && mode!=Mode::NativeLive) {
            return Err(Error::Unsupported);
        }
        if opts.imported_media && !matches!(scope.platform.as_str(),"qq"|"wechat") {
            return Err(Error::Grant);
        }
        let mut random=Zeroizing::new([0_u8;32]);
        getrandom::fill(&mut random[..]).map_err(|_|Error::Random)?;
        let token=Zeroizing::new(hex::encode(&random[..]));
        let token_sha256=hex::encode(Sha256::digest(token.as_bytes()));
        let mut permissions=vec![Permission::Conversations,Permission::Messages];
        if opts.export_text { permissions.push(Permission::MessageExport); }
        if opts.imported_media { permissions.push(Permission::Media); }
        if opts.client_preferences { permissions.push(Permission::ClientPreferences); }
        let config=serde_json::to_vec(&Config{version:1,grants:[Grant {
            id,kind:PluginKind::Desktop,permissions,account_scope:scope,token_sha256,
        }]}).map_err(|_|Error::Grant)?;
        Plugins::parse(&config).map_err(|_|Error::Grant)?;
        Ok(Self{token,config})
    }

    /// Publish a fresh hash-only file in a trusted private directory.
    /// No overwrite. On Windows the caller must configure and verify NTFS ACLs;
    /// Unix mode checks are not ACL audits either.
    pub fn write_new(&self, directory:&Path, filename:&str) -> Result<PathBuf,Error> {
        if !filename_valid(filename) { return Err(Error::Path); }
        let meta=checked_path(directory,true)?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            if meta.permissions().mode()&0o077!=0 { return Err(Error::Path); }
        }
        #[cfg(not(unix))]
        let _=meta;
        let mut stage=tempfile::Builder::new().prefix(".lmh-grant-")
            .tempfile_in(directory).map_err(|_|Error::Publish)?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            stage.as_file().set_permissions(fs::Permissions::from_mode(0o600))
                .map_err(|_|Error::Publish)?;
        }
        stage.write_all(&self.config).and_then(|_|stage.as_file().sync_all()).map_err(|_|Error::Publish)?;
        let target=directory.join(filename);
        stage.persist_noclobber(&target).map_err(|_|Error::Publish)?;
        Ok(target)
    }

    /// Borrow the token briefly. Do not log or persist it, pass it in an
    /// environment/argument, or allow the HTTP layer to forward it on redirects.
    pub fn with_token<R>(&self, use_token:impl FnOnce(&str)->R) -> R {
        use_token(self.token.as_str())
    }
}

fn filename_valid(name:&str) -> bool {
    let Some(stem)=name.strip_suffix(".json") else { return false; };
    (1..=64).contains(&stem.len())
        && stem.as_bytes()[0].is_ascii_lowercase()
        && stem.bytes().all(|b|b.is_ascii_lowercase() || b.is_ascii_digit() || matches!(b,b'-'|b'_'))
        && !matches!(stem,"con"|"prn"|"aux"|"nul"|"com1"|"com2"|"com3"|"com4"|"com5"|"com6"
            |"com7"|"com8"|"com9"|"lpt1"|"lpt2"|"lpt3"|"lpt4"|"lpt5"|"lpt6"|"lpt7"|"lpt8"|"lpt9")
}

fn checked_path(path:&Path, directory:bool) -> Result<fs::Metadata,Error> {
    if !path.is_absolute() { return Err(Error::Path); }
    let mut parts=path.components().peekable();
    let mut cursor=PathBuf::new();
    #[cfg(windows)]
    {
        use std::path::Prefix;
        let Some(Component::Prefix(prefix))=parts.next() else {return Err(Error::Path);};
        if !matches!(prefix.kind(),Prefix::Disk(_)) { return Err(Error::Path); }
        cursor.push(prefix.as_os_str());
    }
    let Some(Component::RootDir)=parts.next() else { return Err(Error::Path); };
    cursor.push(std::path::MAIN_SEPARATOR_STR);
    while let Some(part)=parts.next() {
        let Component::Normal(part)=part else { return Err(Error::Path); };
        #[cfg(windows)]
        {
            let text=part.to_str().ok_or(Error::Path)?;
            if text.contains(':') || text.ends_with('.') || text.ends_with(' ')
                || text.chars().any(char::is_control) { return Err(Error::Path); }
        }
        cursor.push(part);
        let meta=fs::symlink_metadata(&cursor).map_err(|_|Error::Path)?;
        if meta.file_type().is_symlink() { return Err(Error::Path); }
        #[cfg(windows)]
        {
            use std::os::windows::fs::MetadataExt;
            if meta.file_attributes()&0x400!=0 { return Err(Error::Path); }
        }
        if parts.peek().is_none() {
            return if (directory && meta.is_dir()) || (!directory && meta.is_file()) {
                Ok(meta)
            } else { Err(Error::Path) };
        }
        if !meta.is_dir() { return Err(Error::Path); }
    }
    Err(Error::Path)
}


#[cfg(test)]
mod managed_mode_tests {
    use super::*;
    #[test]
    fn ordinary_live_is_supported_but_windows_media_remains_refused() {
        for platform in [Platform::Linux, Platform::MacOs, Platform::Windows] {
            assert!(platform.supports(Mode::Snapshot));
            assert!(platform.supports(Mode::NativeLive));
        }
        assert!(Platform::Linux.supports(Mode::ImportedMedia));
        assert!(!Platform::Windows.supports(Mode::ImportedMedia));
        assert!(!Platform::MacOs.supports(Mode::ImportedMedia));
    }
}
