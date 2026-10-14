import { createDecisionClient, createGatewayClient } from './gateway.ts';
import type { components as AgentComponents } from './generated/agent.ts';
import type { components as GatewayComponents } from './generated/gateway.ts';
import type * as GraphQL from './generated/graphql.ts';
import {
  type ActionApprovedV1,
  type ActionProposedV1,
  type Messages,
  type StepRecordedV1,
  type StepRecordedV2,
  type ToolCall,
  schemas,
} from './generated/messages.ts';
import { parseMessage } from './messages.ts';

// The same examples, read from the schema files, are validated by packages/py-clients/tests.
const examples = Object.entries(schemas).flatMap(([name, schema]) =>
  'examples' in schema
    ? schema.examples.map((example) => [name as keyof Messages, example] as const)
    : [],
);
const [stepV1] = schemas['step-recorded.v1'].examples;

type Rest = GatewayComponents['schemas'];
type Equal<A, B> = [A] extends [B] ? ([B] extends [A] ? true : false) : false;
// Enums copied across REST, messages and GraphQL; `make typecheck` fails here when one drifts.
const enumsAgree: [
  Equal<Rest['ActionKind'], ActionProposedV1['kind']>,
  Equal<Rest['ActionKind'], ActionApprovedV1['kind']>,
  Equal<Uppercase<Rest['ActionKind']>, GraphQL.ActionKind>,
  Equal<Uppercase<Rest['ActionStatus']>, GraphQL.ActionStatus>,
  Equal<Rest['StepKind'], StepRecordedV1['kind']>,
  Equal<Rest['StepKind'], StepRecordedV2['kind']>,
  Equal<Uppercase<Rest['StepKind']>, GraphQL.StepKind>,
  Equal<Rest['ToolCallStatus'], ToolCall['status']>,
  Equal<Uppercase<Rest['ToolCallStatus']>, GraphQL.ToolCallStatus>,
  Equal<Rest['InvestigationStatus'], AgentComponents['schemas']['InvestigationStatus']>,
  Equal<Uppercase<Rest['InvestigationStatus']>, GraphQL.InvestigationStatus>,
  Equal<Uppercase<Rest['RootCauseClass']>, GraphQL.RootCauseClass>,
] = [true, true, true, true, true, true, true, true, true, true, true, true];

describe('message contracts', () => {
  it.each(examples)('%s example validates', (name, example) => {
    expect(parseMessage(name, example)).toEqual(example);
  });

  it('accepts a v1 StepRecorded wherever StepRecorded is consumed', () => {
    expect(parseMessage('step-recorded', stepV1)).toEqual(stepV1);
  });

  it('rejects unknown versions and unknown fields', () => {
    expect(() => parseMessage('step-recorded', { ...stepV1, schema_version: 3 })).toThrow(
      /invalid step-recorded message/,
    );
    expect(() => parseMessage('step-recorded.v1', { ...stepV1, extra: true })).toThrow(
      /additional properties/,
    );
  });

  it.each(['action-proposed.v1', 'action-approved.v1'] as const)(
    '%s rejects action kinds outside the allowlist',
    (name) => {
      const [example] = schemas[name].examples;
      expect(() => parseMessage(name, { ...example, kind: 'delete_namespace' })).toThrow(
        /allowed values/,
      );
    },
  );

  it('keeps enums copied across contracts in sync (asserted by the type checker)', () => {
    expect(enumsAgree.every(Boolean)).toBe(true);
  });
});

describe('gateway clients', () => {
  afterEach(() => {
    jest.restoreAllMocks();
  });
  const sent = (spy: jest.SpiedFunction<typeof fetch>) => {
    const [request, init] = spy.mock.calls[0] ?? [];
    expect(init?.signal).toBeInstanceOf(AbortSignal);
    return request as Request;
  };

  it('sends the API key with a timeout and returns problem details', async () => {
    const problem = { title: 'Too many requests', status: 429 };
    const spy = jest
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(Response.json(problem, { status: 429 }));
    const client = createGatewayClient({ baseUrl: 'http://gateway.test', apiKey: 'key-1' });

    const { error } = await client.GET('/v1/investigations', { params: { query: { limit: 5 } } });

    expect(error).toEqual(problem);
    expect(sent(spy).headers.get('X-API-Key')).toBe('key-1');
  });

  it('sends only the approver key on decisions', async () => {
    const spy = jest.spyOn(globalThis, 'fetch').mockResolvedValue(Response.json({}));
    const client = createDecisionClient({ baseUrl: 'http://gateway.test', approverKey: 'appr-1' });

    await client.POST('/v1/actions/{id}/reject', {
      params: { path: { id: '9f8b6c1e-3d2a-4f5b-8e7d-6c5b4a392817' } },
      body: { approver: 'oncall@example.com' },
    });

    const request = sent(spy);
    expect(request.url).toBe(
      'http://gateway.test/v1/actions/9f8b6c1e-3d2a-4f5b-8e7d-6c5b4a392817/reject',
    );
    expect(request.headers.get('X-Approver-Key')).toBe('appr-1');
    expect(request.headers.get('X-API-Key')).toBeNull();
  });
});
