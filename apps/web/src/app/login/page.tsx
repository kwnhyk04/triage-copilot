import Link from 'next/link';
import { redirect } from 'next/navigation';

import { SiteHeader } from '../../components/header.tsx';
import { signedIn } from '../../lib/auth.ts';
import { loadWebConfig } from '../../lib/config.ts';
import { safeNext } from '../../lib/guards.ts';
import { PASSWORD_LENGTH } from '../../lib/password.ts';
import { SignInForm } from './form.tsx';

export const metadata = { title: 'Sign in to Triage Copilot' };

/**
 * The one sign-in, shown only when signed out: a signed-in reader goes on to the page asked for
 * when it is safe, else the live incidents. Without its two settings (dev only) sign-in is off and
 * says so.
 */
export default async function SignIn({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { next } = await searchParams;
  if (await signedIn()) redirect(safeNext(next));
  const { LOGIN_PASSWORD_HASH, SESSION_SECRET } = loadWebConfig();
  return (
    <>
      <SiteHeader />
      <main id="main" className="page narrow">
        <section className="panel sign-in">
          <h1>Sign in</h1>
          {LOGIN_PASSWORD_HASH === undefined || SESSION_SECRET === undefined ? (
            <p>
              Sign-in is off: this server has no LOGIN_PASSWORD_HASH and SESSION_SECRET. The README
              says how to make them.
            </p>
          ) : (
            <SignInForm next={safeNext(next)} maxLength={PASSWORD_LENGTH.max} />
          )}
        </section>
        <p>
          No password? <Link href="/demo">Open the demo</Link> to read saved reports.
        </p>
      </main>
    </>
  );
}
