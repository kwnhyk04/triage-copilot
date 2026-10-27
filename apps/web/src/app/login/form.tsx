'use client';

import { useActionState } from 'react';

import { signIn, type SignInState } from './actions.ts';

const MESSAGES: Record<NonNullable<SignInState['error']>, string> = {
  wrong: 'Wrong password.',
  limit: 'Too many attempts. Try again in 15 minutes.',
  off: 'Sign-in is off on this server.',
};

/** The password-only form; after signing in it opens `next`. A client component: it gets the
 * length limit from the page, since lib/password.ts is server code. */
export function SignInForm({ next, maxLength }: { next: string; maxLength: number }) {
  const [state, action, pending] = useActionState(signIn, {});
  return (
    <form action={action} className="stack">
      <input type="hidden" name="next" value={next} />
      <label htmlFor="password">Password</label>
      <input
        id="password"
        name="password"
        type="password"
        autoComplete="current-password"
        required
        maxLength={maxLength}
        aria-invalid={state.error === undefined ? undefined : true}
        aria-describedby={state.error === undefined ? undefined : 'sign-in-error'}
      />
      {state.error !== undefined && (
        <p id="sign-in-error" className="error" role="alert">
          {MESSAGES[state.error]}
        </p>
      )}
      <button type="submit" className="primary" disabled={pending}>
        {pending ? 'Signing in' : 'Sign in'}
      </button>
    </form>
  );
}
