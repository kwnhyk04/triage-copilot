import type { ReactNode } from 'react';

import type { Investigation } from '../lib/investigations.ts';
import {
  ACTION_KINDS,
  cause,
  causeName,
  counted,
  fallbackNotes,
  ISSUE_KINDS,
  OUTCOMES,
  PARTS,
  percent,
  PLAN_KINDS,
  PLAN_WAIT_MINUTES,
  planBadge,
  PLANNING,
  planning,
  planNote,
  progressLine,
  REVIEW_NOTE,
  REVIEW_TAGS,
  reviewHeading,
  statusBadge,
  STEP_KINDS,
  stillChanging,
  utcTime,
  verdictBadge,
  verdictTag,
} from '../lib/report.ts';
import { Icon, type IconName } from './icons.tsx';
import { ILLUSTRATIVE_LABEL } from './investigation-list.tsx';
import { LiveRefresh } from './live-refresh.tsx';
import { Badge } from './ui.tsx';

// The report refreshes every 3 s while it may still change.
const REFRESH_SECONDS = 3;

type Plan = NonNullable<Investigation['plan']>;
type PlanReview = NonNullable<Plan['review']>;
/** Records a human's decision on a plan from the submitted form (a server action). */
export type DecidePlan = (planId: string, form: FormData) => Promise<void>;

/** A section's head: its round icon, its title, any badge beside it, and a tag with its key fact. */
function PanelHead({
  id,
  title,
  icon,
  tag,
  children,
}: {
  id: string;
  title: string;
  icon: IconName;
  tag?: string | undefined;
  children?: ReactNode;
}) {
  return (
    <div className="panel-head">
      <span className="panel-icon">
        <Icon name={icon} size={18} stroke={1.5} />
      </span>
      <h2 id={id}>{title}</h2>
      {(children !== undefined || tag !== undefined) && (
        <span className="panel-aside">
          {children}
          {tag !== undefined && <span className="panel-tag">{tag}</span>}
        </span>
      )}
    </div>
  );
}

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

/** A plan's review: its verdict, each issue with the step it quotes, and a model's note. */
function Review({ review, pending }: { review: PlanReview; pending: boolean }) {
  const { status, issues } = review;
  const heading = reviewHeading(review, pending);
  const warned = status === 'refuse' || status === 'flag';
  return (
    <div className={warned ? `review ${status === 'refuse' ? 'bad' : 'warn'}` : 'review'}>
      {warned ? <h3>{heading}</h3> : <p>{heading}</p>}
      {issues.length > 0 && (
        <ul className="issues" role="list">
          {issues.map((issue, i) => (
            <li key={`${String(i)}-${issue.kind}`}>
              <p>
                <strong>{ISSUE_KINDS[issue.kind]}</strong>: <q>{issue.quote}</q>
              </p>
              <p className="muted">{issue.reason}</p>
            </li>
          ))}
        </ul>
      )}
      {review.by === 'model' && <p className="muted">{REVIEW_NOTE}</p>}
    </div>
  );
}

/**
 * A real incident's plan: what it would do, its review, what approving does, and the decision or
 * its form. A plan its review refused takes no decision, so it has neither note nor form.
 */
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
  // A gateway older than reviews sends none: such a plan shows as it always did.
  const { proposal, review } = plan;
  if (proposal == null) {
    return (
      <section className="panel" aria-labelledby="plan">
        <PanelHead id="plan" title="Plan" icon="plan" tag="No plan" />
        <p>{`No plan: ${plan.problem ?? 'no reason was recorded'}`}</p>
      </section>
    );
  }
  const refused = review?.status === 'refuse';
  const pending = plan.status === 'pending' && !refused;
  const decided = plan.status === 'approved' ? 'Approved' : 'Rejected';
  const newFiles = new Set(review?.new_files);
  return (
    <section className={pending ? 'panel plan pending' : 'panel plan'} aria-labelledby="plan">
      <PanelHead
        id="plan"
        title="Plan"
        icon="plan"
        tag={review == null || refused ? undefined : REVIEW_TAGS[review.status]}
      >
        <Badge state={planBadge(plan.status, review?.status)} />
      </PanelHead>
      <p className="plan-what">
        {PLAN_KINDS[proposal.kind]} {proposal.target}
      </p>
      <p>{proposal.rationale}</p>
      {proposal.files.length > 0 && (
        <ul className="files" role="list">
          {proposal.files.map((file) => (
            <li key={file}>
              <code>{file}</code>
              {newFiles.has(file) && (
                <>
                  {' '}
                  <span className="muted">new file</span>
                </>
              )}
            </li>
          ))}
        </ul>
      )}
      {review != null && <Review review={review} pending={pending} />}
      {refused ? (
        <p className="decided">A refused plan takes no decision.</p>
      ) : (
        <>
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
        </>
      )}
    </section>
  );
}

const WAITING_NOTE =
  `Still planning a fix. A plan can take up to ${String(PLAN_WAIT_MINUTES)} minutes; ` +
  'this page shows it as soon as it lands.';

/** A real verdict's plan on its way: at effort max the planner thinks for minutes. */
function PlanWaiting() {
  return (
    <section className="panel plan" aria-labelledby="plan">
      <PanelHead id="plan" title="Plan" icon="plan">
        <Badge state={PLANNING} />
      </PanelHead>
      <p>{WAITING_NOTE}</p>
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
  // A page that refreshes says a plan is coming; one that never does cannot show it land.
  const waiting = refresh === true && planning(investigation, now, plan !== null);
  const side = plan !== null || waiting || actions.length > 0;
  return (
    <article className="report">
      <header className="report-head">
        <h1>{investigation.title}</h1>
        <p className="badges">
          <Badge state={verdictBadge(investigation)} />
          <Badge state={statusBadge(investigation.status)} />
          {investigation.plan_status !== null && (
            <Badge state={planBadge(investigation.plan_status, investigation.plan_review)} />
          )}
          {waiting && <Badge state={PLANNING} />}
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
          <PanelHead
            id="verdict"
            title="Verdict and cause"
            icon="verdict"
            tag={verdictTag(investigation, top)}
          />
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
          {plan === null && !waiting && (
            <p className="faint">
              No plan was made: plans are only for real incidents, while the planner is on.
            </p>
          )}
        </section>

        {side && (
          <div className="report-side">
            {waiting && <PlanWaiting />}
            {plan !== null && <PlanPanel plan={plan} decide={decide} demo={demo} now={now} />}
            {actions.length > 0 && (
              <section className="panel" aria-labelledby="proposed">
                <PanelHead
                  id="proposed"
                  title="Proposed actions"
                  icon="actions"
                  tag={counted(actions.length, 'action')}
                />
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
          <PanelHead
            id="evidence"
            title="Evidence"
            icon="evidence"
            tag={counted(
              cited.reduce((sum, hypothesis) => sum + hypothesis.evidence.length, 0),
              'result',
            )}
          />
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
          <PanelHead id="steps" title="Steps" icon="steps" tag={counted(steps.length, 'step')} />
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
          <PanelHead
            id="models"
            title="Models that served"
            icon="models"
            tag={counted(investigation.tokens_used, 'token')}
          />
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
            <PanelHead
              id="missing"
              title="Missing evidence"
              icon="missing"
              tag={`${String(investigation.missing_evidence.length)} missing`}
            />
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
