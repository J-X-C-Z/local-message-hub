use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{collections::BTreeSet, fs::File, io::Read, path::Path, sync::Arc};
use subtle::ConstantTimeEq;

const MAX_CONFIG_BYTES: u64 = 64 * 1024;
const MAX_GRANTS: usize = 64;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Ord, PartialOrd, Serialize, Deserialize)]
pub enum Permission {
    #[serde(rename = "conversations.read")]
    Conversations,
    #[serde(rename = "messages.read")]
    Messages,
    #[serde(rename = "exports.presets.manage")]
    ExportPresets,
    #[serde(rename = "messages.export")]
    MessageExport,
    #[serde(rename = "media.read")]
    Media,
    #[serde(rename = "media.live_imported.read")]
    LiveImportedMedia,
    #[serde(rename = "media.live_imported.bytes.read")]
    LiveImportedMediaBytes,
    #[serde(rename = "moments.read")]
    MomentsRead,
    #[serde(rename = "moments.export")]
    MomentsExport,
    #[serde(rename = "mcp.discovery.read")]
    McpDiscovery,
    #[serde(rename = "mcp.sources.read")]
    McpSources,
    #[serde(rename = "mcp.prompts.read")]
    McpPrompts,
    #[serde(rename = "mcp.incoming.read")]
    McpIncoming,
    #[serde(rename = "mcp.conversation_note.write")]
    McpConversationNote,
    #[serde(rename = "legacy.http.read")]
    LegacyHttp,
    #[serde(rename = "legacy.browser.read")]
    LegacyBrowser,
    #[serde(rename = "legacy.search.read")]
    LegacySearch,
    #[serde(rename = "analytics.annual.read")]
    AnnualReport,
    #[serde(rename = "analytics.relationships")]
    RelationshipRead,
    #[serde(rename = "analytics.profiles")]
    AnalyticsProfiles,
    #[serde(rename = "analytics.pair.read")]
    PairReport,
    #[serde(rename = "contacts.read")]
    Contacts,
    #[serde(rename = "conversations.metadata.write")]
    ConversationEdit,
    #[serde(rename = "contacts.identity.write")]
    ContactEdit,
    #[serde(rename = "metadata.audit.read")]
    MetadataAudit,
    #[serde(rename = "campus.read")]
    CampusRead,
    #[serde(rename = "campus.sources.preferences.write")]
    CampusSourcePreferenceWrite,
    #[serde(rename = "campus.scoring.write")]
    CampusScoringWrite,
    #[serde(rename = "campus.profile.display.write")]
    CampusDisplayWrite,
    #[serde(rename = "campus.sources.registration.write")]
    CampusRegistrationWrite,
    #[serde(rename = "campus.events.write")]
    CampusEventWrite,
    #[serde(rename = "campus.bookmarks.write")]
    CampusBookmarkWrite,
    #[serde(rename = "drafts.read")]
    DraftRead,
    #[serde(rename = "drafts.write")]
    DraftWrite,
    #[serde(rename = "voice.transcription.read")]
    TranscriptionRead,
    #[serde(rename = "voice.transcription.write")]
    TranscriptionWrite,
    #[serde(rename = "drafts.review.queue")]
    DraftReview,
    #[serde(rename = "client.preferences.manage")]
    ClientPreferences,
    #[serde(rename = "notifications.read")]
    NotificationRead,
    #[serde(rename = "notifications.write")]
    NotificationWrite,
    #[serde(rename = "events.legacy.read")]
    LegacyEventsRead,
    #[serde(rename = "events.read")]
    Events,
    #[serde(rename = "operations.read")]
    OperationsRead,
    #[serde(rename = "imports.local.manage")]
    ImportsManage,
    #[serde(rename = "sources.jobs.manage")]
    SourceJobsManage,
    #[serde(rename = "messages.ingest")]
    Ingest,
}

#[derive(Debug, Clone, Serialize)]
pub struct Plugin {
    pub id: String,
    pub kind: PluginKind,
    pub permissions: BTreeSet<Permission>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub account_scope: Option<AccountScope>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub campus_profile: Option<String>,
}

/// A scoped grant authorizes one exact account within one platform.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AccountScope {
    pub platform: String,
    pub account_id: String,
}

impl AccountScope {
    fn valid(&self) -> bool {
        crate::store::PLATFORMS.contains(&self.platform.as_str())
            && self.platform != "all"
            && (1..=256).contains(&self.account_id.len())
            && self.account_id.trim() == self.account_id
            && !self.account_id.chars().any(char::is_control)
    }

    /// Fill omitted filters; reject any explicit mismatch without looking at storage.
    pub(crate) fn apply(&self, platform: &mut String, account_id: &mut String) -> bool {
        if (!platform.is_empty() && platform != "all" && platform != &self.platform)
            || (!account_id.is_empty() && account_id != &self.account_id)
        {
            return false;
        }
        platform.clone_from(&self.platform);
        account_id.clone_from(&self.account_id);
        true
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum PluginKind {
    Desktop,
    Tool,
    ScopedTool,
    Producer,
    ScopedProducer,
    CampusReader,
    CampusEditor,
}

impl PluginKind {
    pub(crate) fn is_campus(self)->bool { matches!(self,Self::CampusReader|Self::CampusEditor) }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Config {
    version: u8,
    grants: Vec<Grant>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Grant {
    id: String,
    kind: PluginKind,
    permissions: Vec<Permission>,
    token_sha256: String,
    #[serde(default, deserialize_with = "explicit_scope")]
    account_scope: Option<AccountScope>,
    #[serde(default,deserialize_with="explicit_profile")]
    campus_profile:Option<String>,
}

fn explicit_profile<'de,D:serde::Deserializer<'de>>(deserializer:D)->Result<Option<String>,D::Error> {
    String::deserialize(deserializer).map(Some)
}

fn explicit_scope<'de, D: serde::Deserializer<'de>>(
    deserializer: D,
) -> Result<Option<AccountScope>, D::Error> {
    AccountScope::deserialize(deserializer).map(Some)
}

#[derive(Debug, thiserror::Error)]
pub enum ConfigError {
    #[error("could not read plugin grants: {0}")]
    Io(#[from] std::io::Error),
    #[error("invalid plugin grant file")]
    Invalid,
    #[error("plugin grant file exceeds its size limit")]
    TooLarge,
}

#[derive(Clone)]
pub struct Plugins(Arc<Vec<([u8; 32], Plugin)>>);

fn valid_id(id: &str) -> bool {
    !id.is_empty()
        && id.len() <= 64
        && id.split('.').all(|component| {
            let mut bytes = component.bytes();
            bytes.next().is_some_and(|b| b.is_ascii_lowercase())
                && bytes.all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-')
                && !component.ends_with('-')
        })
}

fn is_lower_hex(s: &str) -> bool {
    s.len() == 64
        && s.bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

fn token_digest(token: &str) -> [u8; 32] {
    Sha256::digest(token.as_bytes()).into()
}

impl Plugins {
    pub fn load(path: impl AsRef<Path>) -> Result<Self, ConfigError> {
        let mut bytes = Vec::new();
        File::open(path)?
            .take(MAX_CONFIG_BYTES + 1)
            .read_to_end(&mut bytes)?;
        Self::parse(&bytes)
    }

    pub fn parse(bytes: &[u8]) -> Result<Self, ConfigError> {
        if bytes.len() as u64 > MAX_CONFIG_BYTES {
            return Err(ConfigError::TooLarge);
        }
        let config: Config = serde_json::from_slice(bytes).map_err(|_| ConfigError::Invalid)?;
        if config.version != 1 || config.grants.is_empty() || config.grants.len() > MAX_GRANTS {
            return Err(ConfigError::Invalid);
        }
        let mut ids = BTreeSet::new();
        let mut digests = BTreeSet::new();
        let mut grants = Vec::with_capacity(config.grants.len());
        for grant in config.grants {
            if !valid_id(&grant.id)
                || !is_lower_hex(&grant.token_sha256)
                || grant.account_scope.as_ref().is_some_and(|scope| {
                    !matches!(grant.kind, PluginKind::Desktop | PluginKind::ScopedTool | PluginKind::ScopedProducer) || !scope.valid()
                })
            {
                return Err(ConfigError::Invalid);
            }
            let mut digest = [0u8; 32];
            hex::decode_to_slice(&grant.token_sha256, &mut digest)
                .map_err(|_| ConfigError::Invalid)?;
            let permission_count = grant.permissions.len();
            let permissions: BTreeSet<_> = grant.permissions.into_iter().collect();
            let operations=permissions.contains(&Permission::OperationsRead);
            if operations && (!matches!(grant.kind,PluginKind::Desktop|PluginKind::ScopedProducer)
                || grant.account_scope.is_none()) {return Err(ConfigError::Invalid);}
            if permissions.contains(&Permission::ImportsManage)
                && (grant.kind != PluginKind::Desktop || grant.account_scope.is_none()) {
                return Err(ConfigError::Invalid);
            }
            if permissions.contains(&Permission::ClientPreferences)
                && (grant.kind!=PluginKind::Desktop || grant.account_scope.is_none()) {
                return Err(ConfigError::Invalid);
            }
            if permissions.contains(&Permission::DraftReview)
                && (grant.kind!=PluginKind::Desktop || grant.account_scope.is_none()
                    || !permissions.contains(&Permission::DraftRead) || !permissions.contains(&Permission::Conversations)) {
                return Err(ConfigError::Invalid);
            }
            if permissions.contains(&Permission::LegacyEventsRead)
                && (grant.kind!=PluginKind::Desktop || grant.account_scope.is_none()
                    || !permissions.contains(&Permission::Events)) {
                return Err(ConfigError::Invalid);
            }
            let transcript_read=permissions.contains(&Permission::TranscriptionRead);
            let transcript_write=permissions.contains(&Permission::TranscriptionWrite);
            if ((transcript_read||transcript_write) && (grant.kind!=PluginKind::Desktop
                || grant.account_scope.is_none() || !permissions.contains(&Permission::Messages)))
                || (transcript_write && !transcript_read) {
                return Err(ConfigError::Invalid);
            }
            if permissions.contains(&Permission::McpDiscovery)
                && (grant.kind!=PluginKind::Desktop || grant.account_scope.is_none()
                    || !permissions.contains(&Permission::Messages)) {
                return Err(ConfigError::Invalid);
            }
            let moments_read=permissions.contains(&Permission::MomentsRead);
            let moments_export=permissions.contains(&Permission::MomentsExport);
            if (moments_read || moments_export) && (!moments_read || grant.kind!=PluginKind::Desktop
                || grant.account_scope.as_ref().is_none_or(|scope|scope.platform!="wechat")) {
                return Err(ConfigError::Invalid);
            }
            if permissions.contains(&Permission::ExportPresets)
                && (grant.kind!=PluginKind::Desktop || grant.account_scope.is_none()
                    || !permissions.contains(&Permission::Messages) || !permissions.contains(&Permission::MessageExport)) {
                return Err(ConfigError::Invalid);
            }
            let mcp_permissions = [Permission::McpSources, Permission::McpPrompts, Permission::McpIncoming, Permission::McpConversationNote];
            if grant.kind == PluginKind::ScopedTool {
                if grant.account_scope.as_ref().is_none_or(|scope| matches!(scope.account_id.as_str(),"."|".."))
                    || if permissions.contains(&Permission::McpIncoming) {
                        permissions.len() != 1
                    } else if permissions.contains(&Permission::McpConversationNote) {
                        permissions.len() != 1
                    } else {
                        permissions.iter().any(|p| !matches!(p, Permission::Messages | Permission::McpSources | Permission::McpPrompts))
                            || (!permissions.contains(&Permission::Messages)
                                && (!permissions.contains(&Permission::McpSources) || permissions.contains(&Permission::McpPrompts)))
                    }
                {
                    return Err(ConfigError::Invalid);
                }
            } else if mcp_permissions.iter().any(|p| permissions.contains(p)) {
                return Err(ConfigError::Invalid);
            }
            let custom = [Permission::ConversationEdit, Permission::ContactEdit, Permission::MetadataAudit]
                .iter().any(|permission| permissions.contains(permission));
            if (custom && (grant.kind != PluginKind::Desktop || grant.account_scope.is_none()))
                || (permissions.contains(&Permission::ConversationEdit) && !permissions.contains(&Permission::Conversations))
                || (permissions.contains(&Permission::ContactEdit) && !permissions.contains(&Permission::Contacts))
            {
                return Err(ConfigError::Invalid);
            }
            if permissions.contains(&Permission::LegacyHttp)
                && (grant.kind != PluginKind::Desktop || grant.account_scope.is_none()
                    || !permissions.contains(&Permission::Conversations) || !permissions.contains(&Permission::Messages))
            { return Err(ConfigError::Invalid); }
            if permissions.contains(&Permission::LegacyBrowser) && !permissions.contains(&Permission::LegacyHttp) {
                return Err(ConfigError::Invalid);
            }
            if permissions.contains(&Permission::LegacySearch) && !permissions.contains(&Permission::LegacyHttp) {
                return Err(ConfigError::Invalid);
            }
            if (permissions.contains(&Permission::AnnualReport) || permissions.contains(&Permission::PairReport)
                || permissions.contains(&Permission::AnalyticsProfiles) || permissions.contains(&Permission::RelationshipRead))
                && (grant.kind != PluginKind::Desktop || grant.account_scope.is_none())
            { return Err(ConfigError::Invalid); }
            if (permissions.contains(&Permission::DraftRead) || permissions.contains(&Permission::DraftWrite))
                && (grant.kind != PluginKind::Desktop || grant.account_scope.is_none()
                    || !permissions.contains(&Permission::Conversations)
                    || !permissions.contains(&Permission::DraftRead))
            { return Err(ConfigError::Invalid); }
            if (permissions.contains(&Permission::NotificationRead) || permissions.contains(&Permission::NotificationWrite))
                && (grant.kind != PluginKind::Desktop || grant.account_scope.is_none()
                    || !permissions.contains(&Permission::NotificationRead))
            { return Err(ConfigError::Invalid); }
            if grant.kind.is_campus() {
                let editor=grant.kind==PluginKind::CampusEditor;
                let event_write=permissions.contains(&Permission::CampusEventWrite);
                let source_preferences=permissions.contains(&Permission::CampusSourcePreferenceWrite);
                let scoring=permissions.contains(&Permission::CampusScoringWrite);
                let display=permissions.contains(&Permission::CampusDisplayWrite);
                let registration=permissions.contains(&Permission::CampusRegistrationWrite);
                let expected=if editor {2 + event_write as usize + source_preferences as usize + scoring as usize + display as usize + registration as usize} else {1};
                if !permissions.contains(&Permission::CampusRead)
                    || editor!=permissions.contains(&Permission::CampusBookmarkWrite)
                    || ((event_write||source_preferences||scoring||display||registration) && !editor) || permissions.len()!=expected
                    || !grant.campus_profile.as_deref().is_some_and(crate::campus::slug)
                { return Err(ConfigError::Invalid); }
            } else if permissions.contains(&Permission::CampusRead)
                || permissions.contains(&Permission::CampusBookmarkWrite)
                || permissions.contains(&Permission::CampusEventWrite)
                || permissions.contains(&Permission::CampusSourcePreferenceWrite)
                || permissions.contains(&Permission::CampusScoringWrite)
                || permissions.contains(&Permission::CampusDisplayWrite)
                || permissions.contains(&Permission::CampusRegistrationWrite) || grant.campus_profile.is_some() {
                return Err(ConfigError::Invalid);
            }
            let ingestion = permissions.contains(&Permission::Ingest);
            if (permissions.contains(&Permission::MessageExport) || permissions.contains(&Permission::Media))
                && (grant.kind != PluginKind::Desktop || grant.account_scope.is_none()
                    || !permissions.contains(&Permission::Messages))
            {
                return Err(ConfigError::Invalid);
            }
            if permissions.contains(&Permission::LiveImportedMedia)
                && (grant.kind != PluginKind::Desktop || !permissions.contains(&Permission::Messages)
                    || !permissions.contains(&Permission::Media) || !grant.account_scope.as_ref()
                        .is_some_and(|scope| matches!(scope.platform.as_str(), "qq" | "wechat"))) {
                return Err(ConfigError::Invalid);
            }
            if permissions.contains(&Permission::LiveImportedMediaBytes)
                && !permissions.contains(&Permission::LiveImportedMedia) {
                return Err(ConfigError::Invalid);
            }
            let source_jobs=permissions.contains(&Permission::SourceJobsManage);
            if source_jobs && grant.kind!=PluginKind::ScopedProducer {
                return Err(ConfigError::Invalid);
            }
            if matches!(grant.kind, PluginKind::Producer | PluginKind::ScopedProducer) {
                let required=1+(source_jobs as usize)+(operations as usize);
                if !ingestion || permissions.len()!=required
                    || (grant.kind == PluginKind::ScopedProducer && grant.account_scope.is_none()) {
                    return Err(ConfigError::Invalid);
                }
            } else if ingestion {
                return Err(ConfigError::Invalid);
            }
            if !ids.insert(grant.id.clone())
                || !digests.insert(digest)
                || permissions.len() != permission_count
            {
                return Err(ConfigError::Invalid);
            }
            grants.push((
                digest,
                Plugin {
                    id: grant.id,
                    kind: grant.kind,
                    permissions,
                    account_scope: grant.account_scope,
                    campus_profile: grant.campus_profile,
                },
            ));
        }
        Ok(Self(Arc::new(grants)))
    }

    #[cfg(target_os = "linux")]
    pub(crate) fn browser_principal(&self,id:&str)->Option<Plugin> {
        self.0.iter().map(|(_,p)|p).find(|p|p.id==id && p.kind==PluginKind::Desktop
            && p.account_scope.is_some() && p.permissions.contains(&Permission::LegacyBrowser)
            && p.permissions.contains(&Permission::LegacyHttp)).cloned()
    }

    #[cfg(target_os="linux")]
    pub(crate) fn snapshot_tool_id(&self,id:&str)->bool {
        self.0.iter().any(|(_,plugin)|plugin.id==id && plugin.kind==PluginKind::ScopedTool
            && plugin.account_scope.is_some() && !plugin.permissions.contains(&Permission::McpIncoming)
            && !plugin.permissions.contains(&Permission::McpConversationNote))
    }

    pub fn authenticate(&self, authorization: &str) -> Option<Plugin> {
        let (scheme, token) = authorization.split_once(' ')?;
        if !scheme.eq_ignore_ascii_case("bearer") {
            return None;
        }
        self.authenticate_token(token)
    }

    pub fn authenticate_token(&self, token: &str) -> Option<Plugin> {
        if !is_lower_hex(token) {
            return None;
        }
        let digest = token_digest(token);
        let mut authenticated = None;
        for entry in self.0.iter() {
            if bool::from(entry.0.ct_eq(&digest)) {
                authenticated = Some(entry.1.clone());
            }
        }
        authenticated
    }
}

pub fn generate_token() -> Result<(String, String), getrandom::Error> {
    let mut random = [0u8; 32];
    getrandom::fill(&mut random)?;
    let token = hex::encode(random);
    let digest = hex::encode(token_digest(&token));
    Ok((token, digest))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::{Value,json};

    #[test]
    fn account_scope_is_exact_explicit_and_desktop_only() {
        let (token, hash) = generate_token().unwrap();
        let mut doc = json!({"version":1,"grants":[{
            "id":"example.scoped","kind":"desktop","token_sha256":hash,
            "permissions":["messages.read"],
            "account_scope":{"platform":"wechat","account_id":"example"}
        }]});
        let parse = |value: &serde_json::Value| Plugins::parse(&serde_json::to_vec(value).unwrap());
        let grant = parse(&doc).unwrap().authenticate_token(&token).unwrap();
        assert_eq!(grant.account_scope.unwrap().account_id, "example");
        for illegal in [
            json!(null), json!({"platform":"all","account_id":"one"}),
            json!({"platform":"wechat","account_id":""}),
            json!({"platform":"wechat","account_id":" x"}),
            json!({"platform":"wechat","account_id":"a\u{001f}"}),
            json!({"platform":"wechat","account_id":"é".repeat(129)}),
            json!({"platform":"wechat","account_id":"one","extra":false}),
        ] {
            doc["grants"][0]["account_scope"] = illegal;
            assert!(parse(&doc).is_err());
        }
        doc["grants"][0]["account_scope"] = json!({"platform":"wechat","account_id":"one"});
        doc["grants"][0]["kind"] = json!("tool");
        assert!(parse(&doc).is_err());
        doc["grants"][0]["kind"] = json!("producer");
        doc["grants"][0]["permissions"] = json!(["messages.ingest"]);
        assert!(parse(&doc).is_err());
        doc["grants"][0].as_object_mut().unwrap().remove("account_scope");
        assert!(parse(&doc).is_ok());
    }

    #[test]
    fn scoped_producer_requires_only_ingest_and_explicit_scope() {
        let (_, hash) = generate_token().unwrap();
        let full = json!({"version":1,"grants":[{
            "id":"fixture.source","kind":"scoped-producer","token_sha256":hash,
            "permissions":["messages.ingest"],
            "account_scope":{"platform":"qq","account_id":"account"}
        }]});
        let valid = |doc: &Value| Plugins::parse(&serde_json::to_vec(doc).unwrap()).is_ok();
        assert!(valid(&full));
        for extra in ["messages.read","events.read","mcp.incoming.read"] {
            let mut bad = full.clone();
            bad["grants"][0]["permissions"] = json!(["messages.ingest",extra]);
            assert!(!valid(&bad));
        }
        let mut bad = full.clone();
        bad["grants"][0].as_object_mut().unwrap().remove("account_scope");
        assert!(!valid(&bad));
        bad["grants"][0]["account_scope"] = Value::Null;
        assert!(!valid(&bad));
    }

    #[test]
    fn message_export_requires_desktop_read_and_an_exact_scope() {
        let (_, hash) = generate_token().unwrap();
        let original = json!({"version":1,"grants":[{
            "id":"fixture.desktop","kind":"desktop","token_sha256":hash,
            "permissions":["messages.read","messages.export"],
            "account_scope":{"platform":"wechat","account_id":"one"}
        }]});
        let accepts = |doc: &serde_json::Value| Plugins::parse(&serde_json::to_vec(doc).unwrap()).is_ok();
        assert!(accepts(&original));
        let mut altered = original.clone();
        altered["grants"][0]["permissions"] = json!(["messages.export"]);
        assert!(!accepts(&altered));
        altered = original.clone();
        altered["grants"][0].as_object_mut().unwrap().remove("account_scope");
        assert!(!accepts(&altered));
        altered["grants"][0]["kind"] = json!("tool");
        assert!(!accepts(&altered));
    }

    #[test]
    fn metadata_grants_are_separate_and_exactly_scoped() {
        let (_, hash) = generate_token().unwrap();
        let mut config = json!({"version":1,"grants":[{
            "id":"fixture.desktop","kind":"desktop","token_sha256":hash,
            "permissions":["metadata.audit.read"],"account_scope":{"platform":"wechat","account_id":"owner"}
        }]});
        let valid = |doc: &serde_json::Value| Plugins::parse(&serde_json::to_vec(doc).unwrap()).is_ok();
        assert!(valid(&config));
        for (write, read) in [
            ("conversations.metadata.write","conversations.read"),
            ("contacts.identity.write","contacts.read"),
        ] {
            config["grants"][0]["permissions"] = json!([write]);
            assert!(!valid(&config));
            config["grants"][0]["permissions"] = json!([write,read]);
            assert!(valid(&config));
        }
        config["grants"][0]["kind"] = json!("tool");
        assert!(!valid(&config));
        config["grants"][0]["kind"] = json!("desktop");
        config["grants"][0].as_object_mut().unwrap().remove("account_scope");
        assert!(!valid(&config));
    }

    #[test]
    fn scoped_tool_requires_exact_scope_and_whitelisted_co_grants() {
        let (_, hash) = generate_token().unwrap();
        let grant = json!({"id":"test.scoped","kind":"scoped-tool","token_sha256":hash,
          "account_scope":{"platform":"qq","account_id":"one"},
          "permissions":["messages.read","mcp.sources.read","mcp.prompts.read"]});
        let parses = |grant: Value| Plugins::parse(
            &serde_json::to_vec(&json!({"version":1,"grants":[grant]})).unwrap()).is_ok();
        assert!(parses(grant.clone()));
        let mut incoming = grant.clone();
        incoming["permissions"]=json!(["mcp.incoming.read"]);
        assert!(parses(incoming));
        for permissions in [
            json!([]), json!(["mcp.incoming.read","messages.read"]), json!(["mcp.prompts.read"]), json!(["mcp.sources.read","mcp.prompts.read"]),
            json!(["messages.read","media.read"]), json!(["messages.read","events.read"]),
            json!(["messages.read","messages.unknown"]), json!(["messages.read","messages.ingest"])
        ] {
            let mut bad = grant.clone();
            bad["permissions"] = permissions;
            assert!(!parses(bad));
        }
        for scope in [Value::Null,json!({"platform":"all","account_id":"one"}),
            json!({"platform":"qq","account_id":".."}),json!({"platform":"unknown","account_id":"one"}),
            json!({"platform":"qq","account_id":"one","extra":true})] {
            let mut bad = grant.clone();
            bad["account_scope"] = scope;
            assert!(!parses(bad));
        }
        let mut omitted=grant.clone();
        omitted.as_object_mut().unwrap().remove("account_scope");
        assert!(!parses(omitted));
        for kind in ["tool","desktop","producer"] {
            let mut bad = grant.clone();
            bad["kind"] = json!(kind);
            bad.as_object_mut().unwrap().remove("account_scope");
            assert!(!parses(bad));
        }
    }

    #[test]
    fn legacy_http_requires_exact_desktop_and_both_read_permissions() {
        let (_, hash)=generate_token().unwrap();
        let mut grant=json!({"id":"fixture.legacy","kind":"desktop","token_sha256":hash,
            "account_scope":{"platform":"qq","account_id":"example"},
            "permissions":["legacy.http.read","messages.read","conversations.read"]});
        let valid=|grant:&Value|Plugins::parse(&serde_json::to_vec(&json!({"version":1,"grants":[grant]})).unwrap()).is_ok();
        assert!(valid(&grant));
        let mut with_search=grant.clone();
        with_search["permissions"].as_array_mut().unwrap().push(json!("legacy.search.read"));
        assert!(valid(&with_search));
        with_search["permissions"].as_array_mut().unwrap().retain(|p|p!="legacy.http.read");
        assert!(!valid(&with_search));
        for replacement in [
            json!(["legacy.http.read","messages.read"]),json!(["legacy.http.read","conversations.read"]),
            json!(["legacy.http.read"]),
        ]{
            let mut reduced=grant.clone();
            reduced["permissions"]=replacement;
            assert!(!valid(&reduced));
        }
        for kind in ["tool","scoped-tool","producer"] {
            let mut forged=grant.clone();
            forged["kind"]=json!(kind);
            assert!(!valid(&forged));
        }
        grant.as_object_mut().unwrap().remove("account_scope");
        assert!(!valid(&grant));
    }

    #[test]
    fn annual_activity_permission_is_exact_desktop_only() {
        let (_,hash)=generate_token().unwrap();
        let grant=json!({"id":"fixture.annual","kind":"desktop","token_sha256":hash,
            "account_scope":{"platform":"qq","account_id":"mine"},"permissions":["analytics.annual.read"]});
        let valid=|g:&Value| Plugins::parse(&serde_json::to_vec(&json!({"version":1,"grants":[g]})).unwrap()).is_ok();
        assert!(valid(&grant));
        for kind in ["tool","scoped-tool","producer","desktop"] {
            let mut invalid=grant.clone();
            invalid["kind"]=json!(kind);
            invalid.as_object_mut().unwrap().remove("account_scope");
            assert!(!valid(&invalid));
        }
    }

    #[test]
    fn distinct_grants_never_impersonate_each_other() {
        let (one, one_hash) = generate_token().unwrap();
        let (two, two_hash) = generate_token().unwrap();
        let config = json!({"version":1,"grants":[
            {"id":"example.desktop","kind":"desktop","token_sha256":one_hash,
                "permissions":[Permission::Conversations]},
            {"id":"example.tool","kind":"tool","token_sha256":two_hash,
                "permissions":[Permission::Messages]}
        ]});
        let registry = Plugins::parse(&serde_json::to_vec(&config).unwrap()).unwrap();
        let identity = registry.authenticate(&format!("bEaReR {one}")).unwrap();
        assert_eq!(identity.id, "example.desktop");
        assert!(!identity.permissions.contains(&Permission::Messages));
        assert_eq!(
            registry.authenticate(&format!("Bearer {two}")).unwrap().id,
            "example.tool"
        );
        assert!(
            registry
                .authenticate(&format!("Bearer {two_hash}"))
                .is_none()
        );
        assert!(
            registry
                .authenticate(&format!("Bearer {two} extra"))
                .is_none()
        );

        let mut duplicate_key = config;
        duplicate_key["grants"][1]["token_sha256"] =
            duplicate_key["grants"][0]["token_sha256"].clone();
        assert!(Plugins::parse(&serde_json::to_vec(&duplicate_key).unwrap()).is_err());
        duplicate_key["grants"][1]["token_sha256"] = json!(two_hash);
        duplicate_key["grants"][1]["permissions"] = json!(["messages.write"]);
        assert!(Plugins::parse(&serde_json::to_vec(&duplicate_key).unwrap()).is_err());
    }
}
