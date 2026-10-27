/** Next.js calls this once at server start, so invalid env stops the server early. */
export async function register(): Promise<void> {
  // Next calls it for the Edge runtime too, which has no node:crypto; this server runs on Node.
  if (process.env.NEXT_RUNTIME === 'nodejs') {
    const { loadWebConfig } = await import('./lib/config.ts');
    loadWebConfig();
  }
}
