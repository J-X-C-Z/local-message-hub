//! Effective, redacted desktop protocol discovery. No archive/provider probes.
use super::{ApiError, Host};
use crate::{plugins::{Permission,Plugin,PluginKind}, drafts_http, draft_review_http,
    client_prefs_http, metadata_http, notifications_http, operations_http, transcription_http};
use axum::{Json,extract::{Extension,RawQuery,State},http::StatusCode};
use serde::Serialize;

pub(super) const NATIVE_PATH:&str="/api/v1/desktop/contract";
#[cfg(target_os="linux")]
pub(super) const BROWSER_PATH:&str="/api/desktop-contract";

#[derive(Clone,Copy,PartialEq,Eq,Serialize)]
#[serde(rename_all="snake_case")]
enum Mode { NativeLive, Snapshot, ImportedMedia, GuardedNative, GuardedLegacy, GuardedBrowser }

#[derive(Serialize)]
#[serde(rename_all="snake_case")]
enum Transport { LoopbackBearer, GuardedLoopbackBearer, GuardedLoopbackCookie }

#[derive(Serialize)]
#[serde(rename_all="snake_case")]
enum Request {
    ExactAccountFromGrant,
    NativeReadQuery,
    LegacyReadQuery,
    LegacyMessageSelector,
    LegacyScopedContextSelector,
    LegacyScopedConversationSelector,
    ExplicitArchiveTarget,
    ImportedAttachmentSelector,
    ArchiveExportQuery,
    LocalCursor,
    LocalStreamCursor,
    DraftTarget,
    DraftCas,
    DraftReviewId,
    DraftRevisionSelection,
    TranscriptionTarget,
    ManualTranscriptionCas,
    NotificationPolicyCas,
    ClientPreferenceCas,
    RedactedOperationSelector,
}

#[derive(Serialize)]
#[serde(rename_all="snake_case")]
enum Projection {
    ArchivedPage,
    ArchivedConversationContext,
    LegacyProjectedPage,
    LegacyProjectedMessages,
    LegacyProjectedContext,
    ConversationMetadataRevision,
    ArchiveExport,
    GuardStatus,
    AttachmentMetadata,
    AttachmentVerification,
    AttachmentOpaqueReference,
    AttachmentBytes,
    JournalCheckpoint,
    EventMetadataPage,
    EventMetadataStream,
    DraftText,
    DraftReceipt,
    DraftReviewStatus,
    ManualTranscript,
    TranscriptionReceipt,
    NotificationPolicy,
    MetadataOnlyPreview,
    ClientPresentationState,
    RedactedOperationState,
}

#[derive(Serialize)]
#[serde(rename_all="UPPERCASE")]
enum Method { Get, Post }

#[derive(Serialize)]
struct Operation {
    id:&'static str, method:Method, path:&'static str,
    request:Request, response:Projection, content_type:&'static str,
}

#[derive(Serialize)]
#[serde(rename_all="snake_case")]
enum Unavailable {
    ProviderLogin,
    ProviderMessageDelivery,
    ProviderAttachmentFetch,
    ProviderSyncActivation,
    AutomaticVoiceTranscription,
    OperatingSystemNotificationDelivery,
    WebUnlock,
    RemoteBackup,
}

#[derive(Serialize)]
pub(super) struct Descriptor {
    descriptor_version:u32,
    native_api_version:u32,
    backend_version:&'static str,
    mode:Mode,
    transport:Transport,
    scope:&'static str,
    coverage:&'static str,
    openapi:Option<&'static str>,
    operations:Vec<Operation>,
    unavailable_actions:[Unavailable;8],
}

pub(super) fn eligible(plugin:&Plugin)->bool {
    plugin.kind==PluginKind::Desktop && plugin.account_scope.is_some()
}

fn authorize_descriptor(plugin:&Plugin,raw:Option<String>)->Result<(),ApiError> {
    if !eligible(plugin) {
        return Err(ApiError(StatusCode::FORBIDDEN,"permission_required"));
    }
    if raw.is_some(){return Err(ApiError::bad_query());}
    Ok(())
}

struct Catalog(Vec<Operation>);
impl Catalog {
    fn json(&mut self,id:&'static str,path:&'static str,request:Request,response:Projection) {
        self.0.push(Operation{id,method:Method::Get,path,request,response,content_type:"application/json"});
    }
    fn write(&mut self,id:&'static str,path:&'static str,request:Request,response:Projection) {
        self.0.push(Operation{id,method:Method::Post,path,request,response,content_type:"application/json"});
    }
    fn streamed(&mut self,id:&'static str,path:&'static str,request:Request,response:Projection,
        content_type:&'static str) {
        self.0.push(Operation{id,method:Method::Get,path,request,response,content_type});
    }
}

fn describe(mode:Mode,plugin:&Plugin)->Descriptor {
    use Permission as P;
    use Projection as Out;
    use Request as In;
    let permit=|p|plugin.permissions.contains(&p);
    let guarded=matches!(mode,Mode::GuardedNative|Mode::GuardedLegacy|Mode::GuardedBrowser);
    let legacy=matches!(mode,Mode::GuardedLegacy|Mode::GuardedBrowser);
    let standard=matches!(mode,Mode::NativeLive|Mode::Snapshot);
    let mut c=Catalog(Vec::with_capacity(28));

    if mode==Mode::ImportedMedia {
        if permit(P::Messages) && permit(P::Media) {
            let base="/api/v1/messages/media";
            c.json("attachments",base,In::ImportedAttachmentSelector,Out::AttachmentMetadata);
            c.json("verify_attachment","/api/v1/messages/media/{id}/verify",In::ImportedAttachmentSelector,Out::AttachmentVerification);
            c.json("attachment_reference","/api/v1/messages/media/{id}/reference",In::ImportedAttachmentSelector,Out::AttachmentOpaqueReference);
            c.streamed("download_attachment","/api/v1/messages/media/{id}",In::ImportedAttachmentSelector,
                Out::AttachmentBytes,"application/octet-stream");
        }
    } else if legacy {
        if permit(P::Conversations) {
            c.json("conversations","/api/conversations",In::LegacyReadQuery,Out::LegacyProjectedPage);
        }
        if permit(P::Messages) {
            c.json("messages","/api/messages",In::LegacyMessageSelector,Out::LegacyProjectedMessages);
            c.json("message_context","/api/context",In::ExplicitArchiveTarget,Out::LegacyProjectedContext);
            if mode==Mode::GuardedLegacy && permit(P::MessageExport) {
                c.streamed("export_messages","/api/export",In::ArchiveExportQuery,Out::ArchiveExport,"format_dependent");
            }
        }
        if permit(P::LegacySearch) {
            c.json("search","/api/search",In::LegacyReadQuery,Out::LegacyProjectedMessages);
        }
        if permit(P::Contacts) {
            c.json("contacts","/api/contacts",In::LegacyReadQuery,Out::LegacyProjectedPage);
        }
    } else {
        if permit(P::Conversations) {
            c.json("conversations","/api/v1/conversations",In::NativeReadQuery,Out::ArchivedPage);
        }
        if permit(P::Messages) {
            c.json("messages","/api/v1/messages",In::NativeReadQuery,Out::ArchivedPage);
            if standard {
                c.json("search","/api/v1/search",In::NativeReadQuery,Out::ArchivedPage);
                c.json("message_context","/api/v1/messages/context",In::ExplicitArchiveTarget,Out::ArchivedConversationContext);
            }
            if permit(P::MessageExport) {
                c.streamed("export_messages","/api/v1/messages/export",In::ArchiveExportQuery,Out::ArchiveExport,"format_dependent");
            }
        }
        if standard && permit(P::Contacts) {
            c.json("contacts","/api/v1/contacts",In::NativeReadQuery,Out::ArchivedPage);
        }
    }

    if guarded && mode!=Mode::GuardedBrowser && permit(P::Messages) {
        c.json("guard_status",if legacy{"/api/account-safety/status"}else{"/api/v1/account-safety/status"},
            In::ExactAccountFromGrant,Out::GuardStatus);
    }

    if mode==Mode::NativeLive {
        if permit(P::LegacyHttp) && permit(P::Messages) {
            c.json("legacy_message_context","/api/context",In::LegacyScopedContextSelector,Out::LegacyProjectedContext);
            if permit(P::Contacts) {
                c.json("legacy_contacts","/api/contacts",In::LegacyReadQuery,Out::LegacyProjectedPage);
            }
        }
        if permit(P::LegacyHttp) && permit(P::ConversationEdit) {
            c.json("legacy_conversation_metadata",metadata_http::LEGACY_CONVERSATION,
                In::LegacyScopedConversationSelector,Out::ConversationMetadataRevision);
        }
        if permit(P::Events) {
            c.json("event_checkpoint","/api/v1/events/checkpoint",In::ExactAccountFromGrant,Out::JournalCheckpoint);
            c.json("event_replay","/api/v1/events",In::LocalCursor,Out::EventMetadataPage);
            c.streamed("event_stream","/api/v1/events/stream",In::LocalStreamCursor,Out::EventMetadataStream,"text/event-stream");
        }
        if permit(P::Conversations) && permit(P::DraftRead) {
            c.json("local_draft",drafts_http::PATH,In::DraftTarget,Out::DraftText);
            if permit(P::DraftWrite) {
                c.write("edit_local_draft",drafts_http::PATH,In::DraftCas,Out::DraftReceipt);
            }
            if cfg!(unix) && permit(P::DraftReview) {
                c.json("own_draft_review",draft_review_http::PATH,In::DraftReviewId,Out::DraftReviewStatus);
                c.write("queue_draft_review",draft_review_http::PATH,In::DraftRevisionSelection,Out::DraftReviewStatus);
            }
        }
        if permit(P::Messages) && permit(P::TranscriptionRead) {
            c.json("manual_transcript",transcription_http::PATH,In::TranscriptionTarget,Out::ManualTranscript);
            if permit(P::TranscriptionWrite) {
                c.write("write_manual_transcript",transcription_http::PATH,In::ManualTranscriptionCas,Out::TranscriptionReceipt);
            }
        }
        if permit(P::NotificationRead) {
            c.json("notification_preferences",notifications_http::PREFERENCES,In::ExactAccountFromGrant,Out::NotificationPolicy);
            c.json("notification_preview",notifications_http::PREVIEW,In::LocalCursor,Out::MetadataOnlyPreview);
            if permit(P::NotificationWrite) {
                c.write("write_notification_preferences",notifications_http::PREFERENCES,In::NotificationPolicyCas,Out::NotificationPolicy);
            }
        }
        if permit(P::ClientPreferences) {
            c.json("client_preferences",client_prefs_http::PATH,In::ExactAccountFromGrant,Out::ClientPresentationState);
            c.write("write_client_preferences",client_prefs_http::PATH,In::ClientPreferenceCas,Out::ClientPresentationState);
        }
        if crate::operations::has_view(plugin) {
            c.json("local_operation_status",operations_http::PATH,In::RedactedOperationSelector,Out::RedactedOperationState);
        }
    }

    Descriptor {
        descriptor_version:1,native_api_version:1,backend_version:env!("CARGO_PKG_VERSION"),
        mode,transport:if mode==Mode::GuardedBrowser {Transport::GuardedLoopbackCookie}
            else if guarded {Transport::GuardedLoopbackBearer}else{Transport::LoopbackBearer},
        scope:"one_exact_account",
        coverage:"desktop_core_not_route_inventory",
        openapi:(!legacy).then_some("/api/v1/openapi.json"),operations:c.0,
        unavailable_actions:[Unavailable::ProviderLogin,Unavailable::ProviderMessageDelivery,
            Unavailable::ProviderAttachmentFetch,Unavailable::ProviderSyncActivation,
            Unavailable::AutomaticVoiceTranscription,Unavailable::OperatingSystemNotificationDelivery,
            Unavailable::WebUnlock,Unavailable::RemoteBackup],
    }
}

pub(super) async fn native(State(host):State<Host>,Extension(plugin):Extension<Plugin>,RawQuery(raw):RawQuery)
    ->Result<Json<Descriptor>,ApiError> {
    authorize_descriptor(&plugin,raw)?;
    let mode=if host.archive.is_live(){Mode::NativeLive}
        else if host.archive.has_media(){Mode::ImportedMedia}
        else if host.archive.legacy(){Mode::GuardedLegacy}
        else if host.archive.guarded().is_some(){Mode::GuardedNative}
        else {Mode::Snapshot};
    Ok(Json(describe(mode,&plugin)))
}

#[cfg(target_os="linux")]
pub(super) async fn browser(Extension(plugin):Extension<Plugin>,RawQuery(raw):RawQuery)
    ->Result<Json<Descriptor>,ApiError> {
    authorize_descriptor(&plugin,raw)?;
    Ok(Json(describe(Mode::GuardedBrowser,&plugin)))
}
