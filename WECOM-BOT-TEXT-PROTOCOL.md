# WeCom smart-bot outbound text protocol (disconnected)

This is the Rust protocol core for the original application's separate **WeCom
smart-bot identity**, not the personal WeChat client, protected WeCom archive, or
source-job import. It does not connect a socket or enable sending from any binary,
HTTP route, SDK, scheduler, or profile. It does not get a secret, discover
recipients, handle inbound messages, or support attachments.

## Accepted scope

- Construct one proposal for one exact bot, one recipient ID and its private/group
  classification, and the exact nonblank UTF-8 content (up to 20,480 bytes). A trusted
  operator interface must present those values and capture approval of its
  proposal-specific 60-second challenge. A new draft, changed body, target kind,
  recipient, or bot must be reviewed anew. An auto-echo of the challenge is not
  operator approval; the protocol library does not authenticate the operator.
- The only protocol endpoint is `wss://openws.work.weixin.qq.com/` (TCP 443). An
  eventual transport must use a maintained TLS and WebSocket stack, platform-approved
  trust roots, certificate chain validation and hostname/SNI validation; reject
  redirects, custom endpoints, custom trust overrides and proxy configuration.
  There is no TLS/WS stack or trust configuration in this slice.
- `Exchange::begin` checks the local credential's bot ID against the reviewed bot.
  It serializes the secret only into a short-lived zeroizing subscribe frame. A
  correlated `aibot_subscribe` success is needed before requesting one
  `aibot_send_msg`. The text is sent via WeCom's `markdown` message type, the
  same dialect as the Python implementation. Nothing normalizes, escapes at
  the Markdown layer, splits, or appends to the reviewed content; JSON string
  escaping does not change the recipient-visible input. The operator UI must
  make the Markdown behavior clear.
- Request IDs are separate and freshly generated. Only an exact request ID and a
  compatible response command can complete a phase. Unrelated IDs and normal
  callbacks cannot acknowledge a send. A disconnected event terminates the
  exchange. Repeated calls do not produce another outbound send frame.
- Match the original's success boundary: integer `errcode: 0` plus a usable
  `body.msgid` confirms provider acceptance, not recipient delivery or read. A
  correlated numeric rejection is distinct. A timeout, disconnect, malformed,
  incomplete or missing receipt, or bounds breach once the frame leaves the
  state machine is delivery unknown. Nothing retries automatically, including a
  correlated rejection. The transport owns frame bytes, and must not clone, queue,
  replay or serialize them into logs.
- One exchange uses an absolute 8-second monotonic deadline from protocol start
  (or earlier approval expiry), at most 64 inbound messages, up to 1 MiB per
  assembled message and 4 MiB cumulative. A future socket driver must apply the
  same deadlines and enforce size limits before allocation/decompression; the
  in-memory boundary alone cannot enforce that. The 8-second exchange does not
  implement a long-connection heartbeat. No body, bot secret, or provider error
  text is logged or written to storage by this core.

## Before exposing or testing live sending

A one-shot Rust value is not a durably consumed authorization. Add a private
trusted operator integration that authenticates the operator, proves the exact
recipient scope, and durably consumes the approved intent/marks a potentially
attempted send before releasing send bytes. Process restart, partial writes, a
write failure and races must not reissue it. Do not auto-retry or use ordinary
draft approval as bot send permission. A new subscription can displace another
session for the same bot; bind to an operator-controlled identity and explicitly
handle existing session ownership instead of opening a competing session silently.

Deferred verification: fake transport for mismatched/stale IDs, false successes
and partial writes; boundary-sized UTF-8 frames and unsolicited floods; durable
restart/cancellation tests; transport tests for TLS hostname failure/redirects;
and an authorized exact-bot/recipient live round trip. No tests or live actions
were run for this source-only slice.

References: [pinned original sender](https://github.com/J-X-C-Z/local-message-hub/blob/c7052745fab3fa1880c6e3a496f40609185668b9/integrations/wecom/bot_sender.py),
[WeCom official SDK protocol](https://github.com/WecomTeam/aibot-node-sdk/blob/main/README.md#-websocket-命令协议),
[Tencent's connection guide](https://www.tencentcloud.com/zh/document/product/1316/84454).
