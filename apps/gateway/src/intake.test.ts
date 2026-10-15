import { readFileSync } from 'node:fs';

import fc from 'fast-check';

import { fingerprint, webhookSchema, withComputedFingerprints } from './intake.ts';

// Copied from prometheus/common model/signature_test.go: TestMetricToFingerprint (LabelSet to
// Fingerprint) and TestSignatureForLabels, whose "empty" case hashes the bytes of {"empty": ""}.
const PROMETHEUS = [
  [{}, 14695981039346656037n],
  [{ name: 'garland, briggs', fear: 'love is not enough' }, 5799056148416392346n],
  [{ empty: '' }, 7187873163539638612n],
] as const;

// Captured from Alertmanager 0.28.1 posting to a webhook receiver. One alert has an upper-case label
// name and a non-ASCII value.
const REAL = webhookSchema.parse(
  JSON.parse(
    readFileSync(new URL('testdata/alertmanager-0.28.1-webhook.json', import.meta.url), 'utf8'),
  ),
);

const sameSet = (a: Record<string, string>, b: Record<string, string>) =>
  JSON.stringify(Object.entries(a).sort()) === JSON.stringify(Object.entries(b).sort());

describe('fingerprint', () => {
  it.each(PROMETHEUS)('matches prometheus/common for %j', (labels, expected) => {
    expect(BigInt(`0x${fingerprint(labels)}`)).toBe(expected);
  });

  it("equals the fingerprint field of a real Alertmanager webhook's alerts", () => {
    expect(REAL.alerts).toHaveLength(2);
    for (const alert of REAL.alerts) expect(fingerprint(alert.labels)).toBe(alert.fingerprint);
  });

  it('sorts names by their UTF-8 bytes, as Go does, not by UTF-16 code units', () => {
    // U+FF61 comes before U+10000 in UTF-8 (EF before F0) but after it in UTF-16 (FF61 after
    // D800). The value is from a reference that sorts by code point, which is UTF-8 byte order.
    const labels = { '\u{10000}': 'b', '｡': 'a' };
    expect(BigInt(`0x${fingerprint(labels)}`)).toBe(8712694575426665773n);
  });

  it('is the same for the same labels in any order', () => {
    const permuted = fc
      .dictionary(fc.string({ unit: 'binary' }), fc.string({ unit: 'binary' }))
      .chain((labels) => {
        const entries = Object.entries(labels);
        const length = { minLength: entries.length, maxLength: entries.length };
        return fc.tuple(fc.constant(labels), fc.shuffledSubarray(entries, length));
      });
    fc.assert(
      fc.property(permuted, ([labels, entries]) => {
        expect(fingerprint(Object.fromEntries(entries))).toBe(fingerprint(labels));
      }),
    );
  });

  it('differs whenever the label sets differ, even when their bytes run together', () => {
    // A tiny alphabet yields pairs such as {ab: c} and {a: bc}, which only the separator tells apart.
    const tiny = fc.string({ unit: fc.constantFrom('a', 'b', 'é'), maxLength: 3 });
    const labels = fc.dictionary(tiny, tiny, { maxKeys: 3 });
    fc.assert(
      fc.property(labels, labels, (a, b) => {
        expect(fingerprint(a) === fingerprint(b)).toBe(sameSet(a, b));
      }),
      { numRuns: 1000 },
    );
  });
});

describe('withComputedFingerprints', () => {
  it('replaces each fingerprint with the computed one and counts those that differed', () => {
    const forged = {
      ...REAL,
      alerts: REAL.alerts.map((alert, i) =>
        i === 0 ? { ...alert, fingerprint: 'forged' } : alert,
      ),
    };
    const { webhook, mismatches } = withComputedFingerprints(forged);
    expect(mismatches).toBe(1);
    expect(webhook.alerts.map((alert) => alert.fingerprint)).toEqual(
      REAL.alerts.map((alert) => alert.fingerprint),
    );
    expect(withComputedFingerprints(REAL).mismatches).toBe(0);
  });
});
