from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.artifact_archive_service import archive_artifact_bundle
from app.services.structured_data_service import (
    import_structured_records,
    preview_structured_import,
    structured_manifest,
    structured_status,
    validate_structured_records,
)
from app.services.structured_import_audit_service import (
    build_structured_import_audit_bundle,
    verify_structured_import_audit_bundle,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Preview or apply structured GTEx/Allen/CUSTOM data imports.")
    parser.add_argument("--output-json", type=Path, help="Optional path to write the command result JSON.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    add_output_arg(subparsers.add_parser("status", help="Print loaded structured data status."))
    add_output_arg(subparsers.add_parser("manifest", help="Print structured data manifest and provenance."))
    add_output_arg(subparsers.add_parser("validate", help="Validate loaded structured records."))

    preview_parser = subparsers.add_parser("preview", help="Preview an import without mutating structured data.")
    add_output_arg(preview_parser)
    preview_parser.add_argument("source", type=Path, help="Source .json or .csv structured file.")

    apply_parser = subparsers.add_parser("apply", help="Import a structured file and write an audit bundle.")
    add_output_arg(apply_parser)
    apply_parser.add_argument("source", type=Path, help="Source .json or .csv structured file.")
    apply_parser.add_argument(
        "--allow-validation-errors",
        action="store_true",
        help="Apply even when the projected merged structured dataset has validation errors.",
    )
    apply_parser.add_argument("--audit-zip", type=Path, help="Optional path for the structured import audit ZIP.")
    apply_parser.add_argument(
        "--archive",
        action="store_true",
        help="Store the generated audit ZIP in the immutable artifact archive and ledger.",
    )
    args = parser.parse_args()

    try:
        result, exit_code = run(args)
    except Exception as exc:  # pragma: no cover - operator-facing CLI guard
        result = {"status": "fail", "error": f"{type(exc).__name__}: {exc}"}
        exit_code = 1

    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(_json(result) + "\n", encoding="utf-8")
    print(_json(result))
    return exit_code


def add_output_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--output-json", type=Path, help=argparse.SUPPRESS)


def run(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    if args.command == "status":
        return {"status": "pass", "structured_status": structured_status()}, 0
    if args.command == "manifest":
        manifest = structured_manifest()
        return {"status": _validation_status(manifest.get("validation") or {}), "structured_manifest": manifest}, 0
    if args.command == "validate":
        validation = validate_structured_records()
        return {"status": _validation_status(validation), "validation": validation}, 1 if validation.get("error_count") else 0
    if args.command == "preview":
        preview = preview_structured_import(args.source)
        return {"status": preview["status"], "preview": preview}, 1 if preview["status"] == "fail" else 0
    if args.command == "apply":
        return apply_import(args)
    raise ValueError(f"Unsupported command: {args.command}")


def apply_import(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    source = Path(args.source)
    preview = preview_structured_import(source)
    projected_errors = int(((preview.get("validation") or {}).get("projected") or {}).get("error_count") or 0)
    if projected_errors and not args.allow_validation_errors:
        return {
            "status": "fail",
            "message": "Projected structured dataset has validation errors; rerun with --allow-validation-errors to force import.",
            "preview": preview,
        }, 1

    import_result = import_structured_records(source)
    request_payload = {
        "source_path": str(source),
        "rebuild_index": False,
        "invoked_by": "backend/scripts/structured_import.py",
    }
    bundle = build_structured_import_audit_bundle(import_result, request_payload)
    verification = verify_structured_import_audit_bundle(bundle)

    audit_zip_path = args.audit_zip or default_audit_zip_path(source)
    audit_zip_path.parent.mkdir(parents=True, exist_ok=True)
    audit_zip_path.write_bytes(bundle)

    archive_result = None
    imported_path = Path(str(import_result.get("imported_path") or source.name))
    if args.archive:
        archive_result = archive_artifact_bundle(
            bundle,
            action="structured_import_audit_cli",
            resource_type="structured_import",
            resource_id=imported_path.name,
            filename=audit_zip_path.name,
            metadata={
                "source_path": str(source),
                "structured_manifest_hash": (import_result.get("status") or {}).get("manifest_hash"),
                "validation_errors": ((import_result.get("status") or {}).get("validation") or {}).get("errors"),
            },
        )

    status = "fail" if verification.get("status") == "fail" else "pass"
    return {
        "status": status,
        "preview": preview,
        "import_result": import_result,
        "audit_zip": str(audit_zip_path),
        "audit_bundle_verification": verification,
        "archive": archive_result,
    }, 1 if status == "fail" else 0


def default_audit_zip_path(source: Path) -> Path:
    return BACKEND_ROOT / "app" / "data" / "runtime" / "structured_import_audits" / f"{source.stem}_import_audit.zip"


def _validation_status(validation: dict[str, Any]) -> str:
    if validation.get("error_count"):
        return "fail"
    if validation.get("warning_count"):
        return "warning"
    return "pass"


def _json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


if __name__ == "__main__":
    raise SystemExit(main())
