import { renderToStaticMarkup } from 'react-dom/server';

import { LIVE_SAMPLES, WORKER_OUT_OF_MEMORY } from '../demo/live-samples.ts';
import { InvestigationList, type ListProps } from './investigation-list.tsx';

// LiveRefresh refreshes through the App Router, which a static render does not mount.
jest.mock('next/navigation', () => ({ useRouter: () => ({ refresh: () => undefined }) }));

const NOW = Date.parse('2026-10-26T10:04:30Z');
const shown = (props: Partial<ListProps> = {}) =>
  renderToStaticMarkup(
    <InvestigationList
      items={LIVE_SAMPLES}
      source="live"
      nextCursor={null}
      basePath="/investigations"
      now={NOW}
      {...props}
    />,
  );
const count = (html: string, text: string) => html.split(text).length - 1;

describe('InvestigationList', () => {
  it('draws each investigation as a tag: verdict, title, cause, status and age', () => {
    const html = shown();
    for (const text of [
      'Real incident',
      'Not a real incident',
      'ContainerDown on worker',
      'Resource exhaustion in worker, 80%',
      'Report ready, opened 12 min ago',
      'AnswersFailing on api',
      'ErrorBurst on api',
    ]) {
      expect([text, html.includes(text)]).toEqual([text, true]);
    }
    // Only the pending plan needs a decision.
    expect(count(html, 'Decision needed')).toBe(1);
  });

  it('links each tag to its report under the base path, and older items to the next page', () => {
    const html = shown({ nextCursor: 'abc=', basePath: '/demo/investigations', listPath: '/demo' });
    expect(html).toContain(`href="/demo/investigations/${WORKER_OUT_OF_MEMORY.id}"`);
    expect(html).toContain('href="/demo?source=live&amp;cursor=abc%3D"');
    expect(html).toContain('>Older</a>');
    expect(shown()).not.toContain('Older');
  });

  it('says when a tab is empty', () => {
    expect(shown({ items: [] })).toContain('No live incidents yet.');
    expect(shown({ items: [], source: 'sim' })).toContain('No eval runs yet.');
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
    expect(html).toContain('1 investigation is still running');
    expect(html).toContain('>Pause updates</button>');
    expect(html).toContain('data-every="10"');
    // Settled items, or a list that never refreshes, show neither.
    expect(shown({ refresh: true })).not.toContain('role="status"');
    expect(shown({ items: [running] })).not.toContain('role="status"');
  });

  it('shows an eval run without a verdict as simulated', () => {
    const run = {
      ...WORKER_OUT_OF_MEMORY,
      source: 'sim' as const,
      verdict: null,
      plan_status: null,
    };
    const html = shown({ items: [run], source: 'sim' });
    expect(html).toContain('Simulated');
    expect(html).not.toContain('Real incident');
  });
});
