import type { Investigation } from '../lib/investigations.ts';
import {
  ACTION_KINDS,
  band,
  cause,
  fallbackNotes,
  PARTS,
  PLAN_KINDS,
  progressLine,
  STATUSES,
  STEP_KINDS,
  stillChanging,
  utcTime,
} from '../lib/report.ts';
import { ILLUSTRATIVE_LABEL, TagBand } from './investigation-list.tsx';
import { LiveRefresh } from './live-refresh.tsx';

// The report refreshes every 3 s while it may still change.
const REFRESH_SECONDS = 3;

type Plan = NonNullable<Investigation['plan']>;
/** Records a human's decision on a plan from the submitted form (a server action). */
export type DecidePlan = (planId: string, form: FormData) => Promise<void>;

// An excerpt over this many lines shows them first and folds the rest behind Show all.
const FOLD_LINES = 12;
const ms = (duration: number) => `${duration.toLocaleString('en-US')} ms`;

/** A tool result as the tool returned it: mono, wrapped, never cut silently. */
function Excerpt({ text }: { text: string }) {
  const lines = text.split('\n');
  if (lines.length <= FOLD_LINES) return <pre className="evidence">{text}</pre>;
  return (
    <>
      <pre className="evidence">{lines.slice(0, FOLD_LINES).join('\n')}</pre>
      <details className="more">
        <summary>Show all {lines.length} lines</summary>
        <pre className="evidence">{lines.slice(FOLD_LINES).join('\n')}</pre>
      </details>
    </>
  );
}

/** The pending plan's two buttons: a form that records the decision, or the demo's dead ones. */
function Decide({ plan, decide, demo }: { plan: Plan; decide?: DecidePlan; demo?: boolean }) {
  if (demo === true) {
    return (
      <div className="decide">
        <p className="buttons">
          <button type="button" className="approve" disabled>
            Approve plan
          </button>
          <button type="button" className="reject" disabled>
            Reject plan
          </button>
        </p>
        <p className="note">The demo records no decision.</p>
      </div>
    );
  }
  if (decide === undefined) return <p>Waiting for a decision.</p>;
  return (
    <form action={decide.bind(null, plan.id)} className="decide">
      <label htmlFor="reason">Reason (optional)</label>
      <textarea id="reason" name="reason" maxLength={2000} rows={2} />
      <p className="buttons">
        <button type="submit" name="verdict" value="approve" className="approve">
          Approve plan
        </button>
        <button type="submit" name="verdict" value="reject" className="reject">
          Reject plan
        </button>
      </p>
    </form>
  );
}

/** A real incident's plan: what it would do, and the decision on it or the way to make one. */
function PlanPanel({
  plan,
  decide,
  demo,
  now,
}: {
  plan: Investigation['plan'];
  decide?: DecidePlan | undefined;
  demo?: boolean | undefined;
  now: number;
}) {
  if (plan?.proposal == null) {
    return (
      <section className="panel" aria-labelledby="plan">
        <h2 id="plan">Plan</h2>
        <p>
          {plan === null
            ? 'No plan was made: plans are only for real incidents, while the planner is on.'
            : `No plan: ${plan.problem ?? 'no reason was recorded'}`}
        </p>
      </section>
    );
  }
  const { proposal } = plan;
  const pending = plan.status === 'pending';
  const decided = plan.status === 'approved' ? 'Approved' : 'Rejected';
  return (
    <section className={pending ? 'panel decision' : 'panel'} aria-labelledby="plan">
      <h2 id="plan">{pending ? 'Decision needed' : 'Plan'}</h2>
      <p className="plan-what">
        {PLAN_KINDS[proposal.kind]} {proposal.target}
      </p>
      <p>{proposal.rationale}</p>
      {proposal.files.length > 0 && (
        <ul className="files" role="list">
          {proposal.files.map((file) => (
            <li key={file}>
              <code>{file}</code>
            </li>
          ))}
        </ul>
      )}
      {pending ? (
        <Decide
          plan={plan}
          {...(decide === undefined ? {} : { decide })}
          {...(demo ? { demo } : {})}
        />
      ) : (
        <p className="decided">
          {decided} at {utcTime(plan.decided_at ?? plan.recorded_at, now)} by{' '}
          {plan.decided_by ?? 'someone unrecorded'}
          {plan.decision_reason === null ? '.' : `: ${plan.decision_reason}`}
        </p>
      )}
      <p className="note">A decision is only recorded: nothing runs from a plan yet.</p>
    </section>
  );
}

export interface ReportProps {
  investigation: Investigation;
  now: number;
  /** The server action that records a decision; without it the plan stays read-only. */
  decide?: DecidePlan;
  /** The demo: the plan buttons show, disabled and with no action. */
  demo?: boolean;
  /** A hand-written sample, labeled as such. */
  illustrative?: boolean;
  /** Refresh while the report may still change, with a progress line; the demo never does. */
  refresh?: boolean;
}

/**
 * One investigation: the verdict tag, then the decision, the cause and its evidence, the steps,
 * the models asked and their misses. In that order on a phone; the decision sits beside the rest
 * on a wide screen.
 */
export function Report({ investigation, now, decide, demo, illustrative, refresh }: ReportProps) {
  const { hypotheses, models, steps, proposed_actions: actions } = investigation;
  const notes = fallbackNotes(models);
  const live =
    refresh === true && stillChanging(investigation, now, investigation.plan !== null) ? (
      <LiveRefresh seconds={REFRESH_SECONDS} status={progressLine(investigation)} />
    ) : undefined;
  const tools = new Map(
    steps.flatMap((step) => (step.tool_call ? [[step.tool_call.id, step.tool_call.name]] : [])),
  );
  return (
    <article className="report">
      <header
        className="tag"
        data-kind={band(investigation).kind}
        data-running={investigation.status === 'running' ? '' : undefined}
      >
        <TagBand item={investigation} />
        <div className="tag-body">
          <h1>{investigation.title}</h1>
          <p className="tag-status">
            {STATUSES[investigation.status]}, opened {utcTime(investigation.created_at, now)}
          </p>
          {illustrative === true && <p className="illustrative">{ILLUSTRATIVE_LABEL}</p>}
          <p className="summary">{investigation.summary ?? 'No report yet.'}</p>
          {investigation.partial && (
            <p>Partial report: the agent stopped at a limit and wrote this report itself.</p>
          )}
          <p className="quiet">
            {investigation.tokens_used.toLocaleString('en-US')} model tokens in its kept steps.
          </p>
        </div>
      </header>

      <div className="report-body">
        <div className="decide-col">
          <PlanPanel plan={investigation.plan} decide={decide} demo={demo} now={now} />
          {actions.length > 0 && (
            <section className="panel" aria-labelledby="proposed">
              <h2 id="proposed">Proposed actions</h2>
              <ul className="stack" role="list">
                {actions.map((action) => (
                  <li key={action.id}>
                    <p className="plan-what">
                      {ACTION_KINDS[action.kind]} on {action.target}
                    </p>
                    <p>{action.rationale}</p>
                    <p className="quiet">Status: {action.status}</p>
                  </li>
                ))}
              </ul>
              <p className="note">Proposals from simulated incidents stay read-only.</p>
            </section>
          )}
        </div>

        <div className="main-col">
          <section aria-labelledby="cause">
            <h2 id="cause">Cause</h2>
            {hypotheses.length === 0 ? (
              <p>None reported.</p>
            ) : (
              <ol className="hypotheses">
                {hypotheses.map((hypothesis) => (
                  <li key={hypothesis.rank}>
                    <p className="hypothesis">
                      <strong>{cause(hypothesis)}</strong>, confidence{' '}
                      {Math.round(hypothesis.confidence * 100)}%
                    </p>
                    <p>{hypothesis.summary}</p>
                    {hypothesis.evidence.map((evidence, i) => (
                      <figure
                        key={`${String(i)}-${evidence.tool_call_id}`}
                        className="evidence-item"
                      >
                        <figcaption>
                          <code>{tools.get(evidence.tool_call_id) ?? 'tool'}</code> result{' '}
                          <code>{evidence.tool_call_id}</code>
                        </figcaption>
                        {evidence.excerpt === '' ? (
                          <p className="quiet">
                            No result kept: the call never returned, or its steps were purged after
                            30 days.
                          </p>
                        ) : (
                          <Excerpt text={evidence.excerpt} />
                        )}
                      </figure>
                    ))}
                  </li>
                ))}
              </ol>
            )}
          </section>

          {investigation.missing_evidence.length > 0 && (
            <section aria-labelledby="missing">
              <h2 id="missing">Missing evidence</h2>
              <ul>
                {investigation.missing_evidence.map((missing) => (
                  <li key={missing}>{missing}</li>
                ))}
              </ul>
            </section>
          )}

          <section aria-labelledby="timeline">
            <h2 id="timeline">Timeline</h2>
            {live}
            {steps.length === 0 ? (
              <p>No steps yet.</p>
            ) : (
              <ol className="timeline" role="list">
                {steps.map((step) => (
                  <li key={step.seq}>
                    <span className="seq">{step.seq}</span>
                    <div>
                      <p className="step-head">
                        <span>{STEP_KINDS[step.kind]}</span>
                        {step.tool_call !== null && (
                          <>
                            {' '}
                            <code>{step.tool_call.name}</code>{' '}
                            <span className="outcome" data-outcome={step.tool_call.status}>
                              {step.tool_call.status}
                            </span>{' '}
                            <span className="quiet">{ms(step.tool_call.duration_ms)}</span>
                          </>
                        )}
                      </p>
                      <p>{step.summary}</p>
                    </div>
                  </li>
                ))}
              </ol>
            )}
          </section>

          <section aria-labelledby="models">
            <h2 id="models">Models</h2>
            {models.length === 0 ? (
              <p>None listed: the report is older than the model list, or not written yet.</p>
            ) : (
              <table className="models" role="table">
                <thead>
                  <tr role="row">
                    <th scope="col" role="columnheader">
                      Part
                    </th>
                    <th scope="col" role="columnheader">
                      Model
                    </th>
                    <th scope="col" role="columnheader">
                      Answered
                    </th>
                    <th scope="col" role="columnheader">
                      Missed
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {models.map((model) => (
                    <tr key={`${model.part}-${model.provider}-${model.model}`} role="row">
                      <td role="cell" data-label="Part">
                        {PARTS[model.part]}
                      </td>
                      <td role="cell" data-label="Model">
                        <code>{model.model}</code> ({model.provider})
                      </td>
                      <td role="cell" data-label="Answered">
                        {model.calls}
                      </td>
                      <td role="cell" data-label="Missed">
                        {model.failed}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>

          <section aria-labelledby="fallbacks">
            <h2 id="fallbacks">Fallback notes</h2>
            {notes.length === 0 ? (
              <p>None: every model asked answered each request.</p>
            ) : (
              <ul>
                {notes.map((note) => (
                  <li key={note}>{note}</li>
                ))}
              </ul>
            )}
          </section>
        </div>
      </div>
    </article>
  );
}
