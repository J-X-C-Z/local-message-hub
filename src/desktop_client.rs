//! Explicit synchronous typed access to a managed Hub's numeric IPv4 loopback endpoint.
use crate::{
    desktop_sdk::{Connection, DesktopGrant, Mode},
    plugins::AccountScope,
};
use serde::Deserialize;
use serde_json::Value;
use std::{
    io::{self, Read, Write},
    net::{Ipv4Addr, SocketAddr, SocketAddrV4, TcpStream},
    sync::Arc,
    time::{Duration, Instant},
};
use zeroize::Zeroizing;

pub mod campus;
pub mod contacts;
pub mod drafts;
pub mod exports;
pub mod imports;
pub mod journal;
pub mod media;
pub mod metadata;
pub mod notifications;
pub mod text_export;
pub mod transcription;
pub mod wecom_display;

const HEALTH: &str = "/healthz";
const MANIFEST: &str = "/api/v1/manifest";
const CONVERSATIONS: &str = "/api/v1/conversations";
const MESSAGES: &str = "/api/v1/messages";
const SEARCH:&str="/api/v1/search";
const CONTEXT:&str="/api/v1/messages/context";
const QUOTED:&str="/api/v1/messages/quoted-context";
const MEDIA: &str = "/api/v1/messages/media";
const MAX_HEADERS: usize = 8192;
const MAX_PAGE: usize = 4*1024*1024 + 4096;
const MAX_TARGET: usize = 8*1024;
const TIMEOUT: Duration = Duration::from_secs(3);

#[derive(Debug,thiserror::Error)]
pub enum Error {
    #[error("managed desktop supervisor/backend is no longer current")]
    Stale,
    #[error("unsupported route for this desktop archive")]
    Unsupported,
    #[error("invalid desktop client selection")]
    Input,
    #[error("loopback request failed or timed out")]
    Transport,
    #[error("unexpected Hub HTTP response")]
    Protocol,
    #[error("Hub declined the request (HTTP {0})")]
    Status(u16),
    #[error("Hub manifest is incompatible with this exact grant/account")]
    Identity,
    #[error("Hub response exceeds client limits")]
    TooLarge,
}

#[derive(Deserialize)]
struct Manifest {
    api_version: u8,
    plugin_protocol_version: u8,
    backend: String,
    mode: String,
    plugin: Principal,
    resources: Resources,
    local_notifications: Option<Value>,
    local_import_jobs: Option<Value>,
    local_drafts: Option<Value>,
    local_draft_review: Option<Value>,
    local_asset_jobs: Option<Value>,
    local_selected_message_bundle: Option<Value>,
    local_transcription: Option<Value>,
}
#[derive(Deserialize)]
struct Principal {
    id: String,
    kind: String,
    permissions: Vec<String>,
    account_scope: Option<AccountScope>,
}
#[derive(Deserialize)]
struct Resources {
    conversations: Option<String>,
    conversation_lookup: Option<String>,
    wecom_rendered_display: Option<String>,
    messages: Option<String>,
    search: Option<String>,
    message_context: Option<String>,
    quoted_message_context: Option<String>,
    message_media: Option<String>,
    conversation_metadata: Option<String>,
    contacts: Option<String>,
    contact_identity: Option<String>,
    event_checkpoint: Option<String>,
    events: Option<String>,
    event_stream: Option<String>,
    filtered_message_export: Option<String>,
    filtered_message_sql_export: Option<String>,
    filtered_message_xlsx_export: Option<String>,
}

/// Intentionally not Debug: returned rows contain private local data.
#[derive(Deserialize)]
pub struct Conversations {
    pub items: Vec<Value>,
    pub total: u64,
}
#[derive(Deserialize)]
pub struct Messages {
    pub items: Vec<Value>,
    pub total: u64,
    pub has_more: bool,
    pub next_cursor: Option<String>,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MediaItem { pub id:String,pub size:u64 }
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct MediaList {items:Vec<MediaItem>}

/// Exact-account literal search input. The original text and conversation
/// must be reused unchanged when continuing with an opaque server cursor.
pub struct Search<'a> {
    pub text:&'a str,
    pub conversation_id:Option<&'a str>,
    pub cursor:Option<&'a str>,
    pub limit:u16,
}

/// Private, typed archive fields common to search and context. Deliberately
/// omits untrusted URL, attachment, raw-provider and opaque metadata fields.
#[derive(Deserialize)]
pub struct ArchivedMessage {
    pub id:String,
    pub platform:String,
    pub account_id:String,
    pub conversation_id:String,
    pub timestamp:i64,
    pub is_self:bool,
    pub sender_id:Option<String>,
    pub sender_name:Option<String>,
    #[serde(rename="type")]
    pub kind:Option<String>,
    pub conversation_title:Option<String>,
    pub conversation_kind:Option<String>,
}

#[derive(Deserialize)]
pub struct SearchHit {
    #[serde(flatten)]
    pub message:ArchivedMessage,
    /// At most 1,200 characters; not the full indexed message body.
    #[serde(rename="text")]
    pub text_preview:Option<String>,
}
#[derive(Deserialize)]
pub struct SearchPage {
    pub items:Vec<SearchHit>,
    pub total:u64,
    pub has_more:bool,
    pub next_cursor:Option<String>,
}

#[derive(Clone,Copy)]
pub struct Window {pub before:u8,pub after:u8}
impl Default for Window {
    fn default()->Self {Self{before:5,after:5}}
}
#[derive(Deserialize)]
pub struct ContextMessage {
    #[serde(flatten)]
    pub message:ArchivedMessage,
    /// Full indexed text when present, not a provider message fetch.
    pub text:Option<String>,
}
#[derive(Deserialize)]
pub struct MessageContext {
    pub items:Vec<ContextMessage>,
    pub total:u64,
    pub target_index:usize,
    pub has_more_before:bool,
    pub has_more_after:bool,
}
#[derive(Deserialize)]
pub struct QuotedContext {
    #[serde(flatten)]
    pub context:MessageContext,
    pub target_message_id:String,
}

#[derive(Default)]
struct ArchiveNavigation {search:bool,context:bool,quote:bool,conversation_lookup:bool,wecom_rendered_display:bool}

enum Post<'a> {
    Json(&'a [u8]),
    StockPreflight(&'a [u8]),
    StockStage { bytes: &'a [u8], request_id: &'a imports::RequestId },
}

impl<'a> Post<'a> {
    fn content(&self) -> (&'static str, &'a [u8]) {
        match self {
            Self::Json(bytes) => ("application/json", *bytes),
            Self::StockPreflight(bytes) | Self::StockStage { bytes, .. } =>
                ("application/octet-stream", *bytes),
        }
    }
}

enum Credential<'a> { Generated(&'a DesktopGrant), Installed(Zeroizing<String>), InstalledReader(Zeroizing<String>) }
impl Credential<'_> {
    fn with_token<R>(&self,task:impl FnOnce(&str)->R)->R {
        match self {
            Self::Generated(grant)=>grant.with_token(task),
            Self::Installed(secret)|Self::InstalledReader(secret)=>task(secret.as_str()),
        }
    }
}

/// The caller supplies an exact process-liveness predicate, typically bound to
/// the owned native supervisor handle and its current readiness generation.
/// This predicate must be nonblocking and never treat PID existence as identity.
pub struct Client<'a> {
    connection: Connection,
    grant: Credential<'a>,
    grant_id: String,
    scope: AccountScope,
    is_current: Box<dyn FnMut(u32)->bool+'a>,
    active: bool,
    media: bool,
    filtered_export: bool,
    filtered_text_export: bool,
    filtered_sql_export: bool,
    filtered_xlsx_export: bool,
    navigation: ArchiveNavigation,
    session: Arc<()>,
}
impl<'a> Client<'a> {
    /// Explicit network activity: unauthenticated health then one protected
    /// manifest, only at this managed IPv4 loopback endpoint.
    pub fn connect(connection: Connection,grant:&'a DesktopGrant,grant_id:String,scope:AccountScope,
        is_current:impl FnMut(u32)->bool+'a)->Result<Self,Error> {
        Self::connect_archive(connection,Credential::Generated(grant),grant_id,scope,is_current)
    }

    /// Connect an independently provisioned ordinary native-live reader.
    /// The caller supplies its exact scoped token outside argv and env.
    /// Accepts only the baseline conversation/message read permissions;
    /// neither this constructor nor the client modifies the grant registry.
    pub fn connect_installed_reader(connection:Connection,credential:Zeroizing<String>,grant_id:String,
        scope:AccountScope,is_current:impl FnMut(u32)->bool+'a)->Result<Self,Error> {
        if connection.mode()!=Mode::NativeLive { return Err(Error::Unsupported); }
        if !hex64(&credential) { return Err(Error::Input); }
        Self::connect_archive(connection,Credential::InstalledReader(credential),grant_id,scope,is_current)
    }

    fn connect_exact(connection:Connection,grant:Credential<'a>,grant_id:String,scope:AccountScope,
        is_current:impl FnMut(u32)->bool+'a)->Result<(Self,Manifest),Error> {
        if !valid_identifier(&grant_id,64) || !valid_identifier(&scope.account_id,256)
            || !matches!(scope.platform.as_str(),"wechat"|"wecom"|"qq"|"telegram"|"email") {
            return Err(Error::Input);
        }
        let mut client=Self{connection,grant,grant_id,scope,is_current:Box::new(is_current),active:true,media:false,filtered_export:false,filtered_text_export:false,filtered_sql_export:false,filtered_xlsx_export:false,navigation:ArchiveNavigation::default(),session:Arc::new(())};
        #[derive(Deserialize)]
        #[serde(deny_unknown_fields)]
        struct Health {status:String}
        let health:Health=client.call(HEALTH,false,1024)?;
        if health.status!="ok" {return Err(Error::Protocol);}
        let manifest:Manifest=client.call(MANIFEST,true,64*1024)?;
        let mode=match client.connection.mode() {
            Mode::Snapshot=>"snapshot-read-only",
            Mode::NativeLive=>"native-live",
            Mode::ImportedMedia=>"imported-media-read-only",
        };
        let matches=manifest.plugin.account_scope.as_ref().is_some_and(|s|
            s.platform==client.scope.platform && s.account_id==client.scope.account_id);
        if manifest.api_version!=1 || manifest.plugin_protocol_version!=1
            || manifest.backend!="local-message-hub-rs" || manifest.mode!=mode
            || manifest.plugin.kind!="desktop" || manifest.plugin.id!=client.grant_id || !matches {
            return Err(Error::Identity);
        }
        Ok((client,manifest))
    }

    fn connect_archive(connection:Connection,grant:Credential<'a>,grant_id:String,scope:AccountScope,
        is_current:impl FnMut(u32)->bool+'a)->Result<Self,Error> {
        let installed_reader=matches!(&grant,Credential::InstalledReader(_));
        let (mut client,manifest)=Self::connect_exact(connection,grant,grant_id,scope,is_current)?;
        if installed_reader && (manifest.plugin.permissions.len()!=2 || manifest.plugin.permissions.iter()
            .any(|p| !matches!(p.as_str(),"conversations.read"|"messages.read"))) {
            return Err(Error::Identity);
        }
        let perm=|value:&str|manifest.plugin.permissions.iter().any(|p|p==value);
        if !perm("conversations.read")||!perm("messages.read"){return Err(Error::Identity);}
        if client.connection.mode()==Mode::ImportedMedia {
            client.media=perm("media.read") && manifest.resources.message_media.as_deref()==Some(MEDIA);
            client.filtered_export=client.media && perm("messages.export")
                && matches!(client.scope.platform.as_str(),"wechat"|"qq")
                && exports::advertised(&manifest);
        } else if manifest.resources.conversations.as_deref()!=Some(CONVERSATIONS)
            || manifest.resources.messages.as_deref()!=Some(MESSAGES) {
            return Err(Error::Identity);
        }
        if client.connection.mode()!=Mode::ImportedMedia {
            client.filtered_text_export=perm("messages.export")
                && manifest.resources.filtered_message_export.as_deref()==Some(crate::export::FILTERED_PATH);
            client.filtered_sql_export=client.filtered_text_export
                && manifest.resources.filtered_message_sql_export.as_deref()==Some(crate::export::FILTERED_PATH);
            client.filtered_xlsx_export=client.filtered_text_export
                && manifest.resources.filtered_message_xlsx_export.as_deref()==Some(crate::export::FILTERED_PATH);
            // Manifest resource strings are compared with fixed routes, not used
            // as URLs or redirect targets. Null/unknown is not availability.
            client.navigation=ArchiveNavigation{
                conversation_lookup:manifest.resources.conversation_lookup.as_deref()==Some(CONVERSATIONS),
                wecom_rendered_display:cfg!(target_os="linux") && client.connection.mode()==Mode::NativeLive
                    && client.scope.platform=="wecom" && perm("conversations.read")
                    && manifest.resources.wecom_rendered_display.as_deref()
                        ==Some(crate::wecom_sync::FROZEN_CONVERSATION_DISPLAY),
                search:manifest.resources.search.as_deref()==Some(SEARCH),
                context:manifest.resources.message_context.as_deref()==Some(CONTEXT),
                quote:client.scope.platform=="wechat"
                    && manifest.resources.quoted_message_context.as_deref()==Some(QUOTED),
            };
        }
        Ok(client)
    }

    /// Consume the old session and revalidate a newly accepted supervisor
    /// readiness frame. No previous endpoint or in-flight requests are retried.
    pub fn reconnect(self,fresh:Connection,is_current:impl FnMut(u32)->bool+'a)->Result<Self,Error> {
        if fresh.mode()!=self.connection.mode(){return Err(Error::Identity);}
        Self::connect_archive(fresh,self.grant,self.grant_id,self.scope,is_current)
    }

    /// Existing unfiltered list, unchanged for old and new backends.
    pub fn conversations(&mut self,limit:u16,offset:u32)->Result<Conversations,Error> {
        self.conversations_page(limit,offset,None,None)
    }

    /// Paged literal title search within this managed exact-account archive.
    /// The backend applies it before count and pagination; no provider search.
    pub fn search_conversations(&mut self,title:&str,limit:u16,offset:u32)->Result<Conversations,Error> {
        if title.trim().is_empty() || title.len()>4096 || title.chars().any(char::is_control) {
            return Err(Error::Input);
        }
        self.conversations_page(limit,offset,Some(title),None)
    }

    /// Resolve one exact normalized conversation ID, for example from a
    /// contact selected under the same account. Never guesses by display name.
    /// An older backend without the fixed advertised lookup returns Unsupported.
    pub fn lookup_conversation(&mut self,conversation_id:&str)->Result<Option<Value>,Error> {
        if !self.navigation.conversation_lookup {return Err(Error::Unsupported);}
        if !valid_identifier(conversation_id,256) {return Err(Error::Input);}
        let mut page=self.conversations_page(1,0,None,Some(conversation_id))?;
        if page.total>1 || page.items.len() as u64!=page.total
            || page.items.first().is_some_and(|row|row["id"].as_str()!=Some(conversation_id)) {
            self.active=false;
            return Err(Error::Protocol);
        }
        Ok(page.items.pop())
    }

    fn conversations_page(&mut self,limit:u16,offset:u32,title:Option<&str>,
        exact_id:Option<&str>)->Result<Conversations,Error> {
        if self.connection.mode()==Mode::ImportedMedia {return Err(Error::Unsupported);}
        if !(1..=100).contains(&limit)||offset>100_000 {return Err(Error::Input);}
        let limit=limit.to_string();
        let offset=offset.to_string();
        let mut pairs=vec![("limit",limit.as_str()),("offset",offset.as_str())];
        if let Some(title)=title {pairs.push(("q",title));}
        if let Some(id)=exact_id {pairs.push(("conversation_id",id));}
        let target=self.target(CONVERSATIONS,&pairs)?;
        let page:Conversations=self.call(&target,true,MAX_PAGE)?;
        self.verify_rows(&page.items,None)?;
        Ok(page)
    }

    pub fn messages(&mut self,conversation_id:&str,limit:u16,cursor:Option<&str>)->Result<Messages,Error> {
        if self.connection.mode()==Mode::ImportedMedia {return Err(Error::Unsupported);}
        if !valid_identifier(conversation_id,256)||!(1..=100).contains(&limit)
            || cursor.is_some_and(|c|!valid_cursor(c)) {return Err(Error::Input);}
        let limit=limit.to_string();
        let mut pairs=vec![("conversation_id",conversation_id),("limit",limit.as_str())];
        if let Some(cursor)=cursor {pairs.push(("cursor",cursor));}
        let target=self.target(MESSAGES,&pairs)?;
        let page:Messages=self.call(&target,true,MAX_PAGE)?;
        if page.next_cursor.as_deref().is_some_and(|c|!valid_cursor(c)) {
            self.active=false;
            return Err(Error::Protocol);
        }
        self.verify_rows(&page.items,Some(conversation_id))?;
        Ok(page)
    }

    /// Literal, scoped archive search. Does not expand a grant or execute any
    /// URL, Markdown, query operator, or remote/provider search.
    pub fn search_messages(&mut self,search:Search<'_>)->Result<SearchPage,Error> {
        if !self.navigation.search {return Err(Error::Unsupported);}
        if search.text.trim().is_empty()||search.text.len()>4096
            ||search.text.chars().any(char::is_control)
            ||!(1..=100).contains(&search.limit)
            ||search.conversation_id.is_some_and(|id|!valid_identifier(id,1024))
            ||search.cursor.is_some_and(|c|!valid_search_cursor(c)) {
            return Err(Error::Input);
        }
        let limit=search.limit.to_string();
        let mut pairs=vec![("q",search.text),("limit",limit.as_str())];
        if let Some(id)=search.conversation_id {pairs.push(("conversation_id",id));}
        if let Some(cursor)=search.cursor {pairs.push(("cursor",cursor));}
        let target=self.target(SEARCH,&pairs)?;
        let page:SearchPage=self.call(&target,true,MAX_PAGE)?;
        if page.items.len()>usize::from(search.limit)||page.total<page.items.len() as u64
            ||page.has_more!=page.next_cursor.is_some()
            ||(page.has_more&&page.items.is_empty())
            ||page.next_cursor.as_deref().is_some_and(|c|!valid_search_cursor(c))
            ||page.items.iter().any(|hit|hit.text_preview.as_ref().is_some_and(|v|v.chars().nth(1200).is_some())) {
            self.active=false;
            return Err(Error::Protocol);
        }
        self.verify_archived(page.items.iter().map(|hit|&hit.message),search.conversation_id)?;
        Ok(page)
    }

    /// Retrieve a chronological window around one exact normalized message ID.
    pub fn message_context(&mut self,conversation_id:&str,message_id:&str,window:Window)
        ->Result<MessageContext,Error> {
        if !self.navigation.context {return Err(Error::Unsupported);}
        let target=self.context_target(CONTEXT,conversation_id,"message_id",message_id,window)?;
        let result:MessageContext=self.call(&target,true,MAX_PAGE)?;
        self.verify_context(&result,conversation_id,message_id,window)?;
        Ok(result)
    }

    /// Resolve the canonical target of a WeChat quote inside this exact
    /// account/conversation. No raw provider-side IDs or cross-chat fallback.
    pub fn quoted_message_context(&mut self,conversation_id:&str,reference_message_id:&str,window:Window)
        ->Result<QuotedContext,Error> {
        if !self.navigation.quote {return Err(Error::Unsupported);}
        let target=self.context_target(QUOTED,conversation_id,"reference_message_id",reference_message_id,window)?;
        let result:QuotedContext=self.call(&target,true,MAX_PAGE)?;
        if !valid_identifier(&result.target_message_id,1024)||result.target_message_id==reference_message_id {
            self.active=false;
            return Err(Error::Protocol);
        }
        self.verify_context(&result.context,conversation_id,&result.target_message_id,window)?;
        Ok(result)
    }

    fn context_target(&self,path:&'static str,conversation:&str,id_field:&'static str,id:&str,
        window:Window)->Result<String,Error> {
        if !valid_identifier(conversation,1024)||!valid_identifier(id,1024)
            ||window.before>50||window.after>50 {return Err(Error::Input);}
        let before=window.before.to_string();
        let after=window.after.to_string();
        self.target(path,&[("conversation_id",conversation),(id_field,id),("before",&before),("after",&after)])
    }

    fn verify_archived<'m>(&mut self,mut rows:impl Iterator<Item=&'m ArchivedMessage>,
        conversation:Option<&str>)->Result<(),Error> {
        for msg in &mut rows {
            if msg.platform!=self.scope.platform||msg.account_id!=self.scope.account_id
                ||conversation.is_some_and(|id|msg.conversation_id!=id) {
                self.active=false;
                return Err(Error::Identity);
            }
            if !valid_identifier(&msg.conversation_id,1024)||!valid_identifier(&msg.id,1024) {
                self.active=false;
                return Err(Error::Protocol);
            }
        }
        Ok(())
    }

    fn verify_context(&mut self,result:&MessageContext,conversation:&str,id:&str,window:Window)->Result<(),Error> {
        self.verify_archived(result.items.iter().map(|item|&item.message),Some(conversation))?;
        let len=result.items.len();
        if result.total<len as u64
            || result.items.get(result.target_index).is_none_or(|item|item.message.id!=id)
            || result.target_index>usize::from(window.before)
            || len.saturating_sub(result.target_index.saturating_add(1))>usize::from(window.after)
            || result.items.windows(2).any(|pair|pair[0].message.timestamp>pair[1].message.timestamp) {
            self.active=false;
            return Err(Error::Protocol);
        }
        Ok(())
    }

    pub fn media_list(&mut self,conversation_id:&str,message_id:&str)->Result<Vec<MediaItem>,Error> {
        if !self.media {return Err(Error::Unsupported);}
        if !valid_identifier(conversation_id,512)||!valid_identifier(message_id,512) {return Err(Error::Input);}
        let target=self.target(MEDIA,&[("conversation_id",conversation_id),("message_id",message_id)])?;
        let response:MediaList=self.call(&target,true,1024*1024)?;
        if response.items.iter().any(|item|!hex64(&item.id)||item.size>8*1024*1024) {
            self.active=false;
            return Err(Error::Protocol);
        }
        Ok(response.items)
    }

    fn verify_rows(&mut self,rows:&[Value],conversation:Option<&str>)->Result<(),Error> {
        if rows.iter().any(|row|row["platform"].as_str()!=Some(self.scope.platform.as_str())
            ||row["account_id"].as_str()!=Some(self.scope.account_id.as_str())
            ||conversation.is_some_and(|id|row["conversation_id"].as_str()!=Some(id))) {
            self.active=false;
            return Err(Error::Identity);
        }
        Ok(())
    }

    fn target(&self,path:&str,pairs:&[(&str,&str)])->Result<String,Error> {
        let mut query=url::form_urlencoded::Serializer::new(String::new());
        query.append_pair("platform",&self.scope.platform).append_pair("account_id",&self.scope.account_id);
        for (key,value) in pairs {query.append_pair(key,value);}
        let uri=format!("{path}?{}",query.finish());
        if uri.len()>MAX_TARGET {return Err(Error::Input);}
        Ok(uri)
    }

    fn live(&mut self)->Result<(),Error> {
        if self.active && (self.is_current)(self.connection.backend_pid()) {Ok(())}
        else {self.active=false;Err(Error::Stale)}
    }

    fn call<T:for<'de> Deserialize<'de>>(&mut self,target:&str,authenticated:bool,max:usize)->Result<T,Error> {
        self.call_json(target,authenticated,None,max)
    }

    // Only typed, fixed-route submodules may supply a POST. There is no
    // public arbitrary request method or way to forward a credential.
    fn call_json<T:for<'de> Deserialize<'de>>(&mut self,target:&str,authenticated:bool,
        body:Option<&[u8]>,max:usize)->Result<T,Error> {
        self.call_typed(target,authenticated,body.map(Post::Json),max)
    }

    fn call_typed<T:for<'de> Deserialize<'de>>(&mut self,target:&str,authenticated:bool,
        post:Option<Post<'_>>,max:usize)->Result<T,Error> {
        let raw_path = match &post {
            Some(Post::StockPreflight(_)) => Some(crate::import_jobs_http::LEGACY_PREVIEW),
            Some(Post::StockStage { .. }) => Some(crate::import_jobs_http::LEGACY_SUBMIT),
            _ => None,
        };
        if let Some(path) = raw_path {
            if !authenticated || self.connection.mode()!=Mode::NativeLive
                || target.split_once('?').map(|(path,_)|path)!=Some(path)
                || target.len()>MAX_TARGET {
                return Err(Error::Input);
            }
        }
        let idempotency = match &post {
            Some(Post::StockStage { request_id, .. }) => Some(request_id.as_str()),
            _ => None,
        };
        let expected_status = if idempotency.is_some() { 202 } else { 200 };
        let body=post.as_ref().map(Post::content);
        self.call_wire(target,authenticated,body,idempotency,
            expected_status,max,ResponseKind::Json,|bytes|
                serde_json::from_slice(&bytes).map_err(|_|Error::Protocol))
    }

    fn call_wire<T>(&mut self,target:&str,authenticated:bool,body:Option<(&str,&[u8])>,
        idempotency:Option<&str>,expected_status:u16,max:usize,kind:ResponseKind,
        decode:impl FnOnce(Zeroizing<Vec<u8>>)->Result<T,Error>)->Result<T,Error> {
        let connection=&self.connection;
        let is_current=&mut *self.is_current;
        let active=&mut self.active;
        self.grant.with_token(|token| exchange(connection,is_current,active,target,
            authenticated.then_some(token),body,idempotency,expected_status,max,kind,decode))
    }
}

fn assert_live(connection:&Connection,is_current:&mut dyn FnMut(u32)->bool,active:&mut bool)
    ->Result<(),Error> {
    if *active && is_current(connection.backend_pid()){Ok(())}
    else {*active=false;Err(Error::Stale)}
}

fn exchange<T>(connection:&Connection,is_current:&mut dyn FnMut(u32)->bool,active:&mut bool,
    target:&str,token:Option<&str>,body:Option<(&str,&[u8])>,idempotency:Option<&str>,
    expected_status:u16,max:usize,kind:ResponseKind,
    decode:impl FnOnce(Zeroizing<Vec<u8>>)->Result<T,Error>)->Result<T,Error> {
        let result=(|| {
            assert_live(connection,is_current,active)?;
            let address=connection.address();
            let deadline=Instant::now()+TIMEOUT;
            let mut stream=open(address,deadline)?;
            assert_live(connection,is_current,active)?; // check the owning process again before writing a credential
            let mut request=Zeroizing::new(Vec::with_capacity(target.len()+256));
            let method=if body.is_some(){"POST"}else{"GET"};
            let accept=kind.accept();
            write!(&mut request,"{method} {target} HTTP/1.1\r\nHost: {address}\r\nAccept: {accept}\r\nConnection: close\r\n")
                .map_err(|_|Error::Transport)?;
            if let Some((media_type,body))=body {
                write!(&mut request,"Content-Type: {media_type}\r\nContent-Length: {}\r\n",body.len())
                    .map_err(|_|Error::Transport)?;
            }
            if let Some(id) = idempotency {
                // This value can only come from the validated typed staging key.
                write!(&mut request,"Idempotency-Key: {id}\r\n").map_err(|_|Error::Transport)?;
            }
            if let Some(token)=token {
                if !hex64(token){return Err(Error::Identity);}
                write!(&mut request,"Authorization: Bearer {token}\r\n").map_err(|_|Error::Transport)?;
            }
            request.extend_from_slice(b"\r\n");
            send(&mut stream,&request,deadline)?;
            drop(request);
            if let Some((_,body))=body {
                assert_live(connection,is_current,active)?;
                send(&mut stream,body,deadline)?;
            }
            let body=receive(&mut stream,max,deadline,expected_status,kind)?;
            assert_live(connection,is_current,active)?;
            let result=decode(body)?;
            if !matches!(kind,ResponseKind::Json) {assert_live(connection,is_current,active)?;}
            Ok(result)
        })();
        // Terminal protocol/transport failures require new supervisor readiness.
        // A valid 4xx/5xx response is a denial, not a framing or transport failure.
        if result.as_ref().is_err_and(|e|!matches!(e,Error::Status(_))) {*active=false;}
        result
}

fn valid_identifier(value:&str,max:usize)->bool {
    (1..=max).contains(&value.len()) && value.trim()==value && !value.chars().any(char::is_control)
}
fn valid_cursor(cursor:&str)->bool {
    (1..=4096).contains(&cursor.len()) &&
        cursor.bytes().all(|b|b.is_ascii_alphanumeric()||matches!(b,b'-'|b'_'))
}
fn valid_search_cursor(value:&str)->bool {value.len()<=2048&&valid_cursor(value)}
fn hex64(value:&str)->bool {
    value.len()==64 && value.bytes().all(|b|b.is_ascii_digit()||(b'a'..=b'f').contains(&b))
}
fn remaining(deadline:Instant)->Result<Duration,Error> {
    let time=deadline.saturating_duration_since(Instant::now());
    if time.is_zero(){Err(Error::Transport)} else {Ok(time)}
}
fn open(address:SocketAddrV4,deadline:Instant)->Result<TcpStream,Error> {
    if *address.ip()!=Ipv4Addr::LOCALHOST || address.port()==0{return Err(Error::Identity);}
    TcpStream::connect_timeout(&SocketAddr::V4(address),remaining(deadline)?).map_err(|_|Error::Transport)
}
fn send(stream:&mut TcpStream,mut bytes:&[u8],deadline:Instant)->Result<(),Error> {
    while !bytes.is_empty() {
        stream.set_write_timeout(Some(remaining(deadline)?)).map_err(|_|Error::Transport)?;
        match stream.write(bytes) {
            Ok(0)=>return Err(Error::Transport),
            Ok(sent)=>bytes=&bytes[sent..],
            Err(e) if e.kind()==io::ErrorKind::Interrupted=>{},
            Err(_)=>return Err(Error::Transport),
        }
    }
    Ok(())
}
fn read(stream:&mut TcpStream,bytes:&mut [u8],deadline:Instant)->Result<usize,Error> {
    loop {
        stream.set_read_timeout(Some(remaining(deadline)?)).map_err(|_|Error::Transport)?;
        match stream.read(bytes) {
            Ok(n)=>return Ok(n),
            Err(e) if e.kind()==io::ErrorKind::Interrupted=>{},
            Err(_)=>return Err(Error::Transport),
        }
    }
}
#[derive(Clone,Copy)]
enum ResponseKind {
    Json,
    Octet { exact: usize },
    Zip { exact: usize },
    FilteredExport(crate::export::Format),
    Empty,
}
impl ResponseKind {
    fn accept(self)->&'static str {
        match self {
            Self::Json | Self::Empty=>"application/json",
            Self::Octet{..}=>"application/octet-stream",
            Self::Zip{..}=>"application/zip",
            Self::FilteredExport(format)=>format.content_type(),
        }
    }
}
fn frame(headers:&[u8],max:usize,expected_status:u16,kind:ResponseKind)->Result<usize,Error> {
    let text=std::str::from_utf8(headers).map_err(|_|Error::Protocol)?;
    if !text.is_ascii(){return Err(Error::Protocol);}
    let mut lines=text.split("\r\n");
    let status=lines.next().and_then(|line|line.strip_prefix("HTTP/1.1 "))
        .ok_or(Error::Protocol)?;
    let code=status.split(' ').next().ok_or(Error::Protocol)?;
    if code.len()!=3||!code.bytes().all(|b|b.is_ascii_digit()){return Err(Error::Protocol);}
    let code=code.parse::<u16>().map_err(|_|Error::Protocol)?;
    if (400..=599).contains(&code){return Err(Error::Status(code));}
    if code!=expected_status{return Err(Error::Protocol);} // includes every redirect and informational response
    let (mut length,mut content,mut encoding)=(None,None,None);
    let mut disposition=None;
    for line in lines {
        let (key,value)=line.split_once(':').ok_or(Error::Protocol)?;
        if key.is_empty() || !key.bytes().all(|b|b.is_ascii_alphanumeric()||b==b'-')
            || value.bytes().any(|b|!(b==b'\t'||b>=b' '&&b<b'\x7f')) {return Err(Error::Protocol);}
        let value=value.trim_matches([' ','\t']);
        if key.eq_ignore_ascii_case("transfer-encoding")
            || !matches!(kind,ResponseKind::Json) && key.eq_ignore_ascii_case("content-range") {
            return Err(Error::Protocol);
        }
        if key.eq_ignore_ascii_case("content-length") {
            if length.is_some() || value.is_empty() || !value.bytes().all(|b|b.is_ascii_digit()) {
                return Err(Error::Protocol);
            }
            length=Some(value.parse::<usize>().map_err(|_|Error::TooLarge)?);
        } else if key.eq_ignore_ascii_case("content-type") {
            if content.replace(value).is_some(){return Err(Error::Protocol);}
        } else if key.eq_ignore_ascii_case("content-encoding") {
            if encoding.replace(value).is_some(){return Err(Error::Protocol);}
        } else if matches!(kind,ResponseKind::FilteredExport(_))
            && key.eq_ignore_ascii_case("content-disposition") {
            if disposition.replace(value).is_some(){return Err(Error::Protocol);}
        }
    }
    if let ResponseKind::FilteredExport(format)=kind {
        // JSON and ChatLab JSON have the same MIME. Match the fixed format
        // header too, without exposing or using it as a filesystem name.
        if disposition!=Some(format.disposition()) {return Err(Error::Protocol);}
    }
    if matches!(kind,ResponseKind::Empty) {
        return if length.unwrap_or(0)==0 && content.is_none() && encoding.is_none() {
            Ok(0)
        } else {Err(Error::Protocol)};
    }
    if !content.is_some_and(|s|s.eq_ignore_ascii_case(kind.accept())
        || matches!(kind,ResponseKind::Json) && s.eq_ignore_ascii_case("application/json; charset=utf-8"))
        || encoding.is_some_and(|s|!s.eq_ignore_ascii_case("identity")) {return Err(Error::Protocol);}
    let length=length.ok_or(Error::Protocol)?;
    if let ResponseKind::Octet{exact} | ResponseKind::Zip{exact}=kind {
        if length!=exact{return Err(Error::Protocol);}
    }
    if length>max{Err(Error::TooLarge)} else {Ok(length)}
}
fn receive(stream:&mut TcpStream,max:usize,deadline:Instant,expected_status:u16,kind:ResponseKind)->Result<Zeroizing<Vec<u8>>,Error> {
    receive_with(|bytes|read(stream,bytes,deadline),max,expected_status,kind)
}

fn receive_with(mut read:impl FnMut(&mut [u8])->Result<usize,Error>,max:usize,expected_status:u16,
    kind:ResponseKind)->Result<Zeroizing<Vec<u8>>,Error> {
    let mut chunk=Zeroizing::new([0u8;1024]);
    let n=read(&mut chunk[..])?;
    if n==0{return Err(Error::Protocol);}
    let first=&chunk[..n];
    // A complete header in the first socket read needs no heap prefix and
    // no copy of that read. The stack chunk is still zeroized on every exit.
    if let Some(at)=first.windows(4).position(|x|x==b"\r\n\r\n") {
        let length=frame(&first[..at],max,expected_status,kind)?;
        return read_body(read,&first[at+4..],length,kind);
    }

    // Only fragmented headers need the old bounded accumulation. Keep the
    // same separator search, header cap, and body framing rules.
    let mut prefix=Zeroizing::new(Vec::with_capacity(MAX_HEADERS+1024));
    prefix.extend_from_slice(first);
    loop {
        let n=read(&mut chunk[..])?;
        if n==0{return Err(Error::Protocol);}
        let from=prefix.len().saturating_sub(3);
        prefix.extend_from_slice(&chunk[..n]);
        if let Some(at)=prefix[from..].windows(4).position(|x|x==b"\r\n\r\n").map(|x|x+from) {
            if at>MAX_HEADERS{return Err(Error::TooLarge);}
            let length=frame(&prefix[..at],max,expected_status,kind)?;
            return read_body(read,&prefix[at+4..],length,kind);
        }
        if prefix.len()>=MAX_HEADERS+4 {return Err(Error::TooLarge);}
    }
}

fn read_body(mut read:impl FnMut(&mut [u8])->Result<usize,Error>,buffered:&[u8],length:usize,
    kind:ResponseKind)->Result<Zeroizing<Vec<u8>>,Error> {
    if buffered.len()>length{return Err(Error::Protocol);}
    let mut body=Zeroizing::new(vec![0u8;length]);
    body[..buffered.len()].copy_from_slice(buffered);
    let mut done=buffered.len();
    while done<length {
        let n=read(&mut body[done..])?;
        if n==0{return Err(Error::Protocol);}
        done+=n;
    }
    if !matches!(kind,ResponseKind::Json) && read(&mut Zeroizing::new([0u8;1])[..])?!=0 {
        return Err(Error::Protocol);
    }
    Ok(body)
}

#[cfg(test)]
mod response_read_tests {
    use super::*;
    use std::collections::VecDeque;

    fn supplied<'a>(input:impl IntoIterator<Item=&'a [u8]>)
        ->impl FnMut(&mut [u8])->Result<usize,Error>+'a {
        let mut pending=input.into_iter().collect::<VecDeque<_>>();
        move |output| {
            let Some(next)=pending.pop_front() else {return Ok(0);};
            let used=next.len().min(output.len());
            output[..used].copy_from_slice(&next[..used]);
            if used<next.len(){pending.push_front(&next[used..]);}
            Ok(used)
        }
    }

    #[test]
    fn framing_matches_for_coalesced_and_fragmented_reads() {
        let body=b"null";
        let header=b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 4\r\n\r\n";
        let packet=[header.as_slice(),body].concat();
        for split in 1..packet.len() {
            let parsed=receive_with(supplied([&packet[..split],&packet[split..]]),4,200,ResponseKind::Json).unwrap();
            assert_eq!(parsed.as_slice(),body);
        }
        assert_eq!(receive_with(supplied([packet.as_slice()]),4,200,ResponseKind::Json).unwrap().as_slice(),body);
        assert!(matches!(receive_with(supplied([packet.as_slice()]),3,200,ResponseKind::Json),Err(Error::TooLarge)));
        assert!(matches!(receive_with(supplied([header.as_slice(),&body[..3]]),4,200,ResponseKind::Json),Err(Error::Protocol)));
        let extra=[packet.as_slice(),b"!"].concat();
        assert!(matches!(receive_with(supplied([extra.as_slice()]),4,200,ResponseKind::Json),Err(Error::Protocol)));
        let denied=b"HTTP/1.1 403 Forbidden\r\n\r\n";
        assert!(matches!(receive_with(supplied([denied.as_slice()]),4,200,ResponseKind::Json),Err(Error::Status(403))));
    }

    #[test]
    fn strict_binary_eof_and_fragmented_header_limit_unchanged() {
        let packet=b"HTTP/1.1 200 OK\r\nContent-Type: application/octet-stream\r\nContent-Length: 2\r\n\r\nhi";
        let kind=ResponseKind::Octet{exact:2};
        assert_eq!(receive_with(supplied([packet.as_slice()]),2,200,kind).unwrap().as_slice(),b"hi");
        assert!(matches!(receive_with(supplied([packet.as_slice(),b"!"]),2,200,kind),Err(Error::Protocol)));
        let start=b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 0\r\nX-Fill: ";
        for size in [MAX_HEADERS,MAX_HEADERS+1] {
            let mut wire=start.to_vec();
            wire.resize(size,b'x');
            wire.extend_from_slice(b"\r\n\r\n");
            let result=receive_with(supplied([wire.as_slice()]),0,200,ResponseKind::Json);
            if size==MAX_HEADERS {assert!(result.unwrap().is_empty());}
            else {assert!(matches!(result,Err(Error::TooLarge)));}
        }
    }
}
