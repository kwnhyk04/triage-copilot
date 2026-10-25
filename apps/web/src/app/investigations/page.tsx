import Link from 'next/link';
import { connection } from 'next/server';

import { loadWebConfig } from '../../lib/config.ts';
import { listInvestigations } from '../../lib/investigations.ts';
import { VERDICTS } from '../../lib/report.ts';

/** Every investigation, newest first, 25 a page; each links to its report. */
export default async function Investigations({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  // Render per request: the list is live data and GATEWAY_URL exists only at runtime.
  await connection();
  const { cursor } = await searchParams;
  const page = await listInvestigations(
    loadWebConfig(),
    typeof cursor === 'string' ? cursor : undefined,
  );
  return (
    <main>
      <h1>Investigations</h1>
      {page.items.length === 0 ? (
        <p>None yet.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Opened (UTC)</th>
              <th>Alert</th>
              <th>Status</th>
              <th>Verdict</th>
            </tr>
          </thead>
          <tbody>
            {page.items.map((item) => (
              <tr key={item.id}>
                <td>{item.created_at}</td>
                <td>
                  <Link href={`/investigations/${item.id}`}>{item.title}</Link>
                </td>
                <td>{item.status}</td>
                <td>{item.verdict === null ? 'None' : VERDICTS[item.verdict]}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {page.next_cursor !== null && (
        <p>
          <Link href={`/investigations?cursor=${encodeURIComponent(page.next_cursor)}`}>Older</Link>
        </p>
      )}
    </main>
  );
}
