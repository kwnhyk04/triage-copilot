import './globals.css';

import localFont from 'next/font/local';
import { connection } from 'next/server';
import type { ReactNode } from 'react';

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

export const metadata = {
  title: 'Triage Copilot',
  description: 'Is this alert a real incident? The verdict, its cause and evidence, and the fix.',
};
export const viewport = { colorScheme: 'light dark' };

export default async function RootLayout({ children }: { children: ReactNode }) {
  // Every page renders per request, so each gets the proxy's fresh CSP nonce.
  await connection();
  return (
    <html lang="en" className={`${sans.variable} ${mono.variable}`}>
      <body>
        <a className="skip" href="#main">
          Skip to content
        </a>
        {children}
      </body>
    </html>
  );
}
