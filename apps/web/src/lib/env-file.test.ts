import { envValue, forMake, withEnvLine } from './env-file.ts';

describe('env file lines', () => {
  const text = 'GATEWAY_URL=http://127.0.0.1:4200\nSESSION_SECRET=\n# LOGIN_PASSWORD_HASH=old\n';

  it('replaces the variable line, or appends one', () => {
    expect(withEnvLine(text, 'SESSION_SECRET', 'abc')).toBe(
      'GATEWAY_URL=http://127.0.0.1:4200\nSESSION_SECRET=abc\n# LOGIN_PASSWORD_HASH=old\n',
    );
    expect(withEnvLine(text, 'LOGIN_PASSWORD_HASH', 'h')).toBe(`${text}LOGIN_PASSWORD_HASH=h\n`);
    expect(withEnvLine('A=1', 'B', '2')).toBe('A=1\nB=2\n');
    expect(withEnvLine('', 'B', '2')).toBe('B=2\n');
  });

  it('reads a value, empty or commented out counting as none', () => {
    expect(envValue(text, 'GATEWAY_URL')).toBe('http://127.0.0.1:4200');
    expect(envValue(text, 'SESSION_SECRET')).toBeUndefined();
    expect(envValue(text, 'LOGIN_PASSWORD_HASH')).toBeUndefined();
    expect(envValue('A=1\r\nB=2\r\n', 'A')).toBe('1');
  });

  it("writes each $ as $$, which the Makefile's include reads back as one", () => {
    expect(forMake('$argon2id$v=19$m=65536')).toBe('$$argon2id$$v=19$$m=65536');
  });
});
