import type { BadgeState } from '../lib/report.ts';

/** A state as a tinted pill: its word beside a dot, never colour alone; the dot pulses while work
 * goes on. */
export function Badge({ state }: { state: BadgeState }) {
  const classes = ['badge', state.tone === 'neutral' ? '' : state.tone, state.busy ? 'busy' : ''];
  return <span className={classes.filter(Boolean).join(' ')}>{state.label}</span>;
}
