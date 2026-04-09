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

If all candidates in the active priority tier already have latency samples:

- choose the provider with the lowest `last_latency_ms`

If at least one candidate does not yet have latency data:

- use round robin

This design prevents cold-start bias and keeps the system observable and explainable.

## Why Not Use Pure Latency Routing From the First Request

A pure latency policy can get stuck on whichever provider receives the first measured request.
That creates a feedback loop:

- provider A gets the first sample
- provider B remains unmeasured
- router keeps preferring provider A
- provider B never gets a chance to warm up

The current implementation avoids that by keeping round robin until all candidates have at least one latency sample.

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
