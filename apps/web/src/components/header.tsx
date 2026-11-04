import { cookies } from 'next/headers';
import Link from 'next/link';

import { signedIn } from '../lib/auth.ts';
import { HOME } from '../lib/guards.ts';
import { parseTheme, type Theme, THEME_COOKIE } from '../lib/theme.ts';
import { Menu, NavBar, NavLinks, ThemeSwitch } from './client.tsx';
import { LogoMark } from './logo.tsx';

/**
 * The bar for the page being rendered, from the request's own session check and theme cookie.
 * Every page renders it, so the bar and the page read the same request. A layout would keep its
 * first bar across client-side navigation, and its Sign out could outlive the session.
 */
export async function SiteHeader() {
  const theme = parseTheme((await cookies()).get(THEME_COOKIE)?.value);
  return <Header signedIn={await signedIn()} theme={theme} />;
}

/**
 * The bar: the mark and name, the two lists, the theme, and Sign in or Sign out. On a phone the
 * lists and the sign-in fold into the menu. The proxy and lib/data.ts guard the pages; signedIn
 * only picks the button.
 */
export function Header({ signedIn, theme }: { signedIn: boolean; theme: Theme }) {
  const account = signedIn ? (
    <form method="post" action="/logout">
      <button type="submit" className="nav-item">
        Sign out
      </button>
    </form>
  ) : (
    <Link href="/login" className="nav-item">
      Sign in
    </Link>
  );
  return (
    <header className="top">
      <div className="top-bar">
        <Link href={signedIn ? HOME : '/'} className="brand">
          <LogoMark />
          Triage Copilot
        </Link>
        <NavBar />
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
