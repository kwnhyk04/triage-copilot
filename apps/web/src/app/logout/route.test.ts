import { POST } from './route.ts';

const signOut = (headers: Record<string, string>) =>
  POST(new Request('http://127.0.0.1:3200/logout', { method: 'POST', headers }));

describe('POST /logout', () => {
  it('expires the cookie, clears the cache and goes to sign-in for this site', () => {
    for (const headers of [
      { 'sec-fetch-site': 'same-origin' },
      { origin: 'http://127.0.0.1:3200', host: '127.0.0.1:3200' },
    ]) {
      const response = signOut(headers);
      expect(response.status).toBe(303);
      expect(response.headers.get('location')).toBe('/login');
      expect(response.headers.get('clear-site-data')).toBe('"cache"');
      expect(response.headers.get('cache-control')).toBe('no-store');
      expect(response.headers.get('set-cookie')).toBe(
        '__Host-triage_session=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax',
      );
    }
  });

  it('refuses a post from another site, and keeps the cookie', () => {
    for (const headers of [
      { 'sec-fetch-site': 'cross-site' },
      { origin: 'https://evil.example', host: '127.0.0.1:3200' },
      {},
    ]) {
      const response = signOut(headers);
      expect(response.status).toBe(403);
      expect(response.headers.get('set-cookie')).toBeNull();
    }
  });
});
