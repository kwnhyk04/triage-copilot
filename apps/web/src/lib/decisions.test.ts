import { decideAction, decidePlan, decisionFrom } from './decisions.ts';

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

  it('posts a plan decision with the approver key only', async () => {
    const spy = jest
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(Response.json({ status: 'rejected' }));

    const { data } = await decidePlan(config, '5b1c9d2e-7f3a-4e8b-9c6d-1a2b3c4d5e6f', 'reject', {
      approver: 'oncall@example.com',
      reason: 'The worker is fine; the queue is the problem.',
    });

    expect(data).toEqual({ status: 'rejected' });
    const request = spy.mock.calls[0]?.[0] as Request;
    expect(request.url).toMatch(/\/v1\/plans\/5b1c9d2e-7f3a-4e8b-9c6d-1a2b3c4d5e6f\/reject$/);
    expect(request.headers.get('X-Approver-Key')).toBe(config.APPROVER_API_KEY);
    expect(request.headers.get('X-API-Key')).toBeNull();
  });
});

describe('decisionFrom', () => {
  const form = (fields: Record<string, string>) => {
    const data = new FormData();
    for (const [name, value] of Object.entries(fields)) data.set(name, value);
    return data;
  };

  it('reads the verdict, the approver and an optional reason from the form', () => {
    expect(decisionFrom(form({ verdict: 'approve', approver: ' Ana ', reason: '' }))).toEqual({
      verdict: 'approve',
      decision: { approver: 'Ana' },
    });
    expect(decisionFrom(form({ verdict: 'reject', approver: 'Ana', reason: 'Too risky' }))).toEqual(
      { verdict: 'reject', decision: { approver: 'Ana', reason: 'Too risky' } },
    );
  });

  it('refuses a form without an approver or with another verdict', () => {
    expect(decisionFrom(form({ verdict: 'approve', approver: '  ' }))).toBeUndefined();
    expect(decisionFrom(form({ verdict: 'execute', approver: 'Ana' }))).toBeUndefined();
    expect(decisionFrom(form({ verdict: 'approve', approver: 'x'.repeat(201) }))).toBeUndefined();
  });
});
