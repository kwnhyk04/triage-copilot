import { fallbackNotes } from './report.ts';
import { LIVE_TRIAGE } from './testing.ts';

describe('fallbackNotes', () => {
  it('names each model that missed requests, why, and what answered its part instead', () => {
    expect(fallbackNotes(LIVE_TRIAGE.models)).toEqual([
      'Triage: gemini-3.5-flash-lite answered; claude-haiku-5-5 missed 1 request: ' +
        'claude-haiku-5-5 skipped: Claude is switched off (agent.switches)',
    ]);
  });

  it('is empty when the first model asked answered every request', () => {
    const [, gemini] = LIVE_TRIAGE.models;
    if (gemini === undefined) throw new Error('the fixture lists Gemini');
    expect(fallbackNotes([gemini])).toEqual([]);
  });

  it('says when no model answered a part', () => {
    const missed = { calls: 0, failed: 2, reason: 'RateLimited: gemini 429: rate limited' };
    const summaries = LIVE_TRIAGE.models.map((model) =>
      model.part === 'summary' ? { ...model, ...missed } : model,
    );
    expect(fallbackNotes(summaries)).toContain(
      'Log summaries: no model answered; gemini-3.1-flash-lite missed 2 requests: ' +
        'RateLimited: gemini 429: rate limited',
    );
  });
});
