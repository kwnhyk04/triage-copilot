import { renderToStaticMarkup } from 'react-dom/server';

import { LIVE_SAMPLES, WORKER_OUT_OF_MEMORY } from '../demo/live-samples.ts';
import { InvestigationList, type ListProps } from './investigation-list.tsx';

// LiveRefresh and the pager use the App Router, which a static render does not mount.
jest.mock('next/navigation', () => ({
  useRouter: () => ({ refresh: () => undefined, push: () => undefined, replace: () => undefined }),
  usePathname: () => '/demo',
  useSearchParams: () => new URLSearchParams('source=live'),
}));

const NOW = Date.parse('2026-10-26T10:04:30Z');
const shown = (props: Partial<ListProps> = {}) =>
  renderToStaticMarkup(
    <InvestigationList
      items={LIVE_SAMPLES}
      source="live"
      total={LIVE_SAMPLES.length}
      page={1}
      size={20}
      basePath="/investigations"
      now={NOW}
      {...props}
    />,
  );
const count = (html: string, text: string) => html.split(text).length - 1;
const headers = (html: string) =>
  [...html.matchAll(/<th scope="col"[^>]*>([^<]*)<\/th>/g)].map((match) => match[1]);
const run = { ...WORKER_OUT_OF_MEMORY, source: 'sim' as const, verdict: null, plan_status: null };

describe('InvestigationList', () => {
  it('lists live incidents as a table: incident, verdict, cause, confidence, plan, status, age', () => {
    const html = shown();
    expect(headers(html)).toEqual([
      'Incident',
      'Verdict',
      'Cause',
      'Confidence',
      'Plan',
      'Status',
      'Opened',
    ]);
    expect(html).toContain(
      '<caption class="visually-hidden">Live incidents, newest first</caption>',
    );
    for (const text of [
      'ContainerDown on worker',
      'AnswersFailing on api',
      'ErrorBurst on api',
      'Resource exhaustion in worker',
      '12 min ago',
    ]) {
      expect([text, html.includes(text)]).toEqual([text, true]);
    }
  });

  it('shows the verdict, plan and status as badges, red only for a real incident', () => {
    const html = shown();
    expect(count(html, '<span class="badge bad">Real incident</span>')).toBe(2);
    expect(html).toContain('<span class="badge">Not a real incident</span>');
    // Only the pending plan needs a decision.
    expect(count(html, 'Decision needed')).toBe(1);
    expect(html).toContain('<span class="badge warn">Decision needed</span>');
    expect(html).toContain('<span class="badge ok">Approved</span>');
    expect(count(html, '<span class="badge ok">Report ready</span>')).toBe(3);
  });

  it('shows a plan its review refused as a red Refused badge, never as still planning', () => {
    const refused = {
      ...WORKER_OUT_OF_MEMORY,
      plan_status: 'pending' as const,
      plan_review: 'refuse' as const,
      updated_at: '2026-10-26T10:00:00.000Z',
    };
    const html = shown({ items: [refused], refresh: true });
    expect(html).toContain('<td data-card="aside"><span class="badge bad">Refused</span></td>');
    expect(html).not.toContain('Decision needed');
    expect(html).not.toContain('Planning');
  });

  it('shows a plan no model reviewed as one that cannot be approved, never as needing a decision', () => {
    const unreviewed = LIVE_SAMPLES.map((item) => ({
      ...item,
      plan_review: 'not_reviewed' as const,
    }));
    const blocked = '<span class="badge bad">Not reviewed, cannot be approved</span>';
    expect(shown({ items: unreviewed })).toContain(blocked);
    expect(shown({ items: unreviewed })).not.toContain('Decision needed');
    // An older gateway sends no plan_review, which reads the same as null.
    const items = LIVE_SAMPLES.map((item) => ({ ...item, plan_review: null }));
    const older = items.map((item) => {
      const copy: Record<string, unknown> = { ...item };
      delete copy.plan_review;
      return copy;
    }) as unknown as typeof items;
    expect(shown({ items: older })).toBe(shown({ items }));
    expect(shown({ items })).toContain(blocked);
  });

  it('fixes the columns and centres the confidence as a block whose digits line up', () => {
    const html = shown();
    expect(html).toContain('<table class="list-table cards fixed incidents">');
    expect(count(html, '<col/>')).toBe(7);
    expect(html).toContain(
      '<td class="figure" data-card="hide"><span class="figure-block">80%</span>',
    );
  });

  it('names the whole incident and cause in a title, as a narrow table cuts them short', () => {
    const html = shown();
    expect(html).toMatch(
      /<a (?=[^>]*title="ContainerDown on worker")[^>]*>ContainerDown on worker<\/a>/,
    );
    expect(html).toContain(
      '<td class="cause-cell" title="Resource exhaustion in worker" data-card="wide">',
    );
  });

  it('turns each row into a card on a phone, the confidence joining the cause', () => {
    const html = shown();
    for (const role of ['title', 'aside', 'meta', 'wide', 'hide']) {
      expect([role, html.includes(`data-card="${role}"`)]).toEqual([role, true]);
    }
    expect(html).toContain('<span class="phone-only">, 80%</span>');
  });

  it('lists eval runs without the verdict and plan columns, which they never fill', () => {
    const html = shown({ items: [run], source: 'sim' });
    expect(headers(html)).toEqual(['Run', 'Cause', 'Confidence', 'Status', 'Opened']);
    expect(html).toContain('<table class="list-table cards fixed runs">');
    expect(html).not.toContain('Real incident');
  });

  it('links each row to its report under the base path, and numbers the pages under the table', () => {
    const html = shown({ total: 45, page: 2, basePath: '/demo/investigations' });
    expect(html).toContain(`href="/demo/investigations/${WORKER_OUT_OF_MEMORY.id}"`);
    // Each row can be found again, so a new page size can bring the old first row into view.
    expect(html).toContain(`<tr id="investigation-${WORKER_OUT_OF_MEMORY.id}">`);
    expect(html.indexOf('</table>')).toBeLessThan(
      html.indexOf('Page <span>2</span> of <span>3</span>'),
    );
    expect(html).toContain('href="/demo?source=live&amp;page=3&amp;size=20"');
    expect(html).not.toContain('Older');
  });

  it('says when a tab is empty, and points to the demo for eval runs, which production keeps none of', () => {
    expect(shown({ items: [] })).toContain('No live incidents yet.');
    const sim = shown({ items: [], source: 'sim' });
    expect(sim).toContain('No eval runs here.');
    expect(sim).toContain('<a href="/demo?source=sim">');
    expect(sim).not.toContain('<table');
    // Nothing to page through either.
    expect(sim).not.toContain('Pagination');
  });

  it('labels the illustrative samples it is told of', () => {
    const illustrative = new Set([WORKER_OUT_OF_MEMORY.id]);
    const html = shown({ illustrative });
    expect(count(html, 'Illustrative sample, not a real incident')).toBe(1);
  });

  it('updates every 10 s while an item may still change, with a way to pause', () => {
    const running = { ...WORKER_OUT_OF_MEMORY, status: 'running' as const };
    const html = shown({ items: [running, ...LIVE_SAMPLES.slice(1)], refresh: true });
    expect(html).toContain('role="status"');
    expect(html).toContain('1 investigation is still running or planning');
    expect(html).toContain('>Pause updates</button>');
    expect(html).toContain('data-every="10"');
    expect(html).toContain('<span class="badge info busy">Running</span>');
    // Settled items, or a list that never refreshes, show neither.
    expect(shown({ refresh: true })).not.toContain('role="status"');
    expect(shown({ items: [running] })).not.toContain('role="status"');
  });

  it('shows a real verdict waiting for its plan as planning, while the list refreshes', () => {
    const waiting = { ...WORKER_OUT_OF_MEMORY, updated_at: '2026-10-26T10:00:00.000Z' };
    const items = [{ ...waiting, plan_status: null }];
    const html = shown({ items, refresh: true });
    expect(html).toContain('<td data-card="aside"><span class="badge info busy">Planning</span>');
    expect(html).toContain('1 investigation is still running or planning');
    // A list that never refreshes, such as the demo's, never says a plan is coming.
    expect(shown({ items })).not.toContain('Planning');
  });
});
