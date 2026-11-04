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

/** The two lists, as the header and the pages name them. */
export const TABS: Record<Schemas['InvestigationSource'], string> = {
  live: 'Live incidents',
  sim: 'Eval runs',
};

/** A badge's colour, always beside its word: tinted pills, and one solid one for Failed. */
export type Tone = 'neutral' | 'info' | 'ok' | 'warn' | 'bad' | 'fail';
/** A badge: its word, its tone, and a pulsing dot while work goes on. */
export interface BadgeState {
  label: string;
  tone: Tone;
  busy?: true;
}

/** The verdict as a badge, red only for a real incident; a simulated incident has none. */
export function verdictBadge({
  verdict,
  source,
  status,
}: Pick<Summary, 'verdict' | 'source' | 'status'>): BadgeState {
  if (verdict === 'real') return { label: VERDICTS.real, tone: 'bad' };
  if (verdict === 'not_real') return { label: VERDICTS.not_real, tone: 'neutral' };
  if (source === 'sim') return { label: 'Simulated', tone: 'neutral' };
  return status === 'queued' || status === 'running'
    ? { label: 'Investigating', tone: 'info', busy: true }
    : { label: 'No verdict', tone: 'neutral' };
}

/** The verdict card's tag: the verdict in a word, and the top cause's confidence when there is one. */
export function verdictTag(
  item: Pick<Summary, 'verdict' | 'source' | 'status'>,
  top: Pick<Schemas['Hypothesis'], 'confidence'> | undefined,
): string {
  const { label } = verdictBadge(item);
  const word = label === VERDICTS.real ? 'Real' : label === VERDICTS.not_real ? 'Not real' : label;
  return top === undefined ? word : `${word} · ${percent(top.confidence)}`;
}

/** A count with its noun, such as "1 step" or "21,380 tokens". */
export const counted = (count: number, noun: string) =>
  `${count.toLocaleString('en-US')} ${noun}${count === 1 ? '' : 's'}`;

const STATUS_TONES: Record<Schemas['InvestigationStatus'], Tone> = {
  queued: 'neutral',
  running: 'info',
  awaiting_approval: 'warn',
  resolved: 'ok',
  failed: 'fail',
  cancelled: 'neutral',
};

/** A status as a badge, its dot pulsing while the agent runs. */
export const statusBadge = (status: Schemas['InvestigationStatus']): BadgeState =>
  status === 'running'
    ? { label: STATUSES[status], tone: STATUS_TONES[status], busy: true }
    : { label: STATUSES[status], tone: STATUS_TONES[status] };

/** A plan's state as a badge, amber while it waits for a person. */
export const PLAN_STATES: Record<Schemas['ActionStatus'], BadgeState> = {
  pending: { label: 'Decision needed', tone: 'warn' },
  approved: { label: 'Approved', tone: 'ok' },
  rejected: { label: 'Rejected', tone: 'neutral' },
};

/**
 * A plan's badge: red Refused once its review refused it, which leaves it pending for good, else
 * its decision. An older gateway sends no review, so its plans show their decision as before.
 */
export const planBadge = (
  status: Schemas['ActionStatus'],
  review: Schemas['PlanReviewStatus'] | null | undefined,
): BadgeState => (review === 'refuse' ? { label: 'Refused', tone: 'bad' } : PLAN_STATES[status]);

/** A plan's review in a word or two, for its card's tag. */
export const REVIEW_TAGS: Record<Schemas['PlanReviewStatus'], string> = {
  ok: 'No issues',
  flag: 'Flagged',
  refuse: 'Refused',
  not_reviewed: 'Unreviewed',
};

/** Each kind of issue a plan's review raises, in plain words. */
export const ISSUE_KINDS: Record<Schemas['PlanIssueKind'], string> = {
  forbidden_step: 'Forbidden step',
  fallback_step: 'Fallback step',
  contradicts_evidence: 'Contradicts the evidence',
  unfit_file: "File doesn't fit",
  code_check: 'Code check',
};

/** Under every review a model made: a second opinion, never a promise that the plan is safe. */
export const REVIEW_NOTE = 'Reviewed by a second model. Read every step yourself.';

/** What a plan's review says first: its verdict, or why there is none, and who refused it. */
export function reviewHeading(
  { status, by, reason }: Pick<Schemas['PlanReview'], 'status' | 'by' | 'reason'>,
  pending: boolean,
): string {
  switch (status) {
    case 'ok':
      return 'Reviewed: no issues';
    case 'not_reviewed':
      return `Not reviewed: ${reason ?? 'no reason was recorded'}`;
    case 'flag':
      return pending ? 'Read these warnings before deciding' : 'Warnings from the review';
    case 'refuse':
      return by === 'check' ? "Refused by the agent's checks" : 'Refused by the review';
  }
}

/** A step's tool outcome as a badge; a call that answered stays quiet. */
export const OUTCOMES: Record<Schemas['ToolCallStatus'], BadgeState> = {
  ok: { label: 'OK', tone: 'neutral' },
  error: { label: 'Error', tone: 'bad' },
  timeout: { label: 'Timed out', tone: 'warn' },
};

/** What approving does: only an approved restart runs, once, while the actions switch is on. */
export const planNote = ({ kind, target }: Pick<Schemas['PlanProposal'], 'kind' | 'target'>) =>
  kind === 'restart_service'
    ? 'Approving only records the decision unless the actions switch is on. ' +
      `With it on, the executor restarts ${target} once.`
    : 'Approving only records the decision: nothing runs from this kind of plan.';

/** A hypothesis's root cause class and the service it names, such as "database in postgres". */
export const cause = ({
  root_cause_class,
  service,
}: Pick<Schemas['Hypothesis'], 'root_cause_class' | 'service'>) =>
  `${root_cause_class.replaceAll('_', ' ')} in ${service}`;

/** A cause as a reader says it, such as "Resource exhaustion in worker". */
export function causeName(top: Pick<Schemas['Hypothesis'], 'root_cause_class' | 'service'>) {
  const text = cause(top);
  return `${text.charAt(0).toUpperCase()}${text.slice(1)}`;
}

/** A confidence as a whole percentage, such as "80%". */
export const percent = (confidence: number) => `${String(Math.round(confidence * 100))}%`;

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
// After a real verdict the plan follows: at effort max within about 9 minutes (64,000 tokens at
// the measured 120 a second); with the planner off, never.
export const PLAN_WAIT_MINUTES = 10;

/** A plan still being made, its dot pulsing. */
export const PLANNING: BadgeState = { label: 'Planning', tone: 'info', busy: true };

/** Whether a real verdict's plan may still come: none yet, its report under 10 minutes old. */
export const planning = (
  item: Pick<Summary, 'updated_at' | 'verdict' | 'plan_status'>,
  now: number,
  planArrived = item.plan_status !== null,
) =>
  item.verdict === 'real' &&
  !planArrived &&
  now - Date.parse(item.updated_at) < PLAN_WAIT_MINUTES * MINUTE_MS;

/**
 * Whether a page showing this investigation should keep refreshing: while it runs, while it is
 * queued and young, and while a real verdict's plan may still come.
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
  return planning(item, now, planArrived);
}

/** How far the investigation has got, such as "Running, 7 steps so far, last at 10:02:41 UTC". */
export function progressLine({
  status,
  steps,
  plan,
}: Pick<Schemas['Investigation'], 'status' | 'steps' | 'plan'>) {
  if (status !== 'queued' && status !== 'running') {
    return `${STATUSES[status]}, ${plan === null ? 'still planning' : 'plan received'}`;
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
