// Operator commands:
//   pnpm --filter @triage/gateway tenant:token <tenant name>   issues the tenant's webhook token
//   pnpm --filter @triage/gateway outbox:replay                re-queues parked outbox messages
//   pnpm --filter @triage/gateway events:replay                moves the agent events DLQ back
//   pnpm --filter @triage/gateway digest [YYYY-MM-DD]           a UTC day's uninvestigated groups
import { randomBytes } from 'node:crypto';

import { loadConfig, z } from '@triage/ts-config';

import { connectDb } from './db.ts';
import { replayDeadLetters } from './events.ts';
import { sha256 } from './intake.ts';
import { createDigest } from './investigations.ts';
import { replayParked, sqsClient } from './outbox.ts';

const settings = z.object({
  GATEWAY_DATABASE_URL: z.url(),
  SQS_ENDPOINT_URL: z.url().optional(),
  AWS_REGION: z.string().min(1).default('us-east-1'),
  AGENT_EVENTS_QUEUE: z.string().min(1).default('agent-events.fifo'),
  AGENT_EVENTS_DLQ: z.string().min(1).default('agent-events-dlq.fifo'),
});
let config: z.infer<typeof settings>;
try {
  config = loadConfig(settings);
} catch (error) {
  console.error(`gateway: ${(error as Error).message}`);
  process.exit(1);
}

const [command, name] = process.argv.slice(2);
const { sql } = connectDb(config.GATEWAY_DATABASE_URL);
try {
  if (command === 'outbox-replay') {
    // The running gateway's relay sends them.
    console.log(
      JSON.stringify({ msg: 'outbox messages re-queued', count: await replayParked(sql) }),
    );
  } else if (command === 'events-replay') {
    // The running gateway's events consumer stores them.
    const sqs = sqsClient(config, 5000);
    try {
      const queues = { queue: config.AGENT_EVENTS_QUEUE, dlq: config.AGENT_EVENTS_DLQ };
      const count = await replayDeadLetters(sqs, queues);
      console.log(JSON.stringify({ msg: 'agent events re-queued', count }));
    } finally {
      sqs.destroy();
    }
  } else if (command === 'digest' && (name === undefined || /^\d{4}-\d\d-\d\d$/.test(name))) {
    // Today (UTC) unless a day is given.
    const day = name ?? new Date().toISOString().slice(0, 10);
    console.log(JSON.stringify(await createDigest(sql)(day), null, 2));
  } else if (command === 'tenant-token' && name) {
    // Creates the tenant if it is new and replaces any earlier token; only the hash is stored.
    const token = randomBytes(32).toString('base64url');
    const [tenant] = await sql<{ id: string }[]>`
      INSERT INTO gateway.tenants (name, webhook_token_sha256) VALUES (${name}, ${sha256(token)})
      ON CONFLICT (name) DO UPDATE SET webhook_token_sha256 = excluded.webhook_token_sha256
      RETURNING id`;
    console.error(`tenant ${name} (${tenant?.id ?? '?'}): new webhook token, shown only once:`);
    console.log(token);
  } else {
    console.error(
      'usage: node src/cli.ts tenant-token <tenant name> | outbox-replay | events-replay' +
        ' | digest [YYYY-MM-DD]',
    );
    process.exitCode = 2;
  }
} finally {
  await sql.end();
}
