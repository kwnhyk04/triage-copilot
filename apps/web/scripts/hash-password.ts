// pnpm --filter @triage/web hash-password [--write-env]: asks for the sign-in password twice,
// unechoed, and prints its argon2id hash for Vercel; --write-env writes it into the root .env
// instead, printing nothing secret.
import { readFile, writeFile } from 'node:fs/promises';

import { forMake, withEnvLine } from '../src/lib/env-file.ts';
import { hashPassword, PASSWORD_LENGTH } from '../src/lib/password.ts';

const ENV_FILE = new URL('../../../.env', import.meta.url);

/** Reads one line from the terminal without echoing it. */
function secretLine(prompt: string): Promise<string> {
  const { stdin, stdout } = process;
  if (!stdin.isTTY) throw new Error('run it in a terminal: the password is read unechoed');
  stdout.write(prompt);
  stdin.setRawMode(true);
  stdin.setEncoding('utf8');
  stdin.resume();
  return new Promise((resolve, reject) => {
    let line = '';
    const done = () => {
      stdin.off('data', onData);
      stdin.setRawMode(false);
      stdin.pause();
      stdout.write('\n');
    };
    const onData = (chunk: string) => {
      for (const char of chunk) {
        if (char === '\r' || char === '\n') {
          done();
          resolve(line);
          return;
        }
        if (char === '\u0003') {
          done();
          reject(new Error('cancelled'));
          return;
        }
        line = char === '\u007f' || char === '\b' ? line.slice(0, -1) : line + char;
      }
    };
    stdin.on('data', onData);
  });
}

try {
  const password = await secretLine('Sign-in password: ');
  if (password.length < PASSWORD_LENGTH.min || password.length > PASSWORD_LENGTH.max) {
    throw new Error(
      `the password needs ${String(PASSWORD_LENGTH.min)} to ${String(PASSWORD_LENGTH.max)} characters`,
    );
  }
  if ((await secretLine('Again: ')) !== password) throw new Error('the two passwords differ');
  const hash = await hashPassword(password);
  if (process.argv.includes('--write-env')) {
    const text = await readFile(ENV_FILE, 'utf8').catch(() => '');
    await writeFile(ENV_FILE, withEnvLine(text, 'LOGIN_PASSWORD_HASH', forMake(hash)));
    console.log('LOGIN_PASSWORD_HASH written to .env, each $ doubled for make.');
  } else {
    console.log(hash);
  }
} catch (error) {
  console.error(`hash-password: ${(error as Error).message}`);
  process.exitCode = 1;
}
