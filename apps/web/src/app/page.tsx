import { SiteHeader } from '../components/header.tsx';
import { Hero } from '../components/hero.tsx';
import { signedIn } from '../lib/auth.ts';

/**
 * The public landing: what Triage Copilot does, a saved report, and the demo, with a note after
 * signing out. A signed-in reader gets Live incidents as the button. It reads nothing from the
 * gateway.
 */
export default async function Landing({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const signedOut = (await searchParams)['signed-out'] !== undefined;
  return (
    <>
      <SiteHeader />
      <main id="main" className="landing">
        <Hero
          signedIn={await signedIn()}
          note={
            signedOut && (
              <p className="muted" role="status">
                You&apos;re signed out.
              </p>
            )
          }
        />
      </main>
    </>
  );
}
