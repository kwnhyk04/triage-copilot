import type { InvestigationUpdate, Resolvers } from '@triage/ts-clients/graphql';
import { typeDefs } from '@triage/ts-clients/schema';
import Fastify, { type FastifyInstance, type FastifyReply, type FastifyRequest } from 'fastify';
import { createPubSub, createSchema, createYoga } from 'graphql-yoga';

import type { GatewayConfig } from './config.ts';

/** In-process fan-out of investigation updates, keyed by investigation id. */
export const createUpdates = () =>
  createPubSub<{ 'investigation:updated': [id: string, update: InvestigationUpdate] }>();

export type Updates = ReturnType<typeof createUpdates>;

export interface AppDeps {
  config: Pick<GatewayConfig, 'LOG_LEVEL'>;
  /** Rejects when a dependency the gateway needs is unreachable. */
  ping: () => Promise<void>;
  updates: Updates;
}

export function buildApp({ config, ping, updates }: AppDeps): FastifyInstance {
  const app = Fastify({ logger: { level: config.LOG_LEVEL, base: { service: 'gateway' } } });

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

  const resolvers: Resolvers = {
    Query: { health: () => ({ status: 'ok' }) },
    Subscription: {
      investigationUpdated: {
        subscribe: (_parent, { id }) => updates.subscribe('investigation:updated', id),
        resolve: (update: InvestigationUpdate) => update,
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
