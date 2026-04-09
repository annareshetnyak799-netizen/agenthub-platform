import asyncio
import json
import os
import time
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from pydantic import BaseModel, Field
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

from .auth import require_bearer_token

app = FastAPI(title="mock-provider-anthropic", version="0.1.0")

SERVICE_NAME = os.getenv("SERVICE_NAME", "mock-provider-anthropic")
PROVIDER_ID = os.getenv("PROVIDER_ID", "mock-anthropic")
PROVIDER_NAME = os.getenv("PROVIDER_NAME", "mock-anthropic")
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "claude-3-haiku")
STREAM_DELAY_SECONDS = float(os.getenv("STREAM_DELAY_SECONDS", "0.25"))
BASE_LATENCY_MS = int(os.getenv("BASE_LATENCY_MS", "400"))
OTEL_EXPORTER_OTLP_ENDPOINT = os.getenv(
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "http://otel-collector:4317",
)
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
LLM_REQUESTS = Counter(
    "agenthub_provider_requests_total",
    "Requests handled by a mock provider.",
    ["provider_id", "model", "stream"],
)
LLM_TOKENS = Counter(
    "agenthub_provider_tokens_total",
    "Token counts emitted by a mock provider.",
    ["provider_id", "direction"],
)
failure_mode = {
    "enabled": False,
    "status_code": 503,
}


class FailureModeRequest(BaseModel):
    enabled: bool
    status_code: int = Field(default=503, ge=400, le=599)


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


def build_text(payload: dict[str, Any]) -> str:
    messages = payload.get("messages", [])
    prompt = messages[-1]["content"] if messages else "hello"
    return f"[{PROVIDER_NAME}] Response to: {prompt}"


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": SERVICE_NAME, "provider_id": PROVIDER_ID}


@app.get("/metrics")
async def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/admin/failure-mode")
async def set_failure_mode(payload: FailureModeRequest, request: Request) -> dict[str, Any]:
    require_bearer_token(request, ADMIN_API_TOKEN, "mock-provider-admin")
    failure_mode["enabled"] = payload.enabled
    failure_mode["status_code"] = payload.status_code
    return {"provider_id": PROVIDER_ID, "failure_mode": failure_mode}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> Any:
    payload = await request.json()
    model = payload.get("model", DEFAULT_MODEL)
    text = build_text(payload)
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created = int(time.time())

    with tracer.start_as_current_span("provider.chat_completions") as span:
        span.set_attribute("llm.provider_id", PROVIDER_ID)
        span.set_attribute("llm.model", model)
        span.set_attribute("llm.stream", bool(payload.get("stream")))
        span.set_attribute("llm.simulated_latency_ms", BASE_LATENCY_MS)

        await asyncio.sleep(BASE_LATENCY_MS / 1000)
        if failure_mode["enabled"]:
            span.set_attribute("error", True)
            return JSONResponse(
                status_code=failure_mode["status_code"],
                content={"detail": f"{PROVIDER_ID} forced failure mode enabled"},
            )
        LLM_REQUESTS.labels(
            provider_id=PROVIDER_ID,
            model=model,
            stream=str(bool(payload.get("stream"))).lower(),
        ).inc()

        if payload.get("stream"):
            prompt_tokens = max(len(" ".join(m.get("content", "") for m in payload.get("messages", []))) // 6, 1)
            completion_tokens = max(len(text.split()), 1)
            LLM_TOKENS.labels(provider_id=PROVIDER_ID, direction="input").inc(prompt_tokens)
            LLM_TOKENS.labels(provider_id=PROVIDER_ID, direction="output").inc(completion_tokens)

            async def event_stream():
                for token in text.split():
                    chunk = {
                        "id": completion_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model,
                        "provider": PROVIDER_NAME,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"content": token + " "},
                                "finish_reason": None,
                            }
                        ],
                    }
                    yield f"data: {json.dumps(chunk)}\n\n"
                    await asyncio.sleep(STREAM_DELAY_SECONDS)

                final_chunk = {
                    "id": completion_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "provider": PROVIDER_NAME,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                }
                yield f"data: {json.dumps(final_chunk)}\n\n"
                yield "data: [DONE]\n\n"

            return StreamingResponse(event_stream(), media_type="text/event-stream")

        response = {
            "id": completion_id,
            "object": "chat.completion",
            "created": created,
            "model": model,
            "provider": PROVIDER_NAME,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": max(len(text) // 6, 1),
                "completion_tokens": max(len(text.split()), 1),
                "total_tokens": max(len(text) // 6, 1) + max(len(text.split()), 1),
            },
        }
        LLM_TOKENS.labels(provider_id=PROVIDER_ID, direction="input").inc(response["usage"]["prompt_tokens"])
        LLM_TOKENS.labels(provider_id=PROVIDER_ID, direction="output").inc(response["usage"]["completion_tokens"])
        return JSONResponse(content=response)
