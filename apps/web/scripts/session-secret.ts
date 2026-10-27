// pnpm --filter @triage/web session-secret: writes a random SESSION_SECRET into the root .env,
// printing nothing secret. It refuses when one is set already, since a new one signs everyone out.
import { randomBytes } from 'node:crypto';
import { readFile, writeFile } from 'node:fs/promises';

import { envValue, withEnvLine } from '../src/lib/env-file.ts';

const ENV_FILE = new URL('../../../.env', import.meta.url);

const text = await readFile(ENV_FILE, 'utf8').catch(() => '');
if (envValue(text, 'SESSION_SECRET') !== undefined) {
  console.error('session-secret: .env has a SESSION_SECRET already; remove it to make a new one');
  process.exitCode = 1;
} else {
  await writeFile(
    ENV_FILE,
    withEnvLine(text, 'SESSION_SECRET', randomBytes(32).toString('base64url')),
  );
  console.log('SESSION_SECRET written to .env.');
}
