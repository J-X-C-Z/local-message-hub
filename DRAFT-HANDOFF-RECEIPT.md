# Signed consumer acknowledgement of a local draft export

An unsigned export from [DRAFT-HANDOFF.md](DRAFT-HANDOFF.md) is not eligible
for an authenticated external acknowledgement. The owner may optionally
enable a signed-receipt binding when starting a **new, unconsumed** local
export. This does not grant delivery permission or add a delivery adapter.

The trusted Linux/macOS operator submits `receipt_public_key` in both the
existing handoff preview and the separate handoff pull frames: 130 lowercase
hex characters encoding the 65-byte uncompressed SEC1 P-256 public key
(`04 || X || Y`) of the designated recipient application or operator. The owner must establish the public key's
identity and independent custody out of band before selecting it. Do not use
an owner- or Hub-controlled private key to assert independent acceptance. Hub
verifies a preselected key, not the holder's identity beyond that binding.
No private/signing key is accepted, generated, or stored. Only a private
operator process may supply the frame; no Desktop grant or HTTP endpoint can.

The chosen key is bound to the short-lived approval before export; the pull
must supply the same key and still explicitly approve the exact local export.
Changing or omitting the key requires a new unconsumed preview; a consumed
unsigned export cannot be retrofitted. A bound packet adds
`receipt_binding:{protocol,handoff_id,recipient_key_sha256}`, where the
fingerprint is SHA-256 of the exact uncompressed public-key bytes. `handoff_id` is an opaque
lowercase hex digest tied to this exact archive instance, account,
conversation, revision, review, body, expiry and random export nonce. It
must not be reused for another packet.

The independent selected consumer may sign a narrowly scoped acknowledgement.
ECDSA P-256 with SHA-256 signs exactly the ASCII below, with one LF after **every** line,
including the last. Use the packet's exact canonical lowercase hex ID and
body hash; disposition is exactly `accepted`, `declined` or `unknown`.

```text
hub-local-draft-consumer-receipt-v1
HANDOFF_ID
BODY_SHA256
DISPOSITION
```

The signature is the full 64 bytes rendered in 128 lowercase hex characters:
fixed-width 32-byte unsigned big-endian `r` followed by `s` (not ASN.1 DER).
Retry the same serialized signature rather than generating a fresh one.
It is a statement about handling the **export packet**, never a claim about
sending or delivering to a provider recipient. Hub does not run this consumer,
obtain the signature, choose an external destination, or make a network or
provider request. The packet's existing `expires_at` remains the consumer's
instruction to reject stale packets; the Hub receipt timestamp proves only
when the local receipt was recorded, not when a consumer handled the export.

The authorized owner manually runs
`draft-handoff-reconcile --directory ARCHIVE` with the same private JSON
pipe requirements and operator passphrase. Supply an ordinary archive and a
single frame with `passphrase`, the original `target` object (scope, review
ID, conversation and revision), and
`"receipt":{"handoff_id":"…","body_sha256":"…","disposition":"accepted","signature":"…"}`.
Hub verifies the exact consumed local export and the signature under the key
bound **before** consumption. It refuses unsigned, unsolicited, changed-scope
or expired new reports, and cannot be tricked into self-acceptance by a bare
operator flag. New reports must be recorded within 24 hours of local export.
Identical signed retries return the original timestamp; a different result
(including after a first `unknown`) cannot replace the record. Reported data
are restricted to the fixed disposition, signature digest and local receipt
time, keyed to the existing bounded handoff ledger. No bodies, endpoints,
display names, free-text reports, or original signatures are added to storage.

The output explicitly distinguishes `signature_verified:true` from
`delivery_verified:false`. None of these commands proves an external
provider sent or delivered anything or updates a user-visible sent status.
This source has not been built or exercised against a consumer.
