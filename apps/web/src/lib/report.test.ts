import { ONE_CLIENT_RETRYING, WORKER_OUT_OF_MEMORY } from '../demo/live-samples.ts';
import {
  ago,
  causeName,
  fallbackNotes,
  ISSUE_KINDS,
  percent,
  PLAN_STATES,
  planBadge,
  planning,
  planNote,
  progressLine,
  REVIEW_NOTE,
  reviewHeading,
  STATUSES,
  statusBadge,
  stillChanging,
  utcTime,
  verdictBadge,
} from './report.ts';

describe('fallbackNotes', () => {
  it('names each model that missed requests, why, and what answered its part instead', () => {
    expect(fallbackNotes(WORKER_OUT_OF_MEMORY.models)).toEqual([
      'Triage: gemini-3.5-flash-lite answered; claude-haiku-5-5 missed 1 request: ' +
        'claude-haiku-5-5 skipped: Claude is switched off (agent.switches)',
    ]);
  });

  it('is empty when the first model asked answered every request', () => {
    expect(fallbackNotes(ONE_CLIENT_RETRYING.models)).toEqual([]);
  });

  it('says when no model answered a part', () => {
    const missed = { calls: 0, failed: 2, reason: 'RateLimited: gemini 429: rate limited' };
    const summaries = WORKER_OUT_OF_MEMORY.models.map((model) =>
      model.part === 'summary' ? { ...model, ...missed } : model,
    );
    expect(fallbackNotes(summaries)).toContain(
      'Log summaries: no model answered; gemini-3.1-flash-lite missed 2 requests: ' +
        'RateLimited: gemini 429: rate limited',
    );
  });
});

describe('labels', () => {
  it('names each status as a reader says it', () => {
    expect(STATUSES).toEqual({
      queued: 'Queued',
      running: 'Running',
      awaiting_approval: 'Needs approval',
      resolved: 'Report ready',
      failed: 'Failed',
      cancelled: 'Cancelled',
    });
  });

  it('gives each verdict one badge, red only for a real incident', () => {
    const live = { ...WORKER_OUT_OF_MEMORY, verdict: null };
    expect(verdictBadge(WORKER_OUT_OF_MEMORY)).toEqual({ label: 'Real incident', tone: 'bad' });
    expect(verdictBadge(ONE_CLIENT_RETRYING)).toEqual({
      label: 'Not a real incident',
      tone: 'neutral',
    });
    expect(verdictBadge({ ...live, source: 'sim' })).toEqual({
      label: 'Simulated',
      tone: 'neutral',
    });
    for (const status of ['queued', 'running'] as const) {
      expect(verdictBadge({ ...live, status })).toEqual({
        label: 'Investigating',
        tone: 'info',
        busy: true,
      });
    }
    expect(verdictBadge({ ...live, status: 'resolved' })).toEqual({
      label: 'No verdict',
      tone: 'neutral',
    });
  });

  it('colours each status and plan state, with a pulsing dot while running', () => {
    const statuses = Object.keys(STATUSES) as (keyof typeof STATUSES)[];
    expect(statuses.map(statusBadge)).toEqual([
      { label: 'Queued', tone: 'neutral' },
      { label: 'Running', tone: 'info', busy: true },
      { label: 'Needs approval', tone: 'warn' },
      { label: 'Report ready', tone: 'ok' },
      { label: 'Failed', tone: 'fail' },
      { label: 'Cancelled', tone: 'neutral' },
    ]);
    expect(PLAN_STATES).toEqual({
      pending: { label: 'Decision needed', tone: 'warn' },
      approved: { label: 'Approved', tone: 'ok' },
      rejected: { label: 'Rejected', tone: 'neutral' },
    });
  });

  it('shows a plan its review refused as Refused, in red, and any other by its decision', () => {
    expect(planBadge('pending', 'refuse')).toEqual({ label: 'Refused', tone: 'bad' });
    // A flag leaves the decision to a person.
    for (const review of ['flag', 'ok'] as const) {
      expect([review, planBadge('pending', review)]).toEqual([review, PLAN_STATES.pending]);
    }
    expect(planBadge('approved', 'flag')).toEqual(PLAN_STATES.approved);
  });

  it('shows a pending plan no model reviewed as one that cannot be approved', () => {
    const blocked = { label: 'Not reviewed, cannot be approved', tone: 'bad' };
    // The reviewer could not answer, or the plan is from before reviews and carries none.
    for (const review of ['not_reviewed', null, undefined] as const) {
      expect([review, planBadge('pending', review)]).toEqual([review, blocked]);
    }
    // One decided before the gateway refused these keeps its decision.
    expect(planBadge('approved', 'not_reviewed')).toEqual(PLAN_STATES.approved);
    expect(planBadge('rejected', null)).toEqual(PLAN_STATES.rejected);
  });

  it("names each kind of issue a plan's review raises in plain words", () => {
    expect(ISSUE_KINDS).toEqual({
      forbidden_step: 'Forbidden step',
      fallback_step: 'Fallback step',
      contradicts_evidence: 'Contradicts the evidence',
      unfit_file: "File doesn't fit",
      code_check: 'Code check',
    });
  });

  it("heads a plan's review with its verdict, and who refused a refused plan", () => {
    const review = { status: 'ok', by: 'model', reason: null } as const;
    expect(reviewHeading(review, true)).toBe('Reviewed: no issues');
    const unreviewed = { status: 'not_reviewed', by: null } as const;
    expect(reviewHeading({ ...unreviewed, reason: 'its daily cap is spent' }, true)).toBe(
      'Not reviewed: its daily cap is spent',
    );
    expect(reviewHeading({ ...unreviewed, reason: null }, true)).toBe(
      'Not reviewed: no reason was recorded',
    );
    // Warnings ask to be read before a decision, and stay once one is made.
    expect(reviewHeading({ ...review, status: 'flag' }, true)).toBe(
      'Read these warnings before deciding',
    );
    expect(reviewHeading({ ...review, status: 'flag' }, false)).toBe('Warnings from the review');
    expect(reviewHeading({ ...review, status: 'refuse' }, false)).toBe('Refused by the review');
    expect(reviewHeading({ ...review, status: 'refuse', by: 'check' }, false)).toBe(
      "Refused by the agent's checks",
    );
  });

  it('notes that a second model reviewed the plan, never that the plan is safe', () => {
    expect(REVIEW_NOTE).toBe('Reviewed by a second model. Read every step yourself.');
  });

  it('says what approving does: a restart runs once while the actions switch is on, until it expires', () => {
    expect(planNote({ kind: 'restart_service', target: 'worker' }, 6)).toBe(
      'Approving queues this restart. With the actions switch on, the executor restarts worker ' +
        'once. The approval expires after 6 hours: past that, nothing runs.',
    );
    expect(planNote({ kind: 'restart_service', target: 'worker' }, 1)).toContain('after 1 hour:');
    // An older gateway sends no window: 24 hours is the setting's default.
    expect(planNote({ kind: 'restart_service', target: 'worker' })).toContain('after 24 hours:');
    for (const kind of ['code_fix', 'none'] as const) {
      expect(planNote({ kind, target: 'api' }, 6)).toBe(
        'Approving only records the decision: nothing runs from this kind of plan.',
      );
    }
  });

  it('names the top cause and its confidence', () => {
    const top = { root_cause_class: 'resource_exhaustion', service: 'worker' } as const;
    expect(causeName(top)).toBe('Resource exhaustion in worker');
    expect([0.8, 0.756, 1].map((confidence) => percent(confidence))).toEqual([
      '80%',
      '76%',
      '100%',
    ]);
  });

  it('shows a time in UTC, with the day when it is not today, and its age', () => {
    const now = Date.parse('2026-10-26T10:04:30Z');
    expect(utcTime('2026-10-26T09:52:30.000Z', now)).toBe('09:52 UTC');
    expect(utcTime('2026-10-24T22:41:15.000Z', now)).toBe('24 Oct, 22:41 UTC');
    expect(utcTime('2025-12-31T23:00:00.000Z', now)).toBe('31 Dec 2025, 23:00 UTC');
    expect(ago('2026-10-26T10:04:10Z', now)).toBe('just now');
    expect(ago('2026-10-26T10:03:30Z', now)).toBe('1 min ago');
    expect(ago('2026-10-26T09:52:30.000Z', now)).toBe('12 min ago');
    expect(ago('2026-10-26T07:04:30Z', now)).toBe('3 h ago');
    expect(ago('2026-10-25T09:04:30Z', now)).toBe('1 day ago');
    expect(ago('2026-10-24T10:04:30Z', now)).toBe('2 days ago');
  });
});

describe('refreshing', () => {
  const now = Date.parse('2026-10-26T10:00:00Z');
  const at = (minutesAgo: number) => new Date(now - minutesAgo * 60_000).toISOString();
  const settled = { ...WORKER_OUT_OF_MEMORY, created_at: at(30), updated_at: at(20) };

  it('keeps a running investigation refreshing, however old it is', () => {
    expect(stillChanging({ ...settled, status: 'running', created_at: at(600) }, now)).toBe(true);
  });

  it('keeps a queued one for 15 minutes, then stops: it is parked or stuck', () => {
    expect(stillChanging({ ...settled, status: 'queued', created_at: at(14) }, now)).toBe(true);
    expect(stillChanging({ ...settled, status: 'queued', created_at: at(15) }, now)).toBe(false);
  });

  it('is planning for 10 minutes after a real verdict, until its plan comes, and no longer', () => {
    const verdict = { ...settled, plan_status: null };
    const cases: [Parameters<typeof stillChanging>[0], boolean | undefined, boolean][] = [
      [{ ...verdict, updated_at: at(9) }, undefined, true],
      [{ ...verdict, updated_at: at(10) }, undefined, false],
      // A plan arrived: one without a proposal has no status, and the report knows it came.
      [{ ...verdict, updated_at: at(1) }, true, false],
      [{ ...settled, updated_at: at(1) }, undefined, false],
      [{ ...verdict, verdict: 'not_real', updated_at: at(1) }, undefined, false],
    ];
    for (const [item, arrived, expected] of cases) {
      expect(planning(item, now, arrived)).toBe(expected);
      expect(stillChanging(item, now, arrived)).toBe(expected);
    }
  });

  it('says how far a running investigation has got', () => {
    const running = { ...WORKER_OUT_OF_MEMORY, status: 'running' as const };
    expect(progressLine(running)).toBe('Running, 5 steps so far, last at 09:54:10 UTC');
    const first = running.steps.slice(0, 1);
    expect(progressLine({ ...running, steps: first })).toBe(
      'Running, 1 step so far, last at 09:52:41 UTC',
    );
    expect(progressLine({ ...running, status: 'queued', steps: [] })).toBe('Queued, no steps yet');
    expect(progressLine({ ...WORKER_OUT_OF_MEMORY, plan: null })).toBe(
      'Report ready, still planning',
    );
  });
});
