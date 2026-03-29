# Architecture Draft

## Project Goal

Build an agent platform that can:

- register A2A agents
- register LLM providers
- route requests to the best available provider
- proxy streaming responses
- expose metrics, logs, traces, and health checks

## High-Level Services

- `api-gateway`: unified external API
- `router-service`: provider and agent routing decisions
- `agent-registry`: A2A agent cards and discovery
- `provider-registry`: provider metadata, price, limits, health
- `mock-providers`: deterministic and failure-injectable LLM mocks
- `observability stack`: OpenTelemetry, Prometheus, Grafana, MLflow

## Delivery Plan

### Level 1

- Docker Compose environment
- multiple LLM providers
- model-based routing
- round robin or weighted balancing
- streaming passthrough
- basic monitoring

### Level 2

- agent registry
- provider registry
- latency-aware and health-aware routing
- token and cost telemetry
- tracing with MLflow

### Level 3

- guardrails
- token-based authorization
- load and failure testing

