//! Exactly three embedded assets for the opt-in guarded read harness.
use axum::{http::{HeaderValue,header},response::{IntoResponse,Response}};

pub(crate) const INDEX:&str="/api/_reader/";
const CSS:&str="/api/_reader/reader.css";
const JS:&str="/api/_reader/reader.js";

pub(super) fn contains(path:&str)->bool { matches!(path,INDEX|CSS|JS) }

fn asset(kind:&'static str,content:&'static str)->Response {
    ([(header::CONTENT_TYPE,HeaderValue::from_static(kind))],content).into_response()
}
pub(super) fn response(path:&str)->Option<Response> {
    match path {
        INDEX=>Some(asset("text/html; charset=utf-8",include_str!("../../guarded-reader/index.html"))),
        CSS=>Some(asset("text/css; charset=utf-8",include_str!("../../guarded-reader/reader.css"))),
        JS=>Some(asset("text/javascript; charset=utf-8",include_str!("../../guarded-reader/reader.js"))),
        _=>None,
    }
}

#[cfg(test)]
mod tests {
    #[test]
    fn only_embedded_canonical_reader_paths_are_accepted() {
        for path in [super::INDEX,super::CSS,super::JS] { assert!(super::contains(path)); }
        for path in ["/","/app.js","/vendor/lucide.js","/api/_reader","/api/_reader/a.js",
            "/api/_reader/../reader.js","/api/_reader/%72eader.js","/api/_reader/index.html"] {
            assert!(!super::contains(path));
        }
    }
}
