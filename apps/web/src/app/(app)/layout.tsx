import Link from 'next/link';
import type { ReactNode } from 'react';

/** The signed-in pages' header. The proxy and lib/data.ts check the session, not this layout. */
export default function SignedIn({ children }: { children: ReactNode }) {
  return (
    <>
      <header className="site-header">
        <Link className="brand" href="/investigations">
          Triage Copilot
        </Link>
        <form method="post" action="/logout">
          <button type="submit" className="button quiet">
            Sign out
          </button>
        </form>
      </header>
      {children}
    </>
  );
}
