import Link from 'next/link';

import type { Investigation, Source } from '../lib/investigations.ts';
import { ago, band, causeBrief, STATUSES, stillChanging } from '../lib/report.ts';
import { LiveRefresh } from './live-refresh.tsx';

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
>;

export const ILLUSTRATIVE_LABEL = 'Illustrative sample, not a real incident';
const EMPTY: Record<Source, string> = {
  live: 'No live incidents yet.',
  sim: 'No eval runs yet.',
};
export const TABS: Record<Source, string> = { live: 'Live incidents', sim: 'Eval runs' };

/** The tag's band, the punched hole and its first words, with a chip while a plan waits. */
export function TagBand({ item }: { item: Summary }) {
  return (
    <div className="band">
      <span className="hole" aria-hidden="true" />
      <span className="band-label">{band(item).label}</span>
      {item.plan_status === 'pending' && <span className="chip">Decision needed</span>}
    </div>
  );
}

/** The two tabs, Live incidents and Eval runs, of the list at `listPath`. */
export function SourceTabs({ source, listPath }: { source: Source; listPath: string }) {
  return (
    <nav className="tabs" aria-label="Investigations">
      <ul role="list">
        {(Object.keys(TABS) as Source[]).map((tab) => (
          <li key={tab}>
            <Link
              href={`${listPath}?source=${tab}`}
              aria-current={tab === source ? 'page' : undefined}
            >
              {TABS[tab]}
            </Link>
          </li>
        ))}
      </ul>
    </nav>
  );
}

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

/** Investigations as triage tags, newest first, with a link to the older ones. */
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
  return (
    <>
      {changing > 0 && (
        <LiveRefresh
          seconds={REFRESH_SECONDS}
          status={`${changing === 1 ? '1 investigation is' : `${String(changing)} investigations are`} still running, so this list updates every ${String(REFRESH_SECONDS)} seconds.`}
        />
      )}
      {items.length === 0 ? (
        <p className="empty">{EMPTY[source]}</p>
      ) : (
        <ul className="tags" role="list">
          {items.map((item) => {
            const brief = causeBrief(item.cause);
            return (
              <li
                key={item.id}
                className="tag"
                data-kind={band(item).kind}
                data-running={item.status === 'running' ? '' : undefined}
              >
                <TagBand item={item} />
                <div className="tag-body">
                  <h2 className="tag-title">
                    <Link href={`${basePath}/${item.id}`}>{item.title}</Link>
                  </h2>
                  {brief !== undefined && <p className="tag-cause">{brief}</p>}
                  <p className="tag-status">
                    {STATUSES[item.status]}, opened {ago(item.created_at, now)}
                  </p>
                  {illustrative?.has(item.id) && (
                    <p className="illustrative">{ILLUSTRATIVE_LABEL}</p>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}
      {nextCursor !== null && (
        <p className="older">
          <Link href={`${listPath}?source=${source}&cursor=${encodeURIComponent(nextCursor)}`}>
            Older
          </Link>
        </p>
      )}
    </>
  );
}
