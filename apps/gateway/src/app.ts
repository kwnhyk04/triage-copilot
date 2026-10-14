import { createHash, timingSafeEqual } from 'node:crypto';

import type { components } from '@triage/ts-clients/gateway';
import type { Investigation, Resolvers } from '@triage/ts-clients/graphql';
import { typeDefs } from '@triage/ts-clients/schema';
import { z } from '@triage/ts-config';
import Fastify, { type FastifyInstance, type FastifyReply, type FastifyRequest } from 'fastify';
import { createPubSub, createSchema, createYoga } from 'graphql-yoga';

import type { GatewayConfig } from './config.ts';

type Schemas = components['schemas'];

/** In-process fan-out of changed investigations, keyed by investigation id. */
export const createUpdates = () =>
  createPubSub<{ 'investigation:updated': [id: string, investigation: Investigation] }>();

export type Updates = ReturnType<typeof createUpdates>;

/** Records a human decision on a pending action. */
export type Decide = (
  actionId: string,
  verdict: 'approved' | 'rejected',
  decision: Schemas['Decision'],
) => Promise<Schemas['ProposedAction'] | 'not_found' | 'already_decided'>;

export interface AppDeps {
  config: Pick<GatewayConfig, 'LOG_LEVEL' | 'APPROVER_API_KEY'>;
  /** Rejects when a dependency the gateway needs is unreachable. */
  ping: () => Promise<void>;
  updates: Updates;
  decide: Decide;
}

const decisionBody = z.strictObject({
  approver: z.string().min(1).max(200),
  reason: z.string().max(2000).exactOptional(),
});

const digest = (value: string) => createHash('sha256').update(value).digest();
// Equal-length digests let timingSafeEqual compare without revealing how much matched.
const sameSecret = (given: string, expected: string) =>
  timingSafeEqual(digest(given), digest(expected));

/** RFC 9457 problem details, the contract's error format. */
const problem = (reply: FastifyReply, status: number, title: string, detail?: string) =>
  reply
    .code(status)
    .type('application/problem+json')
    .send({ title, status, ...(detail === undefined ? {} : { detail }) });

export function buildApp({ config, ping, updates, decide }: AppDeps): FastifyInstance {
  const app = Fastify({ logger: { level: config.LOG_LEVEL, base: { service: 'gateway' } } });

  // Fastify's own errors, such as malformed JSON, leave as problem details too.
  app.setErrorHandler((error: Error & { statusCode?: number }, _request, reply) => {
    const status = error.statusCode ?? 500;
    if (status >= 500) app.log.error({ err: error }, 'request failed');
    return problem(reply, status, status >= 500 ? 'Internal Server Error' : error.message);
  });

  app.get('/healthz', () => ({ status: 'ok' }));
  app.get('/readyz', async (_request, reply) => {
    try {
      await ping();
      return { status: 'ok' };
    } catch (error) {
      app.log.warn({ err: error }, 'readiness check failed');
      return reply.code(503).send({ status: 'unavailable' });
    }
  });

  // Approve and reject take only the approver key, which the web app's server alone holds.
  for (const [route, verdict] of [
    ['approve', 'approved'],
    ['reject', 'rejected'],
  ] as const) {
    app.post<{ Params: { id: string } }>(`/v1/actions/:id/${route}`, async (request, reply) => {
      const key = request.headers['x-approver-key'];
      if (key === undefined && (request.headers['x-api-key'] ?? request.headers.authorization)) {
        return problem(reply, 403, 'Forbidden', 'Approve and reject take X-Approver-Key only.');
      }
      if (typeof key !== 'string' || !sameSecret(key, config.APPROVER_API_KEY)) {
        reply.header('WWW-Authenticate', 'ApiKey header="X-Approver-Key"');
        return problem(reply, 401, 'Unauthorized', 'Send a valid X-Approver-Key.');
      }
      const id = z.guid().safeParse(request.params.id);
      const body = decisionBody.safeParse(request.body);
      if (!id.success || !body.success) {
        const issues = [...(id.error?.issues ?? []), ...(body.error?.issues ?? [])];
        return problem(reply, 400, 'Bad Request', issues.map((issue) => issue.message).join('; '));
      }
      const result = await decide(id.data, verdict, body.data);
      if (result === 'not_found') return problem(reply, 404, 'Not Found', 'No action has this id.');
      if (result === 'already_decided') {
        return problem(reply, 409, 'Conflict', 'The action was already approved or rejected.');
      }
      return result;
    });
  }

  const resolvers: Resolvers = {
    Query: { health: () => ({ status: 'ok' }) },
    Subscription: {
      investigationUpdated: {
        subscribe: (_parent, { id }) => updates.subscribe('investigation:updated', id),
        resolve: (investigation: Investigation) => investigation,
      },
    },
  };

  const yoga = createYoga<{ req: FastifyRequest; reply: FastifyReply }>({
    schema: createSchema({ typeDefs, resolvers }),
    logging: app.log,
    // Only the web backend-for-frontend calls the gateway, never a browser: no CORS, no GraphiQL.
    cors: false,
    graphiql: false,
  });

  // Yoga answers queries as JSON and subscriptions as server-sent events.
  app.route({
    url: yoga.graphqlEndpoint,
    method: ['GET', 'POST', 'OPTIONS'],
    handler: async (request, reply) => {
      const response = await yoga.handleNodeRequestAndResponse(request, reply, {
        req: request,
        reply,
      });
      response.headers.forEach((value, key) => {
        reply.header(key, value);
      });
      reply.status(response.status);
      return reply.send(response.body);
    },
  });

  return app;
}
