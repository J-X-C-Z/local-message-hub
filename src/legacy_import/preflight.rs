//! Stateless diagnostics for bounded stock JSON text records; not a staging API.
use super::{LegacyTextRow,OFFLINE_PRODUCER};
use crate::events::{IngestRequest,MAX_BATCH_BYTES};
use serde::{Deserialize,Deserializer,Serialize,de::{self,SeqAccess,Visitor}};
use serde_json::value::RawValue;
use std::{collections::BTreeSet,fmt,marker::PhantomData};

const MAX_ROWS:usize=100;
const MAX_SAMPLE:usize=5;

#[derive(Debug)]
pub(crate) enum Error { Format,Scope,Limit }

#[derive(Deserialize)]
struct ExactScope {platform:String,account_id:String}

struct Rows<R>(Vec<R>);
impl<'de,R:Deserialize<'de>> Deserialize<'de> for Rows<R> {
    fn deserialize<D:Deserializer<'de>>(data:D)->Result<Self,D::Error> {
        struct RowVisitor<R>(PhantomData<R>);
        impl<'de,R:Deserialize<'de>> Visitor<'de> for RowVisitor<R> {
            type Value=Rows<R>;
            fn expecting(&self,f:&mut fmt::Formatter<'_>)->fmt::Result {
                f.write_str("one bounded JSON message array")
            }
            fn visit_seq<S:SeqAccess<'de>>(self,mut seq:S)->Result<Rows<R>,S::Error> {
                let mut rows=Vec::new();
                while let Some(row)=seq.next_element::<R>()? {
                    if rows.len()==MAX_ROWS{return Err(de::Error::custom("too many preview rows"));}
                    rows.push(row);
                }
                Ok(Rows(rows))
            }
        }
        data.deserialize_seq(RowVisitor(PhantomData))
    }
}

fn read_rows(body:&[u8])->Result<Rows<&RawValue>,Error> {
    // The input slice remains alive for the complete preflight/staging parse.
    // Keep the exact JSON spans, rather than copying each record.
    if body.len()>MAX_BATCH_BYTES {return Err(Error::Limit);}
    serde_json::from_slice(body).map_err(|_|Error::Format)
}

// The strict submit uses the same bounded raw row reader, not diagnostic
// samples. Require an object and parse each typed row exactly once.
pub(super) fn strict_stock_rows(body:&[u8])->Result<Vec<LegacyTextRow>,Error> {
    let Rows(rows)=read_rows(body)?;
    if rows.is_empty() {return Err(Error::Format);}
    rows.into_iter().map(|raw| {
        if !raw.get().trim_start().starts_with('{') {return Err(Error::Format);}
        serde_json::from_str(raw.get()).map_err(|_|Error::Format)
    }).collect()
}

#[derive(Serialize)]
pub(crate) struct Report {
    total:usize,valid:usize,invalid:usize,errors:Vec<RowError>,
    platforms:Vec<String>,accounts:Vec<String>,sample:Vec<Sample>,
}
#[derive(Serialize)]
struct RowError {row:usize,error:&'static str}
#[derive(Serialize)]
struct Sample {
    platform:String,account_id:String,conversation_id:String,sender_name:String,
    timestamp:i64,#[serde(rename="type")] kind:&'static str,text:String,
}

pub(crate) fn preview(body:&[u8],platform:&str,account:&str)->Result<Report,Error> {
    let Rows(rows)=read_rows(body)?;
    // Validate scope for every object before producing any counts or samples.
    // A malformed/duplicate scope also fails the whole preview, without echo.
    for input in &rows {
        if !input.get().trim_start().starts_with('{'){continue;}
        let scope:ExactScope=serde_json::from_str(input.get()).map_err(|_|Error::Scope)?;
        if scope.platform!=platform||scope.account_id!=account{return Err(Error::Scope);}
    }
    let mut report=Report{total:rows.len(),valid:0,invalid:0,errors:Vec::new(),
        platforms:Vec::new(),accounts:Vec::new(),sample:Vec::new()};
    let mut seen=BTreeSet::new();
    let mut normalized_size=2; // array brackets; add commas one at a time
    for (index,input) in rows.into_iter().enumerate() {
        let row=if input.get().trim_start().starts_with('{') {
            serde_json::from_str::<LegacyTextRow>(input.get()).ok()
                .and_then(|row|row.normalize(platform,account).ok())
        } else {None};
        let Some(message)=row else {
            report.errors.push(RowError{row:index+1,error:"invalid_text_record"});
            continue;
        };
        let request=IngestRequest{idempotency_key:"legacy-json-preflight".into(),messages:vec![message]};
        if request.validate(OFFLINE_PRODUCER).is_err() {
            report.errors.push(RowError{row:index+1,error:"invalid_text_record"});
            continue;
        }
        let [message]=request.messages.as_slice() else {return Err(Error::Format);};
        if !seen.insert(message.message_id.clone()) {
            report.errors.push(RowError{row:index+1,error:"duplicate_message_id"});
            continue;
        }
        normalized_size+=serde_json::to_vec(message).map_err(|_|Error::Format)?.len()
            + if report.valid==0 {0} else {1};
        if normalized_size>MAX_BATCH_BYTES{return Err(Error::Limit);}
        report.valid+=1;
        if report.sample.len()<MAX_SAMPLE {
            report.sample.push(Sample{platform:platform.to_owned(),account_id:account.to_owned(),
                conversation_id:message.conversation_id.clone(),
                sender_name:prefix(message.sender_name.as_deref().unwrap_or(""),96),
                timestamp:message.timestamp,kind:"text",
                text:prefix(message.text.as_deref().unwrap_or(""),256)});
        }
    }
    report.invalid=report.total-report.valid;
    if report.valid!=0 {
        report.platforms.push(platform.to_owned());report.accounts.push(account.to_owned());
    }
    Ok(report)
}

fn prefix(value:&str,bytes:usize)->String {
    let mut end=value.len().min(bytes);
    while !value.is_char_boundary(end) {end-=1;}
    value[..end].to_owned()
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn raw_row_wire_spans_match_owned_reference() {
        let under=format!("[{}]",vec!["null";MAX_ROWS].join(","));
        let over=format!("[{}]",vec!["null";MAX_ROWS+1].join(","));
        let fixtures:&[&[u8]]=&[
            br#"[]"#,br#" [ { "a": 2, "a": null }, true, "a\u4e2d\n", -1.25e+1, [1] ]  "#,
            br#"[{"text":"\uD800"}]"#, b"[\"\xff\"]",b"[\"\x01\"]",
            b"[true,]",b"[true]{}", b"{\"a\":[]}",b"[[true]",under.as_bytes(),over.as_bytes(),
        ];
        for &body in fixtures {
            let owned=serde_json::from_slice::<Rows<Box<RawValue>>>(body)
                .map(|r|r.0.into_iter().map(|raw|raw.get().to_owned()).collect::<Vec<_>>());
            let borrowed=serde_json::from_slice::<Rows<&RawValue>>(body).map(|r| {
                let start=body.as_ptr() as usize;
                let end=start+body.len();
                r.0.into_iter().map(|raw| {
                    let span=raw.get();
                    let at=span.as_ptr() as usize;
                    assert!(at>=start&&at+span.len()<=end);
                    span.to_owned()
                }).collect::<Vec<_>>()
            });
            assert_eq!(borrowed.map_err(|e|e.to_string()),owned.map_err(|e|e.to_string()));
        }
    }

    #[test]
    fn large_preflight_keeps_scope_first_and_strict_rows_owned() {
        let mut records=Vec::new();
        for n in 0..8 {
            records.push(json!({"platform":"qq","account_id":"A","conversation_id":"group",
                "id":format!("test-{n}"),"sender_name":"示例","timestamp":1700000000,
                "is_self":false,"type":"text","text":"A".repeat(55*1024)}));
        }
        let bytes=serde_json::to_vec(&records).unwrap();
        assert!(bytes.len()>400*1024 && bytes.len()<MAX_BATCH_BYTES);
        let rows=strict_stock_rows(&bytes).unwrap();
        let report=serde_json::to_value(preview(&bytes,"qq","A").unwrap()).unwrap();
        assert_eq!(report["valid"],8);
        assert_eq!(report["sample"].as_array().unwrap().len(),5);
        assert_eq!(report["sample"][0]["text"].as_str().unwrap().len(),256);
        drop(bytes);
        assert_eq!(rows[7].text.len(),55*1024);
        records[0]["text"]=json!(false);
        records[7]["account_id"]=json!("foreign");
        let bytes=serde_json::to_vec(&records).unwrap();
        assert!(matches!(preview(&bytes,"qq","A"),Err(Error::Scope)));
        assert!(matches!(strict_stock_rows(&bytes),Err(Error::Format)));
        assert!(matches!(preview(&vec![b' ';MAX_BATCH_BYTES+1],"qq","A"),Err(Error::Limit)));
    }
}
