'use client';

import Link from 'next/link';
import { usePathname, useSearchParams } from 'next/navigation';
import { type ReactNode, useRef, useState } from 'react';

import { TABS } from '../lib/report.ts';
import { type Theme, THEME_LABEL, themeCookie, THEMES } from '../lib/theme.ts';
import { Icon } from './icons.tsx';

const SOURCES = ['live', 'sim'] as const;

/** The two lists: the live ones, or the demo's own inside the demo. The one shown is marked. */
export function NavLinks() {
  const pathname = usePathname();
  const shown = useSearchParams().get('source') === 'sim' ? 'sim' : 'live';
  const base = pathname === '/demo' || pathname.startsWith('/demo/') ? '/demo' : '/investigations';
  return SOURCES.map((source) => (
    <Link
      key={source}
      href={`${base}?source=${source}`}
      aria-current={pathname === base && source === shown ? 'page' : undefined}
    >
      {TABS[source]}
    </Link>
  ));
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
