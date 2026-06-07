from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from threading import Lock
from typing import Any


STARTED_AT = time.time()


@dataclass
class RequestMetric:
    count: int = 0
    total_duration_ms: float = 0.0
    max_duration_ms: float = 0.0
    status_counts: dict[str, int] = field(default_factory=dict)


class MetricsRegistry:
    def __init__(self) -> None:
        self._lock = Lock()
        self._requests: dict[tuple[str, str], RequestMetric] = {}

    def record_request(self, method: str, path: str, status_code: int, duration_ms: float) -> None:
        key = (method.upper(), _normalize_path(path))
        status_class = f"{status_code // 100}xx"
        with self._lock:
            metric = self._requests.setdefault(key, RequestMetric())
            metric.count += 1
            metric.total_duration_ms += duration_ms
            metric.max_duration_ms = max(metric.max_duration_ms, duration_ms)
            metric.status_counts[status_class] = metric.status_counts.get(status_class, 0) + 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            requests = [
                {
                    "method": method,
                    "path": path,
                    "count": metric.count,
                    "avg_duration_ms": round(metric.total_duration_ms / max(metric.count, 1), 3),
                    "max_duration_ms": round(metric.max_duration_ms, 3),
                    "status_counts": dict(sorted(metric.status_counts.items())),
                }
                for (method, path), metric in sorted(self._requests.items())
            ]
        return {"uptime_seconds": round(time.time() - STARTED_AT, 3), "requests": requests}

    def prometheus(self, extra: dict[str, Any] | None = None) -> str:
        snapshot = self.snapshot()
        lines = [
            "# TYPE app_uptime_seconds gauge",
            f"app_uptime_seconds {snapshot['uptime_seconds']}",
            "# TYPE app_http_requests_total counter",
            "# TYPE app_http_request_duration_ms_avg gauge",
            "# TYPE app_http_request_duration_ms_max gauge",
        ]
        for request in snapshot["requests"]:
            labels = f'method="{request["method"]}",path="{request["path"]}"'
            lines.append(f"app_http_requests_total{{{labels}}} {request['count']}")
            lines.append(f"app_http_request_duration_ms_avg{{{labels}}} {request['avg_duration_ms']}")
            lines.append(f"app_http_request_duration_ms_max{{{labels}}} {request['max_duration_ms']}")
            for status_class, count in request["status_counts"].items():
                lines.append(f'app_http_requests_by_status_total{{{labels},status_class="{status_class}"}} {count}')
        for key, value in sorted((extra or {}).items()):
            if isinstance(value, (int, float)):
                lines.append(f"app_{key} {value}")
        return "\n".join(lines) + "\n"


metrics_registry = MetricsRegistry()


def record_request(method: str, path: str, status_code: int, duration_ms: float) -> None:
    metrics_registry.record_request(method, path, status_code, duration_ms)


def metrics_snapshot() -> dict[str, Any]:
    return metrics_registry.snapshot()


def metrics_prometheus(extra: dict[str, Any] | None = None) -> str:
    return metrics_registry.prometheus(extra)


def _normalize_path(path: str) -> str:
    normalized = re.sub(r"/run_[a-f0-9]+", "/{run_id}", path)
    normalized = re.sub(r"/job_[a-f0-9]+", "/{job_id}", normalized)
    normalized = re.sub(r"/[0-9a-f]{16,}", "/{id}", normalized)
    return normalized
