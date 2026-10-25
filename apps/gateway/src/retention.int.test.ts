import { randomUUID } from 'node:crypto';

import Fastify from 'fastify';

import { connectDb } from './db.ts';
import { purgeLive, startRetention } from './retention.ts';
import { eventually, newTenant, scratchDb } from './testing.ts';

const REPORT = {
  summary: "freight-audit's api fails its readiness check.",
  hypotheses: [],
  missing_evidence: [],
  proposed_actions: [],
  partial: false,
};

describe('live data retention against Postgres', () => {
  let scratch: Awaited<ReturnType<typeof scratchDb>>;
  let db: ReturnType<typeof connectDb>;

  beforeAll(async () => {
    scratch = await scratchDb();
    db = connectDb(scratch.url);
  }, 60_000);
  afterAll(async () => {
    await db.sql.end();
    await scratch.drop();
  });

  /** A group with one watch alert, two steps and a report, all stored `daysAgo` days ago. */
  const investigation = async (tenantId: string, daysAgo: number) => {
    const id = randomUUID();
    const at = new Date(Date.now() - daysAgo * 24 * 60 * 60 * 1000);
    const [group] = await db.sql<{ id: string }[]>`
      INSERT INTO gateway.alert_groups
        (tenant_id, group_key, window_start, severity, status, investigation_id, created_at)
      VALUES (${tenantId}, ${`group-${id}`}, ${at}, 'critical', 'resolved', ${id}, ${at})
      RETURNING id`;
    if (group === undefined) throw new Error('no group');
    const annotations = {
      summary: 'freight-audit api: readiness check failed',
      description: 'Seen by watch (health) on the prod stack.',
      snapshot: 'Health: api /readyz 503. Logs: readiness check failed.',
    };
    await db.sql`
      INSERT INTO gateway.alerts
        (tenant_id, group_id, fingerprint, labels, annotations, starts_at, status, created_at)
      VALUES (${tenantId}, ${group.id}, ${`f-${id}`}, ${db.sql.json({ alertname: 'ApiDown' })},
              ${db.sql.json(annotations)}, ${at}, 'firing', ${at})`;
    for (const seq of [1, 2]) {
      await db.sql`
        INSERT INTO gateway.investigation_steps
          (investigation_id, seq, kind, summary, tool_name, tool_call_id, result, recorded_at,
           created_at)
        VALUES (${id}, ${seq}, 'tool_call', 'Read the snapshot.', 'read_snapshot',
                'tc_01JA0SNAP0', ${seq === 2 ? annotations.snapshot : null}, ${at}, ${at})`;
    }
    await db.sql`
      INSERT INTO gateway.investigation_reports (investigation_id, status, report, recorded_at)
      VALUES (${id}, 'resolved', ${db.sql.json(REPORT)}, ${at})`;
    return id;
  };
  const kept = async (id: string) => {
    const [row] = await db.sql<
      { annotations: Record<string, string>; steps: number; reports: number }[]
    >`
      SELECT a.annotations,
             (SELECT count(*)::int FROM gateway.investigation_steps s
              WHERE s.investigation_id = g.investigation_id) AS steps,
             (SELECT count(*)::int FROM gateway.investigation_reports r
              WHERE r.investigation_id = g.investigation_id) AS reports
      FROM gateway.alert_groups g JOIN gateway.alerts a ON a.group_id = g.id
      WHERE g.investigation_id = ${id}`;
    return row;
  };

  it('purges live snapshots and steps over 30 days old; reports, newer rows and the sim stay', async () => {
    const { tenantId: live } = await newTenant(db.sql);
    const { tenantId: sim } = await newTenant(db.sql);
    const old = await investigation(live, 31);
    const recent = await investigation(live, 29);
    const simOld = await investigation(sim, 31);

    expect(await purgeLive(db.sql, [live])).toEqual({ snapshots: 1, steps: 2 });

    // The alert keeps its summary and description; the report stays for the user to read.
    expect(await kept(old)).toEqual({
      annotations: {
        summary: 'freight-audit api: readiness check failed',
        description: 'Seen by watch (health) on the prod stack.',
      },
      steps: 0,
      reports: 1,
    });
    expect(await kept(recent)).toMatchObject({ steps: 2, reports: 1 });
    expect((await kept(recent))?.annotations).toHaveProperty('snapshot');
    // Simulated incidents are the eval's, not live data.
    expect(await kept(simOld)).toMatchObject({ steps: 2, reports: 1 });
    expect((await kept(simOld))?.annotations).toHaveProperty('snapshot');
    // Nothing older remains, so a second pass purges nothing.
    expect(await purgeLive(db.sql, [live])).toEqual({ snapshots: 0, steps: 0 });
    expect(await purgeLive(db.sql, [])).toEqual({ snapshots: 0, steps: 0 });
  });

  it('purges when it starts, then waits for its next day until stopped', async () => {
    const { tenantId: live } = await newTenant(db.sql);
    const old = await investigation(live, 40);
    const retention = startRetention(db.sql, [live], Fastify({ logger: false }).log);
    try {
      await eventually(async () => (await kept(old))?.steps === 0);
    } finally {
      await retention.stop();
    }
    expect((await kept(old))?.annotations).not.toHaveProperty('snapshot');
  });
});
