# Workflow Orchestration

The backend now exposes an explicit local workflow layer that mirrors the PDF's agent topology without requiring a distributed agent runtime.

## Agents

- `planner`: normalizes the request into tools, hard constraints, soft objectives, and evidence policy.
- `transcript_resolver`: fetches canonical CDS with MANE-aware priority.
- `optimizer`: runs seeded NSGA-II over synonymous CDS candidates.
- `retriever`: retrieves hybrid RAG evidence plus structured GTEx/Allen/CUSTOM/tRNA context.
- `qc_writer`: generates the structured QC report.

## API

Plan a gene design task without executing it:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/api/v1/workflow/plan `
  -ContentType 'application/json' `
  -InFile .\backend\examples\design_from_gene_request.json
```

Run the workflow and return only the workflow/trace summary:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/api/v1/workflow/design-from-gene `
  -ContentType 'application/json' `
  -InFile .\backend\examples\design_from_gene_request.json
```

The regular `/design-from-gene` endpoint also uses the same orchestrator and returns the full design payload.

Inspect the active workflow runtime contract:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/workflow/status
```

## Trace

Each workflow step emits a trace item:

- `name`
- `status`
- `timestamp`
- `detail`

Traces are stored in the SQLite run store and displayed in the UI's Agent Trace panel.

Export and verify a workflow-only trace bundle for one saved run:

```powershell
Invoke-WebRequest `
  -Uri http://127.0.0.1:8000/api/v1/runs/{run_id}/workflow/export.zip `
  -OutFile .\workflow_trace_bundle.zip

Invoke-RestMethod http://127.0.0.1:8000/api/v1/runs/{run_id}/workflow/export/verify
```

The bundle contains `workflow.json`, `task.json`, `plan.json`, `trace.json`, `agent_roles.json`, `run_summary.json`, and provenance snapshots. Verification checks required files, trace row shape, plan rows, workflow id consistency, trace/plan hash consistency, and structured-manifest hash consistency. Archived workflow trace bundles are indexed by `/api/v1/artifacts/workflow-traces/semantic-summary`, surfaced in deployment readiness, and included in production audit bundles so reviewers can confirm that agent plans and execution traces are preserved as promotion evidence.

## Agent Memory

Saved runs are indexed into a persistent agent memory store:

- `session_memory`: target, workflow id, and executed trace steps.
- `semantic_memory`: evidence rules, coverage, candidate diagnostics, recommendation audit, and open questions.
- `artifact_memory`: request/design/trace hashes, optimizer manifest hash, recommended candidate id, and selected score summaries.

Inspect memory state:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/agent-memory/summary
Invoke-RestMethod "http://127.0.0.1:8000/api/v1/agent-memory?gene=SNCA&limit=5"
Invoke-RestMethod http://127.0.0.1:8000/api/v1/agent-memory/{run_id}
```

The memory table is included in the Postgres deployment schema, SQLite-to-Postgres migration bundle, deployment readiness gate, production audit evidence, and data snapshot summary. Readiness and audit summaries also expose the latest memory hash and a stable aggregate hash over persisted memory rows so reviewers can detect changes to the indexed agent memory evidence.

## Production Upgrade Path

This local orchestrator keeps runtime dependencies low and makes every step inspectable. A future LangGraph migration can keep the same state shape:

- `task`
- `plan`
- `trace`
- `artifacts`
- `design`
- `qc_report`

The external orchestration engine should preserve the same run store and QC provenance contract.
