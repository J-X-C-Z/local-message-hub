use rusqlite::Connection;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{path::Path, process::Command};

fn cli(command: &str, options: &[&str]) -> std::process::Output {
    Command::new(env!("CARGO_BIN_EXE_local-message-hub-rs"))
        .arg(command).args(options).output().unwrap()
}

fn call(command: &str, options: &[&str]) -> Value {
    let out = cli(command, options);
    assert!(out.status.success(), "{command}: {}", String::from_utf8_lossy(&out.stderr));
    serde_json::from_slice(&out.stdout).unwrap()
}

fn reject(command: &str, options: &[&str]) {
    let out = cli(command, options);
    assert!(!out.status.success() && out.stdout.is_empty(), "{command}: {out:?}");
    assert!(!String::from_utf8_lossy(&out.stderr).contains("BODY_CANARY"));
}

fn write(location: &Path, payload: &Value) {
    std::fs::write(location, serde_json::to_vec(payload).unwrap()).unwrap();
}

#[test]
fn scoped_detached_fill_rejects_stale_competing_body_and_preserves_legacy_import() {
    let temp = tempfile::tempdir().unwrap();
    let archive = tempfile::Builder::new().prefix("native-").tempdir_in(temp.path()).unwrap();
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(archive.path(), std::fs::Permissions::from_mode(0o700)).unwrap();
    }
    let root = archive.path().to_str().unwrap();
    call("init-live", &["--directory", root]);
    let intake = temp.path().join("initial.json");
    let a = "alpha";
    let b = "beta";
    let url = "https://www.sysu.edu.cn/info/9999/123.htm";
    let article_id = hex::encode(Sha256::digest(url));
    let initial = json!({"url": url,"title":"Stable title","summary":"Stable summary",
         "source_id":"synthetic-source","source_name":"Catalog","category":"teaching"});
    let bring_in = |scope: &str| {
        write(&intake, &json!({"version":1,"profile":scope,"articles":[initial]}));
        let mut input = vec!["--source", intake.to_str().unwrap(), "--directory", root, "--profile", scope];
        let preview = call("preview-campus-json", &input);
        input.extend_from_slice(&["--confirm", preview["confirmation"].as_str().unwrap()]);
        assert_eq!(call("import-campus-json", &input)["created"], 1);
        preview["confirmation"].as_str().unwrap().to_owned()
    };
    let first_receipt = bring_in(a);
    bring_in(b);
    let first_file = temp.path().join("first.json");
    let other_file = temp.path().join("other.json");
    let first = json!({"version":1,"profile":a,"article_id":article_id,"content":"BODY_CANARY 科研"});
    let alternative = json!({"version":1,"profile":a,"article_id":article_id,"content":"Different body"});
    write(&first_file, &first);
    write(&other_file, &alternative);

    let first_args = ["--source", first_file.to_str().unwrap(), "--directory", root, "--profile", a];
    let other_args = ["--source", other_file.to_str().unwrap(), "--directory", root, "--profile", a];
    let preview = call("preview-campus-content", &first_args);
    assert_eq!(preview["article_id"], article_id);
    assert_eq!(preview["unchanged"], false);
    assert!(preview.get("content").is_none());
    let other_preview = call("preview-campus-content", &other_args);
    let text_for = |scope: &str| {
        Connection::open(archive.path().join("native-v1.sqlite3")).unwrap().query_row(
            "SELECT title,summary,content,search_text FROM native_campus_articles WHERE profile=?1 AND id=?2",
            (scope, &article_id), |r| Ok([r.get::<_,String>(0)?,r.get(1)?,r.get(2)?,r.get(3)?]),
        ).unwrap()
    };
    assert_eq!(text_for(a)[2], "");
    let commit = |command, options: &[&str], pin| {
        let mut args = options.to_vec();
        args.extend_from_slice(&["--confirm", pin]);
        call(command, &args)
    };
    let pin = preview["confirmation"].as_str().unwrap();
    let mut wrong = other_args.to_vec();
    wrong.extend_from_slice(&["--confirm", pin]);
    reject("import-campus-content", &wrong);
    let mut wrong_profile = first_args;
    wrong_profile[5] = b;
    reject("preview-campus-content", &wrong_profile);

    assert_eq!(commit("import-campus-content", &first_args, pin)["unchanged"], false);
    assert_eq!(commit("import-campus-content", &first_args, pin)["unchanged"], true);
    let mut stale = other_args.to_vec();
    stale.extend_from_slice(&["--confirm", other_preview["confirmation"].as_str().unwrap()]);
    reject("import-campus-content", &stale);
    assert_eq!(text_for(a), ["Stable title","Stable summary","BODY_CANARY 科研",
                            "stable title stable summary body_canary 科研 catalog"]);
    assert_eq!(text_for(b)[2], "");

    // Original confirmed intake still reconciles its original batch, rather
    // than erasing or fighting the separately approved content fill.
    write(&intake, &json!({"version":1,"profile":a,"articles":[initial]}));
    let old_args = ["--source", intake.to_str().unwrap(), "--directory", root, "--profile", a];
    let replay = commit("import-campus-json", &old_args, &first_receipt);
    assert_eq!(replay["created"], 1);
    assert_eq!(text_for(a)[2], "BODY_CANARY 科研");
}
