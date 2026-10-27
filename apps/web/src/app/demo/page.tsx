import { InvestigationList, SourceTabs, TABS } from '../../components/investigation-list.tsx';
import { demoPage, ILLUSTRATIVE } from '../../demo/index.ts';
import type { Source } from '../../lib/investigations.ts';
import { requestTime } from '../../lib/report.ts';

export const metadata = { title: 'Demo: Triage Copilot' };

/** The demo's list. A signed-in page lands here, with offline=1, when the gateway is down. */
export default async function DemoList({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { source: asked, offline } = await searchParams;
  const source: Source = asked === 'sim' ? 'sim' : 'live';
  const page = demoPage(source);
  return (
    <main id="main" className="page">
      {offline === '1' && (
        <p className="empty" role="status">
          The live system is not answering. It runs on a PC that may be off, so these are the saved
          samples.
        </p>
      )}
      <h1>{TABS[source]}</h1>
      <SourceTabs source={source} listPath="/demo" />
      <InvestigationList
        items={page.items}
        source={source}
        nextCursor={page.next_cursor}
        basePath="/demo/investigations"
        listPath="/demo"
        now={requestTime()}
        illustrative={ILLUSTRATIVE}
      />
    </main>
  );
}
