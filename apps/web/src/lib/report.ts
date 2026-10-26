import type { components } from '@triage/ts-clients/gateway';

type Schemas = components['schemas'];

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

/** The verdict as a reader says it; a simulated incident, or a report after a stop, has none. */
export const verdictLabel = (verdict: Schemas['Verdict'] | null) =>
  verdict === null
    ? 'No verdict: a simulated incident, or a report the agent wrote itself after a stop'
    : VERDICTS[verdict];

/** A hypothesis's root cause class and the service it names, such as "database in postgres". */
export const cause = ({ root_cause_class, service }: Schemas['Hypothesis']) =>
  `${root_cause_class.replaceAll('_', ' ')} in ${service}`;

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
