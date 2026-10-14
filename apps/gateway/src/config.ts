import { type Env, loadConfig, z } from '@triage/ts-config';

export const loadGatewayConfig = (env?: Env) =>
  loadConfig(
    z.object({
      GATEWAY_HOST: z.string().default('127.0.0.1'),
      GATEWAY_PORT: z.coerce.number().int().default(4200),
      GATEWAY_DATABASE_URL: z.url(),
      // Verifies X-Approver-Key on approve and reject; only the web app's server sends it.
      APPROVER_API_KEY: z.string().min(32),
      SQS_ENDPOINT_URL: z.url().optional(),
      AWS_REGION: z.string().min(1).default('us-east-1'),
      INVESTIGATIONS_QUEUE: z.string().min(1).default('investigations.fifo'),
      // Requests per minute for each credential, or each client address without one.
      RATE_LIMIT_PER_MINUTE: z.coerce.number().int().positive().default(600),
      // Investigations one tenant may start within any 5 minutes; later groups go uninvestigated.
      MAX_NEW_INVESTIGATIONS: z.coerce.number().int().nonnegative().default(5),
      LOG_LEVEL: z.enum(['debug', 'info', 'warn', 'error']).default('info'),
    }),
    env,
  );

export type GatewayConfig = ReturnType<typeof loadGatewayConfig>;
