import http from "k6/http";
import { check, sleep } from "k6";

export const options = {
  vus: 10,
  duration: "30s",
  thresholds: {
    http_req_failed: ["rate<0.05"],
    http_req_duration: ["p(95)<2000"],
  },
};

const baseUrl = __ENV.BASE_URL || "http://api-gateway:8000";
const clientToken = __ENV.PLATFORM_API_TOKEN || "agenthub-client-token";

export default function () {
  const payload = JSON.stringify({
    model: "shared-demo-model",
    stream: false,
    messages: [{ role: "user", content: "k6 baseline chat load test" }],
  });

  const response = http.post(`${baseUrl}/v1/chat/completions`, payload, {
    headers: {
      Authorization: `Bearer ${clientToken}`,
      "Content-Type": "application/json",
    },
  });

  check(response, {
    "chat load returns 200": (res) => res.status === 200,
    "provider header is present": (res) => !!res.headers["X-Selected-Provider"],
  });

  sleep(1);
}
