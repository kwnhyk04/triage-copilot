import Link from 'next/link';
import type { ReactNode } from 'react';

import type { Investigation, Source } from '../lib/investigations.ts';
import {
  ago,
  causeName,
  percent,
  planBadge,
  PLANNING,
  planning,
  statusBadge,
  stillChanging,
  TABS,
  utcTime,
  verdictBadge,
} from '../lib/report.ts';
import { LiveRefresh } from './live-refresh.tsx';
import { Badge } from './ui.tsx';

// The list refreshes every 10 s while one of its items may still change.
const REFRESH_SECONDS = 10;

type Summary = Pick<
  Investigation,
  | 'id'
  | 'title'
  | 'status'
  | 'verdict'
  | 'created_at'
  | 'updated_at'
  | 'source'
  | 'cause'
  | 'plan_status'
  | 'plan_review'
>;

export const ILLUSTRATIVE_LABEL = 'Illustrative sample, not a real incident';
const EMPTY: Record<Source, ReactNode> = {
  live: 'No live incidents yet.',
  // Production keeps no eval runs; the demo shows recorded ones.
  sim: (
    <>
      No eval runs here. <Link href="/demo?source=sim">See the recorded eval runs in the demo</Link>
      .
    </>
  ),
};
// Each list's columns in order; globals.css gives each its base width and an equal share of the
// spare width. Eval runs have no verdict and no plan, so they leave both out.
const COLUMNS: Record<Source, string[]> = {
  live: ['Incident', 'Verdict', 'Cause', 'Confidence', 'Plan', 'Status', 'Opened'],
  sim: ['Run', 'Cause', 'Confidence', 'Status', 'Opened'],
};
const NONE = <span className="faint">None</span>;

export interface ListProps {
  items: readonly Summary[];
  source: Source;
  nextCursor: string | null;
  /** Where reports live: /investigations, or /demo/investigations. */
  basePath: string;
  /** The list's own path, for the next page. */
  listPath?: string;
  now: number;
  /** Hand-written samples, labeled as such. */
  illustrative?: ReadonlySet<string>;
  /** Refresh while an item may still change; the demo never does. */
  refresh?: boolean;
}

/** A live row's plan: its state or Refused, Planning while a refreshing list waits, else None. */
function PlanCell({ item, waiting }: { item: Summary; waiting: boolean }) {
  const state = item.plan_status === null ? null : planBadge(item.plan_status, item.plan_review);
  const shown = waiting ? PLANNING : state;
  return (
    <td data-card={shown === null ? 'hide' : 'aside'}>
      {shown === null ? NONE : <Badge state={shown} />}
    </td>
  );
}

/**
 * Investigations as a table, newest first, with a link to the older ones. Below 720 px each row is
 * a card, its cells placed by data-card: the incident and verdict, then the status, plan and age,
 * then the cause with its confidence.
 */
export function InvestigationList({
  items,
  source,
  nextCursor,
  basePath,
  listPath = '/investigations',
  now,
  illustrative,
  refresh,
}: ListProps) {
  const changing = refresh === true ? items.filter((item) => stillChanging(item, now)).length : 0;
  const live = source === 'live';
  return (
    <>
      {changing > 0 && (
        <LiveRefresh
          seconds={REFRESH_SECONDS}
          status={`${changing === 1 ? '1 investigation is' : `${String(changing)} investigations are`} still running or planning, so this list updates every ${String(REFRESH_SECONDS)} seconds.`}
        />
      )}
      {items.length === 0 ? (
        <p className="empty">{EMPTY[source]}</p>
      ) : (
        <div
          className="table-wrap list card-list"
          role="region"
          aria-label={TABS[source]}
          tabIndex={0}
        >
          <table className={`list-table cards fixed ${live ? 'incidents' : 'runs'}`}>
            <caption className="visually-hidden">{TABS[source]}, newest first</caption>
            <colgroup>
              {COLUMNS[source].map((column) => (
                <col key={column} />
              ))}
            </colgroup>
            <thead>
              <tr>
                {COLUMNS[source].map((column) => (
                  <th
                    key={column}
                    scope="col"
                    className={column === 'Confidence' ? 'center' : undefined}
                  >
                    {column}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.id}>
                  <td data-card="title">
                    <Link href={`${basePath}/${item.id}`}>{item.title}</Link>
                    {illustrative?.has(item.id) === true && (
                      <span className="illustrative">{ILLUSTRATIVE_LABEL}</span>
                    )}
                  </td>
                  {live && (
                    <td data-card="aside">
                      <Badge state={verdictBadge(item)} />
                    </td>
                  )}
                  <td data-card={item.cause === null ? 'hide' : 'wide'}>
                    {item.cause === null ? (
                      NONE
                    ) : (
                      <>
                        {causeName(item.cause)}
                        <span className="phone-only">{`, ${percent(item.cause.confidence)}`}</span>
                      </>
                    )}
                  </td>
                  <td className="figure" data-card="hide">
                    {item.cause === null ? (
                      NONE
                    ) : (
                      <span className="figure-block">{percent(item.cause.confidence)}</span>
                    )}
                  </td>
                  {live && (
                    <PlanCell item={item} waiting={refresh === true && planning(item, now)} />
                  )}
                  <td data-card={live ? 'meta' : 'aside'}>
                    <Badge state={statusBadge(item.status)} />
                  </td>
                  <td data-card="meta">
                    <time dateTime={item.created_at} title={utcTime(item.created_at, now)}>
                      {ago(item.created_at, now)}
                    </time>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {nextCursor !== null && (
        <p className="pager">
          <Link
            className="button"
            href={`${listPath}?source=${source}&cursor=${encodeURIComponent(nextCursor)}`}
          >
            Older
          </Link>
        </p>
      )}
    </>
  );
}
