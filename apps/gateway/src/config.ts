import { type Env, isPlaceholder, loadConfig, z } from '@triage/ts-config';

// Each grants different powers, so no two may be the same secret.
const CREDENTIALS = [
  'APPROVER_API_KEY',
  'GATEWAY_API_KEY',
  'AGENT_SERVICE_TOKEN',
  'EXECUTOR_SERVICE_TOKEN',
] as const;
// The agent's LIVE_TENANTS, {"<tenant uuid>": "freight-audit"}; the gateway needs the tenants only.
const liveTenants = z.record(z.guid(), z.enum(['freight-audit', 'filing-lens']));
// LIVE_TENANTS's JSON as the tenant uuids, or an issue naming the shape it must have.
const liveTenantIds = (text: string, ctx: z.RefinementCtx) => {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    parsed = undefined;
  }
  const tenants = liveTenants.safeParse(parsed);
  if (tenants.success) return Object.keys(tenants.data);
  const message = 'must map tenant uuids to freight-audit or filing-lens, as JSON';
  ctx.addIssue({ code: 'custom', message });
  return z.NEVER;
};
/** LIVE_TENANTS as the gateway reads it: the uuids of the tenants whose alerts are real incidents. */
export const liveTenantsSetting = z.string().default('{}').transform(liveTenantIds);
/** LIVE_TENANTS with no default ({} while live is off): demo-save's guard against live data. */
export const liveTenantsRequired = z.string().transform(liveTenantIds);
/** Minutes without a step (or a message without a receive) before an investigation counts as stuck. */
export const stuckAfterMinutesSetting = z.coerce.number().int().positive().default(10);
const proxyAddress = z.union([z.ipv4(), z.ipv6(), z.cidrv4(), z.cidrv6()]);
// The proxies in front, as Fastify's trustProxy: their addresses and CIDRs. Never true, which would
// take any client's X-Forwarded-For as its address, nor a hop count, on which Fastify trusts nothing.
const trustProxySetting = z
  .string()
  .transform((value, ctx) => {
    const proxies = value.split(',').map((item) => item.trim());
    if (proxies.every((item) => proxyAddress.safeParse(item).success)) return proxies;
    const message = 'takes the proxies as addresses or CIDRs, never true or a hop count';
    ctx.addIssue({ code: 'custom', message });
    return z.NEVER;
  })
  .optional();

export const loadGatewayConfig = (env?: Env) =>
  loadConfig(
    z
      .object({
        // The production compose sets production, which refuses the .env.example keys.
        NODE_ENV: z.string().optional(),
        GATEWAY_HOST: z.string().default('127.0.0.1'),
        GATEWAY_PORT: z.coerce.number().int().default(4200),
        GATEWAY_DATABASE_URL: z.url(),
        // Verifies X-Approver-Key on approve and reject; only the web app's server sends it.
        APPROVER_API_KEY: z.string().min(32),
        // Verifies X-API-Key on every route without a credential of its own (/graphql included).
        GATEWAY_API_KEY: z.string().min(32),
        // The executor's listener: never the public port, and its own token.
        GATEWAY_INTERNAL_HOST: z.string().default('127.0.0.1'),
        GATEWAY_INTERNAL_PORT: z.coerce.number().int().default(4201),
        EXECUTOR_SERVICE_TOKEN: z.string().min(32),
        SQS_ENDPOINT_URL: z.url().optional(),
        AWS_REGION: z.string().min(1).default('us-east-1'),
        INVESTIGATIONS_QUEUE: z.string().min(1).default('investigations.fifo'),
        // Approved actions (ActionApproved) for the executor.
        ACTIONS_QUEUE: z.string().min(1).default('actions.fifo'),
        // StepRecorded and ReportRecorded from the agent, copied into the mirror tables.
        AGENT_EVENTS_QUEUE: z.string().min(1).default('agent-events.fifo'),
        // Agent events the gateway can never store go here at once; `make dlq-replay` returns them.
        AGENT_EVENTS_DLQ: z.string().min(1).default('agent-events-dlq.fifo'),
        // Requests per minute for each credential, or each client address without one.
        RATE_LIMIT_PER_MINUTE: z.coerce.number().int().positive().default(600),
        // Failed credential checks (401) one client address may make a minute; past it, 429.
        AUTH_FAILURES_PER_MINUTE: z.coerce.number().int().positive().default(20),
        // Behind Tailscale Funnel the client address is the sidecar's unless its proxy is trusted.
        GATEWAY_TRUST_PROXY: trustProxySetting,
        // Investigations one tenant may start within any 5 minutes; later groups go uninvestigated.
        MAX_NEW_INVESTIGATIONS: z.coerce.number().int().nonnegative().default(5),
        // And within one UTC day: 30 triages a day per app (each app is one tenant).
        MAX_DAILY_INVESTIGATIONS: z.coerce.number().int().nonnegative().default(30),
        // Tenants whose alerts are real incidents: their snapshots and steps go after 30 days, and
        // the list serves them as live.
        LIVE_TENANTS: liveTenantsSetting,
        // The agent's internal API, which the recovery sweeper asks with the service token.
        AGENT_URL: z.url().default('http://127.0.0.1:4210'),
        AGENT_SERVICE_TOKEN: z.string().min(32),
        // How often the recovery sweeper runs, and how long an investigation may go without a
        // step (or a message without a receive) before it counts as stuck. A queued investigation
        // with a newer step reads as running.
        SWEEPER_INTERVAL_SECONDS: z.coerce.number().int().positive().default(15),
        SWEEPER_STUCK_AFTER_MINUTES: stuckAfterMinutesSetting,
        LOG_LEVEL: z.enum(['debug', 'info', 'warn', 'error']).default('info'),
      })
      .superRefine((config, ctx) => {
        CREDENTIALS.forEach((name, i) => {
          if (CREDENTIALS.slice(0, i).some((other) => config[other] === config[name])) {
            ctx.addIssue({ code: 'custom', path: [name], message: 'must differ from the others' });
          }
          if (config.NODE_ENV === 'production' && isPlaceholder(config[name])) {
            const message = 'is the .env.example placeholder; make a fresh one';
            ctx.addIssue({ code: 'custom', path: [name], message });
          }
        });
      }),
    env,
  );

export type GatewayConfig = ReturnType<typeof loadGatewayConfig>;
