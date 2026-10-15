import { connection } from 'next/server';

import { loadWebConfig } from '../lib/config.ts';
import { gatewayStatus } from '../lib/gateway.ts';

export default async function Home() {
  // Render per request: gateway status is live data and GATEWAY_URL exists only at runtime.
  await connection();
  const status = await gatewayStatus(loadWebConfig());
  return (
    <main>
      <h1>Triage Copilot</h1>
      <p>Gateway: {status}</p>
    </main>
  );
}
