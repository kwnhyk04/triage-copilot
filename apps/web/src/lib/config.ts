import { type Env, loadConfig, z } from '@triage/ts-config';

import { parseHash } from './password.ts';

const SIGN_IN = ['LOGIN_PASSWORD_HASH', 'SESSION_SECRET'] as const;

/** Server-only settings: none carry the NEXT_PUBLIC_ prefix, so none reach the browser. */
export const loadWebConfig = (env?: Env) =>
  loadConfig(
    z
      .object({
        // next start and Vercel run production, which needs sign-in; next dev runs development.
        NODE_ENV: z.string().optional(),
        GATEWAY_URL: z.url(),
        // Sent as X-API-Key on the gateway's read routes and /graphql; never reaches the browser.
        GATEWAY_API_KEY: z.string().min(32),
        // Only this server holds it; the gateway accepts it on approve and reject alone.
        APPROVER_API_KEY: z.string().min(32),
        // The one sign-in's argon2id hash, from `pnpm --filter @triage/web hash-password`.
        LOGIN_PASSWORD_HASH: z
          .string()
          .refine((hash) => parseHash(hash) !== undefined, {
            message: 'must be an argon2id hash from hash-password, within its cost bounds',
          })
          .optional(),
        // Signs the session cookie; rotating it signs everyone out.
        SESSION_SECRET: z.string().min(32).optional(),
      })
      .superRefine((config, ctx) => {
        // Required in production; in dev both or neither, and without them sign-in is off.
        const wanted =
          config.NODE_ENV === 'production' || SIGN_IN.some((name) => config[name] !== undefined);
        for (const name of SIGN_IN) {
          if (wanted && config[name] === undefined) {
            ctx.addIssue({ code: 'custom', path: [name], message: 'is required' });
          }
        }
        const secret = config.SESSION_SECRET;
        if (secret === config.GATEWAY_API_KEY || secret === config.APPROVER_API_KEY) {
          const message = 'must differ from GATEWAY_API_KEY and APPROVER_API_KEY';
          ctx.addIssue({ code: 'custom', path: ['SESSION_SECRET'], message });
        }
      }),
    env,
  );

export type WebConfig = ReturnType<typeof loadWebConfig>;
