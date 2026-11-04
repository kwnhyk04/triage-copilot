import { GatewayOffline, getInvestigation, listInvestigations } from './investigations.ts';
import { WORKER_OUT_OF_MEMORY as LIVE_TRIAGE } from '../demo/live-samples.ts';

const config = {
  GATEWAY_URL: 'http://gateway.test',
  GATEWAY_API_KEY: 'gateway-test-key-0000000000000000',
};
const PAGE = { items: [], next_cursor: null, total: 0 };
const FIRST = { source: 'live', offset: 0, limit: 20 } as const;

describe('investigations through the gateway', () => {
  afterEach(() => {
    jest.restoreAllMocks();
  });

  it('lists a numbered page of one source with the API key: its offset and size', async () => {
    const spy = jest.spyOn(globalThis, 'fetch').mockResolvedValue(Response.json(PAGE));
    await expect(
      listInvestigations(config, { source: 'live', offset: 40, limit: 20 }),
    ).resolves.toEqual(PAGE);
    const request = spy.mock.calls[0]?.[0] as Request;
    const url = new URL(request.url);
    expect([
      url.pathname,
      ...['source', 'offset', 'limit'].map((n) => url.searchParams.get(n)),
    ]).toEqual(['/v1/investigations', 'live', '40', '20']);
    expect(request.headers.get('X-API-Key')).toBe(config.GATEWAY_API_KEY);
  });

  it('reads one investigation, and nothing for an id the gateway does not know', async () => {
    const spy = jest
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(Response.json(LIVE_TRIAGE))
      .mockResolvedValueOnce(Response.json({ title: 'Not Found', status: 404 }, { status: 404 }));
    await expect(getInvestigation(config, LIVE_TRIAGE.id)).resolves.toEqual(LIVE_TRIAGE);
    const request = spy.mock.calls[0]?.[0] as Request;
    expect(new URL(request.url).pathname).toBe(`/v1/investigations/${LIVE_TRIAGE.id}`);
    expect(request.headers.get('X-API-Key')).toBe(config.GATEWAY_API_KEY);
    await expect(getInvestigation(config, LIVE_TRIAGE.group_id)).resolves.toBeUndefined();
  });

  it('never sends the gateway an id that is no uuid', async () => {
    const spy = jest.spyOn(globalThis, 'fetch');
    await expect(getInvestigation(config, '..%2Factions')).resolves.toBeUndefined();
    expect(spy).not.toHaveBeenCalled();
  });

  it('fails as offline when the gateway does not answer, and plainly on any other error', async () => {
    const answer = (status: number) => () =>
      Promise.resolve(Response.json({ title: 'x', status }, { status }));
    const spy = jest.spyOn(globalThis, 'fetch');
    for (const offline of [
      () => Promise.reject(new TypeError('fetch failed')),
      () => Promise.reject(new DOMException('The operation timed out.', 'TimeoutError')),
      answer(502),
      answer(503),
      answer(504),
    ]) {
      spy.mockImplementation(offline);
      await expect(listInvestigations(config, FIRST)).rejects.toBeInstanceOf(GatewayOffline);
      await expect(getInvestigation(config, LIVE_TRIAGE.id)).rejects.toBeInstanceOf(GatewayOffline);
    }
    spy.mockImplementation(answer(500));
    const failed = listInvestigations(config, FIRST);
    await expect(failed).rejects.toThrow('gateway answered 500');
    await expect(failed).rejects.not.toBeInstanceOf(GatewayOffline);
  });
});
