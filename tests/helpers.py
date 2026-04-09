GATEWAY_URL = "http://localhost:8000"
ROUTER_URL = "http://localhost:8005"
PROVIDER_REGISTRY_URL = "http://localhost:8006"
AGENT_REGISTRY_URL = "http://localhost:8007"
MOCK_OPENAI_URL = "http://localhost:8101"

DEFAULT_TIMEOUT = 10
PLATFORM_API_TOKEN = "agenthub-client-token"
ADMIN_API_TOKEN = "agenthub-admin-token"


def gateway_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {PLATFORM_API_TOKEN}"}


def admin_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {ADMIN_API_TOKEN}"}
