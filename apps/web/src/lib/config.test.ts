import { PLACEHOLDERS } from '@triage/ts-config';

import { loadWebConfig } from './config.ts';

const KEYS = {
  GATEWAY_URL: 'http://127.0.0.1:4200',
  GATEWAY_API_KEY: 'gateway-key-for-these-tests-000000000',
  APPROVER_API_KEY: 'approver-key-for-these-tests-00000000',
};
const HASH =
  '$argon2id$v=19$m=65536,t=2,p=1$c29tZXNhbHQ$CTFhFdXPJO1aFaMaO6Mm5c8y7cJHAph8ArZWb2GRPPc';
const SECRET = 'session-secret-for-these-tests-0000';
/** The error loadWebConfig throws for this environment, or none. */
const problem = (env: Record<string, string>) => {
  try {
    loadWebConfig({ ...KEYS, ...env });
  } catch (error) {
    return (error as Error).message;
  }
  return 'none';
};

describe('loadWebConfig', () => {
  it('runs in dev with sign-in off, empty values counting as unset, or with both settings', () => {
    const off = loadWebConfig({ ...KEYS, NODE_ENV: 'development' });
    expect([off.LOGIN_PASSWORD_HASH, off.SESSION_SECRET]).toEqual([undefined, undefined]);
    const empty = { NODE_ENV: 'development', LOGIN_PASSWORD_HASH: '', SESSION_SECRET: '' };
    expect(problem(empty)).toBe('none');
    const both = { LOGIN_PASSWORD_HASH: HASH, SESSION_SECRET: SECRET };
    expect(loadWebConfig({ ...KEYS, ...both, NODE_ENV: 'production' })).toMatchObject(both);
  });

  it('names each variable that is missing in production, half set, or bad', () => {
    for (const [env, expected] of [
      [{ NODE_ENV: 'production' }, 'LOGIN_PASSWORD_HASH is required'],
      [{ NODE_ENV: 'production' }, 'SESSION_SECRET is required'],
      [{ LOGIN_PASSWORD_HASH: HASH }, 'SESSION_SECRET is required'],
      [{ SESSION_SECRET: SECRET }, 'LOGIN_PASSWORD_HASH is required'],
      [{ LOGIN_PASSWORD_HASH: 'hunter2', SESSION_SECRET: SECRET }, 'LOGIN_PASSWORD_HASH: must be'],
      [{ LOGIN_PASSWORD_HASH: HASH, SESSION_SECRET: KEYS.GATEWAY_API_KEY }, 'SESSION_SECRET: must'],
      [
        { LOGIN_PASSWORD_HASH: HASH, SESSION_SECRET: KEYS.APPROVER_API_KEY },
        'SESSION_SECRET: must',
      ],
      [{ LOGIN_PASSWORD_HASH: HASH, SESSION_SECRET: SECRET.slice(0, 31) }, 'SESSION_SECRET:'],
    ] as const) {
      expect([env, problem(env)]).toEqual([env, expect.stringContaining(expected)]);
    }
  });

  it('refuses the .env.example keys in production, and one key for both roles anywhere', () => {
    const signIn = { LOGIN_PASSWORD_HASH: HASH, SESSION_SECRET: SECRET };
    const production = { ...signIn, NODE_ENV: 'production' };
    for (const [name, value, expected] of [
      ['GATEWAY_API_KEY', PLACEHOLDERS.GATEWAY_API_KEY, 'is the .env.example placeholder'],
      ['APPROVER_API_KEY', PLACEHOLDERS.APPROVER_API_KEY, 'is the .env.example placeholder'],
    ] as const) {
      const said = problem({ ...production, [name]: value });
      expect([name, said]).toEqual([name, expect.stringContaining(`${name}: ${expected}`)]);
      expect(said).not.toContain(value);
    }
    const shared = problem({ APPROVER_API_KEY: KEYS.GATEWAY_API_KEY });
    expect(shared).toContain('APPROVER_API_KEY: must differ from GATEWAY_API_KEY');
    expect(shared).not.toContain(KEYS.GATEWAY_API_KEY);
    // next dev runs on them.
    const dev = { NODE_ENV: 'development', ...PLACEHOLDERS };
    expect(problem(dev)).toBe('none');
  });

  it('never echoes a value it refuses', () => {
    const bad = { LOGIN_PASSWORD_HASH: 'hunter2-not-a-hash', SESSION_SECRET: 'short-secret' };
    expect(problem(bad)).not.toMatch(/hunter2|short-secret/);
  });
});
