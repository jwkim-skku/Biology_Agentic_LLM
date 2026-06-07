from __future__ import annotations

import re
import shutil
import subprocess
from typing import Any

from app.config import get_settings
from app.optimizer.codon_table import normalize_dna
from app.optimizer.scoring import secondary_structure_proxy


RNA_FOLDING_SCHEMA = "agentic-rag-rna-folding-v1"


def rna_folding_status() -> dict[str, Any]:
    settings = get_settings()
    executable_path = shutil.which(settings.rnafold_executable)
    requested = settings.rna_folding_backend
    rnafold_ready = requested == "rnafold" and bool(executable_path)
    active_backend = "rnafold" if rnafold_ready else "deterministic_proxy"
    fallback_active = requested == "rnafold" and not rnafold_ready
    status = "ready" if rnafold_ready else "fallback" if fallback_active else "proxy"
    return {
        "folding_schema": RNA_FOLDING_SCHEMA,
        "status": status,
        "requested_backend": requested,
        "active_backend": active_backend,
        "fallback_active": fallback_active,
        "executable": settings.rnafold_executable,
        "executable_path": executable_path,
        "timeout_seconds": settings.rnafold_timeout_seconds,
        "window_nt": settings.rnafold_window_nt,
        "validated_backend": rnafold_ready,
        "production_ready": rnafold_ready,
        "recommendation": _status_recommendation(status, settings.rnafold_executable),
    }


def evaluate_rna_folding(cds: str) -> dict[str, Any]:
    settings = get_settings()
    status = rna_folding_status()
    dna = normalize_dna(cds)
    window = dna[: settings.rnafold_window_nt]
    rna = window.replace("T", "U")
    proxy_score, proxy_mfe = secondary_structure_proxy(window, window_nt=settings.rnafold_window_nt)
    result: dict[str, Any] = {
        "folding_schema": RNA_FOLDING_SCHEMA,
        "status": "pass",
        "requested_backend": status["requested_backend"],
        "active_backend": status["active_backend"],
        "fallback_active": status["fallback_active"],
        "input_length_nt": len(dna),
        "evaluated_window_nt": len(window),
        "proxy": {
            "secondary_structure_proxy_score": proxy_score,
            "mfe_proxy_delta_g": proxy_mfe,
        },
        "rnafold": None,
        "warnings": [],
    }
    if len(rna) < 18:
        result["status"] = "warning"
        result["warnings"].append("Sequence is shorter than the configured RNA folding window minimum.")
        return result
    if status["active_backend"] != "rnafold":
        result["status"] = "warning"
        result["warnings"].append("Using deterministic secondary-structure proxy; configure RNA_FOLDING_BACKEND=rnafold for thermodynamic MFE evidence.")
        return result

    folded = _run_rnafold(rna, status["executable"], int(status["timeout_seconds"]))
    if folded["status"] != "pass":
        result["status"] = "warning"
        result["active_backend"] = "deterministic_proxy"
        result["fallback_active"] = True
        result["rnafold"] = folded
        result["warnings"].append(f"RNAfold failed; deterministic proxy retained: {folded.get('message')}")
        return result
    result["rnafold"] = folded
    result["thermodynamic_mfe_delta_g"] = folded["mfe_delta_g"]
    result["thermodynamic_structure"] = folded["structure"]
    result["thermodynamic_risk_score"] = _mfe_risk_score(float(folded["mfe_delta_g"]), len(rna))
    return result


def _run_rnafold(rna: str, executable: str, timeout_seconds: int) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            [executable, "--noPS"],
            input=f"{rna}\n",
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "fail", "message": str(exc)}
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or f"RNAfold exited with {completed.returncode}").strip()
        return {"status": "fail", "message": message}
    parsed = _parse_rnafold_output(completed.stdout)
    if not parsed:
        return {"status": "fail", "message": "RNAfold output did not contain a parseable dot-bracket/MFE line."}
    structure, mfe = parsed
    return {
        "status": "pass",
        "algorithm": "ViennaRNA RNAfold",
        "sequence_length_nt": len(rna),
        "structure": structure,
        "mfe_delta_g": mfe,
        "raw_output_sha256_supported": False,
    }


def _parse_rnafold_output(output: str) -> tuple[str, float] | None:
    for line in output.splitlines()[1:]:
        stripped = line.strip()
        if not stripped:
            continue
        match = re.search(r"^([().\[\]{}<>]+)\s+\(([-+]?\d+(?:\.\d+)?)\)", stripped)
        if match:
            return match.group(1), round(float(match.group(2)), 4)
    return None


def _mfe_risk_score(mfe_delta_g: float, length_nt: int) -> float:
    if length_nt <= 0 or mfe_delta_g >= 0:
        return 0.0
    return round(min(abs(mfe_delta_g) / max(length_nt, 1) / 0.12, 1.0), 4)


def _status_recommendation(status: str, executable: str) -> str:
    if status == "ready":
        return "RNAfold is configured and will be used for folding evidence endpoints."
    if status == "fallback":
        return f"RNA_FOLDING_BACKEND requests RNAfold, but {executable!r} is not available on PATH."
    return "Set RNA_FOLDING_BACKEND=rnafold and install ViennaRNA RNAfold for production thermodynamic folding evidence."
