import { type components, createDecisionClient } from '@triage/ts-clients/gateway';

import type { WebConfig } from './config.ts';

/**
 * Records a human approval or rejection at the gateway. Server code only: it sends the approver
 * key, which no other service holds.
 */
export function decideAction(
  config: Pick<WebConfig, 'GATEWAY_URL' | 'APPROVER_API_KEY'>,
  actionId: string,
  verdict: 'approve' | 'reject',
  decision: components['schemas']['Decision'],
) {
  const client = createDecisionClient({
    baseUrl: config.GATEWAY_URL,
    approverKey: config.APPROVER_API_KEY,
  });
  const init = { params: { path: { id: actionId } }, body: decision };
  return verdict === 'approve'
    ? client.POST('/v1/actions/{id}/approve', init)
    : client.POST('/v1/actions/{id}/reject', init);
}
