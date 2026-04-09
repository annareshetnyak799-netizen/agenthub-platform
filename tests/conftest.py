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
