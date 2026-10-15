import { decodeCursor, encodeCursor } from './investigations.ts';

const ID = '9f8b6c1e-3d2a-4f5b-8e7d-6c5b4a392817';
const encode = (value: unknown) => Buffer.from(JSON.stringify(value)).toString('base64url');

describe('cursor', () => {
  it('decodes what it encodes, to the microsecond', () => {
    const at = '2026-10-15T04:44:39.473995Z';
    expect(decodeCursor(encodeCursor(at, ID))).toEqual([at, ID]);
  });

  it('refuses anything else, impossible instants included', () => {
    for (const cursor of [
      'forged',
      encode(['2026-02-31T00:00:00.000000Z', ID]),
      encode(['2026-10-15T25:00:00.000000Z', ID]),
      encode(['2026-10-15T04:44:39.473Z', ID]),
      encode(['2026-10-15T04:44:39.473995Z', 'not-a-uuid']),
      encode(['2026-10-15T04:44:39.473995Z']),
    ]) {
      expect(decodeCursor(cursor)).toBeUndefined();
    }
  });
});
