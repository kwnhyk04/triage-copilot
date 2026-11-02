'use client';

import Link from 'next/link';

import { LogoMark } from '../../components/logo.tsx';

/** Any error but an offline gateway. It cannot tell a bug from an ended session, so it offers both.
 * The page's header failed with the page, so the bar here keeps only the way home. */
export default function Failed({ retry }: { error: unknown; retry: () => void }) {
  return (
    <>
      <header className="top">
        <div className="top-bar">
          <Link href="/" className="brand">
            <LogoMark />
            Triage Copilot
          </Link>
        </div>
      </header>
      <main id="main" className="page narrow">
        <section className="panel">
          <h1>This page could not load</h1>
          <p>
            Something failed while reading it. Try again; if your session has ended, sign in again.
            The demo works without the live system.
          </p>
          <ul className="actions" role="list">
            <li>
              <button
                type="button"
                className="button primary"
                onClick={() => {
                  retry();
                }}
              >
                Try again
              </button>
            </li>
            <li>
              <Link className="button" href="/login">
                Sign in
              </Link>
            </li>
            <li>
              <Link className="button" href="/demo">
                Open the demo
              </Link>
            </li>
          </ul>
        </section>
      </main>
    </>
  );
}
