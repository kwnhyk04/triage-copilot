'use client';

import Link from 'next/link';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { type ChangeEvent, useEffect, useRef } from 'react';

import {
  keepFirstRow,
  pageCount,
  pageHref,
  pageSize,
  pageWindow,
  SIZES,
  storedSize,
  storeSize,
} from '../lib/paging.ts';
import { Icon } from './icons.tsx';

export interface PagerProps {
  page: number;
  size: number;
  total: number;
  /** The first row shown, which a new size keeps on screen. */
  firstId?: string | undefined;
}

/** Previous or Next: a link, or a disabled button at the end of the list. */
function Step({
  to,
  label,
  icon,
}: {
  to: string | null;
  label: string;
  icon: 'previous' | 'next';
}) {
  const glyph = <Icon name={icon} size={16} stroke={2} />;
  return to === null ? (
    <button type="button" className="pager-button outline" disabled aria-label={label}>
      {glyph}
    </button>
  ) : (
    <Link className="pager-button outline" href={to} aria-label={label}>
      {glyph}
    </Link>
  );
}

/**
 * The list's pages: where the reader is, Previous, the page numbers and Next, and the size. Each
 * page is a link, so Back, a reload and a shared link keep it. A new size moves to the page that
 * holds the old first row and brings that row into view; the size is remembered for lists opened
 * without one.
 */
export function Pager({ page, size, total, firstId }: PagerProps) {
  const router = useRouter();
  const path = usePathname();
  const search = useSearchParams();
  const kept = useRef<string | undefined>(undefined);
  const pages = pageCount(total, size);
  const href = (to: number, by = size) => pageHref(path, search, to, by);

  // A list opened without a size takes the one last chosen.
  useEffect(() => {
    const remembered = storedSize();
    if (search.has('size') || remembered === undefined || remembered === size) return;
    router.replace(pageHref(path, search, keepFirstRow(page, size, remembered), remembered), {
      scroll: false,
    });
  }, [page, path, router, search, size]);

  // Once the new size has rendered, the row that led the old page comes into view.
  useEffect(() => {
    const id = kept.current;
    kept.current = undefined;
    if (id !== undefined) document.getElementById(`investigation-${id}`)?.scrollIntoView();
  }, [page, size]);

  const resize = (event: ChangeEvent<HTMLSelectElement>) => {
    const next = pageSize(event.target.value);
    if (next === undefined) return;
    storeSize(next);
    kept.current = firstId;
    router.push(href(keepFirstRow(page, size, next), next), { scroll: false });
  };

  return (
    <div className="pager">
      <p className="pager-status" aria-live="polite">
        Page <span>{page}</span> of <span>{pages}</span>
      </p>
      <nav aria-label="Pagination">
        <ul className="pager-pages">
          <li>
            <Step to={page > 1 ? href(page - 1) : null} label="Previous page" icon="previous" />
          </li>
          {pageWindow(page, pages).map((each, i) =>
            each === null ? (
              <li key={`gap-${String(i)}`} className="pager-gap" aria-hidden="true">
                …
              </li>
            ) : (
              <li key={each} className="pager-number">
                <Link
                  className="pager-button"
                  href={href(each)}
                  aria-label={`Page ${String(each)}`}
                  aria-current={each === page ? 'page' : undefined}
                >
                  {each}
                </Link>
              </li>
            ),
          )}
          <li>
            <Step to={page < pages ? href(page + 1) : null} label="Next page" icon="next" />
          </li>
        </ul>
      </nav>
      <label className="pager-size">
        <span className="visually-hidden">Results per page</span>
        <select value={size} onChange={resize}>
          {SIZES.map((each) => (
            <option key={each} value={each}>
              {`${String(each)} / page`}
            </option>
          ))}
        </select>
        <Icon name="down" size={16} />
      </label>
    </div>
  );
}
