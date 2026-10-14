import { agentStates, type State } from './sweeper.ts';

const ID = '1b4e28ba-2fa1-41d2-883f-0016d3cca427';
const TOKEN = 'service-token-for-these-tests-0000';

describe('agentStates', () => {
  afterEach(() => {
    jest.restoreAllMocks();
  });

  it('asks with the service token, and reads 404 as never received', async () => {
    const state: State = {
      investigation_id: ID,
      status: 'queued',
      cancel_requested: false,
      steps_taken: 0,
      tool_calls: 0,
      tokens_used: 0,
      updated_at: '2026-10-15T12:00:00Z',
      waiting_for: 'tenant_cap',
      waiting_since: '2026-10-15T12:00:00Z',
      resumable: true,
    };
    const spy = jest
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(Response.json(state))
      .mockResolvedValueOnce(Response.json({ title: 'Not Found', status: 404 }, { status: 404 }));
    const getState = agentStates('http://agent.test', TOKEN);

    expect(await getState(ID)).toEqual(state);
    expect(await getState(ID)).toBeUndefined();
    const request = spy.mock.calls[0]?.[0] as Request;
    expect(request.url).toBe(`http://agent.test/internal/investigations/${ID}/state`);
    expect(request.headers.get('Authorization')).toBe(`Bearer ${TOKEN}`);
  });

  it('throws on any other answer, so an agent outage never looks like a lost message', async () => {
    const problem = { title: 'Unauthorized', status: 401 };
    jest.spyOn(globalThis, 'fetch').mockResolvedValue(Response.json(problem, { status: 401 }));

    await expect(agentStates('http://agent.test', TOKEN)(ID)).rejects.toThrow('HTTP 401');
  });
});
