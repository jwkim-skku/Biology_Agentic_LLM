from __future__ import annotations

from time import perf_counter

from fastapi import Request
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import router
from app.config import get_settings
from app.services.metrics_service import record_request
from app.security import (
    InMemoryRateLimiter,
    extract_api_key,
    has_required_role,
    is_public_path,
    request_id_from_headers,
    required_role_for_request,
    roles_for_api_key,
)


settings = get_settings()
rate_limiter = InMemoryRateLimiter()


app = FastAPI(
    title="Agentic RAG Codon Optimization MVP",
    version="0.1.0",
    description="MVP backend for CDS scoring and synonymous codon optimization.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def operational_controls(request: Request, call_next):
    request_id = request_id_from_headers(request.headers)
    request.state.request_id = request_id
    request.state.auth_roles = ()
    request.state.required_role = None
    started = perf_counter()

    if request.method != "OPTIONS":
        api_key = extract_api_key(request)
        if settings.auth_enabled and not is_public_path(request.url.path) and api_key not in settings.api_keys:
            response = _json_error(401, "Missing or invalid API key.", request_id)
            _record_response(request, response.status_code, started)
            return response
        roles = roles_for_api_key(api_key, settings)
        request.state.auth_roles = roles
        request.state.required_role = required_role_for_request(request.method, request.url.path)
        if settings.auth_enabled and not has_required_role(roles, request.state.required_role):
            response = _json_error(403, "API key role is not allowed for this operation.", request_id)
            _record_response(request, response.status_code, started)
            return response

        client_host = request.client.host if request.client else "unknown"
        limiter_key = f"{api_key or 'anonymous'}:{client_host}"
        decision = rate_limiter.check(limiter_key, settings.rate_limit_per_minute)
        if not decision.allowed:
            response = _json_error(429, "Rate limit exceeded.", request_id)
            response.headers["Retry-After"] = str(decision.reset_seconds)
            _add_rate_limit_headers(response, decision.limit, decision.remaining, request_id)
            _record_response(request, response.status_code, started)
            return response

    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    if settings.rate_limit_per_minute > 0:
        response.headers.setdefault("X-RateLimit-Limit", str(settings.rate_limit_per_minute))
    _record_response(request, response.status_code, started)
    return response


def _json_error(status_code: int, detail: str, request_id: str) -> JSONResponse:
    response = JSONResponse(status_code=status_code, content={"detail": detail, "request_id": request_id})
    response.headers["X-Request-ID"] = request_id
    return response


def _add_rate_limit_headers(response: JSONResponse, limit: int, remaining: int, request_id: str) -> None:
    response.headers["X-RateLimit-Limit"] = str(limit)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    response.headers["X-Request-ID"] = request_id


def _record_response(request: Request, status_code: int, started: float) -> None:
    duration_ms = (perf_counter() - started) * 1000
    record_request(request.method, request.url.path, status_code, duration_ms)


app.include_router(router, prefix="/api/v1")
