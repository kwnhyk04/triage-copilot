// Three hand-written live triages for the public demo, with service names from TC's catalog of
// FreightAudit and FilingLens (services/watch/src/watch/catalog.py). None is a real incident: each
// page labels them illustrative. They are also the web's unit-test fixtures.
import type { Investigation } from '../lib/investigations.ts';

const SNAPSHOT = [
  'containers (freightaudit):',
  '  api       running   healthy',
  '  worker    exited (137) 2 minutes ago, restarting',
  '  postgres  running   healthy',
  '  redis     running   healthy',
  'memory_usage_ratio{service="worker"} 0.98 at 09:50 UTC',
].join('\n');

const ANSWER_LOGS = Array.from(
  { length: 18 },
  (_, i) =>
    `14:0${String(Math.floor(i / 6) + 6)}:${String(10 + i * 3)}Z api ERROR answer failed: ` +
    `setting ANSWER_MODEL is empty (request ${String(4810 + i)})`,
).join('\n');

/** FreightAudit's worker, killed out of memory: a real incident, its restart plan waiting. */
const WORKER_OUT_OF_MEMORY = {
  id: '7c2e4a10-5b3d-4f6e-8a9b-1c2d3e4f5a60',
  group_id: '0e1f2a3b-4c5d-4e6f-8a7b-9c0d1e2f3a41',
  title: 'ContainerDown on worker',
  status: 'resolved',
  verdict: 'real',
  created_at: '2026-10-26T09:52:30.000Z',
  updated_at: '2026-10-26T09:54:10.000Z',
  source: 'live',
  cause: { root_cause_class: 'resource_exhaustion', service: 'worker', confidence: 0.8 },
  plan_status: 'pending',
  summary:
    "FreightAudit's worker exits with code 137 once its memory reaches the limit; the api and " +
    'its stores stay healthy.',
  tokens_used: 21_380,
  hypotheses: [
    {
      rank: 1,
      root_cause_class: 'resource_exhaustion',
      service: 'worker',
      summary: 'The worker runs out of memory on large invoices and is killed with code 137.',
      confidence: 0.8,
      evidence: [{ tool_call_id: 'tc_01JB2SNAP01', excerpt: SNAPSHOT }],
    },
    {
      rank: 2,
      root_cause_class: 'dependency_failure',
      service: 'redis',
      summary: 'A queue outage could stop the worker, but redis answers its health check.',
      confidence: 0.15,
      evidence: [{ tool_call_id: 'tc_01JB2DEPS02', excerpt: '' }],
    },
  ],
  missing_evidence: ['get_dependencies failed: it timed out.'],
  partial: false,
  models: [
    {
      part: 'triage',
      provider: 'anthropic',
      model: 'claude-haiku-5-5',
      calls: 0,
      failed: 1,
      reason: 'claude-haiku-5-5 skipped: Claude is switched off (agent.switches)',
    },
    {
      part: 'triage',
      provider: 'gemini',
      model: 'gemini-3.5-flash-lite',
      calls: 4,
      failed: 0,
      reason: null,
    },
    {
      part: 'summary',
      provider: 'gemini',
      model: 'gemini-3.1-flash-lite',
      calls: 1,
      failed: 0,
      reason: null,
    },
    {
      part: 'plan',
      provider: 'anthropic',
      model: 'claude-opus-5-5',
      calls: 1,
      failed: 0,
      reason: null,
    },
  ],
  proposed_actions: [],
  plan: {
    id: '5b1c9d2e-7f3a-4e8b-9c6d-1a2b3c4d5e6f',
    proposal: {
      kind: 'restart_service',
      target: 'worker',
      files: [],
      rationale:
        'The worker exited with 137 while its dependencies stay healthy [tc_01JB2SNAP01]. A ' +
        'restart frees its memory while a person looks at the large invoices.',
    },
    problem: null,
    status: 'pending',
    recorded_at: '2026-10-26T09:55:20.000Z',
    decided_by: null,
    decided_at: null,
    decision_reason: null,
  },
  steps: [
    {
      seq: 1,
      kind: 'tool_call',
      summary: 'Read the snapshot of alert 1.',
      tool_call: { id: 'tc_01JB2SNAP01', name: 'read_snapshot', status: 'ok', duration_ms: 12 },
      recorded_at: '2026-10-26T09:52:41.000Z',
    },
    {
      seq: 2,
      kind: 'tool_call',
      summary: 'read_snapshot returned',
      tool_call: { id: 'tc_01JB2SNAP01', name: 'read_snapshot', status: 'ok', duration_ms: 12 },
      recorded_at: '2026-10-26T09:52:42.000Z',
    },
    {
      seq: 3,
      kind: 'tool_call',
      summary: 'What the worker needs.',
      tool_call: {
        id: 'tc_01JB2DEPS02',
        name: 'get_dependencies',
        status: 'timeout',
        duration_ms: 10_000,
      },
      recorded_at: '2026-10-26T09:53:05.000Z',
    },
    {
      seq: 4,
      kind: 'tool_call',
      summary: 'get_dependencies failed: TimeoutError',
      tool_call: {
        id: 'tc_01JB2DEPS02',
        name: 'get_dependencies',
        status: 'timeout',
        duration_ms: 10_000,
      },
      recorded_at: '2026-10-26T09:53:15.000Z',
    },
    {
      seq: 5,
      kind: 'report',
      summary: 'Report: a real incident, the worker out of memory.',
      tool_call: null,
      recorded_at: '2026-10-26T09:54:10.000Z',
    },
  ],
} satisfies Investigation;

/** FilingLens's api after a renamed setting: a real incident, its code fix approved. */
const ANSWERS_AFTER_RENAME = {
  id: '2f8d6b4a-9c1e-4d3f-a5b7-c9e1f3a5b7d9',
  group_id: '6a8c0e2f-4b6d-4f8a-9c1e-3a5c7e9f1b2d',
  title: 'AnswersFailing on api',
  status: 'resolved',
  verdict: 'real',
  created_at: '2026-10-25T14:10:05.000Z',
  updated_at: '2026-10-25T14:12:40.000Z',
  source: 'live',
  cause: { root_cause_class: 'bad_deploy', service: 'api', confidence: 0.75 },
  plan_status: 'approved',
  summary:
    "FilingLens's api fails every answer since the change that renamed a setting: the old " +
    'name now reads as empty.',
  tokens_used: 33_910,
  hypotheses: [
    {
      rank: 1,
      root_cause_class: 'bad_deploy',
      service: 'api',
      summary: 'Answers began failing right after the change that renamed ANSWER_MODEL.',
      confidence: 0.75,
      evidence: [
        { tool_call_id: 'tc_01JB1LOGS01', excerpt: ANSWER_LOGS },
        {
          tool_call_id: 'tc_01JB1GIT02',
          excerpt: 'a1b2c3d Rename the answer model setting (2 hours before the alert)',
        },
      ],
    },
  ],
  missing_evidence: [],
  partial: false,
  models: [
    {
      part: 'triage',
      provider: 'anthropic',
      model: 'claude-haiku-5-5',
      calls: 5,
      failed: 0,
      reason: null,
    },
    {
      part: 'summary',
      provider: 'gemini',
      model: 'gemini-3.1-flash-lite',
      calls: 2,
      failed: 0,
      reason: null,
    },
    {
      part: 'plan',
      provider: 'anthropic',
      model: 'claude-opus-5-5',
      calls: 1,
      failed: 0,
      reason: null,
    },
  ],
  proposed_actions: [],
  plan: {
    id: '8e0a2c4e-6f8a-4c0e-9a2c-4e6a8c0e2a4c',
    proposal: {
      kind: 'code_fix',
      target: 'api',
      files: ['services/api/settings.py', 'services/api/answers.py'],
      rationale: 'Read the setting under its new name in both files [tc_01JB1GIT02].',
    },
    problem: null,
    status: 'approved',
    recorded_at: '2026-10-25T14:13:05.000Z',
    decided_by: 'operator',
    decided_at: '2026-10-25T14:20:00.000Z',
    decision_reason: null,
  },
  steps: [
    {
      seq: 1,
      kind: 'tool_call',
      summary: 'Query the api logs of the last 30 minutes.',
      tool_call: { id: 'tc_01JB1LOGS01', name: 'query_logs', status: 'ok', duration_ms: 240 },
      recorded_at: '2026-10-25T14:10:20.000Z',
    },
    {
      seq: 2,
      kind: 'tool_call',
      summary: 'query_logs returned 18 lines',
      tool_call: { id: 'tc_01JB1LOGS01', name: 'query_logs', status: 'ok', duration_ms: 240 },
      recorded_at: '2026-10-25T14:10:21.000Z',
    },
    {
      seq: 3,
      kind: 'tool_call',
      summary: 'List the recent changes.',
      tool_call: { id: 'tc_01JB1GIT02', name: 'get_deploys', status: 'ok', duration_ms: 35 },
      recorded_at: '2026-10-25T14:11:02.000Z',
    },
    {
      seq: 4,
      kind: 'tool_call',
      summary: 'get_deploys returned 3 changes',
      tool_call: { id: 'tc_01JB1GIT02', name: 'get_deploys', status: 'ok', duration_ms: 35 },
      recorded_at: '2026-10-25T14:11:03.000Z',
    },
    {
      seq: 5,
      kind: 'report',
      summary: 'Report: a real incident, the renamed setting.',
      tool_call: null,
      recorded_at: '2026-10-25T14:12:40.000Z',
    },
  ],
} satisfies Investigation;

/** FreightAudit's api with a short error burst from one client: not a real incident. */
const ONE_CLIENT_RETRYING = {
  id: '9a7b5c3d-1e2f-4a6b-8c0d-2e4f6a8b0c1d',
  group_id: '3c5e7a9b-1d3f-4b5d-8e7f-9a1b3c5d7e9f',
  title: 'ErrorBurst on api',
  status: 'resolved',
  verdict: 'not_real',
  created_at: '2026-10-24T22:41:15.000Z',
  updated_at: '2026-10-24T22:42:30.000Z',
  source: 'live',
  cause: { root_cause_class: 'unknown', service: 'api', confidence: 0.6 },
  plan_status: null,
  summary:
    'One client retried a malformed upload ten times; every other request to the api succeeded.',
  tokens_used: 9_870,
  hypotheses: [
    {
      rank: 1,
      root_cause_class: 'unknown',
      service: 'api',
      summary: 'The ten errors share one client and one bad upload; nothing else failed.',
      confidence: 0.6,
      evidence: [
        {
          tool_call_id: 'tc_01JB0LOGS01',
          excerpt: '22:40:02Z api WARN upload refused: invalid PDF (client 7, attempt 1 of 10)',
        },
      ],
    },
  ],
  missing_evidence: [],
  partial: false,
  models: [
    {
      part: 'triage',
      provider: 'gemini',
      model: 'gemini-3.5-flash-lite',
      calls: 2,
      failed: 0,
      reason: null,
    },
  ],
  proposed_actions: [],
  plan: null,
  steps: [
    {
      seq: 1,
      kind: 'tool_call',
      summary: 'Query the api logs around the burst.',
      tool_call: { id: 'tc_01JB0LOGS01', name: 'query_logs', status: 'ok', duration_ms: 180 },
      recorded_at: '2026-10-24T22:41:30.000Z',
    },
    {
      seq: 2,
      kind: 'report',
      summary: 'Report: not a real incident.',
      tool_call: null,
      recorded_at: '2026-10-24T22:42:30.000Z',
    },
  ],
} satisfies Investigation;

export const LIVE_SAMPLES: Investigation[] = [
  WORKER_OUT_OF_MEMORY,
  ANSWERS_AFTER_RENAME,
  ONE_CLIENT_RETRYING,
];
export { ANSWERS_AFTER_RENAME, ONE_CLIENT_RETRYING, WORKER_OUT_OF_MEMORY };
