import Link from 'next/link';
import type { ReactNode } from 'react';

import { EVAL_RUNS } from '../demo/index.ts';
import { HOME } from '../lib/guards.ts';
import { ACTION_KINDS, causeName, percent, PLAN_STATES, verdictBadge } from '../lib/report.ts';
import { Badge } from './ui.tsx';

/** The recorded eval run the hero shows: a bad deploy on checkout, from the saved demo data. */
export const HERO_RUN_ID = '59787641-d31c-47d7-a1de-f06c7af65275';

// A logs result opens with its count, such as "100 lines:"; the first line after it is the quote.
const COUNT_LINE = /^\d+ lines?:$/;

/** What the hero's card shows of the saved run, every word from its report; undefined without it. */
export function heroReport() {
  const run = EVAL_RUNS.find((item) => item.id === HERO_RUN_ID);
  const top = run?.hypotheses[0];
  const action = run?.proposed_actions[0];
  if (run === undefined || top === undefined || action === undefined) return undefined;
  const tools = new Map(
    run.steps.flatMap((step) => (step.tool_call ? [[step.tool_call.id, step.tool_call.name]] : [])),
  );
  const to = action.parameters.to_version;
  return {
    href: `/demo/investigations/${run.id}`,
    title: run.title,
    verdict: verdictBadge(run),
    cause: causeName(top),
    confidence: percent(top.confidence),
    evidence: top.evidence.map((item) => ({
      tool: tools.get(item.tool_call_id) ?? 'tool',
      line: item.excerpt.split('\n').find((line) => !COUNT_LINE.test(line)) ?? '',
    })),
    action: {
      what: `${ACTION_KINDS[action.kind]} on ${action.target}`,
      to: typeof to === 'string' ? to : undefined,
      state: PLAN_STATES[action.status],
    },
  };
}

/**
 * The landing's hero, after two 21st.dev heroes (docs/third-party.md): the headline, one button,
 * a saved report in a framed panel, and a glowing horizon at the foot.
 */
export function Hero({ signedIn, note }: { signedIn: boolean; note?: ReactNode }) {
  const report = heroReport();
  return (
    <section className="hero" aria-labelledby="hero-title">
      {note}
      <h1 id="hero-title">An AI on-call that investigates first, and lets a human decide</h1>
      <p className="hero-sub">
        It reads the metrics, logs and deploys behind an alert with read-only tools, names the
        likely cause with its evidence, and drafts a fix that a person approves or rejects.
      </p>
      <Link className="button primary large hero-cta" href={signedIn ? HOME : '/demo'}>
        {signedIn ? 'Open Live incidents' : 'Open the demo'}
      </Link>
      {report && (
        <div className="hero-preview">
          <article className="hero-report" aria-labelledby="hero-report-title">
            <p className="faint">Recorded eval run on a simulated incident</p>
            <div className="hero-report-head">
              <h2 id="hero-report-title">
                <Link href={report.href}>{report.title}</Link>
              </h2>
              <Badge state={report.verdict} />
            </div>
            <p className="hero-cause">
              {report.cause} <span className="muted">· {report.confidence} confidence</span>
            </p>
            <h3>Evidence</h3>
            <ul className="hero-evidence" role="list">
              {report.evidence.map((item) => (
                <li key={item.tool} title={item.line}>
                  <code>{item.tool}</code>
                  <span className="hero-line">{item.line}</span>
                </li>
              ))}
            </ul>
            <h3>Proposed action</h3>
            <div className="hero-action">
              <p>
                {report.action.what}
                {report.action.to !== undefined && (
                  <span className="muted"> to {report.action.to}</span>
                )}
              </p>
              <Badge state={report.action.state} />
            </div>
          </article>
        </div>
      )}
      <div className="hero-horizon" aria-hidden="true"></div>
    </section>
  );
}
