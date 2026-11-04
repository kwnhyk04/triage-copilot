import { renderToStaticMarkup } from 'react-dom/server';

import { EVAL_RUNS } from '../demo/index.ts';
import { Hero, HERO_RUN_ID, heroReport } from './hero.tsx';

const run = EVAL_RUNS.find((item) => item.id === HERO_RUN_ID);
const count = (html: string, text: string) => html.split(text).length - 1;

describe('heroReport', () => {
  it('takes a recorded eval run from the saved demo data', () => {
    expect(run?.source).toBe('sim');
  });

  it('shows the alert, verdict, cause and confidence, as the report does', () => {
    expect(heroReport()).toMatchObject({
      href: `/demo/investigations/${HERO_RUN_ID}`,
      title: 'CheckoutHighErrorRate on checkout',
      verdict: { label: 'Simulated', tone: 'neutral' },
      cause: 'Bad deploy in checkout',
      confidence: '95%',
    });
  });

  it('quotes one line of each cited result, word for word, with its tool', () => {
    const { evidence } = heroReport() ?? { evidence: [] };
    expect(evidence.map((item) => item.tool)).toEqual([
      'list_deploys',
      'query_logs',
      'query_metrics',
    ]);
    const excerpts = run?.hypotheses[0]?.evidence.map((item) => item.excerpt.split('\n')) ?? [];
    evidence.forEach((item, i) => {
      expect(excerpts[i]).toContain(item.line);
    });
    expect(evidence[0]?.line).toContain('checkout v1.7.3 -> v1.8.0');
    expect(evidence[1]?.line).toMatch(/^\+20s checkout\/error java\.lang\.NullPointerException/);
    expect(evidence[2]?.line).toMatch(/^http_error_ratio\{service="checkout"\}/);
  });

  it('shows the proposed rollback and that it waits for a person', () => {
    expect(heroReport()?.action).toEqual({
      what: 'Rollback deploy on checkout',
      to: 'v1.7.3',
      state: { label: 'Decision needed', tone: 'warn' },
    });
  });
});

describe('Hero', () => {
  const shown = (signedIn = false) => renderToStaticMarkup(<Hero signedIn={signedIn} />);

  it('opens with the headline: no badge or link above it', () => {
    const html = shown();
    expect(html).toMatch(/^<section class="hero"[^>]*><h1/);
    expect(count(html, '<h1')).toBe(1);
  });

  it('has one button, to the demo when signed out', () => {
    const html = shown();
    expect(html).toContain(
      '<a class="button primary large hero-cta" href="/demo">Open the demo</a>',
    );
    expect(count(html, 'class="button')).toBe(1);
  });

  it('sends a signed-in reader to Live incidents instead', () => {
    const html = shown(true);
    expect(html).toContain(
      '<a class="button primary large hero-cta" href="/investigations">Open Live incidents</a>',
    );
    expect(html).not.toContain('Open the demo');
  });

  it('frames the saved report, its title linking to it in the demo', () => {
    const html = shown();
    expect(html).toContain(
      `<a href="/demo/investigations/${HERO_RUN_ID}">CheckoutHighErrorRate on checkout</a>`,
    );
    expect(html).toContain('Recorded eval run on a simulated incident');
    expect(html).toContain('<span class="badge">Simulated</span>');
    expect(html).toContain('<span class="badge warn">Decision needed</span>');
    expect(count(html, '<code>')).toBe(3);
  });

  it('draws no grid, and hides the horizon from assistive technology', () => {
    const html = shown();
    expect(html).not.toContain('hero-grid');
    expect(html).toContain('<div class="hero-horizon" aria-hidden="true"></div>');
  });

  it('uses no inline styles, which the CSP refuses', () => {
    expect(shown()).not.toContain('style=');
  });
});
