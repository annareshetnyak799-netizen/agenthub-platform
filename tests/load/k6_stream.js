import http from "k6/http";
import { check, sleep } from "k6";

export const options = {
  vus: 5,
  duration: "20s",
  thresholds: {
    http_req_failed: ["rate<0.05"],
    http_req_duration: ["p(95)<3000"],
  },
};

const baseUrl = __ENV.BASE_URL || "http://api-gateway:8000";
const clientToken = __ENV.PLATFORM_API_TOKEN || "agenthub-client-token";

export default function () {
  const payload = JSON.stringify({
    model: "shared-demo-model",
    stream: true,
    messages: [{ role: "user", content: "k6 streaming load test" }],
  });

  const response = http.post(`${baseUrl}/v1/chat/completions`, payload, {
    headers: {
      Authorization: `Bearer ${clientToken}`,
      "Content-Type": "application/json",
    },
    timeout: "60s",
  });

  check(response, {
    "streaming load returns 200": (res) => res.status === 200,
    "stream contains done marker": (res) =>
      typeof res.body === "string" && res.body.includes("data: [DONE]"),
  });

  sleep(1);
}
