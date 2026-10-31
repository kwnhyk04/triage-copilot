import { z } from 'zod';

export { z };

export type Env = Readonly<Record<string, string | undefined>>;

/** The keys and tokens .env.example ships, which production refuses; a test keeps them equal. */
export const PLACEHOLDERS = {
  GATEWAY_API_KEY: 'gateway-dev-api-key-00000000000000',
  APPROVER_API_KEY: 'approver-dev-key-0000000000000000',
  AGENT_SERVICE_TOKEN: 'agent-service-dev-token-000000000000',
  EXECUTOR_SERVICE_TOKEN: 'executor-service-dev-token-0000000',
} as const;

/** Whether `value` is one of the .env.example keys or tokens. */
export const isPlaceholder = (value: string) =>
  (Object.values(PLACEHOLDERS) as readonly string[]).includes(value);

/**
 * Validates the environment against `schema` at startup. Empty values count as unset, and errors
 * name every bad variable without echoing its value.
 */
export function loadConfig<T extends z.ZodObject>(schema: T, env: Env = process.env): z.infer<T> {
  const set = Object.fromEntries(Object.entries(env).filter(([, value]) => value !== ''));
  const result = schema.safeParse(set);
  if (result.success) return result.data;
  const problems = result.error.issues.map((issue) => {
    const name = issue.path.map(String).join('.');
    return set[name] === undefined ? `${name} is required` : `${name}: ${issue.message}`;
  });
  throw new Error(`Invalid environment: ${problems.join('; ')}`);
}
