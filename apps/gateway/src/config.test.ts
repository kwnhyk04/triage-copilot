import { loadConfig, PLACEHOLDERS, z } from '@triage/ts-config';

import { liveTenantsRequired, loadGatewayConfig } from './config.ts';

describe('liveTenantsRequired', () => {
  // demo-save's one guard against live data, so it never falls back to "no tenant is live".
  const live = (env: Record<string, string>) =>
    loadConfig(z.object({ LIVE_TENANTS: liveTenantsRequired }), env).LIVE_TENANTS;

  it('refuses an unset or empty LIVE_TENANTS, and reads {} or a mapping', () => {
    expect(() => live({})).toThrow('LIVE_TENANTS is required');
    expect(() => live({ LIVE_TENANTS: '' })).toThrow('LIVE_TENANTS is required');
    expect(live({ LIVE_TENANTS: '{}' })).toEqual([]);
    const tenant = '0b6f2d1e-3c4a-4f5e-8a9b-1c2d3e4f5a6b';
    expect(live({ LIVE_TENANTS: JSON.stringify({ [tenant]: 'freight-audit' }) })).toEqual([tenant]);
  });
});

const secret = (name: string) => `${name}-for-this-test-000000000000000`;
const env = {
  GATEWAY_DATABASE_URL: 'postgres://gateway@127.0.0.1:5434/triage',
  APPROVER_API_KEY: secret('approver'),
  GATEWAY_API_KEY: secret('gateway'),
  EXECUTOR_SERVICE_TOKEN: secret('executor'),
  AGENT_SERVICE_TOKEN: secret('agent'),
};
const TENANT = '0b6f2d1e-3c4a-4f5e-8a9b-1c2d3e4f5a6b';

describe('LIVE_TENANTS', () => {
  it('is the tenants whose alerts are real incidents, as the agent reads them', () => {
    expect(loadGatewayConfig(env).LIVE_TENANTS).toEqual([]);
    // Empty, as .env.example leaves it, counts as unset.
    expect(loadGatewayConfig({ ...env, LIVE_TENANTS: '' }).LIVE_TENANTS).toEqual([]);
    const live = { ...env, LIVE_TENANTS: `{"${TENANT}": "freight-audit"}` };
    expect(loadGatewayConfig(live).LIVE_TENANTS).toEqual([TENANT]);
  });

  it.each([
    ['not JSON', `{${TENANT}: freight-audit}`],
    ['an app TC does not triage', `{"${TENANT}": "payroll"}`],
    ['a tenant that is no uuid', '{"freight-audit": "freight-audit"}'],
    ['a list', `["${TENANT}"]`],
  ])('refuses %s, naming the variable', (_case, value) => {
    expect(() => loadGatewayConfig({ ...env, LIVE_TENANTS: value })).toThrow(
      'LIVE_TENANTS: must map tenant uuids to freight-audit or filing-lens, as JSON',
    );
  });
});

describe('in production', () => {
  it('refuses any key or token still at its .env.example value', () => {
    for (const name of [
      'APPROVER_API_KEY',
      'GATEWAY_API_KEY',
      'AGENT_SERVICE_TOKEN',
      'EXECUTOR_SERVICE_TOKEN',
    ] as const) {
      const load = () =>
        loadGatewayConfig({ ...env, NODE_ENV: 'production', [name]: PLACEHOLDERS[name] });
      expect(load).toThrow(`${name}: is the .env.example placeholder`);
      expect(load).not.toThrow(PLACEHOLDERS[name]);
    }
  });

  it('runs on fresh keys, and dev runs on the .env.example ones', () => {
    expect(() => loadGatewayConfig({ ...env, NODE_ENV: 'production' })).not.toThrow();
    expect(() => loadGatewayConfig({ ...env, ...PLACEHOLDERS })).not.toThrow();
  });
});

describe('GATEWAY_TRUST_PROXY', () => {
  it('is off by default and takes the proxies as addresses and CIDRs', () => {
    expect(loadGatewayConfig(env).GATEWAY_TRUST_PROXY).toBeUndefined();
    const empty = { ...env, GATEWAY_TRUST_PROXY: '' };
    expect(loadGatewayConfig(empty).GATEWAY_TRUST_PROXY).toBeUndefined();
    const proxies = { ...env, GATEWAY_TRUST_PROXY: '172.18.0.0/16, 10.0.0.1, ::1' };
    expect(loadGatewayConfig(proxies).GATEWAY_TRUST_PROXY).toEqual([
      '172.18.0.0/16',
      '10.0.0.1',
      '::1',
    ]);
  });

  // true would take any client's X-Forwarded-For as its address, and Fastify trusts nothing on a
  // hop count, since it cannot check the peer.
  it.each(['true', 'false', '1', '0', 'gateway', '300.1.1.1', '10.0.0.0/33', '10.0.0.1,'])(
    'refuses %s, naming the variable',
    (value) => {
      expect(() => loadGatewayConfig({ ...env, GATEWAY_TRUST_PROXY: value })).toThrow(
        'GATEWAY_TRUST_PROXY: takes the proxies as addresses or CIDRs, never true or a hop count',
      );
    },
  );

  // A range holding every client lets anyone claim any address: all of IPv4 or IPv6 in any
  // spelling, or all of IPv4 as the mapped IPv6 range Fastify matches IPv4 clients against.
  it.each(['0.0.0.0/0', '::/0', '0::/0', '::ffff:0:0/96', '::ffff:0.0.0.0/96', '10.0.0.1, ::/0'])(
    'refuses %s, which trusts every client',
    (value) => {
      expect(() => loadGatewayConfig({ ...env, GATEWAY_TRUST_PROXY: value })).toThrow(
        'GATEWAY_TRUST_PROXY: trusts every client; name the proxies instead',
      );
    },
  );

  it('takes narrower ranges, mapped ones included', () => {
    const value = '0.0.0.0/1, 10.231.233.16/28, ::ffff:10.0.0.0/104, 2001:db8::/32';
    expect(loadGatewayConfig({ ...env, GATEWAY_TRUST_PROXY: value }).GATEWAY_TRUST_PROXY).toEqual(
      value.split(', '),
    );
  });
});

describe('AUTH_FAILURES_PER_MINUTE', () => {
  it('holds back an address after 20 failed credential checks a minute by default', () => {
    expect(loadGatewayConfig(env).AUTH_FAILURES_PER_MINUTE).toBe(20);
    expect(
      loadGatewayConfig({ ...env, AUTH_FAILURES_PER_MINUTE: '5' }).AUTH_FAILURES_PER_MINUTE,
    ).toBe(5);
    expect(() => loadGatewayConfig({ ...env, AUTH_FAILURES_PER_MINUTE: '0' })).toThrow(
      'AUTH_FAILURES_PER_MINUTE:',
    );
  });
});
