//! Explicit in-memory download of one filtered conversation on ordinary modes.
use super::{Client, Error, ResponseKind, valid_identifier};
use crate::{
    desktop_sdk::Mode,
    export::{FILTERED_PATH, Format, MAX_OUTPUT},
    store::parse_bound,
};
use zeroize::Zeroizing;

/// Fixed single-conversation export serializers; XLSX cells contain text only.
/// ChatLab serializers accept selected rows of type text or system only.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum TextFormat {
    Json,
    Csv,
    Txt,
    Markdown,
    Html,
    /// PostgreSQL script bytes only. Never execute or auto-open them.
    Sql,
    /// Bounded spreadsheet bytes; no workbook is opened or parsed by the SDK.
    Xlsx,
    ChatLabJson,
    ChatLabJsonLines,
}

impl TextFormat {
    fn wire(self) -> (&'static str, Format) {
        match self {
            Self::Json => ("json", Format::Json),
            Self::Csv => ("csv", Format::Csv),
            Self::Txt => ("txt", Format::Txt),
            Self::Markdown => ("md", Format::Md),
            Self::Html => ("html", Format::Html),
            Self::Sql => ("sql", Format::Sql),
            Self::Xlsx => ("xlsx", Format::Xlsx),
            Self::ChatLabJson => ("chatlab", Format::Chatlab),
            Self::ChatLabJsonLines => ("chatlab-jsonl", Format::ChatlabJsonl),
        }
    }
}

/// Explicit filters of one exact local conversation. Date-only strings
/// mean inclusive UTC+08 calendar days, independent of the desktop timezone.
pub struct TextFilters<'a> {
    pub conversation_id: &'a str,
    pub format: TextFormat,
    pub keyword: Option<&'a str>,
    pub message_type: Option<&'a str>,
    pub start: Option<&'a str>,
    pub end: Option<&'a str>,
}

impl Client<'_> {
    /// Negotiated support for this ordinary archive, not current liveness.
    pub fn supports_filtered_text_export(&self) -> bool {
        self.active && self.filtered_text_export
    }

    /// Separate advertisement so old servers never receive a SQL request.
    pub fn supports_filtered_sql_export(&self) -> bool {
        self.active && self.filtered_sql_export
    }

    /// Separate capability for this workbook format on the current client.
    pub fn supports_filtered_xlsx_export(&self) -> bool {
        self.active && self.filtered_xlsx_export
    }

    /// One explicit read-only full-body download. Does not save, launch,
    /// render, retry, or fall back to unfiltered/native media export.
    /// Returned bytes are private and untrusted; don't auto-open them.
    pub fn download_filtered_text_export(
        &mut self, filters: TextFilters<'_>,
    ) -> Result<Zeroizing<Vec<u8>>, Error> {
        if !self.filtered_text_export
            || (filters.format == TextFormat::Sql && !self.filtered_sql_export)
            || (filters.format == TextFormat::Xlsx && !self.filtered_xlsx_export)
            || !matches!(self.connection.mode(), Mode::Snapshot | Mode::NativeLive)
        {
            return Err(Error::Unsupported);
        }
        if !valid_identifier(filters.conversation_id, 256)
            || !bounded(filters.keyword, 4096)
            || !bounded(filters.message_type, 128)
            || !bounded(filters.start, 128)
            || !bounded(filters.end, 128)
        {
            return Err(Error::Input);
        }
        let start = parse_bound(filters.start.unwrap_or_default(), false)
            .map_err(|_| Error::Input)?;
        let end = parse_bound(filters.end.unwrap_or_default(), true)
            .map_err(|_| Error::Input)?;
        if start.zip(end).is_some_and(|(start, end)| start > end) {
            return Err(Error::Input);
        }
        let (name, format) = filters.format.wire();
        let mut pairs = vec![("conversation_id", filters.conversation_id), ("format", name)];
        if let Some(value) = filters.keyword { pairs.push(("q", value)); }
        if let Some(value) = filters.message_type { pairs.push(("type", value)); }
        if let Some(value) = filters.start { pairs.push(("start", value)); }
        if let Some(value) = filters.end { pairs.push(("end", value)); }
        // Uses fixed form encoding and derives platform/account from the
        // authenticated archive connection. No arbitrary target or headers.
        let target = self.target(FILTERED_PATH, &pairs)?;
        self.call_wire(
            &target, true, None, None, 200, MAX_OUTPUT, ResponseKind::FilteredExport(format),
            |bytes| {
                let valid=if matches!(format,Format::Xlsx) {
                    // Binary attachment. No unzipping or interpreting the workbook.
                    bytes.starts_with(b"PK\x03\x04")
                } else {
                    // TXT legitimately has no bytes for a known empty result.
                    (!bytes.is_empty() || matches!(format,Format::Txt))
                        && std::str::from_utf8(bytes.as_slice()).is_ok()
                };
                if !valid {return Err(Error::Protocol);}
                Ok(bytes)
            },
        )
    }
}

fn bounded(input: Option<&str>, max: usize) -> bool {
    input.is_none_or(|s| s.len() <= max && !s.chars().any(char::is_control))
}
