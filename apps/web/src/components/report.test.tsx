import { renderToStaticMarkup } from 'react-dom/server';

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

  it('says when evidence has no result kept, and when there is no verdict', () => {
    const html = shown({ ...LIVE_TRIAGE, verdict: null });
    expect(html).toContain('No result kept');
    expect(html).toContain('No verdict');
    expect(shown({ ...LIVE_TRIAGE, verdict: 'not_real' })).toContain('Not a real incident');
  });
});
