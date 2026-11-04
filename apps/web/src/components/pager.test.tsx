import { renderToStaticMarkup } from 'react-dom/server';

import { Pager } from './pager.tsx';

// Where the list is: the pager reads it through the App Router, which a static render does not
// mount.
const mockAt = { pathname: '/investigations', search: 'source=live&page=2&size=20' };
jest.mock('next/navigation', () => ({
  usePathname: () => mockAt.pathname,
  useSearchParams: () => new URLSearchParams(mockAt.search),
  useRouter: () => ({ push: () => undefined, replace: () => undefined }),
}));

const shown = (page: number, total: number, size = 20) =>
  renderToStaticMarkup(<Pager page={page} size={size} total={total} />);
const count = (html: string, text: string) => html.split(text).length - 1;
const escape = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
/** An element with these attributes in any order, as React and next/link order them. */
const tag = (name: string, attrs: Record<string, string>, inner = '') =>
  new RegExp(
    `<${name} ${Object.entries(attrs)
      .map(([key, value]) => `(?=[^>]*${key}="${escape(value)}")`)
      .join('')}[^>]*>${inner}`,
  );
const at = (page: number, size = 20) =>
  `/investigations?source=live&amp;page=${String(page)}&amp;size=${String(size)}`;

describe('Pager', () => {
  beforeEach(() => {
    mockAt.pathname = '/investigations';
    mockAt.search = 'source=live&page=2&size=20';
  });

  it('says which page of how many, and says it again when the page changes', () => {
    expect(shown(2, 95)).toContain(
      '<p class="pager-status" aria-live="polite">Page <span>2</span> of <span>5</span></p>',
    );
  });

  it('links Previous, Next and each page, keeping the source and size, the current one marked', () => {
    const html = shown(2, 95);
    expect(html).toMatch(tag('nav', { 'aria-label': 'Pagination' }));
    expect(html).toMatch(tag('a', { href: at(1), 'aria-label': 'Previous page' }));
    expect(html).toMatch(tag('a', { href: at(3), 'aria-label': 'Next page' }));
    for (const page of [1, 3, 4, 5]) {
      expect(html).toMatch(tag('a', { href: at(page), 'aria-label': `Page ${String(page)}` }));
    }
    expect(html).toMatch(tag('a', { href: at(2), 'aria-current': 'page' }, '2</a>'));
    expect(count(html, 'aria-current')).toBe(1);
  });

  it('disables Previous on the first page and Next on the last', () => {
    const first = shown(1, 95);
    expect(first).toMatch(
      tag('button', { type: 'button', disabled: '', 'aria-label': 'Previous page' }),
    );
    expect(first).toMatch(tag('a', { href: at(2), 'aria-label': 'Next page' }));
    const last = shown(5, 95);
    expect(last).toMatch(
      tag('button', { type: 'button', disabled: '', 'aria-label': 'Next page' }),
    );
    expect(last).toMatch(tag('a', { href: at(4), 'aria-label': 'Previous page' }));
    // A single page has nowhere to go either way.
    expect(count(shown(1, 3), 'disabled=""')).toBe(2);
  });

  it('marks the gaps between page numbers with an ellipsis that screen readers skip', () => {
    const html = shown(10, 400);
    expect(count(html, '<li class="pager-gap" aria-hidden="true">…</li>')).toBe(2);
    for (const page of [1, 9, 10, 11, 20]) expect(html).toContain(`>${String(page)}</a>`);
    expect(html).not.toContain('>12</a>');
  });

  it('offers 10, 20 and 50 a page under a label, the current size chosen', () => {
    const html = shown(1, 95, 50);
    expect(html).toContain('<span class="visually-hidden">Results per page</span>');
    for (const size of [10, 20]) {
      expect(html).toContain(`<option value="${String(size)}">${String(size)} / page</option>`);
    }
    expect(html).toContain('<option value="50" selected="">50 / page</option>');
  });

  it("keeps the demo's other settings in its links, and drops an old cursor", () => {
    mockAt.pathname = '/demo';
    mockAt.search = 'offline=1&source=sim&cursor=abc';
    expect(shown(1, 45)).toMatch(
      tag('a', {
        href: '/demo?offline=1&amp;source=sim&amp;page=2&amp;size=20',
        'aria-label': 'Next page',
      }),
    );
  });
});
