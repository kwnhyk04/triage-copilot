import type { Investigation } from '../lib/investigations.ts';
import {
  ACTION_KINDS,
  cause,
  causeName,
  fallbackNotes,
  OUTCOMES,
  PARTS,
  percent,
  PLAN_KINDS,
  PLAN_STATES,
  planNote,
  progressLine,
  statusBadge,
  STEP_KINDS,
  stillChanging,
  utcTime,
  verdictBadge,
} from '../lib/report.ts';
import { ILLUSTRATIVE_LABEL } from './investigation-list.tsx';
import { LiveRefresh } from './live-refresh.tsx';
import { Badge } from './ui.tsx';

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
  if (lines.length <= FOLD_LINES) return <pre className="excerpt">{text}</pre>;
  return (
    <>
      <pre className="excerpt">{lines.slice(0, FOLD_LINES).join('\n')}</pre>
      <details className="more">
        <summary>Show all {lines.length} lines</summary>
        <pre className="excerpt">{lines.slice(FOLD_LINES).join('\n')}</pre>
      </details>
    </>
  );
}

/** The pending plan's two buttons: a form that records the decision, or the demo's dead ones. */
function Decide({ plan, decide, demo }: { plan: Plan; decide?: DecidePlan; demo?: boolean }) {
  if (demo === true) {
    return (
      <div className="decide">
        <div className="buttons">
          <button type="button" className="button primary" disabled>
            Approve plan
          </button>
          <button type="button" className="button danger" disabled>
            Reject plan
          </button>
        </div>
        <p className="faint">The demo records no decision.</p>
      </div>
    );
  }
  if (decide === undefined) return <p>Waiting for a decision.</p>;
  return (
    <form action={decide.bind(null, plan.id)} className="decide">
      <label htmlFor="reason">Reason (optional)</label>
      <textarea id="reason" name="reason" maxLength={2000} rows={2} />
      <div className="buttons">
        <button type="submit" name="verdict" value="approve" className="button primary">
          Approve plan
        </button>
        <button type="submit" name="verdict" value="reject" className="button danger">
          Reject plan
        </button>
      </div>
    </form>
  );
}

/** A real incident's plan: what it would do, what approving does, and the decision or its form. */
function PlanPanel({
  plan,
  decide,
  demo,
  now,
}: {
  plan: Plan;
  decide?: DecidePlan | undefined;
  demo?: boolean | undefined;
  now: number;
}) {
  const { proposal } = plan;
  if (proposal == null) {
    return (
      <section className="panel" aria-labelledby="plan">
        <h2 id="plan">Plan</h2>
        <p>{`No plan: ${plan.problem ?? 'no reason was recorded'}`}</p>
      </section>
    );
  }
  const pending = plan.status === 'pending';
  const decided = plan.status === 'approved' ? 'Approved' : 'Rejected';
  return (
    <section className={pending ? 'panel plan pending' : 'panel plan'} aria-labelledby="plan">
      <div className="panel-head">
        <h2 id="plan">Plan</h2>
        <Badge state={PLAN_STATES[plan.status]} />
      </div>
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
      <p className="plan-note">{planNote(proposal)}</p>
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
 * One investigation: its title and badges, then the verdict and cause, the evidence, the steps,
 * the models that served and the missing evidence. The plan follows the verdict on a phone and
 * sits beside the rest on a wide screen.
 */
export function Report({ investigation, now, decide, demo, illustrative, refresh }: ReportProps) {
  const { hypotheses, models, steps, plan, proposed_actions: actions } = investigation;
  const [top, ...others] = hypotheses;
  const cited = hypotheses.filter((hypothesis) => hypothesis.evidence.length > 0);
  const notes = fallbackNotes(models);
  const tools = new Map(
    steps.flatMap((step) => (step.tool_call ? [[step.tool_call.id, step.tool_call.name]] : [])),
  );
  const side = plan !== null || actions.length > 0;
  return (
    <article className="report">
      <header className="report-head">
        <h1>{investigation.title}</h1>
        <p className="badges">
          <Badge state={verdictBadge(investigation)} />
          <Badge state={statusBadge(investigation.status)} />
          {investigation.plan_status !== null && (
            <Badge state={PLAN_STATES[investigation.plan_status]} />
          )}
        </p>
        <p className="meta">
          <span>Opened {utcTime(investigation.created_at, now)}</span>
          <span>
            {investigation.tokens_used.toLocaleString('en-US')} model tokens in its kept steps
          </span>
        </p>
        {illustrative === true && <p className="illustrative">{ILLUSTRATIVE_LABEL}</p>}
        {refresh === true && stillChanging(investigation, now, plan !== null) && (
          <LiveRefresh seconds={REFRESH_SECONDS} status={progressLine(investigation)} />
        )}
      </header>

      <div className={side ? 'report-body with-side' : 'report-body'}>
        <section className="panel" aria-labelledby="verdict">
          <h2 id="verdict">Verdict and cause</h2>
          <p className="lede">{investigation.summary ?? 'No report yet.'}</p>
          {investigation.partial && (
            <p className="notice">
              Partial report: the agent stopped at a limit and wrote this report itself.
            </p>
          )}
          {top === undefined ? (
            <p>No cause reported.</p>
          ) : (
            <div className="cause">
              <p>
                <strong>{causeName(top)}</strong>{' '}
                <span className="muted">{percent(top.confidence)} confidence</span>
              </p>
              <p>{top.summary}</p>
            </div>
          )}
          {others.length > 0 && (
            <>
              <h3>Other causes considered</h3>
              <ul className="others" role="list">
                {others.map((hypothesis) => (
                  <li key={hypothesis.rank}>
                    <p>
                      <strong>{causeName(hypothesis)}</strong>{' '}
                      <span className="muted">{percent(hypothesis.confidence)} confidence</span>
                    </p>
                    <p>{hypothesis.summary}</p>
                  </li>
                ))}
              </ul>
            </>
          )}
          {plan === null && (
            <p className="faint">
              No plan was made: plans are only for real incidents, while the planner is on.
            </p>
          )}
        </section>

        {side && (
          <div className="report-side">
            {plan !== null && <PlanPanel plan={plan} decide={decide} demo={demo} now={now} />}
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
                      <p className="muted">Status: {action.status}</p>
                    </li>
                  ))}
                </ul>
                <p className="faint">Proposals from simulated incidents stay read-only.</p>
              </section>
            )}
          </div>
        )}

        <section className="panel" aria-labelledby="evidence">
          <h2 id="evidence">Evidence</h2>
          {cited.length === 0 ? (
            <p>None cited.</p>
          ) : (
            cited.map((hypothesis) => (
              <div key={hypothesis.rank} className="evidence-group">
                {cited.length > 1 && <h3>For {cause(hypothesis)}</h3>}
                {hypothesis.evidence.map((evidence, i) => (
                  <figure key={`${String(i)}-${evidence.tool_call_id}`} className="evidence-item">
                    <figcaption>
                      <code>{tools.get(evidence.tool_call_id) ?? 'tool'}</code> result{' '}
                      <code>{evidence.tool_call_id}</code>
                    </figcaption>
                    {evidence.excerpt === '' ? (
                      <p className="muted">
                        No result kept: the call never returned, or its steps were purged after 30
                        days.
                      </p>
                    ) : (
                      <Excerpt text={evidence.excerpt} />
                    )}
                  </figure>
                ))}
              </div>
            ))
          )}
        </section>

        <section className="panel" aria-labelledby="steps">
          <h2 id="steps">Steps</h2>
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
                          <code>{step.tool_call.name}</code>
                          <Badge state={OUTCOMES[step.tool_call.status]} />
                          <span className="muted">{ms(step.tool_call.duration_ms)}</span>
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

        <section className="panel" aria-labelledby="models">
          <h2 id="models">Models that served</h2>
          {models.length === 0 ? (
            <p>None listed: the report is older than the model list, or not written yet.</p>
          ) : (
            <div
              className="table-wrap list card-list"
              role="region"
              aria-labelledby="models"
              tabIndex={0}
            >
              <table className="list-table cards fixed models">
                <colgroup>
                  <col />
                  <col />
                  <col />
                  <col />
                </colgroup>
                <thead>
                  <tr>
                    <th scope="col">Part</th>
                    <th scope="col">Model</th>
                    <th scope="col" className="center">
                      Answered
                    </th>
                    <th scope="col" className="center">
                      Missed
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {models.map((model) => (
                    <tr key={`${model.part}-${model.provider}-${model.model}`}>
                      <td data-card="meta">{PARTS[model.part]}</td>
                      <td data-card="title">
                        <code>{model.model}</code> <span className="muted">({model.provider})</span>
                      </td>
                      <td className="figure" data-card="aside">
                        <span className="card-label">Answered</span>
                        <span className="figure-block">{model.calls}</span>
                      </td>
                      <td className="figure" data-card="aside">
                        <span className="card-label">Missed</span>
                        <span className="figure-block">{model.failed}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <h3>Fallback notes</h3>
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

        {investigation.missing_evidence.length > 0 && (
          <section className="panel" aria-labelledby="missing">
            <h2 id="missing">Missing evidence</h2>
            <ul>
              {investigation.missing_evidence.map((missing) => (
                <li key={missing}>{missing}</li>
              ))}
            </ul>
          </section>
        )}
      </div>
    </article>
  );
}
