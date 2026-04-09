import json
import os
import time
from threading import Lock

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from pydantic import BaseModel, HttpUrl
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

from .auth import require_bearer_token

app = FastAPI(title="agent-registry", version="0.1.0")

SERVICE_NAME = os.getenv("SERVICE_NAME", "agent-registry")
OTEL_EXPORTER_OTLP_ENDPOINT = os.getenv(
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "http://otel-collector:4317",
)
INITIAL_AGENTS = os.getenv("INITIAL_AGENTS", "[]")
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
    "agenthub_agent_registry_operations_total",
    "Agent registry operations.",
    ["operation", "result"],
)


class AgentCard(BaseModel):
    agent_id: str
    name: str
    description: str
    supported_methods: list[str]
    endpoint_url: HttpUrl
    status: str = "active"
    auth_required: bool = False


class AgentRegisterRequest(BaseModel):
    agent_id: str
    name: str
    description: str
    supported_methods: list[str]
    endpoint_url: HttpUrl
    status: str = "active"
    auth_required: bool = False


agent_store: dict[str, AgentCard] = {}
agent_lock = Lock()


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
    if path.startswith("/agents/register"):
        return "/agents/register"
    if path.startswith("/agents/"):
        return "/agents/{agent_id}"
    if path.startswith("/agents"):
        return "/agents"
    return path


def load_initial_agents() -> None:
    raw = json.loads(INITIAL_AGENTS)
    for item in raw:
        card = AgentCard(**item)
        agent_store[card.agent_id] = card


@app.on_event("startup")
async def startup_event() -> None:
    load_initial_agents()


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


@app.post("/agents/register", response_model=AgentCard, status_code=201)
async def register_agent(payload: AgentRegisterRequest, request: Request) -> AgentCard:
    require_bearer_token(request, ADMIN_API_TOKEN, "agent-registry")
    with tracer.start_as_current_span("agent_registry.register") as span:
        span.set_attribute("agent.id", payload.agent_id)
        existed = payload.agent_id in agent_store
        card = AgentCard(**payload.model_dump())
        with agent_lock:
            agent_store[card.agent_id] = card
        REGISTRY_OPERATIONS.labels(
            operation="register",
            result="updated" if existed else "created",
        ).inc()
        return card


@app.get("/agents", response_model=list[AgentCard])
async def list_agents() -> list[AgentCard]:
    REGISTRY_OPERATIONS.labels(operation="list", result="success").inc()
    with agent_lock:
        return sorted(agent_store.values(), key=lambda a: a.agent_id)


@app.get("/agents/{agent_id}", response_model=AgentCard)
async def get_agent(agent_id: str) -> AgentCard:
    with agent_lock:
        agent = agent_store.get(agent_id)
    if agent is None:
        REGISTRY_OPERATIONS.labels(operation="get", result="not_found").inc()
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")
    REGISTRY_OPERATIONS.labels(operation="get", result="success").inc()
    return agent
