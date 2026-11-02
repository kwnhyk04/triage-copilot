import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { parseTheme, THEME_COOKIE, themeCookie } from './theme.ts';

// The declarations of the first rule whose selector is exactly `selector`, sorted.
const declarations = (css: string, selector: string) => {
  const start = css.indexOf(`${selector} {`);
  const body = css.slice(start + selector.length + 2, css.indexOf('}', start));
  return body
    .split(';')
    .map((line) => line.trim())
    .filter(Boolean)
    .sort();
};

describe('theme', () => {
  it('keeps Light or Dark in the tc-theme cookie for a year, and System as no cookie', () => {
    expect(THEME_COOKIE).toBe('tc-theme');
    expect(themeCookie('dark')).toBe('tc-theme=dark; path=/; max-age=31536000; samesite=lax');
    expect(themeCookie('light')).toBe('tc-theme=light; path=/; max-age=31536000; samesite=lax');
    expect(themeCookie('system')).toBe('tc-theme=; path=/; max-age=0; samesite=lax');
  });

  it('reads anything but light or dark as System', () => {
    expect(['light', 'dark', 'system', 'blue', undefined].map(parseTheme)).toEqual([
      'light',
      'dark',
      'system',
      'system',
      'system',
    ]);
  });

  it("gives the device's dark scheme and a chosen Dark the same colours", () => {
    const css = readFileSync(join(__dirname, '../app/globals.css'), 'utf8');
    const device = declarations(css, "  :root:not([data-theme='light'])");
    const chosen = declarations(css, ":root[data-theme='dark']");
    expect(device.length).toBeGreaterThan(10);
    expect(device).toEqual(chosen);
  });
});
