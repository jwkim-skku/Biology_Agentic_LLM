# Run Persistence

The backend stores design runs in a local SQLite database at:

```text
backend/app/data/runtime/runs.sqlite3
```

Operational audit events are stored separately at:

```text
backend/app/data/runtime/audit.sqlite3
```

Exported run/job/data snapshot bundles are archived under:

```text
backend/app/data/runtime/artifact_archive.sqlite3
backend/app/data/runtime/artifact_archive/
backend/app/data/runtime/artifact_archive_ledger.jsonl
```

Structured source releases can be pinned separately at:

```text
backend/app/data/runtime/data_release_lock.json
```

Set `APP_DATA_DIR` or `RAG_DATA_DIR` to move this database into another persistent directory.

This runtime database is intentionally ignored by Git. In Docker, `backend/app/data` is mounted as a volume so run history persists across container restarts.

## API

List recent runs:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/runs
```

Fetch one run:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/runs/run_abc123
```

Fetch an exported artifact if one has been generated:

```powershell
Invoke-WebRequest http://127.0.0.1:8000/api/v1/runs/run_abc123/artifacts/qc_report_html
```

Inspect recent operational audit events:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/audit/events
```

Inspect archived export bundles:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/artifacts
```

Verify the append-only artifact archive ledger:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/artifacts/ledger/verify
```

Inspect the structured data release lock:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/data/release-lock
```

Artifact types:

- `qc_report_markdown`
- `qc_report_html`

## Stored Fields

Each run stores:

- request payload
- full design JSON
- QC report JSON
- agent/tool trace
- recommended candidate ID
- composite score
- target gene/region/cell type/modality
- structured manifest hash
- artifact manifest hash verification through run/job export verify endpoints

This is still a local single-user persistence layer. For production multi-user deployments, migrate the same schema shape to Postgres with role-based access control and immutable artifact storage.
