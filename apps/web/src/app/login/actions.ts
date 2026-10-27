'use server';

import { cookies, headers } from 'next/headers';
import { redirect } from 'next/navigation';

import { loadWebConfig } from '../../lib/config.ts';
import { clientKey, safeNext, signInLimits } from '../../lib/guards.ts';
import { PASSWORD_LENGTH, verifyPassword } from '../../lib/password.ts';
import { SESSION_COOKIE, SESSION_COOKIE_OPTIONS, signSession } from '../../lib/session.ts';

export interface SignInState {
  error?: 'wrong' | 'limit' | 'off';
}

/** Checks the one password, starts an 8-hour session, then opens the page that was asked for. */
export async function signIn(_state: SignInState, form: FormData): Promise<SignInState> {
  const { LOGIN_PASSWORD_HASH: hash, SESSION_SECRET: secret } = loadWebConfig();
  if (hash === undefined || secret === undefined) return { error: 'off' };
  const address = clientKey(await headers());
  // Counted before the password check, and nothing awaits between the check and the count.
  if (!signInLimits.attempt(address)) return { error: 'limit' };
  const password = form.get('password');
  if (
    typeof password !== 'string' ||
    password.length > PASSWORD_LENGTH.max ||
    !(await verifyPassword(password, hash))
  ) {
    return { error: 'wrong' };
  }
  signInLimits.succeeded(address);
  (await cookies()).set(SESSION_COOKIE, signSession(secret), SESSION_COOKIE_OPTIONS);
  redirect(safeNext(form.get('next')));
}
