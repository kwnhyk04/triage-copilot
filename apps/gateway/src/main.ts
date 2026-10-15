import { SQSClient } from '@aws-sdk/client-sqs';

import { buildApp, createUpdates } from './app.ts';
import { type GatewayConfig, loadGatewayConfig } from './config.ts';
import { connectDb } from './db.ts';
import { createDecide } from './decisions.ts';
import { createClaim, createReport } from './executions.ts';
import { createFindTenant, createIntake } from './intake.ts';
import { buildInternalApp } from './internal.ts';
import { createListInvestigations } from './investigations.ts';
import { sqsSender, startRelay } from './outbox.ts';
import { agentStates, startSweeper } from './sweeper.ts';

let config: GatewayConfig;
try {
  config = loadGatewayConfig();
} catch (error) {
  // One line naming each bad variable instead of a stack trace.
  console.error(`gateway: ${(error as Error).message}`);
  process.exit(1);
}

const db = connectDb(config.GATEWAY_DATABASE_URL);
// Bounded like every external call: 3 s to connect, 5 s per request, two attempts.
const sqs = new SQSClient({
  region: config.AWS_REGION,
  ...(config.SQS_ENDPOINT_URL === undefined ? {} : { endpoint: config.SQS_ENDPOINT_URL }),
  maxAttempts: 2,
  requestHandler: { connectionTimeout: 3000, requestTimeout: 5000 },
});
// The relay needs the app's logger, so intake and decisions reach it through this once it exists.
let nudgeRelay: () => void = () => undefined;
const nudge = () => {
  nudgeRelay();
};
const app = await buildApp({
  config,
  ping: db.ping,
  updates: createUpdates(),
  decide: createDecide(db.sql, nudge),
  findTenant: createFindTenant(db.sql),
  intake: createIntake(db.sql, nudge, config.MAX_NEW_INVESTIGATIONS),
  listInvestigations: createListInvestigations(db.sql),
});
const internal = buildInternalApp({
  config,
  claim: createClaim(db.sql),
  report: createReport(db.sql),
});
const senders = {
  investigations: sqsSender(sqs, config.INVESTIGATIONS_QUEUE),
  actions: sqsSender(sqs, config.ACTIONS_QUEUE),
};
const relay = startRelay(db.sql, (message) => senders[message.queue](message), app.log);
nudgeRelay = relay.nudge;
const sweeper = startSweeper(
  db.sql,
  agentStates(config.AGENT_URL, config.AGENT_SERVICE_TOKEN),
  {
    stuckAfterMinutes: config.SWEEPER_STUCK_AFTER_MINUTES,
    intervalMs: config.SWEEPER_INTERVAL_SECONDS * 1000,
  },
  app.log,
  relay.nudge,
);

for (const signal of ['SIGINT', 'SIGTERM'] as const) {
  process.once(signal, () => {
    void Promise.all([app.close(), internal.close()]).then(async () => {
      await sweeper.stop();
      await relay.stop();
      sqs.destroy();
      await db.sql.end({ timeout: 5 });
    });
  });
}

await app.listen({ host: config.GATEWAY_HOST, port: config.GATEWAY_PORT });
await internal.listen({ host: config.GATEWAY_INTERNAL_HOST, port: config.GATEWAY_INTERNAL_PORT });
