from __future__ import annotations

import argparse
import json
import sys
from hashlib import sha256
from pathlib import Path
from typing import Any


MATRIX_SCHEMA = "agentic-rag-portfolio-readiness-matrix-v1"
ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify a generated portfolio readiness matrix JSON artifact.")
    parser.add_argument("--path", type=Path, help="Path to portfolio_readiness_matrix.json. Reads stdin when omitted.")
    args = parser.parse_args()

    try:
        payload = load_payload(args.path)
    except Exception as exc:
        print(json.dumps({"status": "fail", "errors": [f"invalid matrix input: {exc}"]}, indent=2), file=sys.stderr)
        return 1

    errors = validate_matrix(payload)
    print(
        json.dumps(
            {
                "status": "pass" if not errors else "fail",
                "schema": payload.get("schema") if isinstance(payload, dict) else None,
                "matrix_hash": payload.get("matrix_hash") if isinstance(payload, dict) else None,
                "errors": errors,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if not errors else 1


def load_payload(path: Path | None) -> Any:
    if path is None:
        return json.loads(sys.stdin.read())
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-16", "utf-16-le", "utf-16-be"):
        try:
            return json.loads(raw.decode(encoding))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    return json.loads(raw.decode("utf-8"))


def validate_matrix(payload: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["matrix must be a JSON object"]
    if payload.get("schema") != MATRIX_SCHEMA:
        errors.append(f"schema must be {MATRIX_SCHEMA}")
    root = matrix_root(payload)
    requirements = payload.get("requirements") if isinstance(payload.get("requirements"), list) else []
    if not isinstance(payload.get("requirements"), list):
        errors.append("requirements must be a list")

    expected_summary = {
        "status": "fail" if any(req.get("status") == "fail" for req in requirements if isinstance(req, dict)) else "warning"
        if any(req.get("status") == "warning" for req in requirements if isinstance(req, dict))
        else "pass",
        "requirement_count": len(requirements),
        "pass_count": sum(1 for req in requirements if isinstance(req, dict) and req.get("status") == "pass"),
        "warning_count": sum(1 for req in requirements if isinstance(req, dict) and req.get("status") == "warning"),
        "fail_count": sum(1 for req in requirements if isinstance(req, dict) and req.get("status") == "fail"),
    }
    if payload.get("summary") != expected_summary:
        errors.append("summary does not match requirements")

    for requirement in requirements:
        if not isinstance(requirement, dict):
            errors.append("requirements contains a non-object item")
            continue
        errors.extend(validate_requirement(requirement, root=root))

    expected_matrix_hash = hash_payload({key: value for key, value in payload.items() if key != "matrix_hash"})
    if payload.get("matrix_hash") != expected_matrix_hash:
        errors.append("matrix_hash does not match matrix payload")
    return errors


def validate_requirement(requirement: dict[str, Any], *, root: Path) -> list[str]:
    errors: list[str] = []
    evidence = requirement.get("evidence") if isinstance(requirement.get("evidence"), list) else []
    if not isinstance(requirement.get("evidence"), list):
        errors.append(f"requirement {requirement.get('id', 'unknown')!r} evidence must be a list")
    if requirement.get("evidence_count") != len(evidence):
        errors.append(f"requirement {requirement.get('id', 'unknown')!r} evidence_count does not match evidence")
    passing_count = sum(1 for item in evidence if isinstance(item, dict) and item.get("status") == "pass")
    if requirement.get("passing_evidence_count") != passing_count:
        errors.append(f"requirement {requirement.get('id', 'unknown')!r} passing_evidence_count does not match evidence")
    expected_status = "fail" if any(isinstance(item, dict) and item.get("status") == "fail" for item in evidence) else "pass"
    if requirement.get("status") != expected_status:
        errors.append(f"requirement {requirement.get('id', 'unknown')!r} status does not match evidence")
    if requirement.get("evidence_hash") != hash_payload(evidence):
        errors.append(f"requirement {requirement.get('id', 'unknown')!r} evidence_hash does not match evidence")
    for item in evidence:
        if isinstance(item, dict):
            errors.extend(validate_evidence_item(item, root=root))
        else:
            errors.append(f"requirement {requirement.get('id', 'unknown')!r} evidence contains a non-object item")
    return errors


def validate_evidence_item(item: dict[str, Any], *, root: Path) -> list[str]:
    errors: list[str] = []
    kind = item.get("kind")
    if kind == "file":
        rel_path = item.get("path")
        if not isinstance(rel_path, str) or not rel_path:
            return ["file evidence path must be a non-empty string"]
        path = root / rel_path
        exists = path.exists()
        if item.get("exists") != exists:
            errors.append(f"file evidence {rel_path} exists flag does not match filesystem")
        if exists:
            actual_sha = sha256(path.read_bytes()).hexdigest()
            if item.get("sha256") != actual_sha:
                errors.append(f"file evidence {rel_path} sha256 does not match filesystem")
    elif kind == "glob":
        pattern = item.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            return ["glob evidence pattern must be a non-empty string"]
        count = len(sorted(root.glob(pattern)))
        if item.get("count") != count:
            errors.append(f"glob evidence {pattern} count does not match filesystem")
        if item.get("status") != ("pass" if count >= int(item.get("min_count") or 1) else "fail"):
            errors.append(f"glob evidence {pattern} status does not match count")
    else:
        errors.append(f"unknown evidence kind: {kind!r}")
    return errors


def matrix_root(payload: dict[str, Any]) -> Path:
    raw_root = payload.get("root")
    if isinstance(raw_root, str) and raw_root:
        return Path(raw_root)
    return ROOT


def hash_payload(payload: Any) -> str:
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
