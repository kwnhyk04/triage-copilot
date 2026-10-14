import { buildApp, createUpdates } from './app.ts';
import { loadGatewayConfig } from './config.ts';
import { connectDb } from './db.ts';

describe('gateway against real Postgres', () => {
  const config = loadGatewayConfig();
  const db = connectDb(config.GATEWAY_DATABASE_URL);
  afterAll(() => db.sql.end());

  it('is ready', async () => {
    const app = buildApp({ config, ping: db.ping, updates: createUpdates() });
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
