//! Import-time normalization only. Never opens article URLs or local provider accounts.
use base64::{Engine as _, engine::general_purpose::STANDARD};
use std::collections::BTreeMap;
use url::form_urlencoded;

pub(crate) const SOURCE_ID: &str = "wechat-manual";
pub(crate) const SOURCE_NAME: &str = "微信公众号公开文章（按链接导入）";
pub(crate) const CATEGORY: &str = "wechat";
const ORIGIN: &str = "https://mp.weixin.qq.com/s";

pub(crate) struct ArticleUrl {
    pub url: String,
    // Separate grouping identity: a changed signature cannot create a second
    // stored copy of the same article slot in an exact Campus profile.
    pub key: String,
}

pub(crate) fn normalize(input: &str) -> Option<ArticleUrl> {
    if input.len() > 1024 || !input.is_ascii() || input.bytes().any(|b|
        b.is_ascii_control() || b == b' ' || b == b'\\')
    {
        return None;
    }
    let (input, fragment) = input.split_once('#').map_or((input, ""), |(a,b)|(a,b));
    if !matches!(fragment, "" | "wechat_redirect") { return None; }
    let (base, raw) = input.split_once('?')?;
    let (authority, path) = base.split_at_checked(ORIGIN.len()-2)?;
    if !authority.eq_ignore_ascii_case("https://mp.weixin.qq.com") || path != "/s" { return None; }

    // Values use the ordinary application/x-www-form-urlencoded decoding also
    // used by the pinned adapter. Check the wire shape first: malformed percent
    // escapes, duplicate keys (including empty fields) and encoded keys fail.
    let mut fields = BTreeMap::new();
    for part in raw.split('&') {
        let (key, value) = part.split_once('=')?;
        if key.is_empty() || !key.bytes().all(|b| b.is_ascii_alphanumeric() || b==b'_')
            || !well_encoded(value) || fields.contains_key(key)
        {
            return None;
        }
        let mut decoded = form_urlencoded::parse(part.as_bytes());
        let (_, value) = decoded.next()?;
        if decoded.next().is_some() || value.is_empty() || !value.is_ascii() { return None; }
        match key {
            "__biz" if biz(&value) => (),
            "idx" if canonical_positive(&value, 10) && value.parse::<u32>().is_ok() => (),
            "mid" if canonical_positive(&value, 20) && value.parse::<u64>().is_ok() => (),
            "sn" if value.len()==32 && value.bytes().all(lower_hex) => (),
            // Only known share/tracking metadata is discarded. Unknown query
            // names fail closed; credential-like names are never retained.
            "scene" | "subscene" | "isappinstalled" if tracking_number(&value) => (),
            "from" if value.len()<=32 && value.bytes().all(|b|b.is_ascii_alphanumeric()
                || matches!(b,b'_'|b'-')) => (),
            "chksm" if value.len()<=128 && value.bytes().all(lower_hex) => (),
            _ => return None,
        };
        fields.insert(key,value);
    }

    let mut query = form_urlencoded::Serializer::new(String::new());
    for key in ["__biz","idx","mid"] {
        query.append_pair(key,fields.get(key)?.as_ref());
    }
    let sn = fields.get("sn")?;
    let key = format!("{ORIGIN}?{}", query.finish());
    let url = format!("{key}&sn={sn}");
    Some(ArticleUrl{url,key})
}

fn lower_hex(b:u8)->bool { b.is_ascii_digit() || (b'a'..=b'f').contains(&b) }

fn tracking_number(value:&str)->bool {
    value.len()<=8 && value.bytes().all(|b|b.is_ascii_digit())
}

fn canonical_positive(value:&str, max:usize)->bool {
    value.len()<=max && value.as_bytes().first().is_some_and(|b| matches!(b,b'1'..=b'9'))
        && value.bytes().all(|b|b.is_ascii_digit())
}

fn biz(value:&str)->bool {
    if !(4..=128).contains(&value.len()) { return false; }
    // No alternate base64 spellings are treated as distinct public authorities.
    STANDARD.decode(value).is_ok_and(|decoded| STANDARD.encode(decoded)==value)
}

fn well_encoded(value:&str)->bool {
    let bytes=value.as_bytes();
    let mut i=0;
    while i<bytes.len() {
        if bytes[i]==b'%' {
            if !bytes.get(i+1..i+3).is_some_and(|pair|pair.iter().all(|b|b.is_ascii_hexdigit())) {
                return false;
            }
            i+=3;
        } else {
            i+=1;
        }
    }
    true
}
