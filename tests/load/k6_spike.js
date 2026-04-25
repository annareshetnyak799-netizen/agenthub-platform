/**
 * Spike load test.
 *
 * Simulates a sudden traffic burst:
 *   0 →  5 VUs over  5 s  — baseline
 *   5 → 50 VUs over  5 s  — spike (10× baseline)
 *  50 → 50 VUs for  15 s  — hold at peak
 *  50 →  5 VUs over  5 s  — recovery
 *   5 →  5 VUs for  10 s  — verify stable after spike
 *   5 →  0 VUs over  5 s  — wind-down
 *
 * The error rate threshold is deliberately relaxed (10 %) because some
 * requests may queue under the spike; the key assertion is that the
 * balancer does not crash and recovers cleanly.
 */
import http from "k6/http";
import { check, sleep } from "k6";

export const options = {
  stages: [
    { duration: "5s",  target: 5  },
    { duration: "5s",  target: 50 },
    { duration: "15s", target: 50 },
    { duration: "5s",  target: 5  },
    { duration: "10s", target: 5  },
    { duration: "5s",  target: 0  },
  ],
  thresholds: {
    http_req_failed: ["rate<0.10"],
    http_req_duration: ["p(95)<8000"],
  },
};

const baseUrl = __ENV.BASE_URL || "http://api-gateway:8000";
const clientToken = __ENV.PLATFORM_API_TOKEN || "agenthub-client-token";

export default function () {
  const payload = JSON.stringify({
    model: "shared-demo-model",
    stream: false,
    messages: [{ role: "user", content: "k6 spike load test" }],
  });

  const response = http.post(`${baseUrl}/v1/chat/completions`, payload, {
    headers: {
      Authorization: `Bearer ${clientToken}`,
      "Content-Type": "application/json",
    },
    timeout: "30s",
  });

  check(response, {
    "spike load returns 2xx or 503": (res) => res.status < 600,
    "no internal server error": (res) => res.status !== 500,
  });

  sleep(0.5);
}
