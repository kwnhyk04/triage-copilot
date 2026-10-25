import { getInvestigation, listInvestigations } from './investigations.ts';
import { LIVE_TRIAGE } from './testing.ts';

const config = {
  GATEWAY_URL: 'http://gateway.test',
  GATEWAY_API_KEY: 'gateway-test-key-0000000000000000',
};
const PAGE = { items: [], next_cursor: null };

describe('investigations through the gateway', () => {
  afterEach(() => {
    jest.restoreAllMocks();
  });

  it('lists a page with the API key, passing the cursor on', async () => {
    const spy = jest.spyOn(globalThis, 'fetch').mockResolvedValue(Response.json(PAGE));
    await expect(listInvestigations(config, 'abc')).resolves.toEqual(PAGE);
    const request = spy.mock.calls[0]?.[0] as Request;
    const url = new URL(request.url);
    expect([url.pathname, url.searchParams.get('cursor')]).toEqual(['/v1/investigations', 'abc']);
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

  it('fails when the gateway cannot answer, so the page shows an error instead of nothing', async () => {
    const down = Response.json({ title: 'Service Unavailable', status: 503 }, { status: 503 });
    jest.spyOn(globalThis, 'fetch').mockResolvedValue(down);
    await expect(listInvestigations(config)).rejects.toThrow('gateway answered 503');
  });
});
