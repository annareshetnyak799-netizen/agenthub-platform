/**
 * Ramp-up load test.
 *
 * Stages:
 *   0 →  20 VUs over 30 s  — warm-up ramp
 *  20 →  20 VUs for  60 s  — sustained peak
 *  20 →   0 VUs over 15 s  — ramp-down
 *
 * Thresholds are intentionally tight so regressions are visible.
 */
import http from "k6/http";
import { check, sleep } from "k6";

export const options = {
  stages: [
    { duration: "30s", target: 20 },
    { duration: "60s", target: 20 },
    { duration: "15s", target: 0 },
  ],
  thresholds: {
    http_req_failed: ["rate<0.05"],
    http_req_duration: ["p(95)<3000", "p(99)<5000"],
  },
};

const baseUrl = __ENV.BASE_URL || "http://api-gateway:8000";
const clientToken = __ENV.PLATFORM_API_TOKEN || "agenthub-client-token";

export default function () {
  const payload = JSON.stringify({
    model: "shared-demo-model",
    stream: false,
    messages: [{ role: "user", content: "k6 ramp-up load test" }],
  });

  const response = http.post(`${baseUrl}/v1/chat/completions`, payload, {
    headers: {
      Authorization: `Bearer ${clientToken}`,
      "Content-Type": "application/json",
    },
  });

  check(response, {
    "ramp load returns 200": (res) => res.status === 200,
    "provider header is present": (res) => !!res.headers["X-Selected-Provider"],
  });

  sleep(1);
}
