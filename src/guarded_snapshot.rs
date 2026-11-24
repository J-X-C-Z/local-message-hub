//! Operator-authorized, bounded account projection from one detached v9 index.
use crate::{events::GUARDED_V9_PRODUCER as PRODUCER,migration::{fingerprint,validate_source},profile_lock::AuthorizedGuardedImport,snapshot::Snapshot,store::PLATFORMS};
use rusqlite::{Connection,OptionalExtension,ToSql,TransactionBehavior,params_from_iter,types::Value as Sql};
use serde::{Deserialize,Serialize,Serializer,ser::{Error as _,SerializeSeq}};
use serde_json::{Value,json};
use sha2::{Digest,Sha256};
use std::{cell::RefCell,collections::{BTreeMap,BTreeSet},fs,io::Write,path::Path,time::{Duration,Instant}};

const MAX_FILE:u64=128*1024*1024;
pub(crate) const MAX_PAYLOAD:usize=4*1024*1024;

#[derive(Debug,thiserror::Error)]
pub(crate) enum Error {
    #[error("source is not a supported bounded detached v9 index")]
    Source,
    #[error("source changed while reading")]
    Changed,
    #[error("invalid or over-budget normalized account rows")]
    Rows,
    #[error("guarded target is unavailable")]
    Target,
    #[error("guarded snapshot import requires an empty archive")]
    Populated,
    #[error("confirmation does not match this exact preview")]
    Confirmation,
    #[error("guarded snapshot commit was not confirmed; use the same exact preview to retry")]
    Apply,
}

struct Projection {
    table:&'static str, columns:&'static str, select:&'static str, cap:usize,
}
const TABLES:&[Projection]=&[
    Projection{table:"conversations",columns:"id,title,kind,member_count,last_message,last_timestamp,message_count",
        select:"id,title,kind,member_count,last_message,last_timestamp,message_count",cap:256},
    Projection{table:"contacts",columns:"id,name,kind",select:"id,name,kind",cap:256},
    Projection{table:"messages",columns:"id,conversation_id,sender_id,sender_name,timestamp,type,text,is_self,extra",
        select:"id,conversation_id,sender_id,sender_name,timestamp,type,text,is_self,
        CASE WHEN platform='wechat' AND json_valid(extra) THEN
            CASE WHEN json_type(extra,'$._hub_wechat_server_id')='text' AND
                      length(json_extract(extra,'$._hub_wechat_server_id'))<=20
                THEN json_extract(extra,'$._hub_wechat_server_id') END END,
        CASE WHEN platform='wechat' AND json_valid(extra) THEN
            CASE WHEN json_type(extra,'$.wechat_quote.target_server_id')='text' AND
                      length(json_extract(extra,'$.wechat_quote.target_server_id'))<=20
                THEN json_extract(extra,'$.wechat_quote.target_server_id') END END",cap:1000},
    Projection{table:"conversation_metadata",columns:"conversation_id,note,tags,archived,updated_at",
        select:"conversation_id,note,tags,archived,updated_at",cap:256},
    Projection{table:"contact_identities",columns:"contact_id,identity_id,updated_at",
        select:"contact_id,identity_id,updated_at",cap:256},
];

struct Plan {
    confirmation:String, hash:String, records:Vec<Vec<Vec<Sql>>>,
}
#[derive(Serialize)]
pub(crate) struct Preview {
    confirmation:String, rows:BTreeMap<&'static str,usize>,
}
#[derive(Serialize)]
pub(crate) struct Receipt {
    batch_id:String, rows:BTreeMap<&'static str,usize>,
}

fn sqlite_source(path:&Path) -> Result<Connection,Error> {
    let db=Connection::open_in_memory().map_err(|_|Error::Source)?;
    db.pragma_update(None,"trusted_schema",false).map_err(|_|Error::Source)?;
    let deadline=Instant::now()+Duration::from_secs(15);
    db.progress_handler(2048,Some(move || Instant::now()>deadline));
    let mut uri=url::Url::from_file_path(path).map_err(|_|Error::Source)?;
    if uri.has_host() {return Err(Error::Source);}
    uri.set_query(Some("mode=ro&immutable=1"));
    db.execute("ATTACH DATABASE ? AS source",[uri.as_str()]).map_err(|_|Error::Source)?;
    db.set_limit(rusqlite::limits::Limit::SQLITE_LIMIT_LENGTH,256*1024);
    db.pragma_update(None,"query_only",true).map_err(|_|Error::Source)?;
    validate_source(&db).map_err(|_|Error::Source)?;
    Ok(db)
}

fn id(s:&str,max:usize) -> bool {
    (1..=max).contains(&s.len()) && !s.chars().any(char::is_control)
}
fn key(sql:&Sql) -> Result<&str,Error> {
    match sql {Sql::Text(s) if id(s,256) => Ok(s),_=>Err(Error::Rows)}
}
fn valid_text(sql:&Sql,limit:usize) -> bool {
    matches!(sql,Sql::Null) || matches!(sql,Sql::Text(s) if s.len()<=limit && !s.contains('\0'))
}
fn valid_int(sql:&Sql,nullable:bool) -> bool {
    matches!(sql,Sql::Integer(n) if *n>=0) || (nullable && matches!(sql,Sql::Null))
}
fn bit(sql:&Sql) -> bool { matches!(sql,Sql::Integer(0|1)) }
fn decimal(sql:&Sql) -> Option<&str> {
    match sql {
        Sql::Text(s) if (1..=20).contains(&s.len()) && s.as_bytes()[0]!=b'0'
            && s.bytes().all(|c|c.is_ascii_digit()) => Some(s),
        _=>None,
    }
}

fn normalize(table:&str, row:&mut Vec<Sql>,conversations:&BTreeSet<String>,contacts:&BTreeSet<String>)
    -> Result<(),Error> {
    let first=key(&row[0])?;
    let good=match table {
        "conversations" => valid_text(&row[1],1024) && valid_text(&row[2],128) && valid_int(&row[3],true)
            && valid_text(&row[4],65_536) && valid_int(&row[5],true) && valid_int(&row[6],false),
        "contacts" => valid_text(&row[1],1024) && valid_text(&row[2],128),
        "messages" => {
            let server=decimal(&row[8]);
            let quote=decimal(&row[9]);
            let mut extra=serde_json::Map::new();
            if let Some(server)=server {extra.insert("_hub_wechat_server_id".into(),json!(server));}
            if let Some(quote)=quote {extra.insert("wechat_quote".into(),json!({"target_server_id":quote}));}
            row.truncate(8);
            row.push(if extra.is_empty(){Sql::Null}else{Sql::Text(Value::Object(extra).to_string())});
            conversations.contains(key(&row[1])?)
                && (matches!(&row[2],Sql::Null) || matches!(&row[2],Sql::Text(s) if s.is_empty() || id(s,256)))
                && valid_text(&row[3],1024) && valid_int(&row[4],false) && valid_text(&row[5],128)
                && valid_text(&row[6],65_536) && bit(&row[7])
        }
        "conversation_metadata" => {
            let tags=match &row[2] {
                Sql::Text(tags) if tags.len()<=8192=> serde_json::from_str::<Vec<String>>(tags).ok(),
                _=>None,
            };
            conversations.contains(first) && matches!(&row[1],Sql::Text(_)) && valid_text(&row[1],8192)
                && tags.is_some_and(|tags|tags.len()<=64 && tags.iter().all(|tag|id(tag,128)))
                && bit(&row[3]) && valid_int(&row[4],false)
        }
        "contact_identities" => contacts.contains(first) && key(&row[1]).is_ok() && valid_int(&row[2],false),
        _=>false,
    };
    if good {Ok(())} else {Err(Error::Rows)}
}

fn load_records(db:&Connection,schema:&str,platform:&str,account:&str)->Result<Vec<Vec<Vec<Sql>>>,Error> {
        let mut records=Vec::new();
        let (mut conversations,mut contacts)=(BTreeSet::new(),BTreeSet::new());
        let mut budget=MAX_PAYLOAD;
        for p in TABLES {
            let query=format!("SELECT platform,account_id,{} FROM {schema}.{}
                WHERE platform COLLATE BINARY=? AND account_id COLLATE BINARY=? ORDER BY rowid LIMIT ?",
                              p.select,p.table);
            let mut stmt=db.prepare(&query).map_err(|_|Error::Source)?;
            let columns=stmt.column_count();
            let mut list=stmt.query((platform,account,p.cap+1)).map_err(|_|Error::Rows)?;
            let mut saved=Vec::new();
            while let Some(row)=list.next().map_err(|_|Error::Rows)? {
                if saved.len()>=p.cap {return Err(Error::Rows);}
                if row.get::<_,String>(0).map_err(|_|Error::Rows)?!=platform
                    || row.get::<_,String>(1).map_err(|_|Error::Rows)?!=account { return Err(Error::Rows); }
                let mut values=(2..columns).map(|n|row.get::<_,Sql>(n)).collect::<rusqlite::Result<Vec<_>>>()
                    .map_err(|_|Error::Rows)?;
                normalize(p.table,&mut values,&conversations,&contacts)?;
                budget=budget.checked_sub(platform.len()+account.len()+64).ok_or(Error::Rows)?;
                for field in &values {
                    let cost=match field {
                        Sql::Text(value)=>value.len()+32,
                        Sql::Integer(_)|Sql::Null=>32,
                        _=>return Err(Error::Rows),
                    };
                    budget=budget.checked_sub(cost).ok_or(Error::Rows)?;
                }
                if p.table=="conversations" {conversations.insert(key(&values[0])?.to_owned());}
                if p.table=="contacts" {contacts.insert(key(&values[0])?.to_owned());}
                saved.push(values);
            }
            records.push(saved);
        }
        if records.iter().all(Vec::is_empty) {return Err(Error::Rows);}
        Ok(records)
}

pub(crate) fn valid_scope(platform:&str,account:&str)->bool {
    PLATFORMS.contains(&platform) && platform!="all" && id(account,256) && account.trim()==account
}

impl Plan {
    fn read(path:&Path,platform:&str,account:&str,authorization:&AuthorizedGuardedImport<'_>) -> Result<Self,Error> {
        if !valid_scope(platform,account) {
            return Err(Error::Rows);
        }
        let source=Snapshot::open(path).map_err(|_|Error::Source)?;
        if fs::metadata(source.path()).map_err(|_|Error::Source)?.len()>MAX_FILE {return Err(Error::Source);}
        let hash=fingerprint(source.path()).map_err(|_|Error::Source)?;
        let db=sqlite_source(source.path())?;
        let records=load_records(&db,"source",platform,account)?;
        source.verify().map_err(|_|Error::Changed)?;
        if fs::metadata(source.path()).map_err(|_|Error::Changed)?.len()>MAX_FILE
            || fingerprint(source.path()).map_err(|_|Error::Changed)?!=hash
        {return Err(Error::Changed);}
        let proof=serde_json::to_vec(&("lmh-guarded-v9-v1",authorization.archive().archive_id(),platform,account,&hash))
            .map_err(|_|Error::Rows)?;
        Ok(Self {confirmation:hex::encode(Sha256::digest(proof)),hash,records})
    }
    fn counts(&self) -> BTreeMap<&'static str,usize> {
        TABLES.iter().zip(&self.records).map(|(p,v)|(p.table,v.len())).collect()
    }
    fn already_or_empty(&self,db:&Connection) -> Result<bool,Error> {
        let previous:Option<Vec<u8>>=db.query_row(
            "SELECT digest FROM native_ingest_receipts WHERE producer=? AND idempotency_key=?",
            (PRODUCER,&self.confirmation),|r|r.get(0)).optional().map_err(|_|Error::Target)?;
        if let Some(previous)=previous {
            return if previous==self.hash.as_bytes() {Ok(true)} else {Err(Error::Target)};
        }
        let occupied:bool=db.query_row("SELECT EXISTS(
              SELECT 1 FROM conversations UNION ALL SELECT 1 FROM messages
              UNION ALL SELECT 1 FROM contacts UNION ALL SELECT 1 FROM conversation_metadata
              UNION ALL SELECT 1 FROM contact_identities UNION ALL SELECT 1 FROM native_ingest_receipts
              UNION ALL SELECT 1 FROM native_event_journal UNION ALL SELECT 1 FROM native_owners
              UNION ALL SELECT 1 FROM native_account_event_sequence UNION ALL SELECT 1 FROM native_account_incoming
              UNION ALL SELECT 1 FROM native_metadata_audit)",[],|row|row.get(0)).map_err(|_|Error::Target)?;
        if occupied {return Err(Error::Populated);}
        Ok(false)
    }
}

pub(crate) fn preview(source:&Path,platform:&str,account:&str,auth:&AuthorizedGuardedImport<'_>)
    -> Result<Preview,Error> {
    let plan=Plan::read(source,platform,account,auth)?;
    auth.recheck().map_err(|_|Error::Target)?;
    let native=auth.archive().read_connection().map_err(|_|Error::Target)?;
    plan.already_or_empty(&native)?;
    Ok(Preview{rows:plan.counts(),confirmation:plan.confirmation})
}

pub(crate) fn commit(source:&Path,platform:&str,account:&str,confirmation:&str,auth:&AuthorizedGuardedImport<'_>)
    -> Result<Receipt,Error> {
    let plan=Plan::read(source,platform,account,auth)?;
    if plan.confirmation!=confirmation {return Err(Error::Confirmation);}
    write_plan(&plan,platform,account,auth)?;
    Ok(Receipt{rows:plan.counts(),batch_id:plan.confirmation})
}

fn borrowed_bindings<'a>(scope: [&'a dyn ToSql; 2], row: &'a [Sql]) -> impl Iterator<Item=&'a dyn ToSql> {
    scope.into_iter().chain(row.iter().map(|cell| cell as &dyn ToSql))
}

fn write_plan(plan:&Plan,platform:&str,account:&str,auth:&AuthorizedGuardedImport<'_>) -> Result<(),Error> {
    let archive=auth.archive();
    let mut db=archive.guarded_snapshot_writer(auth).map_err(|_|Error::Target)?;
    let tx=db.transaction_with_behavior(TransactionBehavior::Immediate).map_err(|_|Error::Apply)?;
    archive.verify_connection(&tx).map_err(|_|Error::Target)?;
    if !plan.already_or_empty(&tx)? {
        for (p,records) in TABLES.iter().zip(&plan.records) {
            let values=vec!["?";p.columns.split(',').count()+2].join(",");
            let sql=format!("INSERT INTO {}(platform,account_id,{}) VALUES({values})",p.table,p.columns);
            let mut stmt=tx.prepare(&sql).map_err(|_|Error::Apply)?;
            for row in records {
                // SQLite already copies bound text into its own storage. Keep
                // the normalized plan alive and let ToSql borrow each value.
                let payload=borrowed_bindings([&platform,&account],row);
                stmt.execute(params_from_iter(payload)).map_err(|_|Error::Apply)?;
            }
        }
        tx.execute("INSERT INTO native_ingest_receipts(producer,idempotency_key,digest,created,revised,unchanged,checkpoint)
            VALUES (?,?,?,?,0,0,0)",(PRODUCER,&plan.confirmation,plan.hash.as_bytes(),plan.records[2].len()))
            .map_err(|_|Error::Apply)?;
        tx.commit().map_err(|_|Error::Apply)?;
    }
    Ok(())
}

#[derive(Serialize,Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct BackupRows {
    platform:String, account_id:String, tables:Vec<Vec<Vec<Value>>>,
}

pub(crate) fn backup_rows(auth:&AuthorizedGuardedImport<'_>,platform:&str,account:&str)->Result<BackupRows,Error> {
    if !valid_scope(platform,account) {return Err(Error::Rows);}
    auth.recheck().map_err(|_|Error::Target)?;
    let mut db=auth.archive().read_connection().map_err(|_|Error::Target)?;
    let tx=db.transaction().map_err(|_|Error::Target)?;
    auth.archive().verify_connection(&tx).map_err(|_|Error::Target)?;
    let tables=load_records(&tx,"main",platform,account)?.into_iter().map(|table|table.into_iter()
        .map(|row|row.into_iter().map(|cell|match cell {
            Sql::Null=>Value::Null,Sql::Integer(n)=>json!(n),Sql::Text(s)=>Value::String(s),
            _=>unreachable!("normalized row types"),
        }).collect()).collect()).collect();
    auth.recheck().map_err(|_|Error::Target)?;
    Ok(BackupRows{platform:platform.into(),account_id:account.into(),tables})
}

// Serde's JSON writer visits each sequence element once. Own its
// source rows here so emitted row strings can be released before later
// output grows. No changes to BackupRows deserialization or validation.
struct OnceRows(RefCell<Option<Vec<Vec<Value>>>>);
impl Serialize for OnceRows {
    fn serialize<S:Serializer>(&self,serializer:S)->Result<S::Ok,S::Error> {
        let rows=self.0.borrow_mut().take().ok_or_else(||S::Error::custom("backup rows already consumed"))?;
        let mut seq=serializer.serialize_seq(Some(rows.len()))?;
        for row in rows { seq.serialize_element(&row)?; }
        seq.end()
    }
}

struct OnceTables(RefCell<Option<Vec<Vec<Vec<Value>>>>>);
impl Serialize for OnceTables {
    fn serialize<S:Serializer>(&self,serializer:S)->Result<S::Ok,S::Error> {
        let tables=self.0.borrow_mut().take().ok_or_else(||S::Error::custom("backup tables already consumed"))?;
        let mut seq=serializer.serialize_seq(Some(tables.len()))?;
        for table in tables { seq.serialize_element(&OnceRows(RefCell::new(Some(table))))?; }
        seq.end()
    }
}

#[derive(Serialize)]
struct ConsumingBackupRows {
    platform:String, account_id:String, tables:OnceTables,
}

impl BackupRows {
    pub(crate) fn write_consuming_json(self,writer:impl Write) -> serde_json::Result<()> {
        let Self{platform,account_id,tables}=self;
        serde_json::to_writer(writer,&ConsumingBackupRows{
            platform,account_id,tables:OnceTables(RefCell::new(Some(tables))),
        })
    }

    pub(crate) fn matches_scope(&self, platform: &str, account: &str) -> bool {
        self.platform == platform && self.account_id == account
    }

    // Treat authenticated files as untrusted data too. Validate structure,
    // scope, size/foreign keys and exact canonical quote subset before staging.
    pub(crate) fn validate(self) -> Result<ValidatedBackup,Error> {
        let Self{platform,account_id,tables}=self;
        if !valid_scope(&platform,&account_id) || tables.len()!=TABLES.len()
            || tables.iter().all(Vec::is_empty) {return Err(Error::Rows);}
        let (mut conversations,mut contacts)=(BTreeSet::new(),BTreeSet::new());
        let mut records=Vec::new();
        let mut budget=MAX_PAYLOAD;
        for (p,table) in TABLES.iter().zip(tables) {
            if table.len()>p.cap {return Err(Error::Rows);}
            let mut seen=BTreeSet::new();
            let mut saved=Vec::new();
            for row in table {
                if row.len()!=p.columns.split(',').count() {return Err(Error::Rows);}
                let mut row=row.into_iter().map(|cell|match cell {
                    Value::Null=>Ok(Sql::Null),Value::String(s)=>Ok(Sql::Text(s)),
                    Value::Number(n)=>n.as_i64().map(Sql::Integer).ok_or(Error::Rows),_=>Err(Error::Rows),
                }).collect::<Result<Vec<_>,_>>()?;
                let message_extra=if p.table=="messages" {
                    let extra=row.pop().ok_or(Error::Rows)?;
                    let (server,quote)=match &extra {
                        Sql::Null=>(Sql::Null,Sql::Null),
                        Sql::Text(extra) if platform=="wechat" && extra.len()<=256 =>{
                            let val:Value=serde_json::from_str(extra).map_err(|_|Error::Rows)?;
                            let server=val["_hub_wechat_server_id"].as_str();
                            let quote=val["wechat_quote"]["target_server_id"].as_str();
                            (server.map_or(Sql::Null,|s|Sql::Text(s.into())),quote.map_or(Sql::Null,|s|Sql::Text(s.into())))
                        }
                        _=>return Err(Error::Rows),
                    };
                    row.extend([server,quote]);
                    Some(extra)
                } else {None};
                normalize(p.table,&mut row,&conversations,&contacts)?;
                // Writer always uses this canonical JSON representation and
                // scope. No alternative spelling/foreign/raw extras accepted.
                if message_extra.as_ref().is_some_and(|extra| &row[8]!=extra) {return Err(Error::Rows);}
                let id=key(&row[0])?.to_owned();
                if !seen.insert(id.clone()) {return Err(Error::Rows);}
                if p.table=="conversations" {conversations.insert(id);}
                else if p.table=="contacts" {contacts.insert(id);}
                budget=budget.checked_sub(platform.len()+account_id.len()+64).ok_or(Error::Rows)?;
                for field in &row {
                    budget=budget.checked_sub(32+match field {Sql::Text(s)=>s.len(),_=>0}).ok_or(Error::Rows)?;
                }
                saved.push(row);
            }
            records.push(saved);
        }
        Ok(ValidatedBackup{platform,account_id,records})
    }
}

pub(crate) struct ValidatedBackup {platform:String,account_id:String,records:Vec<Vec<Vec<Sql>>>}
/// Observe the transaction marker written by `ValidatedBackup::write`.
/// This is receipt evidence, not a fresh assertion about every restored row.
pub(crate) fn has_backup_receipt(auth:&AuthorizedGuardedImport<'_>,identity:&str)->Result<bool,Error> {
    auth.recheck().map_err(|_|Error::Target)?;
    let mut db=auth.archive().read_connection().map_err(|_|Error::Target)?;
    let tx=db.transaction().map_err(|_|Error::Target)?;
    auth.archive().verify_connection(&tx).map_err(|_|Error::Target)?;
    let digest:Option<Vec<u8>>=tx.query_row(
        "SELECT digest FROM native_ingest_receipts WHERE producer=? AND idempotency_key=?",
        (PRODUCER,format!("backup-{identity}")),|row|row.get(0),
    ).optional().map_err(|_|Error::Target)?;
    auth.recheck().map_err(|_|Error::Target)?;
    match digest {
        Some(saved) if saved.as_slice()==identity.as_bytes()=>Ok(true),
        Some(_)=>Err(Error::Target),
        None=>Ok(false),
    }
}

impl ValidatedBackup {
    pub(crate) fn write(self,auth:&AuthorizedGuardedImport<'_>,ciphertext_identity:&str) -> Result<BTreeMap<&'static str,usize>,Error> {
        let plan=Plan {confirmation:format!("backup-{ciphertext_identity}"),hash:ciphertext_identity.into(),records:self.records};
        write_plan(&plan,&self.platform,&self.account_id,auth)?;
        Ok(plan.counts())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn consuming_backup_rows_preserve_plaintext_and_bounded_writer_errors() {
        let input=br#"{"platform":"wechat","account_id":"sample","tables":[[],[["friend","name\n\t\\\"","group"]],[["m",null,7,0,"\u4e91"]],[],[]]}"#;
        let parsed:BackupRows=serde_json::from_slice(input).unwrap();
        let expected=serde_json::to_vec(&parsed).unwrap();
        let mut emitted=Vec::new();
        parsed.write_consuming_json(&mut emitted).unwrap();
        assert_eq!(emitted,expected);

        struct Limit { cap:usize, body:Vec<u8> }
        impl Write for Limit {
            fn write(&mut self,b:&[u8])->std::io::Result<usize> {
                if self.body.len()+b.len()>self.cap {
                    return Err(std::io::Error::other("synthetic bound"));
                }
                self.body.extend_from_slice(b);
                Ok(b.len())
            }
            fn flush(&mut self)->std::io::Result<()> { Ok(()) }
        }
        for cap in 0..=expected.len() {
            let reset=||Limit{cap,body:Vec::new()};
            let mut old=reset();
            let mut new=reset();
            let data:BackupRows=serde_json::from_slice(input).unwrap();
            let legacy=serde_json::to_writer(&mut old,&data);
            let result=data.write_consuming_json(&mut new);
            assert_eq!(legacy.is_ok(),result.is_ok(),"cap {cap}");
            assert_eq!(old.body,new.body,"cap {cap}");
            if cap<expected.len() {
                assert!(legacy.unwrap_err().is_io());
                assert!(result.unwrap_err().is_io());
            }
        }
    }

    #[test]
    fn guarded_write_borrowed_bindings_match_owned_bindings() {
        let db=Connection::open_in_memory().unwrap();
        db.execute_batch("CREATE TABLE t(platform TEXT,account TEXT,id TEXT,ts INTEGER,body,note)").unwrap();
        let data=[
            vec![Sql::Text("中😀".into()),Sql::Integer(i64::MAX),Sql::Text("a".repeat(4096)),Sql::Null],
            vec![Sql::Text("other".into()),Sql::Integer(0),Sql::Text(String::new()),Sql::Text("exact".into())],
            vec![Sql::Text("mixed".into()),Sql::Real(3.25),Sql::Blob(vec![0,1,255]),Sql::Null],
        ];
        let (platform,account)=("wechat","synthetic-owner");
        let sql="INSERT INTO t VALUES(?,?,?,?,?,?)";
        let mut stmt=db.prepare(sql).unwrap();
        for row in &data {
            let owned=std::iter::once(Sql::Text(platform.into()))
                .chain(std::iter::once(Sql::Text(account.into()))).chain(row.iter().cloned());
            stmt.execute(params_from_iter(owned)).unwrap();
            stmt.execute(params_from_iter(borrowed_bindings([&platform,&account],row))).unwrap();
        }
        let mut query=db.prepare("SELECT platform,account,id,ts,body,note FROM t ORDER BY rowid").unwrap();
        let result=query.query_map([],|row|(0..6).map(|idx|row.get::<_,Sql>(idx)).collect::<rusqlite::Result<Vec<_>>>())
            .unwrap().collect::<rusqlite::Result<Vec<_>>>().unwrap();
        for pair in result.chunks_exact(2) {assert_eq!(pair[0],pair[1]);}
        assert_eq!(result.len(),6);
        drop(query);
        drop(stmt);
        db.execute_batch("CREATE TRIGGER reject_later BEFORE INSERT ON t WHEN new.id='stop'
            BEGIN SELECT RAISE(ABORT,'synthetic trigger rejection'); END").unwrap();
        let rejected=[Sql::Text("stop".into()),Sql::Integer(0),Sql::Null,Sql::Null];
        for borrowed in [false,true] {
            let tx=db.unchecked_transaction().unwrap();
            let mut stmt=tx.prepare(sql).unwrap();
            let execute=|row:&[Sql]| {
                if borrowed {
                    stmt.execute(params_from_iter(borrowed_bindings([&platform,&account],row)))
                } else {
                    let owned=std::iter::once(Sql::Text(platform.into()))
                        .chain(std::iter::once(Sql::Text(account.into()))).chain(row.iter().cloned());
                    stmt.execute(params_from_iter(owned))
                }
            };
            let mut execute=execute;
            execute(&data[0]).unwrap();
            let err=execute(&rejected).unwrap_err();
            assert!(matches!(&err,rusqlite::Error::SqliteFailure(code,Some(msg))
                if code.extended_code==rusqlite::ffi::SQLITE_CONSTRAINT_TRIGGER
                    && msg=="synthetic trigger rejection"));
            drop(stmt);
            drop(tx); // same implicit rollback as write_plan on an execution error
            let rows:i64=db.query_row("SELECT count(*) FROM t",[],|r|r.get(0)).unwrap();
            assert_eq!(rows,6);
        }
    }

    #[test]
    fn quote_projection_retains_only_canonical_decimal_text_keys() {
        let room=BTreeSet::from(["room".to_owned()]);
        let cols=|canonical:Sql,quote:Sql|vec![
            Sql::Text("message".into()),Sql::Text("room".into()),Sql::Null,Sql::Null,
            Sql::Integer(1),Sql::Text("text".into()),Sql::Null,Sql::Integer(0),canonical,quote];
        let mut row=cols(Sql::Text("123456".into()),Sql::Text("2468".into()));
        normalize("messages",&mut row,&room,&BTreeSet::new()).unwrap();
        let Sql::Text(payload)=&row[8] else {panic!("expected canonical metadata");};
        assert_eq!(serde_json::from_str::<Value>(payload).unwrap(),
            json!({"_hub_wechat_server_id":"123456","wechat_quote":{"target_server_id":"2468"}}));
        let mut row=cols(Sql::Text("012345".into()),Sql::Integer(1234));
        normalize("messages",&mut row,&room,&BTreeSet::new()).unwrap();
        assert_eq!(row[8],Sql::Null);
    }
}
