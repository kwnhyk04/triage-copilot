import { loadWebConfig } from './lib/config.ts';

/** Next.js calls this once at server start, so invalid env stops the server early. */
export function register(): void {
  loadWebConfig();
}
