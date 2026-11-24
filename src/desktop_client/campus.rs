//! Managed typed access to detached Campus articles. A Campus credential is
//! separate from the message Desktop grant and remains confined to one profile.
use super::{Error, HEALTH, MANIFEST, MAX_TARGET, ResponseKind, exchange, hex64, valid_identifier};
use crate::{campus::{item_id, slug}, campus_http, desktop_sdk::{Connection, Mode}, plugins::PluginKind};
use chrono::{Datelike, NaiveDate};
use serde::{Deserialize, Serialize};
use serde_json::json;
use zeroize::Zeroizing;

#[derive(Deserialize)]
struct Manifest {
    api_version:u8,
    plugin_protocol_version:u8,
    backend:String,
    mode:String,
    plugin:Principal,
    campus_offline:Offline,
    campus_local_state:Local,
}
#[derive(Deserialize)]
struct Principal {
    id:String,
    kind:PluginKind,
    permissions:Vec<String>,
    campus_profile:String,
    account_scope:Option<serde_json::Value>,
}
#[derive(Deserialize)]
struct Offline {items:String, offline:bool, fetch:bool, models:bool, autosync:bool}
#[derive(Deserialize)]
struct Local {digest:String, article_state:String, can_write_bookmarks:bool}

pub struct CampusClient<'a> {
    connection:Connection,
    credential:Zeroizing<String>,
    profile:String,
    is_current:Box<dyn FnMut(u32)->bool+'a>,
    active:bool,
    editor:bool,
}

#[derive(Clone, Copy)]
pub struct Filter<'a> {
    pub text:Option<&'a str>,
    pub category:Option<&'a str>,
    pub source_id:Option<&'a str>,
    pub limit:u16,
    pub offset:u16,
}
impl Default for Filter<'_> {
    fn default()->Self {Self{text:None,category:None,source_id:None,limit:25,offset:0}}
}
#[derive(Deserialize)]
pub struct Article {
    pub id:String,
    pub title:String,
    pub published_at:String,
    pub summary:String,
    pub source_id:String,
    pub source_name:String,
    pub category:String,
}
#[derive(Deserialize)]
pub struct ArticleDetail {
    #[serde(flatten)]
    pub article:Article,
    pub content:String,
}
#[derive(Deserialize)]
pub struct Page {pub total:u64,pub items:Vec<Article>}
#[derive(Deserialize)]
pub struct CategoryCount {pub category:String,pub count:u64}
#[derive(Deserialize)]
pub struct Digest {
    pub date:String,
    pub total:u64,
    pub items:Vec<Article>,
    pub category_counts:Vec<CategoryCount>,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ArticleState {pub bookmarked:bool,pub read:bool,pub revision:u64}
#[derive(Clone,Copy,Serialize)]
pub struct StateChanges {
    pub expected_revision:u64,
    #[serde(skip_serializing_if="Option::is_none")]
    pub bookmarked:Option<bool>,
    #[serde(skip_serializing_if="Option::is_none")]
    pub read:Option<bool>,
}

const MAX_REVISION:u64=9_007_199_254_740_991;

impl<'a> CampusClient<'a> {
    /// Authorize only a separately installed reader or basic editor token,
    /// obtained outside argv/env. Does not create or elevate a grant.
    pub fn connect(connection:Connection,credential:Zeroizing<String>,grant_id:&str,
        profile:&str,is_current:impl FnMut(u32)->bool+'a)->Result<Self,Error> {
        if connection.mode()!=Mode::NativeLive {return Err(Error::Unsupported);}
        if !hex64(&credential)||!slug(profile)||!valid_identifier(grant_id,64){return Err(Error::Input);}
        let mut client=Self{connection,credential,profile:profile.to_owned(),is_current:Box::new(is_current),
            active:true,editor:false};
        #[derive(Deserialize)]
        #[serde(deny_unknown_fields)]
        struct Health {status:String}
        let health:Health=client.call(HEALTH,false,None,1024)?;
        if health.status!="ok" {return Err(Error::Protocol);}
        let m:Manifest=client.call(MANIFEST,true,None,64*1024)?;
        let editor=m.plugin.kind==PluginKind::CampusEditor;
        let permission=|p:&str|m.plugin.permissions.iter().any(|v|v==p);
        if m.api_version!=1 || m.plugin_protocol_version!=1 || m.backend!="local-message-hub-rs"
            || m.mode!="native-live" || !m.plugin.kind.is_campus() || m.plugin.id!=grant_id
            || m.plugin.account_scope.is_some() || m.plugin.campus_profile!=profile
            || m.plugin.permissions.len()!=if editor {2}else{1}
            || !permission("campus.read") || permission("campus.bookmarks.write")!=editor
            || !m.campus_offline.offline || m.campus_offline.fetch || m.campus_offline.models
            || m.campus_offline.autosync || m.campus_offline.items!=campus_http::ITEMS
            || m.campus_local_state.digest!=campus_http::DIGEST
            || m.campus_local_state.article_state!=campus_http::DETAIL_STATE
            || m.campus_local_state.can_write_bookmarks!=editor {
            return Err(Error::Identity);
        }
        client.editor=editor;
        Ok(client)
    }

    pub fn can_edit_state(&self)->bool {self.editor}

    /// Imported, detached observations, not live provider search.
    pub fn articles(&mut self,filter:Filter<'_>)->Result<Page,Error> {
        if !(1..=50).contains(&filter.limit)||filter.offset>4096
            || filter.text.is_some_and(|q|q.len()>512||q.chars().any(char::is_control))
            || filter.category.is_some_and(|s|!slug(s))||filter.source_id.is_some_and(|s|!slug(s)) {
            return Err(Error::Input);
        }
        let limit=filter.limit.to_string();
        let offset=filter.offset.to_string();
        let mut pairs=vec![("limit",limit.as_str()),("offset",offset.as_str())];
        pairs.extend([("q",filter.text),("category",filter.category),("source_id",filter.source_id)]
            .into_iter().filter_map(|(key,value)|value.map(|v|(key,v))));
        let target=self.target(campus_http::ITEMS,&pairs)?;
        let page:Page=self.call(&target,true,None,1024*1024)?;
        if page.items.len()>usize::from(filter.limit)||page.items.len() as u64>page.total
            || page.items.iter().any(|a|!item_id(&a.id)) {return self.bad_response();}
        Ok(page)
    }

    /// Exact-day local publication digest; no scheduler, ranking or fetching.
    pub fn digest(&mut self,date:&str,limit:u16)->Result<Digest,Error> {
        if !(1..=50).contains(&limit)||!date_valid(date){return Err(Error::Input);}
        let limit_string=limit.to_string();
        let target=self.target(campus_http::DIGEST,&[("date",date),("limit",&limit_string)])?;
        let digest:Digest=self.call(&target,true,None,1024*1024)?;
        if digest.date!=date||digest.items.len()>usize::from(limit)||digest.items.len() as u64>digest.total
            ||digest.items.iter().any(|a|a.published_at!=date||!item_id(&a.id)) {return self.bad_response();}
        Ok(digest)
    }

    pub fn article(&mut self,id:&str)->Result<ArticleDetail,Error> {
        let path=article_path(id,false)?;
        let detail:ArticleDetail=self.call(&self.target(&path,&[])?,true,None,256*1024)?;
        if detail.article.id!=id||detail.content.len()>16*1024 {return self.bad_response();}
        Ok(detail)
    }

    pub fn article_state(&mut self,id:&str)->Result<ArticleState,Error> {
        let path=article_path(id,true)?;
        let state=self.call(&self.target(&path,&[])?,true,None,1024)?;
        self.check_state(state)
    }

    /// The caller must deliberately choose a mutation and supply the revision
    /// seen. HTTP 409 and ambiguous transport errors are never retried.
    pub fn change_article_state(&mut self,id:&str,change:StateChanges)->Result<ArticleState,Error> {
        if !self.editor {return Err(Error::Unsupported);}
        if change.expected_revision>MAX_REVISION||(change.bookmarked.is_none()&&change.read.is_none()){
            return Err(Error::Input);
        }
        let path=article_path(id,true)?;
        let mut body=serde_json::to_value(change).map_err(|_|Error::Input)?;
        body["profile"]=json!(self.profile);
        let payload=Zeroizing::new(serde_json::to_vec(&body).map_err(|_|Error::Input)?);
        let state=self.call(&path,true,Some(payload.as_slice()),1024)?;
        let state=self.check_state(state)?;
        if state.revision<change.expected_revision || state.revision>change.expected_revision+1
            || change.bookmarked.is_some_and(|v|v!=state.bookmarked)
            || change.read.is_some_and(|v|v!=state.read) {
            return self.bad_response();
        }
        Ok(state)
    }

    fn check_state(&mut self,state:ArticleState)->Result<ArticleState,Error> {
        if state.revision>MAX_REVISION||(state.revision==0&&(state.read||state.bookmarked)) {
            return self.bad_response();
        }
        Ok(state)
    }
    fn bad_response<T>(&mut self)->Result<T,Error> {self.active=false;Err(Error::Protocol)}

    fn target(&self,path:&str,extra:&[(&str,&str)])->Result<String,Error> {
        let mut pairs=url::form_urlencoded::Serializer::new(String::new());
        pairs.append_pair("profile",&self.profile);
        for (k,v) in extra {pairs.append_pair(k,v);}
        let result=format!("{path}?{}",pairs.finish());
        if result.len()>MAX_TARGET {return Err(Error::Input);}
        Ok(result)
    }

    fn call<T:for<'de> Deserialize<'de>>(&mut self,target:&str,authenticated:bool,body:Option<&[u8]>,
        max:usize)->Result<T,Error> {
        exchange(&self.connection,&mut *self.is_current,&mut self.active,target,
            authenticated.then_some(self.credential.as_str()),body.map(|b|("application/json",b)),
            None,200,max,ResponseKind::Json,|bytes|serde_json::from_slice(&bytes).map_err(|_|Error::Protocol))
    }
}

fn article_path(id:&str,state:bool)->Result<String,Error> {
    if !item_id(id){return Err(Error::Input);}
    if state {Ok(campus_http::DETAIL_STATE.replace("{id}",id))}
    else {Ok(format!("{}/{id}",campus_http::ITEMS))}
}
fn date_valid(s:&str)->bool {
    s.len()==10&&NaiveDate::parse_from_str(s,"%Y-%m-%d").ok().is_some_and(|d|
        d.year()>0&&d.format("%Y-%m-%d").to_string()==s)
}
