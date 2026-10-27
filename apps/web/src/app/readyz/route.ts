import { loadWebConfig } from '../../lib/config.ts';
import { gatewayStatus } from '../../lib/gateway.ts';

// The probe is public and each gateway call spends this server's rate limit there, so one call,
// in flight or answered, serves every probe for 10 s.
const HOLD_MS = 10_000;
let last: { at: number; ok: Promise<boolean> } | undefined;

/** Ready only when the gateway answers; the backend-for-frontend serves nothing live without it. */
export async function GET(): Promise<Response> {
  const now = Date.now();
  if (last === undefined || now - last.at >= HOLD_MS) {
    last = { at: now, ok: gatewayStatus(loadWebConfig()).then((status) => status === 'ok') };
  }
  const ok = await last.ok;
  return Response.json({ status: ok ? 'ok' : 'unavailable' }, { status: ok ? 200 : 503 });
}
