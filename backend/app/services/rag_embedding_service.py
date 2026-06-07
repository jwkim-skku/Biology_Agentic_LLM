from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.config import get_settings


RAG_EMBEDDING_SCHEMA = "agentic-rag-embedding-backend-v1"
HASH_BOW_MODEL = "hash-bow-v1"
DEFAULT_EMBEDDING_DIMENSIONS = 128
OPENAI_CACHE_SCHEMA = "agentic-rag-openai-embedding-cache-v1"


def rag_embedding_status() -> dict[str, Any]:
    settings = get_settings()
    requested = settings.rag_embedding_backend
    sentence_transformers_available = importlib.util.find_spec("sentence_transformers") is not None
    openai_configured = bool(settings.openai_api_key)
    if requested == "sentence_transformers" and sentence_transformers_available:
        active = "sentence_transformers"
    elif requested == "openai" and openai_configured:
        active = "openai"
    else:
        active = "hash_bow"
    fallback_active = requested != active
    model = settings.rag_embedding_model if active in {"sentence_transformers", "openai"} else HASH_BOW_MODEL
    dimensions = settings.rag_embedding_dimensions if active in {"sentence_transformers", "openai"} else DEFAULT_EMBEDDING_DIMENSIONS
    openai_cache = _openai_cache_status(settings)
    openai_budget = _openai_budget_status(settings, openai_cache)
    warnings: list[str] = []
    if requested == "sentence_transformers" and not sentence_transformers_available:
        warnings.append("RAG_EMBEDDING_BACKEND=sentence_transformers requires the sentence-transformers package and a locally available model.")
    if requested == "openai" and not openai_configured:
        warnings.append("RAG_EMBEDDING_BACKEND=openai requires OPENAI_API_KEY.")
    if requested != active:
        warnings.append(f"RAG embedding backend fell back from {requested} to {active}.")
    if active == "openai" and not openai_budget["price_configured"]:
        warnings.append("RAG_EMBEDDING_BACKEND=openai should set OPENAI_EMBEDDING_PRICE_PER_1K_TOKENS for spend tracking.")
    if active == "openai" and not openai_budget["budget_configured"]:
        warnings.append("RAG_EMBEDDING_BACKEND=openai should set OPENAI_EMBEDDING_BUDGET_USD for spend guardrails.")
    if active == "openai" and openai_budget["budget_exceeded"]:
        warnings.append("OpenAI embedding estimated spend has reached or exceeded the configured budget.")
    production_ready = active == "sentence_transformers" and not fallback_active
    if active == "openai":
        production_ready = not fallback_active and openai_budget["price_configured"] and openai_budget["budget_configured"] and openai_budget["within_budget"]
    return {
        "embedding_schema": RAG_EMBEDDING_SCHEMA,
        "status": "warning" if warnings else "pass",
        "requested_backend": requested,
        "active_backend": active,
        "fallback_active": fallback_active,
        "embedding_model": model,
        "embedding_dimensions": dimensions,
        "sentence_transformers": {
            "package_available": sentence_transformers_available,
            "configured_model": settings.rag_embedding_model,
            "configured_dimensions": settings.rag_embedding_dimensions,
            "model_load_policy": "local_files_only",
        },
        "openai": {
            "api_key_configured": openai_configured,
            "base_url": settings.openai_embedding_base_url,
            "configured_model": settings.rag_embedding_model,
            "configured_dimensions": settings.rag_embedding_dimensions,
            "budget": openai_budget,
            "cache": openai_cache,
        },
        "production_ready": production_ready,
        "warnings": warnings,
        "recommendation": _recommendation(active, fallback_active),
    }


def embed_text(text: str, aliases: dict[str, list[str]] | None = None) -> dict[str, Any]:
    status = rag_embedding_status()
    if status["active_backend"] == "sentence_transformers":
        embedded = _sentence_transformer_embedding(text)
        if embedded.get("status") == "pass":
            return {
                **status,
                "status": "pass",
                "embedding_model": embedded["embedding_model"],
                "embedding_dimensions": len(embedded["embedding"]),
                "embedding": embedded["embedding"],
                "cache_status": embedded.get("cache_status", "unknown"),
                "warnings": status["warnings"],
            }
        warnings = list(status["warnings"]) + [embedded.get("error") or "sentence-transformers embedding failed; falling back to hash-bow-v1."]
        return {
            **_hash_bow_response(text, aliases),
            "requested_backend": status["requested_backend"],
            "fallback_active": True,
            "warnings": warnings,
            "status": "warning",
        }
    if status["active_backend"] == "openai":
        embedded = _openai_embedding(text)
        if embedded.get("status") == "pass":
            return {
                **status,
                "status": "pass",
                "embedding_model": embedded["embedding_model"],
                "embedding_dimensions": len(embedded["embedding"]),
                "embedding": embedded["embedding"],
                "cache_status": embedded.get("cache_status", "unknown"),
                "warnings": status["warnings"],
            }
        warnings = list(status["warnings"]) + [embedded.get("error") or "OpenAI embedding failed; falling back to hash-bow-v1."]
        return {
            **_hash_bow_response(text, aliases),
            "requested_backend": status["requested_backend"],
            "fallback_active": True,
            "warnings": warnings,
            "status": "warning",
        }
    return _hash_bow_response(text, aliases)


def _hash_bow_response(text: str, aliases: dict[str, list[str]] | None) -> dict[str, Any]:
    embedding = _hash_bow_embedding(text, aliases=aliases, dimensions=DEFAULT_EMBEDDING_DIMENSIONS)
    return {
        "embedding_schema": RAG_EMBEDDING_SCHEMA,
        "status": "pass",
        "requested_backend": get_settings().rag_embedding_backend,
        "active_backend": "hash_bow",
        "fallback_active": get_settings().rag_embedding_backend != "hash_bow",
        "embedding_model": HASH_BOW_MODEL,
        "embedding_dimensions": len(embedding),
        "embedding": embedding,
        "warnings": [],
    }


def _hash_bow_embedding(text: str, *, aliases: dict[str, list[str]] | None, dimensions: int) -> list[float]:
    vector = [0.0] * dimensions
    for token in _expanded_token_list(text, aliases or {}):
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % dimensions
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[index] += sign
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [round(value / norm, 8) for value in vector]


def _sentence_transformer_embedding(text: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        model = _sentence_transformer_model(settings.rag_embedding_model)
        vector = model.encode([text], normalize_embeddings=True, show_progress_bar=False)[0]
    except Exception as exc:  # pragma: no cover - depends on optional model runtime.
        return {"status": "fail", "error": str(exc)}
    values = [round(float(value), 8) for value in vector]
    configured_dimensions = settings.rag_embedding_dimensions
    if configured_dimensions and len(values) != configured_dimensions:
        values = _resize_embedding(values, configured_dimensions)
    return {
        "status": "pass",
        "embedding_model": settings.rag_embedding_model,
        "embedding": values,
    }


def _openai_embedding(text: str) -> dict[str, Any]:
    settings = get_settings()
    cache_key = _openai_cache_key(text, model=settings.rag_embedding_model, dimensions=settings.rag_embedding_dimensions)
    cached = _read_openai_cache_entry(cache_key)
    if cached is not None:
        return {
            "status": "pass",
            "embedding_model": settings.rag_embedding_model,
            "embedding": cached,
            "cache_status": "hit",
        }
    budget_check = _openai_request_budget_check(text, settings=settings)
    if budget_check["status"] == "fail":
        return {"status": "fail", "error": budget_check["message"], "budget_check": budget_check}
    payload: dict[str, Any] = {
        "model": settings.rag_embedding_model,
        "input": text,
    }
    if settings.rag_embedding_dimensions:
        payload["dimensions"] = settings.rag_embedding_dimensions
    request = urllib.request.Request(
        f"{settings.openai_embedding_base_url}/embeddings",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {settings.openai_api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as exc:  # pragma: no cover - depends on external API.
        return {"status": "fail", "error": str(exc)}
    try:
        values = [round(float(value), 8) for value in body["data"][0]["embedding"]]
    except (KeyError, IndexError, TypeError, ValueError) as exc:  # pragma: no cover - defensive parsing for external API.
        return {"status": "fail", "error": f"Invalid OpenAI embedding response: {exc}"}
    if settings.rag_embedding_dimensions and len(values) != settings.rag_embedding_dimensions:
        values = _resize_embedding(values, settings.rag_embedding_dimensions)
    _write_openai_cache_entry(cache_key, values, text=text, model=settings.rag_embedding_model, dimensions=settings.rag_embedding_dimensions)
    return {
        "status": "pass",
        "embedding_model": settings.rag_embedding_model,
        "embedding": values,
        "cache_status": "miss",
    }


def _openai_cache_status(settings: Any | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    path = _openai_cache_path(settings)
    entries = 0
    invalid_entries = 0
    missing_usage_entries = 0
    estimated_input_tokens = 0
    estimated_cost_usd = 0.0
    models: dict[str, int] = {}
    dimensions: dict[str, int] = {}
    entry_keys_hash: str | None = None
    file_sha256: str | None = None
    if path.exists():
        try:
            raw = path.read_bytes()
            file_sha256 = hashlib.sha256(raw).hexdigest()
            payload = json.loads(raw.decode("utf-8"))
            cache_entries = payload.get("entries") if isinstance(payload, dict) else {}
            if isinstance(cache_entries, dict):
                entries = len(cache_entries)
                entry_keys_hash = _hash_json(sorted(str(key) for key in cache_entries))
                for entry in cache_entries.values():
                    if not isinstance(entry, dict):
                        invalid_entries += 1
                        continue
                    model = str(entry.get("model") or "unknown")
                    models[model] = models.get(model, 0) + 1
                    dimension = entry.get("dimensions")
                    dimension_key = str(dimension if dimension is not None else "unknown")
                    dimensions[dimension_key] = dimensions.get(dimension_key, 0) + 1
                    if not isinstance(entry.get("embedding"), list):
                        invalid_entries += 1
                    tokens = entry.get("estimated_input_tokens")
                    if isinstance(tokens, int) and tokens >= 0:
                        estimated_input_tokens += tokens
                    else:
                        missing_usage_entries += 1
                    cost = entry.get("estimated_cost_usd")
                    if isinstance(cost, int | float) and cost >= 0:
                        estimated_cost_usd += float(cost)
        except json.JSONDecodeError:
            entries = 0
            invalid_entries = 1
    return {
        "cache_schema": OPENAI_CACHE_SCHEMA,
        "path": str(path),
        "exists": path.exists(),
        "file_sha256": file_sha256,
        "entries": entries,
        "entry_keys_hash": entry_keys_hash,
        "models": models,
        "dimensions": dimensions,
        "estimated_input_tokens": estimated_input_tokens,
        "estimated_cost_usd": round(estimated_cost_usd, 8),
        "missing_usage_entries": missing_usage_entries,
        "invalid_entries": invalid_entries,
        "enabled": True,
    }


def _read_openai_cache_entry(cache_key: str) -> list[float] | None:
    path = _openai_cache_path()
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    entry = (payload.get("entries") or {}).get(cache_key) if isinstance(payload, dict) else None
    values = entry.get("embedding") if isinstance(entry, dict) else None
    if not isinstance(values, list):
        return None
    try:
        return [round(float(value), 8) for value in values]
    except (TypeError, ValueError):
        return None


def _write_openai_cache_entry(cache_key: str, embedding: list[float], *, text: str, model: str, dimensions: int) -> None:
    path = _openai_cache_path()
    settings = get_settings()
    estimated_input_tokens = _estimate_openai_input_tokens(text)
    estimated_cost_usd = _estimate_openai_embedding_cost(estimated_input_tokens, settings.openai_embedding_price_per_1k_tokens)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {"cache_schema": OPENAI_CACHE_SCHEMA, "entries": {}}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(existing, dict) and isinstance(existing.get("entries"), dict):
                payload = existing
        except json.JSONDecodeError:
            payload = {"cache_schema": OPENAI_CACHE_SCHEMA, "entries": {}}
    payload["cache_schema"] = OPENAI_CACHE_SCHEMA
    payload.setdefault("entries", {})[cache_key] = {
        "model": model,
        "dimensions": dimensions,
        "input_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "estimated_input_tokens": estimated_input_tokens,
        "estimated_cost_usd": estimated_cost_usd,
        "price_per_1k_tokens_usd": settings.openai_embedding_price_per_1k_tokens,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "embedding": embedding,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _openai_cache_key(text: str, *, model: str, dimensions: int) -> str:
    payload = {"model": model, "dimensions": dimensions, "text": text}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _hash_json(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _openai_budget_status(settings: Any, cache: dict[str, Any]) -> dict[str, Any]:
    estimated_spend = float(cache.get("estimated_cost_usd") or 0.0)
    budget = float(settings.openai_embedding_budget_usd or 0.0)
    remaining = budget - estimated_spend if budget else None
    budget_exceeded = remaining is not None and remaining <= 0
    return {
        "price_per_1k_tokens_usd": settings.openai_embedding_price_per_1k_tokens,
        "budget_usd": budget,
        "estimated_spend_usd": round(estimated_spend, 8),
        "estimated_remaining_usd": round(remaining, 8) if remaining is not None else None,
        "budget_configured": budget > 0,
        "price_configured": settings.openai_embedding_price_per_1k_tokens > 0,
        "within_budget": remaining is None or remaining > 0,
        "budget_exceeded": budget_exceeded,
        "next_request_policy": "block requests when estimated cached spend plus estimated request cost exceeds OPENAI_EMBEDDING_BUDGET_USD",
        "usage_estimate_policy": "ceil(len(input_text)/4) tokens; operator-supplied embedding price",
        "missing_usage_entries": cache.get("missing_usage_entries", 0),
    }


def _openai_request_budget_check(text: str, *, settings: Any) -> dict[str, Any]:
    cache = _openai_cache_status(settings)
    budget = _openai_budget_status(settings, cache)
    tokens = _estimate_openai_input_tokens(text)
    request_cost = _estimate_openai_embedding_cost(tokens, settings.openai_embedding_price_per_1k_tokens)
    projected_spend = round(float(budget["estimated_spend_usd"]) + request_cost, 8)
    budget_usd = float(budget["budget_usd"] or 0.0)
    allowed = not budget["budget_configured"] or projected_spend <= budget_usd
    message = (
        "OpenAI embedding request is within the configured budget."
        if allowed
        else f"OpenAI embedding request would exceed configured budget ${budget_usd:.4f}."
    )
    return {
        "status": "pass" if allowed else "fail",
        "estimated_input_tokens": tokens,
        "estimated_request_cost_usd": request_cost,
        "estimated_spend_usd": budget["estimated_spend_usd"],
        "projected_spend_usd": projected_spend,
        "budget_usd": budget_usd,
        "message": message,
    }


def _estimate_openai_input_tokens(text: str) -> int:
    return max(1, math.ceil(len(text.encode("utf-8")) / 4))


def _estimate_openai_embedding_cost(tokens: int, price_per_1k_tokens: float) -> float:
    return round((max(0, tokens) / 1000.0) * max(0.0, price_per_1k_tokens), 8)


def _openai_cache_path(settings: Any | None = None) -> Path:
    settings = settings or get_settings()
    return settings.data_dir / "runtime" / "openai_embedding_cache.json"


@lru_cache(maxsize=2)
def _sentence_transformer_model(model_name: str) -> Any:
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(model_name, local_files_only=True)


def _resize_embedding(values: list[float], dimensions: int) -> list[float]:
    if len(values) == dimensions:
        return values
    if len(values) > dimensions:
        resized = values[:dimensions]
    else:
        resized = values + [0.0] * (dimensions - len(values))
    norm = math.sqrt(sum(value * value for value in resized)) or 1.0
    return [round(value / norm, 8) for value in resized]


def _expanded_token_list(text: str, aliases: dict[str, list[str]]) -> list[str]:
    tokens = _token_list(text)
    expanded: list[str] = []
    for token in tokens:
        expanded.append(token)
        expanded.extend(aliases.get(token, []))
    return expanded


def _token_list(text: str) -> list[str]:
    normalized = text.lower().replace("5′", "5prime").replace("5'", "5prime")
    return [token for token in re.split(r"[^a-zA-Z0-9]+", normalized) if len(token) >= 3]


def _recommendation(active: str, fallback_active: bool) -> str:
    if active == "sentence_transformers" and not fallback_active:
        return "Biomedical embedding backend is active; keep the model pinned and covered by RAG regression before promotion."
    if active == "openai" and not fallback_active:
        return "OpenAI embedding backend is active; keep model, dimensions, and RAG regression evidence pinned before promotion."
    return "Configure RAG_EMBEDDING_BACKEND=sentence_transformers with a locally pinned biomedical model or RAG_EMBEDDING_BACKEND=openai with OPENAI_API_KEY before production retrieval promotion."
