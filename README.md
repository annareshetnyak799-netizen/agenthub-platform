# AgentHub Platform

**Infrastructure-first agent platform** for registering A2A agents, connecting multiple LLM providers, routing requests intelligently, and collecting end-to-end telemetry — with **gateway-level safety controls** as a first-class concern.

The current prototype keeps the control plane lightweight by using file-backed JSON snapshots for the registries; a production-grade next step would move that state to PostgreSQL or another durable shared store.

The project is structured as a staged implementation:

- **Level 1:** multi-provider LLM routing, streaming proxy, monitoring
- **Level 2:** agent and provider registries, smart routing, tracing
- **Level 3:** guardrails, authorization, load and failure testing

---

## For AI safety researchers

This platform explores **infrastructure-level safety for multi-agent systems** — safety controls implemented at the routing layer rather than baked into individual agents:

- **Gateway-level guardrails** — prompt-injection patterns, secret leakage, and basic exfiltration patterns are detected at the API gateway *before* a request reaches the router or any provider. This means a single chokepoint can be audited and updated without touching individual agents. See [Level 3 safety controls](#level-3-safety-controls) for details.
- **Trust boundary between control plane and data plane** — the gateway accepts client traffic with `agenthub-client-token`; registry mutations and provider admin operations require a separate `agenthub-admin-token`. This is a deliberate authorization split, not just a single API key.
- **Health-aware routing with cooldown ejection** — unhealthy providers are temporarily ejected from routing during a cooldown window; failover continues across remaining providers rather than stopping at first failure. Routing decisions are recorded in telemetry and reproducible via fixtures.
- **Full request-level observability** — every LLM and agent request is logged through OpenTelemetry → Jaeger (traces), Prometheus + Grafana (metrics), and MLflow (per-request runs with provider, model, tokens, latency, TTFT, TPOT, cost, failover metadata). Designed so multi-agent coordination patterns are inspectable post-hoc.
- **Reproducible failure scenarios** — k6 load and failure tests run against the live local stack via Docker Compose, and mock providers expose `/admin/failure-mode` endpoints so failover behavior can be triggered deterministically.
- **A2A agent registry with explicit Agent Cards** — agents are registered with declared capabilities, and `target_agent` values are validated against the registry rather than trusted blindly from the request.

The architectural bet is that **multi-agent safety is largely an infrastructure problem** — auth boundaries, request inspection chokepoints, observability for inter-agent traffic, and deterministic failover — not just an alignment problem at the model level.

---

## Planned components

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

## Repository structure

```
docs/       Architecture, API, deployment, and testing notes
services/   Application services and mock components
infra/      Observability and local infrastructure configs
tests/      Automated integration tests for core Level 1/2 flows
            and Level 3 load scenarios
```

Key documentation:

- `docs/ARCHITECTURE.md`
- `docs/API.md`
- `docs/RUNBOOK.md`
- `docs/TESTING.md`
- `docs/STRATEGY_COMPARISON.md`
- `docs/EVIDENCE.md`
- `docs/LOAD_TESTING.md`

---

## Implemented capabilities

The repository delivers a complete local platform package across Levels 1, 2, and 3.

### Level 1 — routing and streaming

- Docker Compose deployment for all components
- multiple mock LLM providers
- model-based routing with round-robin fallback
- streaming passthrough
- health endpoints
- Prometheus, Grafana, OpenTelemetry, and Jaeger monitoring

### Level 2 — registries, smart routing, tracing

- dynamic provider registration
- A2A agent registry with Agent Cards
- runnable mock agent services
- health-aware routing with cooldown-based ejection
- latency-aware routing after warm-up
- TTFT, TPOT, token, and cost telemetry
- MLflow request tracking for both LLM and agent execution
- automated black-box integration tests for core Level 1/2 flows

### Level 3 — safety controls and resilience testing

- gateway guardrails for prompt injection, secret leakage, and basic exfiltration patterns
- bearer-token authorization for gateway, registry mutation endpoints, and provider admin operations
- reproducible k6 load and failure test scenarios through Docker Compose networking

---

## Level 3 safety controls

The gateway is the chokepoint for all client traffic, so safety logic lives there rather than being scattered across providers or agents.

### Guardrails

Requests are inspected **before routing** and rejected if they match patterns for:

- **Prompt injection** — instruction-overriding patterns ("ignore previous instructions", role-play attempts to bypass system policy, etc.).
- **Secret leakage** — common credential patterns (API key formats, token shapes) appearing in either request or expected output.
- **Basic exfiltration** — patterns suggesting attempts to extract system prompts, internal state, or routing metadata.

This is intentionally a **first-line defense**, not a complete solution: it catches surface-level attacks and shifts the bar, but does not replace per-agent or per-provider safety policies. False negatives are expected on adversarial inputs designed to evade pattern matching.

### Authorization

Two separate bearer tokens enforce a control plane / data plane split:

- `agenthub-client-token` — public gateway requests (LLM completions, agent invocations).
- `agenthub-admin-token` — registry mutations and provider admin endpoints (failure-mode toggles, registration, deregistration).

This means a compromised client token cannot reconfigure the platform, and admin operations leave a distinct audit trail.

### Failure testing

The k6 scenarios under `tests/` exercise:

- provider outage and recovery (forced via `/admin/failure-mode`),
- gateway behavior under sustained load,
- failover correctness when multiple providers degrade simultaneously.

These run against the live Docker Compose stack, so behavior reflects actual networking and timeout dynamics rather than unit-test mocks.

---

## Quick start

```
docker compose up --build
```

Run the automated integration suite against the live local stack:

```
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r tests/requirements.txt
PYTHONNOUSERSITE=1 python -m pytest -p no:debugging tests -v
```

Monitoring endpoints after startup:

- Gateway: `http://localhost:8000/metrics`
- Router: `http://localhost:8005/metrics`
- Agent Registry: `http://localhost:8007/metrics`
- Provider Registry: `http://localhost:8006/metrics`
- Mock OpenAI: `http://localhost:8101/metrics`
- Mock Anthropic: `http://localhost:8102/metrics`
- Mock Agent Summarizer: `http://localhost:8201/metrics`
- Mock Agent Classifier: `http://localhost:8202/metrics`
- Prometheus: `http://localhost:9090`
- Grafana: `http://localhost:3000` (admin / admin)
- Jaeger: `http://localhost:16686`
- MLflow: `http://localhost:5001`

Example LLM request:

```
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer agenthub-client-token" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "shared-demo-model",
    "stream": false,
    "messages": [{"role": "user", "content": "Hello from AgentHub"}]
  }'
```

The gateway asks the router to select a provider and then proxies the request to the selected mock provider.

Example agent request:

```
curl -X POST http://localhost:8000/v1/agents/summarizer-agent/summarize_text \
  -H "Authorization: Bearer agenthub-client-token" \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Service latency increased after the morning deploy. Rolling restart restored normal behavior."
  }'
```

---

## Level 2 delivery notes

The first Level 2 service is `provider-registry`. It stores provider metadata in a small local JSON snapshot, supports runtime registration, and is now used by `router-service` as the primary source of active providers, with static config kept as a fallback path.

The second Level 2 service is `agent-registry`. It stores Agent Cards in a local JSON snapshot and allows `router-service` to validate `target_agent` values passed in generation requests.

The current routing layer also supports a basic health-aware flow:

- `api-gateway` reports provider success and failure back to `provider-registry`
- unhealthy providers are temporarily excluded from routing during a cooldown window
- failover can continue across the remaining healthy providers instead of stopping after the first retry
- mock providers expose `/admin/failure-mode` so failover can be demonstrated locally

Docker startup is also health-gated:

- core application services wait for `otel-collector`
- public gateway waits for router, registries, agents, and MLflow to become healthy
- Prometheus and Grafana start only after the application plane is ready

The gateway exposes Level 2 request telemetry for LLM traffic:

- `agenthub_gateway_llm_ttft_seconds`
- `agenthub_gateway_llm_tpot_seconds`
- `agenthub_gateway_llm_tokens_total`
- `agenthub_gateway_llm_cost_total`
- `agenthub_gateway_llm_failovers_total`

Basic CPU visibility is also available through `process_cpu_seconds_total`, scraped by Prometheus and displayed in Grafana.

MLflow is wired in as a request-level tracking UI. Each LLM request is logged as a run with provider, model, routing strategy, tokens, latency, TTFT, TPOT, cost, and failover metadata. Each agent invocation is logged as a run with agent ID, method, latency, and status metadata.

---

## Honest limitations

This is a prototype, and the README would be misleading without listing what it does *not* yet do:

- **Pattern-based guardrails are first-line, not comprehensive** — sophisticated prompt injections (encoded payloads, multi-turn attacks, context-window manipulation) will pass through. The guardrails are useful as a baseline filter, not as a defense-in-depth solution.
- **Bypass / FN rate is not yet benchmarked** — there is no adversarial eval suite measuring how often the guardrails miss true positives or over-trigger on benign inputs. This is the most important next step for the safety story.
- **Mock providers and agents** — real LLM provider integration and real A2A agents are out of scope for the current prototype; routing and observability are validated against mocks.
- **JSON-snapshot registries** — fine for a local prototype, but production deployment would require a durable shared store (PostgreSQL or similar) with proper migration handling.
- **No tenant isolation, no rate limiting, no audit log** — the current authorization is bearer-token-based and does not yet model multi-tenant deployments or enforce per-client limits.

---

## Research extensions

Natural directions for extending this platform toward AI safety / multi-agent control research:

- **Adversarial guardrail benchmark** — build a labelled set of injection / leakage / exfiltration attempts (and benign-but-similar inputs), measure bypass rate and false-positive rate of the gateway filters, and use this as a regression suite when guardrails are updated.
- **Inter-agent traffic monitoring** — extend MLflow / OTEL tracing to flag suspicious patterns in A2A communication: agents requesting capabilities outside their declared scope, unusual call graphs, or coordination patterns suggestive of collusion. Relevant for control protocols on untrusted agents.
- **Untrusted monitor protocol** — implement a variant of AI Control's "untrusted monitoring" pattern where one agent reviews another's outputs before routing decisions, and measure the system-level safety / utility tradeoff.
- **Agent Card verification** — currently Agent Cards are taken at face value at registration. A research extension would investigate behavioral verification: testing whether a registered agent's actual behavior matches its declared capabilities, and detecting drift over time.
- **Routing strategy as a safety lever** — empirically compare routing strategies (round-robin, latency-aware, capability-aware, safety-rating-aware) on a controlled benchmark to characterize how routing decisions shape end-to-end safety and reliability.

These are concrete starting points for follow-up work, not commitments of the current prototype.
