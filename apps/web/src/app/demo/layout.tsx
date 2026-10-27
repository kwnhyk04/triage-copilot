import Link from 'next/link';
import type { ReactNode } from 'react';

/** The public demo's banner and header: saved reports, readable without signing in. */
export default function Demo({ children }: { children: ReactNode }) {
  return (
    <>
      <div className="demo-banner" role="region" aria-label="About the demo">
        <div>
          <p>
            <strong>Demo.</strong> These are saved reports, open without signing in. The eval runs
            are recorded runs on simulated incidents. The three live incidents are illustrative
            samples written by hand, not real incidents. The plan buttons do nothing here.
          </p>
        </div>
      </div>
      <header className="site-header">
        <Link className="brand" href="/">
          Triage Copilot
        </Link>
        <Link className="button quiet" href="/login">
          Sign in
        </Link>
      </header>
      {children}
    </>
  );
}
