# Personal WeChat and enterprise WeCom are separate sources

This note concerns protocol and input boundaries in Rust source. It is
not a statement that a provider is connected, a service is deployed, a
user can log in, or a binary was compiled. A source owner must separately
authorize the appropriate environment and data before real verification.

| Source | Permitted input / authority | Cannot establish |
| --- | --- | --- |
| Personal WeChat detached text | Data independently exported or prepared with authority (see [personal source SDK](PERSONAL-WECHAT-SOURCE.md)), explicit local account, stable adapter/source namespace and message IDs, and exact sender direction. | A general official personal conversation history API, raw backup recovery, key availability, or equivalence of unrelated export IDs. |
| WeCom rendered local snapshot | A detached, independently authorized rendered snapshot with explicit IDs, sender direction and local account (see [WeCom rendered sync](WECOM-DETACHED-SYNC.md)). | Availability of a raw snapshot reader, entitlement to access other chats, or any platform credential. Official app callbacks and the enterprise archive API are distinct products excluded from this port. |

## Archive identity and local availability

Keep personal WeChat under `wechat` and enterprise WeCom under `wecom`.
An exact local account label is routing metadata, not proof of external
identity. The WeCom label does not identify a vendor account or authorize any other dataset. Do not use message IDs, names, phone numbers, agent IDs
or account aliases to merge data across sources. The local availability
endpoint reports normalized index rows only; it never serves as login
status, readiness check, or authority to call a provider.

No attachment fetch, provider credentials, raw client database/key access,
background sync or outbound actions follow from selecting either platform.
An unknown or outside-authority source remains unsupported instead of
falling back to another platform's parser or credential.

## Pinned Python comparison

In the pinned original `hub/server.py`, `sync_wechat` (line 1162) and
`sync_wecom` (line 1264) are invoked by the sync surface. The old source
description calls WeCom a local snapshot; the source does not establish
equivalence between that snapshot, an official app callback, and an
enterprise archive API. The old WeCom Bot credential/approval/send
routes are a separate outbound path. None are fulfilled merely by
normalizing messages in the formats here.

The original `POST /api/sync`, provider configuration, and
the original outbound routes do not change status in the route ledger.

The original personal adapter kept raw row and server ID information for
reconciliation and quotes; a human-readable export does not prove all of
it was included. A scoped import must distinguish missing fields from
known values and must not invent a server ID, author, direction or cursor
from content text. A local batch checkpoint measures Hub ingestion of
detached data, never the position of an upstream WeChat/WeCom database.

048 adds two local boundaries: [explicitly observed per-conversation
WeChat source positions](PERSONAL-WECHAT-CURSOR.md) when an independently
authorized ordered page actually carries the original tuple, and WeCom
[completed-generation metadata reconciliation](WECOM-DETACHED-SYNC.md).
Old exported text cannot yield a cursor. WeCom partial generations and
omissions do not change classification or delete indexed history.
