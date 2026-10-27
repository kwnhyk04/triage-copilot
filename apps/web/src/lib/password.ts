import { argon2, randomBytes, timingSafeEqual } from 'node:crypto';
import { promisify } from 'node:util';

// The sign-in cost: 64 MiB, 3 passes and 4 lanes, with a 16-byte salt and a 32-byte tag.
const COST = { memory: 65_536, passes: 3, parallelism: 4 };
const SALT_BYTES = 16;
const TAG_BYTES = 32;
// What a stored hash may ask for, so a bad LOGIN_PASSWORD_HASH can neither make each sign-in cost
// gigabytes nor skip the work.
const BOUNDS = {
  memory: [19_456, 262_144],
  passes: [1, 10],
  parallelism: [1, 16],
  salt: [8, 64],
  tag: [32, 64],
} as const;
const PHC =
  /^\$argon2id\$v=19\$m=(\d{1,7}),t=(\d{1,3}),p=(\d{1,3})\$([A-Za-z0-9+/]+)\$([A-Za-z0-9+/]+)$/;
/** A password hash-password takes: 12 to 1,024 characters; the form takes up to 1,024. */
export const PASSWORD_LENGTH = { min: 12, max: 1024 } as const;

const derive = promisify(argon2);
const unpadded = (bytes: Buffer) => bytes.toString('base64').replace(/=+$/, '');
const within = (value: number, [min, max]: readonly [number, number]) =>
  value >= min && value <= max;

interface Hash {
  memory: number;
  passes: number;
  parallelism: number;
  salt: Buffer;
  tag: Buffer;
}

/** A stored argon2id hash's parameters; undefined when it is malformed or out of bounds. */
export function parseHash(phc: string): Hash | undefined {
  const match = PHC.exec(phc);
  if (match === null) return undefined;
  const [, memory = '', passes = '', parallelism = '', salt = '', tag = ''] = match;
  const hash = {
    memory: Number(memory),
    passes: Number(passes),
    parallelism: Number(parallelism),
    salt: Buffer.from(salt, 'base64'),
    tag: Buffer.from(tag, 'base64'),
  };
  // Canonical base64 only, so one string decodes one way.
  const canonical = unpadded(hash.salt) === salt && unpadded(hash.tag) === tag;
  const bounded =
    within(hash.memory, BOUNDS.memory) &&
    within(hash.passes, BOUNDS.passes) &&
    within(hash.parallelism, BOUNDS.parallelism) &&
    within(hash.salt.length, BOUNDS.salt) &&
    within(hash.tag.length, BOUNDS.tag);
  return canonical && bounded ? hash : undefined;
}

/** A new argon2id hash of the password at the sign-in cost, as a PHC string. */
export async function hashPassword(password: string): Promise<string> {
  const salt = randomBytes(SALT_BYTES);
  const tag = await derive('argon2id', {
    message: password,
    nonce: salt,
    ...COST,
    tagLength: TAG_BYTES,
  });
  const cost = `m=${String(COST.memory)},t=${String(COST.passes)},p=${String(COST.parallelism)}`;
  return `$argon2id$v=19$${cost}$${unpadded(salt)}$${unpadded(tag)}`;
}

/** Whether the password matches the stored hash; false for any hash parseHash refuses. */
export async function verifyPassword(password: string, phc: string): Promise<boolean> {
  const hash = parseHash(phc);
  if (hash === undefined) return false;
  const tag = await derive('argon2id', {
    message: password,
    nonce: hash.salt,
    memory: hash.memory,
    passes: hash.passes,
    parallelism: hash.parallelism,
    tagLength: hash.tag.length,
  });
  return timingSafeEqual(tag, hash.tag);
}
