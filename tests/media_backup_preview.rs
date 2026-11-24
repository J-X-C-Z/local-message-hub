use rusqlite::Connection;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{fs, io::Write, path::{Path,PathBuf}, process::{Command, Output}};
use zip::{CompressionMethod, ZipWriter, write::SimpleFileOptions};

const QQ_PATH: &str = "qq-roaming/0123456789abcdefabcd/photo.png";
const QQ_BYTES: &[u8] = b"synthetic qq cache";
const WECHAT_BYTES: &[u8] = b"synthetic wechat cache";

fn wechat_key() -> String {
    hex::encode(Sha256::digest(b"owner\0room\0shard.db\x0014"))
}

fn invoke(path: &Path, temp: &Path) -> Output {
    Command::new(env!("CARGO_BIN_EXE_local-message-hub-rs"))
        .env("TMPDIR", temp).env("TEMP",temp).env("TMP",temp)
        .arg("inspect-media-backup").arg("--source").arg(path)
        .output().unwrap()
}

fn index_bytes(dir: &Path) -> Vec<u8> {
    let path = dir.join("input.sqlite");
    let db = Connection::open(&path).unwrap();
    db.execute_batch(include_str!("fixtures/index-v9.sql")).unwrap();
    db.execute("INSERT INTO messages(platform,account_id,id,conversation_id,type,extra) VALUES('qq','owner','qq1','room','image',?)",
        [json!({"roaming_resources":[{"path":QQ_PATH}]}).to_string()]).unwrap();
    db.execute("INSERT INTO messages(platform,account_id,id,conversation_id,type,raw_type,extra) VALUES('wechat','owner','room:shard.db:14','room','image','3',?)",
        [json!({"session_id":"room","local_id":14,"shard":"shard.db","local_type":3}).to_string()]).unwrap();
    db.close().unwrap();
    fs::read(path).unwrap()
}

fn descriptor(path: &str, bytes: &[u8]) -> Value {
    json!({"path":path,"size":bytes.len(),"sha256":hex::encode(Sha256::digest(bytes))})
}

fn manifest(media: Vec<Value>, size: usize) -> Value {
    json!({"format":"local-message-hub-media-backup","version":1,"created_at":1,"media_count":media.len(),
        "missing_media_count":2,"media_bytes":size,"media":media})
}

fn archive(path: &Path, index: &[u8], encoded: &[u8], members: &[(&str,&[u8])]) {
    let mut zip = ZipWriter::new(fs::File::create(path).unwrap());
    let deflated = SimpleFileOptions::default().compression_method(CompressionMethod::Deflated);
    let stored = SimpleFileOptions::default().compression_method(CompressionMethod::Stored);
    zip.start_file("index.sqlite3",deflated).unwrap();
    zip.write_all(index).unwrap();
    zip.start_file("manifest.json",stored).unwrap();
    zip.write_all(encoded).unwrap();
    for (name,bytes) in members {
        zip.start_file(*name,stored).unwrap();
        zip.write_all(bytes).unwrap();
    }
    zip.finish().unwrap();
}

fn invoke_clean(backup: &Path, scratch: &Path) -> Output {
    let before = fs::read(backup).unwrap();
    let out = invoke(backup,scratch);
    assert!(fs::read_dir(scratch).unwrap().next().is_none());
    assert_eq!(fs::read(backup).unwrap(),before);
    out
}

#[test]
fn previews_real_zip_bytes_without_extracting_media_or_emitting_cache_paths() {
    let dir = tempfile::tempdir().unwrap();
    let scratch = tempfile::tempdir().unwrap();
    let backup = dir.path().join("backup.zip");
    let index = index_bytes(dir.path());
    let qq = format!("media/{QQ_PATH}");
    let wechat = format!("media/wechat-media/{}/mid.jpg",wechat_key());
    archive(&backup,&index,&serde_json::to_vec(&manifest(vec![
        descriptor(&qq,QQ_BYTES),descriptor(&wechat,WECHAT_BYTES),
    ],QQ_BYTES.len()+WECHAT_BYTES.len())).unwrap(),&[(&qq,QQ_BYTES),(&wechat,WECHAT_BYTES)]);
    let result = invoke_clean(&backup,scratch.path());
    assert!(result.status.success(),"{}",String::from_utf8_lossy(&result.stderr));
    let receipt: Value = serde_json::from_slice(&result.stdout).unwrap();
    assert_eq!(receipt, json!({
        "archive_sha256":hex::encode(Sha256::digest(fs::read(&backup).unwrap())),
        "index_sha256":hex::encode(Sha256::digest(&index)),
        "verified_media_count":2,"verified_media_bytes":QQ_BYTES.len()+WECHAT_BYTES.len(),
        "referenced_qq_count":1,"referenced_wechat_count":1,"reported_missing_media_count":2
    }));
    let out = String::from_utf8(result.stdout).unwrap();
    assert!(!out.contains("photo.png") && !out.contains("mid.jpg") && !out.contains("synthetic"));
}

#[test]
fn rejects_mismatched_bytes_manifest_counts_and_valid_digest_without_index_reference() {
    let dir = tempfile::tempdir().unwrap();
    let scratch = tempfile::tempdir().unwrap();
    let backup = dir.path().join("backup.zip");
    let index = index_bytes(dir.path());
    let qq = format!("media/{QQ_PATH}");
    let orphan = format!("media/wechat-media/{}/mid.jpg","f".repeat(64));
    let mut wrong_sha = descriptor(&qq,QQ_BYTES);
    wrong_sha["sha256"] = json!("f".repeat(64));
    let mut wrong_size = descriptor(&qq,QQ_BYTES);
    wrong_size["size"] = json!(QQ_BYTES.len()+1);
    for (cases, input) in [
        (vec![wrong_sha],qq.as_str()),
        (vec![wrong_size],qq.as_str()),
        (vec![descriptor(&orphan,QQ_BYTES)],orphan.as_str()),
    ] {
        archive(&backup,&index,&serde_json::to_vec(&manifest(cases,QQ_BYTES.len())).unwrap(),&[(input,QQ_BYTES)]);
        let result=invoke_clean(&backup,scratch.path());
        assert!(!result.status.success());
        assert!(result.stdout.is_empty());
        assert!(!String::from_utf8_lossy(&result.stderr).contains("mid.jpg"));
    }
    let descriptor = descriptor(&qq,QQ_BYTES);
    archive(&backup,&index,&serde_json::to_vec(&manifest(vec![descriptor],1)).unwrap(),&[(&qq,QQ_BYTES)]);
    assert!(!invoke_clean(&backup,scratch.path()).status.success());
}

#[test]
fn rejects_duplicate_manifest_members_extra_members_and_over_budget_archive_without_output() {
    let dir = tempfile::tempdir().unwrap();
    let scratch = tempfile::tempdir().unwrap();
    let backup = dir.path().join("backup.zip");
    let index = index_bytes(dir.path());
    let qq = format!("media/{QQ_PATH}");
    let another = format!("media/wechat-media/{}/mid.jpg",wechat_key());
    let dupe = descriptor(&qq,QQ_BYTES);
    archive(&backup,&index,&serde_json::to_vec(&manifest(vec![dupe.clone(),dupe],2*QQ_BYTES.len())).unwrap(),&[(&qq,QQ_BYTES),(&another,QQ_BYTES)]);
    assert!(!invoke_clean(&backup,scratch.path()).status.success());
    let allowed = serde_json::to_vec(&manifest(vec![],0)).unwrap();
    archive(&backup,&index,&allowed,&[("unexpected.bin",b"synthetic")]);
    assert!(!invoke_clean(&backup,scratch.path()).status.success());
    let rejected = "media/qq-roaming/0123456789abcdefabcd/invalid:name";
    archive(&backup,&index,&allowed,&[(rejected,b"synthetic")]);
    assert!(!invoke_clean(&backup,scratch.path()).status.success());
    let file=fs::File::create(&backup).unwrap();
    file.set_len(256*1024*1024+1).unwrap();
    let output=invoke(&backup,scratch.path());
    assert!(!output.status.success());
    assert!(output.stdout.is_empty());
    assert!(String::from_utf8_lossy(&output.stderr).contains("exceeds preview limits"));
}

#[test]
#[ignore = "requires pinned stock hub.backups/module/WeChat provider source on PYTHONPATH"]
fn actual_pinned_python_generator_produces_a_verified_mixed_media_archive() {
    let dir = tempfile::tempdir().unwrap();
    let scratch = tempfile::tempdir().unwrap();
    let backup = dir.path().join("backup.zip");
    let script = r#"
import ast, hashlib, pathlib, sys, types
from hub.backups import create_media_backup
from hub.store import ChatStore
# Load only the pinned provider's pure cache-identity function, not its live
# integration imports or local provider APIs.
provider_path=next(pathlib.Path(root)/'integrations/wechat/provider.py'
    for root in sys.path if (pathlib.Path(root)/'integrations/wechat/provider.py').is_file())
parsed=ast.parse(provider_path.read_text())
helper=next(node for node in parsed.body if isinstance(node,ast.FunctionDef) and node.name=='media_cache_identity')
module=types.ModuleType('integrations.wechat.provider')
module.hashlib=hashlib
exec(compile(ast.Module(body=[helper],type_ignores=[]),str(provider_path),'exec'),module.__dict__)
sys.modules[module.__name__]=module

root=pathlib.Path(sys.argv[1])
out=pathlib.Path(sys.argv[2])
store=ChatStore(root/'hub.sqlite3')
relative='qq-roaming/0123456789abcdefabcd/photo.png'
qq=root/relative
qq.parent.mkdir(parents=True)
qq.write_bytes(b'synthetic qq cache')
store.ingest('qq','owner',[{'id':'room','title':'QQ fixture'}],
    [{'id':'qq1','conversation_id':'room','type':'image','timestamp':1,
      'roaming_resources':[{'path':relative}]}])
cache=root/'wechat-cache'
identity=module.media_cache_identity('owner','room','shard.db',14)
(cache/identity).mkdir(parents=True)
(cache/identity/'mid.jpg').write_bytes(b'synthetic wechat cache')
(cache/('f'*64)).mkdir()
(cache/('f'*64)/'orphan.jpg').write_bytes(b'excluded orphan')
store.ingest('wechat','owner',[{'id':'room','title':'WeChat fixture'}],
    [{'id':'room:shard.db:14','conversation_id':'room','type':'image','raw_type':'3','timestamp':2,
      'session_id':'room','shard':'shard.db','local_id':14,'local_type':3}])
manifest=create_media_backup(store,root,out,cache)
assert manifest['media_count']==2, manifest
store.close()
"#;
    let py = Command::new("python3").args(["-B","-c",script]).arg(dir.path()).arg(&backup).output().unwrap();
    assert!(py.status.success(),"{}",String::from_utf8_lossy(&py.stderr));
    let actual=invoke_clean(&backup,scratch.path());
    assert!(actual.status.success(),"{}",String::from_utf8_lossy(&actual.stderr));
    let report: Value=serde_json::from_slice(&actual.stdout).unwrap();
    assert_eq!(report["referenced_wechat_count"],1);
    assert_eq!(report["referenced_qq_count"],1);
    assert_eq!(report["verified_media_bytes"],QQ_BYTES.len()+WECHAT_BYTES.len());
    assert_eq!(report["reported_missing_media_count"],0);
    #[cfg(target_os = "linux")]
    {
        let parent=import_parent();
        let target=parent.path().join("fresh-from-pinned-python");
        let original_file=dir.path().join(QQ_PATH);
        let cache=dir.path().join("wechat-cache");
        let original_wechat=cache.join(wechat_key()).join("mid.jpg");
        let orphan=cache.join("f".repeat(64)).join("orphan.jpg");
        let snapshot=fs::read(&backup).unwrap();
        let imported=import_into(&backup,scratch.path(),&target);
        assert!(imported.status.success(),"{}",String::from_utf8_lossy(&imported.stderr));
        let receipt:Value=serde_json::from_slice(&imported.stdout).unwrap();
        assert_eq!(receipt["verified_media_count"],2);
        assert_eq!(receipt["unique_object_count"],2);
        check_objects(&target,&[QQ_BYTES,WECHAT_BYTES]);
        assert_media_roundtrip(&target, QQ_BYTES, WECHAT_BYTES);
        assert_eq!(fs::read(original_file).unwrap(),QQ_BYTES);
        assert_eq!(fs::read(original_wechat).unwrap(),WECHAT_BYTES);
        assert_eq!(fs::read(orphan).unwrap(),b"excluded orphan");
        assert_eq!(fs::read(&backup).unwrap(),snapshot);
        assert!(fs::read_dir(scratch.path()).unwrap().next().is_none());
    }
}

#[test]
fn rejects_a_malformed_local_header_or_corrupted_member_bytes() {
    let dir = tempfile::tempdir().unwrap();
    let scratch = tempfile::tempdir().unwrap();
    let backup = dir.path().join("backup.zip");
    let index = index_bytes(dir.path());
    let qq = format!("media/{QQ_PATH}");
    let doc = serde_json::to_vec(&manifest(vec![descriptor(&qq,QQ_BYTES)],QQ_BYTES.len())).unwrap();
    archive(&backup,&index,&doc,&[(&qq,QQ_BYTES)]);
    let original = fs::read(&backup).unwrap();
    let mut bad_name = original.clone();
    assert_eq!(&bad_name[30..30+"index.sqlite3".len()],b"index.sqlite3");
    bad_name[30] = b'x';
    fs::write(&backup,bad_name).unwrap();
    let result = invoke_clean(&backup,scratch.path());
    assert!(!result.status.success());
    assert!(result.stdout.is_empty());

    let mut bad_content = original;
    let offset = bad_content.windows(QQ_BYTES.len()).position(|window| window==QQ_BYTES).unwrap();
    bad_content[offset] ^= 1;
    fs::write(&backup,bad_content).unwrap();
    let result = invoke_clean(&backup,scratch.path());
    assert!(!result.status.success());
    assert!(result.stdout.is_empty());
}

#[test]
fn the_single_final_directory_offset_count_and_envelope_must_match() {
    let dir = tempfile::tempdir().unwrap();
    let scratch = tempfile::tempdir().unwrap();
    let backup = dir.path().join("backup.zip");
    let index = index_bytes(dir.path());
    let doc = serde_json::to_vec(&manifest(vec![],0)).unwrap();
    archive(&backup,&index,&doc,&[]);
    let valid = fs::read(&backup).unwrap();
    assert!(invoke_clean(&backup,scratch.path()).status.success());
    let end = valid.len()-22;
    let mut too_many = valid.clone();
    too_many[end+8..end+10].copy_from_slice(&4097u16.to_le_bytes());
    too_many[end+10..end+12].copy_from_slice(&4097u16.to_le_bytes());
    fs::write(&backup,too_many).unwrap();
    let failure = invoke_clean(&backup,scratch.path());
    assert!(!failure.status.success());
    assert!(String::from_utf8_lossy(&failure.stderr).contains("exceeds preview limits"));
    let mut wrong_offset = valid.clone();
    wrong_offset[end+16..end+20].copy_from_slice(&0u32.to_le_bytes());
    fs::write(&backup,wrong_offset).unwrap();
    assert!(!invoke_clean(&backup,scratch.path()).status.success());
    let mut extra = valid;
    extra.extend_from_slice(b"an extraneous tail");
    fs::write(&backup,extra).unwrap();
    assert!(!invoke_clean(&backup,scratch.path()).status.success());
}

#[cfg(target_os = "linux")]
fn import_into(path: &Path, temp: &Path, target: &Path) -> Output {
    Command::new(env!("CARGO_BIN_EXE_local-message-hub-rs"))
        .env("TMPDIR", temp).env("TEMP",temp).env("TMP",temp)
        .arg("import-media-backup").arg("--source").arg(path).arg("--directory").arg(target)
        .output().unwrap()
}

#[cfg(target_os = "linux")]
fn import_parent() -> tempfile::TempDir {
    use std::os::unix::fs::PermissionsExt;
    let result = tempfile::tempdir().unwrap();
    fs::set_permissions(result.path(),fs::Permissions::from_mode(0o700)).unwrap();
    result
}

#[cfg(target_os = "linux")]
fn check_objects(target: &Path, expected: &[&[u8]]) {
    assert_eq!(fs::read_dir(target.join("objects")).unwrap().count(),expected.len());
    for bytes in expected {
        let key = hex::encode(Sha256::digest(bytes));
        assert_eq!(fs::read(target.join("objects").join(key)).unwrap(),*bytes);
    }
}

#[cfg(target_os = "linux")]
#[test]
fn fresh_media_import_deduplicates_verified_blobs_and_projects_readable_index() {
    use local_message_hub_rs::store::{Query,Store};
    use std::os::unix::fs::PermissionsExt;
    let dir = tempfile::tempdir().unwrap();
    let scratch = tempfile::tempdir().unwrap();
    let parent = import_parent();
    let target = parent.path().join("fresh");
    let backup = dir.path().join("fixture.zip");
    let index = index_bytes(dir.path());
    let qq = format!("media/{QQ_PATH}");
    let wechat = format!("media/wechat-media/{}/mid.jpg",wechat_key());
    let shared = b"shared synthetic cache content";
    let encoded = serde_json::to_vec(&manifest(vec![descriptor(&qq,shared),descriptor(&wechat,shared)],2*shared.len())).unwrap();
    archive(&backup,&index,&encoded,&[(&qq,shared),(&wechat,shared)]);
    let before = fs::read(&backup).unwrap();
    let out = import_into(&backup,scratch.path(),&target);
    assert!(out.status.success(),"{}",String::from_utf8_lossy(&out.stderr));
    let receipt:Value = serde_json::from_slice(&out.stdout).unwrap();
    assert_eq!(receipt["unique_object_count"],1);
    assert_eq!(receipt["verified_media_count"],2);
    assert_eq!(receipt["parent_synced"],true);
    assert_eq!(receipt["archive_sha256"],hex::encode(Sha256::digest(&before)));
    assert_eq!(fs::read(&backup).unwrap(),before);
    check_objects(&target,&[shared]);
    let catalog = fs::read(target.join("catalog.json")).unwrap();
    let index = target.join("snapshot/index.sqlite3");
    assert_eq!(receipt["catalog_sha256"],hex::encode(Sha256::digest(&catalog)));
    assert_eq!(receipt["projected_index_sha256"],hex::encode(Sha256::digest(fs::read(&index).unwrap())));
    let metadata: Value = serde_json::from_slice(&catalog).unwrap();
    assert_eq!(metadata["items"].as_array().unwrap().len(),2);
    for (p,platform) in [(&qq,"qq"),(&wechat,"wechat")] {
        let key=hex::encode(Sha256::digest([b"lmh-legacy-cache-v1\0".as_slice(),p.as_bytes()].concat()));
        let item = metadata["items"].as_array().unwrap().iter().find(|row|row["ref_sha256"]==key).unwrap();
        assert_eq!(item["source"],platform);
        assert_eq!(item["blob_sha256"],hex::encode(Sha256::digest(shared)));
    }
    assert!(!String::from_utf8_lossy(&catalog).contains("photo.png"));
    assert_eq!(metadata["items"].as_array().unwrap().iter().find(|x|x["source"]=="wechat").unwrap()["wechat_leaf"],"mid.jpg");
    assert!(!String::from_utf8_lossy(&catalog).contains("wechat-media/"));
    let root = Store::open(&index).unwrap();
    let messages = root.messages(&Query{platform:"qq".into(),account_id:"owner".into(),conversation_id:"room".into(),..Default::default()}).unwrap();
    assert_eq!(messages["items"][0]["id"],"qq1");
    assert_eq!(fs::metadata(&target).unwrap().permissions().mode() & 0o077,0);
    assert_eq!(fs::metadata(target.join("catalog.json")).unwrap().permissions().mode() & 0o077,0);
    assert_eq!(fs::read_dir(parent.path()).unwrap().count(),1);
    assert!(fs::read_dir(scratch.path()).unwrap().next().is_none());
}

#[cfg(target_os = "linux")]
#[test]
fn concurrent_atomic_publication_does_not_replace_an_existing_target() {
    let dir = tempfile::tempdir().unwrap();
    let scratch = tempfile::tempdir().unwrap();
    let parent = import_parent();
    let target = parent.path().join("committed");
    let backup=dir.path().join("fixture.zip");
    let index=index_bytes(dir.path());
    let qq=format!("media/{QQ_PATH}");
    let encoded=serde_json::to_vec(&manifest(vec![descriptor(&qq,QQ_BYTES)],QQ_BYTES.len())).unwrap();
    archive(&backup,&index,&encoded,&[(&qq,QQ_BYTES)]);
    let barrier=std::sync::Barrier::new(2);
    let results=std::thread::scope(|scope|{
        let work=||{barrier.wait(); import_into(&backup,scratch.path(),&target)};
        let first=scope.spawn(work); let second=scope.spawn(work);
        [first.join().unwrap(), second.join().unwrap()]
    });
    assert_eq!(results.iter().filter(|r|r.status.success()).count(),1);
    let catalog=fs::read(target.join("catalog.json")).unwrap();
    let projected=fs::read(target.join("snapshot/index.sqlite3")).unwrap();
    assert!(!import_into(&backup,scratch.path(),&target).status.success());
    assert_eq!(fs::read(target.join("catalog.json")).unwrap(),catalog);
    assert_eq!(fs::read(target.join("snapshot/index.sqlite3")).unwrap(),projected);
    check_objects(&target,&[QQ_BYTES]);
    assert_eq!(fs::read_dir(parent.path()).unwrap().count(),1);
    assert!(fs::read_dir(scratch.path()).unwrap().next().is_none());
}

#[cfg(target_os = "linux")]
#[test]
fn late_index_reference_failure_and_size_cap_publish_nothing() {
    let dir = tempfile::tempdir().unwrap();
    let scratch = tempfile::tempdir().unwrap();
    let parent = import_parent();
    let target = parent.path().join("fresh");
    let existing=parent.path().join("existing");
    let backup=dir.path().join("fixture.zip");
    let index=index_bytes(dir.path());
    let qq=format!("media/{QQ_PATH}");
    let valid=serde_json::to_vec(&manifest(vec![descriptor(&qq,QQ_BYTES)],QQ_BYTES.len())).unwrap();
    archive(&backup,&index,&valid,&[(&qq,QQ_BYTES)]);
    assert!(import_into(&backup,scratch.path(),&existing).status.success());
    let existing_catalog=fs::read(existing.join("catalog.json")).unwrap();
    let existing_snapshot=fs::read(existing.join("snapshot/index.sqlite3")).unwrap();
    let foreign=format!("media/wechat-media/{}/mid.jpg","f".repeat(64));
    let encoded=serde_json::to_vec(&manifest(vec![descriptor(&qq,QQ_BYTES),descriptor(&foreign,WECHAT_BYTES)],QQ_BYTES.len()+WECHAT_BYTES.len())).unwrap();
    archive(&backup,&index,&encoded,&[(&qq,QQ_BYTES),(&foreign,WECHAT_BYTES)]);
    let before=fs::read(&backup).unwrap();
    let out=import_into(&backup,scratch.path(),&target);
    assert!(!out.status.success());
    assert!(String::from_utf8_lossy(&out.stderr).contains("not referenced by the index"));
    assert_eq!(fs::read(&backup).unwrap(),before);
    assert!(!target.exists());
    let too_large=fs::File::create(dir.path().join("over-limit.zip")).unwrap();
    too_large.set_len(256*1024*1024+1).unwrap();
    assert!(!import_into(&dir.path().join("over-limit.zip"),scratch.path(),&target).status.success());
    assert!(!target.exists());
    assert_eq!(fs::read(existing.join("catalog.json")).unwrap(),existing_catalog);
    assert_eq!(fs::read(existing.join("snapshot/index.sqlite3")).unwrap(),existing_snapshot);
    check_objects(&existing,&[QQ_BYTES]);
    assert_eq!(fs::read_dir(parent.path()).unwrap().count(),1);
    assert!(fs::read_dir(scratch.path()).unwrap().next().is_none());
}

#[cfg(target_os = "linux")]
#[test]
fn media_import_refuses_public_or_symlink_parent_and_preserves_target_symlink() {
    use std::os::unix::fs::{PermissionsExt,symlink};
    let dir=tempfile::tempdir().unwrap();
    let scratch=tempfile::tempdir().unwrap();
    let parent=import_parent();
    let backup=dir.path().join("fixture.zip");
    let index=index_bytes(dir.path());
    archive(&backup,&index,&serde_json::to_vec(&manifest(vec![],0)).unwrap(),&[]);
    let target=parent.path().join("fresh");
    fs::set_permissions(parent.path(),fs::Permissions::from_mode(0o755)).unwrap();
    assert!(!import_into(&backup,scratch.path(),&target).status.success());
    fs::set_permissions(parent.path(),fs::Permissions::from_mode(0o700)).unwrap();
    let alias=dir.path().join("alias");
    symlink(parent.path(),&alias).unwrap();
    assert!(!import_into(&backup,scratch.path(),&alias.join("fresh")).status.success());
    assert!(fs::read_dir(parent.path()).unwrap().next().is_none());
    symlink(&backup,&target).unwrap();
    assert!(!import_into(&backup,scratch.path(),&target).status.success());
    assert_eq!(fs::read_link(&target).unwrap(),backup);
    assert!(fs::read_dir(scratch.path()).unwrap().next().is_none());
}

#[cfg(target_os = "linux")]
struct MediaHost(std::process::Child, String);
#[cfg(target_os = "linux")]
impl Drop for MediaHost {
    fn drop(&mut self) { let _ = self.0.kill(); let _ = self.0.wait(); }
}
#[cfg(target_os = "linux")]
impl MediaHost {
    fn start(target: &Path, grants: &Path) -> Self {
        use std::io::{BufRead, BufReader};
        let mut child = Command::new(env!("CARGO_BIN_EXE_local-message-hub-rs"))
            .args(["serve","--media-bundle"]).arg(target).arg("--grants").arg(grants)
            .stdout(std::process::Stdio::piped()).spawn().unwrap();
        let mut output = BufReader::new(child.stdout.take().unwrap());
        let mut host = Self(child, String::new());
        let mut line = String::new();
        output.read_line(&mut line).unwrap();
        let ready: Value = serde_json::from_str(&line).unwrap();
        assert_eq!(ready["mode"],"imported-media-read-only");
        host.1 = ready["origin"].as_str().unwrap().trim_start_matches("http://").into();
        host
    }
    fn get(&self, resource: &str, token: &str, extra: &str) -> (u16,String,Vec<u8>) {
        use std::{io::Read, net::TcpStream, time::Duration};
        let mut socket = TcpStream::connect(&self.1).unwrap();
        socket.set_read_timeout(Some(Duration::from_secs(8))).unwrap();
        write!(socket,"GET {resource} HTTP/1.1\r\nHost: {}\r\nConnection: close\r\nAuthorization: Bearer {token}\r\n{extra}\r\n",self.1).unwrap();
        let mut bytes = Vec::new();
        socket.read_to_end(&mut bytes).unwrap();
        let split = bytes.windows(4).position(|w|w==b"\r\n\r\n").unwrap();
        let headers = String::from_utf8_lossy(&bytes[..split]).into_owned();
        (headers.split_whitespace().nth(1).unwrap().parse().unwrap(),headers,bytes[split+4..].to_vec())
    }
}

#[cfg(target_os = "linux")]
const SCOPE_QQ: &str = "platform=qq&account_id=owner&conversation_id=room&message_id=qq1";
#[cfg(target_os = "linux")]
const SCOPE_WECHAT: &str = "platform=wechat&account_id=owner&conversation_id=room&message_id=room:shard.db:14";
#[cfg(target_os = "linux")]
const MEDIA_ROUTE: &str = "/api/v1/messages/media";

#[cfg(target_os = "linux")]
fn media_grants(target: &Path) -> (PathBuf,Vec<String>) {
    use local_message_hub_rs::plugins::generate_token;
    let grants = target.parent().unwrap().join("grants.json");
    let mut tokens=Vec::new();
    let mut records=Vec::new();
    for (id, platform, account, media) in [("qq","qq","owner",true),("wx","wechat","owner",true),
        ("readonly","qq","owner",false),("unrelated","qq","someone-else",true)] {
        let (token,hash)=generate_token().unwrap();
        tokens.push(token);
        records.push(json!({"id":id,"kind":"desktop","token_sha256":hash,
            "permissions":if media {vec!["messages.read","media.read"]} else {vec!["messages.read"]},
            "account_scope":{"platform":platform,"account_id":account}}));
    }
    fs::write(&grants,serde_json::to_vec(&json!({"version":1,"grants":records})).unwrap()).unwrap();
    (grants,tokens)
}

#[cfg(target_os = "linux")]
fn lookup(host: &MediaHost, token: &str, scope: &str) -> Value {
    let (status,_,body)=host.get(&format!("{MEDIA_ROUTE}?{scope}"),token,"");
    assert_eq!(status,200);
    serde_json::from_slice::<Value>(&body).unwrap()["items"].clone()
}
#[cfg(target_os = "linux")]
fn item_url(item: &Value, scope: &str) -> String {
    format!("{MEDIA_ROUTE}/{}?{scope}",item["id"].as_str().unwrap())
}
#[cfg(target_os = "linux")]
fn assert_media_roundtrip(target: &Path, qq: &[u8], wechat: &[u8]) {
    let (grants,tokens)=media_grants(target);
    let host=MediaHost::start(target,&grants);
    let manifest: Value = serde_json::from_slice(&host.get("/api/v1/manifest",&tokens[0],"").2).unwrap();
    assert_eq!(manifest["resources"]["message_media"], MEDIA_ROUTE);
    assert!(manifest["resources"]["messages"].is_null());
    assert_eq!(host.get("/api/v1/messages",&tokens[0],"").0,404);
    let left=lookup(&host,&tokens[0],SCOPE_QQ);
    let right=lookup(&host,&tokens[1],SCOPE_WECHAT);
    assert_eq!(left.as_array().unwrap().len(),1);
    assert_eq!(right.as_array().unwrap().len(),1);
    assert_ne!(left[0]["id"],right[0]["id"]);
    for (items,scope,bytes,token) in [(&left,SCOPE_QQ,qq,&tokens[0]),(&right,SCOPE_WECHAT,wechat,&tokens[1])] {
        let item=&items[0];
        assert_eq!(item.as_object().unwrap().len(),2);
        assert_eq!(item["size"],bytes.len());
        let url=item_url(item,scope);
        let (status,headers,actual)=host.get(&url,token,"");
        assert_eq!(status,200);
        assert_eq!(actual,bytes);
        for expected in ["content-type: application/octet-stream","content-disposition: attachment; filename=hub-attachment.bin",
            "x-content-type-options: nosniff","cache-control: no-store","cross-origin-resource-policy: same-origin"] {
            assert!(headers.contains(expected),"{headers}");
        }
        let (partial, headers, body) = host.get(&url,token,"Range: bytes=0-1\r\n");
        assert_eq!((partial, body.as_slice()), (206, &bytes[..2]));
        assert!(headers.contains(&format!("content-range: bytes 0-1/{}", bytes.len())));
        let (partial, _, body) = host.get(&url,token,"Range: bytes=-2\r\n");
        assert_eq!((partial, body.as_slice()), (206, &bytes[bytes.len()-2..]));
        let (status, headers, _) = host.get(&url,token,"Range: bytes=0-1,3-4\r\n");
        assert_eq!(status,416);
        assert!(headers.contains(&format!("content-range: bytes */{}", bytes.len())));
        let (status, _, body) = host.get(&url,token,"Range: bytes=0-1\r\nIf-Range: \"unknown\"\r\n");
        assert_eq!((status, body.as_slice()), (200, bytes));
        assert_ne!(host.get(&url,token,"Origin: http://example.invalid\r\n").0,200);
    }
    assert_eq!(host.get(&item_url(&right[0],SCOPE_QQ),&tokens[0],"").0,404);
    assert_eq!(host.get(&item_url(&left[0],SCOPE_WECHAT),&tokens[1],"").0,404);
    assert_eq!(host.get(&item_url(&left[0],SCOPE_QQ),&tokens[2],"").0,403);
    assert_eq!(host.get(&item_url(&left[0],SCOPE_QQ),&tokens[3],"").0,404);
    assert_eq!(host.get(&item_url(&left[0],&SCOPE_QQ.replace("conversation_id=room","conversation_id=other")),&tokens[0],"").0,404);
}

#[cfg(target_os = "linux")]
fn import_fixture(target: &Path, ambiguous: bool, shared: &[u8]) {
    let dir=tempfile::tempdir().unwrap();
    let scratch=tempfile::tempdir().unwrap();
    let backup=dir.path().join("original.zip");
    let mut index=index_bytes(dir.path());
    if ambiguous {
        let path=dir.path().join("input.sqlite");
        let db=Connection::open(&path).unwrap();
        db.execute("INSERT INTO messages(platform,account_id,id,conversation_id,type,extra) VALUES('qq','someone-else','qq1','room','image',?)",
            [json!({"roaming_resources":[{"path":QQ_PATH}]}).to_string()]).unwrap();
        db.close().unwrap();
        index=fs::read(path).unwrap();
    }
    let qq=format!("media/{QQ_PATH}");
    let wx=format!("media/wechat-media/{}/private-filename.jpg",wechat_key());
    let manifest=serde_json::to_vec(&manifest(vec![descriptor(&qq,shared),descriptor(&wx,shared)],2*shared.len())).unwrap();
    archive(&backup,&index,&manifest,&[(&qq,shared),(&wx,shared)]);
    let imported=import_into(&backup,scratch.path(),target);
    assert!(imported.status.success(),"{}",String::from_utf8_lossy(&imported.stderr));
}

#[cfg(target_os = "linux")]
#[test]
fn media_reads_exact_bytes_and_denies_cross_scopes_even_for_shared_blob() {
    let parent=import_parent();
    let target=parent.path().join("import");
    let bytes=b"<html>\x00\xff synthetic attached bytes";
    import_fixture(&target,false,bytes);
    assert_media_roundtrip(&target,bytes,bytes);
    check_objects(&target,&[bytes]);
}

#[cfg(target_os = "linux")]
#[test]
fn ambiguous_qq_references_are_hidden_and_old_catalog_only_serves_qq() {
    let parent=import_parent();
    let ambiguous=parent.path().join("ambiguous");
    import_fixture(&ambiguous,true,QQ_BYTES);
    let (grants,tokens)=media_grants(&ambiguous);
    let server=MediaHost::start(&ambiguous,&grants);
    assert_eq!(lookup(&server,&tokens[0],SCOPE_QQ),json!([]));
    assert_eq!(lookup(&server,&tokens[3],&SCOPE_QQ.replace("account_id=owner","account_id=someone-else")),json!([]));
    assert_eq!(lookup(&server,&tokens[1],SCOPE_WECHAT).as_array().unwrap().len(),1);

    let old=parent.path().join("old");
    import_fixture(&old,false,QQ_BYTES);
    let catalog_path=old.join("catalog.json");
    let mut catalog:Value=serde_json::from_slice(&fs::read(&catalog_path).unwrap()).unwrap();
    for item in catalog["items"].as_array_mut().unwrap() {
        item.as_object_mut().unwrap().remove("wechat_key");
        item.as_object_mut().unwrap().remove("wechat_leaf");
    }
    fs::write(catalog_path,serde_json::to_vec(&catalog).unwrap()).unwrap();
    let old_host=MediaHost::start(&old,&grants);
    assert_eq!(lookup(&old_host,&tokens[1],SCOPE_WECHAT),json!([]));
    let items=lookup(&old_host,&tokens[0],SCOPE_QQ);
    assert_eq!(old_host.get(&item_url(&items[0],SCOPE_QQ),&tokens[0],"").2,QQ_BYTES);
}

#[cfg(target_os = "linux")]
#[test]
fn invalid_blob_and_stale_or_mismatched_index_fail_closed_without_bytes() {
    let parent=import_parent();
    let target=parent.path().join("import");
    import_fixture(&target,false,QQ_BYTES);
    let (grants,tokens)=media_grants(&target);
    let host=MediaHost::start(&target,&grants);
    let items=lookup(&host,&tokens[0],SCOPE_QQ);
    let url=item_url(&items[0],SCOPE_QQ);
    let blob=target.join("objects").join(hex::encode(Sha256::digest(QQ_BYTES)));
    fs::write(blob,vec![b'x'; QQ_BYTES.len()]).unwrap();
    let result=host.get(&url,&tokens[0],"");
    assert_eq!(result.0,503);
    assert!(!result.2.windows(QQ_BYTES.len()).any(|s|s==QQ_BYTES));
    let (status, headers, _) = host.get(&url,&tokens[0],"Range: bytes=0-1\r\n");
    assert_eq!(status,503);
    assert!(!headers.contains("content-range"));
    let index=target.join("snapshot/index.sqlite3");
    fs::OpenOptions::new().append(true).open(index).unwrap().write_all(b"stale").unwrap();
    assert_eq!(host.get(&format!("{MEDIA_ROUTE}?{SCOPE_QQ}"),&tokens[0],"").0,503);
    let out=Command::new(env!("CARGO_BIN_EXE_local-message-hub-rs")).args(["serve","--media-bundle"])
        .arg(&target).arg("--grants").arg(&grants).output().unwrap();
    assert!(!out.status.success());
    assert!(out.stdout.is_empty());
    assert!(!String::from_utf8_lossy(&out.stderr).contains(target.to_str().unwrap()));
}

#[cfg(target_os = "linux")]
#[test]
fn objects_above_download_budget_are_importable_but_not_listed() {
    let parent=import_parent();
    let target=parent.path().join("import");
    let bytes=vec![37_u8; 8*1024*1024+1];
    import_fixture(&target,false,&bytes);
    let (grants,tokens)=media_grants(&target);
    let host=MediaHost::start(&target,&grants);
    assert_eq!(lookup(&host,&tokens[0],SCOPE_QQ),json!([]));
    assert_eq!(lookup(&host,&tokens[1],SCOPE_WECHAT),json!([]));
}

#[cfg(target_os = "linux")]
#[test]
fn mismatched_wechat_reference_is_rejected_before_listening() {
    let parent=import_parent();
    let target=parent.path().join("import");
    import_fixture(&target,false,QQ_BYTES);
    let catalog_path=target.join("catalog.json");
    let mut catalog: Value = serde_json::from_slice(&fs::read(&catalog_path).unwrap()).unwrap();
    let row=catalog["items"].as_array_mut().unwrap().iter_mut().find(|row|row["source"]=="wechat").unwrap();
    row["wechat_key"]=json!("f".repeat(64));
    fs::write(&catalog_path,serde_json::to_vec(&catalog).unwrap()).unwrap();
    let (grants,_tokens)=media_grants(&target);
    let out=Command::new(env!("CARGO_BIN_EXE_local-message-hub-rs")).args(["serve","--media-bundle"])
        .arg(&target).arg("--grants").arg(&grants).output().unwrap();
    assert!(!out.status.success());
    assert!(out.stdout.is_empty());
}

#[cfg(target_os = "linux")]
#[test]
fn oversized_candidate_metadata_fails_binding_instead_of_hiding_a_competing_owner() {
    let parent=import_parent();
    let target=parent.path().join("import");
    import_fixture(&target,true,QQ_BYTES);
    let index=target.join("snapshot/index.sqlite3");
    let db=Connection::open(&index).unwrap();
    db.execute("UPDATE messages SET extra=? WHERE account_id='someone-else'",[json!({
        "roaming_resources":[{"path":QQ_PATH}],"unrelated":"z".repeat(256*1024)
    }).to_string()]).unwrap();
    db.close().unwrap();
    let catalog_path=target.join("catalog.json");
    let mut catalog: Value=serde_json::from_slice(&fs::read(&catalog_path).unwrap()).unwrap();
    catalog["projected_index_sha256"]=json!(hex::encode(Sha256::digest(fs::read(index).unwrap())));
    fs::write(catalog_path,serde_json::to_vec(&catalog).unwrap()).unwrap();
    let (grants,_tokens)=media_grants(&target);
    let out=Command::new(env!("CARGO_BIN_EXE_local-message-hub-rs")).args(["serve","--media-bundle"])
        .arg(&target).arg("--grants").arg(&grants).output().unwrap();
    assert!(!out.status.success());
    assert!(out.stdout.is_empty());
}
