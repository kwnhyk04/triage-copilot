import createClient from 'openapi-fetch';

import { fetchWithTimeout } from './fetch.ts';
import type { paths } from './generated/agent.ts';

export type * from './generated/agent.ts';

/** Typed client for the agent's internal API; it carries the service token. */
export const createAgentClient = ({
  baseUrl,
  serviceToken,
  timeoutMs = 10_000,
}: {
  baseUrl: string;
  serviceToken: string;
  timeoutMs?: number;
}) =>
  createClient<paths>({
    baseUrl,
    headers: { Authorization: `Bearer ${serviceToken}` },
    fetch: fetchWithTimeout(timeoutMs),
  });
