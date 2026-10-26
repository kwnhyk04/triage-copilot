import { createHash, timingSafeEqual } from 'node:crypto';
import { STATUS_CODES } from 'node:http';

import rateLimit from '@fastify/rate-limit';
import type { components } from '@triage/ts-clients/gateway';
import type { Investigation, Resolvers } from '@triage/ts-clients/graphql';
import { typeDefs } from '@triage/ts-clients/schema';
import { z } from '@triage/ts-config';
import Fastify, { type FastifyInstance, type FastifyReply, type FastifyRequest } from 'fastify';
import { createPubSub, createSchema, createYoga } from 'graphql-yoga';

import type { GatewayConfig } from './config.ts';
import {
  type FindTenant,
  type Intake,
  sha256,
  webhookSchema,
  withComputedFingerprints,
} from './intake.ts';
import type { GetInvestigation, ListInvestigations } from './investigations.ts';

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

/** Records a human decision on a plan that holds a proposal; nothing runs from it. */
export type DecidePlan = (
  planId: string,
  verdict: 'approved' | 'rejected',
  decision: Schemas['Decision'],
) => Promise<Schemas['Plan'] | 'not_found' | 'already_decided' | 'no_proposal'>;

export interface AppDeps {
  config: Pick<
    GatewayConfig,
    'LOG_LEVEL' | 'APPROVER_API_KEY' | 'GATEWAY_API_KEY' | 'RATE_LIMIT_PER_MINUTE'
  >;
  /** Rejects when a dependency the gateway needs is unreachable. */
  ping: () => Promise<void>;
  updates: Updates;
  decide: Decide;
  decidePlan: DecidePlan;
  findTenant: FindTenant;
  intake: Intake;
  listInvestigations: ListInvestigations;
  getInvestigation: GetInvestigation;
}

// Routes that take no X-API-Key: the probes, and the routes with a credential of their own.
const OWN_CREDENTIAL = new Set([
  '/healthz',
  '/readyz',
  '/v1/alerts',
  '/v1/actions/:id/approve',
  '/v1/actions/:id/reject',
  '/v1/plans/:id/approve',
  '/v1/plans/:id/reject',
]);

/** Webhook bodies over 1 MB get 413. */
export const BODY_LIMIT = 1024 * 1024;
/** A client must send its whole request within 10 s. */
export const REQUEST_TIMEOUT_MS = 10_000;

const header = (request: FastifyRequest, name: string) => {
  const value = request.headers[name];
  return typeof value === 'string' && value !== '' ? value : undefined;
};
export const bearer = (request: FastifyRequest) =>
  /^Bearer (\S+)$/i.exec(header(request, 'authorization') ?? '')?.[1];
export const httpError = (statusCode: number, message: string) =>
  Object.assign(new Error(message), { statusCode });

const decisionBody = z.strictObject({
  approver: z.string().min(1).max(200),
  reason: z.string().max(2000).exactOptional(),
});
const listQuery = z.object({
  limit: z.coerce.number().int().min(1).max(100).default(25),
  cursor: z.string().min(1).max(512).optional(),
});

const digest = (value: string) => createHash('sha256').update(value).digest();
// Equal-length digests let timingSafeEqual compare without revealing how much matched.
export const sameSecret = (given: string, expected: string) =>
  timingSafeEqual(digest(given), digest(expected));

/** RFC 9457 problem details, the contract's error format. */
export const problem = (reply: FastifyReply, status: number, title: string, detail?: string) =>
  reply
    .code(status)
    .type('application/problem+json')
    .send({ title, status, ...(detail === undefined ? {} : { detail }) });

export const badRequest = (reply: FastifyReply, issues: readonly { message: string }[]) =>
  problem(reply, 400, 'Bad Request', issues.map((issue) => issue.message).join('; '));

/** Errors thrown anywhere, Fastify's own included, leave as problem details. */
export function answerWithProblems(app: FastifyInstance) {
  app.setErrorHandler((error: Error & { statusCode?: number }, _request, reply) => {
    const status = error.statusCode ?? 500;
    const title = STATUS_CODES[status] ?? 'Error';
    if (status < 500) return problem(reply, status, title, error.message);
    app.log.error({ err: error }, 'request failed');
    return problem(reply, status, title);
  });
}

export async function buildApp({
  config,
  ping,
  updates,
  decide,
  decidePlan,
  findTenant,
  intake,
  listInvestigations,
  getInvestigation,
}: AppDeps): Promise<FastifyInstance> {
  const app = Fastify({
    logger: { level: config.LOG_LEVEL, base: { service: 'gateway' } },
    requestTimeout: REQUEST_TIMEOUT_MS,
  });
  answerWithProblems(app);

  // Registered before the routes, which it hooks as they are added.
  await app.register(rateLimit, {
    max: config.RATE_LIMIT_PER_MINUTE,
    timeWindow: 60_000,
    // ponytail: in-memory counters fit one gateway; several gateways need a shared store (Redis).
    // Invented credentials each get their own count, so an edge limit per address must stop
    // floods of them.
    keyGenerator: (request) => {
      const credential =
        bearer(request) ?? header(request, 'x-api-key') ?? header(request, 'x-approver-key');
      return credential === undefined ? `ip:${request.ip}` : `key:${sha256(credential)}`;
    },
    // RateLimit-Limit, RateLimit-Remaining and RateLimit-Reset, and Retry-After on 429.
    enableDraftSpec: true,
  });

  // Every other route takes X-API-Key, routes added later included. preParsing runs after the
  // rate limit's onRequest hook and before the body is read.
  app.addHook('preParsing', async (request, reply, payload) => {
    if (OWN_CREDENTIAL.has(request.routeOptions.url ?? '')) return payload;
    const key = header(request, 'x-api-key');
    if (key === undefined || !sameSecret(key, config.GATEWAY_API_KEY)) {
      reply.header('WWW-Authenticate', 'ApiKey header="X-API-Key"');
      throw httpError(401, 'Send a valid X-API-Key.');
    }
    return payload;
  });

  const probe = { config: { rateLimit: false } } as const;
  app.get('/healthz', probe, () => ({ status: 'ok' }));
  app.get('/readyz', probe, async (_request, reply) => {
    try {
      await ping();
      return { status: 'ok' };
    } catch (error) {
      app.log.warn({ err: error }, 'readiness check failed');
      return reply.code(503).send({ status: 'unavailable' });
    }
  });

  // Alertmanager webhook. The token is checked after the rate limit and before the body is read.
  const tenants = new WeakMap<FastifyRequest, string>();
  app.post(
    '/v1/alerts',
    {
      bodyLimit: BODY_LIMIT,
      preParsing: async (request, reply, payload) => {
        const token = bearer(request);
        if (
          token === undefined &&
          (header(request, 'x-api-key') ?? header(request, 'x-approver-key'))
        ) {
          throw httpError(403, 'POST /v1/alerts takes the tenant webhook token only.');
        }
        let tenantId: string | undefined;
        try {
          tenantId = token === undefined ? undefined : await findTenant(sha256(token));
        } catch (error) {
          request.log.error({ err: error }, 'tenant lookup failed');
          reply.header('Retry-After', '5');
          throw httpError(503, 'Alerts cannot be stored right now.');
        }
        if (tenantId === undefined) {
          reply.header('WWW-Authenticate', 'Bearer');
          throw httpError(401, 'Send a valid tenant webhook token.');
        }
        tenants.set(request, tenantId);
        return payload;
      },
    },
    async (request, reply) => {
      const webhook = webhookSchema.safeParse(request.body);
      if (!webhook.success) {
        const issues = webhook.error.issues.slice(0, 10);
        const detail = issues.map((issue) => `${issue.path.join('.')}: ${issue.message}`);
        return problem(reply, 400, 'Bad Request', detail.join('; '));
      }
      const tenantId = tenants.get(request);
      if (tenantId === undefined)
        throw new Error('webhook request reached its handler unauthenticated');
      // Deduplication uses fingerprints computed from the labels; a sender's differing value is
      // counted, never refused.
      const { webhook: computed, mismatches } = withComputedFingerprints(webhook.data);
      if (mismatches > 0) {
        request.log.warn({ mismatches }, 'payload fingerprints differ from their labels');
      }
      let accepted;
      try {
        // Only stores the alerts; the agent investigates from the queue, never in this request.
        accepted = await intake(tenantId, computed);
      } catch (error) {
        request.log.error({ err: error }, 'alert intake failed');
        reply.header('Retry-After', '5');
        return problem(reply, 503, 'Service Unavailable', 'The alerts could not be stored.');
      }
      return reply.code(202).send(accepted);
    },
  );

  // Approve and reject, of actions and of plans, take only the approver key, which the web app's
  // server alone holds.
  const decisions = [
    ['actions', 'action', decide],
    ['plans', 'plan', decidePlan],
  ] as const;
  for (const [route, verdict] of [
    ['approve', 'approved'],
    ['reject', 'rejected'],
  ] as const)
    for (const [resource, noun, record] of decisions) {
      addDecisionRoute(`/v1/${resource}/:id/${route}`, noun, (id, body) =>
        record(id, verdict, body),
      );
    }

  function addDecisionRoute(
    url: string,
    noun: string,
    record: (id: string, decision: Schemas['Decision']) => ReturnType<Decide | DecidePlan>,
  ) {
    app.post<{ Params: { id: string } }>(url, async (request, reply) => {
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
        return badRequest(reply, [...(id.error?.issues ?? []), ...(body.error?.issues ?? [])]);
      }
      let result;
      try {
        result = await record(id.data, body.data);
      } catch (error) {
        request.log.error({ err: error }, 'decision failed');
        reply.header('Retry-After', '5');
        return problem(reply, 503, 'Service Unavailable', 'The decision could not be recorded.');
      }
      if (result === 'not_found')
        return problem(reply, 404, 'Not Found', `No ${noun} has this id.`);
      if (result === 'already_decided') {
        return problem(reply, 409, 'Conflict', `The ${noun} was already approved or rejected.`);
      }
      if (result === 'no_proposal') {
        return problem(reply, 409, 'Conflict', 'The plan holds no proposal to decide.');
      }
      return result;
    });
  }

  // Newest first, keyset paginated; the limit is enforced here, whatever the client asks.
  app.get('/v1/investigations', async (request, reply) => {
    const query = listQuery.safeParse(request.query);
    if (!query.success) return badRequest(reply, query.error.issues);
    const page = await listInvestigations(query.data);
    if (page === 'bad_cursor') {
      return problem(reply, 400, 'Bad Request', 'cursor: pass a next_cursor this API returned');
    }
    return page;
  });

  app.get<{ Params: { id: string } }>('/v1/investigations/:id', async (request, reply) => {
    const id = z.guid().safeParse(request.params.id);
    if (!id.success) return badRequest(reply, id.error.issues);
    const investigation = await getInvestigation(id.data);
    if (investigation === undefined) {
      return problem(reply, 404, 'Not Found', 'No investigation has this id.');
    }
    return investigation;
  });

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
