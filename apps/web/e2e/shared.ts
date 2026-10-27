// Shared by playwright.config.ts, the fake gateway and the specs: ports, keys and the one
// password. All are test-only values; the ports are this project's (docs/context.md).
const port = (name: string, fallback: number) => Number(process.env[name] ?? fallback);
const url = (value: number) => `http://127.0.0.1:${String(value)}`;

const webPort = port('E2E_WEB_PORT', 3202);
const isolatedPort = port('E2E_ISOLATED_PORT', 3203);
const gatewayPort = port('E2E_GATEWAY_PORT', 4250);

export const E2E = {
  password: 'browser-tests-password',
  apiKey: 'gateway-key-for-the-browser-tests-0000',
  approverKey: 'approver-key-for-the-browser-tests-000',
  webPort,
  isolatedPort,
  gatewayPort,
  webUrl: url(webPort),
  // Its own limiter and a gateway that never answers, so the limit and offline tests stay apart.
  isolatedUrl: url(isolatedPort),
  gatewayUrl: url(gatewayPort),
  // Nothing listens on the discard port.
  closedUrl: url(9),
  session: 'test-results/session.json',
  // The fake gateway's investigation that runs while it is read.
  runningId: 'c0ffee00-0000-4000-8000-000000000001',
};
