const WINDOW_MS = 15 * 60_000;
const PER_ADDRESS = 5;
const EVERYONE = 30;
const HOME = '/investigations';
const BASE = 'http://site.invalid';

/**
 * Sign-in attempts, counted before the password check: 5 per address and 30 in all, each within
 * 15 minutes of its first counted attempt. A refused attempt is not counted, so one address cannot
 * use up the shared cap, and only counted attempts make entries, so the map stays small.
 */
export function createSignInLimits(now = Date.now) {
  const addresses = new Map<string, { count: number; until: number }>();
  let everyone = { count: 0, until: 0 };
  return {
    /** Counts an attempt; false, counting nothing, when the address or everyone is at the limit. */
    attempt(address: string): boolean {
      const at = now();
      for (const [key, entry] of addresses) if (entry.until <= at) addresses.delete(key);
      if (everyone.until <= at) everyone = { count: 0, until: at + WINDOW_MS };
      const entry = addresses.get(address);
      if ((entry?.count ?? 0) >= PER_ADDRESS || everyone.count >= EVERYONE) return false;
      everyone.count++;
      if (entry === undefined) addresses.set(address, { count: 1, until: at + WINDOW_MS });
      else entry.count++;
      return true;
    },
    /** The right password clears its address. */
    succeeded(address: string) {
      addresses.delete(address);
    },
    get size() {
      return addresses.size;
    },
  };
}

// ponytail: held in one server's memory; Vercel may run several instances, so step 10 adds a
// firewall rule on POST /login.
export const signInLimits = createSignInLimits();

/**
 * The limiter's key: Vercel's x-real-ip there. Elsewhere every client shares one bucket, since the
 * server listens on 127.0.0.1 and no client header can be trusted.
 */
export function clientKey(
  headers: Headers,
  env: Readonly<Record<string, string | undefined>> = process.env,
): string {
  return env.VERCEL === '1' ? (headers.get('x-real-ip') ?? 'unknown') : 'local';
}

/** Where to go after signing in: a path on this site, dot segments resolved; else the list. */
export function safeNext(next: unknown): string {
  if (typeof next !== 'string' || !next.startsWith('/') || next.includes('\\')) return HOME;
  let url: URL;
  try {
    url = new URL(next, BASE);
  } catch {
    return HOME;
  }
  const path = url.pathname;
  if (url.origin !== BASE || path.startsWith('//') || /^\/log(?:in|out)(?:\/|$)/.test(path)) {
    return HOME;
  }
  return path + url.search;
}

/** Whether a request comes from this site: Sec-Fetch-Site when sent, else Origin against Host. */
export function isSameOrigin(headers: Headers): boolean {
  const site = headers.get('sec-fetch-site');
  if (site !== null) return site === 'same-origin';
  const origin = headers.get('origin');
  const host = headers.get('host');
  if (origin === null || host === null) return false;
  try {
    return new URL(origin).host === host;
  } catch {
    return false;
  }
}
