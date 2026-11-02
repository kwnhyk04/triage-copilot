import './globals.css';

import localFont from 'next/font/local';
import { cookies } from 'next/headers';
import { connection } from 'next/server';
import type { ReactNode } from 'react';

import { Header } from '../components/header.tsx';
import { loadWebConfig } from '../lib/config.ts';
import { SESSION_COOKIE, validSession } from '../lib/session.ts';
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

export default async function RootLayout({ children }: { children: ReactNode }) {
  // Every page renders per request, so each gets the proxy's fresh CSP nonce.
  await connection();
  const jar = await cookies();
  // The theme cookie picks the colours on the server (data-theme); without one the device does.
  const theme = parseTheme(jar.get(THEME_COOKIE)?.value);
  const signedIn = validSession(jar.get(SESSION_COOKIE)?.value, loadWebConfig().SESSION_SECRET);
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
        <Header signedIn={signedIn} theme={theme} />
        {children}
      </body>
    </html>
  );
}
