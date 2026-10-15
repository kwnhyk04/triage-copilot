import { z } from '@triage/ts-config';
import Fastify, { type FastifyInstance } from 'fastify';

import {
  answerWithProblems,
  badRequest,
  bearer,
  httpError,
  problem,
  REQUEST_TIMEOUT_MS,
  sameSecret,
} from './app.ts';
import type { GatewayConfig } from './config.ts';
import type { Claim, Report } from './executions.ts';

export interface InternalDeps {
  config: Pick<GatewayConfig, 'LOG_LEVEL' | 'EXECUTOR_SERVICE_TOKEN'>;
  claim: Claim;
  report: Report;
}

const claimBody = z.strictObject({ dry_run: z.boolean() });
const resultBody = z.strictObject({
  state: z.enum(['succeeded', 'failed']),
  detail: z.string().max(2000).exactOptional(),
});

/**
 * The executor's API. It has a listener of its own, bound to 127.0.0.1 (the internal Docker
 * network in production), so the public port that Tailscale Funnel exposes never serves it.
 */
export function buildInternalApp({ config, claim, report }: InternalDeps): FastifyInstance {
  const app = Fastify({
    logger: { level: config.LOG_LEVEL, base: { service: 'gateway-internal' } },
    requestTimeout: REQUEST_TIMEOUT_MS,
  });
  answerWithProblems(app);

  // Every route but the probe takes the executor's token, which no other service holds.
  app.addHook('onRequest', async (request, reply) => {
    if (request.routeOptions.url === '/healthz') return;
    const token = bearer(request);
    if (token === undefined || !sameSecret(token, config.EXECUTOR_SERVICE_TOKEN)) {
      reply.header('WWW-Authenticate', 'Bearer');
      throw httpError(401, 'Send the executor service token.');
    }
  });
  app.get('/healthz', () => ({ status: 'ok' }));

  // 201: claimed now, so the executor may act. 200: claimed before, so it must not act again.
  app.post<{ Params: { id: string } }>(
    '/internal/actions/:id/execution',
    async (request, reply) => {
      const id = z.guid().safeParse(request.params.id);
      const body = claimBody.safeParse(request.body);
      if (!id.success || !body.success) {
        return badRequest(reply, [...(id.error?.issues ?? []), ...(body.error?.issues ?? [])]);
      }
      const claimed = await claim(id.data, body.data.dry_run);
      if (claimed === 'not_found') {
        return problem(reply, 404, 'Not Found', 'No approved action has this id.');
      }
      return reply.code(claimed.created ? 201 : 200).send(claimed.execution);
    },
  );

  app.post<{ Params: { id: string } }>(
    '/internal/actions/:id/execution/result',
    async (request, reply) => {
      const id = z.guid().safeParse(request.params.id);
      const body = resultBody.safeParse(request.body);
      if (!id.success || !body.success) {
        return badRequest(reply, [...(id.error?.issues ?? []), ...(body.error?.issues ?? [])]);
      }
      const result = await report(id.data, body.data);
      if (result === 'not_found')
        return problem(reply, 404, 'Not Found', 'No execution has this id.');
      if (result === 'already_finished') {
        return problem(reply, 409, 'Conflict', 'The execution already has its result.');
      }
      return result;
    },
  );
  return app;
}
