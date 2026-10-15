import { setTimeout as sleep } from 'node:timers/promises';

import type { components } from '@triage/ts-clients/gateway';
import type { FastifyInstance } from 'fastify';

import { buildApp, createUpdates } from './app.ts';
import { connectDb } from './db.ts';
import { createListInvestigations } from './investigations.ts';
import { APP_CONFIG, newTenant, scratchDb } from './testing.ts';

type Page = components['schemas']['InvestigationPage'];

describe('investigation list against Postgres', () => {
  let scratch: Awaited<ReturnType<typeof scratchDb>>;
  let db: ReturnType<typeof connectDb>;
  let app: FastifyInstance;
  const unused = () => Promise.reject(new Error('unused by this test'));

  beforeAll(async () => {
    scratch = await scratchDb();
    db = connectDb(scratch.url);
    app = await buildApp({
      config: APP_CONFIG,
      ping: db.ping,
      updates: createUpdates(),
      decide: unused,
      findTenant: unused,
      intake: unused,
      listInvestigations: createListInvestigations(db.sql),
    });
  }, 60_000);
  afterAll(async () => {
    await app.close();
    await db.sql.end();
    await scratch.drop();
  });
  beforeEach(() => db.sql`DELETE FROM gateway.alert_groups`);

  /** Investigated groups; rows of one statement share created_at, so only the id orders them. */
  const insert = async (tenantId: string, count: number, createdAt: string | null = null) =>
    (
      await db.sql<{ investigation_id: string }[]>`
        INSERT INTO gateway.alert_groups
          (tenant_id, group_key, window_start, severity, status, investigation_id, created_at)
        SELECT ${tenantId}, 'group-' || gen_random_uuid(), now(), 'warning', 'queued',
               gen_random_uuid(), coalesce(${createdAt}::text::timestamptz, now())
        FROM generate_series(1, ${count})
        RETURNING investigation_id`
    ).map((row) => row.investigation_id);
  const page = async (query: string) => {
    const response = await app.inject({
      url: `/v1/investigations?${query}`,
      headers: { 'x-api-key': APP_CONFIG.GATEWAY_API_KEY },
    });
    expect(response.statusCode).toBe(200);
    return response.json<Page>();
  };
  /** Every page from the first, `limit` items each. */
  const walk = async (limit: number) => {
    const ids: string[] = [];
    let cursor: string | null = null;
    let pages = 0;
    do {
      const query: string = cursor === null ? '' : `&cursor=${cursor}`;
      const body = await page(`limit=${String(limit)}${query}`);
      ids.push(...body.items.map((item) => item.id));
      cursor = body.next_cursor;
      pages++;
    } while (cursor !== null);
    return { ids, pages };
  };
  /** The ids in the order the list promises: created_at, then id, newest first. */
  const ordered = async (ids: string[]) =>
    (
      await db.sql<{ investigation_id: string }[]>`
        SELECT investigation_id FROM gateway.alert_groups WHERE investigation_id = ANY(${ids})
        ORDER BY created_at DESC, investigation_id DESC`
    ).map((row) => row.investigation_id);

  it('lists 25 per page by default, newest first', async () => {
    const { tenantId } = await newTenant(db.sql);
    await insert(tenantId, 30);
    const first = await page('');
    expect(first.items).toHaveLength(25);
    expect(first.next_cursor).not.toBeNull();
    expect(first.items.map((item) => item.id)).toEqual(
      await ordered(first.items.map((item) => item.id)),
    );
  });

  it('keeps rows created within one millisecond apart, by their microseconds and ids', async () => {
    const { tenantId } = await newTenant(db.sql);
    const before = [
      ...(await insert(tenantId, 1, '2026-01-01T00:00:00.123999Z')),
      ...(await insert(tenantId, 1, '2026-01-01T00:00:00.123456Z')),
      ...(await insert(tenantId, 3, '2026-01-01T00:00:00.123455Z')),
      ...(await insert(tenantId, 1, '2026-01-01T00:00:00.123001Z')),
      ...(await insert(tenantId, 2, '2026-01-01T00:00:00.123000Z')),
    ];
    // Stored with their microseconds: five instants, all within one millisecond.
    expect(await db.sql`SELECT DISTINCT created_at FROM gateway.alert_groups`).toHaveLength(5);
    // Two per page, so pages end inside the millisecond and inside each shared microsecond.
    const { ids, pages } = await walk(2);
    expect(pages).toBe(4);
    expect(ids).toEqual(await ordered(before));
  });

  it('pages through concurrent inserts with no duplicate and no gap', async () => {
    const { tenantId } = await newTenant(db.sql);
    const before = [
      ...(await insert(tenantId, 12)),
      ...(await insert(tenantId, 10, '2026-01-01T00:00:00Z')),
    ];
    for (let i = 0; i < 25; i++) before.push(...(await insert(tenantId, 1)));
    // New investigations arrive while the pages are read, both before and behind the cursor.
    const stop = new AbortController();
    let written = 0;
    const writer = (async () => {
      while (!stop.signal.aborted) {
        await insert(tenantId, 1, written % 2 === 0 ? null : '2026-06-01T00:00:00Z');
        written++;
        await sleep(1);
      }
    })();
    let walked;
    try {
      walked = await walk(7);
    } finally {
      stop.abort();
      await writer;
    }
    expect(written).toBeGreaterThan(0);
    expect(new Set(walked.ids).size).toBe(walked.ids.length);
    expect(walked.ids).toEqual(expect.arrayContaining(before));
    expect(walked.ids).toEqual(await ordered(walked.ids));
    expect(walked.pages).toBeGreaterThanOrEqual(Math.ceil(before.length / 7));
  });
});
