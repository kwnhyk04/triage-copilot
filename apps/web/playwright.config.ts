// Browser tests (`make test-e2e`): Chromium against the built web (`next start`) and a fake
// gateway, all on 127.0.0.1. A taken port stops the run instead of reusing a stranger's server.
import { randomBytes } from 'node:crypto';

import { defineConfig, devices } from '@playwright/test';

import { E2E } from './e2e/shared.ts';
import { hashPassword } from './src/lib/password.ts';

// Made at load, so no hash or secret is committed. Workers load this file again; only the main
// process's values reach the servers.
const hash = await hashPassword(E2E.password);
const secret = randomBytes(32).toString('base64url');
const inherited = Object.fromEntries(
  Object.entries(process.env).filter((entry): entry is [string, string] => entry[1] !== undefined),
);
const env = (gatewayUrl: string) => ({
  ...inherited,
  NODE_ENV: 'production',
  NEXT_TELEMETRY_DISABLED: '1',
  GATEWAY_URL: gatewayUrl,
  GATEWAY_API_KEY: E2E.apiKey,
  APPROVER_API_KEY: E2E.approverKey,
  LOGIN_PASSWORD_HASH: hash,
  SESSION_SECRET: secret,
});
const web = (port: number, gatewayUrl: string) => ({
  command: `pnpm exec next start --hostname 127.0.0.1 --port ${String(port)}`,
  url: `http://127.0.0.1:${String(port)}/healthz`,
  env: env(gatewayUrl),
  reuseExistingServer: false,
  timeout: 120_000,
});
const signedIn = { ...devices['Desktop Chrome'], storageState: E2E.session };

export default defineConfig({
  testDir: 'e2e',
  outputDir: 'test-results/output',
  reporter: 'list',
  // One worker: the tests share the fake gateway's state.
  workers: 1,
  fullyParallel: false,
  retries: 0,
  use: { baseURL: E2E.webUrl, trace: 'off', screenshot: 'off' },
  projects: [
    { name: 'setup', testMatch: /auth\.setup\.ts/ },
    {
      name: 'chromium',
      testMatch: /(auth|reports|demo)\.spec\.ts/,
      dependencies: ['setup'],
      use: signedIn,
    },
    { name: 'a11y', testMatch: /a11y\.spec\.ts/, dependencies: ['setup'], use: signedIn },
  ],
  webServer: [
    {
      command: 'node e2e/fake-gateway.ts',
      url: `${E2E.gatewayUrl}/healthz`,
      env: { ...inherited, ...env(E2E.gatewayUrl) },
      reuseExistingServer: false,
    },
    web(E2E.webPort, E2E.gatewayUrl),
    web(E2E.isolatedPort, E2E.closedUrl),
  ],
});
