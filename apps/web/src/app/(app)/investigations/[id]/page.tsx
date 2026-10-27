import Link from 'next/link';
import { notFound } from 'next/navigation';

import { TABS } from '../../../../components/investigation-list.tsx';
import { Report } from '../../../../components/report.tsx';
import { readInvestigation } from '../../../../lib/data.ts';
import { requestTime } from '../../../../lib/report.ts';
import { decidePlanAction } from './actions.ts';

export const metadata = { title: 'Report: Triage Copilot' };

/** One investigation's report, read-only but for its plan's decision. */
export default async function InvestigationReport({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const investigation = await readInvestigation(id);
  if (investigation === undefined) notFound();
  const now = requestTime();
  return (
    <main id="main" className="page">
      <p className="back">
        <Link href={`/investigations?source=${investigation.source}`}>
          {TABS[investigation.source]}
        </Link>
      </p>
      <Report
        investigation={investigation}
        now={now}
        decide={decidePlanAction.bind(null, investigation.id)}
        refresh
      />
    </main>
  );
}
