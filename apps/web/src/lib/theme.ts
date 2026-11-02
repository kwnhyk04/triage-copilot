// The reader's theme: System follows the device, Light and Dark override it. The choice lives in a
// cookie, so the server renders the page in it and nothing flashes on load; System is no cookie.
export const THEME_COOKIE = 'tc-theme';
export const THEMES = ['system', 'light', 'dark'] as const;
export type Theme = (typeof THEMES)[number];
export const THEME_LABEL: Record<Theme, string> = {
  system: 'System',
  light: 'Light',
  dark: 'Dark',
};

export const parseTheme = (value: string | undefined): Theme =>
  value === 'light' || value === 'dark' ? value : 'system';

/** Keeps a choice for a year, or forgets it for System. Not secret, so the switch writes it. */
export const themeCookie = (theme: Theme) =>
  theme === 'system'
    ? `${THEME_COOKIE}=; path=/; max-age=0; samesite=lax`
    : `${THEME_COOKIE}=${theme}; path=/; max-age=31536000; samesite=lax`;
