import { redirect } from 'next/navigation';

import { DemoBanner } from '../../components/demo-banner.tsx';
import { SiteHeader } from '../../components/header.tsx';
import { InvestigationList } from '../../components/investigation-list.tsx';
import { demoPage, ILLUSTRATIVE } from '../../demo/index.ts';
import type { Source } from '../../lib/investigations.ts';
import { offsetOf, pageCount, pageHref, paging } from '../../lib/paging.ts';
import { requestTime, TABS } from '../../lib/report.ts';

export const metadata = { title: 'Demo: Triage Copilot' };

/** The demo's list. A signed-in page lands here, with offline=1, when the gateway is down. */
export default async function DemoList({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const query = await searchParams;
  const source: Source = query.source === 'sim' ? 'sim' : 'live';
  const offline = query.offline === '1';
  const { page, size } = paging(query);
  const list = demoPage(source, { offset: offsetOf(page, size), limit: size });
  const pages = pageCount(list.total, size);
  if (page > pages) {
    const kept = new URLSearchParams({ source, ...(offline ? { offline: '1' } : {}) });
    redirect(pageHref('/demo', kept, pages, size));
  }
  return (
    <>
      <SiteHeader />
      <DemoBanner />
      <main id="main" className="page">
        {offline && (
          <p className="notice" role="status">
            The live system is not answering. It runs on a PC that may be off, so these are the
            saved samples.
          </p>
        )}
        <h1>{TABS[source]}</h1>
        <InvestigationList
          items={list.items}
          source={source}
          total={list.total}
          page={page}
          size={size}
          basePath="/demo/investigations"
          now={requestTime()}
          illustrative={ILLUSTRATIVE}
        />
      </main>
    </>
  );
}
