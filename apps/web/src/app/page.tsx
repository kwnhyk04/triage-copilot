import Link from 'next/link';
import { redirect } from 'next/navigation';

import { SiteHeader } from '../components/header.tsx';
import { signedIn } from '../lib/auth.ts';
import { HOME } from '../lib/guards.ts';

/**
 * The public landing: what Triage Copilot does, sign-in, and the demo. A signed-in reader goes on
 * to the live incidents. It reads nothing from the gateway.
 */
export default async function Landing() {
  if (await signedIn()) redirect(HOME);
  return (
    <>
      <SiteHeader />
      <main id="main" className="page narrow landing">
        <h1>Is this alert a real incident?</h1>
        <p className="lede">
          Triage Copilot reads the logs, metrics and recent changes behind an alert. It says whether
          the alert shows a real incident, names the likely cause with its evidence, and proposes a
          fix that a person approves or rejects. Nothing runs without that decision.
        </p>
        <ul className="actions" role="list">
          <li>
            <Link className="button primary large" href="/login">
              Sign in
            </Link>
          </li>
          <li>
            <Link className="button large" href="/demo">
              Open the demo
            </Link>
          </li>
        </ul>
      </main>
    </>
  );
}
