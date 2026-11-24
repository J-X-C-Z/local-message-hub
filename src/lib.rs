pub mod events;
pub mod ingress_policy;
mod source_progress;
mod source_jobs;
mod source_jobs_http;
pub mod host;
pub mod live_http;
pub mod mcp;
#[cfg(target_os="linux")]
pub mod mcp_http;
mod mcp_scoped;
mod mcp_incoming;
mod mcp_metadata;
pub mod plugins;
pub mod snapshot;
pub mod store;
pub mod migration;
mod export;
mod stored_zip;
mod export_presets;
mod export_presets_http;
// Private scheduler scaffold: no driver or public routes are installed yet.
#[allow(dead_code)]
mod export_schedule;
#[cfg(target_os = "linux")]
pub mod export_schedule_cli;
mod grouped_exports;
mod index_overview;
pub mod desktop_sdk;
pub mod desktop_client;
pub mod portable_package;
mod transcription;
mod transcription_http;
mod transcription_legacy;
pub mod transcription_cli;
mod operations;
mod operations_http;
#[cfg(target_os = "linux")]
mod asset_jobs;
#[cfg(target_os = "linux")]
mod asset_jobs_http;
#[cfg(target_os = "linux")]
mod media_catalog_http;
#[cfg(target_os = "linux")]
mod selected_message_bundle;
#[cfg(target_os = "linux")]
pub mod live_imported_media;
#[cfg(target_os = "linux")]
pub mod detached_voice_wav;
#[cfg(target_os = "linux")]
pub mod detached_voice_result;
mod metadata;
mod metadata_http;
mod drafts;
mod drafts_http;
mod draft_review;
#[cfg(any(target_os="linux",target_os="macos"))]
mod draft_handoff;
#[cfg(any(target_os="linux",target_os="macos"))]
mod draft_handoff_receipt;
mod draft_review_http;
pub mod draft_review_cli;
mod client_prefs;
mod client_prefs_http;
mod notifications;
mod notifications_http;
mod notification_pull;
mod settings_http;
pub mod campus;
mod campus_wechat;
mod campus_catalog;
mod campus_events;
mod campus_events_http;
mod campus_scoring;
mod campus_scoring_http;
mod campus_display;
mod campus_display_http;
mod campus_registration;
mod campus_registration_http;
mod campus_preferences;
mod campus_preferences_http;
mod campus_http;
pub mod media_backup;

#[cfg(target_os = "linux")]
pub mod media_bundle;

pub mod legacy_import;
mod import_jobs;
mod import_jobs_http;
pub mod telegram_import;
pub mod detached_platform;
pub mod wechat_detached;
pub mod wechat_source;
pub mod wecom_sync;
pub mod wecom_bot_text;
#[cfg(any(target_os = "linux", target_os = "macos"))]
mod wecom_bot_release;
pub mod provider_profiles;
pub mod provider_manifest;
pub mod qq_csv_import;
pub mod eml_import;
pub mod imap_fixture;
#[cfg(any(target_os = "linux", target_os = "macos"))]
pub mod imap_readonly;
#[cfg(any(target_os = "linux", target_os = "macos"))]
pub mod imap_readonly_import;

mod profile_lock;
#[cfg(target_os = "linux")]
pub mod guarded_cli;

#[cfg(target_os = "linux")]
mod guarded_snapshot;

#[cfg(target_os = "linux")]
mod guarded_backup;

#[cfg(target_os = "linux")]
mod guarded_backup_catalog;

#[cfg(target_os = "linux")]
mod guarded_backup_retention;

#[cfg(target_os = "linux")]
mod guarded_backup_quarantine;

mod annual_report;
mod chat_analytics;
mod relationship;

mod pair_report;

pub mod moments;
mod moments_http;
