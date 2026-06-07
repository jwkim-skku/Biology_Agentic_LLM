from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.data_refresh_service import data_catalog, refresh_log, refresh_reference_data, validate_refresh_plan
from app.services.data_release_lock_service import verify_data_release_lock, write_data_release_lock


DEFAULT_OUTPUT_DIR = BACKEND_ROOT / "app" / "data" / "runtime" / "data_refresh_runs"


def main() -> int:
    parser = argparse.ArgumentParser(description="Plan or run release-pinned GTEx/Allen reference data refreshes.")
    parser.add_argument("--output-json", type=Path, help="Optional path to write the command result JSON.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    add_output_arg(subparsers.add_parser("catalog", help="Print refreshable source catalog and current data status."))
    log_parser = subparsers.add_parser("log", help="Print recent reference refresh log entries.")
    add_output_arg(log_parser)
    log_parser.add_argument("--limit", type=int, default=20, help="Number of recent entries to show.")

    plan_parser = subparsers.add_parser("plan", help="Plan a refresh without network calls or data mutation.")
    add_refresh_args(plan_parser)
    add_output_arg(plan_parser)

    validate_parser = subparsers.add_parser("validate", help="Validate refresh inputs, current data coverage, and release-lock state.")
    add_refresh_args(validate_parser)
    add_output_arg(validate_parser)

    apply_parser = subparsers.add_parser("apply", help="Run live GTEx/Allen imports and optionally update the release lock.")
    add_refresh_args(apply_parser)
    add_output_arg(apply_parser)
    apply_parser.add_argument(
        "--write-release-lock",
        action="store_true",
        help="Write the data release lock after a fully successful refresh.",
    )
    apply_parser.add_argument(
        "--allow-partial-lock",
        action="store_true",
        help="Allow release-lock write even when some refresh operations failed.",
    )
    apply_parser.add_argument(
        "--evidence-json",
        type=Path,
        help="Optional refresh evidence JSON path. Defaults to app/data/runtime/data_refresh_runs/<hash>.json.",
    )

    args: argparse.Namespace | None = None
    try:
        args = parser.parse_args()
        result, exit_code = run(args)
    except Exception as exc:  # pragma: no cover - operator-facing CLI guard
        result = {"status": "fail", "error": f"{type(exc).__name__}: {exc}"}
        exit_code = 1

    output_path = getattr(args, "output_json", None) if args else None
    if output_path:
        write_json(output_path, result)
    print(_json(result))
    return exit_code


def run(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    if args.command == "catalog":
        return {"status": "pass", "catalog": data_catalog(), "release_lock": verify_data_release_lock()}, 0
    if args.command == "log":
        return {"status": "pass", "refresh_log": refresh_log(limit=args.limit)}, 0
    if args.command == "plan":
        result = refresh_reference_data(**refresh_kwargs(args), dry_run=True, rebuild_index_after=False)
        return {"status": "pass", "plan": result, "release_lock": verify_data_release_lock()}, 0
    if args.command == "validate":
        validation = validate_refresh_plan(**refresh_kwargs(args))
        return {"status": validation["status"], "validation": validation}, 1 if validation["status"] == "fail" else 0
    if args.command == "apply":
        return apply_refresh(args)
    raise ValueError(f"Unsupported command: {args.command}")


def apply_refresh(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    refresh = refresh_reference_data(**refresh_kwargs(args), dry_run=False, rebuild_index_after=not args.no_rebuild_index)
    failed = int((refresh.get("summary") or {}).get("failed") or 0)
    lock_result = verify_data_release_lock()
    lock_written = False
    if args.write_release_lock:
        if failed and not args.allow_partial_lock:
            lock_result = {
                "status": "not_written",
                "reason": "refresh had failed operations; rerun with --allow-partial-lock to pin a partial refresh",
                "current": verify_data_release_lock(),
            }
        else:
            lock_result = write_data_release_lock()
            lock_written = True

    status = "fail" if failed else "pass"
    result = {
        "status": status,
        "refresh": refresh,
        "release_lock": lock_result,
        "release_lock_written": lock_written,
    }
    evidence_path = args.evidence_json or default_evidence_path(refresh)
    write_json(evidence_path, result)
    result["evidence_json"] = str(evidence_path)
    return result, 1 if failed else 0


def add_refresh_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--genes", nargs="*", default=None, help="Gene symbols for GTEx median expression import.")
    parser.add_argument("--regions", nargs="*", default=None, help="Brain regions to request from GTEx.")
    parser.add_argument("--allen-terms", nargs="*", default=None, help="Allen taxonomy query terms.")
    parser.add_argument("--dataset-id", default="gtex_v8", help="GTEx dataset id.")
    parser.add_argument("--max-allen-records", type=int, default=250, help="Maximum Allen taxonomy records to import.")
    parser.add_argument("--no-gtex", action="store_true", help="Disable GTEx import operations.")
    parser.add_argument("--no-allen", action="store_true", help="Disable Allen taxonomy import operations.")
    parser.add_argument("--no-rebuild-index", action="store_true", help="Skip RAG index rebuild after successful live imports.")


def add_output_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--output-json", type=Path, help=argparse.SUPPRESS)


def refresh_kwargs(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "genes": args.genes,
        "brain_regions": args.regions,
        "include_gtex": not args.no_gtex,
        "include_allen": not args.no_allen,
        "allen_query_terms": args.allen_terms,
        "max_allen_records": args.max_allen_records,
        "dataset_id": args.dataset_id,
    }


def default_evidence_path(refresh: dict[str, Any]) -> Path:
    completed_at = str(refresh.get("completed_at") or "refresh").replace(":", "").replace("+", "Z")
    manifest_hash = str(refresh.get("manifest_hash") or "planned")
    return DEFAULT_OUTPUT_DIR / f"data_refresh_{completed_at}_{manifest_hash}.json"


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json(payload) + "\n", encoding="utf-8")


def _json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


if __name__ == "__main__":
    raise SystemExit(main())
