import requests

from tests.helpers import (
    ADMIN_API_TOKEN,
    AGENT_REGISTRY_URL,
    DEFAULT_TIMEOUT,
    GATEWAY_URL,
    MOCK_OPENAI_URL,
    PROVIDER_REGISTRY_URL,
    gateway_headers,
)


def test_gateway_rejects_missing_client_token() -> None:
    response = requests.post(
        f"{GATEWAY_URL}/v1/chat/completions",
        json={
            "model": "shared-demo-model",
            "stream": False,
            "messages": [{"role": "user", "content": "Auth integration test"}],
        },
        timeout=DEFAULT_TIMEOUT,
    )

    assert response.status_code == 401
    assert "Missing bearer token" in response.json()["detail"]


def test_provider_registry_rejects_missing_admin_token() -> None:
    response = requests.post(
        f"{PROVIDER_REGISTRY_URL}/providers/register",
        json={
            "provider_id": "unauthorized-provider",
            "provider_name": "Unauthorized Provider",
            "base_url": "http://unauthorized-provider:9999",
            "supported_models": ["unauthorized-model"],
            "weight": 1,
            "price_input_per_1k": 0.01,
            "price_output_per_1k": 0.02,
            "priority": 150,
            "enabled": True,
        },
        timeout=DEFAULT_TIMEOUT,
    )

    assert response.status_code == 401
    assert "Missing bearer token" in response.json()["detail"]


def test_agent_registry_accepts_admin_token() -> None:
    response = requests.post(
        f"{AGENT_REGISTRY_URL}/agents/register",
        json={
            "agent_id": "auth-agent",
            "name": "Auth Agent",
            "description": "Agent registered through auth test.",
            "supported_methods": ["ping"],
            "endpoint_url": "http://auth-agent:9997",
            "status": "active",
            "auth_required": False,
        },
        headers={"Authorization": f"Bearer {ADMIN_API_TOKEN}"},
        timeout=DEFAULT_TIMEOUT,
    )

    assert response.status_code == 201


def test_gateway_accepts_client_token() -> None:
    response = requests.post(
        f"{GATEWAY_URL}/v1/agents/classifier-agent/classify_priority",
        json={"text": "Critical outage affecting all customers."},
        headers=gateway_headers(),
        timeout=DEFAULT_TIMEOUT,
    )

    assert response.status_code == 200


def test_mock_provider_admin_rejects_missing_admin_token() -> None:
    response = requests.post(
        f"{MOCK_OPENAI_URL}/admin/failure-mode",
        json={"enabled": True, "status_code": 503},
        timeout=DEFAULT_TIMEOUT,
    )

    assert response.status_code == 401
    assert "Missing bearer token" in response.json()["detail"]
