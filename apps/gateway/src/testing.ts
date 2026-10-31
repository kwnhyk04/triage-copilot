// Shared by the integration suites (*.int.test.ts): needs `make infra-up` and the .env variables.
//
// Tests never touch the dev database. Each scratch database is named SCRATCH_PREFIX, this
// process's run id and its own id, and its suite drops it. One over an hour old was left by a
// crashed run and is dropped by the next; nothing without the prefix is ever dropped. Importing this
// module points GATEWAY_DATABASE_URL at a database that does not exist, so no test reaches the dev
// database by accident.
import { randomBytes, randomUUID } from 'node:crypto';
import { fileURLToPath } from 'node:url';

import { loadConfig, z } from '@triage/ts-config';
import { drizzle } from 'drizzle-orm/postgres-js';
import { migrate } from 'drizzle-orm/postgres-js/migrator';
import postgres, { type Sql } from 'postgres';

import { fingerprint, sha256, webhookSchema } from './intake.ts';

const config = loadConfig(
  z.object({
    GATEWAY_DATABASE_URL: z.url(),
    POSTGRES_PASSWORD: z.string().min(1),
    SQS_ENDPOINT_URL: z.url(),
    AWS_REGION: z.string().default('us-east-1'),
  }),
);
export const env = { SQS_ENDPOINT_URL: config.SQS_ENDPOINT_URL, AWS_REGION: config.AWS_REGION };
export const SCRATCH_PREFIX = 'triage_test_';
export const REFUSED_DATABASE = 'tests_never_use_the_dev_database';
// Unix time this process started, so a later run can tell a stale database from one in use.
const RUN_ID = `${String(Math.floor(Date.now() / 1000))}_${randomBytes(3).toString('hex')}`;
const STALE_AFTER_SECONDS = 3600;
// SCRATCH_PREFIX, start time, run id and database id, as scratchDb names them.
const SCRATCH_NAME = /^triage_test_(\d+)_[0-9a-f]{6}_[0-9a-f]{8}$/;
const devDatabase = new URL(config.GATEWAY_DATABASE_URL).pathname.slice(1);
const quiet = { onnotice: () => undefined };

/** A URL for one database on the dev server, as the gateway role or the superuser. */
const at = (database: string, superuser = false) => {
  const url = new URL(config.GATEWAY_DATABASE_URL);
  url.pathname = `/${database}`;
  if (superuser) {
    url.username = 'postgres';
    url.password = config.POSTGRES_PASSWORD;
  }
  return url.href;
};
process.env.GATEWAY_DATABASE_URL = at(REFUSED_DATABASE);

/** Keys for apps built in these tests; the gateway process gets them from its own environment. */
export const APP_CONFIG = {
  LOG_LEVEL: 'error',
  APPROVER_API_KEY: 'approver-key-for-these-tests-00000000',
  GATEWAY_API_KEY: 'gateway-key-for-these-tests-000000000',
  RATE_LIMIT_PER_MINUTE: 600,
  AUTH_FAILURES_PER_MINUTE: 20,
} as const;

/** Drops prefixed databases over STALE_AFTER_SECONDS old, which crashed runs left behind. */
async function dropStale(admin: Sql) {
  const names = await admin<{ datname: string }[]>`
    SELECT datname FROM pg_database WHERE starts_with(datname, ${SCRATCH_PREFIX})`;
  for (const { datname } of names) {
    // Only names this file makes; a hand-made triage_test_ database is never touched.
    const started = SCRATCH_NAME.exec(datname)?.[1];
    if (started === undefined || datname === devDatabase) continue;
    if (Date.now() / 1000 - Number(started) > STALE_AFTER_SECONDS) {
      // IF EXISTS: another suite of this run may drop it first.
      await admin.unsafe(`DROP DATABASE IF EXISTS "${datname.replaceAll('"', '""')}" WITH (FORCE)`);
    }
  }
}
let staleDropped: Promise<void> | undefined;

/** An empty database set up like infra/postgres-init.sql, with the gateway migrations applied. */
export async function scratchDb() {
  if (devDatabase.startsWith(SCRATCH_PREFIX)) {
    throw new Error(`refusing to run: the dev database ${devDatabase} has the scratch prefix`);
  }
  const name = `${SCRATCH_PREFIX}${RUN_ID}_${randomBytes(4).toString('hex')}`;
  const admin = postgres(at('postgres', true), { max: 1, ...quiet });
  await (staleDropped ??= dropStale(admin));
  await admin.unsafe(`CREATE DATABASE ${name}`);
  const setup = postgres(at(name, true), { max: 1, ...quiet });
  await setup.unsafe(`
    REVOKE ALL ON DATABASE ${name} FROM PUBLIC;
    GRANT CONNECT ON DATABASE ${name} TO gateway, agent;
    GRANT CREATE ON DATABASE ${name} TO gateway;
    CREATE SCHEMA gateway AUTHORIZATION gateway;
    CREATE SCHEMA agent AUTHORIZATION agent;`);
  await setup.end();
  const migrator = postgres(at(name), { max: 1, ...quiet });
  const migrationsFolder = fileURLToPath(new URL('../drizzle', import.meta.url));
  await migrate(drizzle(migrator), { migrationsFolder, migrationsSchema: 'gateway' });
  await migrator.end();
  return {
    url: at(name),
    drop: async () => {
      await admin.unsafe(`DROP DATABASE ${name} WITH (FORCE)`);
      await admin.end();
    },
  };
}

/** A tenant and its webhook token, stored as `pnpm tenant:token` stores it. */
export async function newTenant(sql: Sql) {
  const token = randomBytes(32).toString('base64url');
  const [tenant] = await sql<{ id: string }[]>`
    INSERT INTO gateway.tenants (name, webhook_token_sha256)
    VALUES (${randomUUID()}, ${sha256(token)}) RETURNING id`;
  if (tenant === undefined) throw new Error('no tenant');
  return { tenantId: tenant.id, token };
}

/** A webhook whose alerts carry their name as a label, with fingerprints computed from labels. */
export const webhook = (groupKey: string, alerts: { alert: string; status?: string }[]) =>
  webhookSchema.parse({
    version: '4',
    groupKey,
    status: 'firing',
    receiver: 'triage-copilot',
    groupLabels: {},
    commonLabels: {},
    commonAnnotations: {},
    externalURL: 'http://alertmanager.example:9093',
    alerts: alerts.map(({ alert, status = 'firing' }) => {
      const labels = { alertname: 'HighErrorRate', severity: 'critical', alert };
      return {
        status,
        labels,
        annotations: {},
        startsAt: '2026-10-14T11:54:12.481516234Z',
        endsAt: '0001-01-01T00:00:00Z',
        generatorURL: 'http://prometheus.example:9090/graph',
        fingerprint: fingerprint(labels),
      };
    }),
  });

export async function eventually(condition: () => Promise<boolean>, ms = 10_000) {
  const deadline = Date.now() + ms;
  while (!(await condition())) {
    if (Date.now() > deadline) throw new Error('timed out');
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
}
