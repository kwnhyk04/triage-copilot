import { gatewayStatus } from './gateway.ts';

const config = {
  GATEWAY_URL: 'http://gateway.test',
  GATEWAY_API_KEY: 'gateway-test-key-0000000000000000',
};

describe('gatewayStatus', () => {
  afterEach(() => {
    jest.restoreAllMocks();
  });

  it('is ok when the gateway reports ok, and sends the API key', async () => {
    const spy = jest
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(Response.json({ data: { health: { status: 'ok' } } }));
    await expect(gatewayStatus(config)).resolves.toBe('ok');
    const init = spy.mock.calls[0]?.[1];
    expect(new Headers(init?.headers).get('X-API-Key')).toBe(config.GATEWAY_API_KEY);
  });

  it('is unreachable when the request fails', async () => {
    jest.spyOn(globalThis, 'fetch').mockRejectedValue(new TypeError('fetch failed'));
    await expect(gatewayStatus(config)).resolves.toBe('unreachable');
  });
});
