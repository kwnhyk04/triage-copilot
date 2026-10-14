import createClient from 'openapi-fetch';

import type { paths } from './generated/agent.ts';

/** Typed client for the agent's internal API (contracts/openapi/agent.yaml). */
export const createAgentClient = (baseUrl: string) => createClient<paths>({ baseUrl });
