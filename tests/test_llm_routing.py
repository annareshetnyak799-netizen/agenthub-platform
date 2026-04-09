import requests

from tests.helpers import DEFAULT_TIMEOUT, GATEWAY_URL


def test_non_stream_chat_completion_returns_provider_response(shared_demo_payload: dict) -> None:
    response = requests.post(
        f"{GATEWAY_URL}/v1/chat/completions",
        json=shared_demo_payload,
        timeout=DEFAULT_TIMEOUT,
    )

    assert response.status_code == 200
    assert response.headers["x-selected-provider"] in {"mock-openai", "mock-anthropic"}

    payload = response.json()
    assert payload["model"] == "shared-demo-model"
    assert payload["provider"] in {"mock-openai", "mock-anthropic"}
    assert payload["choices"][0]["message"]["content"]
    assert payload["usage"]["total_tokens"] > 0


def test_streaming_chat_completion_emits_sse_chunks() -> None:
    payload = {
        "model": "shared-demo-model",
        "stream": True,
        "messages": [{"role": "user", "content": "Streaming integration test"}],
    }

    with requests.post(
        f"{GATEWAY_URL}/v1/chat/completions",
        json=payload,
        timeout=DEFAULT_TIMEOUT,
        stream=True,
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")

        chunks = [
            line.decode("utf-8")
            for line in response.iter_lines()
            if line
        ]

    assert any(line.startswith("data: {") for line in chunks)
    assert "data: [DONE]" in chunks
