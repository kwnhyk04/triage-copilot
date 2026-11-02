import Link from 'next/link';
import { notFound } from 'next/navigation';

import { DemoBanner } from '../../../../components/demo-banner.tsx';
import { SiteHeader } from '../../../../components/header.tsx';
import { Report } from '../../../../components/report.tsx';
import { demoInvestigation, ILLUSTRATIVE } from '../../../../demo/index.ts';
import { requestTime, TABS } from '../../../../lib/report.ts';

export const metadata = { title: 'Demo report: Triage Copilot' };

/** One saved report. Its plan buttons show, disabled, and record nothing. */
export default async function DemoReport({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const investigation = demoInvestigation(id);
  if (investigation === undefined) notFound();
  return (
    <>
      <SiteHeader />
      <DemoBanner />
      <main id="main" className="page">
        <p className="crumbs">
          <Link href={`/demo?source=${investigation.source}`}>{TABS[investigation.source]}</Link>
        </p>
        <Report
          investigation={investigation}
          now={requestTime()}
          demo
          illustrative={ILLUSTRATIVE.has(investigation.id)}
        />
      </main>
    </>
  );
}
