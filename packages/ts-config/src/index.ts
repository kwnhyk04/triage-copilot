import { z } from 'zod';

export { z };

export type Env = Readonly<Record<string, string | undefined>>;

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
