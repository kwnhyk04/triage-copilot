import { SiteHeader } from '../../../components/header.tsx';
import { InvestigationList } from '../../../components/investigation-list.tsx';
import { readInvestigations } from '../../../lib/data.ts';
import type { Source } from '../../../lib/investigations.ts';
import { requestTime, TABS } from '../../../lib/report.ts';

export const metadata = { title: 'Investigations: Triage Copilot' };

/** Live incidents (the default) or eval runs, newest first, 25 a page; the header links both. */
export default async function Investigations({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { cursor, source: asked } = await searchParams;
  const source: Source = asked === 'sim' ? 'sim' : 'live';
  const page = await readInvestigations(source, typeof cursor === 'string' ? cursor : undefined);
  const now = requestTime();
  return (
    <>
      <SiteHeader />
      <main id="main" className="page">
        <h1>{TABS[source]}</h1>
        <InvestigationList
          items={page.items}
          source={source}
          nextCursor={page.next_cursor}
          basePath="/investigations"
          now={now}
          refresh
        />
      </main>
    </>
  );
}
