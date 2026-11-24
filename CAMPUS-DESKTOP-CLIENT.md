# Managed Desktop Campus client

Use `desktop_client::campus::CampusClient` with a trusted managed
`desktop-supervise --live` readiness frame and a process-generation liveness
callback, as for the message Desktop SDK. Supply a separately provisioned,
exact-profile Campus reader or basic Campus editor bearer via an in-process
secret, never argv/environment. The client does not generate, install, widen,
or enable a grant: a reader needs only `campus.read`, and a basic editor adds
`campus.bookmarks.write`. The message Desktop credential and Campus credential
are distinct, and broader Campus editor grants are not accepted by this client.

The v1 native-live let a desktop client list/filter detached local articles,
read a known article and its text, view an exact-day publication digest, or
load a known article's read/bookmark state. Only the caller holding the basic
editor grant may explicitly submit a read/bookmark change with its previously
observed revision. A stale revision conflicts; same flags at the exact current
revision are a no-op. There is no automatic mark-read, retry, replay, or URL
navigation. A separate handshake is required after restarting the supervisor;
an ambiguous failed write should be reconciled by reading the exact article
state. No message-account scope, linked URL, profile picker or route URL is
accepted or embedded in the typed API.

The client verifies the exact grant ID/kind/profile and fixed manifest v1
routes before use; it does not follow a manifest URL or HTTP redirect.
`campus_local_state.article_state` advertises the existing versioned template
for this workflow. All data and flags are local archive observations only.
This does not fetch content, rank by a remote model, enroll in provider sync,
write offline intake files, or promise compatibility with the old WebUI.
