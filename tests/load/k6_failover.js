import http from "k6/http";
import { check, sleep } from "k6";

export const options = {
  vus: 5,
  duration: "20s",
  thresholds: {
    http_req_failed: ["rate<0.05"],
    http_req_duration: ["p(95)<2500"],
  },
};

const baseUrl = __ENV.BASE_URL || "http://api-gateway:8000";
const providerAdminUrl =
  __ENV.MOCK_OPENAI_ADMIN_URL || "http://mock-provider-openai:8101";
const providerRegistryUrl =
  __ENV.PROVIDER_REGISTRY_URL || "http://provider-registry:8002";
const clientToken = __ENV.PLATFORM_API_TOKEN || "agenthub-client-token";
const adminToken = __ENV.ADMIN_API_TOKEN || "agenthub-admin-token";

export function setup() {
  const enableFailure = http.post(
    `${providerAdminUrl}/admin/failure-mode`,
    JSON.stringify({ enabled: true, status_code: 503 }),
    {
      headers: {
        Authorization: `Bearer ${adminToken}`,
        "Content-Type": "application/json",
      },
    },
  );

  check(enableFailure, {
    "failure mode enabled": (res) => res.status === 200,
  });
}

export default function () {
  const payload = JSON.stringify({
    model: "shared-demo-model",
    stream: false,
    messages: [{ role: "user", content: "k6 failover load test" }],
  });

  const response = http.post(`${baseUrl}/v1/chat/completions`, payload, {
    headers: {
      Authorization: `Bearer ${clientToken}`,
      "Content-Type": "application/json",
    },
    timeout: "60s",
  });

  check(response, {
    "failover load returns 200": (res) => res.status === 200,
    "fallback provider selected": (res) =>
      res.headers["X-Selected-Provider"] === "mock-anthropic",
  });

  const registryResponse = http.get(
    `${providerRegistryUrl}/providers/mock-openai`,
    {
      headers: { Authorization: `Bearer ${adminToken}` },
      timeout: "30s",
    },
  );

  check(registryResponse, {
    "provider registry remains reachable": (res) => res.status === 200,
    "openai provider marked unhealthy": (res) =>
      JSON.parse(res.body).health_status === "unhealthy",
  });

  sleep(1);
}

export function teardown() {
  http.post(
    `${providerAdminUrl}/admin/failure-mode`,
    JSON.stringify({ enabled: false, status_code: 503 }),
    {
      headers: {
        Authorization: `Bearer ${adminToken}`,
        "Content-Type": "application/json",
      },
    },
  );

  http.post(
    `${providerRegistryUrl}/providers/mock-openai/enable`,
    null,
    {
      headers: {
        Authorization: `Bearer ${adminToken}`,
      },
    },
  );
}
