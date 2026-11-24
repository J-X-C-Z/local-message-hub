//! Text-only export of an explicit literal search under one exact local account.
use super::{Capped, Format, MAX_CELL, MAX_ROWS, Record, CSV_HEADER, csv_row, json_row, time_text, write_error};
use crate::{
    plugins::AccountScope,
    store::{Result, Store, StoreError, exact_search_export_filter_sql, valid_search_export_keyword},
};
use rusqlite::{params_from_iter, types::{Value as Sql, ValueRef}};
use serde::Deserialize;
use std::{collections::BTreeSet, io::Write};

pub(crate) const PATH: &str = "/api/v1/messages/export/search";

#[derive(Clone, Copy, Default, Deserialize)]
#[serde(rename_all = "lowercase")]
pub(crate) enum SearchFormat {
    #[default]
    Json,
    Csv,
}

impl From<SearchFormat> for Format {
    fn from(value: SearchFormat) -> Self {
        match value {
            SearchFormat::Json => Self::Json,
            SearchFormat::Csv => Self::Csv,
        }
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Selection {
    q: String,
    #[serde(default)]
    pub(crate) format: SearchFormat,
}

impl Selection {
    pub(crate) fn valid(&self) -> bool { valid_search_export_keyword(&self.q) }
}

pub(crate) fn render(store: Store, scope: AccountScope, request: Selection) -> Result<Vec<u8>> {
    let mut db = store.connection()?;
    let tx = db.transaction()?;
    let (where_sql, mut values) = exact_search_export_filter_sql(&tx, &scope, &request.q)?;
    values.push(Sql::Integer((MAX_ROWS + 1) as i64));
    // Explicit binary scope on both sides of the join. LEFT JOIN retains
    // a matching message even if optional conversation display data is absent.
    // Read and serialize full text in this one pinned read transaction.
    let mut statement = tx.prepare(&format!(
        "SELECT m.id,m.conversation_id,c.title,m.sender_id,m.sender_name,
                m.timestamp,m.type,m.text,m.is_self
         FROM messages m LEFT JOIN conversations c
           ON c.platform COLLATE BINARY=m.platform
          AND c.account_id COLLATE BINARY=m.account_id
          AND c.id COLLATE BINARY=m.conversation_id
         {where_sql}
         ORDER BY m.timestamp DESC,m.id COLLATE BINARY DESC,m.conversation_id COLLATE BINARY DESC LIMIT ?"
    ))?;
    let mut rows = statement.query(params_from_iter(values.iter()))?;
    let mut out = Capped::default();
    let mut identities = BTreeSet::new();
    match request.format {
        SearchFormat::Json => out.write_all(b"[").map_err(|_| write_error())?,
        SearchFormat::Csv => out.write_all(CSV_HEADER.as_bytes()).map_err(|_| write_error())?,
    }
    let mut n = 0;
    while let Some(row) = rows.next()? {
        if n == MAX_ROWS { return Err(StoreError::TooLarge); }
        for column in [0, 1, 2, 3, 4, 6, 7] {
            if let ValueRef::Text(bytes) = row.get_ref(column)? {
                if bytes.len() > MAX_CELL { return Err(StoreError::TooLarge); }
            }
        }
        let timestamp = row.get(5)?;
        let record = Record {
            id: row.get(0)?,
            platform: scope.platform.clone(),
            account_id: scope.account_id.clone(),
            conversation_id: row.get(1)?,
            conversation_title: row.get(2)?,
            sender_id: row.get(3)?,
            sender_name: row.get(4)?,
            timestamp,
            kind: row.get(6)?,
            text: row.get(7)?,
            is_self: row.get(8)?,
            timestamp_text: time_text(timestamp)?,
        };
        if record.conversation_id.is_empty() || record.id.is_empty()
            || !identities.insert((record.conversation_id.clone(), record.id.clone()))
        {
            return Err(StoreError::BadQuery("ambiguous search export message"));
        }
        match request.format {
            SearchFormat::Json => json_row(&mut out, &record, n)?,
            SearchFormat::Csv => csv_row(&mut out, &record).map_err(|_| write_error())?,
        }
        n += 1;
    }
    if matches!(request.format, SearchFormat::Json) {
        out.write_all(if n == 0 { b"]" } else { b"\n]" }).map_err(|_| write_error())?;
    }
    Ok(out.bytes)
}
