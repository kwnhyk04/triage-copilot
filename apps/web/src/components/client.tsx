'use client';

import Link from 'next/link';
import { usePathname, useSearchParams } from 'next/navigation';
import { type ReactNode, useEffect, useRef, useState, useSyncExternalStore } from 'react';

import { DEFAULT_SIZE, storedSize } from '../lib/paging.ts';
import { TABS } from '../lib/report.ts';
import { type Theme, THEME_LABEL, themeCookie, THEMES } from '../lib/theme.ts';
import { Icon } from './icons.tsx';

const SOURCES = ['live', 'sim'] as const;
const onStorage = (changed: () => void) => {
  window.addEventListener('storage', changed);
  return () => {
    window.removeEventListener('storage', changed);
  };
};

/**
 * The two lists: the live ones, or the demo's own inside the demo. The one shown is marked. Each
 * opens at page 1, at the size last chosen once the browser has read it; the server renders none.
 */
export function NavLinks() {
  const pathname = usePathname();
  const shown = useSearchParams().get('source') === 'sim' ? 'sim' : 'live';
  const size = useSyncExternalStore(onStorage, storedSize, () => undefined);
  const sized = size === undefined || size === DEFAULT_SIZE ? '' : `&size=${String(size)}`;
  const base = pathname === '/demo' || pathname.startsWith('/demo/') ? '/demo' : '/investigations';
  const list = pathname === base;
  // A report sits under Live incidents, though it is no list page.
  const report = pathname.startsWith(`${base === '/demo' ? '/demo' : ''}/investigations/`);
  return SOURCES.map((source) => (
    <Link
      key={source}
      href={`${base}?source=${source}${sized}`}
      aria-current={list && source === shown ? 'page' : undefined}
      data-current={(list && source === shown) || (report && source === 'live') ? '' : undefined}
    >
      {TABS[source]}
    </Link>
  ));
}

/** Puts a mark under `link`, sliding from where it was; a hidden mark appears in place. */
function place(mark: HTMLElement | null | undefined, link: Element | null) {
  if (!mark) return;
  if (!(link instanceof HTMLElement)) {
    mark.removeAttribute('data-shown');
    return;
  }
  const appear = !mark.hasAttribute('data-shown');
  if (appear) mark.setAttribute('data-jump', '');
  mark.style.setProperty('--x', `${String(link.offsetLeft)}px`);
  mark.style.setProperty('--w', `${String(link.offsetWidth)}px`);
  mark.setAttribute('data-shown', '');
  if (appear) {
    mark.getBoundingClientRect();
    mark.removeAttribute('data-jump');
  }
}

/**
 * The bar's two lists in the tabs' look: a tint and an underline follow the pointer or keyboard
 * focus from item to item, and a second underline marks the list shown. Without script the marked
 * link draws its own underline.
 */
export function NavBar() {
  const nav = useRef<HTMLElement>(null);
  const pathname = usePathname();
  const search = useSearchParams().toString();
  const marks = (names: string[], link: Element | null) => {
    for (const name of names) place(nav.current?.querySelector<HTMLElement>(name), link);
  };
  useEffect(() => {
    const bar = nav.current;
    if (!bar) return undefined;
    const mark = () => {
      place(bar.querySelector<HTMLElement>('.nav-mark'), bar.querySelector('a[data-current]'));
    };
    mark();
    bar.setAttribute('data-ready', '');
    void document.fonts.ready.then(mark);
    window.addEventListener('resize', mark);
    return () => {
      window.removeEventListener('resize', mark);
    };
  }, [pathname, search]);
  return (
    <nav
      ref={nav}
      className="top-nav"
      aria-label="Main"
      onPointerOver={(event) => {
        marks(['.nav-glow', '.nav-line'], (event.target as Element).closest('a'));
      }}
      onPointerLeave={() => {
        marks(['.nav-glow', '.nav-line'], nav.current?.querySelector('a:focus-visible') ?? null);
      }}
      onFocus={(event) => {
        if (event.target.matches(':focus-visible')) marks(['.nav-glow', '.nav-line'], event.target);
      }}
      onBlur={(event) => {
        if (!nav.current?.contains(event.relatedTarget)) marks(['.nav-glow', '.nav-line'], null);
      }}
    >
      <span className="nav-glow" aria-hidden="true"></span>
      <NavLinks />
      <span className="nav-line" aria-hidden="true"></span>
      <span className="nav-mark" aria-hidden="true"></span>
    </nav>
  );
}

/** Paints the page in the chosen theme at once, and keeps it in the cookie for the server. */
function applyTheme(theme: Theme) {
  document.cookie = themeCookie(theme);
  const root = document.documentElement;
  if (theme === 'system') root.removeAttribute('data-theme');
  else root.setAttribute('data-theme', theme);
}

/**
 * System, Light or Dark as one radio group of icons, each radio named: a choice applies at once
 * and its cookie keeps it, so the server renders the next page in it. On a phone one button steps
 * through the three instead.
 */
export function ThemeSwitch({ initial }: { initial: Theme }) {
  const [theme, setTheme] = useState(initial);
  const choose = (next: Theme) => {
    setTheme(next);
    applyTheme(next);
  };
  const next = THEMES[(THEMES.indexOf(theme) + 1) % THEMES.length] ?? 'system';
  return (
    <>
      <fieldset className="theme-switch">
        <legend className="visually-hidden">Theme</legend>
        {THEMES.map((each) => (
          <label key={each} title={THEME_LABEL[each]}>
            <input
              type="radio"
              name="theme"
              value={each}
              checked={theme === each}
              onChange={() => {
                choose(each);
              }}
            />
            <Icon name={each} size={16} />
            <span className="visually-hidden">{THEME_LABEL[each]}</span>
          </label>
        ))}
      </fieldset>
      <button
        type="button"
        className="theme-cycle"
        onClick={() => {
          choose(next);
        }}
        aria-label={`Theme: ${THEME_LABEL[theme]}. Switch to ${THEME_LABEL[next]}`}
      >
        <Icon name={theme} size={18} />
      </button>
    </>
  );
}

/** The phone menu: a native disclosure under the bar, closed again once one of its links is
 * followed, since the bar stays mounted across pages. */
export function Menu({ children }: { children: ReactNode }) {
  const menu = useRef<HTMLDetailsElement>(null);
  return (
    <details
      ref={menu}
      className="menu"
      onClick={(event) => {
        if ((event.target as HTMLElement).closest('a')) menu.current?.removeAttribute('open');
      }}
    >
      <summary aria-label="Menu">
        <Icon name="menu" />
      </summary>
      <div className="menu-panel">{children}</div>
    </details>
  );
}
