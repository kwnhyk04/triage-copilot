// The public demo's data: recorded eval runs (generated/eval-runs.ts, from `make demo-save`) and
// three hand-written live samples. The demo pages and the e2e fake gateway read it; the demo
// calls no gateway and needs no session.
import type { Investigation, InvestigationPage, Source } from '../lib/investigations.ts';
import { EVAL_RUNS as RECORDED } from './generated/eval-runs.ts';
import { LIVE_SAMPLES } from './live-samples.ts';

export {
  ANSWERS_AFTER_RENAME,
  LIVE_SAMPLES,
  ONE_CLIENT_RETRYING,
  WORKER_OUT_OF_MEMORY,
} from './live-samples.ts';
/** Recorded eval runs; demo.test.ts checks at run time that each is simulated. */
export const EVAL_RUNS: readonly Investigation[] = RECORDED;
/** The hand-written samples, which every page labels illustrative. */
export const ILLUSTRATIVE: ReadonlySet<string> = new Set(LIVE_SAMPLES.map((sample) => sample.id));
const ALL: readonly Investigation[] = [...LIVE_SAMPLES, ...EVAL_RUNS];

/** One source's investigations, newest first, as one page. */
export function demoPage(source: Source): InvestigationPage {
  const items = ALL.filter((item) => item.source === source).sort((a, b) =>
    b.created_at.localeCompare(a.created_at),
  );
  return { items, next_cursor: null };
}

/** One investigation, or undefined when none has the id. */
export const demoInvestigation = (id: string) => ALL.find((item) => item.id === id);
