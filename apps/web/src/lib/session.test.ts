import { SESSION_SECONDS, signSession, validSession } from './session.ts';

const SECRET = 'session-secret-for-these-tests-0000';
const NOW = Date.parse('2026-10-27T10:00:00Z');

describe('session', () => {
  it('holds while signed with the secret and unexpired, for 8 hours', () => {
    const token = signSession(SECRET, NOW);
    expect(token).toMatch(/^v1\.\d+\.[A-Za-z0-9_-]{43}$/);
    expect(validSession(token, SECRET, NOW)).toBe(true);
    expect(SESSION_SECONDS).toBe(8 * 60 * 60);
    expect(validSession(token, SECRET, NOW + SESSION_SECONDS * 1000 - 1000)).toBe(true);
    expect(validSession(token, SECRET, NOW + SESSION_SECONDS * 1000)).toBe(false);
  });

  it('is refused when tampered with or signed with another secret', () => {
    const token = signSession(SECRET, NOW);
    const [version = '', expiry = '', mac = ''] = token.split('.');
    const later = String(Number(expiry) + 3600);
    for (const forged of [
      `${version}.${later}.${mac}`,
      `${token.slice(0, -1)}${token.endsWith('A') ? 'B' : 'A'}`,
      token.replace('v1.', 'v2.'),
      `${token}.`,
      signSession(`${SECRET}-other`, NOW),
      '',
      undefined,
    ]) {
      expect([forged, validSession(forged, SECRET, NOW)]).toEqual([forged, false]);
    }
  });

  it('signs no one in without a secret of 32 characters or more', () => {
    const token = signSession(SECRET, NOW);
    expect(validSession(token, undefined, NOW)).toBe(false);
    expect(validSession(token, '', NOW)).toBe(false);
    const short = SECRET.slice(0, 31);
    expect(validSession(token, short, NOW)).toBe(false);
    expect(() => signSession(short, NOW)).toThrow('SESSION_SECRET');
  });
});
