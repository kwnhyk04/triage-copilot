import { redirect } from 'next/navigation';

import { SiteHeader } from '../../../components/header.tsx';
import { InvestigationList } from '../../../components/investigation-list.tsx';
import { readInvestigations } from '../../../lib/data.ts';
import type { Source } from '../../../lib/investigations.ts';
import { offsetOf, pageCount, pageHref, paging } from '../../../lib/paging.ts';
import { requestTime, TABS } from '../../../lib/report.ts';

export const metadata = { title: 'Investigations: Triage Copilot' };

/** Live incidents (the default) or eval runs, newest first, a numbered page at a time. */
export default async function Investigations({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const query = await searchParams;
  const source: Source = query.source === 'sim' ? 'sim' : 'live';
  const { page, size } = paging(query);
  const list = await readInvestigations(source, { offset: offsetOf(page, size), limit: size });
  const pages = pageCount(list.total, size);
  // A page past the end, from an old or edited link, shows the last one.
  if (page > pages) redirect(pageHref('/investigations', `source=${source}`, pages, size));
  return (
    <>
      <SiteHeader />
      <main id="main" className="page">
        <h1>{TABS[source]}</h1>
        <InvestigationList
          items={list.items}
          source={source}
          total={list.total}
          page={page}
          size={size}
          basePath="/investigations"
          now={requestTime()}
          refresh
        />
      </main>
    </>
  );
}
