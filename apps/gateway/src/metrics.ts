import type { Sql } from 'postgres';

import type { App } from './config.ts';

/** What Prometheus reads at GET /metrics on the internal listener, in its text format. */
export type Metrics = () => Promise<string>;

/**
 * Each live verdict and each plan that waits for a human, for an hour after the gateway stored it:
 * long enough for a scrape after a restart to see it, and short enough that Alertmanager, which
 * reminds after 24 hours, sends it once (infra/alertmanager; docs/alerts.md). A plan no model
 * reviewed waits for nobody, as it takes no approval. The labels hold uuids, an app, closed lists
 * and a URL's href, so none needs escaping and nothing a model wrote reaches Discord.
 */
export function createMetrics(
  sql: Sql,
  apps: Readonly<Partial<Record<string, App>>>,
  webUrl: string,
): Metrics {
  const tenants = Object.keys(apps);
  const base = webUrl.endsWith('/') ? webUrl : `${webUrl}/`;
  const labels = (investigation: string, tenant: string) =>
    `investigation="${investigation}",app="${apps[tenant] ?? ''}"`;
  const report = (investigation: string) =>
    `report="${new URL(`investigations/${investigation}`, base).href}"`;
  return async () => {
    const verdicts = await sql<
      { investigation_id: string; tenant_id: string; verdict: string; cause: string | null }[]
    >`
      SELECT g.investigation_id, g.tenant_id, r.verdict,
             r.report->'hypotheses'->0->>'root_cause_class' AS cause
      FROM gateway.investigation_reports r
      JOIN gateway.alert_groups g ON g.investigation_id = r.investigation_id
      WHERE r.verdict IS NOT NULL AND r.created_at > now() - interval '1 hour'
        AND g.tenant_id = ANY(${tenants}::uuid[])
      ORDER BY r.created_at`;
    const waiting = await sql<{ investigation_id: string; tenant_id: string; kind: string }[]>`
      SELECT p.investigation_id, p.tenant_id, p.kind
      FROM gateway.plans p LEFT JOIN gateway.plan_decisions d ON d.plan_id = p.id
      WHERE d.plan_id IS NULL AND p.kind IS NOT NULL AND p.review->>'by' = 'model'
        AND p.review->>'status' <> 'refuse' AND p.created_at > now() - interval '1 hour'
        AND p.tenant_id = ANY(${tenants}::uuid[])
      ORDER BY p.created_at`;
    return [
      "# HELP tc_investigation_verdict A live investigation's verdict in its first hour.",
      '# TYPE tc_investigation_verdict gauge',
      ...verdicts.map(
        ({ investigation_id: id, tenant_id: tenant, verdict, cause }) =>
          `tc_investigation_verdict{${labels(id, tenant)},verdict="${verdict}",` +
          `cause="${cause ?? 'unknown'}",${report(id)}} 1`,
      ),
      '# HELP tc_plan_waiting A plan that waits for a human decision, in its first hour.',
      '# TYPE tc_plan_waiting gauge',
      ...waiting.map(
        ({ investigation_id: id, tenant_id: tenant, kind }) =>
          `tc_plan_waiting{${labels(id, tenant)},kind="${kind}",${report(id)}} 1`,
      ),
      '',
    ].join('\n');
  };
}
