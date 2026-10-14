import { HealthDocument, type HealthQuery } from '../generated/graphql.ts';

/** Asks the gateway for its health over GraphQL. Server-side only: the browser never calls the API. */
export async function gatewayStatus(gatewayUrl: string): Promise<'ok' | 'unreachable'> {
  try {
    const response = await fetch(new URL('/graphql', gatewayUrl), {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
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
