import { gatewayStatus } from './gateway.ts';

describe('gatewayStatus', () => {
  afterEach(() => {
    jest.restoreAllMocks();
  });

  it('is ok when the gateway reports ok', async () => {
    jest
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(Response.json({ data: { health: { status: 'ok' } } }));
    await expect(gatewayStatus('http://gateway.test')).resolves.toBe('ok');
  });

  it('is unreachable when the request fails', async () => {
    jest.spyOn(globalThis, 'fetch').mockRejectedValue(new TypeError('fetch failed'));
    await expect(gatewayStatus('http://gateway.test')).resolves.toBe('unreachable');
  });
});
