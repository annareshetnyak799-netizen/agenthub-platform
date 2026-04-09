import requests

from tests.helpers import DEFAULT_TIMEOUT, GATEWAY_URL


def test_agent_invocation_is_proxied_through_gateway() -> None:
    payload = {"text": "Critical outage affecting all customers."}
    response = requests.post(
        f"{GATEWAY_URL}/v1/agents/classifier-agent/classify_priority",
        json=payload,
        timeout=DEFAULT_TIMEOUT,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["agent_id"] == "classifier-agent"
    assert body["method"] == "classify_priority"
    assert body["label"] == "high"


def test_missing_agent_returns_404() -> None:
    response = requests.post(
        f"{GATEWAY_URL}/v1/agents/missing-agent/classify_priority",
        json={"text": "Anything"},
        timeout=DEFAULT_TIMEOUT,
    )

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()
