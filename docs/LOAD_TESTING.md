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

File: `tests/load/k6_chat.js`

Purpose:

- exercise the standard non-streaming LLM path
- measure throughput and latency under moderate concurrent load (10 VUs, 30 s)

### 2. Streaming Load

File: `tests/load/k6_stream.js`

Purpose:

- exercise the streaming SSE path
- verify that the gateway keeps streaming responses intact under concurrent usage

### 3. Failover Load

File: `tests/load/k6_failover.js`

Purpose:

- enable failure mode on `mock-openai`
- send concurrent shared-model traffic
- verify that requests still succeed through fallback routing
- verify that the failing provider is marked `unhealthy`

### 4. Ramp-Up Load

File: `tests/load/k6_ramp.js`

Purpose:

- simulate gradual traffic growth (0 → 20 VUs over 30 s, hold 60 s, ramp down)
- reveal latency degradation as concurrency increases
- verify that the balancer remains stable throughout the full ramp cycle

### 5. Spike Load

File: `tests/load/k6_spike.js`

Purpose:

- simulate a sudden 10× traffic burst (5 → 50 VUs in 5 s)
- verify the gateway does not crash or return 500 under peak load
- verify the platform recovers cleanly once the spike subsides

### 6. Multi-Provider Failure

File: `tests/load/k6_multi_failure.js`

Purpose:

- enable failure mode on **both** providers simultaneously
- verify the gateway returns a clean error (4xx/5xx) and does not hang or panic
- verify full recovery after both providers are restored (teardown smoke check)

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

### Ramp-Up Load

```bash
docker compose run --rm k6 run /scripts/k6_ramp.js
```

### Spike Load

```bash
docker compose run --rm k6 run /scripts/k6_spike.js
```

### Multi-Provider Failure

```bash
docker compose run --rm k6 run /scripts/k6_multi_failure.js
```

## What to Record

For each run, keep the following values from the `k6` summary:

- total requests
- requests per second
- average latency
- p95 latency and p99 latency (ramp/spike)
- error rate

For the failover and multi-failure runs also record:

- whether requests stayed successful (or failed cleanly)
- whether `mock-openai` / `mock-anthropic` were marked `unhealthy`
- whether traffic shifted correctly
- whether the platform recovered after teardown

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

### Ramp-Up Load

| Metric | Value |
| --- | --- |
| duration | 1m45s |
| VUs | 0 → 20 over 3 stages |
| requests | 948 |
| req/s | 8.98 |
| avg latency | 762.72ms |
| p95 latency | 1.24s |
| p99 latency | 1.46s |
| error rate | 0.00% |

### Spike Load

| Metric | Value |
| --- | --- |
| duration | 45s |
| VUs | 1 → 50 over 6 stages |
| requests | 405 |
| req/s | 8.85 |
| avg latency | 2.39s |
| p95 latency | 3.69s |
| error rate | 0.00% |

### Multi-Provider Failure

| Metric | Value |
| --- | --- |
| duration | 20s |
| VUs | 5 |
| requests | 97 HTTP requests across 90 iterations |
| req/s | 4.62 |
| avg latency | 126.45ms |
| p95 latency | 647.68ms |
| error rate | 92.78% |
| controlled failure path | yes, checks confirmed expected error status without hangs or `500` |
| platform recovery | yes, teardown smoke check passed |

## Observed Results

The recorded runs demonstrate the following:

- the baseline non-streaming path remains stable under moderate concurrent load
- the streaming path preserves SSE completion markers and stays below the configured latency threshold
- the failover scenario keeps client-facing requests successful while `mock-openai` is forced to return `503`
- `provider-registry` reflects the unhealthy state during the failover scenario and routing shifts to `mock-anthropic`
- the ramp-up scenario remains stable as concurrency gradually increases to 20 VUs
- the spike scenario absorbs a short burst to 50 VUs without internal server errors
- the multi-provider failure scenario intentionally produces a high `http_req_failed` value because both upstream providers are forced to fail; this is expected and is not treated as a scenario failure
- after the failover scenario finishes, `teardown()` restores `mock-openai` to a healthy state, so the strongest evidence is the `k6` summary itself rather than a post-run `curl`

## Evidence Capture

Recommended commands for saving terminal excerpts into `docs/evidence/`:

```bash
docker compose run --rm k6 run /scripts/k6_chat.js      | tee docs/evidence/k6-baseline.txt
docker compose run --rm k6 run /scripts/k6_stream.js     | tee docs/evidence/k6-stream.txt
docker compose run --rm k6 run /scripts/k6_failover.js   | tee docs/evidence/k6-failover.txt
docker compose run --rm k6 run /scripts/k6_ramp.js       | tee docs/evidence/k6-ramp.txt
docker compose run --rm k6 run /scripts/k6_spike.js      | tee docs/evidence/k6-spike.txt
docker compose run --rm k6 run /scripts/k6_multi_failure.js | tee docs/evidence/k6-multi-failure.txt

# Capture provider health state during failover run
curl -H "Authorization: Bearer agenthub-admin-token" \
  http://localhost:8006/providers/mock-openai | tee docs/evidence/k6-failover-provider-state.txt
```

## Expected Qualitative Outcome

- baseline load should complete with low error rate
- streaming load should preserve SSE completion and remain stable
- failover load should keep requests successful even when `mock-openai` is forced to return `503`
- ramp-up load should remain stable as concurrency increases gradually
- spike load should avoid internal server errors during the burst
- multi-provider failure should fail cleanly and recover cleanly
- the provider registry should mark the failing provider as `unhealthy` during the failover run

## Notes

This load pack is intentionally small and deterministic.
It is designed for course-level reproducibility rather than exhaustive production benchmarking.
The scripts focus on the platform acceptance criteria from Level 3 rather than synthetic maximum throughput.
