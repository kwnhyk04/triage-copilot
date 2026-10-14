import { decideAction } from './decisions.ts';

const config = {
  GATEWAY_URL: 'http://gateway.test',
  APPROVER_API_KEY: 'approver-test-key-000000000000000',
};

describe('decideAction', () => {
  afterEach(() => {
    jest.restoreAllMocks();
  });

  it('posts the decision with the approver key only', async () => {
    const spy = jest
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(Response.json({ status: 'approved' }));

    const { data } = await decideAction(config, '9f8b6c1e-3d2a-4f5b-8e7d-6c5b4a392817', 'approve', {
      approver: 'oncall@example.com',
    });

    expect(data).toEqual({ status: 'approved' });
    const request = spy.mock.calls[0]?.[0] as Request;
    expect(request.url).toMatch(/\/v1\/actions\/9f8b6c1e-3d2a-4f5b-8e7d-6c5b4a392817\/approve$/);
    expect(request.headers.get('X-Approver-Key')).toBe(config.APPROVER_API_KEY);
    expect(request.headers.get('X-API-Key')).toBeNull();
  });
});
