//! Separate cookie-only, read-only guarded loopback listener.
use super::{Archive,Host,MAX_ARCHIVE_WORK,MAX_QUERY_BYTES,health,legacy_http,reader_assets,desktop_contract};
use crate::{events::EventStore,plugins::{Permission,Plugin,Plugins},profile_lock::Gate,store::Store};
use axum::{Router,extract::{Request,State},http::{HeaderMap,HeaderName,HeaderValue,Method,StatusCode,header},
    middleware::{Next,from_fn_with_state},response::{IntoResponse,Response},routing::get};
use serde::Serialize;
use sha2::{Digest,Sha256};
use std::{net::SocketAddrV4,sync::{Arc,Mutex},time::{Duration,Instant}};
use subtle::ConstantTimeEq;
use tokio::sync::Semaphore;
use zeroize::Zeroizing;

pub(crate) const BOOTSTRAP:&str="/_local/browser/bootstrap";
const TICKET_HEADER:&str="x-lmh-bootstrap";
const COOKIE:&str="lmh_guarded_browser";
pub(crate) const TICKET_TTL:Duration=Duration::from_secs(30);
const SESSION_TTL:Duration=Duration::from_secs(300);

#[derive(Clone)]
pub(crate) struct Browser { principal:Plugin, auth:Arc<Mutex<Auth>> }
#[derive(Default)]
struct Auth { ticket:Option<Lease>,session:Option<Lease> }
struct Lease { generation:u64,expires:Instant,digest:[u8;32] }

/// Point-in-time private pipe metadata, never a proof of authentication.
#[derive(Serialize)]
pub(crate) struct BrowserStatus {
    locked:bool,
    guard_expires_in:Option<u64>,
    ticket_expires_in:Option<u64>,
    session_expires_in:Option<u64>,
}

fn seconds_left(now:Instant,until:Instant)->Option<u64> {
    let remaining=until.checked_duration_since(now)?;
    if remaining.is_zero() {return None;}
    Some(remaining.as_secs()+if remaining.subsec_nanos()!=0 {1} else {0})
}

fn secret()->Option<Zeroizing<String>> {
    let mut bytes=Zeroizing::new([0;32]);
    getrandom::fill(&mut *bytes).ok()?;
    Some(Zeroizing::new(hex::encode(*bytes)))
}
fn digest(value:&str)->Option<[u8;32]> {
    (value.len()==64 && value.bytes().all(|b|b.is_ascii_digit() || (b'a'..=b'f').contains(&b)))
        .then(||Sha256::digest(value).into())
}
impl Lease {
    fn agrees(&self,generation:u64,now:Instant,proof:&[u8;32])->bool {
        self.generation==generation && self.expires>now && bool::from(self.digest.ct_eq(proof))
    }
}
impl Browser {
    pub(crate) fn new(mut principal:Plugin)->Self {
        principal.permissions.remove(&Permission::AnnualReport);
        principal.permissions.remove(&Permission::PairReport);
        Self{principal,auth:Arc::new(Mutex::new(Auth::default()))}
    }
    pub(crate) fn revoke(&self) {
        if let Ok(mut auth)=self.auth.lock() { *auth=Auth::default(); }
    }
    pub(crate) fn status(&self, gate:&Gate)->Option<BrowserStatus> {
        let auth=self.auth.lock().ok()?;
        // The guard window is read while holding the browser snapshot. The
        // operator loop serializes lock/unlock/revoke; the request path does
        // not hold the gate lock while accessing browser auth.
        let now=Instant::now();
        let Some((generation,until))=gate.window_at(now) else {
            return Some(BrowserStatus{locked:true,guard_expires_in:None,
                ticket_expires_in:None,session_expires_in:None});
        };
        let remaining=|lease:Option<&Lease>| {
            let lease=lease.filter(|lease|lease.generation==generation)?;
            seconds_left(now,lease.expires.min(until))
        };
        Some(BrowserStatus{locked:false,guard_expires_in:seconds_left(now,until),
            ticket_expires_in:remaining(auth.ticket.as_ref()),
            session_expires_in:remaining(auth.session.as_ref())})
    }
    pub(crate) fn issue(&self,generation:u64,now:Instant)->Option<Zeroizing<String>> {
        let mut auth=self.auth.lock().ok()?;
        let proof=secret()?;
        auth.ticket=Some(Lease{generation,expires:now+TICKET_TTL,digest:digest(&proof)?});
        Some(proof)
    }
    fn exchange(&self,generation:u64,now:Instant,ticket:&str)->Option<(Zeroizing<String>,[u8;32])> {
        let hash=digest(ticket)?;
        let mut auth=self.auth.lock().ok()?;
        if !auth.ticket.as_ref()?.agrees(generation,now,&hash) { return None; }
        let value=secret()?;
        let proof=digest(&value)?;
        auth.ticket=None;
        auth.session=Some(Lease{generation,expires:now+SESSION_TTL,digest:proof});
        Some((value,proof))
    }
    fn live(&self,generation:u64,now:Instant,proof:&[u8;32])->bool {
        self.auth.lock().ok().is_some_and(|state|
            state.session.as_ref().is_some_and(|lease|lease.agrees(generation,now,proof)))
    }
}

#[derive(Clone)]
struct Boundary { authority:HeaderValue,origin:String,gate:Gate,browser:Browser,reader:bool }
pub(crate) fn router(address:SocketAddrV4,owner:EventStore,store:Store,plugins:Plugins,gate:Gate,browser:Browser,reader:bool)
    ->Result<Router,&'static str> {
    if address.ip().octets()!=[127,0,0,1] || address.port()==0 { return Err("invalid browser loopback address"); }
    let authority=HeaderValue::from_str(&address.to_string()).map_err(|_|"invalid browser authority")?;
    let host=Host{authority:authority.clone(),store,plugins,archive:Archive::Guarded(owner,gate.clone(),true),
        permits:Arc::new(Semaphore::new(MAX_ARCHIVE_WORK)),addons:super::HostAddons::default()};
    let router=Router::new().route("/healthz",get(health))
        .route(desktop_contract::BROWSER_PATH,get(desktop_contract::browser))
        .fallback(||async { status(StatusCode::NOT_FOUND) });
    Ok(legacy_http::browser_routes(router).with_state(host).layer(from_fn_with_state(
        Boundary{authority,origin:format!("http://{address}"),gate,browser,reader},authorize)))
}

fn status(code:StatusCode)->Response { (code,legacy_http::error(code)).into_response() }

fn safe(mut res:Response,reader_html:bool)->Response {
    let page=reader_html && res.status()==StatusCode::OK;
    let headers=res.headers_mut();
    for (key,value) in [
        ("cache-control","no-store"),("pragma","no-cache"),("referrer-policy","no-referrer"),
        ("x-content-type-options","nosniff"),("x-frame-options","DENY"),
        ("cross-origin-opener-policy","same-origin"),("cross-origin-resource-policy","same-origin"),
        ("content-security-policy",if page {
            "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'; object-src 'none'"
        } else { "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'" }),
        ("connection","close"),
    ] {
        headers.insert(HeaderName::from_static(key),HeaderValue::from_static(value));
    }
    res
}

fn single(headers:&HeaderMap,key:HeaderName)->Result<Option<&str>,()> {
    let all=headers.get_all(key);
    let mut iter=all.iter();
    let first=iter.next().map(|v|v.to_str().map_err(|_|())).transpose()?;
    if iter.next().is_some() { return Err(()); }
    Ok(first)
}

fn same_origin_boundary(router:&Boundary,req:&Request,bootstrap:bool)->bool {
    let h=req.headers();
    if super::one(h,header::HOST)!=Some(&router.authority) || req.uri().authority().is_some()
        || req.uri().scheme().is_some() { return false; }
    let Ok(origin)=single(h,header::ORIGIN) else { return false; };
    let Ok(referer)=single(h,header::REFERER) else { return false; };
    let Ok(fetch)=single(h,HeaderName::from_static("sec-fetch-site")) else { return false; };
    if bootstrap {
        origin.is_none() && referer.is_none() && matches!(fetch,None|Some("none"))
    } else {
        origin.is_none_or(|o|o==router.origin)
            && referer.is_none_or(|r|r==router.origin || r.strip_prefix(&router.origin).is_some_and(|s|s.starts_with('/')))
            && matches!(fetch,None|Some("none"|"same-origin"))
    }
}

async fn authorize(State(router):State<Boundary>,req:Request,next:Next)->Response {
    let reader_html=router.reader && req.uri().path()==reader_assets::INDEX;
    safe(authorized(router,req,next).await,reader_html)
}

async fn authorized(router:Boundary,mut req:Request,next:Next)->Response {
    let path=req.uri().path();
    let bootstrap=path==BOOTSTRAP;
    if !same_origin_boundary(&router,&req,bootstrap) { return status(StatusCode::FORBIDDEN); }
    let good_method=if bootstrap {*req.method()==Method::POST} else {*req.method()==Method::GET};
    if !good_method { return status(StatusCode::METHOD_NOT_ALLOWED); }
    let headers=req.headers();
    if headers.contains_key(header::TRANSFER_ENCODING)
        || !matches!(single(headers,header::CONTENT_LENGTH),Ok(None|Some("0")))
        || ((bootstrap || (router.reader && reader_assets::contains(path))) && req.uri().query().is_some())
        || req.uri().query().is_some_and(|q|q.len()>MAX_QUERY_BYTES) {
        return status(StatusCode::BAD_REQUEST);
    }
    if path=="/healthz" {
        if req.uri().query().is_some() { return status(StatusCode::BAD_REQUEST); }
        return next.run(req).await;
    }
    let Some(generation)=router.gate.generation() else { return status(StatusCode::LOCKED); };
    if headers.contains_key(header::AUTHORIZATION) { return status(StatusCode::UNAUTHORIZED); }
    let (mut res,proof)=if bootstrap {
        if headers.contains_key(header::COOKIE) { return status(StatusCode::BAD_REQUEST); }
        let Ok(Some(ticket))=single(headers,HeaderName::from_static(TICKET_HEADER)) else {
            return status(StatusCode::UNAUTHORIZED);
        };
        let ticket=Zeroizing::new(ticket.to_owned());
        if !matches!(tokio::time::timeout(Duration::from_millis(500),
            axum::body::to_bytes(req.into_body(),0)).await,Ok(Ok(bytes)) if bytes.is_empty()) {
            return status(StatusCode::BAD_REQUEST);
        }
        let Some((cookie,proof))=router.browser.exchange(generation,Instant::now(),&ticket) else {
            return status(StatusCode::UNAUTHORIZED);
        };
        let Ok(value)=HeaderValue::from_str(&format!("{COOKIE}={}; Path=/api; Max-Age={}; HttpOnly; SameSite=Strict",
            *cookie,SESSION_TTL.as_secs())) else { return status(StatusCode::SERVICE_UNAVAILABLE); };
        let mut res=StatusCode::NO_CONTENT.into_response();
        res.headers_mut().insert(header::SET_COOKIE,value);
        (res,proof)
    } else {
        if headers.contains_key(HeaderName::from_static(TICKET_HEADER)) { return status(StatusCode::UNAUTHORIZED); }
        let Ok(Some(cookie))=single(headers,header::COOKIE) else { return status(StatusCode::UNAUTHORIZED); };
        let Some(proof)=cookie.strip_prefix("lmh_guarded_browser=").and_then(digest) else {
            return status(StatusCode::UNAUTHORIZED);
        };
        if !router.browser.live(generation,Instant::now(),&proof) { return status(StatusCode::UNAUTHORIZED); }
        let response=if router.reader { reader_assets::response(path) } else { None };
        let response=match response {
            Some(asset)=>asset,
            None=>{
                req.extensions_mut().insert(router.browser.principal.clone());
                next.run(req).await
            }
        };
        (response,proof)
    };
    if router.gate.generation()!=Some(generation) {
        return status(StatusCode::LOCKED);
    }
    if !router.browser.live(generation,Instant::now(),&proof) {
        return status(StatusCode::UNAUTHORIZED);
    }
    if res.status().is_client_error() || res.status().is_server_error() {
        let (parts,_)=res.into_parts();
        let code=parts.status;
        res=(parts,legacy_http::error(code)).into_response();
    }
    res
}


#[cfg(test)]
mod tests {
    use super::*;
    use crate::plugins::{AccountScope,PluginKind};
    use axum::body::Body;
    use tower::ServiceExt;

    fn browser()->Browser {
        Browser::new(Plugin{id:"fixture.browser".into(),kind:PluginKind::Desktop,
            permissions:[Permission::LegacyBrowser,Permission::AnnualReport].into_iter().collect(),
            account_scope:Some(AccountScope{platform:"qq".into(),account_id:"one".into()}),
            campus_profile:None})
    }
    #[test]
    fn expires_replaces_and_never_reuses_a_ticket() {
        let auth=browser();
        assert!(!auth.principal.permissions.contains(&Permission::AnnualReport));
        let now=Instant::now();
        let stale=auth.issue(1,now).unwrap();
        let real=auth.issue(1,now).unwrap();
        assert!(auth.exchange(1,now,&stale).is_none());
        assert!(auth.exchange(2,now,&real).is_none());
        assert!(auth.exchange(1,now+TICKET_TTL,&real).is_none());
        let next=auth.issue(2,now).unwrap();
        let (_,cookie)=auth.exchange(2,now,&next).unwrap();
        assert!(auth.exchange(2,now,&next).is_none());
        assert!(auth.live(2,now,&cookie));
        assert!(!auth.live(2,now+SESSION_TTL,&cookie));
        let next=auth.issue(2,now).unwrap();
        let (_,new_cookie)=auth.exchange(2,now,&next).unwrap();
        assert!(!auth.live(2,now,&cookie) && auth.live(2,now,&new_cookie));
        auth.revoke();
        assert!(!auth.live(2,now,&new_cookie));
    }

    #[tokio::test]
    async fn in_flight_response_cannot_escape_lock_generation() {
        let gate=Gate::default();
        assert!(gate.unlock());
        let auth=browser();
        let now=Instant::now();
        let generation=gate.generation().unwrap();
        let ticket=auth.issue(generation,now).unwrap();
        let (cookie,_)=auth.exchange(generation,now,&ticket).unwrap();
        let started=Arc::new(tokio::sync::Notify::new());
        let release=Arc::new(tokio::sync::Notify::new());
        let one=started.clone();
        let two=release.clone();
        let app=Router::new().route("/api/messages",get(move|| {
            let (one,two)=(one.clone(),two.clone());
            async move { one.notify_one();two.notified().await;"never-publish-this" }
        })).layer(from_fn_with_state(Boundary{authority:HeaderValue::from_static("127.0.0.1:32145"),
            origin:"http://127.0.0.1:32145".into(),gate:gate.clone(),browser:auth.clone(),reader:false},authorize));
        let req=Request::builder().uri("/api/messages")
            .header("host","127.0.0.1:32145").header("cookie",format!("{COOKIE}={}",*cookie))
            .body(Body::empty()).unwrap();
        let response=tokio::spawn(app.oneshot(req));
        started.notified().await;
        gate.lock();
        auth.revoke();
        assert!(gate.unlock());
        release.notify_one();
        let result=response.await.unwrap().unwrap();
        assert_eq!(result.status(),StatusCode::LOCKED);
        let bytes=axum::body::to_bytes(result.into_body(),1024).await.unwrap();
        assert!(std::str::from_utf8(&bytes).unwrap().contains("account_locked"));
        assert!(!std::str::from_utf8(&bytes).unwrap().contains("never-publish-this"));
    }
}
