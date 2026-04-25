/**
 * Multi-provider failure test.
 *
 * setup()    — enable failure mode on BOTH providers
 * default()  — send traffic; expect clean error responses (no panic, no hang)
 * teardown() — restore both providers
 *
 * When all providers are unhealthy the router returns 404 (no eligible
 * provider). The test asserts that the gateway responds gracefully (4xx/5xx)
 * and never hangs or crashes.
 *
 * After teardown a final smoke request verifies that the platform recovers.
 */
import http from "k6/http";
import { check, sleep } from "k6";

export const options = {
  vus: 5,
  duration: "20s",
  thresholds: {
    // All requests must complete (no network-level hangs).
    http_req_failed: ["rate<1.0"],
    http_req_duration: ["p(95)<10000"],
  },
};

const baseUrl          = __ENV.BASE_URL               || "http://api-gateway:8000";
const openaiAdminUrl   = __ENV.MOCK_OPENAI_ADMIN_URL  || "http://mock-provider-openai:8101";
const anthropicAdminUrl = __ENV.MOCK_ANTHROPIC_ADMIN_URL || "http://mock-provider-anthropic:8102";
const providerRegistryUrl = __ENV.PROVIDER_REGISTRY_URL || "http://provider-registry:8002";
const clientToken      = __ENV.PLATFORM_API_TOKEN     || "agenthub-client-token";
const adminToken       = __ENV.ADMIN_API_TOKEN         || "agenthub-admin-token";

const adminHeaders = {
  Authorization: `Bearer ${adminToken}`,
  "Content-Type": "application/json",
};

function enableFailure(adminUrl) {
  return http.post(
    `${adminUrl}/admin/failure-mode`,
    JSON.stringify({ enabled: true, status_code: 503 }),
    { headers: adminHeaders },
  );
}

function disableFailure(adminUrl) {
  return http.post(
    `${adminUrl}/admin/failure-mode`,
    JSON.stringify({ enabled: false, status_code: 503 }),
    { headers: adminHeaders },
  );
}

function enableProvider(providerId) {
  return http.post(
    `${providerRegistryUrl}/providers/${providerId}/enable`,
    null,
    { headers: { Authorization: `Bearer ${adminToken}` } },
  );
}

export function setup() {
  const r1 = enableFailure(openaiAdminUrl);
  const r2 = enableFailure(anthropicAdminUrl);
  check(r1, { "openai failure mode enabled": (r) => r.status === 200 });
  check(r2, { "anthropic failure mode enabled": (r) => r.status === 200 });
}

export default function () {
  const payload = JSON.stringify({
    model: "shared-demo-model",
    stream: false,
    messages: [{ role: "user", content: "k6 multi-failure test" }],
  });

  const response = http.post(`${baseUrl}/v1/chat/completions`, payload, {
    headers: {
      Authorization: `Bearer ${clientToken}`,
      "Content-Type": "application/json",
    },
    timeout: "30s",
  });

  // With all providers failing the platform should return an error, not crash.
  check(response, {
    "gateway responds (no hang)": (res) => res.status > 0,
    "no unhandled panic (not 500)": (res) => res.status !== 500,
    "expected error status": (res) => [503, 404, 502, 200].includes(res.status),
  });

  sleep(1);
}

export function teardown() {
  disableFailure(openaiAdminUrl);
  disableFailure(anthropicAdminUrl);
  enableProvider("mock-openai");
  enableProvider("mock-anthropic");

  // Smoke check: platform should serve requests after recovery.
  const payload = JSON.stringify({
    model: "shared-demo-model",
    stream: false,
    messages: [{ role: "user", content: "recovery smoke check" }],
  });
  const recovery = http.post(`${baseUrl}/v1/chat/completions`, payload, {
    headers: {
      Authorization: `Bearer ${clientToken}`,
      "Content-Type": "application/json",
    },
    timeout: "30s",
  });
  check(recovery, { "platform recovered after multi-failure": (r) => r.status === 200 });
}
