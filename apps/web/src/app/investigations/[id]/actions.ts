'use server';

import { z } from '@triage/ts-config';
import { redirect } from 'next/navigation';

import { loadWebConfig } from '../../../lib/config.ts';
import { decidePlan, decisionFrom } from '../../../lib/decisions.ts';

const ids = z.tuple([z.guid(), z.guid()]);

/**
 * Records a human's approval or rejection of a plan through the gateway, with the approver key only
 * this server holds, then shows the report again. Nothing runs from a plan yet. Next.js refuses a
 * form posted from another origin.
 */
export async function decidePlanAction(investigationId: string, planId: string, form: FormData) {
  const decided = decisionFrom(form);
  if (!ids.safeParse([investigationId, planId]).success || decided === undefined) {
    throw new Error('The decision needs a name and approve or reject.');
  }
  const { response } = await decidePlan(loadWebConfig(), planId, decided.verdict, decided.decision);
  // 409: a decision already stands (a second submit, say); the report shows it.
  if (!response.ok && response.status !== 409) {
    throw new Error(`gateway answered ${String(response.status)}`);
  }
  redirect(`/investigations/${investigationId}`);
}
