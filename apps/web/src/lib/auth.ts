// The one session check. The proxy asks it with the request's token; the data layer, the header
// and the pages ask it for the request being rendered. Server code only.
import { cookies } from 'next/headers';

import { loadWebConfig } from './config.ts';
import { SESSION_COOKIE, validSession } from './session.ts';

/** Whether a session token is valid on this server now: signed with its secret, not expired. */
export const sessionValid = (token: string | undefined) =>
  validSession(token, loadWebConfig().SESSION_SECRET);

/** Whether the request being rendered carries a valid session. */
export async function signedIn(): Promise<boolean> {
  return sessionValid((await cookies()).get(SESSION_COOKIE)?.value);
}
