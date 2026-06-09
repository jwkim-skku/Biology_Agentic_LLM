# Deployment

The included production path is Docker Compose with persistent backend data mounted into `backend/app/data`.

Production profile template:

```powershell
Copy-Item .env.production.example .env.production
```

Replace every placeholder secret in `.env.production` before use. The template enables the settings expected by the production readiness gate: Postgres runtime storage, API keys with explicit RBAC roles, request rate limiting, artifact HMAC signing, CORS narrowed to the deployed dashboard origin, and nonzero artifact retention.

Validate the template shape in CI/local preflight, then validate the filled file before deployment:

```powershell
python scripts/validate_production_env.py --template
python scripts/validate_production_env.py --path .env.production
```

Validate the Compose deployment graph before starting containers:

```powershell
python scripts/compose_preflight.py
docker compose --env-file .env.production -f docker-compose.yml -f docker-compose.production.yml config
```

`docker-compose.production.yml` is an override for production-like launches. It requires Postgres storage, API keys with explicit role mapping, artifact signing, nonzero retention settings, a configured frontend API URL, and a healthy Postgres service before the backend starts.
`scripts/compose_preflight.py` uses a synthetic production env with Postgres, signing, rate-limit, OpenAI embedding, and object-store mirror values, so `docker compose config` checks the same required variables that production startup demands.

Create a deployment audit artifact before promotion:

```powershell
python scripts/production_audit.py --path .env.production --require-api
python scripts/production_audit.py --path .env.production --require-api --preflight-evidence backend/app/data/runtime/preflight_latest.json
```

For CI or a workstation without a live backend, use the template/static profile:

```powershell
python scripts/production_audit.py --template --skip-api
```

The audit writes paired JSON and Markdown reports to `backend/app/data/runtime/production_audits`. The full profile combines production env validation, Docker Compose deployment-shape checks, optional recent preflight evidence validation, and live API checks for health readiness, deployment readiness, production audit bundle verification, security, storage, data provenance, data-release bundle verification, external source coverage, RAG diagnostics, RAG embedding status, RAG evaluation/regression bundle verification, optimizer diagnostics, RNAfold backend status, optimizer benchmark bundle verification, QC report bundle request-provenance verification, governance attestation verification, artifact-ledger verification, object-store mirror backlog, and archived QC/structured-import/data-release/RAG/vector-index/optimizer/workflow-trace semantic summaries. The live bundle checks require API auth, RBAC bindings, at least one API key, positive rate limiting, artifact signing key ids, Postgres target/active storage with configured `DATABASE_URL`, Postgres schema hash, driver availability, vector extension readiness, deployment readiness gate/action hashes, production audit verifier `semantic_summary` status/hash/count consistency, required action coverage/detail hashes, production RAG embeddings with `model_fingerprint_hash`, OpenAI price/budget guardrails when used, validated RNAfold execution backend, governance attestation hash plus verified signature and bundle file checks, artifact-ledger entries/latest hash with zero missing archived artifacts, data-release record hashes, tRNA caveat count/blocking-use evidence, RAG evaluation retrieval-trace/evidence-sufficiency/facet-gap/query-term/top-source coverage checks, the RAG regression result hash, archived RAG vector-index chunk/dimension/model/retrieval/manifest evidence, optimizer benchmark result hash plus seed-strategy/candidate-diagnostics/recommendation-audit checks, archived optimizer benchmark/stress/case/folding-candidate-match evidence, workflow trace step/hash/task evidence, object-store mirror plan readiness with zero pending candidates when enabled, and QC request/report/candidate-ranking hashes plus recommendation-readiness/recommended-folding candidate IDs, optimizer reproducibility, and candidate-ranking explainability checks before promotion. The CLI JSON includes an `audit_hash`; the write-mode console output uses `agentic-rag-cli-production-audit-write-result-v1` and repeats the audit/preflight hashes plus JSON/Markdown SHA-256 values beside the artifact paths for CI log traceability. The Markdown report repeats the hash, summarizes live API evidence status counts when API checks run, and includes the preflight evidence root, output JSON path, age, required-check count, missing-check count, declared/actual failed-check count, status-mismatch count, skipped-check count, and missing command/cwd/duration/details/output evidence counts for promotion review. When `--preflight-evidence` is supplied, stale, failing, or incomplete preflight JSON is treated as a production audit failure.

Useful runtime variables:

- `APP_DATA_DIR`: persistent backend data root.
- `CORS_ORIGINS`: comma-separated frontend origins.
- `STORAGE_BACKEND`: `sqlite` for local runtime storage or `postgres` for the optional Postgres run/job/audit adapter.
- `DATABASE_URL`: optional Postgres connection URL. It is redacted in status payloads.
- `RAG_VECTOR_BACKEND`: `local_json`, `pgvector`, or `qdrant`. Production promotion expects `pgvector` or `qdrant`.
- `RAG_PGVECTOR_TABLE`: pgvector table used when `RAG_VECTOR_BACKEND=pgvector`.
- `QDRANT_URL` and `QDRANT_COLLECTION`: Qdrant endpoint and collection used when `RAG_VECTOR_BACKEND=qdrant`.
- `RAG_EMBEDDING_BACKEND`: `hash_bow`, `sentence_transformers`, or `openai`. Production promotion expects `sentence_transformers` with a pinned local model or `openai` with an API key and regression evidence.
- `OPENAI_API_KEY` and `OPENAI_EMBEDDING_BASE_URL`: optional managed embedding backend credentials used only when `RAG_EMBEDDING_BACKEND=openai`. Repeated OpenAI embedding calls are cached under `APP_DATA_DIR/runtime/openai_embedding_cache.json`; `GET /api/v1/rag/embedding/status` reports cache existence, entry count, file SHA-256, entry-key hash, model/dimension distribution, estimated input tokens, estimated spend, and invalid-entry count for promotion evidence.
- `OPENAI_EMBEDDING_PRICE_PER_1K_TOKENS` and `OPENAI_EMBEDDING_BUDGET_USD`: optional operator-supplied cost controls for managed embedding runs. The application does not hard-code provider pricing; set the current embedding price and budget before production refreshes to expose estimated spend and remaining budget in `/rag/embedding/status`. When a budget is configured, uncached OpenAI embedding calls are blocked before the network request if the estimated cached spend plus the estimated request cost would exceed the budget.
  Both Compose profiles pass these OpenAI embedding variables through to the backend container, and `scripts/compose_preflight.py` validates the pass-through path with a synthetic OpenAI embedding profile before deployment.
- `API_KEYS`: comma-separated API keys. Empty means local-development mode.
- `API_KEY_ROLES`: optional API key role mapping. Use `key=admin;other=viewer,operator` or JSON such as `{"key":["admin"]}`. Without this mapping, configured keys default to `admin`.
- `NEXT_PUBLIC_API_KEY`: browser demo key when API auth is enabled. It must map to a configured `viewer` or `operator` role, never `admin`.
- `RATE_LIMIT_PER_MINUTE`: per-client in-process request limit. Set `0` to disable.
- `ARTIFACT_SIGNING_KEY`: optional HMAC signing secret for run/job/data export manifests.
- `ARTIFACT_SIGNING_KEY_ID`: optional public identifier for the signing key used in artifact manifests.
- `ARTIFACT_ED25519_PRIVATE_KEY`: optional Ed25519 private key for asymmetric artifact and governance-attestation signatures. Supports PEM or base64 raw private key bytes.
- `ARTIFACT_ED25519_PUBLIC_KEY`: optional Ed25519 public key for external verification. Supports PEM or base64 raw public key bytes.
- `ARTIFACT_ED25519_KEY_ID`: public identifier for the Ed25519 keypair when Ed25519 signing or verification is configured.
- `ARTIFACT_RETENTION_DAYS`: archived export retention window. `0` disables automatic eligibility.
- `ARTIFACT_RETENTION_KEEP_MIN`: minimum newest archived exports to preserve when retention is applied.
- `ARTIFACT_OBJECT_STORE_ENABLED`: set `true` to enable S3-compatible mirroring for immutable archived ZIP bundles.
- `ARTIFACT_OBJECT_STORE_ENDPOINT`: S3-compatible HTTPS endpoint, such as AWS S3 or MinIO behind TLS.
- `ARTIFACT_OBJECT_STORE_BUCKET`: destination bucket for archived bundle mirrors.
- `ARTIFACT_OBJECT_STORE_PREFIX`: destination object key prefix.
- `ARTIFACT_OBJECT_STORE_REGION`: SigV4 region used for object-store requests.
- `ARTIFACT_OBJECT_STORE_ACCESS_KEY_ID` and `ARTIFACT_OBJECT_STORE_SECRET_ACCESS_KEY`: object-store credentials used by the mirror endpoint.
  `/api/v1/artifacts/object-store/status`, `/api/v1/artifacts/object-store/mirror/plan`, deployment readiness, the production audit API, and the static production audit CLI expose a lifecycle-policy summary plus `lifecycle_policy_hash`, mirror candidate count, and mirror candidate bytes so promotion reviewers can verify archive retention intent and off-host mirror backlog without inspecting secrets.
- `NEXT_PUBLIC_API_BASE_URL`: frontend API base URL.

Operational endpoints:

- `GET /api/v1/health/live`
- `GET /api/v1/health/ready`
- `GET /api/v1/deployment/readiness`
- `GET /api/v1/deployment/audit`
- `GET /api/v1/deployment/audit/cache`
- `GET /api/v1/deployment/audit/export.zip`
- `GET /api/v1/deployment/audit/verify`
- `GET /api/v1/security/status`
- `GET /api/v1/optimizer/benchmark`
- `GET /api/v1/optimizer/benchmark/export.zip`
- `GET /api/v1/optimizer/benchmark/export/verify`
- `GET /api/v1/optimizer/diagnostics`
- `GET /api/v1/optimizer/stress`
- `GET /api/v1/optimizer/benchmark/cases`
- `GET /api/v1/agent-memory/summary`
- `GET /api/v1/agent-memory`
- `GET /api/v1/agent-memory/{run_id}`
- `GET /api/v1/workflow/status`
- `POST /api/v1/workflow/plan`
- `GET /api/v1/runs/{run_id}/workflow/export.zip`
- `GET /api/v1/runs/{run_id}/workflow/export/verify`
- `GET /api/v1/data/quality`
- `GET /api/v1/rag/regression`
- `GET /api/v1/rag/regression/export.zip`
- `GET /api/v1/rag/regression/export/verify`
- `GET /api/v1/rag/regression/cases`
- `GET /api/v1/rag/diagnostics`
- `GET /api/v1/rag/vector-store/status`
- `GET /api/v1/rag/vector-store/import/plan?target_backend=pgvector`
- `POST /api/v1/rag/vector-store/import?target_backend=pgvector&dry_run=true`
- `GET /api/v1/rag/vector-store/parity?target_backend=local_json`
- `POST /api/v1/rag/evaluate/export.zip`
- `POST /api/v1/rag/evaluate/export/verify`
- `GET /api/v1/rag/vector-index/export.zip`
- `GET /api/v1/rag/vector-index/export/verify`
- `GET /api/v1/storage/status`
- `GET /api/v1/storage/postgres/schema.sql`
- `POST /api/v1/storage/postgres/schema.sql/write`
- `GET /api/v1/storage/migration/sqlite/summary`
- `GET /api/v1/storage/migration/sqlite/export.zip`
- `GET /api/v1/storage/migration/sqlite/parity`
- `POST /api/v1/storage/migration/sqlite/import?dry_run=true`
- `GET /api/v1/governance/attestation`
- `GET /api/v1/governance/attestation/export.zip`
- `GET /api/v1/governance/attestation/verify`
- `GET /api/v1/metrics`
- `GET /api/v1/metrics/prometheus`
- `GET /api/v1/audit/summary`
- `GET /api/v1/audit/events`
- `GET /api/v1/artifacts/summary`
- `GET /api/v1/artifacts/qc-bundles/semantic-summary`
- `GET /api/v1/artifacts/structured-imports/semantic-summary`
- `GET /api/v1/artifacts/data-snapshots/semantic-summary`
- `GET /api/v1/artifacts/data-refresh-plans/semantic-summary`
- `GET /api/v1/artifacts/data-releases/semantic-summary`
- `GET /api/v1/artifacts/rag-evaluations/semantic-summary`
- `GET /api/v1/artifacts/rag-regressions/semantic-summary`
- `GET /api/v1/artifacts/rag-vector-indexes/semantic-summary`
- `GET /api/v1/artifacts/optimizer-benchmarks/semantic-summary`
- `GET /api/v1/artifacts/workflow-traces/semantic-summary`
- `GET /api/v1/artifacts`
- `GET /api/v1/artifacts/retention/plan`
- `POST /api/v1/artifacts/retention/apply?dry_run=true`
- `GET /api/v1/artifacts/object-store/status`
- `GET /api/v1/artifacts/object-store/mirror/plan`
- `POST /api/v1/artifacts/object-store/mirror?dry_run=true`
- `GET /api/v1/artifacts/ledger`
- `GET /api/v1/artifacts/ledger/verify`
- `POST /api/v1/artifacts/ledger/backfill?dry_run=true`
- `GET /api/v1/data/release-lock`
- `POST /api/v1/data/release-lock/write`
- `POST /api/v1/data/refresh/plan/export.zip`
- `POST /api/v1/data/refresh/plan/export/verify`
- `GET /api/v1/data/release/export.zip`
- `GET /api/v1/data/release/export/verify`
- `GET /api/v1/data/external-sources`
- `POST /api/v1/data/external-sources/backfill?dry_run=true`
- `POST /api/v1/report/export-bundle.zip`
- `POST /api/v1/report/export-bundle/verify`

The semantic-summary endpoints include a `freshness_status`, `latest_created_at`, `latest_age_hours`, and `freshness_policy.warning_hours` so production reviewers can distinguish current archive evidence from stale or untimestamped artifacts.

Smoke test:

```powershell
cd backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
python scripts/smoke_test.py
```

The smoke test checks health readiness, deployment readiness, settings, security status, RAG status, structured data status, metrics, CDS report generation, release-lock pinning, data release bundle export/verification, data refresh plan bundle export/verification, run export archival and verification, data snapshot archival and semantic verification, a dry-run background data-refresh job with job export archival/verification, artifact archive download/re-verification, indexed semantic archive summaries including data snapshots and workflow traces, artifact ledger verification, and audit-log visibility. Data snapshot verification exposes `snapshot_manifest_hash`, `structured_manifest_hash`, `rag_index_hash`, `external_snapshot_file_count`, and file-list consistency checks so reviewers can confirm the exported local data/RAG state is reproducible; `/api/v1/artifacts/data-snapshots/semantic-summary` indexes the same evidence for archived snapshots and production audits. Data release bundles include `record_source_summary.json` plus `record_source_summary_hash`, `dataset_count`, and `source_file_count` so reviewers can verify dataset/release/source-file composition independently from row-level JSONL/CSV hashes. Data refresh plan bundles include `request_hash`, `operations_hash`, `data_catalog_hash`, `external_sources_hash`, `structured_quality_hash`, `data_provenance_hash`, and `rag_status_hash` so reviewers can verify the normalized request, planned operation CSV, source catalog, external-source snapshot state, promotion quality gate, provenance caveats, and RAG state independently of row counts.

RAG evaluation responses include `query_fingerprint`, `ranking_policy`, `retrieval_trace`, `facet_gap_analysis`, `query_term_coverage`, `top_sources`, per-result `rationale`, and stable `rank_evidence_hash` fields so operators can reproduce and explain why evidence chunks were selected for a design context, which source families support the result set, and which requested facets are absent from retrieved results or the indexed corpus. Retrieval v3 expands candidate search with requested biological facets, records a `retrieval_query_hash`, adds intent-match scoring for query-specific source families such as CUSTOM/codon, MANE, Allen, GTEx, and AAV, and diversifies final evidence by collection/source before ranking by score. `POST /api/v1/rag/evaluate/export.zip` turns the same evaluation into a manifested ZIP with `evaluation.json`, `retrieval_trace.json`, `evidence_sufficiency.json`, `facet_gap_analysis.json`, `query_term_coverage.json`, `top_sources.json`, `source_provenance.json`, `score_breakdown.csv`, `chunks.jsonl`, `rag_status.json`, and `structured_manifest.json`; the bundle manifest records `evaluation_hash`, `retrieval_trace_hash`, `evidence_sufficiency_hash`, `facet_gap_analysis_hash`, `query_term_coverage_hash`, `top_sources_hash`, `source_provenance_hash`, `score_breakdown_hash`, and `chunks_hash`, and `POST /api/v1/rag/evaluate/export/verify` recomputes those hashes plus the semantic checks before reuse in audit records.

RAG regression exports capture the fixed retrieval regression suite as deployment evidence. `GET /api/v1/rag/regression/export.zip` writes `regression.json`, `cases.json`, `case_metrics.csv`, `weak_cases.json`, `quality_summary.json`, `source_provenance_summary.json`, `diagnostics.json`, `ranking_policy.json`, `rag_status.json`, and `structured_manifest.json`; `regression.json` and the bundle manifest include stable `results_hash`, `quality_summary_hash`, `case_metrics_hash`, and `source_provenance_summary_hash` values, and `GET /api/v1/rag/regression/export/verify` checks case hash consistency, result hash consistency, regression quality summary schema/hash/case-count/status, case metric row counts, required case-metric explainability columns, case-metric hash consistency, case source-provenance hash/count consistency, diagnostics schema, weak-case counts, ranking policy, and structured manifest consistency before the archive is reused for search-policy promotion.

Workflow trace exports make the PDF's agentic role topology auditable independently from full run bundles. `GET /api/v1/workflow/status` reports the local deterministic role-graph runtime and LangGraph-compatible state contract. `GET /api/v1/runs/{run_id}/workflow/export.zip` writes task, plan, trace, role-contract, run summary, RAG status, and structured manifest files; `GET /api/v1/runs/{run_id}/workflow/export/verify` checks trace row shape, required files, workflow id consistency, trace/plan hashes, and structured-manifest consistency.

QC report exports are promotion evidence, not just rendered summaries. `POST /api/v1/report/export-bundle.zip` and the gene-based equivalent include `data_quality.json`, `optimizer_stress.json`, `recommendation_audit.json`, `recommendation_readiness.json`, `recommended_folding_evidence.json`, and `report_formats_summary.json` beside the report formats, request/report/report-format/candidate-ranking/recommendation-audit/recommendation-readiness/folding-evidence hashes, candidate ranking CSV, optimizer reproducibility manifest, RAG status, and structured manifest. Candidate diagnostics include a hash-pinned sequence-policy audit with candidate-level motif categories, positions, status counts, top motifs, recommended synonymous-edit actions, and a hash-pinned Pareto quality summary with front size, feasible-front count, recommended-front membership, score ranges, and approximate hypervolume. The report itself includes `evidence_summary.retrieval_quality` with source/collection diversity, high-confidence count, retrieval model, embedding model, top sources, score ranges, rank-evidence count, and rank-evidence hash, plus `recommendation_readiness` with release-ready status, constraint/Pareto/folding/data-quality/stress/QC gate status, blocking/warning reasons, and a stable readiness hash. Bundle verification cross-checks the report's data-quality, optimizer-stress, retrieval-quality rank evidence, request/report/report-format-summary/candidate-ranking/recommendation-audit/recommendation-readiness/recommended-folding-evidence hashes, recommended-candidate IDs across readiness and folding evidence, candidate-diagnostics count, recommendation evidence, and report-vs-CSV candidate IDs/ranks/key scores before the archive can be treated as semantically clean. `/api/v1/artifacts/qc-bundles/semantic-summary` indexes the same QC archive evidence, including request/report/report-format/candidate hashes, recommendation readiness status/hash/candidate ID, recommended folding evidence status/hash/candidate ID, retrieval-quality status/source/rank-evidence counts, optimizer objective count, data-quality status, and optimizer-stress status for production audits and dashboard review.

`GET /api/v1/rag/embedding/status` reports the configured RAG embedding backend (`hash_bow`, `sentence_transformers`, or `openai`), active adapter, fallback state, model id, dimensions, production readiness, `model_fingerprint_hash`, and OpenAI embedding-cache integrity and budget fields when the managed backend is configured. The fingerprint pins backend, model, dimensions, local-files policy, OpenAI budget readiness, and cache identity for promotion review. OpenAI production readiness requires a configured key, positive price, positive budget, and current estimated spend within budget; budget-overrun requests fall back without sending a provider call. Local development defaults to deterministic `hash-bow-v1`; production should install and pin a locally available biomedical `sentence-transformers` model or configure OpenAI embeddings before rebuilding the RAG index and rerunning regression.

`GET /api/v1/rag/vector-store/status` reports the configured RAG vector backend (`local_json`, `pgvector`, or `qdrant`), the active adapter, fallback state, pgvector table name, and Qdrant collection settings. `GET /api/v1/rag/vector-index/export.zip` exports the complete local RAG vector index for migration planning. The manifested ZIP includes `vector_chunks.jsonl`, `payload_schema.json`, `embedding_status.json`, `pgvector_schema.sql`, `qdrant_collection.json`, `vector_store_import_plan.json`, `vector_store_parity.json`, `rag_status.json`, `rag_diagnostics.json`, and `structured_manifest.json`; `GET /api/v1/rag/vector-index/export/verify` checks row counts, embedding dimensions, embedding status schema, embedding-model consistency, unique chunk IDs, required payload fields, vector-readiness schema, migration-plan schema, parity-report schema, vector row hashes, and structured-manifest hash consistency before the archive is stored in the artifact ledger.

RAG vector-store cutover follows the same dry-run-first pattern as runtime storage migration. Use `GET /api/v1/rag/vector-store/import/plan?target_backend=pgvector` to inspect the source row count, row hash, target requirements, and migration steps; use `POST /api/v1/rag/vector-store/import?target_backend=pgvector&dry_run=true` for an audited dry-run. When the target service is configured, call the same import endpoint with `dry_run=false`, then verify `GET /api/v1/rag/vector-store/parity?target_backend=pgvector` before setting `RAG_VECTOR_BACKEND=pgvector`. Qdrant uses the same endpoints with `target_backend=qdrant`.

Optimizer benchmark exports follow the same audit pattern. `GET /api/v1/optimizer/benchmark/export.zip` writes `benchmark.json`, `diagnostics.json`, `cases.json`, `case_metrics.csv`, `candidate_diagnostics.json`, `recommendation_summary.json`, optimizer/score config snapshots, and `structured_manifest.json`; `benchmark.json` and the bundle manifest include a stable `results_hash`, and the manifest also records `benchmark_hash`, `diagnostics_hash`, `case_metrics_hash`, `candidate_diagnostics_hash`, and `recommendation_summary_hash`. `case_metrics.csv` includes recommendation trade-off/regret, Pareto quality hash, recommended-front membership, feasible-front count, approximate hypervolume, recommended folding candidate ID, and deterministic secondary-structure/MFE-proxy metrics. `recommendation_summary.json` aggregates recommended-candidate trade-off counts, max/mean regret, Pareto-front coverage, and folding evidence coverage across benchmark cases. `GET /api/v1/optimizer/benchmark/export/verify` checks case hashes, result hashes, file hashes, case counts, required CSV metric columns, diagnostics schemas, candidate diagnostics coverage, recommendation summary schema/hash/consistency, folding-evidence candidate ID consistency, Pareto quality schema/hash consistency, recommendation-audit trade-off metadata, and structured manifest consistency; archived benchmark summaries expose the folding-candidate match count for deployment review.

RNA folding backend readiness is exposed separately from deterministic scoring proxies. `GET /api/v1/optimizer/rna-folding/status` reports the requested backend, active backend, fallback state, executable path, timeout, production readiness, and RNAfold execution-evidence capabilities. `POST /api/v1/optimizer/rna-folding/evaluate` evaluates a CDS window with ViennaRNA `RNAfold --noPS` when `RNA_FOLDING_BACKEND=rnafold` is configured and the executable is available; otherwise it returns the deterministic proxy evidence and a warning. Successful and failed RNAfold executions include hash-only stdout/stderr evidence, return code, line counts, and parsed-structure hash without persisting raw sequence-bearing RNAfold output. Optimizer diagnostics, benchmark ZIPs, deployment readiness, and production audit bundles include the same folding-backend evidence. Production deployments should install ViennaRNA and set `RNA_FOLDING_BACKEND=rnafold`, `RNAFOLD_EXECUTABLE`, `RNAFOLD_TIMEOUT_SECONDS`, and `RNAFOLD_WINDOW_NT`.

Deployment readiness:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/deployment/readiness
```

This returns pass/warning/fail gates for structured data, data provenance, structured promotion quality, data release bundle verification, RAG regression, RAG embedding backend, optimizer benchmark, RNA folding backend, workflow runtime, QC snapshot export, storage, security, artifact signing, governance attestation, artifact archive integrity, artifact object-store mirroring, archived QC bundle semantic verification, archived data snapshot semantic verification, archived data-refresh plan semantic verification, archived data-release semantic verification, archived RAG evaluation semantic verification, archived RAG regression semantic verification, archived RAG vector-index semantic verification, archived workflow-trace semantic verification, and archived structured import audit semantic verification. The response also includes `attention_gates`, `required_actions`, `attention_gates_hash`, and `required_actions_hash` so operators can see and pin blocking versus promotion-only work without reading every gate detail. Each `required_actions[]` item includes a gate-specific `detail_hash`, allowing reviewers to pin the exact evidence object that produced the action text. Each archived RAG vector-index audit must expose chunk/model metadata, recommended backend, migration target backend, parity status, vector row hash, and structured manifest hash before the production audit CLI treats it as complete. The archive semantic gates include `freshness_status`, `latest_created_at`, `latest_age_hours`, and `freshness_warning_hours` in their details. The `data_provenance`, `structured_quality`, `data_release_bundle`, `qc_bundle_archive_semantics`, `data_snapshot_archive_semantics`, `data_refresh_plan_archive_semantics`, `data_release_archive_semantics`, `rag_evaluation_archive_semantics`, `rag_regression_archive_semantics`, `rag_vector_index_archive_semantics`, `workflow_trace_archive_semantics`, `rag_embedding_backend`, and `rna_folding_backend` gates keep tRNA caveats, seed priors, live/source snapshot coverage, release pinning, QC request/report/candidate/recommendation hashes, QC retrieval-quality/data-quality/stress/objective evidence, RAG evaluation source-provenance hashes, RAG regression result/quality/case-metric/source-provenance hashes, snapshot manifest/RAG index/external-file evidence, refresh-plan operation evidence, release-bundle semantic checks, release archive record hashes, release handoff hashes, tRNA caveat/blocking-use evidence, vector-index migration evidence, workflow trace hashes, archive evidence freshness, hash-BOW retrieval fallback, and deterministic structure-proxy fallback visible until replaced with release-pinned quantitative matrices, a pinned biomedical embedding model or configured OpenAI embedding backend, and a validated folding executable. `deployment_ready=true` means no blocking failures were found; `production_ready=true` additionally requires zero warnings.

The strict production gates expect:

- `STORAGE_BACKEND=postgres` with `DATABASE_URL` configured and the active runtime adapter on Postgres.
- `RAG_VECTOR_BACKEND=pgvector` with `RAG_PGVECTOR_TABLE`, or `RAG_VECTOR_BACKEND=qdrant` with `QDRANT_URL` and `QDRANT_COLLECTION`.
- `API_KEYS`, explicit `API_KEY_ROLES`, and `RATE_LIMIT_PER_MINUTE > 0`.
- `ARTIFACT_SIGNING_KEY` plus key id, or Ed25519 signing/verification with `ARTIFACT_ED25519_KEY_ID`.
- `ARTIFACT_OBJECT_STORE_ENABLED=true` with HTTPS endpoint, bucket, prefix, region, and credentials for immutable archive mirroring.
- zero structured-data validation warnings and a clean artifact archive ledger.

Governance attestation:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/governance/attestation
Invoke-WebRequest http://127.0.0.1:8000/api/v1/governance/attestation/export.zip -OutFile governance_attestation.zip
Invoke-RestMethod http://127.0.0.1:8000/api/v1/governance/attestation/verify
```

The attestation ZIP captures OpenAPI shape, structured data provenance, release lock status, RAG status, storage parity, signing status, and artifact ledger verification. Exported attestation bundles are archived into the artifact archive and hash-chain ledger. HMAC signing is available through `ARTIFACT_SIGNING_KEY`; Ed25519 asymmetric signing is available through `ARTIFACT_ED25519_PRIVATE_KEY` plus an optional public verifier key.

Production audit bundle:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/deployment/audit
Invoke-RestMethod http://127.0.0.1:8000/api/v1/deployment/audit/cache
Invoke-RestMethod "http://127.0.0.1:8000/api/v1/deployment/audit?refresh=true"
Invoke-WebRequest http://127.0.0.1:8000/api/v1/deployment/audit/export.zip -OutFile production_audit.zip
Invoke-RestMethod http://127.0.0.1:8000/api/v1/deployment/audit/verify
```

The production audit endpoint uses a short in-process TTL cache for dashboard and verification calls; use `refresh=true` when producing fresh deployment-promotion evidence. The production audit ZIP is a deployment-promotion artifact. It includes JSON and Markdown summaries plus evidence files for deployment readiness, production gap summary, security, storage, data provenance, structured data quality, external source coverage, RAG diagnostics, optimizer diagnostics, workflow runtime, agent memory, governance attestation verification, artifact ledger verification, artifact archive summary, archived QC bundle semantic verification, archived data snapshot semantic verification, archived data-refresh plan semantic verification, archived data-release semantic verification, archived structured import audit semantic verification, archived RAG evaluation/regression/vector-index semantic verification, archived workflow-trace semantic verification, audit timing, and audit-log summary. The `production_gap_summary.json` evidence gives each non-pass check a priority, action, evidence key, detail hashes, and stable gap hash so operators can track promotion gaps separately from the full audit payload. The ZIP also includes `evidence_hashes.json`, a canonical SHA-256 manifest over every embedded evidence JSON file plus a combined hash. Bundle verification recomputes the production audit hash, verifies signatures when configured, checks the deployment-readiness, QC archive, RAG vector-index archive, and workflow-trace archive evidence files against the embedded audit payload, verifies every evidence file hash against `evidence_hashes.json`, verifies latest QC recommendation/folding candidate IDs, verifies latest vector-index migration backend, parity, row-hash evidence, verifies latest workflow trace hash/step evidence when trace artifacts are present, recomputes the audit summary from checks, recomputes every check `detail_hash` against embedded evidence, recomputes the promotion summary and production gap summary from embedded evidence, verifies gap hashes, and recomputes `attention_gates_hash`, `required_actions_hash`, required-action coverage for all non-pass readiness gates, and each required action `detail_hash` against the matching gate details. The verification response and dashboard include a `semantic_summary` with pass/fail/warning counts, status, and a stable summary hash over the semantic checks. The CLI audit also fails archived QC archive evidence that lacks request/report/candidate/recommendation/optimizer hashes, recommendation-readiness candidate ID, recommended-folding candidate ID, retrieval-quality source count/status, data-quality status, optimizer-stress status, or optimizer objective count; archived data snapshot evidence that lacks snapshot manifest, structured manifest, RAG index hash, snapshot file count, or external snapshot file count; archived data-refresh plan evidence that claims checked artifacts but lacks operation count, validation status, dataset id, or structured manifest hash; archived RAG evaluation evidence that lacks source-provenance hash/count evidence; archived RAG regression evidence that lacks case/result/quality/case-metrics/source-provenance/top-source evidence; archived RAG vector-index evidence that lacks chunk count, embedding dimensions/model, retrieval model, recommended backend, migration target backend, pass/warning parity status, vector row hash, or structured manifest hash; and archived optimizer benchmark evidence that lacks benchmark, diagnostics, stress, case-count, case-hash, result-hash, benchmark-file-hash, diagnostics-file-hash, case-metrics-hash, candidate-diagnostics-hash, recommendation-summary hash/status/front-count/regret evidence, case-provenance fingerprint evidence, or recommended-candidate folding evidence. The Markdown report includes the readiness action hash, readiness attention hash, action coverage, production gap count, and slowest audit evidence timings for quick operator triage. Exported bundles are archived into the immutable artifact archive and ledger.

Artifact retention:

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/api/v1/artifacts/retention/plan?retention_days=30&keep_min=100"
Invoke-RestMethod -Method Post "http://127.0.0.1:8000/api/v1/artifacts/retention/apply?dry_run=true&retention_days=30&keep_min=100"
```

Retention apply defaults to `dry_run=true`. When called with `dry_run=false`, eligible ZIP files and archive index rows are removed, and a `retention_delete` tombstone is appended to the artifact ledger so hash-chain verification remains auditable.

Artifact ledger backfill:

```powershell
Invoke-RestMethod -Method Post "http://127.0.0.1:8000/api/v1/artifacts/ledger/backfill?dry_run=true"
Invoke-RestMethod -Method Post "http://127.0.0.1:8000/api/v1/artifacts/ledger/backfill?dry_run=false"
```

Backfill appends `backfill_store` ledger events for archived artifacts that predate ledger support. It preserves the append-only hash chain and lets `/api/v1/artifacts/ledger/verify` return `pass` once all historical archive rows are represented.

Postgres migration contract:

```powershell
Invoke-WebRequest http://127.0.0.1:8000/api/v1/storage/postgres/schema.sql -OutFile postgres_schema.sql
Invoke-WebRequest http://127.0.0.1:8000/api/v1/storage/migration/sqlite/export.zip -OutFile sqlite_to_postgres_migration_bundle.zip
Invoke-RestMethod http://127.0.0.1:8000/api/v1/storage/migration/sqlite/parity
Invoke-RestMethod -Method Post "http://127.0.0.1:8000/api/v1/storage/migration/sqlite/import?dry_run=true"
docker compose --profile postgres up postgres
docker compose --env-file .env.production -f docker-compose.yml -f docker-compose.production.yml up --build
```

SQLite remains the default for local reproducibility. When `STORAGE_BACKEND=postgres` and `DATABASE_URL` are set, run/job/audit stores use the Postgres adapter and apply the published schema automatically. The published Postgres DDL also includes the `vector` extension and `rag_chunks` table for `RAG_VECTOR_BACKEND=pgvector`. The migration bundle exports SQLite rows as JSONL plus manifest hashes; the import endpoint defaults to dry-run and only writes to Postgres when called with `dry_run=false` in a configured environment. The parity endpoint compares record counts and row-level canonical SHA-256 hashes when a target Postgres database is reachable.

Preflight verification:

```powershell
python scripts/preflight.py
python scripts/preflight.py --output-json backend/app/data/runtime/preflight_latest.json
```

This runs backend compile checks, production env template validation, Docker Compose deployment-shape checks, the static production audit profile, API contract checks, structured import CLI preview verification, GTEx/Allen data-refresh CLI plan verification, response/value golden tests, the manual backend regression suite, the frontend production build, the HTTP smoke test, a separate signed-artifact smoke profile with `ARTIFACT_SIGNING_KEY` enabled, and a browser UI smoke test that checks operational panels and metrics hydration. The API contract check pins every current FastAPI route and fails unexpected new `/api/v1` routes until they are added to `REQUIRED_PATHS`; it also requires JSON API operations to declare the shared `ApiResponse` envelope unless explicitly listed as raw export, download, text, or health responses. Use `--skip-frontend`, `--skip-smoke`, `--skip-signing-smoke`, or `--skip-ui-smoke` for narrower deployment diagnostics. `--output-json` writes a timestamped `agentic-rag-production-preflight-evidence-v1` payload with the command, working directory, duration, required-check list/count, check/pass/fail/skip counts, pass/fail result, compact stdout/stderr, parsed JSON details for every check, `mode_hash`, `skipped_hash`, `checks_hash`, and `preflight_hash`; CI uploads this file as the `backend-preflight-evidence` artifact, then validates it through `production_audit.py --preflight-evidence` and uploads the resulting `backend-production-audit` artifact. The production audit also checks that preflight structured-import and data-refresh details include source hashes, planned operations, dry-run status, validation schema, normalized gene/region requests, repository-root/output-path consistency, per-check command/cwd/duration/details/stdout/stderr evidence, matching preflight mode/skip/check/evidence hashes, skip-mode consistency, exact failed-list versus nonzero-returncode consistency, per-check status/returncode consistency, and the expected preflight schema/required-check coverage.

API contract and golden tests can also be run directly from the repository root:

```powershell
python scripts/api_contract_test.py
python scripts/golden_response_test.py
python scripts/golden_value_test.py
```

These root wrappers delegate to the backend scripts, matching the same contracts enforced by preflight and CI.
