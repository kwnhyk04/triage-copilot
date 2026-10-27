import { demoInvestigation, demoPage, EVAL_RUNS, ILLUSTRATIVE, LIVE_SAMPLES } from './index.ts';

const newestFirst = (items: readonly { id: string; created_at: string }[]) =>
  [...items].sort((a, b) => b.created_at.localeCompare(a.created_at)).map((item) => item.id);

describe('demo data', () => {
  it("holds simulated eval runs only: a live investigation is a real app's data", () => {
    expect(EVAL_RUNS.length).toBeGreaterThanOrEqual(4);
    expect(EVAL_RUNS.filter((run) => run.source !== 'sim')).toEqual([]);
  });

  it('labels every hand-written live sample illustrative, and no eval run', () => {
    expect(LIVE_SAMPLES).toHaveLength(3);
    for (const sample of LIVE_SAMPLES) {
      expect([sample.id, sample.source, ILLUSTRATIVE.has(sample.id)]).toEqual([
        sample.id,
        'live',
        true,
      ]);
    }
    expect(EVAL_RUNS.filter((run) => ILLUSTRATIVE.has(run.id))).toEqual([]);
  });

  it('pages each source newest first, and finds one by its id', () => {
    expect(demoPage('live').next_cursor).toBeNull();
    expect(demoPage('live').items.map((item) => item.id)).toEqual(newestFirst(LIVE_SAMPLES));
    expect(demoPage('sim').items.map((item) => item.id)).toEqual(newestFirst(EVAL_RUNS));
    const [first] = EVAL_RUNS;
    expect(first && demoInvestigation(first.id)).toBe(first);
    expect(demoInvestigation('3d6f9a2c-1b4e-4c8d-9f7a-5e2b1c0d4a68')).toBeUndefined();
  });
});
