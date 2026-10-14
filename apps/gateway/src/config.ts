import { type Env, loadConfig, z } from '@triage/ts-config';

export const loadGatewayConfig = (env?: Env) =>
  loadConfig(
    z.object({
      GATEWAY_HOST: z.string().default('127.0.0.1'),
      GATEWAY_PORT: z.coerce.number().int().default(4200),
      GATEWAY_DATABASE_URL: z.url(),
      // Verifies X-Approver-Key on approve and reject; only the web app's server sends it.
      APPROVER_API_KEY: z.string().min(32),
      LOG_LEVEL: z.enum(['debug', 'info', 'warn', 'error']).default('info'),
    }),
    env,
  );

export type GatewayConfig = ReturnType<typeof loadGatewayConfig>;
