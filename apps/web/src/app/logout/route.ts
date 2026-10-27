import { isSameOrigin } from '../../lib/guards.ts';
import { SESSION_COOKIE } from '../../lib/session.ts';

/** Signs out: expires the session cookie, clears the cached pages, and goes to sign-in. */
export function POST(request: Request): Response {
  if (!isSameOrigin(request.headers)) {
    return new Response('Sign out from this site.', { status: 403 });
  }
  return new Response(null, {
    status: 303,
    headers: {
      location: '/login',
      'clear-site-data': '"cache"',
      'cache-control': 'no-store',
      'set-cookie': `${SESSION_COOKIE}=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax`,
    },
  });
}
