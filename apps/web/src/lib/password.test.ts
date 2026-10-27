import { hashPassword, parseHash, verifyPassword } from './password.ts';

// phc-winner-argon2's argon2id reference vector (src/test.c): "password", salt "somesalt", 2
// passes, 64 MiB, 1 lane.
const REFERENCE =
  '$argon2id$v=19$m=65536,t=2,p=1$c29tZXNhbHQ$CTFhFdXPJO1aFaMaO6Mm5c8y7cJHAph8ArZWb2GRPPc';

describe('password', () => {
  it('verifies the argon2 reference vector', async () => {
    await expect(verifyPassword('password', REFERENCE)).resolves.toBe(true);
    await expect(verifyPassword('Password', REFERENCE)).resolves.toBe(false);
  });

  it('hashes at the sign-in cost with a fresh salt, and verifies its own hash', async () => {
    const hash = await hashPassword('correct horse battery');
    expect(hash).toMatch(
      /^\$argon2id\$v=19\$m=65536,t=3,p=4\$[A-Za-z0-9+/]{22}\$[A-Za-z0-9+/]{43}$/,
    );
    await expect(verifyPassword('correct horse battery', hash)).resolves.toBe(true);
    await expect(verifyPassword('correct horse batterz', hash)).resolves.toBe(false);
    expect(await hashPassword('correct horse battery')).not.toBe(hash);
  });

  it('refuses a malformed hash and a cost out of bounds, without running argon2', async () => {
    for (const hash of [
      '',
      'hunter2',
      REFERENCE.replace('argon2id', 'argon2i'),
      REFERENCE.replace('v=19', 'v=16'),
      // 4 GiB and 1 MiB.
      REFERENCE.replace('m=65536', 'm=4194304'),
      REFERENCE.replace('m=65536', 'm=1024'),
      REFERENCE.replace('t=2', 't=100'),
      REFERENCE.replace('p=1', 'p=64'),
      // A 3-byte salt, and a tag cut to 17 bytes.
      REFERENCE.replace('$c29tZXNhbHQ$', '$c29t$'),
      REFERENCE.slice(0, -20),
      `${REFERENCE}$extra`,
    ]) {
      expect([hash, parseHash(hash)]).toEqual([hash, undefined]);
      await expect(verifyPassword('password', hash)).resolves.toBe(false);
    }
  });
});
