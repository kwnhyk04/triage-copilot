import type { components } from '@triage/ts-clients/gateway';

type Schemas = components['schemas'];
type Summary = Schemas['InvestigationSummary'];

export const PARTS: Record<Schemas['ModelPart'], string> = {
  triage: 'Triage',
  summary: 'Log summaries',
  plan: 'Plan',
};
export const PLAN_KINDS: Record<Schemas['PlanKind'], string> = {
  none: 'No fixed action for',
  restart_service: 'Restart',
  code_fix: 'Code fix in',
};
export const VERDICTS: Record<Schemas['Verdict'], string> = {
  real: 'Real incident',
  not_real: 'Not a real incident',
};
export const STATUSES: Record<Schemas['InvestigationStatus'], string> = {
  queued: 'Queued',
  running: 'Running',
  awaiting_approval: 'Needs approval',
  resolved: 'Report ready',
  failed: 'Failed',
  cancelled: 'Cancelled',
};
export const ACTION_KINDS: Record<Schemas['ActionKind'], string> = {
  rollback_deploy: 'Rollback deploy',
  restart_service: 'Restart service',
  scale_service: 'Scale service',
  disable_feature_flag: 'Disable feature flag',
};
export const STEP_KINDS: Record<Schemas['StepKind'], string> = {
  tool_call: 'Tool call',
  hypothesis: 'Hypothesis',
  action: 'Action',
  report: 'Report',
};
const MONTHS = 'Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec'.split(' ');

/** A tag's band: its color (kind) and first words. */
export interface Band {
  kind: 'real' | 'not-real' | 'simulated' | 'open' | 'none';
  label: string;
}

/** The band: the verdict, a simulated incident's (which has none), or an open investigation's. */
export function band({ verdict, source, status }: Pick<Summary, 'verdict' | 'source' | 'status'>) {
  if (verdict === 'real') return { kind: 'real', label: VERDICTS.real } satisfies Band;
  if (verdict === 'not_real') return { kind: 'not-real', label: VERDICTS.not_real } satisfies Band;
  if (source === 'sim') return { kind: 'simulated', label: 'Simulated' } satisfies Band;
  return status === 'queued' || status === 'running'
    ? ({ kind: 'open', label: 'Investigating' } satisfies Band)
    : ({ kind: 'none', label: 'No verdict' } satisfies Band);
}

/** A hypothesis's root cause class and the service it names, such as "database in postgres". */
export const cause = ({
  root_cause_class,
  service,
}: Pick<Schemas['Hypothesis'], 'root_cause_class' | 'service'>) =>
  `${root_cause_class.replaceAll('_', ' ')} in ${service}`;

/** The top cause in brief, such as "Resource exhaustion in worker, 80%". */
export function causeBrief(top: Summary['cause']): string | undefined {
  if (top === null) return undefined;
  const text = cause(top);
  return `${text.charAt(0).toUpperCase()}${text.slice(1)}, ${String(Math.round(top.confidence * 100))}%`;
}

/**
 * The time a server page is read at, for its ages. A server component renders once per request, so
 * the value holds for the whole page; React's purity lint cannot tell that from Date.now().
 */
export const requestTime = () => Date.now();

const twoDigits = (value: number) => String(value).padStart(2, '0');

/** A time in UTC: "09:52 UTC" today, "24 Oct, 22:41 UTC" this year, else with its year too. */
export function utcTime(iso: string, now: number): string {
  const at = new Date(iso);
  const today = new Date(now);
  const time = `${twoDigits(at.getUTCHours())}:${twoDigits(at.getUTCMinutes())} UTC`;
  if (iso.slice(0, 10) === today.toISOString().slice(0, 10)) return time;
  const day = `${String(at.getUTCDate())} ${MONTHS[at.getUTCMonth()] ?? ''}`;
  const year =
    at.getUTCFullYear() === today.getUTCFullYear() ? '' : ` ${String(at.getUTCFullYear())}`;
  return `${day}${year}, ${time}`;
}

const MINUTE_MS = 60_000;
// A queued investigation younger than this may start; an older one is parked or stuck.
const QUEUED_MINUTES = 15;
// After a real verdict the plan follows within Opus's 300 s timeout; with the planner off, never.
const PLAN_WAIT_MINUTES = 6;

/**
 * Whether a page showing this investigation should keep refreshing: while it runs, while it is
 * queued and young, and for a while after a real verdict whose plan has not arrived.
 */
export function stillChanging(
  item: Pick<Summary, 'status' | 'created_at' | 'updated_at' | 'verdict' | 'plan_status'>,
  now: number,
  planArrived = item.plan_status !== null,
): boolean {
  if (item.status === 'running') return true;
  if (item.status === 'queued') {
    return now - Date.parse(item.created_at) < QUEUED_MINUTES * MINUTE_MS;
  }
  return (
    item.verdict === 'real' &&
    !planArrived &&
    now - Date.parse(item.updated_at) < PLAN_WAIT_MINUTES * MINUTE_MS
  );
}

/** How far the investigation has got, such as "Running, 7 steps so far, last at 10:02:41 UTC". */
export function progressLine({
  status,
  steps,
  plan,
}: Pick<Schemas['Investigation'], 'status' | 'steps' | 'plan'>) {
  if (status !== 'queued' && status !== 'running') {
    return `${STATUSES[status]}, ${plan === null ? 'waiting for the plan' : 'plan received'}`;
  }
  const last = steps.at(-1);
  if (last === undefined) return `${STATUSES[status]}, no steps yet`;
  const count = steps.length === 1 ? '1 step' : `${String(steps.length)} steps`;
  return `${STATUSES[status]}, ${count} so far, last at ${last.recorded_at.slice(11, 19)} UTC`;
}

/** How long ago, in its largest whole unit. */
export function ago(iso: string, now: number): string {
  const minutes = Math.floor((now - Date.parse(iso)) / 60_000);
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${String(minutes)} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${String(hours)} h ago`;
  const days = Math.floor(hours / 24);
  return days === 1 ? '1 day ago' : `${String(days)} days ago`;
}

/** One note per model that missed requests: what answered its part instead, and why it missed. */
export function fallbackNotes(models: readonly Schemas['ModelUse'][]): string[] {
  return models
    .filter((model) => model.failed > 0)
    .map((model) => {
      const answered = models
        .filter((other) => other.part === model.part && other.calls > 0)
        .map((other) => other.model);
      const by = answered.length === 0 ? 'no model answered' : `${answered.join(', ')} answered`;
      const requests = model.failed === 1 ? '1 request' : `${String(model.failed)} requests`;
      const why = model.reason ?? 'no reason was recorded';
      return `${PARTS[model.part]}: ${by}; ${model.model} missed ${requests}: ${why}`;
    });
}
