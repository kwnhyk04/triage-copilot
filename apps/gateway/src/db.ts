import { setTimeout as sleep } from 'node:timers/promises';

import postgres from 'postgres';

/** Connection pool for the gateway role; `ping` backs /readyz. */
export function connectDb(url: string, pingTimeoutMs = 3000) {
  const sql = postgres(url, {
    max: 5,
    connect_timeout: 5,
    idle_timeout: 30,
    connection: { statement_timeout: 5000 },
  });
  // A stalled connection never answers, so the ping races a timer instead of waiting on it.
  const ping = async (): Promise<void> => {
    const timer = new AbortController();
    try {
      await Promise.race([
        sql`select 1`,
        sleep(pingTimeoutMs, undefined, { signal: timer.signal }).then(() => {
          throw new Error('database ping timed out');
        }),
      ]);
    } finally {
      timer.abort();
    }
  };
  return { sql, ping };
}
