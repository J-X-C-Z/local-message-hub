//! Bounded text-only export of one normalized conversation, without filesystem I/O.
use crate::{
    plugins::AccountScope,
    store::{Query, Result, Store, StoreError, exact_message_filter_sql},
};
use chrono::{DateTime, Datelike, FixedOffset};
use rusqlite::{Connection, OptionalExtension, params, params_from_iter, types::Value as Sql};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{collections::BTreeSet, io::{self, Write}};

pub(crate) mod search;
mod sql;
mod xlsx;

pub(crate) const MAX_ROWS: usize = 1_000;
pub(crate) const MAX_OUTPUT: usize = 4 * 1024 * 1024;
const MAX_CELL: usize = 64 * 1024;
const MD_HEADER: &[u8] = b"# Local Message Hub export\n\n";
const CSV_HEADER: &str = "时间,平台,账号,会话,发送者,发送者ID,类型,内容,媒体链接,文件名,附件,会话ID,消息ID\r\n";

#[derive(Clone, Copy, Default, Deserialize)]
#[serde(rename_all = "lowercase")]
pub(crate) enum Format {
    #[default]
    Json,
    Csv,
    #[serde(alias = "text")]
    Txt,
    #[serde(alias = "markdown")]
    Md,
    Html,
    Chatlab,
    #[serde(rename = "chatlab-jsonl")]
    ChatlabJsonl,
    #[serde(skip_deserializing)]
    Sql,
    #[serde(skip_deserializing)]
    Xlsx,
}

impl Format {
    pub(crate) fn content_type(self) -> &'static str {
        match self {
            Self::Json => "application/json; charset=utf-8",
            Self::Csv => "text/csv; charset=utf-8",
            Self::Txt => "text/plain; charset=utf-8",
            Self::Md => "text/markdown; charset=utf-8",
            Self::Html => "text/html; charset=utf-8",
            Self::Sql => "application/sql; charset=utf-8",
            Self::Xlsx => "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            Self::Chatlab => "application/json; charset=utf-8",
            Self::ChatlabJsonl => "application/x-ndjson; charset=utf-8",
        }
    }

    pub(crate) fn disposition(self) -> &'static str {
        match self {
            Self::Json => "attachment; filename=\"messages.json\"",
            Self::Csv => "attachment; filename=\"messages.csv\"",
            Self::Txt => "attachment; filename=\"messages.txt\"",
            Self::Md => "attachment; filename=\"messages.md\"",
            Self::Html => "attachment; filename=\"messages.html\"",
            Self::Sql => "attachment; filename=\"export.sql\"",
            Self::Xlsx => "attachment; filename=\"export.xlsx\"",
            Self::Chatlab => "attachment; filename=\"export.chatlab.json\"",
            Self::ChatlabJsonl => "attachment; filename=\"export.chatlab.jsonl\"",
        }
    }
}

impl Format {
    fn chatlab(self) -> bool { matches!(self, Self::Chatlab | Self::ChatlabJsonl) }
    pub(crate) fn html(self) -> bool { matches!(self, Self::Html) }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct ExportQuery {
    #[serde(default)]
    pub platform: String,
    #[serde(default)]
    pub account_id: String,
    pub conversation_id: String,
    #[serde(default)]
    pub format: Format,
    pub start: Option<i64>,
    pub end: Option<i64>,
}

pub(crate) const FILTERED_PATH: &str = "/api/v1/messages/export/filtered";

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct FilteredQuery {
    #[serde(default)]
    pub platform: String,
    #[serde(default)]
    pub account_id: String,
    pub conversation_id: String,
    #[serde(default, deserialize_with = "filtered_format")]
    pub format: Format,
    #[serde(default)]
    q: String,
    #[serde(default, rename = "type")]
    message_type: String,
    #[serde(default)]
    start: String,
    #[serde(default)]
    end: String,
}

fn filtered_format<'de,D:serde::Deserializer<'de>>(deserializer:D)->std::result::Result<Format,D::Error> {
    let value=String::deserialize(deserializer)?;
    match value.as_str() {
        "sql" => Ok(Format::Sql),
        "xlsx" => Ok(Format::Xlsx),
        _ => Format::deserialize(serde::de::value::StringDeserializer::<D::Error>::new(value)),
    }
}

#[derive(Serialize)]
struct Record {
    id: String,
    platform: String,
    account_id: String,
    conversation_id: String,
    conversation_title: Option<String>,
    sender_id: Option<String>,
    sender_name: Option<String>,
    timestamp: i64,
    #[serde(rename = "type")]
    kind: Option<String>,
    text: Option<String>,
    is_self: bool,
    timestamp_text: String,
}

pub(crate) struct Capped {
    pub(crate) bytes: Vec<u8>,
    limit: usize,
}

impl Default for Capped {
    fn default() -> Self { Self::bounded(MAX_OUTPUT) }
}

impl Capped {
    fn bounded(limit: usize) -> Self {
        Self { bytes: Vec::new(), limit: limit.min(MAX_OUTPUT) }
    }
}

impl Write for Capped {
    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        if bytes.len() > self.limit - self.bytes.len() {
            return Err(io::Error::other("export limit"));
        }
        self.bytes.extend_from_slice(bytes);
        Ok(bytes.len())
    }

    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

fn write_error() -> StoreError {
    StoreError::TooLarge
}

fn time_text(timestamp: i64) -> Result<String> {
    if timestamp == 0 {
        return Ok(String::new());
    }
    let zone = FixedOffset::east_opt(8 * 3600).expect("valid fixed zone");
    let local = DateTime::from_timestamp(timestamp, 0)
        .and_then(|d| d.naive_utc().checked_add_offset(zone))
        .filter(|d| (1..=9999).contains(&d.year()))
        .ok_or(StoreError::BadQuery("timestamp outside export calendar range"))?;
    Ok(local.format("%Y-%m-%d %H:%M:%S").to_string())
}

pub(crate) fn csv_cell(out: &mut Capped, value: &str) -> io::Result<()> {
    let harmful = |c: Option<char>| c.is_some_and(|c| matches!(c, '=' | '+' | '-' | '@'));
    let prefix = harmful(value.chars().next())
        || value.starts_with(['\t', '\r'])
        || harmful(value.trim_start_matches(|c: char| c.is_whitespace() || c.is_control() || c == '\u{feff}').chars().next());
    let quote = value.bytes().any(|b| matches!(b, b',' | b'"' | b'\r' | b'\n'));
    if quote { out.write_all(b"\"")?; }
    if prefix { out.write_all(b"'")?; }
    let (first, rest) = value.split_once('"').unwrap_or((value, ""));
    out.write_all(first.as_bytes())?;
    if first.len() != value.len() {
        for piece in rest.split('"') {
            out.write_all(b"\"\"")?;
            out.write_all(piece.as_bytes())?;
        }
    }
    if quote { out.write_all(b"\"")?; }
    Ok(())
}

fn csv_row(out: &mut Capped, row: &Record) -> io::Result<()> {
    let values: [&str; 13] = [
        &row.timestamp_text, &row.platform, &row.account_id,
        row.conversation_title.as_deref().unwrap_or_default(),
        row.sender_name.as_deref().unwrap_or_default(),
        row.sender_id.as_deref().unwrap_or_default(),
        row.kind.as_deref().unwrap_or_default(),
        row.text.as_deref().unwrap_or_default(),
        "", "", "[]", &row.conversation_id, &row.id,
    ];
    for (i, cell) in values.iter().enumerate() {
        if i != 0 { out.write_all(b",")?; }
        csv_cell(out, cell)?;
    }
    out.write_all(b"\r\n")
}

/// Maximum JSON row bytes including its separator, all pretty whitespace, keys,
/// and scalar values. A UTF-8 byte expands to at most six bytes (a C0 \uNNNN).
fn json_row_size_bound(row: &Record) -> usize {
    // Destructure exhaustively so changing the serialized schema requires revisiting
    // the bound. 512 covers the fixed syntax, quotes/nulls, i64 and boolean.
    let Record {
        id, platform, account_id, conversation_id, conversation_title, sender_id,
        sender_name, timestamp: _, kind, text, is_self: _, timestamp_text,
    } = row;
    [
        Some(id), Some(platform), Some(account_id), Some(conversation_id),
        conversation_title.as_ref(), sender_id.as_ref(), sender_name.as_ref(),
        kind.as_ref(), text.as_ref(), Some(timestamp_text),
    ].into_iter().flatten().fold(512usize, |bound, text|
        bound.saturating_add(text.len().saturating_mul(6)))
}

fn write_pretty_json_row<W: Write>(out: &mut W, row: &Record, n: usize) -> Result<()> {
    use serde_json::ser::{Formatter, PrettyFormatter};
    let mut formatter = PrettyFormatter::new();
    // The export writes its own opening bracket. Set the formatter's depth to one
    // without writing a second bracket, then stream exactly one pretty array value.
    formatter.begin_array(&mut io::sink()).map_err(|_| write_error())?;
    formatter.begin_array_value(out, n == 0).map_err(|_| write_error())?;
    row.serialize(&mut serde_json::Serializer::with_formatter(out, formatter))
        .map_err(|e| if e.is_io() {write_error()}
            else {StoreError::BadQuery("could not serialize export")})
}

fn json_row(out: &mut Capped, row: &Record, n: usize) -> Result<()> {
    if json_row_size_bound(row) <= out.limit - out.bytes.len() {
        // Fast-path serde's Vec writer, avoiding a bound check per escape fragment.
        let start = out.bytes.len();
        write_pretty_json_row(&mut out.bytes, row, n)?;
        // Preserve the API cap if the schema/formatter changes the assumed bound.
        if out.bytes.len() > out.limit {
            out.bytes.truncate(start);
            return Err(write_error());
        }
        Ok(())
    } else {
        // A conservative bound can overestimate; near the limit enforce exact bytes.
        write_pretty_json_row(out, row, n)
    }
}

fn readable(out: &mut Capped, s: &str, html: bool) -> io::Result<()> {
    let bytes = s.as_bytes();
    let mut start = 0;
    for (offset, c) in s.char_indices() {
        let escape: Option<&[u8]> = match c {
            '&' if html => Some(b"&amp;"),
            '<' if html => Some(b"&lt;"),
            '>' if html => Some(b"&gt;"),
            '"' if html => Some(b"&quot;"),
            '\'' if html => Some(b"&#39;"),
            // Retain line breaks and tabs; render other terminal/bidi controls literally.
            c if (c.is_control() && !matches!(c, '\n' | '\t'))
                || matches!(c, '\u{202a}'..='\u{202e}' | '\u{2066}'..='\u{2069}') => None,
            _ => continue,
        };
        // Only escaped scalars interrupt a contiguous UTF-8 run.
        out.write_all(&bytes[start..offset])?;
        if let Some(replacement) = escape {
            out.write_all(replacement)?;
        } else {
            write!(out, "\\u{{{:x}}}", c as u32)?;
        }
        start = offset + c.len_utf8();
    }
    out.write_all(&bytes[start..])
}

fn human_row(out: &mut Capped, row: &Record, format: Format) -> io::Result<()> {
    let heading = format!("{} {} [{}]", row.timestamp_text,
        row.sender_name.as_deref().or(row.sender_id.as_deref()).unwrap_or("未知"),row.id);
    let text = row.text.as_deref().unwrap_or_default();
    if format.html() {
        out.write_all(b"<article><p>")?;
        readable(out,&heading,true)?;
        out.write_all(b"</p><pre>")?;
        readable(out,text,true)?;
        out.write_all(b"</pre></article>\n")
    } else if matches!(format,Format::Md) {
        // Exactly indent every untrusted line, even blank lines and headings:
        // no link, HTML, image, fence or heading syntax can escape this block.
        for value in [heading.as_str(), text] {
            for line in value.split('\n') {
                out.write_all(b"    ")?;
                readable(out,line,false)?;
                out.write_all(b"\n")?;
            }
        }
        out.write_all(b"\n")
    } else {
        readable(out,&heading,false)?;
        out.write_all(b"\n")?;
        readable(out,text,false)?;
        out.write_all(b"\n\n")
    }
}

pub(crate) fn json_value(out: &mut Capped, value: &Value) -> Result<()> {
    serde_json::to_writer(out,value).map_err(|e| if e.is_io() {write_error()}
        else {StoreError::BadQuery("could not serialize export")})
}

struct Chatlab {
    meta: Value,
    format: Format,
    members: Capped,
    messages: Capped,
    seen: BTreeSet<String>,
}

impl Chatlab {
    fn new(title: Option<String>, kind: Option<String>, scope: &AccountScope, format: Format) -> Result<Self> {
        let kind = match kind.as_deref().map(str::to_ascii_lowercase).as_deref() {
            Some("group") => "group",
            Some("private" | "direct") => "private",
            _ => return Err(StoreError::BadQuery("unsupported ChatLab conversation kind")),
        };
        Ok(Self{
            meta:json!({"name":title.filter(|s|!s.is_empty()).unwrap_or_else(||"本地消息档案".into()),
                "platform":"im","type":kind,"sourcePlatforms":[scope.platform]}),
            format,members:Capped::default(),messages:Capped::default(),seen:BTreeSet::new(),
        })
    }

    fn append(&mut self, row: &Record, index: usize) -> Result<()> {
        let line_format=matches!(self.format,Format::ChatlabJsonl);
        let id=row.sender_id.as_deref().filter(|id|!id.is_empty());
        let name=row.sender_name.as_deref().filter(|name|!name.is_empty()).or(id).unwrap_or("未知");
        if let Some(id)=id.filter(|id|!self.seen.contains(*id)) {
            let mut member=json!({"platformId":id,"accountName":name,"platform":row.platform,"accountId":row.account_id});
            if line_format {member["_type"]=json!("member");}
            if !line_format && !self.seen.is_empty() {self.members.write_all(b",").map_err(|_|write_error())?;}
            json_value(&mut self.members,&member)?;
            if line_format {self.members.write_all(b"\n").map_err(|_|write_error())?;}
            self.seen.insert(id.to_owned());
        }
        let kind = match row.kind.as_deref() {
            Some("text") => 0,
            Some("system") => 80,
            // The subset never exports media references; don't silently replace their type.
            _ => return Err(StoreError::BadQuery("unsupported ChatLab message type")),
        };
        let mut message=json!({"sender":id.unwrap_or(name),"accountName":name,"timestamp":row.timestamp,
            "type":kind,"content":row.text.as_deref().filter(|text|!text.is_empty()),
            "platform":row.platform,"accountId":row.account_id,"conversationId":row.conversation_id,
            "platformMessageId":row.id});
        if line_format {message["_type"]=json!("message");}
        if !line_format && index>0 {self.messages.write_all(b",").map_err(|_|write_error())?;}
        json_value(&mut self.messages,&message)?;
        if line_format {self.messages.write_all(b"\n").map_err(|_|write_error())?;}
        if self.members.bytes.len()+self.messages.bytes.len()>MAX_OUTPUT {return Err(StoreError::TooLarge);}
        Ok(())
    }

    fn finish(self, out: &mut Capped) -> Result<()> {
        let exported_at=std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH)
            .map_err(|_|StoreError::BadQuery("system clock outside export calendar"))?.as_secs();
        let clock=json!({"version":"0.0.2","exportedAt":exported_at,"generator":"Local Message Hub Rust"});
        let member=self.members.bytes;
        let records=self.messages.bytes;
        if matches!(self.format,Format::ChatlabJsonl) {
            json_value(out,&json!({"_type":"header","chatlab":clock,"meta":self.meta}))?;
            out.write_all(b"\n").map_err(|_|write_error())?;
            out.write_all(&member).map_err(|_|write_error())?;
            out.write_all(&records).map_err(|_|write_error())?;
        } else {
            out.write_all(b"{\"chatlab\":").map_err(|_|write_error())?;
            json_value(out,&clock)?;
            out.write_all(b",\"meta\":").map_err(|_|write_error())?;
            json_value(out,&self.meta)?;
            out.write_all(b",\"members\":[").map_err(|_|write_error())?;
            out.write_all(&member).map_err(|_|write_error())?;
            out.write_all(b"],\"messages\":[").map_err(|_|write_error())?;
            out.write_all(&records).map_err(|_|write_error())?;
            out.write_all(b"]}").map_err(|_|write_error())?;
        }
        Ok(())
    }
}

pub(crate) fn render(store: Store, scope: AccountScope, request: ExportQuery) -> Result<Option<Vec<u8>>> {
    let mut conn = store.connection()?;
    let tx = conn.transaction()?;
    Ok(render_in(&tx, &scope, &request, MAX_ROWS, MAX_OUTPUT, None)?.map(|(bytes, _)| bytes))
}

pub(crate) fn render_filtered(store:Store,scope:AccountScope,request:FilteredQuery)
    ->Result<Option<Vec<u8>>> {
    let mut conn=store.connection()?;
    let tx=conn.transaction()?;
    let FilteredQuery{conversation_id,format,q,message_type,start,end,..}=request;
    let (where_sql,values)=exact_message_filter_sql(&tx,&Query{
        platform:scope.platform.clone(),
        account_id:scope.account_id.clone(),
        conversation_id:conversation_id.clone(),
        q,kind_type:message_type,start,end,account_scoped:true,
        ..Query::default()
    })?;
    let selection=ExportQuery{platform:scope.platform.clone(),account_id:scope.account_id.clone(),
        conversation_id,format,start:None,end:None};
    Ok(render_in(&tx,&scope,&selection,MAX_ROWS,MAX_OUTPUT,Some((where_sql,values)))?
        .map(|(bytes,_)|bytes))
}

// Reuse the ordinary serializers on an already-pinned database view. An
// enclosing transaction lets scheduled multi-conversation exports see the
// same snapshot, including the preset and scope checks.
fn render_in(tx: &Connection, scope: &AccountScope, request: &ExportQuery,
             row_limit: usize, byte_limit: usize, filtered:Option<(String,Vec<Sql>)>) -> Result<Option<(Vec<u8>,usize)>> {
    if request.conversation_id.trim().is_empty() || request.conversation_id.len() > 256
        || request.conversation_id.chars().any(char::is_control)
        || matches!((request.start, request.end), (Some(start), Some(end)) if start > end)
    {
        return Err(StoreError::BadQuery("invalid export selection"));
    }
    let exact_filtered=filtered.is_some();
    if !exact_filtered && matches!(request.format, Format::Sql | Format::Xlsx) {
        return Err(StoreError::BadQuery("format requires filtered export"));
    }
    let selected=if exact_filtered {
        let mut lookup=tx.prepare(
            "SELECT CASE WHEN ?4 THEN substr(title,1,?5) END, CASE WHEN ?4 THEN substr(kind,1,128) END
             FROM conversations WHERE platform COLLATE BINARY=?1 AND account_id COLLATE BINARY=?2
             AND id COLLATE BINARY=?3 LIMIT 2")?;
        let mut matches=lookup.query(params![
            scope.platform,scope.account_id,request.conversation_id,request.format.chatlab(),(MAX_CELL+1) as i64,
        ])?;
        let selected=matches.next()?.map(|row| Ok::<_,rusqlite::Error>((
            row.get::<_,Option<String>>(0)?,row.get::<_,Option<String>>(1)?,
        ))).transpose()?;
        if matches.next()?.is_some() {
            return Err(StoreError::BadQuery("ambiguous export conversation"));
        }
        selected
    } else {
        tx.query_row(
        "SELECT CASE WHEN ?4 THEN substr(title,1,?5) END, CASE WHEN ?4 THEN substr(kind,1,128) END
         FROM conversations WHERE platform=?1 AND account_id=?2 AND id=?3",
            params![scope.platform,scope.account_id,request.conversation_id,request.format.chatlab(),(MAX_CELL+1) as i64],
            |row| Ok((row.get::<_,Option<String>>(0)?,row.get::<_,Option<String>>(1)?)),
        ).optional()?
    };
    let Some((title,kind))=selected else {return Ok(None);};
    if title.as_ref().is_some_and(|t|t.len()>MAX_CELL) {return Err(StoreError::TooLarge);}
    let mut chatlab=if request.format.chatlab() {Some(Chatlab::new(title,kind,scope,request.format)?)} else {None};
    const PROJECTION:&str="SELECT m.id,c.title,m.sender_id,m.sender_name,m.timestamp,m.type,m.text,m.is_self
         FROM messages m JOIN conversations c
           ON c.platform COLLATE BINARY=m.platform AND c.account_id COLLATE BINARY=m.account_id
              AND c.id COLLATE BINARY=m.conversation_id";
    let mut query=if let Some((where_sql,_))=filtered.as_ref() {
        tx.prepare(&format!(
            "{PROJECTION} {where_sql} ORDER BY m.timestamp ASC,m.id COLLATE BINARY ASC LIMIT ?"))?
    } else {
        tx.prepare(
            "SELECT m.id,c.title,m.sender_id,m.sender_name,m.timestamp,m.type,m.text,m.is_self
             FROM messages m JOIN conversations c
               ON c.platform=m.platform AND c.account_id=m.account_id AND c.id=m.conversation_id
             WHERE m.platform=?1 AND m.account_id=?2 AND m.conversation_id=?3
               AND m.timestamp BETWEEN ?4 AND ?5
             ORDER BY m.timestamp ASC,m.id COLLATE BINARY ASC LIMIT ?6",
        )?
    };
    let mut rows=if let Some((_,mut values))=filtered {
        values.push(Sql::Integer((row_limit+1) as i64));
        query.query(params_from_iter(values.iter()))?
    } else {
        query.query(params![
            scope.platform, scope.account_id, request.conversation_id,
            request.start.unwrap_or(i64::MIN), request.end.unwrap_or(i64::MAX), row_limit+1,
        ])?
    };
    let mut out = Capped::bounded(byte_limit);
    let mut workbook=if matches!(request.format,Format::Xlsx) {
        Some(xlsx::Workbook::new(byte_limit)?)
    } else {None};
    match request.format {
        Format::Json => out.write_all(b"[").map_err(|_| write_error())?,
        Format::Csv => out.write_all(CSV_HEADER.as_bytes()).map_err(|_| write_error())?,
        Format::Md => out.write_all(MD_HEADER).map_err(|_|write_error())?,
        Format::Sql => out.write_all(sql::HEADER).map_err(|_|write_error())?,
        Format::Html => out.write_all(concat!("<!doctype html><html><head><meta charset=\"utf-8\">",
            "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; form-action 'none'; base-uri 'none'\">",
            "<meta name=\"referrer\" content=\"no-referrer\"><title>Local Message Hub export</title></head><body>",
            "<main><h1>Local Message Hub export</h1>\n").as_bytes()).map_err(|_|write_error())?,
        _ => {}
    }
    let mut n = 0;
    let mut exact_ids = BTreeSet::new();
    while let Some(row) = rows.next()? {
        if n == row_limit { return Err(StoreError::TooLarge); }
        for column in [0, 1, 2, 3, 5, 6] {
            if let rusqlite::types::ValueRef::Text(bytes) = row.get_ref(column)? {
                if bytes.len() > MAX_CELL { return Err(StoreError::TooLarge); }
            }
        }
        let timestamp = row.get(4)?;
        let record = Record {
            id: row.get(0)?,
            platform: scope.platform.clone(),
            account_id: scope.account_id.clone(),
            conversation_id: request.conversation_id.clone(),
            conversation_title: row.get(1)?,
            sender_id: row.get(2)?,
            sender_name: row.get(3)?,
            timestamp,
            kind: row.get(5)?,
            text: row.get(6)?,
            is_self: row.get(7)?,
            timestamp_text: time_text(timestamp)?,
        };
        if exact_filtered && !exact_ids.insert(record.id.clone()) {
            return Err(StoreError::BadQuery("ambiguous export message"));
        }
        match request.format {
            Format::Json => json_row(&mut out, &record, n)?,
            Format::Csv => csv_row(&mut out, &record).map_err(|_| write_error())?,
            Format::Sql => sql::row(&mut out,&record)?,
            Format::Xlsx => workbook.as_mut().expect("initialized workbook").append(n,&record)?,
            Format::Txt | Format::Md | Format::Html => human_row(&mut out,&record,request.format).map_err(|_|write_error())?,
            Format::Chatlab | Format::ChatlabJsonl => chatlab.as_mut().expect("initialized ChatLab output").append(&record,n)?,
        }
        n += 1;
    }
    match request.format {
        Format::Json => out.write_all(if n == 0 { b"]" } else { b"\n]" }).map_err(|_| write_error())?,
        Format::Html => out.write_all(b"</main></body></html>\n").map_err(|_|write_error())?,
        Format::Sql => out.write_all(sql::END).map_err(|_|write_error())?,
        _ => {}
    }
    if let Some(chatlab)=chatlab {chatlab.finish(&mut out)?;}
    if let Some(workbook)=workbook{return Ok(Some((workbook.finish(n,byte_limit)?,n)));}
    Ok(Some((out.bytes,n)))
}

/// Scheduled preset formats only. The result is one deterministic text
/// artifact with one overall row/byte limit, not one quota per conversation.
pub(crate) fn render_selection(
    tx: &Connection, scope: &AccountScope, ids: Vec<String>, format: Format,
    start: Option<i64>, end: Option<i64>, max_bytes: usize,
) -> Result<Option<Vec<u8>>> {
    if !(1..=20).contains(&ids.len())
        || !matches!(format,Format::Json|Format::Csv|Format::Txt|Format::Md)
        || !(1..=MAX_OUTPUT).contains(&max_bytes) {
        return Err(StoreError::BadQuery("invalid scheduled export"));
    }
    let mut output=Capped::bounded(max_bytes);
    let mut remaining=MAX_ROWS;
    match format {
        Format::Json => output.write_all(b"[").map_err(|_|write_error())?,
        Format::Csv => output.write_all(CSV_HEADER.as_bytes()).map_err(|_|write_error())?,
        Format::Md => output.write_all(MD_HEADER).map_err(|_|write_error())?,
        _=>(),
    }
    let mut json_nonempty=false;
    for (index,id) in ids.into_iter().enumerate() {
        let request=ExportQuery { platform:scope.platform.clone(),account_id:scope.account_id.clone(),
            conversation_id:id, format,start,end };
        let Some((body,count))=render_in(tx,scope,&request,remaining,max_bytes,None)? else {return Ok(None);};
        remaining-=count;
        let mut write=|| -> io::Result<()> {
            match format {
                Format::Json if count>0=>{
                    if json_nonempty{output.write_all(b",")?;}
                    output.write_all(&body[1..body.len()-1])?;
                    json_nonempty=true;
                }
                Format::Csv=>output.write_all(&body[CSV_HEADER.len()..])?,
                Format::Txt|Format::Md=>{
                    if matches!(format,Format::Md) {
                        writeln!(output,"## Conversation {}\n",index+1)?;
                        output.write_all(&body[MD_HEADER.len()..])?;
                    } else {
                        writeln!(output,"Conversation {}\n",index+1)?;
                        output.write_all(&body)?;
                    }
                    output.write_all(b"\n")?;
                }
                _=>(),
            }
            Ok(())
        };
        write().map_err(|_|write_error())?;
    }
    if matches!(format,Format::Json) {output.write_all(b"]").map_err(|_|write_error())?;}
    Ok(Some(output.bytes))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn json_export_stream_matches_buffered_pretty_bytes_and_cap() {
        let row = Record {
            id: "synthetic".into(),
            platform: "wechat".into(),
            account_id: "fixture".into(),
            conversation_id: "room".into(),
            conversation_title: None,
            sender_id: Some("测试 🧪".into()),
            sender_name: Some("؜ redacted".into()),
            timestamp: i64::MIN,
            kind: Some("text".into()),
            text: Some((0..=0x9f).filter_map(char::from_u32).collect()),
            is_self: true,
            timestamp_text: String::new(),
        };
        for n in [0, 1] {
            let previous = serde_json::to_vec_pretty(&row).unwrap();
            let mut expected = Vec::from(if n == 0 {&b"\n"[..]} else {&b",\n"[..]});
            for (i, line) in previous.split(|byte| *byte == b'\n').enumerate() {
                if i != 0 { expected.extend_from_slice(b"\n"); }
                expected.extend_from_slice(b"  ");
                expected.extend_from_slice(line);
            }
            for limit in (1..=expected.len()+2).chain([
                json_row_size_bound(&row), json_row_size_bound(&row)+1, MAX_OUTPUT,
            ]) {
                let mut out = Capped::bounded(limit);
                out.write_all(b"[").unwrap();
                let got = json_row(&mut out, &row, n);
                if limit > expected.len() {
                    got.unwrap();
                    assert_eq!(&out.bytes[1..], expected, "row {n}, limit {limit}");
                } else {
                    assert!(matches!(got, Err(StoreError::TooLarge)), "row {n}, limit {limit}");
                }
            }
        }
    }

    #[test]
    fn readable_export_matches_scalar_projection_at_every_byte_limit() {
        fn previous(out: &mut Capped, s: &str, html: bool) -> io::Result<()> {
            for c in s.chars() {
                match c {
                    '&' if html => out.write_all(b"&amp;")?,
                    '<' if html => out.write_all(b"&lt;")?,
                    '>' if html => out.write_all(b"&gt;")?,
                    '"' if html => out.write_all(b"&quot;")?,
                    '\'' if html => out.write_all(b"&#39;")?,
                    c if (c.is_control() && !matches!(c, '\n' | '\t'))
                        || matches!(c, '\u{202a}'..='\u{202e}' | '\u{2066}'..='\u{2069}') =>
                        write!(out, "\\u{{{:x}}}", c as u32)?,
                    c => out.write_all(c.encode_utf8(&mut [0;4]).as_bytes())?,
                }
            }
            Ok(())
        }
        let controls: String = (0..=0x9f).filter_map(char::from_u32).collect();
        let adversarial = format!(
            "{controls}\u{202a}\u{202b}\u{202c}\u{202d}\u{202e}\u{2066}\u{2067}\u{2068}\u{2069} &<>\"' 🧪测试\n\t尾"
        );
        for selection in ["", "plain text 中文 with 𐐷", adversarial.as_str()] {
            for html in [false, true] {
                let mut expected = Capped::default();
                previous(&mut expected, selection, html).unwrap();
                for limit in 0..=expected.bytes.len()+4 {
                    // Seed the output prefix so both forms honor remaining capacity.
                    let mut a = Capped::bounded(limit+1);
                    let mut b = Capped::bounded(limit+1);
                    a.write_all(b"#").unwrap();
                    b.write_all(b"#").unwrap();
                    let a_result = previous(&mut a, selection, html);
                    let b_result = readable(&mut b, selection, html);
                    assert_eq!(a_result.is_ok(), b_result.is_ok(), "html={html}, limit={limit}");
                    if a_result.is_ok() { assert_eq!(a.bytes, b.bytes); }
                }
            }
        }
    }

    #[test]
    fn csv_text_prefixes_triggers_after_extended_whitespace_but_keeps_literals() {
        for (value, expected) in [
            ("\u{00a0}=2+2", "'\u{00a0}=2+2"),
            ("\u{feff}+1", "'\u{feff}+1"),
            ("\tplain", "'\tplain"),
            ("a+b", "a+b"),
            ("plain \"quote\"", "\"plain \"\"quote\"\"\""),
        ] {
            let mut output = Capped::default();
            csv_cell(&mut output, value).unwrap();
            assert_eq!(output.bytes, expected.as_bytes());
        }
    }
}
