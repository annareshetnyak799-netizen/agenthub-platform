import json
import os
import time
from threading import Lock
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import Response
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from pydantic import BaseModel, Field
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

from .auth import require_bearer_token

app = FastAPI(title="provider-registry", version="0.1.0")

SERVICE_NAME = os.getenv("SERVICE_NAME", "provider-registry")
OTEL_EXPORTER_OTLP_ENDPOINT = os.getenv(
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "http://otel-collector:4317",
)
INITIAL_PROVIDERS = os.getenv("INITIAL_PROVIDERS", "[]")
ADMIN_API_TOKEN = os.getenv("ADMIN_API_TOKEN", "agenthub-admin-token")

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
REGISTRY_OPERATIONS = Counter(
    "agenthub_provider_registry_operations_total",
    "Provider registry operations.",
    ["operation", "result"],
)


class ProviderRecord(BaseModel):
    provider_id: str
    provider_name: str
    base_url: str
    supported_models: list[str]
    weight: int = Field(default=1, ge=1)
    price_input_per_1k: float = Field(default=0.0, ge=0.0)
    price_output_per_1k: float = Field(default=0.0, ge=0.0)
    rate_limit: int | None = Field(default=None, ge=1)
    priority: int = 100
    enabled: bool = True
    health_status: str = "healthy"
    last_latency_ms: float | None = Field(default=None, ge=0.0)
    failure_count: int = Field(default=0, ge=0)
    cooldown_until: float | None = None


class ProviderUpsertRequest(BaseModel):
    provider_id: str
    provider_name: str
    base_url: str
    supported_models: list[str]
    weight: int = Field(default=1, ge=1)
    price_input_per_1k: float = Field(default=0.0, ge=0.0)
    price_output_per_1k: float = Field(default=0.0, ge=0.0)
    rate_limit: int | None = Field(default=None, ge=1)
    priority: int = 100
    enabled: bool = True


class ProviderHealthReportRequest(BaseModel):
    success: bool
    latency_ms: float | None = Field(default=None, ge=0.0)
    status_code: int | None = None


provider_store: dict[str, ProviderRecord] = {}
provider_lock = Lock()


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
    if path.startswith("/providers/register"):
        return "/providers/register"
    if path.startswith("/providers/") and path.endswith("/disable"):
        return "/providers/{provider_id}/disable"
    if path.startswith("/providers/") and path.endswith("/enable"):
        return "/providers/{provider_id}/enable"
    if path.startswith("/providers/"):
        return "/providers/{provider_id}"
    if path.startswith("/providers"):
        return "/providers"
    return path


def load_initial_providers() -> None:
    raw = json.loads(INITIAL_PROVIDERS)
    for item in raw:
        record = ProviderRecord(**item)
        provider_store[record.provider_id] = record


def get_cooldown_seconds() -> int:
    return max(int(os.getenv("PROVIDER_COOLDOWN_SECONDS", "30")), 1)


def reconcile_provider(provider: ProviderRecord) -> ProviderRecord:
    if (
        provider.health_status == "unhealthy"
        and provider.cooldown_until is not None
        and provider.cooldown_until <= time.time()
    ):
        return provider.model_copy(
            update={
                "health_status": "healthy",
                "cooldown_until": None,
                "failure_count": 0,
            }
        )
    return provider


@app.on_event("startup")
async def startup_event() -> None:
    load_initial_providers()


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


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": SERVICE_NAME}


@app.get("/metrics")
async def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/providers/register", response_model=ProviderRecord, status_code=201)
async def register_provider(payload: ProviderUpsertRequest, request: Request) -> ProviderRecord:
    require_bearer_token(request, ADMIN_API_TOKEN, "provider-registry")
    with tracer.start_as_current_span("provider_registry.register") as span:
        span.set_attribute("provider.id", payload.provider_id)
        existed = payload.provider_id in provider_store
        record = ProviderRecord(**payload.model_dump())
        with provider_lock:
            provider_store[record.provider_id] = record
        REGISTRY_OPERATIONS.labels(
            operation="register",
            result="updated" if existed else "created",
        ).inc()
        return record


@app.get("/providers", response_model=list[ProviderRecord])
async def list_providers(
    enabled_only: bool = Query(default=False),
    healthy_only: bool = Query(default=False),
    model: str | None = Query(default=None),
) -> list[ProviderRecord]:
    with tracer.start_as_current_span("provider_registry.list") as span:
        span.set_attribute("provider.enabled_only", enabled_only)
        span.set_attribute("provider.healthy_only", healthy_only)
        if model:
            span.set_attribute("llm.model", model)

        with provider_lock:
            for provider_id, provider in list(provider_store.items()):
                provider_store[provider_id] = reconcile_provider(provider)
            providers = list(provider_store.values())

        if enabled_only:
            providers = [p for p in providers if p.enabled]
        if healthy_only:
            providers = [p for p in providers if p.health_status == "healthy"]
        if model:
            providers = [p for p in providers if model in p.supported_models]

        return sorted(providers, key=lambda p: (p.priority, p.provider_id))


@app.get("/providers/{provider_id}", response_model=ProviderRecord)
async def get_provider(provider_id: str) -> ProviderRecord:
    with provider_lock:
        provider = provider_store.get(provider_id)
        if provider is not None:
            provider = reconcile_provider(provider)
            provider_store[provider_id] = provider
    if provider is None:
        REGISTRY_OPERATIONS.labels(operation="get", result="not_found").inc()
        raise HTTPException(status_code=404, detail=f"Provider '{provider_id}' not found")
    REGISTRY_OPERATIONS.labels(operation="get", result="success").inc()
    return provider


@app.post("/providers/{provider_id}/disable", response_model=ProviderRecord)
async def disable_provider(provider_id: str, request: Request) -> ProviderRecord:
    require_bearer_token(request, ADMIN_API_TOKEN, "provider-registry")
    with provider_lock:
        provider = provider_store.get(provider_id)
        if provider is None:
            REGISTRY_OPERATIONS.labels(operation="disable", result="not_found").inc()
            raise HTTPException(status_code=404, detail=f"Provider '{provider_id}' not found")
        updated = provider.model_copy(update={"enabled": False})
        provider_store[provider_id] = updated
    REGISTRY_OPERATIONS.labels(operation="disable", result="success").inc()
    return updated


@app.post("/providers/{provider_id}/enable", response_model=ProviderRecord)
async def enable_provider(provider_id: str, request: Request) -> ProviderRecord:
    require_bearer_token(request, ADMIN_API_TOKEN, "provider-registry")
    with provider_lock:
        provider = provider_store.get(provider_id)
        if provider is None:
            REGISTRY_OPERATIONS.labels(operation="enable", result="not_found").inc()
            raise HTTPException(status_code=404, detail=f"Provider '{provider_id}' not found")
        updated = provider.model_copy(
            update={
                "enabled": True,
                "health_status": "healthy",
                "failure_count": 0,
                "cooldown_until": None,
            }
        )
        provider_store[provider_id] = updated
    REGISTRY_OPERATIONS.labels(operation="enable", result="success").inc()
    return updated


@app.post("/providers/{provider_id}/report-health", response_model=ProviderRecord)
async def report_provider_health(
    provider_id: str,
    payload: ProviderHealthReportRequest,
    request: Request,
) -> ProviderRecord:
    require_bearer_token(request, ADMIN_API_TOKEN, "provider-registry")
    with tracer.start_as_current_span("provider_registry.report_health") as span:
        span.set_attribute("provider.id", provider_id)
        span.set_attribute("provider.success", payload.success)
        if payload.status_code is not None:
            span.set_attribute("http.status_code", payload.status_code)
        if payload.latency_ms is not None:
            span.set_attribute("provider.latency_ms", payload.latency_ms)

        with provider_lock:
            provider = provider_store.get(provider_id)
            if provider is None:
                REGISTRY_OPERATIONS.labels(operation="report_health", result="not_found").inc()
                raise HTTPException(status_code=404, detail=f"Provider '{provider_id}' not found")

            provider = reconcile_provider(provider)

            if payload.success:
                updated = provider.model_copy(
                    update={
                        "health_status": "healthy",
                        "last_latency_ms": payload.latency_ms,
                        "failure_count": 0,
                        "cooldown_until": None,
                    }
                )
            else:
                updated = provider.model_copy(
                    update={
                        "health_status": "unhealthy",
                        "last_latency_ms": payload.latency_ms,
                        "failure_count": provider.failure_count + 1,
                        "cooldown_until": time.time() + get_cooldown_seconds(),
                    }
                )

            provider_store[provider_id] = updated

        REGISTRY_OPERATIONS.labels(operation="report_health", result="success").inc()
        return updated
