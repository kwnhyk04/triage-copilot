import { randomUUID } from 'node:crypto';

import { connectDb } from './db.ts';
import { storeEvent } from './events.ts';
import { createMetrics } from './metrics.ts';
import { newTenant, scratchDb } from './testing.ts';

const WEB = 'https://triage.example.com';
const MODEL = { part: 'triage', provider: 'scripted', model: 'scripted', calls: 1, failed: 0 };
const PROPOSAL = {
  kind: 'restart_service',
  target: 'worker',
  files: [] as string[],
  rationale: 'The worker exited with 137 [tc_01J9ZK3Q7M]. Restart it.',
};
const REVIEW = {
  status: 'ok',
  by: 'model',
  model: 'claude-sonnet-5-5',
  issues: [],
  reason: null,
  new_files: [],
};

describe('metrics against Postgres', () => {
  let scratch: Awaited<ReturnType<typeof scratchDb>>;
  let db: ReturnType<typeof connectDb>;
  // LIVE_TENANTS: a test's tenant is live unless it says otherwise.
  const apps: Record<string, 'freight-audit' | 'filing-lens'> = {};

  beforeAll(async () => {
    scratch = await scratchDb();
    db = connectDb(scratch.url);
  }, 60_000);
  afterAll(async () => {
    await db.sql.end();
    await scratch.drop();
  });

  /** A triage's alert group with its report: a verdict and a top cause, or neither. */
  const reported = async (
    verdict: 'real' | 'not_real' | null = 'real',
    { live = true, cause = 'database' }: { live?: boolean; cause?: string | null } = {},
  ) => {
    const { tenantId } = await newTenant(db.sql);
    if (live) apps[tenantId] = 'freight-audit';
    const investigationId = randomUUID();
    await db.sql`
      INSERT INTO gateway.alert_groups
        (tenant_id, group_key, window_start, severity, status, investigation_id)
      VALUES (${tenantId}, ${randomUUID()}, now(), 'critical', 'queued', ${investigationId})`;
    const hypotheses =
      cause === null
        ? []
        : [
            {
              root_cause_class: cause,
              service: 'postgres',
              confidence: 0.8,
              summary: 'Postgres refused connections.',
              evidence_ids: [],
            },
          ];
    const report = {
      schema_version: 2,
      investigation_id: investigationId,
      status: 'resolved',
      report: {
        summary: 'Postgres refused connections.',
        hypotheses,
        missing_evidence: [],
        proposed_actions: [],
        partial: false,
      },
      verdict,
      models: [{ ...MODEL, reason: null }],
      recorded_at: '2026-10-26T10:01:30Z',
    };
    expect(await storeEvent(db.sql, 'report-recorded', JSON.stringify(report))).toBe('stored');
    return investigationId;
  };
  /** Its plan: a proposal with a review, or none. */
  const planned = async (
    investigationId: string,
    proposal: typeof PROPOSAL | null = PROPOSAL,
    review: Record<string, unknown> | null = REVIEW,
  ) => {
    const planId = randomUUID();
    const plan = {
      schema_version: 2,
      plan_id: planId,
      investigation_id: investigationId,
      plan: proposal,
      problem: proposal === null ? 'No model gave a plan.' : null,
      review,
      models: [{ ...MODEL, part: 'plan', reason: null }],
      recorded_at: '2026-10-26T10:03:10Z',
    };
    expect(await storeEvent(db.sql, 'plan-recorded', JSON.stringify(plan))).toBe('stored');
    return planId;
  };
  const series = async (id: string, webUrl = WEB) =>
    (await createMetrics(db.sql, apps, webUrl)())
      .split('\n')
      .filter((line) => line.includes(id) && !line.startsWith('#'));
  const link = (id: string) => `report="${WEB}/investigations/${id}"`;

  it('lists a live verdict and a plan that waits, each with a link to its report', async () => {
    const id = await reported();
    await planned(id);
    expect(await series(id)).toEqual([
      `tc_investigation_verdict{investigation="${id}",app="freight-audit",verdict="real",` +
        `cause="database",${link(id)}} 1`,
      `tc_plan_waiting{investigation="${id}",app="freight-audit",kind="restart_service",` +
        `${link(id)}} 1`,
    ]);
  });

  it('lists a verdict of not real too, and a report with no cause as unknown', async () => {
    const id = await reported('not_real', { cause: null });
    expect(await series(id)).toEqual([
      `tc_investigation_verdict{investigation="${id}",app="freight-audit",verdict="not_real",` +
        `cause="unknown",${link(id)}} 1`,
    ]);
  });

  it("leaves out a report with no verdict, and a sim tenant's", async () => {
    expect(await series(await reported(null))).toEqual([]);
    expect(await series(await reported('real', { live: false }))).toEqual([]);
  });

  it('holds each for an hour, so a late scrape still sees it and a message never repeats', async () => {
    const id = await reported();
    await planned(id);
    await db.sql`
      UPDATE gateway.investigation_reports SET created_at = now() - interval '59 minutes'
      WHERE investigation_id = ${id}`;
    await db.sql`
      UPDATE gateway.plans SET created_at = now() - interval '59 minutes'
      WHERE investigation_id = ${id}`;
    expect(await series(id)).toHaveLength(2);
    await db.sql`
      UPDATE gateway.investigation_reports SET created_at = now() - interval '61 minutes'
      WHERE investigation_id = ${id}`;
    await db.sql`
      UPDATE gateway.plans SET created_at = now() - interval '61 minutes'
      WHERE investigation_id = ${id}`;
    expect(await series(id)).toEqual([]);
  });

  it('stops listing a plan once a human decides it', async () => {
    const id = await reported();
    const planId = await planned(id);
    await db.sql`
      INSERT INTO gateway.plan_decisions (plan_id, approver, decision)
      VALUES (${planId}, 'operator', 'rejected')`;
    expect((await series(id)).filter((line) => line.startsWith('tc_plan_waiting'))).toEqual([]);
  });

  it('lists no plan a human cannot approve as waiting', async () => {
    const waiting = async (id: string) =>
      (await series(id)).filter((line) => line.startsWith('tc_plan_waiting'));
    const refused = await reported();
    await planned(refused, PROPOSAL, { ...REVIEW, status: 'refuse', by: 'check', model: null });
    const unreviewed = await reported();
    await planned(unreviewed, PROPOSAL, {
      ...REVIEW,
      status: 'not_reviewed',
      by: null,
      reason: 'its credits ended',
    });
    const empty = await reported();
    await planned(empty, null, null);
    for (const id of [refused, unreviewed, empty]) expect(await waiting(id)).toEqual([]);
  });

  it('links the report under whatever address the web has', async () => {
    const id = await reported('not_real');
    const [line] = await series(id, 'https://example.com/triage/');
    expect(line).toContain(`report="https://example.com/triage/investigations/${id}"`);
  });

  it('names each metric for Prometheus, with no sample when nothing is recent', async () => {
    const text = await createMetrics(db.sql, {}, WEB)();
    expect(text.split('\n').filter((line) => line !== '')).toEqual([
      expect.stringMatching(/^# HELP tc_investigation_verdict /) as string,
      '# TYPE tc_investigation_verdict gauge',
      expect.stringMatching(/^# HELP tc_plan_waiting /) as string,
      '# TYPE tc_plan_waiting gauge',
    ]);
  });
});
