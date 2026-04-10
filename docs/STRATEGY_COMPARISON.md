# Routing Strategy Comparison

## Compared Strategies

The platform uses three practical routing ideas:

1. model-based routing
2. round robin balancing
3. smart routing with health-aware and latency-aware selection

## Summary Table

| Strategy | Strength | Weakness | Best Use |
| --- | --- | --- | --- |
| Model-based routing | Simple and deterministic | Does not balance among replicas by itself | baseline provider selection |
| Round robin | Fair initial distribution | Ignores health and latency | cold start and equal candidates |
| Health-aware routing | Improves reliability | Needs provider state and cooldown logic | production safety |
| Latency-aware routing | Improves response speed | Needs warm-up observations and can bias early decisions if not designed carefully | optimizing user experience |

## Why the Final Design Uses Multiple Strategies

The final router is intentionally layered instead of using one single rule.

### Step 1. Filter by model

Only providers that support the requested model are eligible.

### Step 2. Filter by health

Providers are removed from the active pool when:

- manually disabled
- marked `unhealthy`
- inside cooldown after a failure

### Step 3. Filter by priority

Lower priority value wins.
This allows operators to express preferred providers.

### Step 4. Choose between round robin and latency-aware routing

The selected priority tier follows a short warm-up phase:

- if any candidate is still **cold** and has no `last_latency_ms`, use round robin across the whole priority tier
- once **all** candidates in that tier are warm, switch to latency-aware selection

This keeps the pool measurable and predictable:

1. **No cold-start lock-in** — new or restarted providers continue to receive warm-up traffic until they have a latency sample
2. **No hidden starvation** — a provider cannot be excluded forever just because another provider got measured first
3. **Stable failover validation** — providers that have not yet been sampled can still be selected and marked unhealthy when they begin failing

## Why Not Use Pure Latency Routing From the First Request

A pure latency policy gets stuck on whichever provider received the first sample:

- provider A gets measured first
- provider B has no sample yet
- router always prefers provider A
- provider B is never selected, never measured, and stays cold forever

The current implementation avoids that by keeping round robin active until every provider in the selected priority tier has at least one latency sample.

## Operational Trade-offs

### Round robin

Pros:

- easy to reason about
- no external state required
- predictable

Cons:

- can send traffic to unhealthy providers
- can keep selecting slow replicas

### Health-aware routing

Pros:

- improves resilience
- reduces repeated failures
- naturally supports failover demonstrations

Cons:

- requires health reporting path
- requires cooldown state management

### Latency-aware routing

Pros:

- reduces response time for end users
- allows platform to exploit faster providers automatically

Cons:

- depends on recent observations
- needs careful cold-start handling

## Recommendation

For this project, the best production-like balance is:

- model-based filtering
- health-aware filtering
- priority filtering
- round robin during warm-up
- latency-aware selection after warm-up

This gives the system the behavior of a decision-making platform instead of a static HTTP proxy.
