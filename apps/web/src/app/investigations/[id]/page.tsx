import Link from 'next/link';
import { notFound } from 'next/navigation';
import { connection } from 'next/server';

import { Report } from '../../../components/report.tsx';
import { loadWebConfig } from '../../../lib/config.ts';
import { getInvestigation } from '../../../lib/investigations.ts';

/** One investigation's report, read-only. */
export default async function InvestigationReport({ params }: { params: Promise<{ id: string }> }) {
  // Render per request: the report is live data and GATEWAY_URL exists only at runtime.
  await connection();
  const { id } = await params;
  const investigation = await getInvestigation(loadWebConfig(), id);
  if (investigation === undefined) notFound();
  return (
    <main>
      <p>
        <Link href="/investigations">All investigations</Link>
      </p>
      <Report investigation={investigation} />
    </main>
  );
}
