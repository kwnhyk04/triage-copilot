// The data-access layer: pages and actions reach the gateway only through these functions, and
// each checks the session first (app/data-access.test.ts keeps it so). Server code only.
import { z } from '@triage/ts-config';
import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';

import { loadWebConfig, type WebConfig } from './config.ts';
import { decidePlan, decisionFrom } from './decisions.ts';
import {
  GatewayOffline,
  getInvestigation,
  type Investigation,
  type InvestigationPage,
  listInvestigations,
  sent,
  type Source,
} from './investigations.ts';
import { SESSION_COOKIE, validSession } from './session.ts';

/** The one approver: decisions name the signed-in operator. */
export const APPROVER = 'operator';
const OFFLINE = '/demo?offline=1';
const ids = z.tuple([z.guid(), z.guid()]);

/** The settings, once the request carries a valid session; otherwise a redirect to sign-in. */
async function requireSession(): Promise<WebConfig> {
  const config = loadWebConfig();
  const token = (await cookies()).get(SESSION_COOKIE)?.value;
  if (!validSession(token, config.SESSION_SECRET)) redirect('/login');
  return config;
}

/** Runs a gateway call; a gateway that does not answer sends the reader to the demo. */
async function orDemo<T>(call: () => Promise<T>): Promise<T> {
  try {
    return await call();
  } catch (error) {
    if (error instanceof GatewayOffline) redirect(OFFLINE);
    throw error;
  }
}

/** One page of live incidents or eval runs, newest first. */
export async function readInvestigations(
  source: Source,
  cursor?: string,
): Promise<InvestigationPage> {
  const config = await requireSession();
  return orDemo(() =>
    listInvestigations(config, cursor === undefined ? { source } : { source, cursor }),
  );
}

/** One investigation, or undefined when none has the id. */
export async function readInvestigation(id: string): Promise<Investigation | undefined> {
  const config = await requireSession();
  return orDemo(() => getInvestigation(config, id));
}

/**
 * Records the operator's approval or rejection of a plan, then shows its report again. Nothing
 * runs from a plan yet.
 */
export async function recordPlanDecision(
  investigationId: string,
  planId: string,
  form: FormData,
): Promise<void> {
  const config = await requireSession();
  const decided = decisionFrom(form, APPROVER);
  if (!ids.safeParse([investigationId, planId]).success || decided === undefined) {
    throw new Error('The decision needs approve or reject.');
  }
  const { response } = await orDemo(() =>
    sent(() => decidePlan(config, planId, decided.verdict, decided.decision)),
  );
  // 409: a decision already stands (a second submit, say); the report shows it.
  if (!response.ok && response.status !== 409) {
    throw new Error(`gateway answered ${String(response.status)}`);
  }
  redirect(`/investigations/${investigationId}`);
}
