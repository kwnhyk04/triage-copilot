// Operator commands:
//   pnpm --filter @triage/gateway tenant:token <tenant name>   issues the tenant's webhook token
//   pnpm --filter @triage/gateway outbox:replay                re-queues parked outbox messages
import { randomBytes } from 'node:crypto';

import { loadConfig, z } from '@triage/ts-config';

import { connectDb } from './db.ts';
import { sha256 } from './intake.ts';
import { replayParked } from './outbox.ts';

let url: string;
try {
  url = loadConfig(z.object({ GATEWAY_DATABASE_URL: z.url() })).GATEWAY_DATABASE_URL;
} catch (error) {
  console.error(`gateway: ${(error as Error).message}`);
  process.exit(1);
}

const [command, name] = process.argv.slice(2);
const { sql } = connectDb(url);
try {
  if (command === 'outbox-replay') {
    // The running gateway's relay sends them.
    console.log(
      JSON.stringify({ msg: 'outbox messages re-queued', count: await replayParked(sql) }),
    );
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
    console.error('usage: node src/cli.ts tenant-token <tenant name> | outbox-replay');
    process.exitCode = 2;
  }
} finally {
  await sql.end();
}
