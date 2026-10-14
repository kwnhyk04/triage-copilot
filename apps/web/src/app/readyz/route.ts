import { loadWebConfig } from '../../lib/config.ts';
import { gatewayStatus } from '../../lib/gateway.ts';

/** Ready only when the gateway answers; the backend-for-frontend serves nothing live without it. */
export async function GET(): Promise<Response> {
  const ok = (await gatewayStatus(loadWebConfig().GATEWAY_URL)) === 'ok';
  return Response.json({ status: ok ? 'ok' : 'unavailable' }, { status: ok ? 200 : 503 });
}
