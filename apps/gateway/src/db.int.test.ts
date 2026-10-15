import { buildApp, createUpdates } from './app.ts';
import { connectDb } from './db.ts';
import { APP_CONFIG, REFUSED_DATABASE, scratchDb } from './testing.ts';

describe('gateway against real Postgres', () => {
  let scratch: Awaited<ReturnType<typeof scratchDb>>;
  let db: ReturnType<typeof connectDb>;
  const unused = () => Promise.reject(new Error('unused by this test'));

  beforeAll(async () => {
    scratch = await scratchDb();
    db = connectDb(scratch.url);
  }, 60_000);
  afterAll(async () => {
    await db.sql.end();
    await scratch.drop();
  });

  it('is ready', async () => {
    const app = await buildApp({
      config: APP_CONFIG,
      ping: db.ping,
      updates: createUpdates(),
      decide: unused,
      findTenant: unused,
      intake: unused,
      listInvestigations: unused,
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

  it('never reaches the dev database: its variable names a database that does not exist', async () => {
    const url = process.env.GATEWAY_DATABASE_URL ?? '';
    expect(new URL(url).pathname).toBe(`/${REFUSED_DATABASE}`);
    const refused = connectDb(url);
    try {
      await expect(refused.sql`select 1`).rejects.toThrow(/does not exist/);
    } finally {
      await refused.sql.end();
    }
  });
});
