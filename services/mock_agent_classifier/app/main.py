import os
import time

from fastapi import FastAPI, Request
from fastapi.responses import Response
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from pydantic import BaseModel
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

app = FastAPI(title="mock-agent-classifier", version="0.1.0")

SERVICE_NAME = os.getenv("SERVICE_NAME", "mock-agent-classifier")
AGENT_ID = os.getenv("AGENT_ID", "classifier-agent")
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
AGENT_METHODS = Counter(
    "agenthub_mock_agent_requests_total",
    "Requests handled by mock agent methods.",
    ["agent_id", "method"],
)


class ClassificationRequest(BaseModel):
    text: str


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
    if path.startswith("/classify_topic"):
        return "/classify_topic"
    if path.startswith("/classify_priority"):
        return "/classify_priority"
    return path


def classify_topic_value(text: str) -> str:
    lowered = text.lower()
    if any(token in lowered for token in ("invoice", "payment", "billing")):
        return "billing"
    if any(token in lowered for token in ("error", "incident", "outage", "latency")):
        return "operations"
    if any(token in lowered for token in ("feature", "roadmap", "release")):
        return "product"
    return "general"


def classify_priority_value(text: str) -> str:
    lowered = text.lower()
    if any(token in lowered for token in ("critical", "sev1", "urgent", "outage")):
        return "high"
    if any(token in lowered for token in ("soon", "important", "degraded")):
        return "medium"
    return "low"


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
    return {"status": "ok", "service": SERVICE_NAME, "agent_id": AGENT_ID}


@app.get("/metrics")
async def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/classify_topic")
async def classify_topic(payload: ClassificationRequest) -> dict[str, str]:
    AGENT_METHODS.labels(agent_id=AGENT_ID, method="classify_topic").inc()
    with tracer.start_as_current_span("agent.classify_topic") as span:
        span.set_attribute("agent.id", AGENT_ID)
        span.set_attribute("agent.method", "classify_topic")
        return {
            "agent_id": AGENT_ID,
            "method": "classify_topic",
            "label": classify_topic_value(payload.text),
        }


@app.post("/classify_priority")
async def classify_priority(payload: ClassificationRequest) -> dict[str, str]:
    AGENT_METHODS.labels(agent_id=AGENT_ID, method="classify_priority").inc()
    with tracer.start_as_current_span("agent.classify_priority") as span:
        span.set_attribute("agent.id", AGENT_ID)
        span.set_attribute("agent.method", "classify_priority")
        return {
            "agent_id": AGENT_ID,
            "method": "classify_priority",
            "label": classify_priority_value(payload.text),
        }
