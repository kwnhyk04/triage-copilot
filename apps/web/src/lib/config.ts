import { type Env, loadConfig, z } from '@triage/ts-config';

/** Server-only settings: none carry the NEXT_PUBLIC_ prefix, so none reach the browser. */
export const loadWebConfig = (env?: Env) =>
  loadConfig(
    z.object({
      GATEWAY_URL: z.url(),
      // Only this server holds it; the gateway accepts it on approve and reject alone.
      APPROVER_API_KEY: z.string().min(32),
    }),
    env,
  );

export type WebConfig = ReturnType<typeof loadWebConfig>;
