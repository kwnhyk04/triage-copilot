// Shared by the web tests only; no page imports it.
import type { Investigation } from './investigations.ts';

/** A live triage as the gateway serves it: Claude was switched off, so Gemini answered. */
export const LIVE_TRIAGE: Investigation = {
  id: '3d6f9a2c-1b4e-4c8d-9f7a-5e2b1c0d4a68',
  group_id: '1b4e28ba-2fa1-41d2-883f-0016d3cca427',
  title: 'ApiReadinessFailing on api',
  status: 'resolved',
  verdict: 'real',
  created_at: '2026-10-25T09:52:30.000Z',
  updated_at: '2026-10-25T09:54:10.000Z',
  summary: "freight-audit's api fails its readiness check: postgres restarts.",
  tokens_used: 18_460,
  hypotheses: [
    {
      rank: 1,
      root_cause_class: 'database',
      service: 'postgres',
      summary: 'The readiness check fails on postgres, whose container restarts.',
      confidence: 0.8,
      evidence: [{ tool_call_id: 'tc_01JA0SNAP0', excerpt: 'postgres: restarting (exit 137)' }],
    },
    {
      rank: 2,
      root_cause_class: 'dependency_failure',
      service: 'api',
      summary: 'A dependency of the api may be down.',
      confidence: 0.3,
      evidence: [{ tool_call_id: 'tc_01JA0DEPS0', excerpt: '' }],
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
      calls: 3,
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
  ],
  proposed_actions: [],
  steps: [],
};
