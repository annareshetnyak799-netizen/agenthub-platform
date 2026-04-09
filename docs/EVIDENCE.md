# Evidence Guide

This file describes where to store screenshots and short evidence snippets for the submission.

## Recommended Location

Store screenshots in:

```text
docs/evidence/
```

Recommended naming:

- `grafana-latency-traffic.png`
- `grafana-cpu-llm-telemetry.png`
- `jaeger-llm-trace.png`
- `jaeger-agent-trace.png`
- `mlflow-llm-run.png`
- `mlflow-agent-run.png`

## Recommended Evidence Set

### Monitoring

- Grafana screenshot with:
  - p50 / p95 latency
  - provider traffic distribution
- Grafana screenshot with:
  - CPU usage by service
  - optional TTFT / TPOT / cost panels visible
- Jaeger screenshot with:
  - gateway -> router -> provider trace

### Level 2

- MLflow screenshot with an LLM run
- MLflow screenshot with an agent run
- Jaeger screenshot with:
  - gateway -> agent-registry -> mock-agent trace

## Terminal Evidence

It is also reasonable to keep a short terminal excerpt or paste selected outputs into the report showing:

- `curl /health`
- `curl /metrics`
- example LLM request
- example agent invocation

## Notes

The repository can be submitted without screenshots embedded directly into markdown files.
However, keeping them in `docs/evidence/` makes the submission easier to review and easier to defend.
