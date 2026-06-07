from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import re
import urllib.error
import urllib.request
from functools import lru_cache
from typing import Any

from app.config import get_settings


RAG_EMBEDDING_SCHEMA = "agentic-rag-embedding-backend-v1"
HASH_BOW_MODEL = "hash-bow-v1"
DEFAULT_EMBEDDING_DIMENSIONS = 128


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
    warnings: list[str] = []
    if requested == "sentence_transformers" and not sentence_transformers_available:
        warnings.append("RAG_EMBEDDING_BACKEND=sentence_transformers requires the sentence-transformers package and a locally available model.")
    if requested == "openai" and not openai_configured:
        warnings.append("RAG_EMBEDDING_BACKEND=openai requires OPENAI_API_KEY.")
    if requested != active:
        warnings.append(f"RAG embedding backend fell back from {requested} to {active}.")
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
        },
        "production_ready": active in {"sentence_transformers", "openai"} and not fallback_active,
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
    return {
        "status": "pass",
        "embedding_model": settings.rag_embedding_model,
        "embedding": values,
    }


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
