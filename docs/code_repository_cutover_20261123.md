# Code-Only Repository Cutover

`AviramDahan/market-lens-core` contains source code only. The complete original
repository `AviramDahan/market-lens-scanner` remains untouched and readable as
the historical source/data reference. Its refs must not be rewritten or deleted.

The imported source baseline is `1ea4206f2240ad28c4175e2a2a0050e806f8cb78`.
`source_import_20261123.json` records byte identities for the original code.
No production workbook, result history, SQLite database or credentials belong
in this repository. Private recovery assets remain in
`AviramDahan/market-lens-data-archive`.

The production URL, data paths, Docker images, single-writer services and
trading policies do not change. Source checkout cutover must pause only the
Market Lens timers, allow existing jobs to finish, preserve the original Git
directory for rollback, and prove code identity before atomically replacing
Git metadata. Never restore an older portfolio during a source rollback.

The unchanged host runner checks the deployed image commit by Git tree diff.
The new local checkout therefore retains a local-only, shallow legacy image
pin (`3268d92c65310b7ba40249c208ae39953920d650`) from the original repository.
That pin is not a parent of code-only main and must never be pushed/mirrored
to core. Fetch it with `--filter=blob:none`; do not fetch legacy runtime history.

Archive `source_repository` is the stable legacy project identifier required
by existing manifests. It must stay unchanged so old receipts remain valid.
New source commits resolve in `market-lens-core`; this document and the import
manifest provide the repository mapping. The data archive repository is not
changed or recreated by the source migration.

Only Source QA may execute in GitHub Actions. All legacy scanner, monitor,
Telegram and upgrade workflows must remain disabled in both repositories.
Source QA needs no production secrets. The host is the sole portfolio writer.

SQLite persistence/recovery is explicitly out of scope. No web container
recreation or database operation is required for Git metadata cutover.
