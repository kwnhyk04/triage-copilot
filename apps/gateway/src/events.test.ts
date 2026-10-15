import { parseMessage } from '@triage/ts-clients/messages';
import postgres, { type Sql } from 'postgres';

import { InvalidEvent, permanent, stepRow, storeEvent, toolOutcome } from './events.ts';

const step = {
  investigation_id: '1b4e28ba-2fa1-41d2-883f-0016d3cca427',
  seq: 3,
  kind: 'tool_call',
  summary: 'query_metrics on checkout',
  recorded_at: '2026-10-14T12:00:41Z',
};
const v1 = parseMessage('step-recorded', {
  ...step,
  schema_version: 1,
  tool_name: 'query_metrics',
  tool_call_id: 'tc_01J9ZK3Q7M',
});
const v2 = parseMessage('step-recorded', {
  ...step,
  schema_version: 2,
  tool_call: { id: 'tc_01J9ZK4B2X', name: 'search_logs', status: 'timeout', duration_ms: 10000 },
  usage: { input_tokens: 5120, output_tokens: 310 },
});
const v3Result = parseMessage('step-recorded', {
  ...step,
  schema_version: 3,
  tool_name: 'query_metrics',
  tool_call_id: 'tc_01J9ZK3Q7M',
  result: 'Error rate rose from 0.2% to 14% at 11:52.',
  latency_ms: null,
  tokens: null,
});

describe('agent events', () => {
  it('maps every StepRecorded version to a mirror row', () => {
    expect(stepRow(v1)).toMatchObject({
      tool_name: 'query_metrics',
      tool_call_id: 'tc_01J9ZK3Q7M',
      result: null,
      tokens_input: null,
      tool_status: null,
    });
    // v2 nests the call's outcome and usage.
    expect(stepRow(v2)).toMatchObject({
      tool_name: 'search_logs',
      tool_call_id: 'tc_01J9ZK4B2X',
      tool_status: 'timeout',
      duration_ms: 10000,
      tokens_input: 5120,
      tokens_output: 310,
    });
    // A v3 result step carries its call's outcome; a v1 step without tokens is no result.
    expect(stepRow(v3Result)).toMatchObject({
      tool_name: 'query_metrics',
      result: 'Error rate rose from 0.2% to 14% at 11:52.',
      latency_ms: null,
      tokens_input: null,
      tool_status: 'ok',
      duration_ms: null,
    });
    const v3Request = { ...v3Result, result: null, tokens: { input: 5120, output: 310 } };
    expect(stepRow(parseMessage('step-recorded', v3Request))).toMatchObject({
      tool_status: null,
      duration_ms: null,
    });
  });

  it('tells failures no retry fixes from transient ones', async () => {
    // Every invalid message fails before the database, which these never reach.
    const unused = undefined as unknown as Sql;
    for (const [type, body] of [
      ['step-recorded', 'not json'],
      ['step-recorded', JSON.stringify({ ...step, schema_version: 3 })],
      ['report-recorded', '{}'],
      ['step-exploded', JSON.stringify(v3Result)],
      [undefined, JSON.stringify(v3Result)],
    ] as const) {
      await expect(storeEvent(unused, type, body)).rejects.toBeInstanceOf(InvalidEvent);
    }
    expect(permanent(new InvalidEvent('unknown message_type "step-exploded"'))).toBe(true);
    // Postgres errors by SQLSTATE class: 22 data, 23 constraint, 42 SQL; the rest may pass.
    for (const [code, expected] of [
      ['22003', true],
      ['23503', true],
      ['42P01', true],
      ['57014', false],
      ['40001', false],
      ['55P03', false],
      ['08006', false],
    ] as const) {
      const error = Object.assign(new postgres.PostgresError(''), { code, message: code });
      expect([code, permanent(error)]).toEqual([code, expected]);
    }
    const lost = Object.assign(new Error('write CONNECTION_CLOSED'), { code: 'CONNECTION_CLOSED' });
    expect(permanent(lost)).toBe(false);
    expect(permanent(new SyntaxError('not from a message'))).toBe(false);
  });

  it('reads a tool call outcome from its result step only', () => {
    const result = {
      kind: 'tool_call' as const,
      tool_call_id: 'tc_01J9ZK3Q7M',
      tokens_input: null,
      latency_ms: 812,
    };
    expect(toolOutcome({ ...result, result: '42 rows', summary: 'query_logs returned' })).toEqual({
      status: 'ok',
      duration_ms: 812,
    });
    // The agent's summary for a failed call: "<tool> failed: <ErrorType>: <message>".
    for (const [summary, status] of [
      ['query_logs failed: TimeoutError: ', 'timeout'],
      ['query_logs failed: ReadTimeout: timed out', 'timeout'],
      ['query_metrics failed: HTTPStatusError: 503 Service Unavailable', 'error'],
      ['frobnicate failed: unknown tool frobnicate', 'error'],
    ] as const) {
      expect(toolOutcome({ ...result, result: null, summary })).toEqual({
        status,
        duration_ms: 812,
      });
    }
    // The model's request carries tokens; other kinds call no tool.
    const request = { ...result, tokens_input: 5120, result: null, summary: 'Check the logs' };
    expect(toolOutcome(request)).toBeUndefined();
    expect(
      toolOutcome({ ...result, kind: 'report', result: null, summary: 'done' }),
    ).toBeUndefined();
    expect(
      toolOutcome({ ...result, tool_call_id: null, result: null, summary: 'x' }),
    ).toBeUndefined();
  });
});
