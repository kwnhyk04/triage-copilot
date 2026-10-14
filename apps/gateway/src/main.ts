import { SQSClient } from '@aws-sdk/client-sqs';

import { buildApp, createUpdates, type Decide } from './app.ts';
import { type GatewayConfig, loadGatewayConfig } from './config.ts';
import { connectDb } from './db.ts';
import { createFindTenant, createIntake } from './intake.ts';
import { sqsSender, startRelay } from './outbox.ts';

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
// Bounded like every external call: 3 s to connect, 5 s per request, two attempts.
const sqs = new SQSClient({
  region: config.AWS_REGION,
  ...(config.SQS_ENDPOINT_URL === undefined ? {} : { endpoint: config.SQS_ENDPOINT_URL }),
  maxAttempts: 2,
  requestHandler: { connectionTimeout: 3000, requestTimeout: 5000 },
});
// The relay needs the app's logger, so intake reaches it through this once it exists.
let nudgeRelay: () => void = () => undefined;
const app = await buildApp({
  config,
  ping: db.ping,
  updates: createUpdates(),
  decide,
  findTenant: createFindTenant(db.sql),
  intake: createIntake(
    db.sql,
    () => {
      nudgeRelay();
    },
    config.MAX_NEW_INVESTIGATIONS,
  ),
});
const relay = startRelay(db.sql, sqsSender(sqs, config.INVESTIGATIONS_QUEUE), app.log);
nudgeRelay = relay.nudge;

for (const signal of ['SIGINT', 'SIGTERM'] as const) {
  process.once(signal, () => {
    void app.close().then(async () => {
      await relay.stop();
      sqs.destroy();
      await db.sql.end({ timeout: 5 });
    });
  });
}

await app.listen({ host: config.GATEWAY_HOST, port: config.GATEWAY_PORT });
