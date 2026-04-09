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

app = FastAPI(title="mock-agent-summarizer", version="0.1.0")

SERVICE_NAME = os.getenv("SERVICE_NAME", "mock-agent-summarizer")
AGENT_ID = os.getenv("AGENT_ID", "summarizer-agent")
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


class SummarizeTextRequest(BaseModel):
    text: str
    max_sentences: int = 2


class SummarizeIncidentRequest(BaseModel):
    title: str
    description: str
    severity: str = "unknown"


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
    if path.startswith("/summarize_text"):
        return "/summarize_text"
    if path.startswith("/summarize_incident"):
        return "/summarize_incident"
    return path


def truncate_sentences(text: str, max_sentences: int) -> str:
    sentences = [segment.strip() for segment in text.replace("!", ".").replace("?", ".").split(".") if segment.strip()]
    if not sentences:
        return "No content provided."
    return ". ".join(sentences[:max(max_sentences, 1)]) + "."


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


@app.post("/summarize_text")
async def summarize_text(payload: SummarizeTextRequest) -> dict[str, str | int]:
    AGENT_METHODS.labels(agent_id=AGENT_ID, method="summarize_text").inc()
    with tracer.start_as_current_span("agent.summarize_text") as span:
        span.set_attribute("agent.id", AGENT_ID)
        span.set_attribute("agent.method", "summarize_text")
        summary = truncate_sentences(payload.text, payload.max_sentences)
        return {
            "agent_id": AGENT_ID,
            "method": "summarize_text",
            "summary": summary,
            "source_length": len(payload.text),
        }


@app.post("/summarize_incident")
async def summarize_incident(payload: SummarizeIncidentRequest) -> dict[str, str]:
    AGENT_METHODS.labels(agent_id=AGENT_ID, method="summarize_incident").inc()
    with tracer.start_as_current_span("agent.summarize_incident") as span:
        span.set_attribute("agent.id", AGENT_ID)
        span.set_attribute("agent.method", "summarize_incident")
        summary = (
            f"Incident '{payload.title}' with severity '{payload.severity}': "
            f"{truncate_sentences(payload.description, 2)}"
        )
        return {
            "agent_id": AGENT_ID,
            "method": "summarize_incident",
            "summary": summary,
        }
