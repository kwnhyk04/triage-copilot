import { loadConfig, z } from '@triage/ts-config';

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
