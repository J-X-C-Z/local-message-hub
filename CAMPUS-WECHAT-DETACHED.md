# Detached Campus public WeChat articles

The existing `preview-campus-json` and `import-campus-json` commands now
accept a strict public WeChat subset in the bounded v1 detached document.
This corresponds to the **manual public** article source in pinned Python
`c7052745` (`integrations/campus/adapter.py::public_wechat_url` and
`integrations/campus/catalog.py`), not the route that reads a user's local
WeChat official account. No account DB, HTTP request, scraping, authentication
fields, credentials or background tasks are used. Public-article attribution
and detached text are operator-curated, not independently verified.

- Use the exact official long form `https://mp.weixin.qq.com/s` with one
  value each for `__biz`, `idx`, `mid`, and `sn`. Authority is case
  insensitive; path, fields and signatures are not. `__biz` must be
  canonically padded standard Base64, `idx` and `mid` positive canonical
  decimal, and `sn` exactly 32 lowercase hex digits. As in the pinned
  adapter, the stored provenance address orders only the four identity
  fields and omits the fragment. An optional `#wechat_redirect` is ignored.
- Only `scene`, `subscene`, `isappinstalled`, `from` and `chksm` are
  recognized as non-identity tracking; they must use bounded simple values,
  and are discarded. All duplicate keys and unknown parameters fail closed,
  including token/cookie/session-like parameters. Short URLs, missing or
  zero/ambiguous index, explicit ports, userinfo, nonofficial host and
  normalized-path aliases are refused. The service never follows a short
  link to guess its identity.
- A WeChat article must be explicitly submitted as `source_id=wechat-manual`,
  `source_name=微信公众号公开文章（按链接导入）` and `category=wechat`.
  The existing document `profile` must exactly match the operator's
  `--profile` selector. The site URL is stored as inert article provenance;
  these commands never perform a browse or download.
- The native WeChat ID is the SHA-256 of the canonical article slot URL
  (`__biz`, `idx`, `mid`) while the stored URL also carries the submitted
  `sn`. Reordering query fields and changing accepted share metadata do not
  change the ID. Different article indices remain distinct even in one
  Campus profile. A different signature or other stored content for the
  same slot conflicts rather than overwriting or claiming a second identity.
  This deliberately differs from the Python full-URL hash; SYSU IDs do not
  change.
- The original preview binds archive instance, exact Campus profile, and
  exact detached bytes. Commit requires that confirmation; altered bytes
  need a fresh preview. Identical retries return the same immutable receipt.
  A separately previewed reorder has a different confirmation but maps to
  unchanged records when the canonical stored articles match. Mixed or
  duplicate input still uses the original all-or-nothing commit.
