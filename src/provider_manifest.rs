//! Public source/deployment description for an operator. No probing or configuration reads.
use serde::Serialize;

#[derive(Serialize)]
#[serde(rename_all = "kebab-case")]
enum Mode {
    DetachedLocal,
    SyntheticLoopbackFixture,
    ExplicitOperatorTls,
}
#[derive(Serialize)]
#[serde(rename_all = "kebab-case")]
enum Requirement {
    OperatorReviewedDetachedInput,
    ExactAccount,
    ExactStableNamespace,
    ExplicitSenderIdentity,
    ExactCampusProfile,
    SeparatePreviewConfirmation,
    SyntheticPinnedLoopbackEndpoint,
    ExplicitNetworkOptIn,
    OperatorVerifiedTlsTrust,
    PrivateCredentialPipe,
    ExactUidAndEpoch,
    ExplicitPriorCursor,
}
#[derive(Serialize)]
struct Lane {
    platform: &'static str,
    format: &'static str,
    profile_adapter: Option<&'static str>,
    mode: Mode,
    recommended: bool,
    command_wired_for_target: bool,
    preview_command: &'static str,
    import_command: Option<&'static str>,
    requirements: &'static [Requirement],
    exclusions: &'static [&'static str],
}

#[derive(Serialize)]
struct Registration {
    mode: &'static str,
    scope_fields: &'static [&'static str],
    list_command: &'static str,
    create_command: &'static str,
    set_intent_command: &'static str,
    retire_command: &'static str,
    accepts_credentials: bool,
    resolves_alias_to_provider_account: bool,
    activates_provider: bool,
}

#[derive(Serialize)]
struct Deployment {
    target_os: &'static str,
    target_arch: &'static str,
    status_basis: &'static str,
    runtime_probed: bool,
    configured_provider_probed: bool,
    background_sync_supported: bool,
    outbound_delivery_supported: bool,
}

#[derive(Clone, Copy, Serialize)]
pub(crate) struct WechatWecomBoundary {
    platform: &'static str,
    surface: &'static str,
    built_in_live_access: bool,
    live_history_access: bool,
    authority_required: &'static str,
    local_input: &'static str,
}

static LOCAL_CHAT_BOUNDARIES: [WechatWecomBoundary; 2] = [
    WechatWecomBoundary {
        platform: "wechat", surface: "personal-chat",
        built_in_live_access: false, live_history_access: false,
        authority_required: "lawfully-prepared-input-and-explicit-exact-account",
        local_input: "operator-authorized-pinned-text-json-or-normalized-detached-text",
    },
    WechatWecomBoundary {
        platform: "wecom", surface: "pinned-rendered-local-snapshot",
        built_in_live_access: false, live_history_access: false,
        authority_required: "operator-authorized-rendered-snapshot-for-exact-account",
        local_input: "operator-authorized-rendered-text-or-ordered-local-snapshot-batch",
    },
];

pub(crate) fn local_chat_boundary(platform: &str) -> Option<&'static WechatWecomBoundary> {
    LOCAL_CHAT_BOUNDARIES.iter().find(|boundary| boundary.platform == platform)
}

#[derive(Serialize)]
pub struct Manifest {
    schema: &'static str,
    deployment: Deployment,
    profile_registration: Registration,
    lanes: [Lane; 11],
    wechat_wecom_boundaries: [WechatWecomBoundary; 2],
}

pub fn read() -> Manifest {
    use Requirement::*;
    let imap_platform = cfg!(any(target_os = "linux", target_os = "macos"));
    Manifest {
        schema: "hub-provider-manifest-v1",
        deployment: Deployment {
            target_os: std::env::consts::OS,
            target_arch: std::env::consts::ARCH,
            status_basis: "compiled-command-routing-only",
            runtime_probed: false,
            configured_provider_probed: false,
            background_sync_supported: false,
            outbound_delivery_supported: false,
        },
        profile_registration: Registration {
            mode: "ordinary-native-local-intent-only",
            scope_fields: &["adapter", "account_alias", "profile_id"],
            list_command: "provider-profiles",
            create_command: "provider-profile-create",
            set_intent_command: "provider-profile-set",
            retire_command: "provider-profile-retire",
            accepts_credentials: false,
            resolves_alias_to_provider_account: false,
            activates_provider: false,
        },
        wechat_wecom_boundaries: LOCAL_CHAT_BOUNDARIES,
        lanes: [
            Lane {
                platform: "wecom", format: "wecom-rendered-v1",
                profile_adapter: Some("wecom-rendered"), mode: Mode::DetachedLocal,
                recommended: true, command_wired_for_target: true, preview_command: "preview-wecom-json",
                import_command: Some("import-wecom-json"),
                requirements: &[OperatorReviewedDetachedInput, ExactAccount, SeparatePreviewConfirmation],
                exclusions: &["live-enterprise-archive-api", "media retrieval", "account login"],
            },
            Lane {
                platform: "wecom", format: "wecom-rendered-sync-v1",
                profile_adapter: None, mode: Mode::DetachedLocal,
                recommended: true, command_wired_for_target: true, preview_command: "preview-wecom-sync",
                import_command: Some("import-wecom-sync"),
                requirements: &[OperatorReviewedDetachedInput, ExactAccount, ExactStableNamespace,
                    ExplicitPriorCursor, SeparatePreviewConfirmation],
                exclusions: &["live vendor cursor", "raw source acquisition", "attachments", "client invocation"],
            },
            Lane {
                platform: "wechat", format: "wechat-detached-text-v1",
                profile_adapter: None, mode: Mode::DetachedLocal,
                recommended: true, command_wired_for_target: true, preview_command: "preview-wechat-detached",
                import_command: Some("import-wechat-detached"),
                requirements: &[OperatorReviewedDetachedInput, ExactAccount, ExactStableNamespace,
                    ExplicitSenderIdentity, SeparatePreviewConfirmation],
                exclusions: &["raw-client-backup", "key-or-process extraction", "cross-format merge", "media retrieval"],
            },
            Lane {
                platform: "wechat", format: "wechat-provider-page-v1",
                profile_adapter: Some("wechat-provider-page"), mode: Mode::DetachedLocal,
                recommended: false, command_wired_for_target: true, preview_command: "preview-wechat-provider-page",
                import_command: None, requirements: &[OperatorReviewedDetachedInput, ExactAccount],
                exclusions: &["raw-client-backup", "message-content export", "archive import"],
            },
            Lane {
                platform: "qq", format: "hub-normalized-text-csv",
                profile_adapter: Some("qq-hub-csv"), mode: Mode::DetachedLocal,
                recommended: true, command_wired_for_target: true, preview_command: "preview-qq-hub-csv",
                import_command: Some("import-qq-hub-csv"),
                requirements: &[OperatorReviewedDetachedInput, ExactAccount, ExplicitSenderIdentity, SeparatePreviewConfirmation],
                exclusions: &["QQ-account-login", "personal-account-history API", "bot subscription", "media retrieval"],
            },
            Lane {
                platform: "telegram", format: "telegram-desktop-json-supported-text-subset",
                profile_adapter: Some("telegram-desktop-json"), mode: Mode::DetachedLocal,
                recommended: true, command_wired_for_target: true, preview_command: "preview-telegram-json",
                import_command: Some("import-telegram-json"),
                requirements: &[OperatorReviewedDetachedInput, ExactAccount, SeparatePreviewConfirmation],
                exclusions: &["MTProto or bot login", "continuous updates", "media retrieval"],
            },
            Lane {
                platform: "email", format: "single-canonical-plain-text-eml",
                profile_adapter: Some("mail-eml"), mode: Mode::DetachedLocal,
                recommended: true, command_wired_for_target: true, preview_command: "preview-eml",
                import_command: Some("import-eml"),
                requirements: &[OperatorReviewedDetachedInput, ExactAccount, SeparatePreviewConfirmation],
                exclusions: &["multipart or attachments", "remote mailbox access", "SMTP"],
            },
            Lane {
                platform: "email", format: "synthetic-imap-fixture-plaintext",
                profile_adapter: Some("mail-imap-fixture"), mode: Mode::SyntheticLoopbackFixture,
                recommended: false, command_wired_for_target: true, preview_command: "preview-imap-fixture",
                import_command: Some("import-imap-fixture"),
                requirements: &[SyntheticPinnedLoopbackEndpoint, ExactAccount, ExactUidAndEpoch,
                    ExplicitPriorCursor, SeparatePreviewConfirmation],
                exclusions: &["real-mailbox-login", "provider compatibility"],
            },
            Lane {
                platform: "email", format: "pinned-imap-single-uid-counts",
                profile_adapter: None, mode: Mode::ExplicitOperatorTls,
                recommended: false, command_wired_for_target: imap_platform, preview_command: "preview-imap-readonly",
                import_command: None,
                requirements: &[ExplicitNetworkOptIn, OperatorVerifiedTlsTrust, PrivateCredentialPipe, ExactUidAndEpoch],
                exclusions: &["archive import", "OAuth or discovery", "full mailbox traversal", "SMTP"],
            },
            Lane {
                platform: "email", format: "pinned-imap-single-uid-direct-incoming-text",
                profile_adapter: None, mode: Mode::ExplicitOperatorTls,
                recommended: false, command_wired_for_target: imap_platform, preview_command: "preview-imap-import",
                import_command: Some("import-imap-readonly"),
                requirements: &[ExplicitNetworkOptIn, OperatorVerifiedTlsTrust, PrivateCredentialPipe, ExactAccount,
                    ExactUidAndEpoch, ExplicitPriorCursor, SeparatePreviewConfirmation],
                exclusions: &["OAuth or discovery", "managed sync", "arbitrary MIME", "epoch auto-reset", "SMTP"],
            },
            Lane {
                platform: "campus", format: "campus-detached-json-public-text",
                profile_adapter: None, mode: Mode::DetachedLocal,
                recommended: true, command_wired_for_target: true, preview_command: "preview-campus-json",
                import_command: Some("import-campus-json"),
                requirements: &[OperatorReviewedDetachedInput, ExactCampusProfile, SeparatePreviewConfirmation],
                exclusions: &["remote article fetch", "private student records", "account-backed collection", "model scoring"],
            },
        ],
    }
}
