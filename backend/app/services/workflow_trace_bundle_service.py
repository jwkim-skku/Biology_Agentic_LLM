from __future__ import annotations

import json
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
from typing import Any
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile

from app.services.export_manifest_service import ManifestedZip, verify_artifact_bundle
from app.services.rag_service import rag_status
from app.services.structured_data_service import structured_manifest


WORKFLOW_TRACE_BUNDLE_SCHEMA = "agentic-rag-workflow-trace-bundle-v1"
WORKFLOW_RUNTIME_SCHEMA = "agentic-rag-workflow-runtime-v1"
REQUIRED_WORKFLOW_TRACE_FILES = {
    "bundle_manifest.json",
    "workflow.json",
    "task.json",
    "plan.json",
    "trace.json",
    "agent_roles.json",
    "run_summary.json",
    "provenance/rag_status.json",
    "provenance/structured_manifest.json",
}


AGENT_ROLES = [
    {
        "agent": "planner",
        "responsibility": "Normalize user intent into explicit constraints, tools, and evidence policy.",
        "required_for": ["gene_to_design", "cds_optimize"],
    },
    {
        "agent": "transcript_resolver",
        "responsibility": "Resolve canonical/MANE-aware transcript and source CDS provenance.",
        "required_for": ["gene_to_design"],
    },
    {
        "agent": "optimizer",
        "responsibility": "Generate protein-preserving synonymous candidates and recommendation audit evidence.",
        "required_for": ["gene_to_design", "cds_optimize"],
    },
    {
        "agent": "retriever",
        "responsibility": "Retrieve RAG and structured context for target-region/cell-type/modality evidence.",
        "required_for": ["gene_to_design"],
    },
    {
        "agent": "qc_writer",
        "responsibility": "Synthesize QC report, warnings, evidence rules, and export-ready provenance.",
        "required_for": ["gene_to_design", "cds_optimize"],
    },
]


def workflow_runtime_status() -> dict[str, Any]:
    return {
        "runtime_schema": WORKFLOW_RUNTIME_SCHEMA,
        "status": "pass",
        "active_runtime": "local_deterministic_orchestrator",
        "external_runtime": {
            "langgraph_configured": False,
            "migration_contract": ["task", "plan", "trace", "artifacts", "design", "qc_report"],
        },
        "agent_roles": AGENT_ROLES,
        "trace_contract": {
            "required_step_fields": ["name", "status", "timestamp", "detail"],
            "status_values": ["ok", "warning", "failed"],
            "bundle_schema": WORKFLOW_TRACE_BUNDLE_SCHEMA,
        },
        "recommendation": "Keep the local trace contract stable if migrating the role graph to LangGraph or another runtime.",
    }


def workflow_trace_summary_from_design(design: dict[str, Any], *, run_id: str | None = None, run_type: str | None = None) -> dict[str, Any]:
    workflow = design.get("workflow") or {}
    task = workflow.get("task") or {}
    plan = list(workflow.get("plan") or [])
    trace = list(design.get("trace") or [])
    task_type = str(task.get("task_type") or "unknown")
    required_agents = _required_agents(task_type)
    plan_agents = [str(item.get("agent") or "") for item in plan if item.get("agent")]
    trace_names = [str(item.get("name") or "") for item in trace]
    missing_agents = [agent for agent in required_agents if agent not in plan_agents]
    missing_trace_roles = [agent for agent in required_agents if not _trace_has_agent(agent, trace_names)]
    invalid_trace_rows = [
        idx + 1
        for idx, item in enumerate(trace)
        if not all(key in item for key in ["name", "status", "timestamp", "detail"])
    ]
    warnings: list[str] = []
    if missing_agents:
        warnings.append(f"Workflow plan is missing required agents: {', '.join(missing_agents)}.")
    if missing_trace_roles:
        warnings.append(f"Workflow trace is missing required role execution evidence: {', '.join(missing_trace_roles)}.")
    if invalid_trace_rows:
        warnings.append(f"Workflow trace contains malformed rows: {', '.join(str(row) for row in invalid_trace_rows[:8])}.")
    if not workflow.get("workflow_id"):
        warnings.append("Workflow id is missing.")
    status = "warning" if warnings else "pass"
    return {
        "workflow_trace_schema": "agentic-rag-workflow-trace-summary-v1",
        "status": status,
        "workflow_id": workflow.get("workflow_id"),
        "run_id": run_id or design.get("run_id"),
        "run_type": run_type,
        "task_type": task_type,
        "required_agents": required_agents,
        "plan_agents": plan_agents,
        "trace_steps": trace_names,
        "trace_step_count": len(trace),
        "missing_agents": missing_agents,
        "missing_trace_roles": missing_trace_roles,
        "invalid_trace_rows": invalid_trace_rows,
        "trace_hash": _hash_payload(trace),
        "plan_hash": _hash_payload(plan),
        "warnings": warnings,
    }


def workflow_trace_summary_from_run(run: dict[str, Any]) -> dict[str, Any]:
    design = run.get("design") or {}
    return workflow_trace_summary_from_design(design, run_id=run.get("run_id"), run_type=run.get("run_type"))


def build_workflow_trace_bundle(run: dict[str, Any]) -> bytes:
    design = run.get("design") or {}
    workflow = design.get("workflow") or {}
    task = workflow.get("task") or {}
    plan = list(workflow.get("plan") or [])
    trace = list(run.get("trace") or design.get("trace") or [])
    summary = workflow_trace_summary_from_run({**run, "design": {**design, "trace": trace}})
    metadata = {
        "bundle_schema": WORKFLOW_TRACE_BUNDLE_SCHEMA,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "workflow_id": workflow.get("workflow_id"),
        "run_id": run.get("run_id"),
        "run_type": run.get("run_type"),
        "task_type": task.get("task_type"),
        "trace_hash": summary.get("trace_hash"),
        "plan_hash": summary.get("plan_hash"),
        "status": summary.get("status"),
        "structured_manifest_hash": structured_manifest().get("manifest_hash"),
    }
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        bundle = ManifestedZip(archive, "workflow_trace_bundle", metadata)
        bundle.writestr("bundle_manifest.json", _json(metadata))
        bundle.writestr("workflow.json", _json(workflow))
        bundle.writestr("task.json", _json(task))
        bundle.writestr("plan.json", _json({"plan": plan}))
        bundle.writestr("trace.json", _json({"trace": trace}))
        bundle.writestr("agent_roles.json", _json({"runtime": workflow_runtime_status(), "agent_roles": AGENT_ROLES}))
        bundle.writestr("run_summary.json", _json(summary))
        bundle.writestr("provenance/rag_status.json", _json(rag_status()))
        bundle.writestr("provenance/structured_manifest.json", _json(structured_manifest()))
        bundle.write_artifact_manifest()
    return buffer.getvalue()


def verify_workflow_trace_bundle(bundle: bytes) -> dict[str, Any]:
    base = verify_artifact_bundle(bundle)
    errors = list(base.get("errors") or [])
    warnings = list(base.get("warnings") or [])
    semantic_errors: list[str] = []
    semantic_warnings: list[str] = []
    semantic_checks: dict[str, str] = {}
    if base.get("artifact_type") != "workflow_trace_bundle":
        semantic_errors.append("Artifact manifest artifact_type must be workflow_trace_bundle.")
        semantic_checks["artifact_type"] = "fail"
    else:
        semantic_checks["artifact_type"] = "pass"
    try:
        with ZipFile(BytesIO(bundle), "r") as archive:
            names = set(archive.namelist())
            missing = sorted(REQUIRED_WORKFLOW_TRACE_FILES - names)
            if missing:
                semantic_errors.append(f"Required workflow trace files are missing: {', '.join(missing)}.")
                semantic_checks["required_files"] = "fail"
                manifest = {}
                workflow = {}
                plan = {}
                trace = {}
                summary = {}
                structured = {}
            else:
                semantic_checks["required_files"] = "pass"
                manifest = _read_json(archive, "bundle_manifest.json")
                workflow = _read_json(archive, "workflow.json")
                plan = _read_json(archive, "plan.json")
                trace = _read_json(archive, "trace.json")
                summary = _read_json(archive, "run_summary.json")
                structured = _read_json(archive, "provenance/structured_manifest.json")
                _read_json(archive, "task.json")
                _read_json(archive, "agent_roles.json")
                _read_json(archive, "provenance/rag_status.json")
    except (BadZipFile, json.JSONDecodeError, UnicodeDecodeError) as exc:
        semantic_errors.append(f"Invalid workflow trace bundle: {exc}")
        manifest = {}
        workflow = {}
        plan = {}
        trace = {}
        summary = {}
        structured = {}

    if manifest.get("bundle_schema") != WORKFLOW_TRACE_BUNDLE_SCHEMA:
        semantic_errors.append("bundle_manifest.json bundle_schema is invalid.")
        semantic_checks["bundle_schema"] = "fail"
    else:
        semantic_checks["bundle_schema"] = "pass"

    trace_rows = trace.get("trace") or []
    if not isinstance(trace_rows, list) or not trace_rows:
        semantic_errors.append("trace.json must include at least one trace row.")
        semantic_checks["trace_rows"] = "fail"
    elif any(not all(key in row for key in ["name", "status", "timestamp", "detail"]) for row in trace_rows if isinstance(row, dict)):
        semantic_errors.append("trace.json contains malformed trace rows.")
        semantic_checks["trace_rows"] = "fail"
    else:
        semantic_checks["trace_rows"] = "pass"

    plan_rows = plan.get("plan") or []
    if not isinstance(plan_rows, list) or not plan_rows:
        semantic_errors.append("plan.json must include workflow plan rows.")
        semantic_checks["plan_rows"] = "fail"
    else:
        semantic_checks["plan_rows"] = "pass"

    if workflow.get("workflow_id") != manifest.get("workflow_id"):
        semantic_errors.append("workflow_id disagrees between workflow.json and bundle_manifest.json.")
        semantic_checks["workflow_id"] = "fail"
    else:
        semantic_checks["workflow_id"] = "pass"

    if summary.get("trace_hash") != _hash_payload(trace_rows) or summary.get("trace_hash") != manifest.get("trace_hash"):
        semantic_errors.append("Trace hash disagrees across workflow trace bundle files.")
        semantic_checks["trace_hash"] = "fail"
    else:
        semantic_checks["trace_hash"] = "pass"

    if summary.get("plan_hash") != _hash_payload(plan_rows) or summary.get("plan_hash") != manifest.get("plan_hash"):
        semantic_errors.append("Plan hash disagrees across workflow trace bundle files.")
        semantic_checks["plan_hash"] = "fail"
    else:
        semantic_checks["plan_hash"] = "pass"

    structured_hashes = {
        str(value)
        for value in [manifest.get("structured_manifest_hash"), structured.get("manifest_hash")]
        if value
    }
    if len(structured_hashes) > 1:
        semantic_errors.append("Structured manifest hash disagrees across workflow trace bundle files.")
        semantic_checks["structured_manifest_hash"] = "fail"
    else:
        semantic_checks["structured_manifest_hash"] = "pass" if structured_hashes else "warning"

    if summary.get("status") not in {"pass", "warning"}:
        semantic_warnings.append("Workflow trace summary status is not pass/warning.")
        semantic_checks["summary_status"] = "warning"
    else:
        semantic_checks["summary_status"] = "pass"

    semantic_status = "fail" if semantic_errors else "warning" if semantic_warnings else "pass"
    return {
        **base,
        "status": "fail" if errors or semantic_errors else "warning" if warnings or semantic_warnings else "pass",
        "errors": errors + semantic_errors,
        "warnings": warnings + semantic_warnings,
        "semantic_status": semantic_status,
        "semantic_errors": semantic_errors,
        "semantic_warnings": semantic_warnings,
        "semantic_checks": semantic_checks,
        "workflow_id": manifest.get("workflow_id"),
        "run_id": manifest.get("run_id"),
        "task_type": manifest.get("task_type"),
        "trace_hash": manifest.get("trace_hash"),
        "trace_step_count": len(trace_rows) if isinstance(trace_rows, list) else 0,
        "structured_manifest_hash": next(iter(structured_hashes), None),
    }


def _required_agents(task_type: str) -> list[str]:
    if task_type == "cds_optimize":
        return ["planner", "optimizer", "qc_writer"]
    if task_type == "gene_to_design":
        return ["planner", "transcript_resolver", "optimizer", "retriever", "qc_writer"]
    return []


def _trace_has_agent(agent: str, trace_names: list[str]) -> bool:
    aliases = {
        "planner": ["planner"],
        "transcript_resolver": ["transcript", "canonical_transcript_resolver"],
        "optimizer": ["optimizer", "synonymous_optimizer"],
        "retriever": ["retriever"],
        "qc_writer": ["qc_writer"],
    }
    needles = aliases.get(agent, [agent])
    return any(any(needle in trace_name for needle in needles) for trace_name in trace_names)


def _read_json(archive: ZipFile, name: str) -> dict[str, Any]:
    payload = json.loads(archive.read(name).decode("utf-8"))
    return payload if isinstance(payload, dict) else {}


def _hash_payload(payload: Any) -> str:
    return sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
