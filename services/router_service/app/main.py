import json
import os
import time
from collections import defaultdict
from itertools import cycle
from threading import Lock
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from pydantic import BaseModel
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

app = FastAPI(title="router-service", version="0.1.0")

SERVICE_NAME = os.getenv("SERVICE_NAME", "router-service")
PROVIDER_CONFIG = os.getenv("PROVIDER_CONFIG", "[]")
OTEL_EXPORTER_OTLP_ENDPOINT = os.getenv(
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "http://otel-collector:4317",
)


class RouteRequest(BaseModel):
    model: str
    stream: bool | None = False
    messages: list[dict[str, Any]] | None = None
    metadata: dict[str, Any] | None = None


providers = json.loads(PROVIDER_CONFIG)
round_robin_cycles: dict[str, Any] = {}
cycle_lock = Lock()
REQUEST_COUNT = Counter(
    "agenthub_http_requests_total",
    "Total HTTP requests handled by a service.",
    ["service", "method", "path", "status_code"],
)
REQUEST_LATENCY = Histogram(
    "agenthub_http_request_duration_seconds",
    "HTTP request latency in seconds.",
    ["service", "method", "path"],
)
ROUTING_DECISIONS = Counter(
    "agenthub_router_decisions_total",
    "Routing decisions made by the router.",
    ["provider_id", "model", "strategy"],
)
ROUTING_ERRORS = Counter(
    "agenthub_router_errors_total",
    "Routing errors returned by the router.",
    ["reason"],
)


def setup_telemetry() -> None:
    resource = Resource.create({"service.name": SERVICE_NAME})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(endpoint=OTEL_EXPORTER_OTLP_ENDPOINT, insecure=True)
        )
    )
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(
        app,
        excluded_urls="/health,/metrics",
    )


setup_telemetry()
tracer = trace.get_tracer(__name__)


def normalize_path(path: str) -> str:
    if path.startswith("/metrics"):
        return "/metrics"
    if path.startswith("/health"):
        return "/health"
    if path.startswith("/routing/stats"):
        return "/routing/stats"
    if path.startswith("/route"):
        return "/route"
    return path


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    method = request.method
    path = normalize_path(request.url.path)
    start = time.perf_counter()
    status_code = "500"

    try:
        response = await call_next(request)
        status_code = str(response.status_code)
        return response
    finally:
        elapsed = time.perf_counter() - start
        REQUEST_COUNT.labels(
            service=SERVICE_NAME,
            method=method,
            path=path,
            status_code=status_code,
        ).inc()
        REQUEST_LATENCY.labels(
            service=SERVICE_NAME,
            method=method,
            path=path,
        ).observe(elapsed)


def build_cycles() -> None:
    by_model = defaultdict(list)
    for provider in providers:
        for model in provider["supported_models"]:
            by_model[model].append(provider)

    for model, model_providers in by_model.items():
        weighted = []
        for provider in model_providers:
            weight = max(int(provider.get("weight", 1)), 1)
            weighted.extend([provider] * weight)
        round_robin_cycles[model] = cycle(weighted)


build_cycles()


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": SERVICE_NAME}


@app.get("/metrics")
async def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/routing/stats")
async def routing_stats() -> dict[str, Any]:
    return {"providers": providers, "models": sorted(round_robin_cycles.keys())}


@app.post("/route")
async def route(request: RouteRequest) -> dict[str, str]:
    with tracer.start_as_current_span("router.route") as span:
        span.set_attribute("llm.model", request.model)
        span.set_attribute("llm.stream", bool(request.stream))

        if request.model not in round_robin_cycles:
            ROUTING_ERRORS.labels(reason="model_not_registered").inc()
            span.set_attribute("error", True)
            raise HTTPException(
                status_code=404,
                detail=f"No provider registered for model '{request.model}'",
            )

        with cycle_lock:
            provider = next(round_robin_cycles[request.model])

        span.set_attribute("llm.selected_provider", provider["provider_id"])
        span.set_attribute("llm.routing_strategy", "model_round_robin")
        ROUTING_DECISIONS.labels(
            provider_id=provider["provider_id"],
            model=request.model,
            strategy="model_round_robin",
        ).inc()

        return {
            "provider_id": provider["provider_id"],
            "provider_name": provider["provider_name"],
            "provider_url": provider["base_url"],
            "strategy": "model_round_robin",
        }
