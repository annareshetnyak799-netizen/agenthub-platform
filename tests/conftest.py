import time
from typing import Iterator

import pytest
import requests

from tests.helpers import (
    AGENT_REGISTRY_URL,
    ADMIN_API_TOKEN,
    DEFAULT_TIMEOUT,
    GATEWAY_URL,
    MOCK_OPENAI_URL,
    PROVIDER_REGISTRY_URL,
    ROUTER_URL,
)

INITIAL_PROVIDERS = [
    {
        "provider_id": "mock-openai",
        "provider_name": "mock-openai",
        "base_url": "http://mock-provider-openai:8101",
        "supported_models": ["gpt-4o-mini", "shared-demo-model"],
        "weight": 1,
        "price_input_per_1k": 0.15,
        "price_output_per_1k": 0.60,
        "priority": 100,
        "enabled": True,
    },
    {
        "provider_id": "mock-anthropic",
        "provider_name": "mock-anthropic",
        "base_url": "http://mock-provider-anthropic:8102",
        "supported_models": ["claude-3-haiku", "shared-demo-model"],
        "weight": 1,
        "price_input_per_1k": 0.12,
        "price_output_per_1k": 0.48,
        "priority": 100,
        "enabled": True,
    },
]

INITIAL_AGENTS = [
    {
        "agent_id": "summarizer-agent",
        "name": "Summarizer Agent",
        "description": "Summarizes text and incident context.",
        "supported_methods": ["summarize_text", "summarize_incident"],
        "endpoint_url": "http://mock-agent-summarizer:8201",
        "status": "active",
        "auth_required": False,
    },
    {
        "agent_id": "classifier-agent",
        "name": "Classifier Agent",
        "description": "Classifies text topics and priorities.",
        "supported_methods": ["classify_topic", "classify_priority"],
        "endpoint_url": "http://mock-agent-classifier:8202",
        "status": "active",
        "auth_required": False,
    },
]


def wait_for_health(url: str, timeout_seconds: int = 60) -> None:
    deadline = time.time() + timeout_seconds
    last_error: Exception | None = None

    while time.time() < deadline:
        try:
            response = requests.get(url, timeout=3)
            if response.status_code == 200:
                return
        except requests.RequestException as exc:
            last_error = exc
        time.sleep(1)

    raise RuntimeError(f"Service did not become healthy: {url}") from last_error


@pytest.fixture(scope="session", autouse=True)
def wait_for_stack() -> None:
    wait_for_health(f"{GATEWAY_URL}/health")
    wait_for_health(f"{ROUTER_URL}/health")
    wait_for_health(f"{PROVIDER_REGISTRY_URL}/health")
    wait_for_health(f"{AGENT_REGISTRY_URL}/health")
    wait_for_health(f"{MOCK_OPENAI_URL}/health")


def _reset_provider_registry_state() -> None:
    response = requests.get(
        f"{PROVIDER_REGISTRY_URL}/providers",
        headers={"Authorization": f"Bearer {ADMIN_API_TOKEN}"},
        timeout=DEFAULT_TIMEOUT,
    )
    response.raise_for_status()

    for provider in response.json():
        delete_response = requests.delete(
            f"{PROVIDER_REGISTRY_URL}/providers/{provider['provider_id']}",
            headers={"Authorization": f"Bearer {ADMIN_API_TOKEN}"},
            timeout=DEFAULT_TIMEOUT,
        )
        assert delete_response.status_code == 204

    for provider in INITIAL_PROVIDERS:
        register_response = requests.post(
            f"{PROVIDER_REGISTRY_URL}/providers/register",
            json=provider,
            headers={"Authorization": f"Bearer {ADMIN_API_TOKEN}"},
            timeout=DEFAULT_TIMEOUT,
        )
        assert register_response.status_code == 201


def _reset_agent_registry_state() -> None:
    response = requests.get(
        f"{AGENT_REGISTRY_URL}/agents",
        headers={"Authorization": f"Bearer {ADMIN_API_TOKEN}"},
        timeout=DEFAULT_TIMEOUT,
    )
    response.raise_for_status()

    for agent in response.json():
        delete_response = requests.delete(
            f"{AGENT_REGISTRY_URL}/agents/{agent['agent_id']}",
            headers={"Authorization": f"Bearer {ADMIN_API_TOKEN}"},
            timeout=DEFAULT_TIMEOUT,
        )
        assert delete_response.status_code == 204

    for agent in INITIAL_AGENTS:
        register_response = requests.post(
            f"{AGENT_REGISTRY_URL}/agents/register",
            json=agent,
            headers={"Authorization": f"Bearer {ADMIN_API_TOKEN}"},
            timeout=DEFAULT_TIMEOUT,
        )
        assert register_response.status_code == 201


@pytest.fixture(scope="session", autouse=True)
def reset_registry_state() -> None:
    _reset_provider_registry_state()
    _reset_agent_registry_state()


@pytest.fixture()
def shared_demo_payload() -> dict:
    return {
        "model": "shared-demo-model",
        "stream": False,
        "messages": [{"role": "user", "content": "Integration test request"}],
    }


@pytest.fixture()
def reset_openai_failure_mode() -> Iterator[None]:
    requests.post(
        f"{MOCK_OPENAI_URL}/admin/failure-mode",
        json={"enabled": False, "status_code": 503},
        headers={"Authorization": f"Bearer {ADMIN_API_TOKEN}"},
        timeout=DEFAULT_TIMEOUT,
    )
    requests.post(
        f"{PROVIDER_REGISTRY_URL}/providers/mock-openai/enable",
        headers={"Authorization": f"Bearer {ADMIN_API_TOKEN}"},
        timeout=DEFAULT_TIMEOUT,
    )
    yield
    requests.post(
        f"{MOCK_OPENAI_URL}/admin/failure-mode",
        json={"enabled": False, "status_code": 503},
        headers={"Authorization": f"Bearer {ADMIN_API_TOKEN}"},
        timeout=DEFAULT_TIMEOUT,
    )
    requests.post(
        f"{PROVIDER_REGISTRY_URL}/providers/mock-openai/enable",
        headers={"Authorization": f"Bearer {ADMIN_API_TOKEN}"},
        timeout=DEFAULT_TIMEOUT,
    )
