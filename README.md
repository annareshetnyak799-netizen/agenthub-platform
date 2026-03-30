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
