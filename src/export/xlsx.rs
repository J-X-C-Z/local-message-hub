//! Minimal fixed one-sheet OOXML generator. No formula, relationship or archive input.
use super::{Capped, Record, Result, StoreError, write_error};
use crate::stored_zip::StoredZip;
use std::{io::Write, ops::Range};

const CONTENT_TYPES:&[u8]=br#"<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>"#;
const ROOT_RELS:&[u8]=br#"<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>"#;
const WORKBOOK:&[u8]=r#"<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="聊天记录" sheetId="1" r:id="rId1"/></sheets></workbook>"#.as_bytes();
const WORKBOOK_RELS:&[u8]=br#"<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>"#;
const SHEET_BEGIN:&[u8]=br#"<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>"#;
const HEADERS:[&str;13]=[
    "时间","平台","账号","会话","发送者","发送者ID","类型","内容",
    "媒体链接","文件名","附件","会话ID","消息ID",
];

pub(super) struct Workbook {
    sheet: Capped,
    count_slot: Range<usize>,
}

impl Workbook {
    pub(super) fn new(limit:usize)->Result<Self> {
        let mut sheet=Capped::bounded(limit);
        sheet.write_all(SHEET_BEGIN).map_err(|_|write_error())?;
        row(&mut sheet,1,&["本地消息档案"])?;
        row(&mut sheet,2,&["导出工具","Local Message Hub"])?;
        sheet.write_all(br#"<row r="3">"#).map_err(|_|write_error())?;
        cell(&mut sheet,'A',3,"消息数")?;
        cell_start(&mut sheet,'B',3)?;
        // Fixed four-byte placeholder fits the existing max 1,000 message rows.
        let start=sheet.bytes.len();
        sheet.write_all(b"0000").map_err(|_|write_error())?;
        let end=sheet.bytes.len();
        sheet.write_all(b"</t></is></c></row>").map_err(|_|write_error())?;
        row(&mut sheet,4,&HEADERS)?;
        Ok(Self { sheet, count_slot:start..end })
    }

    pub(super) fn append(&mut self,index:usize,record:&Record)->Result<()> {
        row(&mut self.sheet,index+5,&[
            &record.timestamp_text,&record.platform,&record.account_id,
            record.conversation_title.as_deref().unwrap_or_default(),
            record.sender_name.as_deref().unwrap_or_default(),
            record.sender_id.as_deref().unwrap_or_default(),
            record.kind.as_deref().unwrap_or_default(),
            record.text.as_deref().unwrap_or_default(),
            "","","[]",&record.conversation_id,&record.id,
        ])
    }

    pub(super) fn finish(mut self,count:usize,limit:usize)->Result<Vec<u8>> {
        let count=count.to_string();
        if count.len()>self.count_slot.len(){return Err(write_error());}
        drop(self.sheet.bytes.splice(self.count_slot,count.bytes()));
        self.sheet.write_all(b"</sheetData></worksheet>").map_err(|_|write_error())?;
        let mut zip=StoredZip::xlsx(limit.min(super::MAX_OUTPUT));
        for (name,part) in [
            ("[Content_Types].xml",CONTENT_TYPES),
            ("_rels/.rels",ROOT_RELS),
            ("xl/workbook.xml",WORKBOOK),
            ("xl/_rels/workbook.xml.rels",WORKBOOK_RELS),
            ("xl/worksheets/sheet1.xml",self.sheet.bytes.as_slice()),
        ] {
            zip.push(name,part).map_err(|_|write_error())?;
        }
        zip.finish().map_err(|_|write_error())
    }
}

fn row(sheet:&mut Capped,index:usize,values:&[&str])->Result<()> {
    if values.len()>HEADERS.len(){return Err(write_error());}
    write!(sheet,"<row r=\"{index}\">").map_err(|_|write_error())?;
    for (col,value) in values.iter().enumerate() {
        cell(sheet,char::from(b'A'+col as u8),index,value)?;
    }
    sheet.write_all(b"</row>").map_err(|_|write_error())
}

fn cell_start(sheet:&mut Capped,col:char,index:usize)->Result<()> {
    write!(sheet,"<c r=\"{col}{index}\" t=\"inlineStr\"><is><t xml:space=\"preserve\">")
        .map_err(|_|write_error())
}

fn cell(sheet:&mut Capped,col:char,index:usize,value:&str)->Result<()> {
    if value.encode_utf16().take(32_768).count()>32_767 {return Err(write_error());}
    cell_start(sheet,col,index)?;
    let bytes=value.as_bytes();
    let mut start=0;
    for (offset,ch) in value.char_indices() {
        let escaped:&[u8]=match ch {
            '&'=>b"&amp;",
            '<'=>b"&lt;",
            '>'=>b"&gt;",
            '\r'=>b"&#13;", // Prevent XML line-ending normalization.
            '_' if xstring_escape(&bytes[offset..])=>b"_x005F_",
            '\t' | '\n' | '\u{20}'..='\u{d7ff}' | '\u{e000}'..='\u{fffd}' | '\u{10000}'..='\u{10ffff}'=>continue,
            _=>return Err(StoreError::BadQuery("XLSX cell contains invalid XML text")),
        };
        sheet.write_all(&bytes[start..offset]).map_err(|_|write_error())?;
        sheet.write_all(escaped).map_err(|_|write_error())?;
        start=offset+ch.len_utf8();
    }
    sheet.write_all(&bytes[start..]).map_err(|_|write_error())?;
    sheet.write_all(b"</t></is></c>").map_err(|_|write_error())
}

fn xstring_escape(bytes:&[u8])->bool {
    bytes.get(..7).is_some_and(|prefix|
        matches!(prefix[1],b'x'|b'X') && prefix[2..6].iter().all(u8::is_ascii_hexdigit)
            && prefix[6]==b'_')
}
