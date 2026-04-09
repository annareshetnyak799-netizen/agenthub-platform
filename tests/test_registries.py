import requests

from tests.helpers import (
    AGENT_REGISTRY_URL,
    DEFAULT_TIMEOUT,
    PROVIDER_REGISTRY_URL,
)


def test_provider_registry_lists_initial_providers() -> None:
    response = requests.get(f"{PROVIDER_REGISTRY_URL}/providers", timeout=DEFAULT_TIMEOUT)

    assert response.status_code == 200
    providers = response.json()
    provider_ids = {provider["provider_id"] for provider in providers}
    assert {"mock-openai", "mock-anthropic"} <= provider_ids


def test_provider_registry_can_register_and_disable_provider() -> None:
    provider_id = "pytest-provider"
    payload = {
        "provider_id": provider_id,
        "provider_name": "Pytest Provider",
        "base_url": "http://pytest-provider:9999",
        "supported_models": ["pytest-model"],
        "weight": 1,
        "price_input_per_1k": 0.01,
        "price_output_per_1k": 0.02,
        "priority": 150,
        "enabled": True,
    }

    register_response = requests.post(
        f"{PROVIDER_REGISTRY_URL}/providers/register",
        json=payload,
        timeout=DEFAULT_TIMEOUT,
    )
    assert register_response.status_code == 201
    assert register_response.json()["provider_id"] == provider_id

    disable_response = requests.post(
        f"{PROVIDER_REGISTRY_URL}/providers/{provider_id}/disable",
        timeout=DEFAULT_TIMEOUT,
    )
    assert disable_response.status_code == 200
    assert disable_response.json()["enabled"] is False


def test_agent_registry_lists_initial_agents() -> None:
    response = requests.get(f"{AGENT_REGISTRY_URL}/agents", timeout=DEFAULT_TIMEOUT)

    assert response.status_code == 200
    agents = response.json()
    agent_ids = {agent["agent_id"] for agent in agents}
    assert {"summarizer-agent", "classifier-agent"} <= agent_ids


def test_agent_registry_can_register_new_agent_card() -> None:
    payload = {
        "agent_id": "pytest-agent",
        "name": "Pytest Agent",
        "description": "Synthetic agent used by integration tests.",
        "supported_methods": ["ping"],
        "endpoint_url": "http://pytest-agent:9998",
        "status": "active",
        "auth_required": False,
    }

    register_response = requests.post(
        f"{AGENT_REGISTRY_URL}/agents/register",
        json=payload,
        timeout=DEFAULT_TIMEOUT,
    )
    assert register_response.status_code == 201

    get_response = requests.get(
        f"{AGENT_REGISTRY_URL}/agents/pytest-agent",
        timeout=DEFAULT_TIMEOUT,
    )
    assert get_response.status_code == 200
    assert get_response.json()["supported_methods"] == ["ping"]
