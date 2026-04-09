import asyncio
import json
import os
import time
from typing import Any

import httpx
import mlflow
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from mlflow.tracking import MlflowClient
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

from .auth import require_bearer_token
from .guardrails import evaluate_guardrails

app = FastAPI(title="api-gateway", version="0.1.0")

ROUTER_URL = os.getenv("ROUTER_URL", "http://router-service:8001")
AGENT_REGISTRY_URL = os.getenv(
    "AGENT_REGISTRY_URL",
    "http://agent-registry:8003",
)
PROVIDER_REGISTRY_URL = os.getenv(
    "PROVIDER_REGISTRY_URL",
    "http://provider-registry:8002",
)
SERVICE_NAME = os.getenv("SERVICE_NAME", "api-gateway")
OTEL_EXPORTER_OTLP_ENDPOINT = os.getenv(
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "http://otel-collector:4317",
)
MLFLOW_ENABLED = os.getenv("MLFLOW_ENABLED", "true").lower() == "true"
MLFLOW_TRACKING_URI = os.getenv("MLFLOW_TRACKING_URI", "http://mlflow:5000")
MLFLOW_EXPERIMENT_NAME = os.getenv(
    "MLFLOW_EXPERIMENT_NAME",
    "agenthub-gateway-requests",
)
PLATFORM_API_TOKEN = os.getenv("PLATFORM_API_TOKEN", "agenthub-client-token")
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
AGENT_INVOCATIONS = Counter(
    "agenthub_gateway_agent_invocations_total",
    "Agent invocations proxied by the gateway.",
    ["agent_id", "method", "status_code"],
)
LLM_TTFT = Histogram(
    "agenthub_gateway_llm_ttft_seconds",
    "Time to first token or first full response from the provider.",
    ["provider_id", "model", "stream"],
)
LLM_TPOT = Histogram(
    "agenthub_gateway_llm_tpot_seconds",
    "Average time per emitted output token after the first token.",
    ["provider_id", "model"],
)
LLM_TOKENS = Counter(
    "agenthub_gateway_llm_tokens_total",
    "Input and output token counts observed by the gateway.",
    ["provider_id", "model", "direction"],
)
LLM_COST = Counter(
    "agenthub_gateway_llm_cost_total",
    "Estimated LLM request cost in USD.",
    ["provider_id", "model"],
)
LLM_FAILOVERS = Counter(
    "agenthub_gateway_llm_failovers_total",
    "Failovers performed by the gateway after provider-side failures.",
    ["failed_provider_id", "fallback_provider_id", "model"],
)
GUARDRAIL_BLOCKS = Counter(
    "agenthub_gateway_guardrail_blocks_total",
    "Requests blocked by gateway guardrails.",
    ["path", "category", "reason"],
)
mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
mlflow_client = MlflowClient(tracking_uri=MLFLOW_TRACKING_URI)
mlflow_experiment_id: str | None = None


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
    if path.startswith("/v1/agents/"):
        return "/v1/agents/{agent_id}/{method}"
    if path.startswith("/v1/chat/completions"):
        return "/v1/chat/completions"
    return path


def get_mlflow_experiment_id() -> str | None:
    global mlflow_experiment_id
    if not MLFLOW_ENABLED:
        return None
    if mlflow_experiment_id is not None:
        return mlflow_experiment_id

    experiment = mlflow.get_experiment_by_name(MLFLOW_EXPERIMENT_NAME)
    if experiment is None:
        mlflow_experiment_id = mlflow.create_experiment(MLFLOW_EXPERIMENT_NAME)
    else:
        mlflow_experiment_id = experiment.experiment_id
    return mlflow_experiment_id


def estimate_prompt_tokens(messages: list[dict[str, Any]] | None) -> int:
    joined = " ".join(str(message.get("content", "")) for message in messages or [])
    return max(len(joined) // 6, 1) if joined else 0


def estimate_completion_tokens(text: str) -> int:
    return max(len(text.split()), 1) if text else 0


def estimate_cost(
    route: dict[str, Any],
    prompt_tokens: int,
    completion_tokens: int,
) -> float:
    input_price = float(route.get("price_input_per_1k", 0.0) or 0.0)
    output_price = float(route.get("price_output_per_1k", 0.0) or 0.0)
    return (prompt_tokens / 1000 * input_price) + (completion_tokens / 1000 * output_price)


def record_llm_metrics(
    route: dict[str, Any],
    model: str,
    stream_flag: bool,
    prompt_tokens: int,
    completion_tokens: int,
    ttft_seconds: float | None,
    tpot_seconds: float | None,
) -> float:
    provider_id = route["provider_id"]
    if prompt_tokens > 0:
        LLM_TOKENS.labels(
            provider_id=provider_id,
            model=model,
            direction="input",
        ).inc(prompt_tokens)
    if completion_tokens > 0:
        LLM_TOKENS.labels(
            provider_id=provider_id,
            model=model,
            direction="output",
        ).inc(completion_tokens)
    if ttft_seconds is not None:
        LLM_TTFT.labels(
            provider_id=provider_id,
            model=model,
            stream=str(stream_flag).lower(),
        ).observe(ttft_seconds)
    if tpot_seconds is not None:
        LLM_TPOT.labels(
            provider_id=provider_id,
            model=model,
        ).observe(tpot_seconds)

    cost = estimate_cost(route, prompt_tokens, completion_tokens)
    if cost > 0:
        LLM_COST.labels(
            provider_id=provider_id,
            model=model,
        ).inc(cost)
    return cost


def annotate_span_with_llm_telemetry(
    span: Any,
    route: dict[str, Any],
    prompt_tokens: int,
    completion_tokens: int,
    ttft_seconds: float | None,
    tpot_seconds: float | None,
    cost: float,
    failover: bool,
) -> None:
    span.set_attribute("llm.provider_id", route["provider_id"])
    span.set_attribute("llm.input_tokens", prompt_tokens)
    span.set_attribute("llm.output_tokens", completion_tokens)
    span.set_attribute("llm.cost_usd", cost)
    span.set_attribute("llm.failover", failover)
    if ttft_seconds is not None:
        span.set_attribute("llm.ttft_ms", ttft_seconds * 1000)
    if tpot_seconds is not None:
        span.set_attribute("llm.tpot_ms", tpot_seconds * 1000)


def log_request_to_mlflow_sync(
    route: dict[str, Any],
    model: str,
    stream_flag: bool,
    prompt_tokens: int,
    completion_tokens: int,
    ttft_seconds: float | None,
    tpot_seconds: float | None,
    cost: float,
    failover: bool,
    total_latency_seconds: float,
    status_code: int,
) -> None:
    experiment_id = get_mlflow_experiment_id()
    if experiment_id is None:
        return

    run = mlflow_client.create_run(
        experiment_id=experiment_id,
        tags={
            "service": SERVICE_NAME,
            "provider_id": route["provider_id"],
            "model": model,
            "stream": str(stream_flag).lower(),
            "routing_strategy": route.get("strategy", ""),
            "target_agent": route.get("target_agent", "") or "",
        },
    )
    run_id = run.info.run_id
    status = "FINISHED" if status_code < 400 else "FAILED"

    try:
        params = {
            "provider_id": route["provider_id"],
            "provider_name": route.get("provider_name", ""),
            "model": model,
            "stream": str(stream_flag).lower(),
            "routing_strategy": route.get("strategy", ""),
            "target_agent": route.get("target_agent", "") or "",
            "status_code": str(status_code),
            "failover": str(failover).lower(),
        }
        for key, value in params.items():
            mlflow_client.log_param(run_id, key, value)

        metrics = {
            "prompt_tokens": float(prompt_tokens),
            "completion_tokens": float(completion_tokens),
            "total_tokens": float(prompt_tokens + completion_tokens),
            "request_cost_usd": float(cost),
            "request_latency_seconds": float(total_latency_seconds),
        }
        if ttft_seconds is not None:
            metrics["ttft_seconds"] = float(ttft_seconds)
        if tpot_seconds is not None:
            metrics["tpot_seconds"] = float(tpot_seconds)

        for key, value in metrics.items():
            mlflow_client.log_metric(run_id, key, value)
    finally:
        mlflow_client.set_terminated(run_id, status=status)


async def log_request_to_mlflow(
    route: dict[str, Any],
    model: str,
    stream_flag: bool,
    prompt_tokens: int,
    completion_tokens: int,
    ttft_seconds: float | None,
    tpot_seconds: float | None,
    cost: float,
    failover: bool,
    total_latency_seconds: float,
    status_code: int,
) -> None:
    if not MLFLOW_ENABLED:
        return
    try:
        await asyncio.to_thread(
            log_request_to_mlflow_sync,
            route,
            model,
            stream_flag,
            prompt_tokens,
            completion_tokens,
            ttft_seconds,
            tpot_seconds,
            cost,
            failover,
            total_latency_seconds,
            status_code,
        )
    except Exception:
        # MLflow logging is best-effort and must never break the user path.
        return


def extract_usage_from_response(response_payload: dict[str, Any], payload: dict[str, Any]) -> tuple[int, int]:
    usage = response_payload.get("usage", {})
    prompt_tokens = int(usage.get("prompt_tokens", estimate_prompt_tokens(payload.get("messages"))))
    completion_text = ""
    choices = response_payload.get("choices", [])
    if choices:
        completion_text = choices[0].get("message", {}).get("content", "")
    completion_tokens = int(usage.get("completion_tokens", estimate_completion_tokens(completion_text)))
    return prompt_tokens, completion_tokens


class StreamingTelemetryState:
    def __init__(self, request_started: float, prompt_tokens: int):
        self.request_started = request_started
        self.prompt_tokens = prompt_tokens
        self.first_token_at: float | None = None
        self.last_token_at: float | None = None
        self.completion_tokens = 0
        self.buffer = ""
        self.recorded = False

    def consume(self, chunk: bytes) -> None:
        if not chunk:
            return

        self.buffer += chunk.decode("utf-8", errors="ignore")
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            line = line.strip()
            if not line or not line.startswith("data: "):
                continue

            payload = line[6:]
            if payload == "[DONE]":
                continue

            try:
                event = json.loads(payload)
            except json.JSONDecodeError:
                continue

            choices = event.get("choices", [])
            if not choices:
                continue
            delta_content = choices[0].get("delta", {}).get("content", "")
            if not delta_content:
                continue

            token_count = len(delta_content.split())
            if token_count <= 0:
                continue

            now = time.perf_counter()
            if self.first_token_at is None:
                self.first_token_at = now
            self.last_token_at = now
            self.completion_tokens += token_count

    def ttft_seconds(self) -> float | None:
        if self.first_token_at is None:
            return None
        return self.first_token_at - self.request_started

    def tpot_seconds(self) -> float | None:
        if self.first_token_at is None or self.last_token_at is None or self.completion_tokens <= 1:
            return None
        return (self.last_token_at - self.first_token_at) / (self.completion_tokens - 1)


async def report_provider_health(
    provider_id: str,
    success: bool,
    latency_ms: float | None,
    status_code: int | None,
) -> None:
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=2.0)) as client:
            await client.post(
                f"{PROVIDER_REGISTRY_URL}/providers/{provider_id}/report-health",
                json={
                    "success": success,
                    "latency_ms": latency_ms,
                    "status_code": status_code,
                },
                headers={"Authorization": f"Bearer {ADMIN_API_TOKEN}"},
            )
    except httpx.HTTPError:
        # Health reporting is best-effort and must not break the user request path.
        return


async def request_route(
    payload: dict[str, Any],
    exclude_provider_ids: list[str] | None = None,
) -> dict[str, Any]:
    route_payload = dict(payload)
    if exclude_provider_ids:
        route_payload["exclude_provider_ids"] = exclude_provider_ids

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as route_client:
        route_response = await route_client.post(f"{ROUTER_URL}/route", json=route_payload)
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
            raise HTTPException(
                status_code=route_response.status_code,
                detail=detail,
            )
    return route_response.json()


async def get_agent_card(agent_id: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=3.0)) as client:
        response = await client.get(f"{AGENT_REGISTRY_URL}/agents/{agent_id}")
    if response.status_code == 404:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")
    if response.status_code >= 400:
        UPSTREAM_ERRORS.labels(
            upstream="agent-registry",
            status_code=str(response.status_code),
        ).inc()
        raise HTTPException(status_code=503, detail="Agent registry unavailable")
    return response.json()


def enforce_guardrails(payload: dict[str, Any], path: str, span: Any) -> None:
    decision = evaluate_guardrails(payload)
    if not decision.blocked:
        return

    GUARDRAIL_BLOCKS.labels(
        path=path,
        category=decision.category,
        reason=decision.reason,
    ).inc()
    span.set_attribute("guardrail.blocked", True)
    span.set_attribute("guardrail.category", decision.category)
    span.set_attribute("guardrail.reason", decision.reason)
    raise HTTPException(
        status_code=400,
        detail={
            "message": "Request blocked by guardrails",
            "category": decision.category,
            "reason": decision.reason,
        },
    )


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


@app.post("/v1/agents/{agent_id}/{method_name}")
async def invoke_agent(agent_id: str, method_name: str, request: Request) -> Response:
    require_bearer_token(request, PLATFORM_API_TOKEN, "gateway")
    payload = await request.json()
    request_started = time.perf_counter()

    with tracer.start_as_current_span("gateway.invoke_agent") as span:
        span.set_attribute("agent.id", agent_id)
        span.set_attribute("agent.method", method_name)
        enforce_guardrails(payload, "/v1/agents/{agent_id}/{method}", span)

        agent_card = await get_agent_card(agent_id)
        if agent_card.get("status") != "active":
            raise HTTPException(
                status_code=503,
                detail=f"Agent '{agent_id}' is not active",
            )
        if method_name not in agent_card.get("supported_methods", []):
            raise HTTPException(
                status_code=404,
                detail=f"Method '{method_name}' is not supported by agent '{agent_id}'",
            )

        target_url = f"{agent_card['endpoint_url'].rstrip('/')}/{method_name}"
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as client:
            agent_response = await client.post(target_url, json=payload)

        AGENT_INVOCATIONS.labels(
            agent_id=agent_id,
            method=method_name,
            status_code=str(agent_response.status_code),
        ).inc()
        span.set_attribute("http.status_code", agent_response.status_code)

        if agent_response.status_code >= 400:
            UPSTREAM_ERRORS.labels(
                upstream=agent_id,
                status_code=str(agent_response.status_code),
            ).inc()
            raise HTTPException(
                status_code=agent_response.status_code,
                detail=agent_response.text,
            )

        await log_request_to_mlflow(
            route={
                "provider_id": agent_id,
                "provider_name": agent_card.get("name", agent_id),
                "strategy": "agent_registry_lookup",
                "target_agent": agent_id,
            },
            model=f"agent:{method_name}",
            stream_flag=False,
            prompt_tokens=0,
            completion_tokens=0,
            ttft_seconds=None,
            tpot_seconds=None,
            cost=0.0,
            failover=False,
            total_latency_seconds=time.perf_counter() - request_started,
            status_code=agent_response.status_code,
        )

        return JSONResponse(
            content=json.loads(agent_response.text),
            status_code=agent_response.status_code,
            media_type=agent_response.headers.get("content-type", "application/json"),
        )


@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> Any:
    require_bearer_token(request, PLATFORM_API_TOKEN, "gateway")
    payload = await request.json()
    model = payload.get("model", "unknown")
    stream_flag = bool(payload.get("stream"))
    request_started = time.perf_counter()

    with tracer.start_as_current_span("gateway.chat_completions") as span:
        span.set_attribute("llm.model", model)
        span.set_attribute("llm.stream", stream_flag)
        enforce_guardrails(payload, "/v1/chat/completions", span)

        with tracer.start_as_current_span("gateway.route_request") as route_span:
            route_span.set_attribute("llm.model", model)
            route = await request_route(payload)

        provider_url = route["provider_url"]
        PROVIDER_SELECTIONS.labels(
            provider_id=route["provider_id"],
            model=model,
            stream=str(stream_flag).lower(),
        ).inc()
        span.set_attribute("llm.selected_provider", route["provider_id"])
        span.set_attribute("llm.routing_strategy", route["strategy"])

        if stream_flag:
            prompt_tokens = estimate_prompt_tokens(payload.get("messages"))
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
                    await report_provider_health(
                        provider_id=route["provider_id"],
                        success=False,
                        latency_ms=(time.perf_counter() - request_started) * 1000,
                        status_code=upstream_response.status_code,
                    )
                    if upstream_response.status_code >= 500:
                        with tracer.start_as_current_span("gateway.failover_route_request") as failover_span:
                            failover_span.set_attribute("llm.failed_provider", route["provider_id"])
                            retry_route = await request_route(
                                payload,
                                exclude_provider_ids=[route["provider_id"]],
                            )
                            failover_span.set_attribute("llm.selected_provider", retry_route["provider_id"])
                            LLM_FAILOVERS.labels(
                                failed_provider_id=route["provider_id"],
                                fallback_provider_id=retry_route["provider_id"],
                                model=model,
                            ).inc()

                        retry_client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=5.0))
                        retry_request = retry_client.build_request(
                            "POST",
                            f"{retry_route['provider_url']}/v1/chat/completions",
                            json=payload,
                        )
                        retry_response = await retry_client.send(retry_request, stream=True)

                        if retry_response.status_code >= 400:
                            retry_error_body = await retry_response.aread()
                            await retry_response.aclose()
                            await retry_client.aclose()
                            await report_provider_health(
                                provider_id=retry_route["provider_id"],
                                success=False,
                                latency_ms=(time.perf_counter() - request_started) * 1000,
                                status_code=retry_response.status_code,
                            )
                            raise HTTPException(
                                status_code=retry_response.status_code,
                                detail=retry_error_body.decode("utf-8", errors="ignore"),
                            )

                        await report_provider_health(
                            provider_id=retry_route["provider_id"],
                            success=True,
                            latency_ms=(time.perf_counter() - request_started) * 1000,
                            status_code=retry_response.status_code,
                        )

                        stream_state = StreamingTelemetryState(
                            request_started=request_started,
                            prompt_tokens=prompt_tokens,
                        )

                        async def retry_stream_bytes():
                            try:
                                async for chunk in retry_response.aiter_bytes():
                                    stream_state.consume(chunk)
                                    yield chunk
                            finally:
                                if not stream_state.recorded:
                                    ttft_seconds = stream_state.ttft_seconds()
                                    tpot_seconds = stream_state.tpot_seconds()
                                    cost = record_llm_metrics(
                                        retry_route,
                                        model,
                                        True,
                                        stream_state.prompt_tokens,
                                        stream_state.completion_tokens,
                                        ttft_seconds,
                                        tpot_seconds,
                                    )
                                    annotate_span_with_llm_telemetry(
                                        span,
                                        retry_route,
                                        stream_state.prompt_tokens,
                                        stream_state.completion_tokens,
                                        ttft_seconds,
                                        tpot_seconds,
                                        cost,
                                        True,
                                    )
                                    await log_request_to_mlflow(
                                        retry_route,
                                        model,
                                        True,
                                        stream_state.prompt_tokens,
                                        stream_state.completion_tokens,
                                        ttft_seconds,
                                        tpot_seconds,
                                        cost,
                                        True,
                                        time.perf_counter() - request_started,
                                        retry_response.status_code,
                                    )
                                    stream_state.recorded = True
                                await retry_response.aclose()
                                await retry_client.aclose()

                        return StreamingResponse(
                            retry_stream_bytes(),
                            media_type=retry_response.headers.get("content-type", "text/event-stream"),
                            headers={"x-selected-provider": retry_route["provider_id"]},
                        )

                    raise HTTPException(
                        status_code=upstream_response.status_code,
                        detail=error_body.decode("utf-8", errors="ignore"),
                    )

                await report_provider_health(
                    provider_id=route["provider_id"],
                    success=True,
                    latency_ms=(time.perf_counter() - request_started) * 1000,
                    status_code=upstream_response.status_code,
                )

            stream_state = StreamingTelemetryState(
                request_started=request_started,
                prompt_tokens=prompt_tokens,
            )

            async def stream_bytes():
                try:
                    async for chunk in upstream_response.aiter_bytes():
                        stream_state.consume(chunk)
                        yield chunk
                finally:
                    if not stream_state.recorded:
                        ttft_seconds = stream_state.ttft_seconds()
                        tpot_seconds = stream_state.tpot_seconds()
                        cost = record_llm_metrics(
                            route,
                            model,
                            True,
                            stream_state.prompt_tokens,
                            stream_state.completion_tokens,
                            ttft_seconds,
                            tpot_seconds,
                        )
                        annotate_span_with_llm_telemetry(
                            span,
                            route,
                            stream_state.prompt_tokens,
                            stream_state.completion_tokens,
                            ttft_seconds,
                            tpot_seconds,
                            cost,
                            False,
                        )
                        await log_request_to_mlflow(
                            route,
                            model,
                            True,
                            stream_state.prompt_tokens,
                            stream_state.completion_tokens,
                            ttft_seconds,
                            tpot_seconds,
                            cost,
                            False,
                            time.perf_counter() - request_started,
                            upstream_response.status_code,
                        )
                        stream_state.recorded = True
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
            await report_provider_health(
                provider_id=route["provider_id"],
                success=False,
                latency_ms=(time.perf_counter() - request_started) * 1000,
                status_code=provider_response.status_code,
            )
            if provider_response.status_code >= 500:
                with tracer.start_as_current_span("gateway.failover_route_request") as failover_span:
                    failover_span.set_attribute("llm.failed_provider", route["provider_id"])
                    retry_route = await request_route(
                        payload,
                        exclude_provider_ids=[route["provider_id"]],
                    )
                    failover_span.set_attribute("llm.selected_provider", retry_route["provider_id"])
                    LLM_FAILOVERS.labels(
                        failed_provider_id=route["provider_id"],
                        fallback_provider_id=retry_route["provider_id"],
                        model=model,
                    ).inc()

                async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as retry_client:
                    retry_response = await retry_client.post(
                        f"{retry_route['provider_url']}/v1/chat/completions",
                        json=payload,
                    )

                if retry_response.status_code >= 400:
                    await report_provider_health(
                        provider_id=retry_route["provider_id"],
                        success=False,
                        latency_ms=(time.perf_counter() - request_started) * 1000,
                        status_code=retry_response.status_code,
                    )
                else:
                    await report_provider_health(
                        provider_id=retry_route["provider_id"],
                        success=True,
                        latency_ms=(time.perf_counter() - request_started) * 1000,
                        status_code=retry_response.status_code,
                    )

                if retry_response.status_code < 400:
                    retry_payload = json.loads(retry_response.text)
                    prompt_tokens, completion_tokens = extract_usage_from_response(
                        retry_payload,
                        payload,
                    )
                    ttft_seconds = time.perf_counter() - request_started
                    cost = record_llm_metrics(
                        retry_route,
                        model,
                        False,
                        prompt_tokens,
                        completion_tokens,
                        ttft_seconds,
                        None,
                    )
                    annotate_span_with_llm_telemetry(
                        span,
                        retry_route,
                        prompt_tokens,
                        completion_tokens,
                        ttft_seconds,
                        None,
                        cost,
                        True,
                    )
                    await log_request_to_mlflow(
                        retry_route,
                        model,
                        False,
                        prompt_tokens,
                        completion_tokens,
                        ttft_seconds,
                        None,
                        cost,
                        True,
                        time.perf_counter() - request_started,
                        retry_response.status_code,
                    )

                return JSONResponse(
                    content=json.loads(retry_response.text),
                    status_code=retry_response.status_code,
                    media_type=retry_response.headers.get("content-type", "application/json"),
                    headers={"x-selected-provider": retry_route["provider_id"]},
                )
        else:
            await report_provider_health(
                provider_id=route["provider_id"],
                success=True,
                latency_ms=(time.perf_counter() - request_started) * 1000,
                status_code=provider_response.status_code,
            )
            response_payload = json.loads(provider_response.text)
            prompt_tokens, completion_tokens = extract_usage_from_response(
                response_payload,
                payload,
            )
            ttft_seconds = time.perf_counter() - request_started
            cost = record_llm_metrics(
                route,
                model,
                False,
                prompt_tokens,
                completion_tokens,
                ttft_seconds,
                None,
            )
            annotate_span_with_llm_telemetry(
                span,
                route,
                prompt_tokens,
                completion_tokens,
                ttft_seconds,
                None,
                cost,
                False,
            )
            await log_request_to_mlflow(
                route,
                model,
                False,
                prompt_tokens,
                completion_tokens,
                ttft_seconds,
                None,
                cost,
                False,
                time.perf_counter() - request_started,
                provider_response.status_code,
            )

        content_type = provider_response.headers.get("content-type", "application/json")

        return JSONResponse(
            content=json.loads(provider_response.text),
            status_code=provider_response.status_code,
            media_type=content_type,
            headers={"x-selected-provider": route["provider_id"]},
        )
