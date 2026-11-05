// One k6 script for every API, endpoint and phase.
//
//   PHASE=throughput  closed model: VUS virtual users, each sends its next
//                     request as soon as the previous answer arrives.
//                     Measures the most requests/s the API can serve.
//   PHASE=latency     open model: RATE requests/s start on schedule whether or
//                     not earlier ones have finished, so queueing shows up in
//                     the percentiles instead of slowing the load down.
//
// Env: BASE_URL, ENDPOINT, PHASE, DURATION, VUS, RATE, OUT (JSON result path).
import http from 'k6/http';
import { check } from 'k6';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8001';
const ENDPOINT = __ENV.ENDPOINT || 'health';
const PHASE = __ENV.PHASE || 'throughput';
const DURATION = __ENV.DURATION || '20s';
const OUT = __ENV.OUT || '';

// Concurrency for the throughput phase, the same for every API.
const ENDPOINTS = {
  health: { method: 'GET', path: '/health', vus: 64 },
  hash: { method: 'POST', path: '/hash', vus: 32 },
  json: { method: 'POST', path: '/json/transform', vus: 16 },
  primes: { method: 'GET', path: '/compute/primes?n=1000000', vus: 32 },
  users: { method: 'GET', path: '/users?limit=50', vus: 64 },
  fanout: { method: 'GET', path: '/fanout', vus: 256 },
};

const cfg = ENDPOINTS[ENDPOINT];
if (!cfg) throw new Error(`unknown ENDPOINT "${ENDPOINT}"`);

const BODIES = {
  hash: JSON.stringify({ password: 'correct-horse-battery-staple' }),
  json: ENDPOINT === 'json' ? open('/data/orders.json') : null,
};

function scenario() {
  if (PHASE === 'throughput') {
    return { executor: 'constant-vus', vus: Number(__ENV.VUS || cfg.vus), duration: DURATION };
  }
  if (PHASE === 'latency') {
    const rate = Number(__ENV.RATE);
    if (!(rate > 0)) throw new Error('PHASE=latency needs RATE > 0');
    // Enough virtual users for one second of requests in flight; k6 counts a
    // "dropped iteration" if it ever runs out, which the report shows.
    return {
      executor: 'constant-arrival-rate',
      rate,
      timeUnit: '1s',
      duration: DURATION,
      preAllocatedVUs: Math.max(10, Math.ceil(rate * 0.25)),
      maxVUs: Math.max(50, Math.ceil(rate)),
    };
  }
  throw new Error(`unknown PHASE "${PHASE}"`);
}

export const options = {
  scenarios: { main: scenario() },
  discardResponseBodies: true,
  summaryTrendStats: ['avg', 'p(50)', 'p(95)', 'p(99)', 'max'],
};

const params = { headers: { 'Content-Type': 'application/json' }, timeout: '60s' };

export default function () {
  const url = BASE_URL + cfg.path;
  const res = cfg.method === 'POST' ? http.post(url, BODIES[ENDPOINT], params) : http.get(url, params);
  check(res, { 'status is 200': (r) => r.status === 200 });
}

function metric(data, name, stat, fallback = 0) {
  const m = data.metrics[name];
  return m && m.values && m.values[stat] !== undefined ? m.values[stat] : fallback;
}

export function handleSummary(data) {
  const result = {
    endpoint: ENDPOINT,
    phase: PHASE,
    vus: PHASE === 'throughput' ? Number(__ENV.VUS || cfg.vus) : null,
    targetRate: PHASE === 'latency' ? Number(__ENV.RATE) : null,
    requests: metric(data, 'http_reqs', 'count'),
    rps: metric(data, 'http_reqs', 'rate'),
    errorRate: metric(data, 'http_req_failed', 'rate'),
    droppedIterations: metric(data, 'dropped_iterations', 'count'),
    latencyMs: {
      avg: metric(data, 'http_req_duration', 'avg'),
      p50: metric(data, 'http_req_duration', 'p(50)'),
      p95: metric(data, 'http_req_duration', 'p(95)'),
      p99: metric(data, 'http_req_duration', 'p(99)'),
      max: metric(data, 'http_req_duration', 'max'),
    },
  };
  const l = result.latencyMs;
  const line =
    `  ${ENDPOINT.padEnd(7)} ${PHASE.padEnd(10)} ${result.rps.toFixed(1).padStart(9)} req/s` +
    ` | p50 ${l.p50.toFixed(2)} ms | p99 ${l.p99.toFixed(2)} ms` +
    ` | errors ${(result.errorRate * 100).toFixed(2)}%` +
    (result.droppedIterations ? ` | dropped ${result.droppedIterations}` : '') +
    '\n';
  const out = { stdout: line };
  if (OUT) out[OUT] = JSON.stringify(result, null, 2);
  return out;
}
