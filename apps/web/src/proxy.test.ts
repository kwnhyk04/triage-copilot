// The bundled docs call it unstable_doesProxyMatch; 16.4.2 still exports the middleware name.
import { unstable_doesMiddlewareMatch } from 'next/experimental/testing/server';
import { NextRequest } from 'next/server';

import { signSession } from './lib/session.ts';
import { config, proxy } from './proxy.ts';

const SECRET = 'session-secret-for-these-tests-0000';
const ENV = {
  GATEWAY_URL: 'http://127.0.0.1:4200',
  GATEWAY_API_KEY: 'gateway-key-for-these-tests-000000000',
  APPROVER_API_KEY: 'approver-key-for-these-tests-00000000',
  LOGIN_PASSWORD_HASH:
    '$argon2id$v=19$m=65536,t=2,p=1$c29tZXNhbHQ$CTFhFdXPJO1aFaMaO6Mm5c8y7cJHAph8ArZWb2GRPPc',
  SESSION_SECRET: SECRET,
};
const REPORT = '/investigations/3d6f9a2c-1b4e-4c8d-9f7a-5e2b1c0d4a68';
const request = (path: string, session?: string, method = 'GET') =>
  new NextRequest(new URL(path, 'http://127.0.0.1:3200'), {
    method,
    headers: session === undefined ? {} : { cookie: `__Host-triage_session=${session}` },
  });
const nonceOf = (response: Response) =>
  /'nonce-([A-Za-z0-9+/=]+)'/.exec(response.headers.get('content-security-policy') ?? '')?.[1];

describe('proxy', () => {
  beforeAll(() => {
    Object.assign(process.env, ENV);
  });

  it('sends a request without a session to sign-in, with where it was going', () => {
    for (const [path, method] of [
      ['/investigations', 'GET'],
      [`${REPORT}?x=1`, 'GET'],
      [REPORT, 'POST'],
      ['/no-such-page', 'GET'],
      ['/demonstration', 'GET'],
      ['/login2', 'GET'],
    ] as const) {
      const response = proxy(request(path, undefined, method));
      expect([path, response.status]).toEqual([path, 303]);
      const location = new URL(response.headers.get('location') ?? '');
      expect([location.pathname, location.searchParams.get('next')]).toEqual(['/login', path]);
    }
  });

  it('lets a signed-in request through, never a forged or expired one', () => {
    expect(proxy(request(REPORT, signSession(SECRET))).headers.get('location')).toBeNull();
    const nineHoursAgo = Date.now() - 9 * 60 * 60 * 1000;
    for (const forged of [
      signSession(`${SECRET}-other`),
      signSession(SECRET, nineHoursAgo),
      'v1.1.x',
    ]) {
      expect(proxy(request(REPORT, forged)).status).toBe(303);
    }
  });

  it('lets the landing, sign-in and out, the probes and the demo through without a session', () => {
    for (const path of ['/', '/login', '/logout', '/healthz', '/readyz', '/demo', '/demo/x']) {
      const response = proxy(request(path));
      expect([path, response.headers.get('location')]).toEqual([path, null]);
      expect(response.headers.get('x-middleware-next')).toBe('1');
    }
  });

  it("sets a fresh nonce CSP on each response, and hands the nonce to Next's render", () => {
    const first = proxy(request('/login'));
    const csp = first.headers.get('content-security-policy') ?? '';
    for (const directive of [
      "default-src 'self'",
      `script-src 'self' 'nonce-${nonceOf(first) ?? ''}' 'strict-dynamic'`,
      `style-src 'self' 'nonce-${nonceOf(first) ?? ''}'`,
      "font-src 'self'",
      "object-src 'none'",
      "base-uri 'self'",
      "form-action 'self'",
      "frame-ancestors 'none'",
    ]) {
      expect(csp).toContain(directive);
    }
    // Next reads the nonce from the request's CSP header.
    expect(first.headers.get('x-middleware-request-content-security-policy')).toBe(csp);
    expect(first.headers.get('x-middleware-request-x-nonce')).toBe(nonceOf(first));
    expect(nonceOf(proxy(request('/login')))).not.toBe(nonceOf(first));
  });

  it('runs on every path but static files', () => {
    const matches = (url: string) => unstable_doesMiddlewareMatch({ config, url });
    expect([REPORT, '/', '/demo', '/logout'].map(matches)).toEqual([true, true, true, true]);
    expect(['/_next/static/chunks/a.js', '/_next/image', '/favicon.ico'].map(matches)).toEqual([
      false,
      false,
      false,
    ]);
  });
});
