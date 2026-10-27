import { type components, createDecisionClient } from '@triage/ts-clients/gateway';
import { z } from '@triage/ts-config';

import type { WebConfig } from './config.ts';
import { GATEWAY_TIMEOUT_MS } from './investigations.ts';

type Approver = Pick<WebConfig, 'GATEWAY_URL' | 'APPROVER_API_KEY'>;
type Decision = components['schemas']['Decision'];
type Verdict = 'approve' | 'reject';

const client = (config: Approver) =>
  createDecisionClient({
    baseUrl: config.GATEWAY_URL,
    approverKey: config.APPROVER_API_KEY,
    timeoutMs: GATEWAY_TIMEOUT_MS,
  });

/**
 * Records a human approval or rejection at the gateway. Server code only: it sends the approver
 * key, which no other service holds.
 */
export function decideAction(
  config: Approver,
  actionId: string,
  verdict: Verdict,
  decision: Decision,
) {
  const init = { params: { path: { id: actionId } }, body: decision };
  return verdict === 'approve'
    ? client(config).POST('/v1/actions/{id}/approve', init)
    : client(config).POST('/v1/actions/{id}/reject', init);
}

/** Records a human's approval or rejection of a plan; nothing runs from it. Server code only. */
export function decidePlan(config: Approver, planId: string, verdict: Verdict, decision: Decision) {
  const init = { params: { path: { id: planId } }, body: decision };
  return verdict === 'approve'
    ? client(config).POST('/v1/plans/{id}/approve', init)
    : client(config).POST('/v1/plans/{id}/reject', init);
}

const decisionForm = z.object({
  verdict: z.enum(['approve', 'reject']),
  reason: z.string().trim().max(2000).optional(),
});

/**
 * The verdict and decision a submitted form holds, made by `approver` (the signed-in user, never
 * a name the form gives), or undefined when the form holds none.
 */
export function decisionFrom(
  form: FormData,
  approver: string,
): { verdict: Verdict; decision: Decision } | undefined {
  const parsed = decisionForm.safeParse({
    verdict: form.get('verdict'),
    reason: form.get('reason') || undefined,
  });
  if (!parsed.success) return undefined;
  const { verdict, reason } = parsed.data;
  return { verdict, decision: reason ? { approver, reason } : { approver } };
}
