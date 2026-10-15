import { type Env, loadConfig, z } from '@triage/ts-config';

// Each grants different powers, so no two may be the same secret.
const CREDENTIALS = [
  'APPROVER_API_KEY',
  'GATEWAY_API_KEY',
  'AGENT_SERVICE_TOKEN',
  'EXECUTOR_SERVICE_TOKEN',
] as const;

export const loadGatewayConfig = (env?: Env) =>
  loadConfig(
    z
      .object({
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
        // Investigations one tenant may start within any 5 minutes; later groups go uninvestigated.
        MAX_NEW_INVESTIGATIONS: z.coerce.number().int().nonnegative().default(5),
        // The agent's internal API, which the recovery sweeper asks with the service token.
        AGENT_URL: z.url().default('http://127.0.0.1:4210'),
        AGENT_SERVICE_TOKEN: z.string().min(32),
        // How often the recovery sweeper runs, and how long an investigation may go without a
        // step (or a message without a receive) before it counts as stuck.
        SWEEPER_INTERVAL_SECONDS: z.coerce.number().int().positive().default(15),
        SWEEPER_STUCK_AFTER_MINUTES: z.coerce.number().int().positive().default(10),
        LOG_LEVEL: z.enum(['debug', 'info', 'warn', 'error']).default('info'),
      })
      .superRefine((config, ctx) => {
        CREDENTIALS.forEach((name, i) => {
          if (CREDENTIALS.slice(0, i).some((other) => config[other] === config[name])) {
            ctx.addIssue({ code: 'custom', path: [name], message: 'must differ from the others' });
          }
        });
      }),
    env,
  );

export type GatewayConfig = ReturnType<typeof loadGatewayConfig>;
