//! Deterministic bounded page inventory of one already authorized detached file.
use super::{Error,LegacyRow,NormalizedMessage,MAX_BATCH_BYTES,normalize_export_rows};
use serde::{Deserialize,Deserializer,Serialize,de::{self,SeqAccess,Visitor}};
use serde_json::value::RawValue;
use sha2::{Digest,Sha256};
use std::{fmt,marker::PhantomData};
use super::reply_export::{self,Displays,Display};

pub(crate) const MAX_BYTES:usize=4*1024*1024;
const MAX_ROWS:usize=1000;
const PER_PAGE:usize=100;
const MAX_PAGES:usize=64;

pub(crate) const REPLY_FORMAT:&str="legacy_hub_export_reply_display_page_v1";

struct Rows<T>(Vec<T>,Vec<usize>);
impl<'de,T:serde::de::DeserializeOwned> Deserialize<'de> for Rows<T> {
    fn deserialize<D:Deserializer<'de>>(d:D)->Result<Self,D::Error> {
        struct Bounded<T>(PhantomData<T>);
        impl<'de,T:serde::de::DeserializeOwned> Visitor<'de> for Bounded<T> {
            type Value=Rows<T>;
            fn expecting(&self,f:&mut fmt::Formatter<'_>)->fmt::Result {
                f.write_str("a bounded array of original WeChat export rows")
            }
            fn visit_seq<A:SeqAccess<'de>>(self,mut seq:A)->Result<Rows<T>,A::Error> {
                let mut rows=Vec::new();
                let mut lengths=Vec::new();
                while let Some(raw)=seq.next_element::<&'de RawValue>()? {
                    let size=raw.get().len();
                    if rows.len()==MAX_ROWS || size>MAX_BATCH_BYTES-2 {
                        return Err(de::Error::custom("export row budget exceeded"));
                    }
                    rows.push(serde_json::from_str(raw.get())
                        .map_err(|_|de::Error::custom("invalid original export row"))?);
                    lengths.push(size);
                }
                Ok(Rows(rows,lengths))
            }
        }
        d.deserialize_seq(Bounded::<T>(PhantomData))
    }
}

#[derive(Clone,Serialize)]
pub(crate) struct Detail {
    pub(crate) index:usize,
    rows:usize,
    raw_token_bytes:usize,
    normalized_bytes:usize,
    pub(crate) normalized_sha256:String,
    #[serde(skip_serializing_if="Option::is_none")]
    reply_display_rows:Option<usize>,
}
struct Page { detail:Detail, messages:Vec<NormalizedMessage>, replies:Displays }
pub(crate) struct Inventory { pub(crate) source_sha256:String, rows:usize, pages:Vec<Page>, replies:bool }

impl Inventory {
    pub(crate) fn parse(bytes:&[u8],account:&str)->Result<Self,Error> {
        Self::parse_mode(bytes,account,false)
    }

    pub(crate) fn parse_mode(bytes:&[u8],account:&str,replies:bool)->Result<Self,Error> {
        if bytes.len()>MAX_BYTES {return Err(Error::LegacyExport);}
        let (messages,mut displays,lengths)=if replies {
            let Rows(rows,lengths):Rows<reply_export::ExportRow>=serde_json::from_slice(bytes).map_err(|_|Error::LegacyExport)?;
            let (messages,displays)=reply_export::normalize(rows,account,MAX_ROWS)?;
            (messages,displays,lengths)
        } else {
            let Rows(rows,lengths):Rows<LegacyRow>=serde_json::from_slice(bytes).map_err(|_|Error::LegacyExport)?;
            (normalize_export_rows(rows,account,MAX_ROWS)?,Displays::new(),lengths)
        };
        let rows=messages.len();
        let mut pages=Vec::new();
        let mut page=Vec::new();
        let mut page_replies=Displays::new();
        let (mut raw_size,mut normalized_size)=(2usize,2usize);
        for (row,raw_row_size) in messages.into_iter().zip(lengths) {
            let display=displays.remove(&row.message_id);
            let row_size=wire_row(&row,display.as_ref(),replies)?.len();
            if row_size>MAX_BATCH_BYTES-2 {return Err(Error::LegacyExport);}
            let comma=if page.is_empty() {0} else {1};
            if page.len()==PER_PAGE || raw_size+comma+raw_row_size>MAX_BATCH_BYTES
                || normalized_size+comma+row_size>MAX_BATCH_BYTES {
                pages.push(finish(pages.len(),std::mem::take(&mut page),std::mem::take(&mut page_replies),raw_size,normalized_size,replies)?);
                (raw_size,normalized_size)=(2,2);
            }
            let comma=if page.is_empty() {0} else {1};
            raw_size+=comma+raw_row_size;
            normalized_size+=comma+row_size;
            if let Some(display)=display {page_replies.insert(row.message_id.clone(),display);}
            page.push(row);
        }
        if !page.is_empty() {pages.push(finish(pages.len(),page,page_replies,raw_size,normalized_size,replies)?);}
        if pages.len()>MAX_PAGES || !displays.is_empty() {return Err(Error::LegacyExport);}
        Ok(Self{source_sha256:hex::encode(Sha256::digest(bytes)),rows,pages,replies})
    }

    pub(crate) fn report(&self)->serde_json::Value {
        let pages=self.pages.iter().map(|p|&p.detail).collect::<Vec<_>>();
        serde_json::json!({"format":if self.replies {REPLY_FORMAT} else {"legacy_hub_export_page_v1"},"source_sha256":self.source_sha256,
            "rows":self.rows,"pages":pages})
    }

    pub(crate) fn select(self,index:usize)->Option<(Detail,Vec<NormalizedMessage>,Displays)> {
        let page=self.pages.into_iter().nth(index)?;
        Some((page.detail,page.messages,page.replies))
    }
}

fn wire_row(message:&NormalizedMessage,display:Option<&Display>,replies:bool)->Result<Vec<u8>,Error>{
    (if replies {serde_json::to_vec(&(message,display))} else {serde_json::to_vec(message)})
        .map_err(|_|Error::LegacyExport)
}

fn finish(index:usize,messages:Vec<NormalizedMessage>,displays:Displays,
    raw_token_bytes:usize,normalized_bytes:usize,replies:bool)->Result<Page,Error> {
    let encoded=(if replies {
        let pairs=messages.iter().map(|row|(row,displays.get(&row.message_id))).collect::<Vec<_>>();
        serde_json::to_vec(&pairs)
    } else {serde_json::to_vec(&messages)}).map_err(|_|Error::LegacyExport)?;
    if messages.is_empty() || encoded.len()!=normalized_bytes {return Err(Error::LegacyExport);}
    let detail=Detail{index,rows:messages.len(),raw_token_bytes,normalized_bytes,
        normalized_sha256:hex::encode(Sha256::digest(&encoded)),
        reply_display_rows:replies.then_some(displays.len())};
    Ok(Page{detail,messages,replies:displays})
}
