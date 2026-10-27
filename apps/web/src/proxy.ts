import { type NextRequest, NextResponse } from 'next/server';

import { loadWebConfig } from './lib/config.ts';
import { SESSION_COOKIE, validSession } from './lib/session.ts';

// Open without a session: the landing, sign-in and out, the probes, and the demo.
const PUBLIC = /^\/(?:login|logout|healthz|readyz|demo(?:\/.*)?)?$/;

/** A nonce CSP: Next puts the nonce on its own scripts; dev's React needs eval and inline styles. */
const policy = (nonce: string, dev: boolean) =>
  [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${dev ? " 'unsafe-eval'" : ''}`,
    `style-src 'self' ${dev ? "'unsafe-inline'" : `'nonce-${nonce}'`}`,
    "img-src 'self' blob: data:",
    "font-src 'self'",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    // No page may frame this one, so Approve cannot be clicked through someone else's site.
    "frame-ancestors 'none'",
  ].join('; ');

/**
 * Sends every request without a valid session to sign-in, the public pages aside, and sets the
 * CSP. The data-access layer (lib/data.ts) checks the session again before each gateway call.
 */
export function proxy(request: NextRequest): NextResponse {
  const { pathname, search } = request.nextUrl;
  if (!PUBLIC.test(pathname)) {
    const token = request.cookies.get(SESSION_COOKIE)?.value;
    if (!validSession(token, loadWebConfig().SESSION_SECRET)) {
      const login = new URL('/login', request.url);
      login.searchParams.set('next', pathname + search);
      // 303, so a form posted without a session reaches sign-in as a GET.
      return NextResponse.redirect(login, 303);
    }
  }
  const nonce = Buffer.from(crypto.randomUUID()).toString('base64');
  const csp = policy(nonce, process.env.NODE_ENV === 'development');
  const headers = new Headers(request.headers);
  headers.set('x-nonce', nonce);
  headers.set('content-security-policy', csp);
  const response = NextResponse.next({ request: { headers } });
  response.headers.set('content-security-policy', csp);
  return response;
}

export const config = {
  matcher: ['/((?!_next/static|_next/image|favicon.ico).*)'],
};
