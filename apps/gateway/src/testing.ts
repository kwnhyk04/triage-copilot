// Shared by the integration suites (*.int.test.ts): needs `make infra-up` and the .env variables.
import { randomUUID } from 'node:crypto';
import { fileURLToPath } from 'node:url';

import { loadConfig, z } from '@triage/ts-config';
import { drizzle } from 'drizzle-orm/postgres-js';
import { migrate } from 'drizzle-orm/postgres-js/migrator';
import postgres from 'postgres';

import { webhookSchema } from './intake.ts';

export const env = loadConfig(
  z.object({
    GATEWAY_DATABASE_URL: z.url(),
    POSTGRES_PASSWORD: z.string().min(1),
    SQS_ENDPOINT_URL: z.url(),
    AWS_REGION: z.string().default('us-east-1'),
  }),
);
const quiet = { onnotice: () => undefined };

/** An empty database set up like infra/postgres-init.sql, with the gateway migrations applied. */
export async function scratchDb() {
  const name = `triage_test_${randomUUID().replaceAll('-', '').slice(0, 12)}`;
  const at = (database: string, superuser = false) => {
    const url = new URL(env.GATEWAY_DATABASE_URL);
    url.pathname = `/${database}`;
    if (superuser) {
      url.username = 'postgres';
      url.password = env.POSTGRES_PASSWORD;
    }
    return url.href;
  };
  const admin = postgres(at('postgres', true), { max: 1, ...quiet });
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

export const webhook = (groupKey: string, alerts: { fingerprint: string; status?: string }[]) =>
  webhookSchema.parse({
    version: '4',
    groupKey,
    status: 'firing',
    receiver: 'triage-copilot',
    groupLabels: {},
    commonLabels: {},
    commonAnnotations: {},
    externalURL: 'http://alertmanager.example:9093',
    alerts: alerts.map(({ fingerprint, status = 'firing' }) => ({
      status,
      labels: { alertname: 'HighErrorRate', severity: 'critical' },
      annotations: {},
      startsAt: '2026-10-14T11:54:12.481516234Z',
      endsAt: '0001-01-01T00:00:00Z',
      generatorURL: 'http://prometheus.example:9090/graph',
      fingerprint,
    })),
  });

export async function eventually(condition: () => Promise<boolean>, ms = 10_000) {
  const deadline = Date.now() + ms;
  while (!(await condition())) {
    if (Date.now() > deadline) throw new Error('timed out');
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
}
