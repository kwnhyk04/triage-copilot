'use server';

import { recordPlanDecision } from '../../../../lib/data.ts';

/**
 * Records the signed-in operator's approval or rejection of a plan through the gateway, then shows
 * the report again. Anyone can POST to an action, so lib/data.ts checks the session first; Next.js
 * refuses a form posted from another origin.
 */
export async function decidePlanAction(investigationId: string, planId: string, form: FormData) {
  await recordPlanDecision(investigationId, planId, form);
}
