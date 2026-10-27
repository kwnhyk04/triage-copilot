// Lines of the root .env, for the sign-in scripts; a name is a constant, never user input.
const line = (name: string) => new RegExp(`^${name}=([^\\r\\n]*)`, 'm');

/** The value of a NAME= line; undefined when there is none or it is empty. */
export function envValue(text: string, name: string): string | undefined {
  const value = line(name).exec(text)?.[1];
  return value === '' ? undefined : value;
}

/** The text with NAME=value in place of the variable's line, or appended at the end. */
export function withEnvLine(text: string, name: string, value: string): string {
  const entry = `${name}=${value}`;
  if (line(name).test(text)) return text.replace(line(name), () => entry);
  return `${text}${text === '' || text.endsWith('\n') ? '' : '\n'}${entry}\n`;
}

/** A value as the Makefile's `-include .env` must hold it: make expands $, so each is doubled. */
export const forMake = (value: string) => value.replaceAll('$', () => '$$');
