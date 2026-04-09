# Runbook

## Local Startup

```bash
docker compose up --build
```

## Main UIs

- Gateway docs: `http://localhost:8000/docs`
- Router docs: `http://localhost:8005/docs`
- Provider Registry docs: `http://localhost:8006/docs`
- Agent Registry docs: `http://localhost:8007/docs`
- Prometheus: `http://localhost:9090`
- Grafana: `http://localhost:3000`
- Jaeger: `http://localhost:16686`
- MLflow: `http://localhost:5001`

## Smoke Tests

### LLM request

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "shared-demo-model",
    "stream": false,
    "messages": [{"role": "user", "content": "Hello from AgentHub"}]
  }'
```

### Streaming LLM request

```bash
curl -N -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "shared-demo-model",
    "stream": true,
    "messages": [{"role": "user", "content": "Stream telemetry test"}]
  }'
```

### Agent invocation

```bash
curl -X POST http://localhost:8000/v1/agents/summarizer-agent/summarize_text \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Service latency increased after the morning deploy. Rolling restart restored normal behavior."
  }'
```

## Failure Demo

Enable failure mode on `mock-openai`:

```bash
curl -X POST http://localhost:8101/admin/failure-mode \
  -H "Content-Type: application/json" \
  -d '{"enabled": true, "status_code": 503}'
```

Then send a shared-model request through the gateway and observe:

- provider marked `unhealthy` in `provider-registry`
- failover to `mock-anthropic`
- Jaeger trace includes failover path
- MLflow run captures failover metadata

Disable failure mode:

```bash
curl -X POST http://localhost:8101/admin/failure-mode \
  -H "Content-Type: application/json" \
  -d '{"enabled": false, "status_code": 503}'
```

## Key Metrics

- `agenthub_http_requests_total`
- `agenthub_http_request_duration_seconds`
- `agenthub_gateway_provider_selections_total`
- `agenthub_gateway_llm_ttft_seconds`
- `agenthub_gateway_llm_tpot_seconds`
- `agenthub_gateway_llm_tokens_total`
- `agenthub_gateway_llm_cost_total`
- `agenthub_gateway_llm_failovers_total`
- `process_cpu_seconds_total`

## Grafana Dashboard Expectations

The `AgentHub Overview` dashboard should show:

- request rate by service
- p50 and p95 latency for chat completions
- traffic distribution by provider
- response codes by service
- CPU usage by service
- TTFT / TPOT by provider
- estimated cost rate by provider
