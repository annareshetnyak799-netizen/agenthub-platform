# AgentHub Architecture

## Goal

AgentHub is an infrastructure-first agent platform that:

- registers A2A agents and exposes their Agent Cards
- registers multiple LLM providers dynamically
- routes LLM requests using model-aware, latency-aware, and health-aware strategies
- proxies streaming responses without breaking the client connection
- collects metrics, traces, and request-level execution records
- blocks unsafe requests through a lightweight guardrail layer in the gateway
- protects public and administrative operations with bearer-token authorization

## System Components

| Component | Responsibility |
| --- | --- |
| `api-gateway` | Public entrypoint for LLM and agent requests, streaming proxy, failover handling, telemetry emission, MLflow logging |
| `router-service` | Provider selection, target agent validation, routing strategy selection |
| `provider-registry` | Runtime source of truth for provider metadata, price, health, latency, priority, cooldown state |
| `agent-registry` | Runtime source of truth for Agent Cards |
| `mock-provider-openai` | Deterministic mock LLM provider with streaming and injectable failures |
| `mock-provider-anthropic` | Deterministic mock LLM provider with streaming and injectable failures |
| `mock-agent-summarizer` | Minimal A2A-style mock service for summarization methods |
| `mock-agent-classifier` | Minimal A2A-style mock service for classification methods |
| `otel-collector` | Collects OpenTelemetry traces and exports them to Jaeger |
| `jaeger` | Distributed trace storage and UI |
| `prometheus` | Metrics scraping and query engine |
| `grafana` | Monitoring dashboards |
| `mlflow` | Request-level execution tracking UI for LLM and agent runs |

Current persistence model:

- `agent-registry` and `provider-registry` intentionally keep state in memory for a deterministic, easy-to-run course prototype
- this keeps the local stack simple while the main focus remains routing, failover, observability, guardrails, and authorization
- a production-grade next step would be adding persistent storage such as PostgreSQL for control-plane data

## Runtime Topology

```text
                       +-------------------+
Client ---------------->    api-gateway    |
                       +---------+---------+
                                 |
                     +-----------+-----------+
                     |                       |
                     | /route                | /v1/agents/{id}/{method}
                     v                       v
             +-------+--------+      +------+------+
             | router-service |      | agent-      |
             +----+------+----+      | registry    |
                  |      |           +------+------+
      /providers  |      | /agents/{id}           |
                  v      v                         |
          +-------+------+                         |
          | provider-    |                         |
          | registry     |                         |
          +------+-------+                         |
                 |                                 |
       +---------+----------+             +--------+---------------+
       |                    |             |                        |
       v                    v             v                        v
+------+-------------+  +---+------------+  +--------------------+  +--------------------+
| mock-provider-     |  | mock-provider- |  | mock-agent-        |  | mock-agent-        |
| openai             |  | anthropic      |  | summarizer         |  | classifier         |
+--------------------+  +----------------+  +--------------------+  +--------------------+
```

## LLM Request Flow

### 1. Standard non-stream request

```text
Client -> api-gateway -> router-service -> provider-registry
                             |
                             v
                        selected provider
                             |
Client <- api-gateway <- mock-provider-*
```

Detailed flow:

1. Client sends `POST /v1/chat/completions` to `api-gateway`.
2. Gateway asks `router-service` for a route.
3. Router pulls active providers from `provider-registry`.
4. Router filters providers by:
   - supported model
   - `enabled == true`
   - `health_status == healthy`
5. Router selects a provider using:
   - priority
   - latency-aware routing if all candidates have latency samples
   - round robin fallback otherwise
6. Gateway forwards the request to the selected provider.
7. Gateway returns the provider response to the client.
8. Gateway reports success and latency back to `provider-registry`.
9. Gateway emits:
   - Prometheus metrics
   - OpenTelemetry spans
   - MLflow run data

### 2. Streaming request

```text
Client -> api-gateway -> router-service -> mock-provider-*
Client <- api-gateway <- streamed chunks <- mock-provider-*
```

Important behavior:

- `api-gateway` does not buffer the entire answer
- chunks are forwarded as they arrive
- the client connection remains open for the duration of the stream
- gateway measures `TTFT` and `TPOT` from the live chunk stream

### 3. Health-aware failover

```text
Client -> api-gateway -> failing provider (5xx)
                       -> report unhealthy to provider-registry
                       -> ask router for a new route excluding failed provider
                       -> retry once against a healthy provider
Client <- successful fallback response
```

Behavior:

- provider is marked `unhealthy`
- `failure_count` increases
- `cooldown_until` is set
- router stops selecting that provider during cooldown
- after cooldown expires, provider is eligible again

## Agent Request Flow

The platform also exposes agent execution via the gateway:

```text
Client -> api-gateway -> agent-registry -> mock-agent-*
Client <- api-gateway <- mock-agent-*
```

Detailed flow:

1. Client calls `POST /v1/agents/{agent_id}/{method}`.
2. Gateway fetches the Agent Card from `agent-registry`.
3. Gateway validates:
   - agent exists
   - agent status is `active`
   - requested method is listed in `supported_methods`
4. Gateway forwards the payload to the agent endpoint from `endpoint_url`.
5. Gateway returns the JSON response to the client.
6. Gateway logs the invocation to MLflow and traces it through OpenTelemetry.

## Guardrail Flow

Guardrails are enforced in `api-gateway` before routing or agent lookup.

```text
Client -> api-gateway -> guardrail evaluation
                       -> blocked request returns 400
                       -> allowed request continues to router or agent-registry
```

Current guardrail categories:

- prompt injection patterns
- secret leakage patterns
- basic secret exfiltration patterns

## Authorization Flow

The platform uses bearer tokens for public and administrative operations.

Current token split:

- client token for `api-gateway` request entrypoints
- admin token for:
  - provider registry mutation endpoints
  - agent registry registration endpoint
  - mock provider admin failure mode
  - gateway to provider-registry health reporting

## Routing Strategies

### Model-based routing

Used to find providers supporting the requested model.

### Round robin

Used when:

- multiple providers support the same model
- candidates have equal priority
- latency data is not yet available for all candidates

### Latency-aware routing

Used when:

- all healthy candidates for the selected priority tier already have `last_latency_ms`

Selection rule:

- choose the provider with the lowest observed latency
- use round robin only to break ties

### Health-aware routing

Used continuously by filtering out:

- disabled providers
- providers in `unhealthy` state
- providers inside cooldown window

## Observability Architecture

### Prometheus and Grafana

Metrics are exposed by every service through `/metrics`.

Key dashboard categories:

- request rate
- p50/p95 latency
- provider traffic distribution
- response codes
- CPU usage by service
- TTFT / TPOT
- token and cost telemetry
- guardrail block counters

CPU is tracked via `process_cpu_seconds_total`, which is emitted by the Python Prometheus client in each service process and scraped by Prometheus.

### OpenTelemetry and Jaeger

Every service emits spans through OTLP to `otel-collector`, which forwards traces to Jaeger.

Key trace views:

- client request entering `api-gateway`
- provider route selection in `router-service`
- provider call latency
- failover path when a provider returns `5xx`
- agent invocation path through the gateway

### MLflow

MLflow is used as a request-level execution tracking UI.

Each LLM request logs:

- provider and model
- routing strategy
- stream flag
- token counts
- total latency
- TTFT
- TPOT
- request cost
- failover flag

Each agent invocation logs:

- agent id
- method
- request latency
- HTTP status

This satisfies the Level 2 requirement to trace both:

- LLM execution through provider routing
- agent execution through registered Agent Cards

## Design Choices

### Why separate gateway and router

- keeps request proxying separate from decision logic
- makes routing strategies easier to test and extend
- lets the gateway stay focused on protocol handling and telemetry

### Why registries are separate services

- provider and agent metadata become runtime state instead of static config
- new providers and agents can be registered without rebuilding images
- health, latency, and cooldown state is centralized

### Why mock providers and mock agents

- deterministic local development
- no dependency on paid external APIs
- repeatable demos for routing, failover, and tracing

### Why MLflow in addition to Prometheus and Jaeger

- Prometheus answers aggregate questions
- Jaeger explains one distributed request path
- MLflow stores comparable request runs and execution metadata over time

## Current Scope vs. Future Work

### Fully implemented in current repo

- Level 1 deployment and observability stack
- Level 2 registries and smart provider routing
- Level 2 request telemetry and MLflow integration
- minimal runnable mock agents

### Planned for Level 3

- guardrails
- token-based authorization
- structured load and failure test automation
- formal resilience benchmarking
