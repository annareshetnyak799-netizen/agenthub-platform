import requests

from tests.helpers import DEFAULT_TIMEOUT, GATEWAY_URL, gateway_headers


def test_gateway_metrics_expose_llm_and_agent_counters() -> None:
    requests.post(
        f"{GATEWAY_URL}/v1/chat/completions",
        json={
            "model": "shared-demo-model",
            "stream": True,
            "messages": [{"role": "user", "content": "Metrics integration test"}],
        },
        headers=gateway_headers(),
        timeout=DEFAULT_TIMEOUT,
    )
    requests.post(
        f"{GATEWAY_URL}/v1/agents/summarizer-agent/summarize_text",
        json={"text": "Summarize this observability integration test payload."},
        headers=gateway_headers(),
        timeout=DEFAULT_TIMEOUT,
    )

    response = requests.get(f"{GATEWAY_URL}/metrics", timeout=DEFAULT_TIMEOUT)

    assert response.status_code == 200
    metrics_text = response.text
    assert "agenthub_gateway_llm_ttft_seconds" in metrics_text
    assert "agenthub_gateway_llm_tokens_total" in metrics_text
    assert "agenthub_gateway_llm_cost_total" in metrics_text
    assert "agenthub_gateway_agent_invocations_total" in metrics_text
    assert "agenthub_gateway_guardrail_blocks_total" in metrics_text
