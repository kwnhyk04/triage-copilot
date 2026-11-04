import { renderToStaticMarkup } from 'react-dom/server';

import type { Theme } from '../lib/theme.ts';
import { Header } from './header.tsx';

// Where the page is: the header's links read it through the App Router, which a static render
// does not mount.
const mockAt = { pathname: '/investigations', search: '' };
jest.mock('next/navigation', () => ({
  usePathname: () => mockAt.pathname,
  useSearchParams: () => new URLSearchParams(mockAt.search),
}));

const shown = (signedIn = false, theme: Theme = 'system') =>
  renderToStaticMarkup(<Header signedIn={signedIn} theme={theme} />);
const count = (html: string, text: string) => html.split(text).length - 1;

describe('Header', () => {
  beforeEach(() => {
    mockAt.pathname = '/investigations';
    mockAt.search = '';
  });

  it('names the app beside its logo, linking home', () => {
    const html = shown();
    expect(html).toMatch(
      /<a (?=[^>]*class="brand")(?=[^>]*href="\/")[^>]*><svg class="logo-mark"[^>]*aria-hidden="true"/,
    );
    expect(html).toContain('Triage Copilot</a>');
  });

  it('links the two lists, marking the one shown, in the bar and in the phone menu', () => {
    mockAt.search = 'source=sim';
    const html = shown();
    expect(count(html, '<a href="/investigations?source=live">Live incidents</a>')).toBe(2);
    expect(
      count(
        html,
        '<a aria-current="page" data-current="" href="/investigations?source=sim">Eval runs</a>',
      ),
    ).toBe(2);
    expect(html).toMatch(/<details class="menu"><summary aria-label="Menu">/);
  });

  it('underlines Live incidents on a report, which is no list page, so not as the current page', () => {
    for (const [pathname, base] of [
      ['/investigations/x', '/investigations'],
      ['/demo/investigations/x', '/demo'],
    ] as const) {
      mockAt.pathname = pathname;
      const html = shown();
      expect(count(html, `<a data-current="" href="${base}?source=live">Live incidents</a>`)).toBe(
        2,
      );
      expect(html).toContain(`<a href="${base}?source=sim">Eval runs</a>`);
      expect(html).not.toContain('aria-current');
    }
  });

  it('draws the sliding marks as hidden parts of the bar', () => {
    const html = shown();
    for (const mark of ['nav-glow', 'nav-line', 'nav-mark']) {
      expect(html).toContain(`<span class="${mark}" aria-hidden="true"></span>`);
    }
  });

  it('offers Sign in, or once signed in Sign out, which posts', () => {
    const out = shown();
    expect(count(out, '>Sign in</a>')).toBe(2);
    expect(out).not.toContain('/logout');
    const signedIn = shown(true);
    expect(count(signedIn, '<form action="/logout" method="post">')).toBe(2);
    expect(count(signedIn, '>Sign out</button>')).toBe(2);
    expect(signedIn).not.toContain('>Sign in<');
  });

  it('offers System, Light and Dark as the cookie says, and one button that steps them', () => {
    const html = shown(false, 'dark');
    expect(html).toContain('<legend class="visually-hidden">Theme</legend>');
    for (const theme of ['system', 'light', 'dark']) expect(html).toContain(`value="${theme}"`);
    expect(html).toContain('<input type="radio" name="theme" checked="" value="dark"/>');
    expect(count(html, 'checked=""')).toBe(1);
    expect(html).toContain('aria-label="Theme: Dark. Switch to System"');
  });
});
