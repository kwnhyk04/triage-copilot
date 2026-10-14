/** fetch that gives up after `ms` milliseconds, or earlier if the request's own signal aborts. */
export const fetchWithTimeout = (ms: number) => (request: Request) =>
  fetch(request, { signal: AbortSignal.any([request.signal, AbortSignal.timeout(ms)]) });
