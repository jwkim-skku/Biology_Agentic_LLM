# Structured Data Import

The backend can load structured GTEx, Allen Brain Cell Atlas, and CUSTOM-style priors from `backend/app/data/structured`.

Set `APP_DATA_DIR` or `RAG_DATA_DIR` to point the backend at a different persistent data root. Structured files are read from `<data root>/structured`.

Supported formats:

- JSON list of records
- JSON object with a `records` list
- CSV with header columns

Useful fields:

- `id`
- `dataset`: `GTEx`, `Allen Brain Cell Atlas`, `CUSTOM`, or another source label
- `release`
- `species`
- `brain_region`
- `cell_type`
- `coverage_level`
- `confidence`
- `summary`
- `source_url`
- `codon_weight_multipliers` for CUSTOM records
- `codon_availability_weights` for tRNA or codon-availability records

Import a local file:

For offline/operator workflows, use the structured import CLI from the backend directory. It runs the same preview validation used by the API, refuses projected validation errors by default, and writes a manifested audit ZIP for every applied import:

```powershell
cd backend
python scripts/structured_import.py status
python scripts/structured_import.py preview E:\path\to\custom_records.json
python scripts/structured_import.py apply E:\path\to\custom_records.json --archive --output-json app\data\runtime\structured_import_latest.json
```

The `apply` command writes `app/data/runtime/structured_import_audits/<source>_import_audit.zip` unless `--audit-zip` is supplied. Use `--archive` to store the audit ZIP in the immutable artifact archive and ledger, and `--allow-validation-errors` only for deliberate forensic imports.

Preview the import impact before copying files into the structured data directory:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/api/v1/structured/import/preview `
  -ContentType 'application/json' `
  -Body (@{
    source_path = 'E:\path\to\custom_records.json'
    rebuild_index = $false
  } | ConvertTo-Json)
```

The preview response reports source SHA-256, whether the import would replace an existing structured file, imported/current/projected record counts, duplicate IDs, projected manifest hash, import-only validation, merged validation, and provenance completeness without mutating the data directory.

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/api/v1/structured/import `
  -ContentType 'application/json' `
  -Body (@{
    source_path = 'E:\path\to\custom_records.json'
    rebuild_index = $true
  } | ConvertTo-Json)
```

Each successful structured import archives a `structured_import_audit_bundle` with the redacted request payload, import result, structured manifest, validation report, and imported file bytes. The API response includes `audit_bundle.artifact_id` so the bundle can be re-downloaded or verified through the artifact archive endpoints. Recent import audit bundles are summarized by `GET /api/v1/artifacts/structured-imports/semantic-summary`, which checks manifest-hash and validation-count consistency and can use `verify_files=false` for fast indexed archive metadata.

Inspect loaded records:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/structured/status
```

Inspect source file hashes and validation:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/structured/manifest
Invoke-RestMethod http://127.0.0.1:8000/api/v1/structured/validate
Invoke-RestMethod http://127.0.0.1:8000/api/v1/data/external-sources
```

The manifest includes SHA-256 hashes, byte sizes, datasets, releases, record counts, and provenance completeness for every structured source file. QC reports include the current structured manifest hash so a design run can be tied back to the exact local data snapshot.

Backfill source-response snapshots for bundled or manually imported structured files that predate snapshot tracking:

```powershell
Invoke-RestMethod -Method Post "http://127.0.0.1:8000/api/v1/data/external-sources/backfill?dry_run=true"
Invoke-RestMethod -Method Post "http://127.0.0.1:8000/api/v1/data/external-sources/backfill?dry_run=false"
```

The backfill writes canonical snapshot JSON files under `APP_DATA_DIR/runtime/external_sources` and links affected records through `source_payload_sha256`, `source_snapshot_path`, and `source_request_url` when available. After applying a backfill, rebuild the RAG index and rewrite the data/release lockfiles so reproducibility checks pin the updated manifest.

Search target context:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/api/v1/structured/search `
  -ContentType 'application/json' `
  -InFile .\backend\examples\design_from_gene_request.json
```

The bundled seed data is intentionally small. For production, replace it with release-pinned GTEx, Allen, and CUSTOM artifacts and keep the `release` field populated.

## tRNA / Codon Availability Matrix

Kapur-style brain cell-type tRNA priors can be imported through the same structured import endpoint. A record can provide `codon_availability_weights`, keyed by DNA codon:

```json
[
  {
    "id": "kapur_example_dopaminergic",
    "dataset": "Kapur brain tRNA",
    "release": "your_release_id",
    "species": ["mouse", "human_transfer"],
    "brain_region": "substantia nigra",
    "cell_type": "dopaminergic neuron",
    "coverage_level": "cell_type_trna_prior",
    "confidence": "medium",
    "summary": "Release-pinned tRNA/codon availability prior.",
    "codon_availability_weights": {
      "GCC": 1.05,
      "GAC": 1.03,
      "CGA": 0.94
    },
    "source_url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC11065635/"
  }
]
```

These weights affect:

- `tissue_codon_adaptation`
- `rare_codon_clusters`
- NSGA-II objective ranking
- QC report score tables

The bundled `kapur_brain_trna_seed.json` is a conservative placeholder and should be replaced before quantitative interpretation. Records with low-confidence, seed, or placeholder codon-availability priors are now surfaced under the data provenance audit as `trna_prior_caveats`; deployment readiness reports this count in the `data_provenance` gate so production promotion cannot silently treat them as release-pinned quantitative matrices.

Validation checks include:

- required `id`, `dataset`, and `summary`
- duplicate IDs
- GTEx gene-expression fields
- valid DNA codons for codon matrices
- positive numeric weights
- warnings for unusually large or small codon weights

## Live GTEx Import

The backend can fetch GTEx median gene expression through the GTEx Portal API v2. The implementation uses:

- `reference/geneSearch` to resolve a gene symbol to a versioned GENCODE ID.
- `expression/medianGeneExpression` to fetch tissue-level median expression.

Example:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/api/v1/external/gtex/import-gene-expression `
  -ContentType 'application/json' `
  -Body (@{
    gene = 'SNCA'
    brain_regions = @('substantia nigra', 'striatum', 'cortex', 'hippocampus')
    dataset_id = 'gtex_v8'
    rebuild_index = $true
  } | ConvertTo-Json)
```

The current repository has imported SNCA median expression for selected brain regions into `backend/app/data/structured`. New live imports also write request/response source snapshots under `APP_DATA_DIR/runtime/external_sources` and link records back through `source_payload_sha256` and `source_snapshot_path`.

## Live Allen WHB Taxonomy Import

The backend can fetch the Allen Brain Cell Atlas whole-human-brain taxonomy metadata from the public S3-hosted `WHB-taxonomy/20240330` release.

Example:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/api/v1/external/allen/import-whb-taxonomy `
  -ContentType 'application/json' `
  -Body (@{
    query_terms = @('dopaminergic', 'medium spiny', 'astrocyte', 'microglia')
    max_records = 120
    rebuild_index = $true
  } | ConvertTo-Json)
```

This keeps the local dataset small while still using real Allen taxonomy records. New live imports also write source snapshots under `APP_DATA_DIR/runtime/external_sources` so the selected taxonomy records can be audited against the downloaded release artifact.
## Reproducible Reference Refresh

The platform supports two complementary data paths:

- ad hoc imports through `POST /api/v1/structured/import`, `POST /api/v1/external/gtex/import-gene-expression`, and `POST /api/v1/external/allen/import-whb-taxonomy`
- reproducible reference refreshes through `GET /api/v1/data/catalog`, `POST /api/v1/data/refresh/validate`, `POST /api/v1/data/refresh`, and `GET /api/v1/data/refresh-log`

Use `POST /data/refresh/validate` before live refreshes to check normalized genes, regions, source selection, operation count, release-lock state, and external source snapshot coverage. Use `dry_run=true` on `/data/refresh` to inspect the GTEx/Allen operations before making network calls or rebuilding the RAG index. Completed refreshes append an audit entry under `APP_DATA_DIR/runtime/data_refresh_log.jsonl`.

For change-control evidence, export the dry-run plan before applying a refresh:

```powershell
Invoke-WebRequest `
  -Method Post `
  -Uri http://127.0.0.1:8000/api/v1/data/refresh/plan/export.zip `
  -ContentType 'application/json' `
  -Body '{"genes":["SNCA"],"brain_regions":["substantia nigra"],"include_gtex":true,"include_allen":false,"dry_run":true}' `
  -OutFile data_refresh_plan_bundle.zip

Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/api/v1/data/refresh/plan/export/verify `
  -ContentType 'application/json' `
  -Body '{"genes":["SNCA"],"brain_regions":["substantia nigra"],"include_gtex":true,"include_allen":false,"dry_run":true}'
```

The bundle is archived like other export artifacts and includes `refresh_plan.json`, `validation.json`, `operations.csv`, structured data manifests, structured quality, provenance, release-lock, external-source, refresh-log, and RAG status evidence. Verification checks required files, operation-count consistency, validation schema/status, release-lock status, and structured-manifest hash consistency.

For portfolio and promotion handoff, `GET /api/v1/data/release/export.zip` creates a release evidence bundle from the current structured dataset state. It includes structured manifests, validation, promotion quality, provenance, release-lock verification, external-source catalog/status, RAG status, refresh history, normalized `records.jsonl`/`records.csv`, and source bytes. `GET /api/v1/data/release/export/verify` rebuilds and checks the bundle for required files, row-count consistency, manifest-hash consistency, source inclusion, and pass/warning/fail promotion status.

The same refresh path is available offline from the backend CLI. `plan` performs no network calls; `apply` performs live imports, writes a refresh evidence JSON, and can update the release lock after a fully successful refresh:

```powershell
cd backend
python scripts/data_refresh.py catalog
python scripts/data_refresh.py validate --genes SNCA GBA1 --regions "substantia nigra" striatum --allen-terms dopaminergic microglia --max-allen-records 50
python scripts/data_refresh.py plan --genes SNCA GBA1 --regions "substantia nigra" striatum --allen-terms dopaminergic microglia --max-allen-records 50
python scripts/data_refresh.py apply --genes SNCA GBA1 --regions "substantia nigra" striatum --allen-terms dopaminergic microglia --max-allen-records 50 --write-release-lock
python scripts/data_refresh.py log --limit 5
```

Example:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/api/v1/data/refresh/validate `
  -ContentType 'application/json' `
  -Body (@{
    genes = @('SNCA', 'HTT', 'GBA1', 'MECP2')
    brain_regions = @('substantia nigra', 'striatum', 'cortex', 'hippocampus')
    include_gtex = $true
    include_allen = $true
    allen_query_terms = @('dopaminergic', 'medium spiny', 'astrocyte', 'microglia')
    max_allen_records = 250
    dataset_id = 'gtex_v8'
  } | ConvertTo-Json)

Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/api/v1/data/refresh `
  -ContentType 'application/json' `
  -Body (@{
    genes = @('SNCA', 'HTT', 'GBA1', 'MECP2')
    brain_regions = @('substantia nigra', 'striatum', 'cortex', 'hippocampus')
    include_gtex = $true
    include_allen = $true
    allen_query_terms = @('dopaminergic', 'medium spiny', 'astrocyte', 'microglia')
    max_allen_records = 250
    rebuild_index = $true
    dry_run = $false
  } | ConvertTo-Json)
```

Each structured file is included in the manifest with SHA-256, byte size, record count, dataset names, releases, provenance completeness, and validation results. The manifest hash is propagated into design provenance and QC reports.

Inspect the current target-level coverage matrix:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/data/coverage
```

The coverage matrix separates live records from seed/local priors, summarizes dataset-level snapshot and payload-hash completeness, and exposes gene-region and cell-type rows so operators can see which design targets are backed by refreshed GTEx/Allen records versus bundled CUSTOM/Kapur-style seed priors.

Export a complete local data snapshot:

```powershell
Invoke-WebRequest `
  -Uri http://127.0.0.1:8000/api/v1/data/snapshot.zip `
  -OutFile .\agentic_rag_data_snapshot.zip
```

The snapshot includes structured data files, external source snapshots, document extract JSON, `evidence_seed.json`, `rag_index.json`, refresh log, and machine-readable manifests with SHA-256 hashes. Every exported snapshot also includes `artifact_manifest.json`, which records each ZIP member's byte size, media type, SHA-256 digest, and a manifest hash for reproducibility checks.

Verify a freshly rebuilt snapshot bundle:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/data/snapshot/verify
```
