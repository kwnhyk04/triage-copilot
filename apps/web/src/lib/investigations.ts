import { type components, createGatewayClient } from '@triage/ts-clients/gateway';
import { z } from '@triage/ts-config';

import type { WebConfig } from './config.ts';

export type Investigation = components['schemas']['Investigation'];
export type InvestigationPage = components['schemas']['InvestigationPage'];
export type Source = components['schemas']['InvestigationSource'];
type Gateway = Pick<WebConfig, 'GATEWAY_URL' | 'GATEWAY_API_KEY'>;

/** A gateway call gives up after 5 s; the reader then gets the demo instead of a hung page. */
export const GATEWAY_TIMEOUT_MS = 5000;
// What a proxy in front of the gateway answers when it is down.
const DOWN = [502, 503, 504];

/** The gateway did not answer: the request failed or timed out, or it answered 502, 503 or 504. */
export class GatewayOffline extends Error {}

/** Sends one request through `call`, turning a gateway that did not answer into GatewayOffline. */
export async function sent<T extends { response: Response }>(call: () => Promise<T>): Promise<T> {
  let result: T;
  try {
    result = await call();
  } catch (error) {
    throw new GatewayOffline('the gateway did not answer', { cause: error });
  }
  if (DOWN.includes(result.response.status)) {
    throw new GatewayOffline(`the gateway answered ${String(result.response.status)}`);
  }
  return result;
}

const gateway = (config: Gateway) =>
  createGatewayClient({
    baseUrl: config.GATEWAY_URL,
    apiKey: config.GATEWAY_API_KEY,
    timeoutMs: GATEWAY_TIMEOUT_MS,
  });

/** One page of investigations, newest first. Server code only: it sends the API key. */
export async function listInvestigations(
  config: Gateway,
  { source, cursor }: { source?: Source; cursor?: string },
): Promise<InvestigationPage> {
  const query = {
    ...(source === undefined ? {} : { source }),
    ...(cursor === undefined ? {} : { cursor }),
  };
  const { data, response } = await sent(() =>
    gateway(config).GET('/v1/investigations', { params: { query } }),
  );
  if (data === undefined) throw new Error(`gateway answered ${String(response.status)}`);
  return data;
}

/** One investigation, or undefined when none has the id. Server code only: it sends the API key. */
export async function getInvestigation(
  config: Gateway,
  id: string,
): Promise<Investigation | undefined> {
  // The id comes from the page's URL: only a uuid is ever sent on.
  if (!z.guid().safeParse(id).success) return undefined;
  const { data, response } = await sent(() =>
    gateway(config).GET('/v1/investigations/{id}', { params: { path: { id } } }),
  );
  if (response.status === 404) return undefined;
  if (data === undefined) throw new Error(`gateway answered ${String(response.status)}`);
  return data;
}
