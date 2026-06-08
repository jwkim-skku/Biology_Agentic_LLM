# Agentic RAG Codon Optimization MVP

This backend is the first implementation slice of the PDF proposal:

1. user-supplied CDS input
2. Ensembl gene-symbol lookup and MANE-aware canonical CDS fetch
3. protein-preserving synonymous codon search
4. sequence scoring for codon adaptiveness, GC/CpG, motifs, polyA/splice/restriction policy, homopolymers, codon-pair risk, 5-prime GC, structure proxies, and k-mer complexity
5. Pareto-style candidate ranking with feasible-set, score-range, and codon-diversity diagnostics
6. optimizer benchmark suite for constraint violation rate, codon diversity, protein preservation, composite-quality delta, and approximate hypervolume
7. local RAG index with chunking, configurable embedding backend boundary, BM25-style lexical scoring, biomedical alias expansion, metadata filtering, facet-aware reranking, and source-priority scoring
8. document ingestion for PDF, Markdown, text, and JSON records
9. structured GTEx/Allen/CUSTOM/Kapur-style tRNA prior loading and import
10. reproducible GTEx/Allen data catalog, refresh plan, and refresh audit log
11. evidence synthesis into supported/uncertain/rejected design rules
12. release lockfile for structured dataset release/file-hash pinning
13. JSON, Markdown, HTML, and PDF QC/report output
14. SQLite operational audit log for report/export/verify/job/data-refresh actions
15. governance attestation export for OpenAPI, data, RAG, storage, and artifact-ledger state
16. optional Postgres runtime adapter for run/job/audit stores with storage readiness endpoints
17. SQLite-to-Postgres migration bundle export, dry-run import planning, and row-level source/target parity hashing
18. immutable local archive for exported run/job/data snapshot bundles
19. append-only artifact archive ledger with hash-chain verification
20. optional HMAC and Ed25519 signatures for artifact manifests and governance attestation payloads
21. persistent agent memory index for session, semantic-rule, and artifact summaries derived from saved runs
22. API contract, response-shape golden snapshots, value-level golden fixtures, and smoke-test scripts for deployment verification

The current code is still an in silico design-support platform, not a therapeutic efficacy predictor. It keeps provenance and persistent agent memory in payloads/stores so release-pinned data can be attached later.

## Run

```powershell
cd backend
python -m venv .venv
.\\.venv\\Scripts\\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Open:

- `http://127.0.0.1:8000/docs`
- `http://127.0.0.1:8000/api/v1/health`
- `http://127.0.0.1:8000/api/v1/health/ready`

Optional production controls:

```powershell
$env:API_KEYS = 'replace-with-a-long-random-key'
$env:API_KEY_ROLES = 'replace-with-a-long-random-key=admin'
$env:RATE_LIMIT_PER_MINUTE = '120'
$env:ARTIFACT_SIGNING_KEY = 'replace-with-a-long-random-signing-secret'
$env:ARTIFACT_SIGNING_KEY_ID = 'local-hmac-sha256'
$env:ARTIFACT_ED25519_PRIVATE_KEY = '<PEM-or-base64-raw-private-key>'
$env:ARTIFACT_ED25519_PUBLIC_KEY = '<PEM-or-base64-raw-public-key>'
$env:ARTIFACT_ED25519_KEY_ID = 'local-ed25519'
$env:ARTIFACT_RETENTION_DAYS = '0'
$env:ARTIFACT_RETENTION_KEEP_MIN = '100'
$env:ARTIFACT_OBJECT_STORE_ENABLED = 'false'
$env:ARTIFACT_OBJECT_STORE_ENDPOINT = 'https://s3.example.com'
$env:ARTIFACT_OBJECT_STORE_BUCKET = 'agentic-rag-artifacts'
$env:ARTIFACT_OBJECT_STORE_PREFIX = 'agentic-rag/artifacts'
$env:ARTIFACT_OBJECT_STORE_REGION = 'us-east-1'
$env:ARTIFACT_OBJECT_STORE_ACCESS_KEY_ID = '<access-key>'
$env:ARTIFACT_OBJECT_STORE_SECRET_ACCESS_KEY = '<secret-key>'
```

When `API_KEYS` is set, protected routes require `X-API-Key: <key>` or `Authorization: Bearer <key>`. `API_KEY_ROLES` can map keys to `viewer`, `operator`, or `admin` roles using `key=admin;other=viewer,operator` or a JSON object. If roles are omitted, configured keys default to `admin`. Health, readiness, settings, security status, and OpenAPI documentation remain public for deployment checks.
When `ARTIFACT_SIGNING_KEY` is set, run/job/data snapshot artifact manifests and governance attestations are signed with HMAC-SHA256 and verify endpoints validate the signature. When `ARTIFACT_ED25519_PRIVATE_KEY` is set, exports also include Ed25519 signatures for asymmetric external verification.
When `ARTIFACT_OBJECT_STORE_ENABLED=true`, archived ZIP bundles can be mirrored to an S3-compatible object store through the object-store mirror endpoints after a dry-run plan.

## Example

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/api/v1/optimize `
  -ContentType 'application/json' `
  -InFile .\\examples\\optimize_request.json
```

## API

- `POST /api/v1/score`: validate and score one CDS.
- `POST /api/v1/validate-cds`: run ORF, payload, motif, local-GC, polyA, restriction-site, splice-proxy, and detailed sequence-policy validation without optimizing.
- `GET /api/v1/health/live`: liveness probe.
- `GET /api/v1/health/ready`: readiness probe for data directory, structured validation, and RAG index state.
- `GET /api/v1/settings`: inspect runtime data directory and CORS origins.
- `GET /api/v1/security/status`: inspect API key and rate-limit controls without exposing key values.
- `GET /api/v1/metrics`: inspect request, store, RAG, and structured-data metrics as JSON.
- `GET /api/v1/metrics/prometheus`: expose lightweight Prometheus-style counters and gauges.
- `GET /api/v1/optimizer/benchmark`: run fixed optimizer benchmark cases for quality, constraints, diversity, and reproducibility.
- `GET /api/v1/optimizer/benchmark/export.zip`: export a manifested optimizer benchmark bundle with benchmark metrics, stable result hashes, case provenance fingerprints, recommended-candidate folding evidence hashes, recommendation trade-off/regret columns, diagnostics, stress gate, case catalog, candidate diagnostics, config snapshots, structured manifest, archive summary, and deployment-audit evidence.
- `GET /api/v1/optimizer/benchmark/export/verify`: rebuild and semantically verify the optimizer benchmark bundle.
- `GET /api/v1/optimizer/diagnostics`: inspect optimizer objectives, benchmark case/result hashes, macro quality bands, weak cases, and tuning recommendations.
- `GET /api/v1/optimizer/stress`: inspect optimizer stress gates for AAV budget, motif/polyA/restriction/splice policy, structure proxy, low complexity, rare-codon clusters, diversity, and recommendation regret.
- `GET /api/v1/optimizer/rna-folding/status`: inspect the configured RNA folding backend. Local development defaults to deterministic proxy evidence; production should set `RNA_FOLDING_BACKEND=rnafold` with ViennaRNA `RNAfold` available.
- `POST /api/v1/optimizer/rna-folding/evaluate`: evaluate one CDS window through the active RNA folding adapter, returning RNAfold MFE/structure when configured and deterministic proxy evidence otherwise.
- `GET /api/v1/optimizer/benchmark/cases`: inspect the optimizer benchmark fixture.
- `GET /api/v1/audit/events`: list operational audit events with optional event/resource filters.
- `GET /api/v1/audit/summary`: summarize audit counts by event type and outcome.
- `GET /api/v1/artifacts`: list immutable archived export bundles.
- `GET /api/v1/artifacts/summary`: summarize archived bundle counts, bytes, types, and verification status.
- `GET /api/v1/artifacts/qc-bundles/semantic-summary`: summarize recent archived QC report bundle semantic verification status. Use `verify_files=false` for fast indexed metadata, or the default deep mode to re-read ZIP contents.
- `GET /api/v1/artifacts/structured-imports/semantic-summary`: summarize recent structured import audit bundle semantic verification, including manifest-hash and validation-count consistency. Use `verify_files=false` for fast indexed metadata.
- `GET /api/v1/artifacts/retention/plan`: preview archived export bundles eligible for retention cleanup.
- `POST /api/v1/artifacts/retention/apply?dry_run=true`: dry-run or apply retention cleanup while appending ledger tombstones.
- `GET /api/v1/artifacts/object-store/status`: inspect S3-compatible archive mirror readiness.
- `GET /api/v1/artifacts/object-store/mirror/plan`: preview archived bundles that still need object-store mirroring.
- `POST /api/v1/artifacts/object-store/mirror?dry_run=true`: dry-run or apply object-store mirroring for immutable archived bundles.
- `GET /api/v1/artifacts/ledger`: inspect recent artifact archive ledger entries.
- `GET /api/v1/artifacts/ledger/verify`: verify the artifact archive ledger hash chain and compare entries against the archive index.
- `GET /api/v1/artifacts/{artifact_id}/download`: download one archived export bundle.
- `GET /api/v1/artifacts/{artifact_id}/verify`: re-hash and re-verify one archived export bundle against its archive index and embedded manifest. Archived QC report bundles also run report/optimizer reproducibility semantic checks.
- `POST /api/v1/data/refresh/plan/export.zip`: export a manifested dry-run data refresh plan bundle with request, validation, operations CSV, structured data quality, provenance, release-lock, external-source, and RAG status evidence.
- `POST /api/v1/data/refresh/plan/export/verify`: rebuild and semantically verify the dry-run data refresh plan bundle.
- `POST /api/v1/resolve-gene`: resolve a gene symbol through Ensembl REST.
- `POST /api/v1/fetch-cds`: resolve a gene symbol and fetch the selected CDS with MANE Select priority.
- `POST /api/v1/evidence/search`: retrieve MVP evidence records from the local evidence store.
- `GET /api/v1/rag/status`: inspect the local RAG index.
- `GET /api/v1/rag/diagnostics`: inspect corpus distributions, token/embedding health, vector-store readiness/runtime status for pgvector/Qdrant migration, regression macro metrics, and operational recommendations.
- `GET /api/v1/rag/embedding/status`: inspect the configured RAG embedding backend. Local development defaults to deterministic `hash-bow-v1`; production should pin a locally available `sentence-transformers` biomedical model and rerun regression before promotion.
- `GET /api/v1/rag/vector-store/status`: inspect the configured RAG vector backend, active adapter, pgvector/Qdrant configuration, and fallback state.
- `GET /api/v1/rag/vector-store/import/plan?target_backend=pgvector`: plan a local RAG vector-index import into pgvector or Qdrant without writing target data.
- `POST /api/v1/rag/vector-store/import?target_backend=pgvector&dry_run=true`: dry-run or execute vector-store bulk upsert into the selected target backend.
- `GET /api/v1/rag/vector-store/parity?target_backend=local_json`: compare local vector chunks with the selected target backend using row-level hashes.
- `GET /api/v1/rag/documents`: list ingested local documents.
- `POST /api/v1/rag/ingest-local`: extract and ingest a local PDF/text/Markdown/JSON document, then optionally rebuild the index.
- `POST /api/v1/rag/rebuild`: rebuild the local RAG index from seed records.
- `POST /api/v1/rag/search`: retrieve chunks from the local RAG index.
- `POST /api/v1/rag/evaluate`: retrieve chunks with coverage, missing facets, sources, score breakdown, query fingerprint, ranking policy, retrieval trace, and per-result rationale.
- `POST /api/v1/rag/evaluate/export.zip`: export a manifested RAG evaluation bundle with request, evaluation JSON, retrieval trace, score CSV, retrieved chunks JSONL, RAG status, and structured manifest.
- `POST /api/v1/rag/evaluate/export/verify`: rebuild and semantically verify the RAG evaluation bundle for the same request.
- `GET /api/v1/rag/vector-index/export.zip`: export the full local RAG vector index as JSONL plus pgvector schema, Qdrant collection config, payload contract, diagnostics, and structured manifest.
- `GET /api/v1/rag/vector-index/export/verify`: rebuild and semantically verify the full RAG vector-index migration bundle.
- `GET /api/v1/rag/regression`: run fixed retrieval regression cases for required evidence coverage, Recall@k, and nDCG@k.
- `GET /api/v1/rag/regression/export.zip`: export a manifested RAG regression suite bundle with results, stable result hashes, cases, metrics CSV, weak cases, diagnostics, ranking policy, RAG status, and structured manifest.
- `GET /api/v1/rag/regression/export/verify`: rebuild and semantically verify the RAG regression suite bundle.
- `GET /api/v1/rag/regression/cases`: inspect the retrieval regression fixture.
- `GET /api/v1/runs`: list persisted design/report runs.
- `GET /api/v1/runs/{run_id}`: fetch one persisted run with request, design, QC report, and trace.
- `GET /api/v1/runs/{run_id}/export.zip`: export an audit bundle with request, design, QC reports, trace, candidate CSV, RAG status, structured manifest, and file-level SHA-256 artifact manifest.
- `GET /api/v1/runs/{run_id}/export/verify`: rebuild the run audit bundle and verify its artifact manifest hashes.
- `GET /api/v1/runs/{run_id}/workflow/export.zip`: export a manifested workflow trace bundle with task, plan, trace, agent-role contract, RAG/data provenance, and run summary.
- `GET /api/v1/runs/{run_id}/workflow/export/verify`: rebuild and semantically verify the workflow trace bundle, including trace/plan hash consistency and required role evidence.
- `GET /api/v1/runs/{run_id}/artifacts/{artifact_type}`: fetch saved report artifacts.
- `GET /api/v1/jobs`: list queued, running, succeeded, and failed background jobs.
- `GET /api/v1/jobs/{job_id}`: inspect a background job request, result, timestamps, and error.
- `GET /api/v1/jobs/{job_id}/export.zip`: export a job audit bundle with request, result, batch CSVs, run summaries, linked run QC files, and file-level SHA-256 artifact manifest.
- `GET /api/v1/jobs/{job_id}/export/verify`: rebuild the job audit bundle and verify its artifact manifest hashes.
- `POST /api/v1/jobs/design-from-gene`: enqueue a gene-to-design workflow and persist the resulting run.
- `POST /api/v1/jobs/batch-design-from-genes`: enqueue up to 50 gene-to-design workflows, preserving per-gene successes and failures.
- `POST /api/v1/jobs/data-refresh`: enqueue a GTEx/Allen reference refresh job.
- `GET /api/v1/structured/status`: inspect loaded structured GTEx/Allen/CUSTOM seed records.
- `GET /api/v1/structured/manifest`: inspect structured source files, SHA-256 hashes, releases, and validation.
- `GET /api/v1/structured/validate`: validate required fields and codon matrices.
- `GET /api/v1/data/catalog`: inspect refreshable and local structured data sources, default panel, manifest, and last refresh.
- `GET /api/v1/data/coverage`: inspect live-versus-seed structured coverage by dataset, gene/region, cell type, release, and source snapshot completeness.
- `GET /api/v1/data/quality`: evaluate dataset-level structured data promotion readiness, live/seed fractions, release pinning, source hash/snapshot coverage, and operator actions.
- `GET /api/v1/data/provenance`: audit structured records, source hashes, release metadata, RAG index, documents, and refresh provenance.
- `POST /api/v1/data/provenance/baseline`: append a local data baseline event to the refresh audit log and refresh the data lockfile.
- `GET /api/v1/data/lockfile`: compare the current data/RAG/document state against the persisted reproducibility lockfile.
- `POST /api/v1/data/lockfile/write`: write the current data/RAG/document state as the reproducibility lockfile.
- `GET /api/v1/data/release-lock`: compare current structured dataset releases and file hashes against the persisted release lockfile.
- `POST /api/v1/data/release-lock/write`: write current structured dataset releases and file hashes as the release lockfile.
- `GET /api/v1/data/release/export.zip`: export a release-pinned data evidence bundle with manifests, quality/provenance gates, release locks, structured records, record-file hashes, source bytes, RAG status, and refresh history.
- `GET /api/v1/data/release/export/verify`: rebuild and semantically verify the data release bundle for required files, record consistency, JSONL/CSV record hashes, source-byte inclusion, and promotion status.
- `POST /api/v1/data/refresh`: refresh a reproducible GTEx/Allen reference panel, optionally as a dry run.
- `GET /api/v1/data/refresh-log`: inspect refresh audit entries.
- `python scripts/data_refresh.py plan|apply`: run the same GTEx/Allen refresh path from the backend CLI, with dry-run planning, evidence JSON output, and optional release-lock writing.
- `GET /api/v1/data/external-sources`: inspect saved external API/file source snapshots used by live imports and source snapshot coverage.
- `POST /api/v1/data/external-sources/backfill?dry_run=true`: plan or apply source snapshot backfill for bundled/manual structured files that predate snapshot tracking.
- `GET /api/v1/data/snapshot.zip`: export structured data, document extracts, RAG index, refresh log, manifests, and artifact-level SHA-256 hashes as a reproducibility snapshot.
- `GET /api/v1/data/snapshot/verify`: rebuild the data snapshot and verify artifact hashes plus semantic snapshot evidence (`snapshot_manifest_hash`, structured manifest consistency, RAG index hash, and external snapshot file count).
- `GET /api/v1/artifacts/data-snapshots/semantic-summary`: inspect archived data snapshot semantic verification, freshness, manifest hashes, RAG index hashes, and external snapshot file counts.
- `POST /api/v1/structured/search`: search structured priors for a target context.
- `POST /api/v1/structured/import/preview`: preview source parsing, replacement impact, duplicate IDs, projected manifest hash, merged validation, and provenance before mutating structured data.
- `POST /api/v1/structured/import`: import local JSON/CSV structured prior files and archive a manifested, semantically verified structured import audit bundle.
- `python scripts/structured_import.py preview|apply <file>`: run the same structured import preview/apply flow from the backend CLI, with audit ZIP generation and optional immutable archive storage.
- `POST /api/v1/external/gtex/import-gene-expression`: fetch GTEx median gene expression for a gene and brain regions.
- `POST /api/v1/external/allen/import-whb-taxonomy`: fetch filtered Allen WHB taxonomy records from the public S3 metadata release.
- `GET /api/v1/workflow/status`: inspect the local role-graph runtime contract, required agent roles, and LangGraph-compatible migration state shape.
- `POST /api/v1/workflow/plan`: return workflow runtime metadata and the planner output for a gene design request.
- `POST /api/v1/workflow/design-from-gene`: run the gene design workflow and return workflow/trace summary.
- `POST /api/v1/optimize`: generate synonymous candidates and return ranked Pareto-style results.
- `POST /api/v1/design-from-gene`: fetch CDS from a gene symbol, optimize it, and return provenance.
- `POST /api/v1/report-from-gene`: run gene-to-design and return only the structured QC report.
- `POST /api/v1/report-from-gene/export/{markdown|html|json|pdf}`: export gene-to-design QC report.
- `POST /api/v1/report-from-gene/export-bundle.zip`: export a manifested QC bundle with JSON, Markdown, HTML, PDF, candidate CSV, data-quality evidence, optimizer-stress evidence, and provenance.
- `POST /api/v1/report-from-gene/export-bundle/verify`: rebuild and verify the manifested gene-to-design QC bundle, including report/optimizer reproducibility, data-quality, and optimizer-stress cross-checks.
- `POST /api/v1/report`: return a QC-report-shaped JSON payload.
- `POST /api/v1/report/export/{markdown|html|json|pdf}`: export CDS-input QC report.
- `POST /api/v1/report/export-bundle.zip`: export a manifested CDS-input QC bundle with all report formats, request/report/candidate-ranking hashes, data-quality evidence, optimizer-stress evidence, and provenance.
- `POST /api/v1/report/export-bundle/verify`: rebuild and verify the manifested CDS-input QC bundle, including report/optimizer reproducibility, data-quality, and optimizer-stress cross-checks.

Design and report responses include per-candidate `selection_trace` and `constraint_risk` fields so operators can explain the recommended candidate, inspect fallback Pareto trade-offs, and see pass/warning/fail counts for payload, motif, splice, GC-window, hairpin, deterministic secondary-structure proxy, and complexity risks. Workflow runs carry role-level trace rows, and run audit bundles include `workflow_summary.json`. QC reports also summarize structured data quality and optimizer stress state; QC bundles preserve the full `data_quality.json` and `optimizer_stress.json` evidence files, carry candidate summaries in `candidate_ranking.csv`, and verify that the report, CSV, and evidence files remain consistent.

## Gene-to-Design Example

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/api/v1/design-from-gene `
  -ContentType 'application/json' `
  -InFile .\\examples\\design_from_gene_request.json
```

The dashboard supports both gene-symbol design through `/design-from-gene` and offline CDS design through `/optimize`. The CDS mode is useful for reproducible local UI smoke tests because it exercises optimizer, QC report, candidate risk, and selection-trace rendering without depending on Ensembl availability.

## Ingest a Local PDF

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/api/v1/rag/ingest-local `
  -ContentType 'application/json' `
  -Body (@{
    source_path = 'E:\path\to\paper.pdf'
    title = 'Paper title'
    collection = 'literature'
    evidence_class = 'method_paper'
    species = @('human')
    topics = @('codon optimization', 'gene therapy')
    rebuild_index = $true
  } | ConvertTo-Json)
```

## Current MVP Caveats

- Set `APP_DATA_DIR` or `RAG_DATA_DIR` to move documents, structured data, RAG index, and run store into a persistent mounted directory.
- Set `RAG_EMBEDDING_BACKEND=hash_bow`, `sentence_transformers`, or `openai` to choose the RAG embedding adapter. `sentence_transformers` uses `RAG_EMBEDDING_MODEL`, `RAG_EMBEDDING_DIMENSIONS`, and local-files-only loading so the model can be pinned before deployment. `openai` uses `OPENAI_API_KEY`, `OPENAI_EMBEDDING_BASE_URL`, `RAG_EMBEDDING_MODEL`, and `RAG_EMBEDDING_DIMENSIONS`; it only sends embedding requests when explicitly selected, so local/CI defaults do not spend API credits. OpenAI embeddings are cached by model, dimension, and text hash under `APP_DATA_DIR/runtime/openai_embedding_cache.json` to reduce repeated API calls during index rebuilds.
- Set `RAG_VECTOR_BACKEND=local_json`, `pgvector`, or `qdrant` to choose the RAG vector candidate backend. Production templates default to pgvector with `RAG_PGVECTOR_TABLE=rag_chunks`; Qdrant requires `QDRANT_URL` and `QDRANT_COLLECTION`.
- Codon adaptiveness uses a built-in human MVP table plus optional CUSTOM-style structured seed multipliers and Kapur-style tRNA availability priors, not yet a release-pinned external quantitative resource.
- MANE selection uses Ensembl REST `mane=1` metadata when available. The current priority is MANE Select, MANE Plus Clinical, Ensembl canonical, then longest protein-coding fallback.
- Tissue/cell-type evidence is surfaced as coverage context and CUSTOM seed priors can adjust codon weights; production use still needs release-pinned quantitative matrices.

## Verification

```powershell
cd ..
python scripts/preflight.py
```

Contract and golden checks can be run from the repository root:

```powershell
cd ..
python scripts/api_contract_test.py
python scripts/golden_response_test.py
python scripts/golden_value_test.py
```

Backend-only smoke and manual regression checks can be run from `backend`:

```powershell
cd backend
python -c "from tests import test_optimizer as t; [getattr(t, name)() for name in dir(t) if name.startswith('test_')]; print('manual tests passed')"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
python scripts/smoke_test.py
```
- Evidence retrieval defaults to a local hybrid hash/BM25 RAG index, and now exposes a `sentence-transformers` embedding adapter boundary plus deployment/audit evidence. Treat production retrieval as promotion-ready only after a pinned biomedical model is installed locally and the RAG regression suite passes with that model.
- `secondary_structure_proxy_score` and `mfe_proxy_delta_g` remain deterministic design-support proxies in the core scorer, but `/optimizer/rna-folding/*`, optimizer diagnostics, benchmark bundles, deployment readiness, and production audit now expose a configurable ViennaRNA `RNAfold` backend boundary for thermodynamic evidence. Dedicated splice/polyA/restriction classifiers and stricter authenticated multi-user run policies are planned next modules.
