import { createHmac, timingSafeEqual } from 'node:crypto';

export const SESSION_COOKIE = '__Host-triage_session';
export const SESSION_SECONDS = 8 * 60 * 60;
/** Only this site's server reads the cookie, over HTTPS; it lasts the session, with no sliding. */
export const SESSION_COOKIE_OPTIONS = {
  httpOnly: true,
  secure: true,
  sameSite: 'lax',
  path: '/',
  maxAge: SESSION_SECONDS,
} as const;
const MIN_SECRET = 32;
const TOKEN = /^v1\.(\d{1,12})\.([A-Za-z0-9_-]{43})$/;

const mac = (secret: string, body: string) =>
  createHmac('sha256', secret).update(body).digest('base64url');

/** A session token, v1.<expiry in Unix seconds>.<HMAC-SHA256>, good for SESSION_SECONDS. */
export function signSession(secret: string, now = Date.now()): string {
  if (secret.length < MIN_SECRET) throw new Error('SESSION_SECRET must have 32 characters or more');
  const body = `v1.${String(Math.floor(now / 1000) + SESSION_SECONDS)}`;
  return `${body}.${mac(secret, body)}`;
}

/**
 * Whether the token was signed with this secret and has not expired. Sessions are stateless:
 * signing out drops the cookie, but a copied token holds until it expires, and rotating the
 * secret signs everyone out.
 */
export function validSession(
  token: string | undefined,
  secret: string | undefined,
  now = Date.now(),
): boolean {
  if (token === undefined || secret === undefined || secret.length < MIN_SECRET) return false;
  const match = TOKEN.exec(token);
  if (match === null) return false;
  const [, expiry = '', given = ''] = match;
  if (Number(expiry) * 1000 <= now) return false;
  return timingSafeEqual(Buffer.from(given), Buffer.from(mac(secret, `v1.${expiry}`)));
}
