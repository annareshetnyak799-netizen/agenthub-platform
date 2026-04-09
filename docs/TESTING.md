# Testing Report

## Scope

This project is validated through reproducible functional scenarios covering:

- Level 1 LLM routing and streaming
- Level 1 monitoring and tracing
- Level 2 agent and provider registries
- Level 2 health-aware and latency-aware routing
- Level 2 MLflow request tracking

## Test Matrix

| Scenario | Goal | Expected Result |
| --- | --- | --- |
| Non-stream LLM request | Verify basic routing | `200 OK`, provider response returned |
| Streaming LLM request | Verify streaming passthrough | SSE chunks streamed without disconnect |
| Round robin warm-up | Verify shared model can hit multiple providers | first requests alternate while latency samples are incomplete |
| Latency-aware routing | Prefer faster provider after warm-up | lower-latency provider selected consistently |
| Health-aware failover | Remove failing provider from pool | request still succeeds through fallback provider |
| Agent registry lookup | Verify discovery layer | list and get endpoints return Agent Cards |
| Agent invocation | Verify runnable agent path | gateway proxies request to selected mock agent |
| Jaeger tracing | Verify end-to-end traces | gateway, router, provider and agent spans visible |
| MLflow logging | Verify run-level tracking | request runs visible with latency/tokens/cost metadata |
| Grafana dashboard | Verify monitoring UX | panels populated with request and CPU metrics |

## Level 1 Monitoring Acceptance Checklist

This checklist is the direct acceptance block for the Level 1 monitoring requirement.

The condition should be considered closed when all points below are demonstrably true:

- OpenTelemetry is enabled in every service that participates in request handling.
- Prometheus successfully scrapes `/metrics` from:
  - `api-gateway`
  - `router-service`
  - `provider-registry`
  - `agent-registry`
  - `mock-provider-openai`
  - `mock-provider-anthropic`
  - `mock-agent-summarizer`
  - `mock-agent-classifier`
- Each service exposes a working `/health` endpoint.
- Gateway and service metrics include:
  - total request count
  - request duration histogram
  - response code distribution
- Grafana shows:
  - p50 latency
  - p95 latency
  - provider traffic distribution
  - response codes
  - CPU usage by service
- Jaeger shows request traces for:
  - gateway to router to provider
  - gateway to agent-registry to mock agent
- The monitoring stack remains functional during:
  - normal request flow
  - streaming request flow
  - failover scenario

Recommended evidence to keep for submission:

- 1 screenshot of Grafana with latency and provider traffic panels
- 1 screenshot of Grafana with CPU panel visible
- 1 screenshot of Jaeger trace
- 1 short terminal excerpt proving `/health` and `/metrics` availability

Recommended storage location:

- `docs/evidence/`

## Level 2 MLflow Acceptance Checklist

This checklist is the direct acceptance block for the MLflow requirement in Level 2.

The condition should be considered closed when all points below are demonstrably true:

- MLflow UI is reachable.
- At least one LLM request appears in MLflow as a run.
- At least one agent invocation appears in MLflow as a run.
- LLM runs contain execution metadata such as:
  - provider
  - model
  - routing strategy
  - latency
  - token counts
  - request cost
  - TTFT
  - TPOT when streaming is used
- Agent runs contain execution metadata such as:
  - agent id
  - method
  - latency
  - HTTP status
- Failover scenarios set the failover metadata for affected LLM runs.

Recommended evidence to keep for submission:

- 1 screenshot of an LLM run in MLflow
- 1 screenshot of an agent run in MLflow
- 1 short terminal excerpt showing at least one LLM request and one agent invocation

Recommended storage location:

- `docs/evidence/`

## Manual Validation Steps

### 1. Base LLM request

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "shared-demo-model",
    "stream": false,
    "messages": [{"role": "user", "content": "Telemetry smoke test"}]
  }'
```

Expected:

- HTTP `200`
- `x-selected-provider` header present
- JSON body contains `usage`

### 2. Streaming request

```bash
curl -N -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "shared-demo-model",
    "stream": true,
    "messages": [{"role": "user", "content": "Stream telemetry test"}]
  }'
```

Expected:

- `data:` chunks are returned incrementally
- final `data: [DONE]` marker is emitted

### 3. Latency-aware routing

Run several warm-up requests on `shared-demo-model`, then check:

```bash
curl http://localhost:8006/providers
curl http://localhost:8005/routing/stats
```

Expected:

- both providers have `last_latency_ms`
- routing prefers the faster provider when both are healthy

### 4. Health-aware routing and failover

```bash
curl -X POST http://localhost:8101/admin/failure-mode \
  -H "Content-Type: application/json" \
  -d '{"enabled": true, "status_code": 503}'
```

Send a shared-model request through the gateway, then inspect:

```bash
curl http://localhost:8006/providers/mock-openai
```

Expected:

- provider becomes `unhealthy`
- `failure_count` increments
- `cooldown_until` is set
- subsequent requests route to the healthy provider

### 5. Agent invocation

```bash
curl -X POST http://localhost:8000/v1/agents/classifier-agent/classify_priority \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Critical outage affecting all customers."
  }'
```

Expected:

- HTTP `200`
- JSON body contains `label`

### 6. Metrics and dashboards

```bash
curl http://localhost:8000/metrics | rg "agenthub_gateway_llm_(ttft|tpot|tokens|cost|failovers)"
```

Expected:

- TTFT present for streaming and non-streaming requests
- TPOT present for streaming requests
- tokens and cost counters increase over time

### 7. Tracing and MLflow

Expected:

- Jaeger shows gateway -> router -> provider traces
- Jaeger shows gateway -> agent-registry -> mock-agent traces for agent calls
- MLflow shows LLM runs with routing, token, latency, and cost metadata
- MLflow shows agent runs with agent id, method, latency, and status metadata

## Observed Qualitative Results

- `mock-openai` is usually selected after warm-up because its base latency is lower than `mock-anthropic`
- health-aware routing protects the client from provider `5xx` failures
- request cost metrics correlate with provider pricing from `provider-registry`
- TTFT and TPOT reflect streaming behavior and provider delay configuration

## Remaining Manual Checks

The repo currently relies on documented manual and UI-assisted validation rather than a full automated integration test suite.
This is acceptable for the current course milestone, but Level 3 should add scripted load and resilience tests.
