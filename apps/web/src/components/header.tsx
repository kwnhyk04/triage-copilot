import Link from 'next/link';

import type { Theme } from '../lib/theme.ts';
import { Menu, NavLinks, ThemeSwitch } from './client.tsx';
import { LogoMark } from './logo.tsx';

/**
 * Every page's bar: the mark and name, the two lists, the theme, and Sign in or Sign out. On a
 * phone the lists and the sign-in fold into the menu. The proxy and lib/data.ts guard the pages;
 * signedIn only picks the button.
 */
export function Header({ signedIn, theme }: { signedIn: boolean; theme: Theme }) {
  const account = signedIn ? (
    <form method="post" action="/logout">
      <button type="submit" className="button">
        Sign out
      </button>
    </form>
  ) : (
    <Link href="/login" className="button">
      Sign in
    </Link>
  );
  return (
    <header className="top">
      <div className="top-bar">
        <Link href="/" className="brand">
          <LogoMark />
          Triage Copilot
        </Link>
        <nav className="top-nav" aria-label="Main">
          <NavLinks />
        </nav>
        <div className="top-actions">
          <ThemeSwitch initial={theme} />
          <div className="account">{account}</div>
          <Menu>
            <nav aria-label="Main">
              <NavLinks />
            </nav>
            {account}
          </Menu>
        </div>
      </div>
    </header>
  );
}
