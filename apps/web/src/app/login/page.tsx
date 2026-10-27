import Link from 'next/link';

import { loadWebConfig } from '../../lib/config.ts';
import { safeNext } from '../../lib/guards.ts';
import { PASSWORD_LENGTH } from '../../lib/password.ts';
import { SignInForm } from './form.tsx';

export const metadata = { title: 'Sign in to Triage Copilot' };

/** The one sign-in. Without its two settings (dev only) sign-in is off and says so. */
export default async function SignIn({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { next } = await searchParams;
  const { LOGIN_PASSWORD_HASH, SESSION_SECRET } = loadWebConfig();
  return (
    <main id="main" className="page narrow">
      <p className="brand">
        <Link href="/">Triage Copilot</Link>
      </p>
      <h1>Sign in</h1>
      {LOGIN_PASSWORD_HASH === undefined || SESSION_SECRET === undefined ? (
        <p>
          Sign-in is off: this server has no LOGIN_PASSWORD_HASH and SESSION_SECRET. The README says
          how to make them.
        </p>
      ) : (
        <SignInForm next={safeNext(next)} maxLength={PASSWORD_LENGTH.max} />
      )}
      <p>
        No password? <Link href="/demo">Open the demo</Link> to read saved reports.
      </p>
    </main>
  );
}
