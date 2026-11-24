use super::{ApiError,ArchiveQuery,ContextQuery,ExtractedQuery,Host,annual_report_scoped,pair_report_scoped,archive_op,guarded_messages_op,guarded_legacy_context_op,fetch_context,serve_export,export_contacts,observed_response,indexed_accounts,indexed_stats,mcp_discovery,guarded_conversation_state,lock_status,private_profiles,group_profiles,self_sent_daily,relationship_summary,relationship_timeline,relationship_achievements,require,scoped_context,scoped_query};
use crate::plugins::{Permission,Plugin};
use axum::{Extension,Json,Router,extract::{RawQuery,State},http::StatusCode,response::Response,routing::get};
use serde::Deserialize;
use serde_json::{Value,json};

const CAPS: &[(&str,&str)] = &[
    ("campus","中大学生信息站"),("search","全局搜索"),("chat","聊天记录"),("contacts","联系人"),
    ("media","媒体库"),("attachment_download","主动下载附件"),("stats","基础统计"),
    ("imports","文件导入"),("sync","本机同步"),("wechat_live_receive","个人微信实时接收"),
    ("export","聊天导出"),("organization","本地整理"),("qq_history","QQ 在线漫游历史"),("mcp","MCP 服务"),
    ("advanced_analytics","高级统计"),("annual_reports","年度与双人报告"),
    ("qq_send","QQ 文本发送"),("wecom_bot_send","企业微信机器人消息发送"),
    ("moments","朋友圈"),("voice_transcription","本机语音转写"),("notifications","桌面通知"),
    ("auto_export","自动导出"),("relationship_footprint","关系足迹"),("account_safety","账号安全与应用锁"),
    ("anti_recall","防撤回拦截"),("wechat_send","个人微信文本发送"),("wechat_auto_send","个人微信自动发送"),
];

pub(super) fn routes(router:Router<Host>) -> Router<Host> {
    common_routes(router).route("/api/settings",get(settings))
        .route("/api/context",get(native_context))
        .route("/api/messages",get(native_messages))
        .route("/api/account-safety/status",get(lock_status))
        .route("/api/mcp",get(mcp_discovery))
        .route("/api/conversation-metadata",get(guarded_metadata))
        .route("/api/accounts",get(accounts)).route("/api/stats",get(stats))
        .route("/api/export",get(export)).route("/api/export/contacts",get(contact_export))
        .route("/api/conversation-members",get(observed_members)).route("/api/export/group-members",get(export_members)).route("/api/reports/annual",get(annual))
        .route("/api/reports/pair",get(pair))
        .route("/api/analytics/private",get(analytics_private))
        .route("/api/analytics/groups",get(analytics_groups))
        .route("/api/analytics/self-sent-daily",get(analytics_self))
        .route("/api/analytics/relationships",get(analytics_relationship))
        .route("/api/analytics/relationships/timeline",get(analytics_timeline))
        .route("/api/analytics/relationships/achievements",get(analytics_achievements))
}
/// Ordinary live desktop compatibility is opt-in and not the guarded
/// route family or its cookie-facing browser subset.
pub(super) fn native_organizer_reads(router:Router<Host>) -> Router<Host> {
    router.route("/api/conversations",get(native_live_conversations))
        .route("/api/messages",get(native_live_messages))
        .route("/api/search",get(native_live_search))
        .route("/api/context",get(native_live_context))
        .route("/api/contacts",get(native_live_contacts))
}

pub(super) fn browser_routes(router:Router<Host>) -> Router<Host> {
    common_routes(router).route("/api/settings",get(browser_settings))
        .route("/api/context",get(context))
        .route("/api/messages",get(browser_messages))
}

fn common_routes(router:Router<Host>) -> Router<Host> {
    router.route("/api/sources",get(sources))
        .route("/api/conversations",get(conversations))
        .route("/api/search",get(search))
        .route("/api/contacts",get(contacts))
        .route("/api/quoted-message",get(quoted))
}

pub(super) fn error(status:StatusCode) -> Json<Value> {
    let text=match status {
        StatusCode::LOCKED => return Json(json!({"error":"应用已锁定","code":"account_locked"})),
        StatusCode::FORBIDDEN => "仅允许获授权的本机客户端访问",
        StatusCode::UNAUTHORIZED => "需要本机客户端授权",
        StatusCode::BAD_REQUEST | StatusCode::URI_TOO_LONG => "无效查询参数",
        StatusCode::METHOD_NOT_ALLOWED => "此接口只允许读取",
        StatusCode::NOT_FOUND => "接口不存在",
        _ => "本机服务暂不可用",
    };
    Json(json!({"error":text}))
}

fn no_query(raw:Option<String>) -> Result<(),ApiError> {
    if raw.is_some() {return Err(ApiError::bad_query());}
    Ok(())
}

async fn sources(State(host):State<Host>, Extension(plugin):Extension<Plugin>, RawQuery(raw):RawQuery)
    -> Result<Json<Value>,ApiError> {
    no_query(raw)?;
    let scope=plugin.account_scope.ok_or(ApiError(StatusCode::FORBIDDEN,"permission_required"))?;
    archive_op(host, move |store| {
        let db=store.connection()?;
        let (messages,conversations):(i64,i64)=db.query_row(
            "SELECT (SELECT count(*) FROM messages WHERE platform=?1 AND account_id=?2),
             (SELECT count(*) FROM conversations WHERE platform=?1 AND account_id=?2)",
             (&scope.platform,&scope.account_id),|r|Ok((r.get(0)?,r.get(1)?)))?;
        Ok(json!({"sources":[{"id":scope.platform,"state":"disconnected","version":"","last_sync":null,
                 "error":"","progress":"","message_count":messages,"conversation_count":conversations}]}))
    }).await.map(Json)
}

async fn settings(State(host):State<Host>, Extension(plugin):Extension<Plugin>, RawQuery(raw):RawQuery) -> Result<Json<Value>,ApiError> {
    settings_response(host,plugin,raw,true).await
}
async fn browser_settings(State(host):State<Host>, Extension(plugin):Extension<Plugin>, RawQuery(raw):RawQuery) -> Result<Json<Value>,ApiError> {
    settings_response(host,plugin,raw,false).await
}
async fn settings_response(host:Host, plugin:Plugin, raw:Option<String>, export_enabled:bool) -> Result<Json<Value>,ApiError> {
    no_query(raw)?;
    archive_op(host,move |store| {
        let fts=store.status()?["fts_enabled"].as_bool().unwrap_or(false);
        let capabilities= CAPS.iter().map(|(id,name)|{
            let ready=match *id {
                "chat" => true,
                "stats" => export_enabled && plugin.permissions.contains(&Permission::Contacts),
                "search" => plugin.permissions.contains(&Permission::LegacySearch),
                "contacts" => plugin.permissions.contains(&Permission::Contacts),
                "export" => export_enabled && plugin.permissions.contains(&Permission::MessageExport),
                "annual_reports" => export_enabled && (plugin.permissions.contains(&Permission::AnnualReport)
                    || plugin.permissions.contains(&Permission::PairReport)),
                "advanced_analytics" => export_enabled && plugin.permissions.contains(&Permission::AnalyticsProfiles),
                "relationship_footprint" => export_enabled && plugin.permissions.contains(&Permission::RelationshipRead),
                _ => false,
            };
            ((*id).to_owned(),json!({
                "name":name,"available":ready,"enabled":ready,"default":ready,
                "reason":if ready {""} else {"此只读适配器不提供这项功能"},
            }))
        }).collect::<serde_json::Map<_,_>>();
        Ok(json!({"annual_activity_enabled":export_enabled && plugin.permissions.contains(&Permission::AnnualReport),
            "pair_activity_enabled":export_enabled && plugin.permissions.contains(&Permission::PairReport),"account_lock_enabled":true,"account_lock_lease_seconds":crate::profile_lock::LEASE.as_secs(),
            "auto_sync":false,"mcp_write_enabled":false,"auto_download_attachments":false,
            "fts_enabled":fts,"capabilities":capabilities}))
    }).await.map(Json)
}

fn valid_encoding(raw:Option<String>) -> Result<(),ApiError> {
    if raw.as_deref().is_some_and(|query| !super::valid_legacy_query_encoding(query)) {
        return Err(ApiError::bad_query());
    }
    Ok(())
}

async fn analytics_private(State(host):State<Host>,Extension(plugin):Extension<Plugin>,RawQuery(raw):RawQuery,
    query:ExtractedQuery<crate::chat_analytics::ProfileQuery>) -> Result<Json<Value>,ApiError> {
    valid_encoding(raw)?;
    private_profiles(State(host),Extension(plugin),query).await
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct LegacyGroups {
    platform:Option<String>,account_id:Option<String>,start:Option<String>,end:Option<String>,
    conversation_id:Option<String>,limit:Option<u32>,offset:Option<u32>,member_limit:Option<u32>,
}
async fn analytics_groups(State(host):State<Host>,Extension(plugin):Extension<Plugin>,RawQuery(raw):RawQuery,
    query:ExtractedQuery<LegacyGroups>) -> Result<Json<Value>,ApiError> {
    valid_encoding(raw)?;
    let q=require(&plugin,Permission::AnalyticsProfiles,query)?;
    if q.member_limit.is_some_and(|n|n!=0) {return Err(ApiError::bad_query());}
    group_profiles(State(host),Extension(plugin),Ok(axum::extract::Query(crate::chat_analytics::ProfileQuery {
        platform:q.platform,account_id:q.account_id,start:q.start,end:q.end,conversation_id:q.conversation_id,
        limit:q.limit,offset:q.offset,
    }))).await
}
async fn analytics_self(State(host):State<Host>,Extension(plugin):Extension<Plugin>,RawQuery(raw):RawQuery,
    query:ExtractedQuery<crate::chat_analytics::DailyQuery>) -> Result<Json<Value>,ApiError> {
    valid_encoding(raw)?;
    self_sent_daily(State(host),Extension(plugin),query).await
}
async fn analytics_relationship(State(host):State<Host>,Extension(plugin):Extension<Plugin>,RawQuery(raw):RawQuery,
    query:ExtractedQuery<crate::relationship::SummaryQuery>) -> Result<Json<Value>,ApiError> {
    valid_encoding(raw)?;
    let Json(item)=relationship_summary(State(host),Extension(plugin),query).await?;
    Ok(Json(json!({"items":[item],"scope":"one_local_private_conversation"})))
}
async fn analytics_timeline(State(host):State<Host>,Extension(plugin):Extension<Plugin>,RawQuery(raw):RawQuery,
    query:ExtractedQuery<crate::relationship::TimelineQuery>) -> Result<Json<Value>,ApiError> {
    valid_encoding(raw)?;
    relationship_timeline(State(host),Extension(plugin),query).await
}
async fn analytics_achievements(State(host):State<Host>,Extension(plugin):Extension<Plugin>,RawQuery(raw):RawQuery,
    query:ExtractedQuery<crate::relationship::AchievementQuery>) -> Result<Json<Value>,ApiError> {
    valid_encoding(raw)?;
    relationship_achievements(State(host),Extension(plugin),query).await
}

async fn guarded_metadata(State(host):State<Host>,Extension(plugin):Extension<Plugin>,RawQuery(raw):RawQuery,
    query:ExtractedQuery<super::GuardedConversationState>) -> Result<Json<Value>,ApiError> {
    valid_encoding(raw)?;
    guarded_conversation_state(State(host),Extension(plugin),query).await
}

async fn accounts(State(host):State<Host>,Extension(plugin):Extension<Plugin>,RawQuery(raw):RawQuery,
    query:ExtractedQuery<crate::index_overview::Selector>) -> Result<Json<Value>,ApiError> {
    valid_encoding(raw)?;
    indexed_accounts(State(host),Extension(plugin),query).await
}
async fn stats(State(host):State<Host>,Extension(plugin):Extension<Plugin>,RawQuery(raw):RawQuery,
    query:ExtractedQuery<crate::index_overview::Selector>) -> Result<Json<Value>,ApiError> {
    valid_encoding(raw)?;
    indexed_stats(State(host),Extension(plugin),query).await
}

async fn contact_export(State(host):State<Host>,Extension(plugin):Extension<Plugin>,RawQuery(raw):RawQuery,
    query:ExtractedQuery<crate::grouped_exports::ContactExport>) -> Result<Response,ApiError> {
    valid_encoding(raw)?;
    export_contacts(State(host),Extension(plugin),query).await
}
async fn observed_members(State(host):State<Host>,Extension(plugin):Extension<Plugin>,RawQuery(raw):RawQuery,
    query:ExtractedQuery<crate::grouped_exports::Observed>) -> Result<Response,ApiError> {
    valid_encoding(raw)?;
    let q=require(&plugin,Permission::Messages,query)?;
    if q.source.as_deref()!=Some("observed") {return Err(ApiError::bad_query());}
    observed_response(host,plugin,q,false).await
}
async fn export_members(State(host):State<Host>,Extension(plugin):Extension<Plugin>,RawQuery(raw):RawQuery,
    query:ExtractedQuery<crate::grouped_exports::Observed>) -> Result<Response,ApiError> {
    valid_encoding(raw)?;
    let q=require(&plugin,Permission::MessageExport,query)?;
    if q.source.as_deref()!=Some("observed") {return Err(ApiError::bad_query());}
    observed_response(host,plugin,q,true).await
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Export {
    #[serde(default)] platform:String,
    #[serde(default)] account_id:String,
    conversation_id:String,
    #[serde(default)] format:crate::export::Format,
    #[serde(default)] start:String,
    #[serde(default)] end:String,
}

async fn export(State(host):State<Host>, Extension(plugin):Extension<Plugin>, RawQuery(raw):RawQuery,
    query:ExtractedQuery<Export>) -> Result<Response,ApiError> {
    valid_encoding(raw)?;
    let q=require(&plugin,Permission::MessageExport,query)?;
    if q.start.len()>128 || q.end.len()>128 {return Err(ApiError::bad_query());}
    serve_export(host,plugin,crate::export::ExportQuery{
        platform:q.platform,account_id:q.account_id,conversation_id:q.conversation_id,format:q.format,
        start:crate::store::parse_bound(&q.start,false).map_err(ApiError::store)?,
        end:crate::store::parse_bound(&q.end,true).map_err(ApiError::store)?,
    }).await
}

#[derive(Default,Deserialize)]
#[serde(default,deny_unknown_fields)]
struct Conversations {
    platform:String,
    account_id:String,
    q:String,
    limit:Option<i64>,
    offset:Option<i64>,
}

#[derive(Default,Deserialize)]
#[serde(default,deny_unknown_fields)]
struct Messages {
    platform:String,
    account_id:String,
    conversation_id:String,
    q:String,
    #[serde(rename="type")] kind:String,
    start:String,
    end:String,
    cursor:String,
    limit:Option<i64>,
    offset:Option<i64>,
}

const CONVERSATION_FIELDS:&[&str]=&["id","platform","account_id","title","kind","member_count",
    "last_message","last_timestamp","message_count","note","tags","archived"];
const MESSAGE_FIELDS:&[&str]=&["id","platform","account_id","conversation_id","sender_id","sender_name",
    "timestamp","type","is_self","text","conversation_title","conversation_kind"];

fn projection(mut result:Value, fields:&[&str], message:bool) -> Value {
    if let Some(items)=result["items"].as_array_mut() {
        for item in items {
            if let Some(object)=item.as_object_mut() {
                object.retain(|key,_|fields.contains(&key.as_str()));
                object.insert("avatar_url".into(),Value::Null);
                object.insert("extra".into(),json!({}));
                if message {
                    object.remove("avatar_url");
                    for unsupported in ["media_url","file_name","raw_type","sender_avatar_url"] {
                        object.insert(unsupported.into(),Value::Null);
                    }
                }
            }
        }
    }
    result
}

async fn conversations(State(host):State<Host>, Extension(plugin):Extension<Plugin>, RawQuery(raw):RawQuery,
    query:ExtractedQuery<Conversations>) -> Result<Json<Value>,ApiError> {
    valid_encoding(raw)?;
    let data=require(&plugin,Permission::Conversations,query)?;
    let q=scoped_query(&plugin,ArchiveQuery {platform:data.platform,account_id:data.account_id,q:data.q,
        limit:data.limit,offset:data.offset,..Default::default()})?;
    archive_op(host,move |store| store.conversations(&q).map(|result|projection(result,CONVERSATION_FIELDS,false))).await.map(Json)
}

fn native_live_principal(host:&Host,plugin:&Plugin)->Result<(),ApiError>{
    if !matches!(&host.archive,super::Archive::Live(_)) {
        return Err(ApiError(StatusCode::NOT_FOUND,"not_found"));
    }
    if plugin.kind!=crate::plugins::PluginKind::Desktop || plugin.account_scope.is_none()
        || !plugin.permissions.contains(&Permission::LegacyHttp) {
        return Err(ApiError(StatusCode::FORBIDDEN,"permission_required"));
    }
    Ok(())
}

async fn native_live_conversations(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    raw:RawQuery,query:ExtractedQuery<Conversations>)->Result<Json<Value>,ApiError>{
    native_live_principal(&host,&plugin)?;
    conversations(State(host),Extension(plugin),raw,query).await
}

fn native_live_message_query(host:&Host,plugin:&Plugin,raw:Option<String>,
    query:ExtractedQuery<Messages>,is_search:bool)->Result<(ArchiveQuery,bool),ApiError>{
    native_live_principal(host,plugin)?;
    valid_encoding(raw)?;
    if is_search && !plugin.permissions.contains(&Permission::LegacySearch) {
        return Err(ApiError(StatusCode::FORBIDDEN,"permission_required"));
    }
    let data=require(plugin,Permission::Messages,query)?;
    // Never turn a missing message identity into an account-wide query.
    // Search intentionally covers only the account bound to the grant.
    let absent=!is_search && (data.platform.is_empty() || data.platform=="all"
        || data.account_id.is_empty() || data.conversation_id.is_empty());
    let scoped=scoped_query(plugin,ArchiveQuery{platform:data.platform,account_id:data.account_id,
        conversation_id:data.conversation_id,q:data.q,kind_type:data.kind,start:data.start,end:data.end,
        cursor:data.cursor,limit:data.limit,offset:data.offset,..Default::default()})?;
    Ok((scoped,absent))
}

async fn native_live_messages(State(host):State<Host>,Extension(plugin):Extension<Plugin>,RawQuery(raw):RawQuery,
    query:ExtractedQuery<Messages>)->Result<Json<Value>,ApiError>{
    let (q,absent)=native_live_message_query(&host,&plugin,raw,query,false)?;
    let grant_id=plugin.id;
    archive_op(host,move |store|{
        if absent {
            if !q.cursor.is_empty(){return Err(crate::store::StoreError::BadQuery("message scope required"));}
            return Ok(json!({"items":[],"total":0,"has_more":false,"next_cursor":null}));
        }
        store.live_legacy_message_page(&q,&grant_id,true)
            .map(|result|projection(result,MESSAGE_FIELDS,true))
    }).await.map(Json)
}

async fn native_live_search(State(host):State<Host>,Extension(plugin):Extension<Plugin>,RawQuery(raw):RawQuery,
    query:ExtractedQuery<Messages>)->Result<Json<Value>,ApiError>{
    let (q,_)=native_live_message_query(&host,&plugin,raw,query,true)?;
    let grant_id=plugin.id;
    archive_op(host,move |store|store.live_legacy_message_page(&q,&grant_id,false)
        .map(|result|projection(result,MESSAGE_FIELDS,true)))
        .await.map(Json)
}

async fn native_messages(State(host):State<Host>, Extension(plugin):Extension<Plugin>, RawQuery(raw):RawQuery,
    query:ExtractedQuery<Messages>) -> Result<Json<Value>,ApiError> {
    let grant=plugin.id.clone();
    messages_for(host,plugin,raw,query,Some(grant)).await
}

async fn browser_messages(State(host):State<Host>, Extension(plugin):Extension<Plugin>, RawQuery(raw):RawQuery,
    query:ExtractedQuery<Messages>) -> Result<Json<Value>,ApiError> {
    messages_for(host,plugin,raw,query,None).await
}

async fn messages_for(host:Host,plugin:Plugin,raw:Option<String>,query:ExtractedQuery<Messages>,
    native_grant:Option<String>) -> Result<Json<Value>,ApiError> {
    valid_encoding(raw)?;
    let data=require(&plugin,Permission::Messages,query)?;
    // Stock GET with missing identity looks for an empty/all row, not a wildcard.
    let absent=data.platform.is_empty() || data.platform=="all" || data.account_id.is_empty() || data.conversation_id.is_empty();
    let q=scoped_query(&plugin,ArchiveQuery {platform:data.platform,account_id:data.account_id,
        conversation_id:data.conversation_id,q:data.q,kind_type:data.kind,start:data.start,end:data.end,
        cursor:data.cursor,limit:data.limit,offset:data.offset,..Default::default()})?;
    if absent {
        return archive_op(host, move |_| {
            if !q.cursor.is_empty() {return Err(crate::store::StoreError::BadQuery("message scope required"));}
            Ok(json!({"items":[],"total":0,"has_more":false,"next_cursor":null}))
        }).await.map(Json);
    }
    guarded_messages_op(host,q,native_grant,|result| projection(result,MESSAGE_FIELDS,true)).await.map(Json)
}

async fn search(State(host):State<Host>, Extension(plugin):Extension<Plugin>, RawQuery(raw):RawQuery,
    query:ExtractedQuery<Messages>) -> Result<Json<Value>,ApiError> {
    valid_encoding(raw)?;
    let data=require(&plugin,Permission::LegacySearch,query)?;
    let q=scoped_query(&plugin,ArchiveQuery {platform:data.platform,account_id:data.account_id,
        conversation_id:data.conversation_id,q:data.q,kind_type:data.kind,start:data.start,end:data.end,
        cursor:data.cursor,limit:data.limit,offset:data.offset,..Default::default()})?;
    if !host.archive.legacy() {
        return Err(ApiError(StatusCode::NOT_FOUND, "not_found"));
    }
    archive_op(host,move |store| store.search_guarded_legacy(&q,&plugin.id)
        .map(|result|projection(result,MESSAGE_FIELDS,true))).await.map(Json)
}

#[derive(Default,Deserialize)]
#[serde(default,deny_unknown_fields)]
struct Contacts {
    platform:String,
    account_id:String,
    q:String,
    kind:String,
    limit:Option<i64>,
    offset:Option<i64>,
}
const CONTACT_FIELDS:&[&str]=&["id","platform","account_id","name","kind"];

async fn contacts(State(host):State<Host>, Extension(plugin):Extension<Plugin>, RawQuery(raw):RawQuery,
    query:ExtractedQuery<Contacts>) -> Result<Json<Value>,ApiError> {
    valid_encoding(raw)?;
    let data=require(&plugin,Permission::Contacts,query)?;
    let q=scoped_query(&plugin,ArchiveQuery {platform:data.platform,account_id:data.account_id,
        q:data.q,kind:data.kind,limit:data.limit,offset:data.offset,..Default::default()})?;
    archive_op(host,move |store|store.projected_contacts(&q).map(|result|projection(result,CONTACT_FIELDS,false))).await.map(Json)
}

async fn native_live_contacts(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    raw:RawQuery,query:ExtractedQuery<Contacts>)->Result<Json<Value>,ApiError>{
    native_live_principal(&host,&plugin)?;
    contacts(State(host),Extension(plugin),raw,query).await
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Context {
    platform:String,
    account_id:String,
    conversation_id:String,
    message_id:String,
    before:Option<i64>,
    after:Option<i64>,
}

async fn context(State(host):State<Host>, Extension(plugin):Extension<Plugin>, RawQuery(raw):RawQuery,
    query:ExtractedQuery<Context>) -> Result<Json<Value>,ApiError> {
    valid_encoding(raw)?;
    let data=require(&plugin,Permission::Messages,query)?;
    if data.platform=="all" || data.platform.is_empty() || data.account_id.is_empty() {
        return Err(ApiError::bad_query());
    }
    let q=scoped_context(&plugin,ContextQuery {platform:data.platform,account_id:data.account_id,
        conversation_id:data.conversation_id,message_id:data.message_id,before:data.before,after:data.after,..Default::default()})?;
    fetch_context(host,move |store|store.message_context(&q).map(|row|row.map(|result|projection(result,MESSAGE_FIELDS,true)))).await
}

async fn native_live_context(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    RawQuery(raw):RawQuery,query:ExtractedQuery<NativeContext>)->Result<Json<Value>,ApiError>{
    native_live_principal(&host,&plugin)?;
    valid_encoding(raw)?;
    let data=require(&plugin,Permission::Messages,query)?;
    if data.platform=="all" || data.platform.is_empty() || data.account_id.is_empty()
        || data.conversation_id.as_deref()==Some("")
    {
        return Err(ApiError::bad_query());
    }
    let infer=data.conversation_id.is_none();
    let q=scoped_context(&plugin,ContextQuery {platform:data.platform,account_id:data.account_id,
        conversation_id:data.conversation_id.unwrap_or_default(),message_id:data.message_id,
        before:data.before,after:data.after,..Default::default()})?;
    fetch_context(host,move |store|{
        let row=if infer {store.live_legacy_context(q)}else{store.message_context(&q)}?;
        Ok(row.map(|value|projection(value,MESSAGE_FIELDS,true)))
    }).await
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct NativeContext {
    platform:String,
    account_id:String,
    conversation_id:Option<String>,
    message_id:String,
    before:Option<i64>,
    after:Option<i64>,
}

async fn native_context(State(host):State<Host>, Extension(plugin):Extension<Plugin>, RawQuery(raw):RawQuery,
    query:ExtractedQuery<NativeContext>) -> Result<Json<Value>,ApiError> {
    valid_encoding(raw)?;
    let data=require(&plugin,Permission::Messages,query)?;
    if data.platform=="all" || data.platform.is_empty() || data.account_id.is_empty()
        || data.conversation_id.as_deref()==Some("")
    {
        return Err(ApiError::bad_query());
    }
    let infer=data.conversation_id.is_none();
    if infer && (plugin.kind!=crate::plugins::PluginKind::Desktop || plugin.account_scope.is_none()) {
        return Err(ApiError(StatusCode::NOT_FOUND,"not_found"));
    }
    let q=scoped_context(&plugin,ContextQuery {platform:data.platform,account_id:data.account_id,
        conversation_id:data.conversation_id.unwrap_or_default(),message_id:data.message_id,
        before:data.before,after:data.after,..Default::default()})?;
    if infer {
        guarded_legacy_context_op(host,q,|result|projection(result,MESSAGE_FIELDS,true)).await
    } else {
        fetch_context(host,move |store|store.message_context(&q)
            .map(|row|row.map(|result|projection(result,MESSAGE_FIELDS,true)))).await
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Quote {
    platform:Option<String>,
    account_id:String,
    conversation_id:String,
    reference_message_id:String,
    before:Option<i64>,
    after:Option<i64>,
}

async fn quoted(State(host):State<Host>, Extension(plugin):Extension<Plugin>, RawQuery(raw):RawQuery,
    query:ExtractedQuery<Quote>) -> Result<Json<Value>,ApiError> {
    valid_encoding(raw)?;
    let data=require(&plugin,Permission::Messages,query)?;
    let platform=data.platform.unwrap_or_else(||"wechat".into());
    if platform!="wechat" || data.account_id.is_empty() {
        return Err(ApiError::bad_query());
    }
    let q=scoped_context(&plugin,ContextQuery {platform,account_id:data.account_id,
        conversation_id:data.conversation_id,reference_message_id:data.reference_message_id,
        before:data.before,after:data.after,..Default::default()})?;
    fetch_context(host,move |store|store.quoted_message_context(&q).map(|row|row.map(|result|projection(result,MESSAGE_FIELDS,true)))).await
}

async fn annual(State(host):State<Host>, Extension(plugin):Extension<Plugin>, RawQuery(raw):RawQuery,
    query:ExtractedQuery<crate::annual_report::LegacyQuery>) -> Result<Json<Value>,ApiError> {
    valid_encoding(raw)?;
    let q=require(&plugin,Permission::AnnualReport,query)?;
    annual_report_scoped(host,plugin,q,crate::annual_report::Detail::LegacyTypes).await
}

async fn pair(State(host):State<Host>, Extension(plugin):Extension<Plugin>, RawQuery(raw):RawQuery,
    query:ExtractedQuery<crate::pair_report::LegacyQuery>) -> Result<Json<Value>,ApiError> {
    valid_encoding(raw)?;
    let q=require(&plugin,Permission::PairReport,query)?;
    pair_report_scoped(host,plugin,q,crate::annual_report::Detail::LegacyTypes).await
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn query_encoding_allows_literal_unicode_and_percent_but_not_invalid_bytes() {
        assert!(valid_encoding(Some("q=%E4%B8%AD%E6%96%87%25_+literal".into())).is_ok());
        for raw in ["q=%ff","q=%","q=%4","q=%XZ","q=%00","q=%0A"] {
            assert!(valid_encoding(Some(raw.into())).is_err(),"{raw}");
        }
    }
}
