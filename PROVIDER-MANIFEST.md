# Provider setup: read-only manifest and compatibility triage

`local-message-hub-rs provider-manifest` emits `hub-provider-manifest-v1` JSON.
It takes no arguments and reads no database, config, environment settings,
filesystem, device or network state. It does not enumerate accounts, aliases,
credentials, addresses, or installation paths. Target OS/architecture are
compile-time properties of the executable producing the response.
`command_wired_for_target` describes the command routing in that source, not
protocol parity, availability of dependencies, installed provider state or a
runtime test. In particular, Windows does not have the private-pipe real IMAP
command. No watched login, background sync or delivery exists in this
provider/alias manifest. Standalone explicit IMAP is listed independently of
profile registration.

The recommended default lanes are local files selected by the operator,
for preview followed by confirmation. Provider profiles remain optional
configuration labels for the six registered adapter names only; they do not
override the real account selector, authorize access, run the adapters, or
disable standalone import commands. No persistent provider credentials or
provider addresses are accepted. See [profile safety](PROVIDER-PROFILES.md).

| Source | Start with | What still blocks real integration |
| --- | --- | --- |
| Personal WeChat | [Exact detached text](WECHAT-DETACHED-IMPORT.md) with independently established stable scope and sender direction. Existing normalized provider page remains count-only. | No supported official personal-history interchange contract verified here; native recovery/backup files are not suitable interchange. No raw database/client access, media, dedup across identity schemes, or live login. |
| Enterprise WeCom | [Detached WeCom rendered text](DETACHED-PLATFORM-IMPORTS.md), normalized by an authorized operator. | Enterprise conversation archiving is a candidate official route; the enterprise must verify its entitlement, approved authorization/consent scope, official interface versions and lawful handling before any connector work. We did not verify current availability of [WeCom's official documentation page](https://developer.work.weixin.qq.com/document/path/91774) or build an official connector. |
| QQ | Hub-normalized text CSV with explicit sender identity, not raw official client data. | [QQ's official bot platform](https://bot.q.qq.com/wiki/develop/api-v2/) is an option for separately approved bot-scoped events; nothing here establishes personal-account history access through it. A safe live route would need an explicit allowed scope, authenticated events and a stable mapping to a separate archive account. |
| Telegram | [Official Telegram Desktop JSON export](https://telegram.org/blog/export-and-more), restricted to the Rust importer's supported shape/text kinds. | No app or bot login, update/cursor integration, or media sync. A live adapter is a separate authorized integration, not implicit in Desktop exports. |
| Email | Manually exported canonical plain-text EML. For an operator-approved single UID only, see [pinned IMAP](IMAP-READONLY.md) and [confirmed IMAP import](IMAP-READONLY-IMPORT.md). | [IMAP](https://www.rfc-editor.org/rfc/rfc9051.html) does not make this narrow pinned TLS/SASL PLAIN decoder general-purpose. OAuth-only servers, broader legal response grammar/MIME, credential lifecycle, mailbox discovery, epoch handling, automated syncing and SMTP are not supported. The synthetic fixture proves no real-provider compatibility. |
| Campus | Existing detached public text document with an exact local profile. | No verified source discovery or approved feed collector; no remote fetch, account-backed article access, private student records or scoring. Public WeChat article metadata is a local provenance subset, not the personal-chat adapter. |

This is a source-level compatibility assessment. The external alternatives
are not enabled, verified with real accounts, or parity claims. No provider
requests or local runtime verification were performed for this change.
