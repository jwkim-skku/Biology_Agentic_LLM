from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.optimizer.nsga2 import OptimizationConfig
from app.services.design_service import score_cds
from app.services.report_service import generate_qc_report
from app.services.run_export_service import build_run_export_bundle
from app.services.validation_service import validate_cds
from app.services.workflow_service import run_cds_design_workflow
from app.services.export_manifest_service import verify_artifact_bundle


SNAPSHOT_PATH = Path(__file__).resolve().parents[1] / "tests" / "golden" / "api_shape_v1.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Check stable response shapes for core platform outputs.")
    parser.add_argument("--update", action="store_true", help="Rewrite the golden snapshot.")
    args = parser.parse_args()

    current = build_snapshot()
    if args.update:
        SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT_PATH.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps({"updated": str(SNAPSHOT_PATH), "cases": list(current)}, indent=2))
        return 0

    expected = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    failures = _diff(expected, current)
    print(json.dumps({"snapshot": str(SNAPSHOT_PATH), "cases": list(current), "failures": failures}, indent=2, sort_keys=True))
    return 1 if failures else 0


def build_snapshot() -> dict[str, Any]:
    cds = "ATGGCTGACGAGTTCGCCAAGGGTTACTAA"
    policy_cds = "ATGAATAAAGAATTCGTAAGTTAA"
    config = OptimizationConfig(population_size=16, generations=2, max_candidates=2, seed=101)
    design = run_cds_design_workflow(cds, {"gene": "DEMO", "species": "human", "modality": "AAV"}, config)
    report = design["qc_report"] if "qc_report" in design else generate_qc_report(design)
    design["qc_report"] = report
    design["trace"] = [{"name": "golden", "status": "ok", "timestamp": design["timestamp"], "detail": {}}]
    run = {
        "run_id": design["run_id"],
        "run_type": "golden_contract",
        "request": {"cds": cds},
        "design": design,
        "qc_report": report,
        "trace": design["trace"],
        "pipeline_version": design["pipeline_version"],
        "structured_manifest_hash": (design.get("provenance") or {}).get("structured_manifest_hash"),
        "recommended_candidate_id": (design.get("recommended_candidate") or {}).get("candidate_id"),
    }
    export_verification = verify_artifact_bundle(build_run_export_bundle(run))
    return {
        "score_response": _shape(score_cds(cds)),
        "validate_response": _shape(validate_cds(policy_cds)),
        "design_response": _shape(_redact_sequence_payload(design)),
        "qc_report": _shape(report),
        "run_export_verification": _shape(export_verification),
    }


def _shape(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _shape(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_shape(value[0])] if value else []
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if value is None:
        return "null"
    return "str"


def _redact_sequence_payload(payload: dict[str, Any]) -> dict[str, Any]:
    cloned = json.loads(json.dumps(payload))
    if "native" in cloned:
        cloned["native"].pop("cds", None)
        cloned["native"].pop("protein", None)
    for candidate in cloned.get("candidates", []):
        candidate.pop("cds", None)
        candidate.pop("protein", None)
    if cloned.get("recommended_candidate"):
        cloned["recommended_candidate"].pop("cds", None)
        cloned["recommended_candidate"].pop("protein", None)
    return cloned


def _diff(expected: Any, current: Any, path: str = "$") -> list[str]:
    if type(expected) is not type(current):
        return [f"{path}: type changed from {type(expected).__name__} to {type(current).__name__}"]
    if isinstance(expected, dict):
        failures: list[str] = []
        for key in sorted(set(expected) - set(current)):
            failures.append(f"{path}.{key}: missing")
        for key in sorted(set(current) - set(expected)):
            failures.append(f"{path}.{key}: unexpected")
        for key in sorted(set(expected) & set(current)):
            failures.extend(_diff(expected[key], current[key], f"{path}.{key}"))
        return failures
    if isinstance(expected, list):
        if len(expected) != len(current):
            return [f"{path}: list sample length changed from {len(expected)} to {len(current)}"]
        if not expected:
            return []
        return _diff(expected[0], current[0], f"{path}[0]")
    return [] if expected == current else [f"{path}: scalar type changed from {expected} to {current}"]


if __name__ == "__main__":
    raise SystemExit(main())
