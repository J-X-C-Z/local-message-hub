# Export Format Provenance

The Excel, SQL, and ChatLab renderers in `hub/exports.py` are original
standard-library implementations for the Hub's normalized message model. They
use the ChatLab envelope and type conventions exposed by WeFlow 5.0.0 at commit
`2f23e72cebf7f309a0f4d129307d16d7b0cd41c3`, and follow its documented export
semantics where those map cleanly to this read-only, cross-platform model. No
WeFlow formatter source is copied into these renderers. WeFlow is licensed
CC BY-NC-SA 4.0; its license and upstream provenance are retained separately
in `UPSTREAM.md`.

The Hub adds platform, account, and conversation identity to ChatLab records
so IDs from different services remain distinguishable. SQL output is a
portable PostgreSQL-style transaction script for a Hub-owned table; it never
executes against or changes a source chat database. XLSX is generated as a
minimal OOXML workbook using inline text cells, preventing message text from
being interpreted as formulas. All tests use synthetic messages.
