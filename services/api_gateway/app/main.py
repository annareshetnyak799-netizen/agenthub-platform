import json
import os
import time
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

app = FastAPI(title="api-gateway", version="0.1.0")

ROUTER_URL = os.getenv("ROUTER_URL", "http://router-service:8001")
SERVICE_NAME = os.getenv("SERVICE_NAME", "api-gateway")
OTEL_EXPORTER_OTLP_ENDPOINT = os.getenv(
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "http://otel-collector:4317",
)

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
PROVIDER_SELECTIONS = Counter(
    "agenthub_gateway_provider_selections_total",
    "Providers selected by the gateway after routing.",
    ["provider_id", "model", "stream"],
)
UPSTREAM_ERRORS = Counter(
    "agenthub_gateway_upstream_errors_total",
    "Errors returned by router or upstream providers.",
    ["upstream", "status_code"],
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
    HTTPXClientInstrumentor().instrument()


setup_telemetry()
tracer = trace.get_tracer(__name__)


def normalize_path(path: str) -> str:
    if path.startswith("/metrics"):
        return "/metrics"
    if path.startswith("/health"):
        return "/health"
    if path.startswith("/v1/chat/completions"):
        return "/v1/chat/completions"
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


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": SERVICE_NAME}


@app.get("/metrics")
async def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> Any:
    payload = await request.json()
    model = payload.get("model", "unknown")
    stream_flag = bool(payload.get("stream"))

    with tracer.start_as_current_span("gateway.chat_completions") as span:
        span.set_attribute("llm.model", model)
        span.set_attribute("llm.stream", stream_flag)

        with tracer.start_as_current_span("gateway.route_request") as route_span:
            route_span.set_attribute("llm.model", model)
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as route_client:
                route_response = await route_client.post(f"{ROUTER_URL}/route", json=payload)
                route_span.set_attribute("http.status_code", route_response.status_code)
                if route_response.status_code >= 400:
                    UPSTREAM_ERRORS.labels(
                        upstream="router-service",
                        status_code=str(route_response.status_code),
                    ).inc()
                    detail: Any
                    try:
                        detail = route_response.json().get("detail", route_response.text)
                    except json.JSONDecodeError:
                        detail = route_response.text
                    route_span.set_attribute("error", True)
                    raise HTTPException(
                        status_code=route_response.status_code,
                        detail=detail,
                    )

        route = route_response.json()
        provider_url = route["provider_url"]
        PROVIDER_SELECTIONS.labels(
            provider_id=route["provider_id"],
            model=model,
            stream=str(stream_flag).lower(),
        ).inc()
        span.set_attribute("llm.selected_provider", route["provider_id"])
        span.set_attribute("llm.routing_strategy", route["strategy"])

        if stream_flag:
            with tracer.start_as_current_span("gateway.stream_provider_request") as provider_span:
                provider_span.set_attribute("llm.provider_id", route["provider_id"])
                provider_span.set_attribute("llm.model", model)
                upstream_client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=5.0))
                upstream_request = upstream_client.build_request(
                    "POST",
                    f"{provider_url}/v1/chat/completions",
                    json=payload,
                )
                upstream_response = await upstream_client.send(upstream_request, stream=True)
                provider_span.set_attribute("http.status_code", upstream_response.status_code)

                if upstream_response.status_code >= 400:
                    error_body = await upstream_response.aread()
                    await upstream_response.aclose()
                    await upstream_client.aclose()
                    UPSTREAM_ERRORS.labels(
                        upstream=route["provider_id"],
                        status_code=str(upstream_response.status_code),
                    ).inc()
                    provider_span.set_attribute("error", True)
                    raise HTTPException(
                        status_code=upstream_response.status_code,
                        detail=error_body.decode("utf-8", errors="ignore"),
                    )

            async def stream_bytes():
                try:
                    async for chunk in upstream_response.aiter_bytes():
                        yield chunk
                finally:
                    await upstream_response.aclose()
                    await upstream_client.aclose()

            return StreamingResponse(
                stream_bytes(),
                media_type=upstream_response.headers.get("content-type", "text/event-stream"),
                headers={"x-selected-provider": route["provider_id"]},
            )

        with tracer.start_as_current_span("gateway.provider_request") as provider_span:
            provider_span.set_attribute("llm.provider_id", route["provider_id"])
            provider_span.set_attribute("llm.model", model)
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as provider_client:
                provider_response = await provider_client.post(
                    f"{provider_url}/v1/chat/completions",
                    json=payload,
                )
                provider_span.set_attribute("http.status_code", provider_response.status_code)

        if provider_response.status_code >= 400:
            UPSTREAM_ERRORS.labels(
                upstream=route["provider_id"],
                status_code=str(provider_response.status_code),
            ).inc()

        content_type = provider_response.headers.get("content-type", "application/json")

        return JSONResponse(
            content=json.loads(provider_response.text),
            status_code=provider_response.status_code,
            media_type=content_type,
            headers={"x-selected-provider": route["provider_id"]},
        )
