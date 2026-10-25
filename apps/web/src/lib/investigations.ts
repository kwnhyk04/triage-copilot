import { type components, createGatewayClient } from '@triage/ts-clients/gateway';
import { z } from '@triage/ts-config';

import type { WebConfig } from './config.ts';

export type Investigation = components['schemas']['Investigation'];
export type InvestigationPage = components['schemas']['InvestigationPage'];
type Gateway = Pick<WebConfig, 'GATEWAY_URL' | 'GATEWAY_API_KEY'>;

const gateway = (config: Gateway) =>
  createGatewayClient({ baseUrl: config.GATEWAY_URL, apiKey: config.GATEWAY_API_KEY });

/** One page of investigations, newest first. Server code only: it sends the API key. */
export async function listInvestigations(
  config: Gateway,
  cursor?: string,
): Promise<InvestigationPage> {
  const query = cursor === undefined ? {} : { cursor };
  const { data, response } = await gateway(config).GET('/v1/investigations', {
    params: { query },
  });
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
  const { data, response } = await gateway(config).GET('/v1/investigations/{id}', {
    params: { path: { id } },
  });
  if (response.status === 404) return undefined;
  if (data === undefined) throw new Error(`gateway answered ${String(response.status)}`);
  return data;
}
