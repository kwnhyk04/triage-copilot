import { loadConfig, z } from './index.ts';

const schema = z.object({
  HOST: z.string().default('127.0.0.1'),
  PORT: z.coerce.number().int().default(4200),
  DB_URL: z.url(),
  LEVEL: z.enum(['info', 'debug']).default('info'),
});

describe('loadConfig', () => {
  it('parses values, applies defaults and treats empty values as unset', () => {
    expect(
      loadConfig(schema, { PORT: '4300', DB_URL: 'postgres://u:p@db:5432/x', LEVEL: '' }),
    ).toEqual({ HOST: '127.0.0.1', PORT: 4300, DB_URL: 'postgres://u:p@db:5432/x', LEVEL: 'info' });
  });

  it('names every missing or bad variable without echoing values', () => {
    const load = () => loadConfig(schema, { PORT: 'secret-value', LEVEL: 'loud' });
    expect(load).toThrow(/^Invalid environment: PORT: .+; DB_URL is required; LEVEL: .+$/);
    expect(load).not.toThrow(/secret-value/);
  });
});
