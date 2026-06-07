from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from app.optimizer.nsga2 import OptimizationConfig
from app.services.design_service import optimize_design
from app.services.evidence_service import build_design_evidence
from app.services.ensembl_client import EnsemblClient
from app.services.gene_service import fetch_canonical_cds
from app.services.report_service import generate_qc_report
from app.services.run_store import build_trace_step
from app.services.validation_service import qc_gate_for_design
from app.services.workflow_trace_bundle_service import workflow_trace_summary_from_design


@dataclass
class WorkflowState:
    workflow_id: str
    task: dict[str, Any]
    plan: list[dict[str, Any]]
    trace: list[dict[str, Any]] = field(default_factory=list)
    artifacts: dict[str, Any] = field(default_factory=dict)

    def step(self, name: str, status: str, detail: dict[str, Any] | None = None) -> None:
        self.trace.append(build_trace_step(name, status, detail or {}))


def plan_gene_design_task(request_payload: dict[str, Any]) -> dict[str, Any]:
    gene = request_payload.get("gene")
    target = request_payload.get("target", {})
    return {
        "task_type": "gene_to_design",
        "gene": gene,
        "species": request_payload.get("species", "human"),
        "target": target,
        "required_tools": [
            "canonical_transcript_resolver",
            "structured_context_retriever",
            "hybrid_rag_retriever",
            "synonymous_optimizer",
            "qc_writer",
        ],
        "hard_constraints": [
            "preserve protein sequence",
            "avoid configured forbidden motifs",
            "respect configured modality payload budget",
        ],
        "soft_objectives": [
            "increase or preserve composite quality",
            "balance CAI with target-context codon/tRNA priors",
            "avoid CpG and rare low-availability codon clusters",
        ],
        "evidence_policy": "citation-backed evidence and structured coverage must be surfaced in QC output",
    }


def run_gene_design_workflow(
    request_payload: dict[str, Any],
    optimization_config: OptimizationConfig,
    client: EnsemblClient | None = None,
) -> dict[str, Any]:
    task = plan_gene_design_task(request_payload)
    state = WorkflowState(
        workflow_id=f"workflow:{task['species']}:{task['gene']}",
        task=task,
        plan=_workflow_plan(),
    )
    state.step("planner_completed", "ok", {"task_type": task["task_type"], "required_tools": task["required_tools"]})

    cds_payload = fetch_canonical_cds(task["gene"], task["species"], client=client)
    state.artifacts["source_cds"] = {
        "gene": cds_payload["gene"],
        "selected_transcript": cds_payload["selected_transcript"],
        "cds_length_nt": cds_payload["cds_length_nt"],
        "protein_length_aa": cds_payload["protein_length_aa"],
        "provenance": cds_payload["provenance"],
    }
    state.step(
        "canonical_transcript_resolver",
        "ok",
        {
            "transcript_id": cds_payload["selected_transcript"]["id"],
            "selection_reason": cds_payload["selected_transcript"]["selection_reason"],
            "cds_length_nt": cds_payload["cds_length_nt"],
        },
    )

    target = dict(task["target"] or {})
    target["gene"] = target.get("gene") or cds_payload["gene"]["symbol"]
    target["species"] = task["species"]

    design = optimize_design(cds_payload["cds"], optimization_config, target, evidence_used=True)
    design["source_cds"] = state.artifacts["source_cds"]
    design["provenance"]["input_source"] = "ensembl_gene_symbol"
    design["provenance"]["external_databases"] = ["Ensembl REST"]
    state.step(
        "synonymous_optimizer",
        "ok",
        {
            "algorithm": design["optimization_config"].get("algorithm", "seeded_nsga2"),
            "candidates": len(design["candidates"]),
            "recommended": (design.get("recommended_candidate") or {}).get("candidate_id"),
        },
    )

    design["evidence"] = build_design_evidence(target, design["source_cds"])
    design["provenance"]["external_databases"].extend(sorted({record["source"] for record in design["evidence"]["records"]}))
    state.step(
        "retriever_completed",
        "ok",
        {
            "records": len(design["evidence"]["records"]),
            "coverage": design["evidence"]["coverage"],
            "structured_records": len((design["evidence"].get("structured_context") or {}).get("records", [])),
        },
    )

    design["qc_gate"] = qc_gate_for_design(design)
    design["qc_report"] = generate_qc_report(design)
    state.step(
        "qc_writer_completed",
        "ok",
        {
            "supported_rules": len(design["qc_report"]["evidence_summary"]["supported_rules"]),
            "uncertain_rules": len(design["qc_report"]["evidence_summary"]["uncertain_rules"]),
            "warnings": len(design["qc_report"].get("warnings", [])),
        },
    )

    design["workflow"] = {
        "workflow_id": state.workflow_id,
        "task": state.task,
        "plan": state.plan,
    }
    design["trace"] = state.trace
    return design


def run_cds_design_workflow(
    cds: str,
    target: dict[str, Any],
    optimization_config: OptimizationConfig,
    *,
    evidence_used: bool = False,
) -> dict[str, Any]:
    task = {
        "task_type": "cds_optimize",
        "target": target,
        "required_tools": ["synonymous_optimizer", "qc_writer"],
        "hard_constraints": ["preserve protein sequence", "avoid configured forbidden motifs"],
        "soft_objectives": ["balance CAI, GC, CpG, tissue codon priors, and rare-codon clusters"],
    }
    state = WorkflowState("workflow:cds_optimize", task, _workflow_plan(cds_only=True))
    state.step("planner_completed", "ok", {"task_type": task["task_type"], "required_tools": task["required_tools"]})
    design = optimize_design(cds, optimization_config, target, evidence_used=evidence_used)
    state.step(
        "synonymous_optimizer",
        "ok",
        {
            "candidates": len(design["candidates"]),
            "recommended": (design.get("recommended_candidate") or {}).get("candidate_id"),
        },
    )
    design["qc_gate"] = qc_gate_for_design(design)
    design["qc_report"] = generate_qc_report(design)
    state.step("qc_writer_completed", "ok", {"has_report": True, "warnings": len(design.get("warnings", []))})
    design["workflow"] = {"workflow_id": state.workflow_id, "task": state.task, "plan": state.plan}
    design["trace"] = state.trace
    return design


def _workflow_plan(cds_only: bool = False) -> list[dict[str, Any]]:
    if cds_only:
        steps = [
            ("planner", "Normalize CDS optimization request and constraints."),
            ("optimizer", "Generate protein-preserving synonymous Pareto candidates."),
            ("qc_writer", "Synthesize score table, warnings, provenance, and export-ready report."),
        ]
    else:
        steps = [
            ("planner", "Normalize gene therapy design request into explicit tools and constraints."),
            ("transcript_resolver", "Fetch canonical CDS with MANE-aware transcript priority."),
            ("optimizer", "Generate protein-preserving synonymous Pareto candidates."),
            ("retriever", "Retrieve RAG evidence and structured GTEx/Allen/CUSTOM/tRNA context."),
            ("qc_writer", "Synthesize evidence, scores, warnings, provenance, and open questions."),
        ]
    return [{"order": idx + 1, "agent": name, "objective": objective} for idx, (name, objective) in enumerate(steps)]


def workflow_summary(design: dict[str, Any]) -> dict[str, Any]:
    workflow = design.get("workflow", {})
    return {
        "workflow_summary_schema": "agentic-rag-workflow-summary-v1",
        "workflow": workflow,
        "trace": design.get("trace", []),
        "trace_summary": workflow_trace_summary_from_design(design),
        "run_id": design.get("run_id"),
        "recommended_candidate": (design.get("recommended_candidate") or {}).get("candidate_id"),
    }
