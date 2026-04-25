# Runbook

## Local Startup

```bash
docker compose up --build
```

The Compose stack uses `depends_on: condition: service_healthy` for the core service graph.
This means the public gateway starts only after the router, registries, mock agents, `otel-collector`, and MLflow pass their health checks.

## Automated Test Suite

Install the lightweight test dependencies and run the black-box integration suite against the live stack:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r tests/requirements.txt
PYTHONNOUSERSITE=1 python -m pytest -p no:debugging tests -v
```

## Load Test Pack

Load and failover scenarios are provided in:

- `tests/load/k6_chat.js`
- `tests/load/k6_stream.js`
- `tests/load/k6_failover.js`

See:

- `docs/LOAD_TESTING.md`

Quick commands:

```bash
docker compose run --rm k6 run /scripts/k6_chat.js
docker compose run --rm k6 run /scripts/k6_stream.js
docker compose run --rm k6 run /scripts/k6_failover.js
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
  -H "Authorization: Bearer agenthub-client-token" \
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
  -H "Authorization: Bearer agenthub-client-token" \
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
  -H "Authorization: Bearer agenthub-client-token" \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Service latency increased after the morning deploy. Rolling restart restored normal behavior."
  }'
```

## Failure Demo

Enable failure mode on `mock-openai`:

```bash
curl -X POST http://localhost:8101/admin/failure-mode \
  -H "Authorization: Bearer agenthub-admin-token" \
  -H "Content-Type: application/json" \
  -d '{"enabled": true, "status_code": 503}'
```

Then send a shared-model request through the gateway and observe:

- provider marked `unhealthy` in `provider-registry`
- failover to `mock-anthropic`
- additional healthy providers remain eligible for subsequent retries if more than one fallback exists
- Jaeger trace includes failover path
- MLflow run captures failover metadata

Disable failure mode:

```bash
curl -X POST http://localhost:8101/admin/failure-mode \
  -H "Authorization: Bearer agenthub-admin-token" \
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
