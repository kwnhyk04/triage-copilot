import { HealthDocument, type HealthQuery } from '../generated/graphql.ts';
import type { WebConfig } from './config.ts';

/** Asks the gateway for its health over GraphQL. Server-side only: the browser never calls the API. */
export async function gatewayStatus(
  config: Pick<WebConfig, 'GATEWAY_URL' | 'GATEWAY_API_KEY'>,
): Promise<'ok' | 'unreachable'> {
  try {
    const response = await fetch(new URL('/graphql', config.GATEWAY_URL), {
      method: 'POST',
      headers: { 'content-type': 'application/json', 'x-api-key': config.GATEWAY_API_KEY },
      body: JSON.stringify({ query: HealthDocument.toString() }),
      cache: 'no-store',
      signal: AbortSignal.timeout(2000),
    });
    const body = (await response.json()) as { data?: HealthQuery };
    return body.data?.health.status === 'ok' ? 'ok' : 'unreachable';
  } catch {
    return 'unreachable';
  }
}
