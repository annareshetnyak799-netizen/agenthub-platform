import time

import requests

from tests.helpers import (
    DEFAULT_TIMEOUT,
    GATEWAY_URL,
    MOCK_OPENAI_URL,
    PROVIDER_REGISTRY_URL,
)


def test_health_aware_failover_ejects_failing_provider(
    reset_openai_failure_mode,
) -> None:
    enable_failure_response = requests.post(
        f"{MOCK_OPENAI_URL}/admin/failure-mode",
        json={"enabled": True, "status_code": 503},
        timeout=DEFAULT_TIMEOUT,
    )
    assert enable_failure_response.status_code == 200

    route_payload = {
        "model": "shared-demo-model",
        "stream": False,
        "messages": [{"role": "user", "content": "Failover integration test"}],
    }

    provider_record = None
    last_response = None
    for _ in range(6):
        last_response = requests.post(
            f"{GATEWAY_URL}/v1/chat/completions",
            json=route_payload,
            timeout=DEFAULT_TIMEOUT,
        )
        assert last_response.status_code == 200

        provider_record_response = requests.get(
            f"{PROVIDER_REGISTRY_URL}/providers/mock-openai",
            timeout=DEFAULT_TIMEOUT,
        )
        assert provider_record_response.status_code == 200
        provider_record = provider_record_response.json()
        if provider_record["health_status"] == "unhealthy":
            break
        time.sleep(1)

    assert provider_record is not None
    assert provider_record["health_status"] == "unhealthy"
    assert provider_record["failure_count"] >= 1
    assert provider_record["cooldown_until"] is not None

    followup_response = requests.post(
        f"{GATEWAY_URL}/v1/chat/completions",
        json=route_payload,
        timeout=DEFAULT_TIMEOUT,
    )
    assert followup_response.status_code == 200
    assert followup_response.headers["x-selected-provider"] == "mock-anthropic"
