import createClient from 'openapi-fetch';

import { fetchWithTimeout } from './fetch.ts';
import type { paths } from './generated/gateway.ts';

export type * from './generated/gateway.ts';

type Decisions = '/v1/actions/{id}/approve' | '/v1/actions/{id}/reject';
// Served on the internal listener only, for the executor.
type Internal = '/internal/actions/{id}/execution' | '/internal/actions/{id}/execution/result';

/**
 * Typed client for the gateway's X-API-Key routes. Server-side only: it carries the API key.
 * The Alertmanager webhook, the decision routes and the executor's internal routes are left out:
 * each takes its own credential.
 */
export const createGatewayClient = ({
  baseUrl,
  apiKey,
  timeoutMs = 10_000,
}: {
  baseUrl: string;
  apiKey: string;
  timeoutMs?: number;
}) =>
  createClient<Omit<paths, '/v1/alerts' | Decisions | Internal>>({
    baseUrl,
    headers: { 'X-API-Key': apiKey },
    fetch: fetchWithTimeout(timeoutMs),
  });

/** Typed client for approve and reject. Only the web app's server holds the approver key. */
export const createDecisionClient = ({
  baseUrl,
  approverKey,
  timeoutMs = 10_000,
}: {
  baseUrl: string;
  approverKey: string;
  timeoutMs?: number;
}) =>
  createClient<Pick<paths, Decisions>>({
    baseUrl,
    headers: { 'X-Approver-Key': approverKey },
    fetch: fetchWithTimeout(timeoutMs),
  });
