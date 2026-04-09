# Load Testing Guide

## Goal

This document covers the Level 3 load and failure testing requirement.
The objective is to measure:

- throughput
- latency
- error rate
- resilience during provider failure

The project uses a small `k6` scenario pack under `tests/load/`.

## Scenarios

### 1. Baseline Chat Load

File:

- `tests/load/k6_chat.js`

Purpose:

- exercise the standard non-streaming LLM path
- measure throughput and latency under concurrent chat traffic

### 2. Streaming Load

File:

- `tests/load/k6_stream.js`

Purpose:

- exercise the streaming SSE path
- verify that the gateway keeps streaming responses intact under concurrent usage

### 3. Failover Load

File:

- `tests/load/k6_failover.js`

Purpose:

- enable failure mode on `mock-openai`
- send concurrent shared-model traffic
- verify that requests still succeed through fallback routing
- verify that the failing provider is marked `unhealthy`

## How to Run

The most reliable option is to run `k6` through Docker Compose so it joins the same internal network as the platform services.

Preconditions:

- the full stack is already running through `docker compose up --build -d`
- the `k6` image is available locally or can be pulled by Docker
- gateway requests use `agenthub-client-token`
- registry and provider admin operations use `agenthub-admin-token`
- Compose services can reach each other on the default project network

### Baseline Chat Load

```bash
docker compose run --rm k6 run /scripts/k6_chat.js
```

### Streaming Load

```bash
docker compose run --rm k6 run /scripts/k6_stream.js
```

### Failover Load

```bash
docker compose run --rm k6 run /scripts/k6_failover.js
```

## What to Record

For each run, keep the following values from the `k6` summary:

- total requests
- requests per second
- average latency
- p95 latency
- error rate

For the failover run also record:

- whether requests stayed successful
- whether `mock-openai` was marked `unhealthy`
- whether traffic shifted to `mock-anthropic`

Recommended supporting evidence:

- raw terminal summary for each `k6` scenario
- optional `curl http://localhost:8006/providers/mock-openai` excerpt captured during the failover run

## Report Template

### Baseline Chat Load

| Metric | Value |
| --- | --- |
| duration | 30s |
| VUs | 10 |
| requests | 164 |
| req/s | 5.21 |
| avg latency | 894.7ms |
| p95 latency | 1.67s |
| error rate | 0.00% |

### Streaming Load

| Metric | Value |
| --- | --- |
| duration | 20s |
| VUs | 5 |
| requests | 40 |
| req/s | 1.84 |
| avg latency | 1.67s |
| p95 latency | 2.03s |
| error rate | 0.00% |

### Failover Load

| Metric | Value |
| --- | --- |
| duration | 20s |
| VUs | 5 |
| requests | 123 HTTP requests across 60 iterations |
| req/s | 5.69 |
| avg latency | 375.77ms |
| p95 latency | 904.04ms |
| error rate | 0.00% |
| fallback active | yes, checks confirmed `mock-anthropic` was selected |
| provider unhealthy | yes, checks confirmed `mock-openai` was marked unhealthy |

## Observed Results

The recorded runs demonstrate the following:

- the baseline non-streaming path remains stable under moderate concurrent load
- the streaming path preserves SSE completion markers and stays below the configured latency threshold
- the failover scenario keeps client-facing requests successful while `mock-openai` is forced to return `503`
- `provider-registry` reflects the unhealthy state during the failover scenario and routing shifts to `mock-anthropic`
- after the failover scenario finishes, `teardown()` restores `mock-openai` to a healthy state, so the strongest evidence is the `k6` summary itself rather than a post-run `curl`

## Evidence Capture

Recommended commands for saving terminal excerpts into `docs/evidence/`:

```bash
docker compose run --rm k6 run /scripts/k6_chat.js | tee docs/evidence/k6-baseline.txt
docker compose run --rm k6 run /scripts/k6_stream.js | tee docs/evidence/k6-stream.txt
docker compose run --rm k6 run /scripts/k6_failover.js | tee docs/evidence/k6-failover.txt
curl http://localhost:8006/providers/mock-openai | tee docs/evidence/k6-failover-provider-state.txt
```

## Expected Qualitative Outcome

- baseline load should complete with low error rate
- streaming load should preserve SSE completion and remain stable
- failover load should keep requests successful even when `mock-openai` is forced to return `503`
- the provider registry should mark the failing provider as `unhealthy` during the failover run

## Notes

This load pack is intentionally small and deterministic.
It is designed for course-level reproducibility rather than exhaustive production benchmarking.
The scripts focus on the platform acceptance criteria from Level 3 rather than synthetic maximum throughput.
