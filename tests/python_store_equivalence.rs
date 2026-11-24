//! Optional differential against the original Python-produced archive.
use local_message_hub_rs::store::{ContextQuery, Query, Store};
use serde_json::Value;
use std::{path::Path, process::Command};

#[test]
#[ignore = "requires Python 3; run with cargo test --test python_store_equivalence -- --ignored"]
fn python_archive_and_cursor_match_native_reads() {
    let temp = tempfile::tempdir().unwrap();
    let path = temp.path().join("hub.sqlite");
    let original = Command::new(std::env::var_os("PYTHON").unwrap_or_else(|| "python3".into()))
        .current_dir(Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap())
        .arg("-c")
        .arg(r#"
import json, sys
from hub.store import ChatStore
s = ChatStore(sys.argv[1])
for platform, account, message in [('wechat', '家', '甲'), ('qq', 'beta', '乙')]:
    rows = [
        {'id':message+'0','conversation_id':'房','sender_id':'person','timestamp':100,
         'text':'课程 + 100% extra','wechat_quote':{'target_server_id':'128','title':'示例'},
         '_hub_wechat_server_id':'129'},
        {'id':message+'1','conversation_id':'房','sender_id':'person','timestamp':100,
         'text':'课程 100xx','_hub_wechat_server_id':'128'},
    ]
    s.ingest(platform, account, [{'id':'房','title':'课程 Room','last_timestamp':100,
                                  'avatar_url':'/synthetic/avatar.png'}],
             rows, [{'id':'person','name':'名字','kind':'private',
                     'avatar_url':'https://synthetic.invalid/person.png','extra':{'alias':'alias'}}])
sc = {'platform':'wechat','account_id':'家','conversation_id':'房'}
cursor = s.messages(**sc,limit=1)['next_cursor']
resolved = s.quoted_message_target_for_reference(**sc,referencing_message_id='甲0')
quote = s.message_context(**sc,message_id=resolved)
quote['target_message_id'] = resolved
print(json.dumps({'conversations':s.conversations('wechat','课程'),
 'contacts':s.contacts('all','alias'),
 'messages':s.messages(**sc,limit=1),
 'messages_next':s.messages(**sc,limit=1,cursor=cursor),
 'context':s.message_context(**sc,message_id='甲0',before=0,after=1),
 'quote':quote,
 'search':s.search('课程 + 100%',preview_chars=1200)},ensure_ascii=True))
s.close()
"#)
        .arg(&path)
        .output()
        .expect("start Python");
    assert!(
        original.status.success(),
        "{}",
        String::from_utf8_lossy(&original.stderr)
    );
    let expected: Value = serde_json::from_slice(&original.stdout).unwrap();
    let before = std::fs::read(&path).unwrap();
    let native = Store::open(&path).unwrap();
    let mut page = Query {
        platform: "wechat".into(),
        account_id: "家".into(),
        conversation_id: "房".into(),
        limit: Some(1),
        ..Default::default()
    };
    assert_eq!(native.messages(&page).unwrap(), expected["messages"]);
    page.cursor = expected["messages"]["next_cursor"]
        .as_str()
        .unwrap()
        .to_owned();
    assert_eq!(native.messages(&page).unwrap(), expected["messages_next"]);
    assert_eq!(
        native
            .conversations(&Query {
                platform: "wechat".into(),
                q: "课程".into(),
                ..Default::default()
            })
            .unwrap(),
        expected["conversations"]
    );
    assert_eq!(
        native
            .contacts(&Query {
                q: "alias".into(),
                ..Default::default()
            })
            .unwrap(),
        expected["contacts"]
    );
    assert_eq!(
        native
            .search(&Query {
                q: "课程 + 100%".into(),
                ..Default::default()
            })
            .unwrap(),
        expected["search"]
    );
    let context = ContextQuery {
        platform: "wechat".into(),
        account_id: "家".into(),
        conversation_id: "房".into(),
        message_id: "甲0".into(),
        before: Some(0),
        after: Some(1),
        ..Default::default()
    };
    assert_eq!(
        native.message_context(&context).unwrap().unwrap(),
        expected["context"]
    );
    let quote = ContextQuery {
        message_id: String::new(),
        reference_message_id: "甲0".into(),
        before: None,
        after: None,
        ..context
    };
    assert_eq!(
        native.quoted_message_context(&quote).unwrap().unwrap(),
        expected["quote"]
    );
    assert_eq!(std::fs::read(&path).unwrap(), before);
}
