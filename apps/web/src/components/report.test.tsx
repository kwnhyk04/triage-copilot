import { renderToStaticMarkup } from 'react-dom/server';

import type { Investigation } from '../lib/investigations.ts';
import { LIVE_TRIAGE } from '../lib/testing.ts';
import { Report } from './report.tsx';

const shown = (investigation = LIVE_TRIAGE) =>
  renderToStaticMarkup(<Report investigation={investigation} />);

describe('Report', () => {
  it('shows the verdict, the cause, its evidence, the models and the fallback notes', () => {
    const html = shown();
    for (const text of [
      'Real incident',
      'database in postgres',
      'The readiness check fails on postgres, whose container restarts.',
      'postgres: restarting (exit 137)',
      'get_dependencies failed: it timed out.',
      'gemini-3.5-flash-lite',
      'claude-haiku-5-5 missed 1 request',
    ]) {
      expect(html).toContain(text);
    }
  });

  const PLAN: NonNullable<Investigation['plan']> = {
    id: '5b1c9d2e-7f3a-4e8b-9c6d-1a2b3c4d5e6f',
    proposal: {
      kind: 'restart_service',
      target: 'worker',
      files: [],
      rationale: 'The worker exited with 137 while its dependencies stay healthy.',
    },
    problem: null,
    status: 'pending',
    recorded_at: '2026-10-26T10:03:10.000Z',
    decided_by: null,
    decided_at: null,
    decision_reason: null,
  };
  const decide = () => Promise.resolve();

  it('shows the plan read-only, with approve and reject while it is pending', () => {
    const html = renderToStaticMarkup(
      <Report investigation={{ ...LIVE_TRIAGE, plan: PLAN }} decide={decide} />,
    );
    for (const text of [
      'Restart worker',
      'The worker exited with 137 while its dependencies stay healthy.',
      'name="approver"',
      '>Approve</button>',
      '>Reject</button>',
      'nothing runs from a plan yet',
    ]) {
      expect(html).toContain(text);
    }
    // Without a way to decide, the report stays read-only.
    expect(shown({ ...LIVE_TRIAGE, plan: PLAN })).not.toContain('name="approver"');
  });

  it('shows a decided plan without the form, why a plan is missing, and when none was made', () => {
    const decided = { ...PLAN, status: 'approved' as const, decided_by: 'Ana' };
    const html = renderToStaticMarkup(
      <Report investigation={{ ...LIVE_TRIAGE, plan: decided }} decide={decide} />,
    );
    expect(html).toContain('Approved by Ana');
    expect(html).not.toContain('name="approver"');
    const problem = 'No model gave a plan: claude-opus-5-5: Refused';
    expect(shown({ ...LIVE_TRIAGE, plan: { ...PLAN, proposal: null, problem } })).toContain(
      `No plan: ${problem}`,
    );
    expect(shown()).toContain('No plan was made');
  });

  it('says when evidence has no result kept, and when there is no verdict', () => {
    const html = shown({ ...LIVE_TRIAGE, verdict: null });
    expect(html).toContain('No result kept');
    expect(html).toContain('No verdict');
    expect(shown({ ...LIVE_TRIAGE, verdict: 'not_real' })).toContain('Not a real incident');
  });
});
