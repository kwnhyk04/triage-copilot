// Numbered pages of a list: the page and size live in the URL (?page=&size=), the size also in
// localStorage, so the next list opens at the size last chosen. Server and browser code alike.

export const SIZES = [10, 20, 50] as const;
export type Size = (typeof SIZES)[number];
export const DEFAULT_SIZE: Size = 20;
/** The gateway's `offset` maximum (contracts/openapi/gateway.yaml). */
export const MAX_OFFSET = 1_000_000;
const SIZE_KEY = 'tc-page-size';
// Up to this many page numbers show; past it, the first, the last and the current with gaps.
const SLOTS = 7;

/** One of the sizes on offer, or undefined. */
export const pageSize = (value: unknown): Size | undefined =>
  SIZES.find((size) => String(size) === value);

/** The page (a whole number from 1) and size a URL asks for, else page 1 at 20 a page. */
export function paging(query: Record<string, string | string[] | undefined>) {
  const { page, size } = query;
  const asked = typeof page === 'string' && /^\d+$/.test(page) ? Number(page) : 0;
  return {
    page: Number.isSafeInteger(asked) && asked > 0 ? asked : 1,
    size: pageSize(size) ?? DEFAULT_SIZE,
  };
}

/** How many items come before the page, capped where the gateway refuses more. */
export const offsetOf = (page: number, size: number) => Math.min((page - 1) * size, MAX_OFFSET);

/** How many pages the list fills; an empty list still has page 1. */
export const pageCount = (total: number, size: number) => Math.max(1, Math.ceil(total / size));

const range = (from: number, to: number) =>
  Array.from({ length: to - from + 1 }, (_, i) => from + i);

/** The page numbers to show, null for each gap: always seven slots once there are more pages. */
export function pageWindow(current: number, count: number): (number | null)[] {
  if (count <= SLOTS) return range(1, count);
  if (current <= 4) return [...range(1, 5), null, count];
  if (current >= count - 3) return [1, null, ...range(count - 4, count)];
  return [1, null, current - 1, current, current + 1, null, count];
}

/** The page at the new size that holds the first row of the page shown now. */
export const keepFirstRow = (page: number, from: number, to: number) =>
  Math.floor(((page - 1) * from) / to) + 1;

/** A link to a page and size of the list at `path`, keeping its other settings. */
export function pageHref(
  path: string,
  search: string | URLSearchParams,
  page: number,
  size: number,
): string {
  const query = new URLSearchParams(search);
  // Lists numbered their pages from gateway API 0.11.0; an older link's cursor means nothing now.
  query.delete('cursor');
  query.set('page', String(page));
  query.set('size', String(size));
  return `${path}?${query.toString()}`;
}

/** The size last chosen, if storage holds one on offer; a blocked or missing store holds none. */
export function storedSize(): Size | undefined {
  try {
    return pageSize(window.localStorage.getItem(SIZE_KEY));
  } catch {
    return undefined;
  }
}

/** Remembers the size chosen; where storage is blocked, the URL still carries it. */
export function storeSize(size: Size): void {
  try {
    window.localStorage.setItem(SIZE_KEY, String(size));
  } catch {
    // A private window, or storage turned off: nothing to keep it in.
  }
}
