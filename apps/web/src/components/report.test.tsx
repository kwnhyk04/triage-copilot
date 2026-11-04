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
const NOTE = 'Reviewed by a second model. Read every step yourself.';
type Plan = NonNullable<Investigation['plan']>;
/** The worker's pending restart with another review, which the summary's plan_review repeats. */
const reviewed = (review: Plan['review']): Investigation => ({
  ...WORKER_OUT_OF_MEMORY,
  plan_review: review?.status ?? null,
  plan: { ...WORKER_OUT_OF_MEMORY.plan, review },
});
const OK = { status: 'ok', by: 'model', model: 'claude-sonnet-5-5' } as const;
const NO_ISSUES = { issues: [], reason: null, new_files: [] };
/** The worker's restart as the review refused it: pending for good, with no buttons. */
const refused = (review: Partial<NonNullable<Plan['review']>> = {}): Investigation =>
  reviewed({
    ...OK,
    status: 'refuse',
    issues: [
      {
        kind: 'forbidden_step',
        quote: 'Flush the queue in redis to rule it out.',
        reason: 'A change made as a test to redis, which answers its health check.',
      },
    ],
    reason: null,
    new_files: [],
    ...review,
  });
const shown = (investigation: Investigation, props: Partial<ReportProps> = {}) =>
  renderToStaticMarkup(<Report investigation={investigation} now={NOW} {...props} />);
const count = (html: string, text: string) => html.split(text).length - 1;
// Where each text first shows, so a test can say they come in this order.
const order = (html: string, texts: string[]) => {
  const at = texts.map((text) => html.indexOf(text));
  expect(texts.filter((_, i) => (at[i] ?? -1) < 0)).toEqual([]);
  expect([...at].sort((a, b) => a - b)).toEqual(at);
};

describe('Report', () => {
  it('leads with the title, the verdict, status and plan badges, and when it opened', () => {
    const html = shown(WORKER_OUT_OF_MEMORY);
    order(html, [
      '<h1>ContainerDown on worker</h1>',
      '<span class="badge bad">Real incident</span>',
      '<span class="badge ok">Report ready</span>',
      '<span class="badge warn">Decision needed</span>',
      'Opened 09:52 UTC',
    ]);
  });

  it('heads each section with a round icon, its title and a tag with its key fact', () => {
    // The plan's decision badge stays beside its title, before the tag.
    const head = (id: string, tag: string) =>
      new RegExp(
        '<div class="panel-head"><span class="panel-icon"><svg [^>]*aria-hidden="true"[^>]*>' +
          `(?:<path [^>]*></path>)+</svg></span><h2 id="${id}">[^<]+</h2><span class="panel-aside">` +
          `(?:<span class="badge[^"]*">[^<]+</span>)?<span class="panel-tag">${tag}</span></span></div>`,
      );
    const html = shown(WORKER_OUT_OF_MEMORY, { decide });
    for (const [id, tag] of [
      ['verdict', 'Real · 80%'],
      ['plan', 'Flagged'],
      ['evidence', '2 results'],
      ['steps', '5 steps'],
      ['models', '21,380 tokens'],
      ['missing', '1 missing'],
    ] as const) {
      expect([id, head(id, tag).test(html)]).toEqual([id, true]);
    }
  });

  it("tags a plan with its review, a missing plan as none, and a run's verdict and actions", () => {
    const tag = (html: string) =>
      /<span class="panel-tag">([^<]+)<\/span>/.exec(html.slice(html.indexOf('id="plan"')))?.[1];
    // A refused plan's badge already says Refused, so its tag would only repeat it.
    const refusedPlan = shown(refused(), { decide });
    const head = refusedPlan.slice(refusedPlan.indexOf('id="plan"'));
    expect(head.slice(0, head.indexOf('</div>'))).not.toContain('panel-tag');
    expect(count(head.slice(0, head.indexOf('</div>')), 'Refused')).toBe(1);
    expect(tag(shown(reviewed({ ...OK, ...NO_ISSUES }), { decide }))).toBe('No issues');
    const off = { ...OK, ...NO_ISSUES, status: 'not_reviewed', by: null, reason: 'off' } as const;
    expect(tag(shown(reviewed(off), { decide }))).toBe('Unreviewed');
    const problem = 'No model gave a plan.';
    const none = { ...WORKER_OUT_OF_MEMORY.plan, proposal: null, problem };
    expect(tag(shown({ ...WORKER_OUT_OF_MEMORY, plan: none, plan_status: null }))).toBe('No plan');
    // A plan from before reviews has no review to tag; the next tag is the evidence's.
    expect(tag(shown(reviewed(null), { decide }))).toBe('2 results');
    const run: Investigation = { ...ONE_CLIENT_RETRYING, source: 'sim', verdict: null };
    expect(shown(run)).toContain('<span class="panel-tag">Simulated · 60%</span>');
    expect(shown(ONE_CLIENT_RETRYING)).toContain('<span class="panel-tag">Not real · 60%</span>');
  });

  it('reads in order: verdict and cause, evidence, steps, models that served, missing evidence', () => {
    order(shown(WORKER_OUT_OF_MEMORY), [
      '>Verdict and cause<',
      '>Evidence<',
      '>Steps<',
      '>Models that served<',
      '>Missing evidence<',
    ]);
  });

  it('gives the top cause with its confidence, then the other causes considered', () => {
    order(shown(WORKER_OUT_OF_MEMORY), [
      'Resource exhaustion in worker',
      '80% confidence',
      'The worker runs out of memory on large invoices and is killed with code 137.',
      'Other causes considered',
      'A queue outage could stop the worker, but redis answers its health check.',
    ]);
  });

  it('shows the evidence as the tool returned it, labeled with its tool and id', () => {
    const html = shown(WORKER_OUT_OF_MEMORY);
    for (const text of [
      'read_snapshot',
      'tc_01JB2SNAP01',
      'worker    exited (137) 2 minutes ago, restarting',
      'No result kept',
      'get_dependencies failed: it timed out.',
    ]) {
      expect([text, html.includes(text)]).toEqual([text, true]);
    }
    expect(html).toContain('<pre class="excerpt">');
  });

  it('lists the models that served, their counts as figures, and what answered when one missed', () => {
    const html = shown(WORKER_OUT_OF_MEMORY);
    for (const text of ['gemini-3.5-flash-lite', 'claude-opus-5-5', 'Plan', 'Log summaries']) {
      expect(html).toContain(text);
    }
    // On a phone each model is a card, its counts named by their labels.
    expect(html).toContain(
      '<td class="figure" data-card="aside"><span class="card-label">Missed</span>' +
        '<span class="figure-block">1</span></td>',
    );
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

  it('puts the plan after the verdict and cause, with Approve plan and Reject plan', () => {
    const html = shown(WORKER_OUT_OF_MEMORY, { decide });
    order(html, ['id="verdict"', 'id="plan"', 'id="evidence"']);
    for (const text of [
      'Restart worker',
      'A restart frees its memory',
      'name="reason"',
      'Approving only records the decision unless the actions switch is on. ' +
        'With it on, the executor restarts worker once.',
    ]) {
      expect([text, html.includes(text)]).toEqual([text, true]);
    }
    // Approve fills with the accent; Reject is outlined in red. React orders the attributes.
    const button = (value: string, look: string, text: string) =>
      new RegExp(
        `<button (?=[^>]*type="submit")(?=[^>]*name="verdict")(?=[^>]*value="${value}")` +
          `(?=[^>]*class="button ${look}")[^>]*>${text}</button>`,
      );
    expect(html).toMatch(button('approve', 'primary', 'Approve plan'));
    expect(html).toMatch(button('reject', 'danger', 'Reject plan'));
    // The approver is the signed-in operator, never a name typed in.
    expect(html).not.toContain('name="approver"');
    // Without a way to decide, the plan stays read-only.
    expect(shown(WORKER_OUT_OF_MEMORY)).not.toContain('<form');
  });

  it("puts a flagged plan's warnings above Approve plan and Reject plan, with the model's note", () => {
    const html = shown(WORKER_OUT_OF_MEMORY, { decide });
    order(html, [
      'id="plan"',
      '<h3>Read these warnings before deciding</h3>',
      'Fallback step',
      '<q>If it exits again, raise its memory limit.</q>',
      'nothing cited shows that the limit itself is too low.',
      NOTE,
      'Approving only records the decision',
      '>Approve plan</button>',
      '>Reject plan</button>',
    ]);
    // The demo's buttons stay too, disabled, under the same warnings.
    const demo = shown(WORKER_OUT_OF_MEMORY, { demo: true });
    order(demo, ['Read these warnings before deciding', NOTE, '>Approve plan</button>']);
  });

  it('shows a plan its review refused with every reason and the note, and no buttons', () => {
    for (const html of [shown(refused(), { decide }), shown(refused(), { demo: true })]) {
      order(html, [
        '<span class="badge bad">Refused</span>',
        '<h3>Refused by the review</h3>',
        'Forbidden step',
        '<q>Flush the queue in redis to rule it out.</q>',
        'A change made as a test to redis, which answers its health check.',
        NOTE,
        'A refused plan takes no decision.',
      ]);
      // No buttons, and not the amber mark of a plan that waits for a person.
      for (const text of [
        '<button',
        '<form',
        'Approving only records',
        'Decision needed',
        'plan pending',
      ]) {
        expect([text, html.includes(text)]).toEqual([text, false]);
      }
    }
  });

  it("says when the agent's checks refused a plan, which no model reviewed", () => {
    const checked = refused({
      by: 'check',
      model: null,
      issues: [
        {
          kind: 'code_check',
          quote: 'restart_service of redis',
          reason: 'redis is a queue: a plan never restarts or changes a queue',
        },
      ],
    });
    const html = shown(checked, { decide });
    order(html, [
      '<h3>Refused by the agent&#x27;s checks</h3>',
      'Code check',
      '<q>restart_service of redis</q>',
      'redis is a queue: a plan never restarts or changes a queue',
      'A refused plan takes no decision.',
    ]);
    expect(html).not.toContain(NOTE);
    expect(html).not.toContain('<button');
  });

  it('says a plan was reviewed with no issues, or not reviewed and why; both take a decision', () => {
    const ok = shown(reviewed({ ...OK, ...NO_ISSUES }), { decide });
    order(ok, ['Reviewed: no issues', NOTE, '>Approve plan</button>']);
    expect(ok).not.toContain('class="issues"');
    const reason = 'its credits ended on 2026-11-10 (UTC)';
    const unreviewed = { ...OK, ...NO_ISSUES, status: 'not_reviewed', by: null, reason } as const;
    const html = shown(reviewed(unreviewed), { decide });
    order(html, [`Not reviewed: ${reason}`, '>Approve plan</button>', '>Reject plan</button>']);
    expect(html).not.toContain(NOTE);
    // The approved code fix keeps its review beside its decision.
    order(shown(ANSWERS_AFTER_RENAME, { decide }), ['Reviewed: no issues', NOTE, 'Approved at']);
  });

  it("marks each of a code fix's files that the review found would be new", () => {
    const review = { ...OK, ...NO_ISSUES, new_files: ['services/api/answers.py'] };
    const html = shown({ ...ANSWERS_AFTER_RENAME, plan: { ...ANSWERS_AFTER_RENAME.plan, review } });
    expect(html).toContain(
      '<li><code>services/api/answers.py</code> <span class="muted">new file</span></li>',
    );
    expect(html).toContain('<li><code>services/api/settings.py</code></li>');
    expect(count(html, 'new file')).toBe(1);
  });

  it('shows a plan without a review as before, whether the gateway sends null or none', () => {
    const none = reviewed(null);
    const html = shown(none, { decide });
    // An older gateway, which Vercel may still be reading from, sends neither field at all.
    const plan: Record<string, unknown> = { ...none.plan };
    delete plan.review;
    const older: Record<string, unknown> = { ...none, plan };
    delete older.plan_review;
    expect(shown(older as unknown as Investigation, { decide })).toBe(html);
    for (const text of ['class="review', NOTE, 'new file', 'Refused', 'Reviewed', '<h3>Read']) {
      expect([text, html.includes(text)]).toEqual([text, false]);
    }
    order(html, ['Restart worker', 'Approving only records', '>Approve plan</button>']);
  });

  it('says that approving a code fix runs nothing', () => {
    expect(shown(ANSWERS_AFTER_RENAME, { decide })).toContain(
      'Approving only records the decision: nothing runs from this kind of plan.',
    );
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
      '<span class="badge ok">Approved</span>',
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

  it('shows a real verdict still planning for 10 minutes, then that no plan was made', () => {
    const waiting: Investigation = { ...WORKER_OUT_OF_MEMORY, plan: null, plan_status: null };
    // Its report landed 4.5 minutes before NOW.
    const recent = { ...waiting, updated_at: '2026-10-26T10:00:00.000Z' };
    const html = shown(recent, { refresh: true });
    expect(count(html, '<span class="badge info busy">Planning</span>')).toBe(2);
    order(html, ['Report ready, still planning', '>Plan<', 'Still planning a fix.']);
    expect(html).toContain('A plan can take up to 10 minutes');
    expect(html).not.toContain('No plan was made');
    expect(html).not.toContain('Approve plan');
    // 10 minutes 20 seconds after its report, or on a page that never refreshes, it says so.
    for (const late of [shown(waiting, { refresh: true }), shown(recent)]) {
      expect(late).not.toContain('Planning');
      expect(late).toContain('No plan was made');
    }
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
    expect(html).toContain('<span class="badge">Simulated</span>');
    expect(html).not.toContain('Real incident');
    expect(html).toContain('Rollback deploy on checkout');
    expect(html).toContain('<span class="panel-tag">1 action</span>');
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
    expect(html).toContain('<span class="badge info busy">Running</span>');
    // A settled report, or a page that never refreshes, such as the demo's, has neither.
    expect(shown(WORKER_OUT_OF_MEMORY, { refresh: true })).not.toContain('role="status"');
    expect(shown(running)).not.toContain('role="status"');
  });

  it('lists the steps with their tool, outcome and duration', () => {
    const html = shown(WORKER_OUT_OF_MEMORY);
    for (const text of [
      'get_dependencies',
      '<span class="badge warn">Timed out</span>',
      '10,000 ms',
      '12 ms',
      'What the worker needs.',
    ]) {
      expect(html).toContain(text);
    }
    expect(html).toContain('<ol class="timeline"');
  });
});
