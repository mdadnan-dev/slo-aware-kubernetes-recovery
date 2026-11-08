from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from time import perf_counter

from fastapi import FastAPI, Request
from prometheus_client import Counter, Histogram, make_asgi_app

from common.models import PodIdentity


REQUESTS = Counter(
    "net470_http_requests_total",
    "HTTP requests handled by the service",
    ("service", "method", "path", "status"),
)
LATENCY = Histogram(
    "net470_http_request_duration_seconds",
    "HTTP request duration measured inside the service",
    ("service", "method", "path"),
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        message = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in (
            "service",
            "run_id",
            "request_id",
            "event",
            "pod_name",
            "pod_uid",
            "node_name",
            "namespace",
            "image_version",
        ):
            value = getattr(record, key, None)
            if value is not None:
                message[key] = str(value)
        if record.exc_info:
            message["exception"] = self.formatException(record.exc_info)
        return json.dumps(message, separators=(",", ":"), sort_keys=True)


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())


def identity(service: str) -> PodIdentity:
    return PodIdentity(
        service=service,
        pod_name=os.getenv("POD_NAME", os.getenv("HOSTNAME", "local")),
        pod_uid=os.getenv("POD_UID", "local"),
        node_name=os.getenv("NODE_NAME", "local"),
        namespace=os.getenv("POD_NAMESPACE", "local"),
        image_version=os.getenv("IMAGE_VERSION", "development"),
    )


def install_standard_endpoints(app: FastAPI, service: str) -> None:
    service_identity = identity(service)

    @app.middleware("http")
    async def observe_request(request: Request, call_next):
        started = perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            path = request.url.path
            REQUESTS.labels(service, request.method, path, str(status)).inc()
            LATENCY.labels(service, request.method, path).observe(perf_counter() - started)

    @app.get("/health/live", include_in_schema=False)
    def live() -> dict[str, str]:
        return {"status": "live", "service": service}

    @app.get("/health/ready", include_in_schema=False)
    def ready() -> dict[str, str]:
        return {"status": "ready", "service": service}

    @app.get("/identity", response_model=PodIdentity)
    def get_identity() -> PodIdentity:
        return service_identity

    app.mount("/metrics", make_asgi_app())

