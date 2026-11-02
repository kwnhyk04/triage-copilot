import './globals.css';

import localFont from 'next/font/local';
import { cookies } from 'next/headers';
import { connection } from 'next/server';
import type { ReactNode } from 'react';

import { parseTheme, THEME_COOKIE } from '../lib/theme.ts';

// Committed files, served from this origin; the build fetches nothing (docs/memory.md, fonts).
const sans = localFont({
  src: './fonts/AtkinsonHyperlegibleNext.woff2',
  weight: '200 800',
  variable: '--font-sans',
  display: 'swap',
});
const mono = localFont({
  src: './fonts/AtkinsonHyperlegibleMono.woff2',
  weight: '200 800',
  variable: '--font-mono',
  display: 'swap',
});

// The tab and home-screen icons are app/icon.svg and app/apple-icon.png, which Next links itself.
export const metadata = {
  title: 'Triage Copilot',
  description: 'Is this alert a real incident? The verdict, its cause and evidence, and the fix.',
};
export const viewport = { colorScheme: 'light dark' };

/**
 * The document around every page. The header is not here: a layout keeps its first render across
 * client-side navigation, so each page renders the header (SiteHeader) with its own session check.
 */
export default async function RootLayout({ children }: { children: ReactNode }) {
  // Every page renders per request, so each gets the proxy's fresh CSP nonce.
  await connection();
  // The theme cookie picks the colours on the server (data-theme); without one the device does.
  const theme = parseTheme((await cookies()).get(THEME_COOKIE)?.value);
  return (
    <html
      lang="en"
      className={`${sans.variable} ${mono.variable}`}
      data-theme={theme === 'system' ? undefined : theme}
    >
      <body>
        <a className="skip" href="#main">
          Skip to content
        </a>
        {children}
      </body>
    </html>
  );
}
