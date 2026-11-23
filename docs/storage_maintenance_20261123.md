# Bounded Host Storage Maintenance

SQLite persistence and recovery are explicitly excluded. No active result,
workbook, open-position data, database, shared Docker volume, or other project
directory is eligible for deletion.

## Closed Scanner Stages

`MARKET_LENS_CLOSED_STAGE_ARCHIVE_ENABLED=true` opts the existing maintenance
timer into closed-stage archival. It runs only outside 09:00-16:30 New York on
weekdays and when the existing runtime/build busy checks allow maintenance.
The newest two stages and stages newer than 24 hours stay local. Unresolved
checkpoint/local-write fences block retirement. Dedicated worker mounts are
checked again after remote verification. Symlinks, database files, unrecognized
paths, oversized stages and sensitive content are retained rather than guessed.

A stage is inventoried with per-file SHA-256, frozen into a portfolio-only
archive, validated, uploaded to the existing PRIVATE archive and independently
download-verified before its local duplicate may be retired. Existing cold
backup receipt handling is reused, including metadata verification. Retries
must bind the receipt to the exact stage ID; changed evidence remains local.

Limits per pass: 512 MiB uncompressed source and a 180-second between-stage
budget. Individual storage operations retain their existing bounded timeouts.
Preparation preserves the 12 GiB execution reserve plus archive scratch space.
The existing service also has a total systemd timeout and reduced CPU/IO priority.

Durable inventory and receipt: runtime state
`maintenance/closed-stage-archives/<stage-id>.json`, with the original root,
per-file hashes, private repository, release tag, asset IDs and retirement state.
The archive pointer remains in `market-lens-backups/closed-stages/`.

Recovery uses `cold_backups.restore_receipt` to a NEW isolated archive path.
The restored tar contains `agent_results/closed_stage_evidence/<id>/`, including
`stage_manifest.json` and the original stage-relative file hierarchy. Validate
all hashes before analysis. Never extract or copy it over a live portfolio.

An upload-only operator verification (`retire=False`) can validate an old
stage without removing it. It is not a completed capacity reclamation. Normal
retirement still observes the market window and all source/mount checks.

## Image Retention

`MARKET_LENS_IMAGE_RETENTION_ENABLED=true` enables maintenance of only
`market-lens-*` images, outside the same market window. Retain all images used
by any container (including stopped containers), every `latest` tag and the
latest three runtime web/worker versions (current plus two rollbacks).
Only images older than 14 days and tagged exclusively for Market Lens qualify.
Recheck references and tags before each removal; never use force, system prune,
volume removal or stopped-container deletion. Maximum four images per pass.

The candidate inventory/layer identities and actual removals are recorded in
`maintenance/image-retention-last.json`. A failed service is considered idle
only after systemd confirms MainPID=0; unknown or live service states still block.

## Capacity and Limits

The 20 GiB target and 12 GiB execution reserve are unchanged. Maintenance must
report real free space and retained/failed work; a configured task is not proof
that bytes were reclaimed. Current active history is never time-purged. This
maintenance bounds old failed-stage/build accumulation but does not promise
unlimited disk for indefinitely growing live history.

The original GitHub repository remains unchanged. Code moves to the clean core
repository; immutable private recovery assets are not committed as Git blobs.
This avoids rewriting/removing old history or introducing paid Git LFS.
