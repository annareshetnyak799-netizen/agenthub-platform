# AgentHub Platform

AgentHub Platform is an infrastructure-first agent platform for registering A2A agents, connecting multiple LLM providers, routing requests intelligently, and collecting end-to-end telemetry.

The project is designed as a staged implementation:

- Level 1: multi-provider LLM routing, streaming proxy, monitoring
- Level 2: agent and provider registries, smart routing, tracing
- Level 3: guardrails, authorization, load and failure testing

## Planned Components

- `api-gateway`
- `router-service`
- `agent-registry`
- `provider-registry`
- `mock-llm-providers`
- `mock-a2a-agents`
- `otel-collector`
- `prometheus`
- `grafana`
- `mlflow`

## Repository Structure

```text
docs/       Architecture, API, deployment, and testing notes
services/   Application services and mock components
infra/      Observability and local infrastructure configs
tests/      Integration, load, and failure scenario tests
```

## Status

Repository scaffold initialized with a Level 1 development skeleton:

- `api-gateway`
- `router-service`
- `agent-registry`
- `provider-registry`
- `mock-provider-openai`
- `mock-provider-anthropic`
- `docker-compose.yml`

## Quick Start

```bash
docker compose up --build
```

Monitoring endpoints after startup:

- Gateway: `http://localhost:8000/metrics`
- Router: `http://localhost:8005/metrics`
- Agent Registry: `http://localhost:8007/metrics`
- Provider Registry: `http://localhost:8006/metrics`
- Mock OpenAI: `http://localhost:8101/metrics`
- Mock Anthropic: `http://localhost:8102/metrics`
- Prometheus: `http://localhost:9090`
- Grafana: `http://localhost:3000` with `admin/admin`
- Jaeger: `http://localhost:16686`

Example request:

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "shared-demo-model",
    "stream": false,
    "messages": [{"role": "user", "content": "Hello from AgentHub"}]
  }'
```

The gateway asks the router to select a provider and then proxies the request to the selected mock provider.

## Level 2 In Progress

The first Level 2 service is `provider-registry`.
It stores provider metadata in memory, supports runtime registration, and is now used by `router-service` as the primary source of active providers, with static config kept as a fallback path.

The second Level 2 service is `agent-registry`.
It stores Agent Cards in memory and allows `router-service` to validate `target_agent` values passed in generation requests.

The current routing layer also supports a basic health-aware flow:
- `api-gateway` reports provider success and failure back to `provider-registry`
- unhealthy providers are temporarily excluded from routing during a cooldown window
- mock providers expose `/admin/failure-mode` so failover can be demonstrated locally

The gateway now also exposes Level 2 request telemetry for LLM traffic:
- `agenthub_gateway_llm_ttft_seconds`
- `agenthub_gateway_llm_tpot_seconds`
- `agenthub_gateway_llm_tokens_total`
- `agenthub_gateway_llm_cost_total`
- `agenthub_gateway_llm_failovers_total`
