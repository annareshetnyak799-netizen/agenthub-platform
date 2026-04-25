import json
import os
import time
from collections import defaultdict
from threading import Lock
from typing import Any

import httpx
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

ADMIN_API_TOKEN = os.getenv("ADMIN_API_TOKEN", "agenthub-admin-token")


def require_bearer_token(request: Request, expected_token: str, realm: str) -> None:
    authorization = request.headers.get("authorization", "")
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail=f"Missing bearer token for {realm}")
    token = authorization.removeprefix("Bearer ").strip()
    if token != expected_token:
        raise HTTPException(status_code=401, detail=f"Invalid bearer token for {realm}")

app = FastAPI(title="router-service", version="0.1.0")

SERVICE_NAME = os.getenv("SERVICE_NAME", "router-service")
PROVIDER_CONFIG = os.getenv("PROVIDER_CONFIG", "[]")
PROVIDER_REGISTRY_URL = os.getenv(
    "PROVIDER_REGISTRY_URL",
    "http://provider-registry:8002",
)
AGENT_REGISTRY_URL = os.getenv(
    "AGENT_REGISTRY_URL",
    "http://agent-registry:8003",
)
OTEL_EXPORTER_OTLP_ENDPOINT = os.getenv(
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "http://otel-collector:4317",
)


class RouteRequest(BaseModel):
    model: str
    target_agent: str | None = None
    exclude_provider_ids: list[str] | None = None
    stream: bool | None = False
    messages: list[dict[str, Any]] | None = None
    metadata: dict[str, Any] | None = None


fallback_providers = json.loads(PROVIDER_CONFIG)
round_robin_indices: dict[str, int] = {}
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
    round_robin_indices.clear()
    by_model = defaultdict(list)
    for provider in fallback_providers:
        for model in provider["supported_models"]:
            by_model[model].append(provider)

    for model in by_model:
        round_robin_indices[model] = 0


build_cycles()


def build_provider_map(providers: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    by_model = defaultdict(list)

    for provider in providers:
        for model in provider["supported_models"]:
            by_model[model].append(provider)

    expanded: dict[str, list[dict[str, Any]]] = {}
    for model, model_providers in by_model.items():
        weighted = []
        for provider in model_providers:
            weight = max(int(provider.get("weight", 1)), 1)
            weighted.extend([provider] * weight)
        expanded[model] = weighted

    return expanded


def select_provider(
    model: str,
    candidates: list[dict[str, Any]],
) -> tuple[dict[str, Any], str]:
    min_priority = min(int(candidate.get("priority", 100)) for candidate in candidates)
    priority_candidates = [
        candidate
        for candidate in candidates
        if int(candidate.get("priority", 100)) == min_priority
    ]

    all_have_latency = all(
        candidate.get("last_latency_ms") is not None
        for candidate in priority_candidates
    )

    if all_have_latency:
        min_latency = min(float(c["last_latency_ms"]) for c in priority_candidates)
        best_candidates = [
            c
            for c in priority_candidates
            if float(c["last_latency_ms"]) == min_latency
        ]
        strategy = "priority_latency_aware"
    else:
        best_candidates = priority_candidates
        strategy = "model_round_robin"

    if len(best_candidates) == 1:
        return best_candidates[0], strategy

    current_index = round_robin_indices.get(model, 0)
    selected = best_candidates[current_index % len(best_candidates)]
    round_robin_indices[model] = (current_index + 1) % len(best_candidates)
    return selected, strategy


async def get_active_providers() -> tuple[list[dict[str, Any]], str]:
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=2.0)) as client:
            response = await client.get(
                f"{PROVIDER_REGISTRY_URL}/providers",
                params={"enabled_only": "true", "healthy_only": "true"},
                headers={"Authorization": f"Bearer {ADMIN_API_TOKEN}"},
            )
            response.raise_for_status()
            return response.json(), "provider_registry"
    except httpx.HTTPError:
        return fallback_providers, "static_fallback"


async def validate_target_agent(target_agent: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=2.0)) as client:
        response = await client.get(
            f"{AGENT_REGISTRY_URL}/agents/{target_agent}",
            headers={"Authorization": f"Bearer {ADMIN_API_TOKEN}"},
        )
        if response.status_code == 404:
            ROUTING_ERRORS.labels(reason="agent_not_registered").inc()
            raise HTTPException(
                status_code=404,
                detail=f"Agent '{target_agent}' not found",
            )
        if response.status_code >= 400:
            ROUTING_ERRORS.labels(reason="agent_registry_unavailable").inc()
            raise HTTPException(
                status_code=503,
                detail="Agent registry unavailable",
            )
        return response.json()


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": SERVICE_NAME}


@app.get("/metrics")
async def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/routing/stats")
async def routing_stats() -> dict[str, Any]:
    providers, source = await get_active_providers()
    provider_map = build_provider_map(providers)
    return {
        "source": source,
        "providers": providers,
        "models": sorted(provider_map.keys()),
    }


@app.post("/route")
async def route(request: RouteRequest, http_request: Request) -> dict[str, Any]:
    require_bearer_token(http_request, ADMIN_API_TOKEN, "router-service")
    with tracer.start_as_current_span("router.route") as span:
        span.set_attribute("llm.model", request.model)
        span.set_attribute("llm.stream", bool(request.stream))
        selected_agent = None
        if request.target_agent:
            selected_agent = await validate_target_agent(request.target_agent)
            span.set_attribute("agent.id", request.target_agent)

        providers, source = await get_active_providers()
        if request.exclude_provider_ids:
            providers = [
                provider
                for provider in providers
                if provider["provider_id"] not in request.exclude_provider_ids
            ]
            span.set_attribute(
                "llm.excluded_provider_count",
                len(request.exclude_provider_ids),
            )
        provider_map = build_provider_map(providers)
        span.set_attribute("provider.source", source)

        if request.model not in provider_map:
            ROUTING_ERRORS.labels(reason="model_not_registered").inc()
            span.set_attribute("error", True)
            raise HTTPException(
                status_code=404,
                detail=f"No provider registered for model '{request.model}'",
            )

        with cycle_lock:
            provider, strategy = select_provider(
                request.model,
                provider_map[request.model],
            )

        span.set_attribute("llm.selected_provider", provider["provider_id"])
        span.set_attribute("llm.routing_strategy", strategy)
        if provider.get("last_latency_ms") is not None:
            span.set_attribute("llm.selected_provider_latency_ms", float(provider["last_latency_ms"]))
        ROUTING_DECISIONS.labels(
            provider_id=provider["provider_id"],
            model=request.model,
            strategy=strategy,
        ).inc()

        return {
            "provider_id": provider["provider_id"],
            "provider_name": provider["provider_name"],
            "provider_url": provider["base_url"],
            "price_input_per_1k": provider.get("price_input_per_1k", 0.0),
            "price_output_per_1k": provider.get("price_output_per_1k", 0.0),
            "strategy": strategy,
            "target_agent": selected_agent["agent_id"] if selected_agent else "",
        }
