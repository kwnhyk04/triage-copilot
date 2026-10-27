'use client';

import Link from 'next/link';

/** Any error but an offline gateway. It cannot tell a bug from an ended session, so it offers both. */
export default function Failed({ retry }: { error: unknown; retry: () => void }) {
  return (
    <main id="main" className="page narrow">
      <h1>This page could not load</h1>
      <p>
        Something failed while reading it. Try again; if your session has ended, sign in again. The
        demo works without the live system.
      </p>
      <ul className="actions" role="list">
        <li>
          <button
            type="button"
            className="button"
            onClick={() => {
              retry();
            }}
          >
            Try again
          </button>
        </li>
        <li>
          <Link className="button quiet" href="/login">
            Sign in
          </Link>
        </li>
        <li>
          <Link className="button quiet" href="/demo">
            Open the demo
          </Link>
        </li>
      </ul>
    </main>
  );
}
