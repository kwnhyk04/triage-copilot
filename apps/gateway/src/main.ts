import { buildApp, createUpdates } from './app.ts';
import { type GatewayConfig, loadGatewayConfig } from './config.ts';
import { connectDb } from './db.ts';
import { createDecide } from './decisions.ts';
import { startEventsConsumer, storeEvent } from './events.ts';
import { createClaim, createReport } from './executions.ts';
import { createFindTenant, createIntake } from './intake.ts';
import { buildInternalApp } from './internal.ts';
import { createAlertContext, createListInvestigations } from './investigations.ts';
import { sqsClient, sqsSender, startRelay } from './outbox.ts';
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
// 5 s per request, or 30 s for the events consumer, whose long polls wait up to 20 s.
const sqs = sqsClient(config, 5000);
const eventsSqs = sqsClient(config, 30_000);
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
  intake: createIntake(
    db.sql,
    nudge,
    config.MAX_NEW_INVESTIGATIONS,
    config.MAX_DAILY_INVESTIGATIONS,
  ),
  listInvestigations: createListInvestigations(db.sql),
});
const internal = buildInternalApp({
  config,
  claim: createClaim(db.sql),
  report: createReport(db.sql),
  alertContext: createAlertContext(db.sql),
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
const events = startEventsConsumer(
  eventsSqs,
  { queue: config.AGENT_EVENTS_QUEUE, dlq: config.AGENT_EVENTS_DLQ },
  (type, body) => storeEvent(db.sql, type, body),
  app.log,
);

for (const signal of ['SIGINT', 'SIGTERM'] as const) {
  process.once(signal, () => {
    void Promise.all([app.close(), internal.close()]).then(async () => {
      await sweeper.stop();
      await relay.stop();
      await events.stop();
      sqs.destroy();
      eventsSqs.destroy();
      await db.sql.end({ timeout: 5 });
    });
  });
}

await app.listen({ host: config.GATEWAY_HOST, port: config.GATEWAY_PORT });
await internal.listen({ host: config.GATEWAY_INTERNAL_HOST, port: config.GATEWAY_INTERNAL_PORT });
