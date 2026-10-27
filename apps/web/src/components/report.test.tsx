import { renderToStaticMarkup } from 'react-dom/server';

import {
  ANSWERS_AFTER_RENAME,
  ONE_CLIENT_RETRYING,
  WORKER_OUT_OF_MEMORY,
} from '../demo/live-samples.ts';
import type { Investigation } from '../lib/investigations.ts';
import { Report, type ReportProps } from './report.tsx';

// LiveRefresh refreshes through the App Router, which a static render does not mount.
jest.mock('next/navigation', () => ({ useRouter: () => ({ refresh: () => undefined }) }));

const NOW = Date.parse('2026-10-26T10:04:30Z');
const decide = () => Promise.resolve();
const shown = (investigation: Investigation, props: Partial<ReportProps> = {}) =>
  renderToStaticMarkup(<Report investigation={investigation} now={NOW} {...props} />);
const count = (html: string, text: string) => html.split(text).length - 1;

describe('Report', () => {
  it('leads with the verdict tag, then the cause and its evidence as the tool returned it', () => {
    const html = shown(WORKER_OUT_OF_MEMORY);
    for (const text of [
      'Real incident',
      '<h1>ContainerDown on worker</h1>',
      'Report ready, opened 09:52 UTC',
      'resource exhaustion in worker',
      '80%',
      'The worker runs out of memory on large invoices and is killed with code 137.',
      'worker    exited (137) 2 minutes ago, restarting',
      // Evidence is labeled with its tool and id.
      'read_snapshot',
      'tc_01JB2SNAP01',
      'get_dependencies failed: it timed out.',
    ]) {
      expect([text, html.includes(text)]).toEqual([text, true]);
    }
    expect(html).toContain('class="evidence"');
    expect(html).toContain('No result kept');
  });

  it('lists the models asked and what answered when one missed', () => {
    const html = shown(WORKER_OUT_OF_MEMORY);
    for (const text of ['gemini-3.5-flash-lite', 'claude-opus-5-5', 'Plan', 'Log summaries']) {
      expect(html).toContain(text);
    }
    expect(html).toContain(
      'Triage: gemini-3.5-flash-lite answered; claude-haiku-5-5 missed 1 request',
    );
    expect(shown(ONE_CLIENT_RETRYING)).toContain('None: every model asked answered each request.');
  });

  it('folds a long excerpt behind Show all, never cutting it silently', () => {
    const html = shown(ANSWERS_AFTER_RENAME);
    expect(html).toContain('<details');
    expect(html).toContain('Show all 18 lines');
    expect(html).toContain('(request 4827)');
  });

  it('puts a pending plan first, with Approve and Reject, which only record a decision', () => {
    const html = shown(WORKER_OUT_OF_MEMORY, { decide });
    expect(html.indexOf('Decision needed')).toBeLessThan(html.indexOf('>Cause<'));
    for (const text of [
      'Restart worker',
      'A restart frees its memory',
      'name="reason"',
      '>Approve plan</button>',
      '>Reject plan</button>',
      'nothing runs from a plan yet',
    ]) {
      expect([text, html.includes(text)]).toEqual([text, true]);
    }
    // The approver is the signed-in operator, never a name typed in.
    expect(html).not.toContain('name="approver"');
    // Without a way to decide, the plan stays read-only.
    expect(shown(WORKER_OUT_OF_MEMORY)).not.toContain('<form');
  });

  it("disables the demo's plan buttons and gives them no action", () => {
    const html = shown(WORKER_OUT_OF_MEMORY, { demo: true });
    expect(count(html, 'disabled=""')).toBe(2);
    expect(html).not.toContain('<form');
    expect(html).toContain('The demo records no decision.');
  });

  it('shows a decided plan with its files, who decided and when', () => {
    const html = shown(ANSWERS_AFTER_RENAME, { decide });
    for (const text of [
      'Code fix in api',
      'services/api/settings.py',
      'Approved at 25 Oct, 14:20 UTC by operator',
    ]) {
      expect(html).toContain(text);
    }
    expect(html).not.toContain('Decision needed');
    expect(html).not.toContain('<form');
  });

  it('says why a plan is missing, and when none was made', () => {
    const problem = 'No model gave a plan: claude-opus-5-5: Refused';
    const plan = { ...WORKER_OUT_OF_MEMORY.plan, proposal: null, problem };
    expect(shown({ ...WORKER_OUT_OF_MEMORY, plan, plan_status: null }, { decide })).toContain(
      `No plan: ${problem}`,
    );
    expect(shown(ONE_CLIENT_RETRYING)).toContain('No plan was made');
  });

  it('marks an illustrative sample', () => {
    const label = 'Illustrative sample, not a real incident';
    expect(shown(WORKER_OUT_OF_MEMORY, { illustrative: true })).toContain(label);
    expect(shown(WORKER_OUT_OF_MEMORY)).not.toContain(label);
  });

  it('shows an eval run as simulated, without a verdict, its proposals read-only', () => {
    const run: Investigation = {
      ...ONE_CLIENT_RETRYING,
      source: 'sim',
      verdict: null,
      status: 'awaiting_approval',
      proposed_actions: [
        {
          id: '9f8b6c1e-3d2a-4f5b-8e7d-6c5b4a392817',
          investigation_id: ONE_CLIENT_RETRYING.id,
          kind: 'rollback_deploy',
          target: 'checkout',
          parameters: { to_version: 'v1.7.3' },
          rationale: 'Errors began two minutes after v1.8.0.',
          status: 'pending',
          proposed_at: '2026-10-24T22:42:30.000Z',
          decided_by: null,
          decided_at: null,
          decision_reason: null,
        },
      ],
    };
    const html = shown(run, { decide });
    expect(html).toContain('Simulated');
    expect(html).not.toContain('Real incident');
    expect(html).toContain('Rollback deploy on checkout');
    expect(html).toContain('Errors began two minutes after v1.8.0.');
    expect(html).not.toContain('<button');
  });

  it("reports a running investigation's progress every 3 s, with a way to pause", () => {
    const running: Investigation = {
      ...WORKER_OUT_OF_MEMORY,
      status: 'running',
      verdict: null,
      plan: null,
      plan_status: null,
    };
    const html = shown(running, { refresh: true });
    expect(html).toContain('role="status"');
    expect(html).toContain('Running, 5 steps so far, last at 09:54:10 UTC');
    expect(html).toContain('>Pause updates</button>');
    expect(html).toContain('data-every="3"');
    // A settled report, or a page that never refreshes, such as the demo's, has neither.
    expect(shown(WORKER_OUT_OF_MEMORY, { refresh: true })).not.toContain('role="status"');
    expect(shown(running)).not.toContain('role="status"');
  });

  it('lists the steps with their tool, outcome and duration', () => {
    const html = shown(WORKER_OUT_OF_MEMORY);
    for (const text of [
      'get_dependencies',
      'timeout',
      '10,000 ms',
      '12 ms',
      'What the worker needs.',
    ]) {
      expect(html).toContain(text);
    }
    expect(html).toContain('<ol class="timeline"');
  });
});
