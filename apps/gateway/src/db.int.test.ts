import { loadConfig, z } from '@triage/ts-config';

import { buildApp, createUpdates } from './app.ts';
import { connectDb } from './db.ts';

describe('gateway against real Postgres', () => {
  // Only the database URL: make keeps the approver key away from every test process.
  const { GATEWAY_DATABASE_URL } = loadConfig(z.object({ GATEWAY_DATABASE_URL: z.url() }));
  const db = connectDb(GATEWAY_DATABASE_URL);
  afterAll(() => db.sql.end());

  it('is ready', async () => {
    const app = await buildApp({
      config: {
        LOG_LEVEL: 'error',
        APPROVER_API_KEY: 'unused-by-this-test-0000000000000',
        RATE_LIMIT_PER_MINUTE: 600,
      },
      ping: db.ping,
      updates: createUpdates(),
      decide: () => Promise.resolve('not_found'),
      findTenant: () => Promise.resolve(undefined),
      intake: () => Promise.reject(new Error('unused by this test')),
    });
    try {
      expect((await app.inject({ url: '/readyz' })).statusCode).toBe(200);
    } finally {
      await app.close();
    }
  });

  it('cannot write into the agent schema', async () => {
    await expect(db.sql`create table agent.not_ours (id int)`).rejects.toThrow(/permission denied/);
  });
});
