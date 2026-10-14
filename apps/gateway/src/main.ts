import { buildApp, createUpdates, type Decide } from './app.ts';
import { type GatewayConfig, loadGatewayConfig } from './config.ts';
import { connectDb } from './db.ts';

let config: GatewayConfig;
try {
  config = loadGatewayConfig();
} catch (error) {
  // One line naming each bad variable instead of a stack trace.
  console.error(`gateway: ${(error as Error).message}`);
  process.exit(1);
}

const db = connectDb(config.GATEWAY_DATABASE_URL);
// No action is stored before build step 3 adds the table, so every action id is unknown.
const decide: Decide = () => Promise.resolve('not_found');
const app = buildApp({ config, ping: db.ping, updates: createUpdates(), decide });

for (const signal of ['SIGINT', 'SIGTERM'] as const) {
  process.once(signal, () => {
    void app.close().then(() => db.sql.end({ timeout: 5 }));
  });
}

await app.listen({ host: config.GATEWAY_HOST, port: config.GATEWAY_PORT });
