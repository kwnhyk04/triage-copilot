import { createDecisionClient, createGatewayClient } from './gateway.ts';
import type { components as AgentComponents } from './generated/agent.ts';
import type { components as GatewayComponents } from './generated/gateway.ts';
import type * as GraphQL from './generated/graphql.ts';
import {
  type ActionApprovedV1,
  type ActionProposedV1,
  type Messages,
  type ReportRecordedV1,
  type ReportRecordedV2,
  type StepRecordedV1,
  type StepRecordedV2,
  type StepRecordedV3,
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
const [stepV3] = schemas['step-recorded.v3'].examples;
const [reportV1] = schemas['report-recorded.v1'].examples;
if (reportV1 === undefined) throw new Error('report-recorded.v1 has no example');
const [reportV2] = schemas['report-recorded.v2'].examples;
if (reportV2 === undefined) throw new Error('report-recorded.v2 has no example');

type Rest = GatewayComponents['schemas'];
type Reported = ReportRecordedV1['report'];
type ModelUse = ReportRecordedV2['models'][number];
type Equal<A, B> = [A] extends [B] ? ([B] extends [A] ? true : false) : false;
// Enums copied across REST, messages and GraphQL; `make typecheck` fails here when one drifts.
const enumsAgree: [
  Equal<Rest['ActionKind'], ActionProposedV1['kind']>,
  Equal<Rest['ActionKind'], ActionApprovedV1['kind']>,
  Equal<Uppercase<Rest['ActionKind']>, GraphQL.ActionKind>,
  Equal<Uppercase<Rest['ActionStatus']>, GraphQL.ActionStatus>,
  Equal<Rest['StepKind'], StepRecordedV1['kind']>,
  Equal<Rest['StepKind'], StepRecordedV2['kind']>,
  Equal<Rest['StepKind'], StepRecordedV3['kind']>,
  Equal<Uppercase<Rest['StepKind']>, GraphQL.StepKind>,
  Equal<Rest['ToolCallStatus'], ToolCall['status']>,
  Equal<Uppercase<Rest['ToolCallStatus']>, GraphQL.ToolCallStatus>,
  Equal<Rest['InvestigationStatus'], AgentComponents['schemas']['InvestigationStatus']>,
  Equal<Uppercase<Rest['InvestigationStatus']>, GraphQL.InvestigationStatus>,
  Equal<Uppercase<Rest['RootCauseClass']>, GraphQL.RootCauseClass>,
  Equal<Rest['RootCauseClass'], Reported['hypotheses'][number]['root_cause_class']>,
  Equal<Rest['ActionKind'], Reported['proposed_actions'][number]['kind']>,
  Equal<ReportRecordedV2['report'], Reported>,
  Equal<Uppercase<NonNullable<ReportRecordedV2['verdict']>>, GraphQL.Verdict>,
  Equal<Uppercase<ModelUse['part']>, GraphQL.ModelPart>,
] = [
  true,
  true,
  true,
  true,
  true,
  true,
  true,
  true,
  true,
  true,
  true,
  true,
  true,
  true,
  true,
  true,
  true,
  true,
];

describe('message contracts', () => {
  it.each(examples)('%s example validates', (name, example) => {
    expect(parseMessage(name, example)).toEqual(example);
  });

  it.each(['step-recorded.v1', 'step-recorded.v2', 'step-recorded.v3'] as const)(
    'accepts %s wherever StepRecorded is consumed',
    (name) => {
      for (const example of schemas[name].examples) {
        expect(parseMessage('step-recorded', example)).toEqual(example);
      }
    },
  );

  // The same cases run against the Python models in services/agent/tests/test_steps.py.
  it.each([
    ['an empty summary', { summary: '' }, false],
    ['a 1-character summary', { summary: 'x' }, true],
    ['a 2,000-character summary', { summary: 'x'.repeat(2000) }, true],
    ['a summary of 2,000 code points', { summary: '\u{1F600}'.repeat(2000) }, true],
    ['a 2,001-character summary', { summary: 'x'.repeat(2001) }, false],
    ['zero latency', { latency_ms: 0 }, true],
    ['negative latency', { latency_ms: -1 }, false],
    ['zero tokens', { tokens: { input: 0, output: 0 } }, true],
    ['negative input tokens', { tokens: { input: -1, output: 0 } }, false],
    ['negative output tokens', { tokens: { input: 0, output: -1 } }, false],
  ] as const)('StepRecorded v3 limits: %s', (_, change, allowed) => {
    const message = { ...stepV3, ...change };
    if (allowed) {
      expect(parseMessage('step-recorded.v3', message)).toEqual(message);
    } else {
      expect(() => parseMessage('step-recorded.v3', message)).toThrow(/invalid step-recorded.v3/);
    }
  });

  it('marks only StepRecorded v2 deprecated', () => {
    const deprecated = schemas['step-recorded'].oneOf.map((branch) => 'deprecated' in branch);
    expect(deprecated).toEqual([false, true, false]);
  });

  it('accepts InvestigationRequested v1 and v2, marks v1 deprecated, and needs a v2 tenant', () => {
    for (const name of ['investigation-requested.v1', 'investigation-requested.v2'] as const) {
      for (const example of schemas[name].examples) {
        expect(parseMessage('investigation-requested', example)).toEqual(example);
      }
    }
    const deprecated = schemas['investigation-requested'].oneOf.map((b) => 'deprecated' in b);
    expect(deprecated).toEqual([true, false]);
    const noTenant: Record<string, unknown> = {
      ...schemas['investigation-requested.v2'].examples[0],
    };
    delete noTenant.tenant_id;
    expect(() => parseMessage('investigation-requested.v2', noTenant)).toThrow(/tenant_id/);
  });

  it('rejects unknown versions and unknown fields', () => {
    expect(() => parseMessage('step-recorded', { ...stepV1, schema_version: 4 })).toThrow(
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

  it('marks ActionProposed v1 unused, since proposals ride in ReportRecorded v1', () => {
    const [branch] = schemas['action-proposed'].oneOf;
    expect(branch?.deprecated).toBe(true);
    expect(branch?.description).toMatch(/Unused/);
  });

  // The same cases run against the Python models in packages/py-clients/tests/test_contracts.py.
  it.each([
    ['an evidence id that names no tool result', { evidence_ids: ['step-4'] }],
    ['a confidence over 1', { confidence: 1.5 }],
    ['an unknown root-cause class', { root_cause_class: 'cosmic_rays' }],
  ])('ReportRecorded v1 rejects %s', (_, change) => {
    const [hypothesis, ...rest] = reportV1.report.hypotheses;
    const report = { ...reportV1.report, hypotheses: [{ ...hypothesis, ...change }, ...rest] };
    expect(() => parseMessage('report-recorded.v1', { ...reportV1, report })).toThrow(
      /invalid report-recorded.v1/,
    );
  });

  it('ReportRecorded v1 rejects proposed actions outside the allowlist or without evidence', () => {
    const [action] = reportV1.report.proposed_actions;
    for (const change of [{ kind: 'delete_namespace' }, { evidence_ids: [] }]) {
      const report = { ...reportV1.report, proposed_actions: [{ ...action, ...change }] };
      expect(() => parseMessage('report-recorded.v1', { ...reportV1, report })).toThrow(
        /invalid report-recorded.v1/,
      );
    }
  });

  it('accepts ReportRecorded v1 and v2 wherever reports are consumed, and marks v1 deprecated', () => {
    for (const name of ['report-recorded.v1', 'report-recorded.v2'] as const) {
      for (const example of schemas[name].examples) {
        expect(parseMessage('report-recorded', example)).toEqual(example);
      }
    }
    const deprecated = schemas['report-recorded'].oneOf.map((branch) => 'deprecated' in branch);
    expect(deprecated).toEqual([true, false]);
  });

  // The same cases run against the Python models in packages/py-clients/tests/test_contracts.py.
  const [model] = reportV2.models;
  it.each([
    ['an unknown verdict', { verdict: 'maybe' }],
    ['an unknown part', { models: [{ ...model, part: 'plan' }] }],
    ['a negative call count', { models: [{ ...model, calls: -1 }] }],
    ['a reason over 500 characters', { models: [{ ...model, reason: 'x'.repeat(501) }] }],
  ])('ReportRecorded v2 rejects %s', (_, change) => {
    expect(() => parseMessage('report-recorded.v2', { ...reportV2, ...change })).toThrow(
      /invalid report-recorded.v2/,
    );
  });

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
