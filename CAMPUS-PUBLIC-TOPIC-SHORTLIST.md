# Public Campus topic shortlist (native, local only)

The ordinary native-live `GET /api/v1/campus/public-topic-shortlist` requires
an exact Campus profile grant with `campus.read`. It lists individual visible
imported articles, not the legacy personalized `section=selected` or native
category-weight score results. No private student profile, score, event
grouping, model, provider or writes are involved. Here “profile” in the optional
query means only the exact archive access scope.

Pinned Python `hub/campus_briefing.py` at `c7052745` uses these exact literal
terms in `TOPICS` and the three corresponding public topics in its provider-off
`_rank` selected predicate:

- `scholarships`: 奖学金, 奖助, 助学金, 困难补助, 国家励志.
- `competitions`: 竞赛, 大赛, 挑战杯, 创新创业, 比赛, 科创.
- `courses`: 选课, 课程, 补退选, 教务, 考试, 学分, 教学.

As in that predicate, matches inspect normalized title, summary and imported
body, but a match still needs nonblank material beyond a title. This subset
deliberately also rejects cases where every material field is blank or a
trimmed literal title echo (ASCII-insensitive), rather than treating a copied
headline as material. A body or summary may independently qualify; the result
does not assert authenticity, material quality or student relevance. Known
future publication days are omitted using current fixed UTC+08; unknown dates
stay allowed, flagged and last. Existing title-only daily and all cards, and
the distinct v2 category policy, are unchanged.

Use an optional exact `topic` from the three names, `limit` (1–50, default 50),
`offset` (0–4096), and optional exact `profile`. Selection and source visibility
are applied before earliest page offsets; sorting is stored publication day
then stable article ID (both descending). The read takes one bounded archive
snapshot and returns `total` and existing-style cards with public topic labels,
bounded excerpts, article bookmark/read state and report. It never returns
full match material through this shortlist. No legacy alias is added.

The original provider-off route also uses a default or explicitly configured
student profile and source fields, groups across materials, and computes
rule scores. Those outputs and eligibility filters are **not reproduced**.
This is only the documented public topic part, with stricter headline-echo
exclusion. Native source checkpoint only; no runtime qualification.
