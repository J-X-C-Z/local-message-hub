//! Exact-file distribution folder staging and verification for trusted local release binaries.
use serde::{Deserialize,Serialize};
use sha2::{Digest,Sha256};
use std::{
    ffi::{OsStr,OsString},
    fs::{self,File},
    io::{self,Read,Seek,SeekFrom,Write},
    path::{Component,Path,PathBuf},
};

const MANIFEST: &str = "package-manifest.json";
const README: &str = "README.md";
const FORMAT: &str = "lmh-portable-core-v1";
const MAX_BINARY: u64 = 256*1024*1024;
const MAX_DOC: u64 = 128*1024;
const MAX_MANIFEST: u64 = 4096;

#[derive(Debug,thiserror::Error)]
pub enum Error {
    #[error("unsupported package platform or architecture")]
    Platform,
    #[error("invalid package path, arguments or layout")]
    Path,
    #[error("release binary does not match the requested native executable format")]
    Binary,
    #[error("invalid package manifest or SHA-256 mismatch")]
    Integrity,
    #[error("package operation failed")]
    Io,
    #[error("destination exists or exclusive native publication failed")]
    Publish,
}
impl From<io::Error> for Error { fn from(_:io::Error)->Self {Self::Io} }

#[derive(Debug,Clone,Copy,PartialEq,Eq,Serialize,Deserialize)]
#[serde(rename_all="kebab-case")]
enum Target { LinuxX64, LinuxArm64, MacosX64, MacosArm64, WindowsX64 }
impl Target {
    fn native()->Result<Self,Error> {
        if cfg!(all(target_os="linux",target_arch="x86_64")) {Ok(Self::LinuxX64)}
        else if cfg!(all(target_os="linux",target_arch="aarch64")) {Ok(Self::LinuxArm64)}
        else if cfg!(all(target_os="macos",target_arch="x86_64")) {Ok(Self::MacosX64)}
        else if cfg!(all(target_os="macos",target_arch="aarch64")) {Ok(Self::MacosArm64)}
        else if cfg!(all(target_os="windows",target_arch="x86_64")) {Ok(Self::WindowsX64)}
        else {Err(Error::Platform)}
    }
    fn binary(self)->&'static str {
        if self==Self::WindowsX64 {"local-message-hub-rs.exe"} else {"local-message-hub-rs"}
    }
    fn wrapper(self)->Option<(&'static str,&'static [u8],bool)> {
        match self {
            Self::WindowsX64=>Some(("run-core.ps1",include_bytes!("../packaging/windows/run-core.ps1"),false)),
            Self::MacosX64|Self::MacosArm64=>Some(("run-core.sh",include_bytes!("../packaging/macos/run-core.sh"),true)),
            Self::LinuxX64|Self::LinuxArm64=>None,
        }
    }
}

#[derive(Serialize,Deserialize)]
#[serde(deny_unknown_fields)]
struct Item { path:String, bytes:u64, sha256:String }
#[derive(Serialize,Deserialize)]
#[serde(deny_unknown_fields)]
struct Manifest { format:String, target:Target, source_revision:String, files:Vec<Item> }

#[derive(Serialize)]
pub struct Receipt { target:Target, source_revision:String, manifest_sha256:String, binary_sha256:String, files:usize }

fn lower_hex(value:&str,len:usize)->bool {
    value.len()==len && value.bytes().all(|b|b.is_ascii_digit()||(b'a'..=b'f').contains(&b))
}
fn digest(bytes:&[u8])->String { hex::encode(Sha256::digest(bytes)) }

fn leaf(name:&str)->bool {
    let stem=name.split('.').next().unwrap_or_default().to_ascii_lowercase();
    (1..=120).contains(&name.len()) && name.as_bytes()[0].is_ascii_alphanumeric()
        && !name.ends_with('.')
        && name.bytes().all(|b|b.is_ascii_alphanumeric()||matches!(b,b'-'|b'_'|b'.'))
        && !matches!(stem.as_str(),"con"|"prn"|"aux"|"nul"|"com1"|"com2"|"com3"|"com4"|"com5"
            |"com6"|"com7"|"com8"|"com9"|"lpt1"|"lpt2"|"lpt3"|"lpt4"|"lpt5"|"lpt6"|"lpt7"|"lpt8"|"lpt9")
}

fn regular(path:&Path,directory:bool)->Result<fs::Metadata,Error> {
    let meta=fs::symlink_metadata(path).map_err(|_|Error::Path)?;
    if meta.file_type().is_symlink() || if directory {!meta.is_dir()} else {!meta.is_file()} {
        return Err(Error::Path);
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        if meta.file_attributes()&0x400!=0 {return Err(Error::Path);}
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        if !directory && meta.nlink()!=1 {return Err(Error::Path);}
    }
    Ok(meta)
}

fn existing(path:&Path,directory:bool)->Result<fs::Metadata,Error> {
    if !path.is_absolute() {return Err(Error::Path);}
    let mut components=path.components().peekable();
    let mut cursor=PathBuf::new();
    #[cfg(windows)]
    {
        let Some(Component::Prefix(prefix))=components.next() else {return Err(Error::Path);};
        let std::path::Prefix::Disk(drive)=prefix.kind() else {return Err(Error::Path);};
        if !windows_move::fixed(drive) {return Err(Error::Path);}
        cursor.push(prefix.as_os_str());
    }
    if components.next()!=Some(Component::RootDir) {return Err(Error::Path);}
    cursor.push(std::path::MAIN_SEPARATOR_STR);
    while let Some(Component::Normal(part))=components.next() {
        #[cfg(windows)]
        {
            let value=part.to_str().ok_or(Error::Path)?;
            if value.contains(':')||value.ends_with('.')||value.ends_with(' ')
                ||value.chars().any(char::is_control) {return Err(Error::Path);}
        }
        cursor.push(part);
        if components.peek().is_none() {return regular(&cursor,directory);}
        regular(&cursor,true)?;
    }
    Err(Error::Path)
}

fn private_parent(parent:&Path)->Result<(),Error> {
    let meta=existing(parent,true)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        if meta.mode()&0o022!=0 {return Err(Error::Path);}
    }
    #[cfg(not(unix))]
    let _=meta;
    Ok(())
}

fn stamp_file(path:&Path,max:u64,mut into:impl Write)->Result<(u64,String),Error> {
    let meta=regular(path,false)?;
    if meta.len()>max {return Err(Error::Integrity);}
    let mut source=File::open(path)?;
    if source.metadata()?.len()!=meta.len() {return Err(Error::Integrity);}
    let mut reader=(&mut source).take(max+1);
    let mut hash=Sha256::new();
    let mut sent=0_u64;
    let mut buffer=[0_u8;64*1024];
    loop {
        let len=reader.read(&mut buffer)?;
        if len==0 {break;}
        sent+=len as u64;
        if sent>max {return Err(Error::Integrity);}
        hash.update(&buffer[..len]);
        into.write_all(&buffer[..len])?;
    }
    if sent!=meta.len() || source.metadata()?.len()!=meta.len() {return Err(Error::Integrity);}
    Ok((sent,hex::encode(hash.finalize())))
}

fn binary_header(path:&Path,target:Target)->Result<(),Error> {
    let mut file=File::open(path)?;
    let length=file.metadata()?.len();
    let mut first=[0_u8;64];
    file.read_exact(&mut first).map_err(|_|Error::Binary)?;
    let u16_at=|i|u16::from_le_bytes([first[i],first[i+1]]);
    let u32_at=|i|u32::from_le_bytes([first[i],first[i+1],first[i+2],first[i+3]]);
    let pass=match target {
        Target::LinuxX64|Target::LinuxArm64=>{
            let machine=if target==Target::LinuxX64 {62} else {183};
            &first[..7]==b"\x7fELF\x02\x01\x01" && matches!(u16_at(16),2|3) && u16_at(18)==machine
                && u32_at(20)==1 && u16_at(52)==64
        }
        Target::MacosX64|Target::MacosArm64=>{
            let cpu=if target==Target::MacosX64 {0x0100_0007} else {0x0100_000c};
            u32_at(0)==0xfeed_facf && u32_at(4)==cpu && u32_at(12)==2
        }
        Target::WindowsX64=>{
            let offset=u64::from(u32_at(60));
            if &first[..2]!=b"MZ" || !(64..=1024*1024).contains(&offset) || offset+26>length {
                return Err(Error::Binary);
            }
            file.seek(SeekFrom::Start(offset))?;
            let mut pe=[0_u8;26];
            file.read_exact(&mut pe).map_err(|_|Error::Binary)?;
            let optional=u16::from_le_bytes([pe[20],pe[21]]);
            let flags=u16::from_le_bytes([pe[22],pe[23]]);
            &pe[..4]==b"PE\0\0" && pe[4..6]==[0x64,0x86] && optional>=2
                && offset+24+u64::from(optional)<=length && flags&0x2002==2
                && pe[24..26]==[0x0b,2]
        }
    };
    if pass {Ok(())} else {Err(Error::Binary)}
}

fn direct_children(path:&Path,expected:&[&str])->Result<(),Error> {
    let mut actual=Vec::with_capacity(expected.len());
    for entry in fs::read_dir(path)? {
        if actual.len()==expected.len() {return Err(Error::Path);}
        actual.push(entry?.file_name().into_string().map_err(|_|Error::Path)?);
    }
    if actual.len()!=expected.len() || !actual.iter().all(|name|expected.contains(&name.as_str())) {
        return Err(Error::Path);
    }
    Ok(())
}

fn write_leaf(path:&Path,executable:bool)->Result<File,Error> {
    let f=File::options().write(true).create_new(true).open(path)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        f.set_permissions(fs::Permissions::from_mode(if executable {0o755} else {0o644}))?;
    }
    #[cfg(not(unix))]
    let _=executable;
    Ok(f)
}
fn write_fixed(root:&Path,rel:&str,bytes:&[u8],execute:bool)->Result<Item,Error> {
    let mut file=write_leaf(&root.join(rel),execute)?;
    file.write_all(bytes)?;
    file.sync_all()?;
    Ok(Item{path:rel.into(),bytes:bytes.len() as u64,sha256:digest(bytes)})
}

fn inventory(target:Target)->Vec<(String,u64,bool)> {
    let mut paths=vec![(README.into(),MAX_DOC,false),(format!("bin/{}",target.binary()),MAX_BINARY,true)];
    if let Some((name,_,executable))=target.wrapper() {paths.push((name.into(),MAX_DOC,executable));}
    paths
}

/// Stage a native release whose binary digest is already known and trusted.
pub fn stage(binary:&Path,destination:&Path,expected_binary:&str,source_revision:&str)->Result<Receipt,Error> {
    let target=Target::native()?;
    if !lower_hex(expected_binary,64) || !lower_hex(source_revision,40) {return Err(Error::Integrity);}
    existing(binary,false)?;
    let parent=destination.parent().ok_or(Error::Path)?;
    if !destination.file_name().and_then(OsStr::to_str).is_some_and(leaf) {return Err(Error::Path);}
    private_parent(parent)?;
    if !matches!(fs::symlink_metadata(destination),Err(e) if e.kind()==io::ErrorKind::NotFound) {
        return Err(Error::Publish);
    }
    let stage=tempfile::Builder::new().prefix(".lmh-portable-stage-").tempdir_in(parent)?;
    let root=stage.path();
    fs::create_dir(root.join("bin"))?;
    let relative=format!("bin/{}",target.binary());
    let copy=root.join(&relative);
    let mut output=write_leaf(&copy,true)?;
    let (bytes,sha256)=stamp_file(binary,MAX_BINARY,&mut output)?;
    if sha256!=expected_binary {return Err(Error::Integrity);}
    output.sync_all()?;
    drop(output);
    binary_header(&copy,target)?;
    let mut files=vec![
        write_fixed(root,README,include_bytes!("../packaging/VERIFIED-README.md"),false)?,
        Item{path:relative,bytes,sha256},
    ];
    if let Some((name,bytes,executable))=target.wrapper() {
        files.push(write_fixed(root,name,bytes,executable)?);
    }
    let manifest=Manifest{format:FORMAT.into(),target,source_revision:source_revision.into(),files};
    let mut manifest_bytes=serde_json::to_vec(&manifest).map_err(|_|Error::Integrity)?;
    manifest_bytes.push(b'\n');
    let _=write_fixed(root,MANIFEST,&manifest_bytes,false)?;
    let receipt=verify(root,&digest(&manifest_bytes))?;
    // No check-then-plain-rename fallback: unsupported filesystems fail closed.
    publish(root,destination)?;
    let _=stage.keep();
    Ok(receipt)
}

/// Verify the exact extracted tree using a manifest pin acquired independently.
pub fn verify(root:&Path,expected_manifest:&str)->Result<Receipt,Error> {
    let target=Target::native()?;
    if !lower_hex(expected_manifest,64) {return Err(Error::Integrity);}
    existing(root,true)?;
    let mut names=vec![README,"bin",MANIFEST];
    if let Some((name,_,_))=target.wrapper() {names.push(name);}
    direct_children(root,&names)?;
    regular(&root.join("bin"),true)?;
    direct_children(&root.join("bin"),&[target.binary()])?;
    let mut data=Vec::new();
    let (_,manifest_sha256)=stamp_file(&root.join(MANIFEST),MAX_MANIFEST,&mut data)?;
    if manifest_sha256!=expected_manifest {return Err(Error::Integrity);}
    let manifest:Manifest=serde_json::from_slice(&data).map_err(|_|Error::Integrity)?;
    if manifest.target!=target {return Err(Error::Platform);}
    let expected=inventory(target);
    if manifest.format!=FORMAT || !lower_hex(&manifest.source_revision,40) || manifest.files.len()!=expected.len() {
        return Err(Error::Integrity);
    }
    for (entry,(rel,max,executable)) in manifest.files.iter().zip(expected) {
        if entry.path!=rel || entry.bytes>max || !lower_hex(&entry.sha256,64) {return Err(Error::Integrity);}
        let path=root.join(rel);
        #[cfg(unix)]
        {
            use std::os::unix::fs::MetadataExt;
            let mode=regular(&path,false)?.mode();
            if mode&0o7022!=0 || (mode&0o100!=0)!=executable {return Err(Error::Integrity);}
        }
        #[cfg(not(unix))]
        let _=executable;
        let (bytes,sha)=stamp_file(&path,max,io::sink())?;
        if bytes!=entry.bytes || sha!=entry.sha256 {return Err(Error::Integrity);}
    }
    binary_header(&root.join("bin").join(target.binary()),target)?;
    Ok(Receipt {target,source_revision:manifest.source_revision,manifest_sha256,
        binary_sha256:manifest.files[1].sha256.clone(),files:manifest.files.len()})
}

#[cfg(target_os="linux")]
fn publish(from:&Path,to:&Path)->Result<(),Error> {
    use rustix::fs::{CWD,RenameFlags,renameat_with};
    renameat_with(CWD,from,CWD,to,RenameFlags::NOREPLACE).map_err(|_|Error::Publish)
}
#[cfg(target_os="macos")]
fn publish(from:&Path,to:&Path)->Result<(),Error> {
    use std::{ffi::{CString,c_char,c_int,c_uint},os::unix::ffi::OsStrExt};
    const RENAME_EXCL:c_uint=0x0000_0004;
    unsafe extern "C" { fn renamex_np(from:*const c_char,to:*const c_char,flags:c_uint)->c_int; }
    let from=CString::new(from.as_os_str().as_bytes()).map_err(|_|Error::Path)?;
    let to=CString::new(to.as_os_str().as_bytes()).map_err(|_|Error::Path)?;
    // The C strings live through renamex_np and RENAME_EXCL refuses replacement.
    if unsafe {renamex_np(from.as_ptr(),to.as_ptr(),RENAME_EXCL)}==0 {Ok(())}
    else {Err(Error::Publish)}
}
#[cfg(windows)]
mod windows_move {
    use super::*;
    use std::os::windows::ffi::OsStrExt;
    #[link(name="kernel32")]
    unsafe extern "system" {
        #[link_name="MoveFileW"] fn move_file(from:*const u16,to:*const u16)->i32;
        #[link_name="GetDriveTypeW"] fn drive_type(root:*const u16)->u32;
    }
    pub(super) fn fixed(drive:u8)->bool {
        let drive=[u16::from(drive),u16::from(b':'),u16::from(b'\\'),0];
        // The root drive string is null-terminated and lives through the call.
        unsafe {drive_type(drive.as_ptr())==3}
    }
    fn wide(path:&Path)->Result<Vec<u16>,Error> {
        let mut out:Vec<u16>=path.as_os_str().encode_wide().collect();
        if out.contains(&0) {return Err(Error::Path);}
        out.push(0);
        Ok(out)
    }
    pub(super) fn publish(from:&Path,to:&Path)->Result<(),Error> {
        let (from,to)=(wide(from)?,wide(to)?);
        // Same-parent local directory move; MoveFileW refuses an existing target.
        if unsafe {move_file(from.as_ptr(),to.as_ptr())}!=0 {Ok(())} else {Err(Error::Publish)}
    }
}
#[cfg(windows)]
use windows_move::publish;
#[cfg(not(any(windows,target_os="linux",target_os="macos")))]
fn publish(_from:&Path,_to:&Path)->Result<(),Error>{Err(Error::Platform)}

/// Narrow entry point shared by the Hub CLI; reports digests, not host paths.
pub fn command(name:&str, mut args:impl Iterator<Item=OsString>)->Result<(),Error> {
    let mut next=||args.next().ok_or(Error::Path);
    let receipt=match name {
        "stage-portable-core"=>{
            let binary=PathBuf::from(next()?);
            let destination=PathBuf::from(next()?);
            let hash=next()?.into_string().map_err(|_|Error::Integrity)?;
            let revision=next()?.into_string().map_err(|_|Error::Integrity)?;
            if args.next().is_some(){return Err(Error::Path);}
            stage(&binary,&destination,&hash,&revision)?
        }
        "verify-portable-core"=>{
            let root=PathBuf::from(next()?);
            let hash=next()?.into_string().map_err(|_|Error::Integrity)?;
            if args.next().is_some(){return Err(Error::Path);}
            verify(&root,&hash)?
        }
        _=>return Err(Error::Path),
    };
    println!("{}",serde_json::to_string(&receipt).map_err(|_|Error::Integrity)?);
    Ok(())
}
