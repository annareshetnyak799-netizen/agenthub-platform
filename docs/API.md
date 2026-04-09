# API Overview

## Public Gateway

### `POST /v1/chat/completions`

Proxies an LLM request through the routing layer.

Example:

```json
{
  "model": "shared-demo-model",
  "stream": false,
  "target_agent": "summarizer-agent",
  "messages": [
    {"role": "user", "content": "Hello from AgentHub"}
  ]
}
```

Behavior:

- validates and routes through `router-service`
- proxies to the selected provider
- supports streaming and non-streaming responses
- returns `x-selected-provider` response header

### `POST /v1/agents/{agent_id}/{method}`

Invokes a registered agent method by Agent Card lookup.

Examples:

`POST /v1/agents/summarizer-agent/summarize_text`

```json
{
  "text": "The service experienced latency spikes. The on-call engineer mitigated the issue by scaling the deployment."
}
```

`POST /v1/agents/classifier-agent/classify_priority`

```json
{
  "text": "Critical outage affecting all customers."
}
```

Behavior:

- looks up the agent in `agent-registry`
- validates the requested method
- forwards the JSON body to the mock agent service

### `GET /health`

Liveness endpoint for the gateway.

### `GET /metrics`

Prometheus metrics endpoint.

## Router Service

### `POST /route`

Selects a provider for a model request.

Request:

```json
{
  "model": "shared-demo-model",
  "target_agent": "summarizer-agent",
  "exclude_provider_ids": ["mock-openai"],
  "stream": true,
  "messages": [
    {"role": "user", "content": "Hello"}
  ]
}
```

Response:

```json
{
  "provider_id": "mock-anthropic",
  "provider_name": "mock-anthropic",
  "provider_url": "http://mock-provider-anthropic:8102",
  "price_input_per_1k": 0.12,
  "price_output_per_1k": 0.48,
  "strategy": "priority_latency_aware",
  "target_agent": "summarizer-agent"
}
```

### `GET /routing/stats`

Returns the active provider source and model coverage.

### `GET /health`

Liveness endpoint.

### `GET /metrics`

Prometheus metrics endpoint.

## Provider Registry

### `POST /providers/register`

Registers or updates a provider.

### `GET /providers`

Query params:

- `enabled_only`
- `healthy_only`
- `model`

### `GET /providers/{provider_id}`

Returns the provider record.

### `POST /providers/{provider_id}/disable`

Disables a provider manually.

### `POST /providers/{provider_id}/enable`

Re-enables a provider and clears cooldown state.

### `POST /providers/{provider_id}/report-health`

Used internally by the gateway to report success or failure.

Request:

```json
{
  "success": true,
  "latency_ms": 312.4,
  "status_code": 200
}
```

### `GET /health`

Liveness endpoint.

### `GET /metrics`

Prometheus metrics endpoint.

## Agent Registry

### `POST /agents/register`

Registers or updates an Agent Card.

Request:

```json
{
  "agent_id": "summarizer-agent",
  "name": "Summarizer Agent",
  "description": "Summarizes text and incident context.",
  "supported_methods": ["summarize_text", "summarize_incident"],
  "endpoint_url": "http://mock-agent-summarizer:8201",
  "status": "active",
  "auth_required": false
}
```

### `GET /agents`

Returns all registered Agent Cards.

### `GET /agents/{agent_id}`

Returns a single Agent Card.

### `GET /health`

Liveness endpoint.

### `GET /metrics`

Prometheus metrics endpoint.

## Mock Provider Admin API

### `POST /admin/failure-mode`

Available on both mock providers.

Request:

```json
{
  "enabled": true,
  "status_code": 503
}
```

Use this endpoint to simulate provider failures and demonstrate health-aware routing and failover.

## Mock Agent Methods

### Summarizer Agent

- `POST /summarize_text`
- `POST /summarize_incident`

### Classifier Agent

- `POST /classify_topic`
- `POST /classify_priority`
