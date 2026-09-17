# Reliability and recovery operations

The Paralegal Research Desk keeps the approved bidder master, research evidence, comparison proposals, approved-update history, scan lifecycle, durable scan diagnostics, and activity records in `data/crawler.db`. API credentials are stored separately under `.runtime` or environment variables and are intentionally excluded from workspace backups.

## Scan lifecycle and recovery

Persisted scan states are `queued`, `running`, `partial`, `completed`, `failed`, `cancelled`, and `interrupted`. Normal state changes are validated by the backend. A hard process exit cannot leave a scan indefinitely marked `running`: startup marks unfinished `queued`/`running` attempts `interrupted` and retains their diagnostics.

A normal application shutdown also records active work as `interrupted`, not as an operator cancellation. On the next start, an interrupted attempt that has not exceeded the bounded recovery limit is queued as a new child attempt. The interrupted attempt remains unchanged as audit history. Contractor selection and scan mode are carried forward.

Targeted research adapters currently aggregate some completeness state in memory. For those adapters the program deliberately starts a clean recovery attempt instead of claiming an exact checkpoint resume. This preserves the ability to determine completeness. An explicit cancellation remains `cancelled` and is not automatically restarted.

Transient HTTP/network failures use bounded retries. Authentication/access rejection and malformed source structures are not retried indefinitely. Retry attempts and exhaustion are stored in per-scan diagnostics, and exhausted/other acquisition failures prevent a false-complete result.

## Per-scan diagnostics

`GET /api/jobs/{job_id}/diagnostics` returns the persisted job row, durable diagnostics, and the persisted frontier summary. Diagnostics retain lifecycle events, retry counts, acquisition failures, completion/partial status, limits, interruption/restart reasons, and source/job references without intentionally storing API keys.

`GET /api/maintenance/integrity` runs SQLite `quick_check` plus foreign-key verification. `?full=true` runs the full `integrity_check`. Startup also runs `quick_check` after schema initialization/migration.

## Backup

Create a backup from the repository root with:

```powershell
python scripts/backup-workspace.py
```

An explicit destination can be supplied as the first argument. The backup uses SQLite's online backup API rather than copying a live WAL-mode database file. The ZIP contains a consistent `crawler.db`, a manifest with schema version/table counts/SHA-256, and restore instructions. It includes approved master data, evidence, history, proposals, jobs/diagnostics, and activity records stored in SQLite. It does not include DOL/SAM API credentials.

The maintenance API also exposes `GET /api/maintenance/backup` for the same verified database snapshot format.

## Restore

Stop the application before replacing its active workspace. To restore into a new/clean location:

```powershell
python scripts/restore-workspace.py path\to\backup.zip --target path\to\fresh\crawler.db
```

The restore verifies the archive checksum, SQLite integrity, foreign keys, required persistent tables, and schema before placing the database at the destination. Existing targets are refused by default. `--overwrite` is intentionally explicit and should only be used while the application is stopped.

After restore, start the application normally. Schema initialization is idempotent and applies any later migrations before the workspace is used.

## Scale benchmark

A generated-data benchmark is available without changing the production database:

```powershell
python benchmarks/reliability_scale.py
```

Defaults generate 25,000 bidder rows, 100,000 evidence rows, 200,000 history rows, and 50,000 activity rows in a temporary database, then exercise a paginated evidence query. Sizes can be changed with command-line flags.
