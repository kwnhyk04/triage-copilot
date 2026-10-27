import { clientKey, createSignInLimits, isSameOrigin, safeNext } from './guards.ts';

const MINUTES_15 = 15 * 60_000;
const clock = () => {
  let now = Date.parse('2026-10-27T10:00:00Z');
  return {
    now: () => now,
    advance: (ms: number) => {
      now += ms;
    },
  };
};

describe('sign-in limits', () => {
  it('blocks the 6th attempt from one address within 15 minutes, and only that address', () => {
    const time = clock();
    const limits = createSignInLimits(time.now);
    for (let i = 0; i < 5; i++) expect(limits.attempt('a')).toBe(true);
    expect(limits.attempt('a')).toBe(false);
    expect(limits.attempt('b')).toBe(true);
    time.advance(MINUTES_15);
    expect(limits.attempt('a')).toBe(true);
  });

  it('clears an address once it signs in', () => {
    const limits = createSignInLimits(clock().now);
    for (let i = 0; i < 5; i++) limits.attempt('a');
    limits.succeeded('a');
    expect(limits.attempt('a')).toBe(true);
  });

  it('caps every address together at 30 attempts within 15 minutes', () => {
    const time = clock();
    const limits = createSignInLimits(time.now);
    for (let i = 0; i < 30; i++) expect(limits.attempt(`address-${String(i % 10)}`)).toBe(true);
    expect(limits.attempt('fresh')).toBe(false);
    time.advance(MINUTES_15);
    expect(limits.attempt('fresh')).toBe(true);
  });

  it('lets no single address use up the shared cap', () => {
    const limits = createSignInLimits(clock().now);
    for (let i = 0; i < 100; i++) limits.attempt('attacker');
    expect(limits.attempt('owner')).toBe(true);
  });

  it('records no refused attempt, and prunes entries past their window', () => {
    const time = clock();
    const limits = createSignInLimits(time.now);
    for (let i = 0; i < 30; i++) limits.attempt(`address-${String(i)}`);
    expect(limits.attempt('one-more')).toBe(false);
    expect(limits.size).toBe(30);
    time.advance(MINUTES_15);
    limits.attempt('later');
    expect(limits.size).toBe(1);
  });

  it('keys by x-real-ip on Vercel, and puts every client in one bucket elsewhere', () => {
    const headers = new Headers({ 'x-real-ip': '203.0.113.7', 'x-forwarded-for': '198.51.100.1' });
    expect(clientKey(headers, { VERCEL: '1' })).toBe('203.0.113.7');
    expect(clientKey(new Headers(), { VERCEL: '1' })).toBe('unknown');
    expect(clientKey(headers, {})).toBe('local');
  });
});

describe('safeNext', () => {
  it('keeps a path on this site, with dot segments resolved', () => {
    const report = '/investigations/3d6f9a2c-1b4e-4c8d-9f7a-5e2b1c0d4a68';
    expect(safeNext(report)).toBe(report);
    expect(safeNext('/investigations?source=sim&cursor=abc')).toBe(
      '/investigations?source=sim&cursor=abc',
    );
    expect(safeNext('/demo/../investigations')).toBe('/investigations');
  });

  it('sends anything else to the list', () => {
    for (const next of [
      undefined,
      null,
      '',
      'investigations',
      'https://evil.example/',
      '//evil.example/',
      '/\\evil.example',
      '/.//evil.example',
      '/\t/evil.example',
      '/login',
      '/login?next=/investigations',
      '/logout',
    ]) {
      expect([next, safeNext(next)]).toEqual([next, '/investigations']);
    }
  });
});

describe('isSameOrigin', () => {
  const host = '127.0.0.1:3200';
  it('trusts Sec-Fetch-Site, else compares Origin with Host', () => {
    expect(isSameOrigin(new Headers({ 'sec-fetch-site': 'same-origin' }))).toBe(true);
    expect(isSameOrigin(new Headers({ origin: `http://${host}`, host }))).toBe(true);
    for (const headers of [
      { 'sec-fetch-site': 'cross-site', origin: `http://${host}`, host },
      { 'sec-fetch-site': 'same-site' },
      { origin: 'https://evil.example', host },
      { origin: 'null', host },
      { host },
    ]) {
      expect([headers, isSameOrigin(new Headers(headers))]).toEqual([headers, false]);
    }
  });
});
