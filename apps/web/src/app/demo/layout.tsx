import type { ReactNode } from 'react';

/** The public demo's banner, under the header: saved reports, readable without signing in. */
export default function Demo({ children }: { children: ReactNode }) {
  return (
    <>
      <div className="demo-banner" role="region" aria-label="About the demo">
        <p>
          <strong>Demo.</strong> These are saved reports, open without signing in. The eval runs are
          recorded runs on simulated incidents. The three live incidents are illustrative samples
          written by hand, not real incidents. The plan buttons do nothing here.
        </p>
      </div>
      {children}
    </>
  );
}
