import { ONE_CLIENT_RETRYING, WORKER_OUT_OF_MEMORY } from '../demo/live-samples.ts';
import {
  ago,
  band,
  causeBrief,
  fallbackNotes,
  progressLine,
  STATUSES,
  stillChanging,
  utcTime,
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

  it("gives each tag its band: the verdict, a simulated incident's, or one still open", () => {
    const live = { ...WORKER_OUT_OF_MEMORY, verdict: null };
    expect(band(WORKER_OUT_OF_MEMORY)).toEqual({ kind: 'real', label: 'Real incident' });
    expect(band(ONE_CLIENT_RETRYING)).toEqual({ kind: 'not-real', label: 'Not a real incident' });
    expect(band({ ...live, source: 'sim' })).toEqual({ kind: 'simulated', label: 'Simulated' });
    for (const status of ['queued', 'running'] as const) {
      expect(band({ ...live, status })).toEqual({ kind: 'open', label: 'Investigating' });
    }
    expect(band({ ...live, status: 'resolved' })).toEqual({ kind: 'none', label: 'No verdict' });
  });

  it('says the top cause in brief', () => {
    expect(causeBrief(WORKER_OUT_OF_MEMORY.cause)).toBe('Resource exhaustion in worker, 80%');
    expect(causeBrief(null)).toBeUndefined();
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

  it('waits 6 minutes after a real verdict for its plan, and no longer', () => {
    const verdict = { ...settled, plan_status: null };
    expect(stillChanging({ ...verdict, updated_at: at(5) }, now)).toBe(true);
    expect(stillChanging({ ...verdict, updated_at: at(6) }, now)).toBe(false);
    // A plan arrived: one without a proposal has no status, and the report knows it came.
    expect(stillChanging({ ...verdict, updated_at: at(1) }, now, true)).toBe(false);
    expect(stillChanging({ ...settled, updated_at: at(1) }, now)).toBe(false);
    expect(stillChanging({ ...verdict, verdict: 'not_real', updated_at: at(1) }, now)).toBe(false);
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
      'Report ready, waiting for the plan',
    );
  });
});
