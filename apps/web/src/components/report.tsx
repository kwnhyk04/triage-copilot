import type { Investigation } from '../lib/investigations.ts';
import { cause, fallbackNotes, PARTS, PLAN_KINDS, verdictLabel } from '../lib/report.ts';

type Plan = NonNullable<Investigation['plan']>;
/** Records a human's decision on a plan from the submitted form (a server action). */
export type DecidePlan = (planId: string, form: FormData) => Promise<void>;

/** A real incident's plan; while it is pending, and with a way to decide, the form to decide it. */
function PlanSection({ plan, decide }: { plan: Plan; decide: DecidePlan | undefined }) {
  const { proposal } = plan;
  if (proposal === null) return <p>No plan: {plan.problem}</p>;
  const decision = plan.status === 'approved' ? 'Approved' : 'Rejected';
  return (
    <>
      <p>
        <strong>
          {PLAN_KINDS[proposal.kind]} {proposal.target}
        </strong>
        : {proposal.rationale}
      </p>
      {proposal.files.length > 0 && (
        <ul>
          {proposal.files.map((file) => (
            <li key={file}>
              <code>{file}</code>
            </li>
          ))}
        </ul>
      )}
      {plan.status === 'pending' ? (
        <p>Waiting for a decision.</p>
      ) : (
        <p>
          {decision} by {plan.decided_by} at {plan.decided_at} (UTC)
          {plan.decision_reason === null ? '.' : `: ${plan.decision_reason}`}
        </p>
      )}
      {plan.status === 'pending' && decide !== undefined && (
        <form action={decide.bind(null, plan.id)}>
          <p>
            <label>
              Your name <input name="approver" required maxLength={200} />
            </label>
          </p>
          <p>
            <label>
              Reason (optional) <textarea name="reason" maxLength={2000} />
            </label>
          </p>
          <button type="submit" name="verdict" value="approve">
            Approve
          </button>{' '}
          <button type="submit" name="verdict" value="reject">
            Reject
          </button>
        </form>
      )}
      <p>A decision is only recorded: nothing runs from a plan yet.</p>
    </>
  );
}

/**
 * One investigation: verdict, cause and evidence, the plan, the models asked and their misses.
 * Read-only, but for a pending plan's form when `decide` is given.
 */
export function Report({
  investigation,
  decide,
}: {
  investigation: Investigation;
  decide?: DecidePlan;
}) {
  const { hypotheses, models, proposed_actions: actions } = investigation;
  const notes = fallbackNotes(models);
  return (
    <article>
      <h1>{investigation.title}</h1>
      <p>
        <strong>{verdictLabel(investigation.verdict)}</strong>
      </p>
      <p>
        Status {investigation.status}, opened {investigation.created_at} (UTC),{' '}
        {investigation.tokens_used.toLocaleString('en-US')} tokens in its kept steps.
      </p>
      <p>{investigation.summary ?? 'No report yet.'}</p>
      {investigation.partial && (
        <p>Partial report: the agent stopped at a limit and wrote this report itself.</p>
      )}

      <h2>Cause</h2>
      {hypotheses.length === 0 ? (
        <p>None reported.</p>
      ) : (
        <ol>
          {hypotheses.map((hypothesis) => (
            <li key={hypothesis.rank}>
              <p>
                <strong>{cause(hypothesis)}</strong>, confidence{' '}
                {Math.round(hypothesis.confidence * 100)}%: {hypothesis.summary}
              </p>
              <ul>
                {hypothesis.evidence.map((evidence, i) => (
                  <li key={`${String(i)}-${evidence.tool_call_id}`}>
                    Evidence <code>{evidence.tool_call_id}</code>
                    {evidence.excerpt === '' ? (
                      <p>
                        No result kept: the call never returned, or its steps were purged after 30
                        days.
                      </p>
                    ) : (
                      <pre>{evidence.excerpt}</pre>
                    )}
                  </li>
                ))}
              </ul>
            </li>
          ))}
        </ol>
      )}
      {investigation.missing_evidence.length > 0 && (
        <>
          <h2>Missing evidence</h2>
          <ul>
            {investigation.missing_evidence.map((missing) => (
              <li key={missing}>{missing}</li>
            ))}
          </ul>
        </>
      )}
      {actions.length > 0 && (
        <>
          <h2>Proposed actions</h2>
          <ul>
            {actions.map((action) => (
              <li key={action.id}>
                {action.kind} on {action.target}, {action.status}: {action.rationale}
              </li>
            ))}
          </ul>
        </>
      )}

      <h2>Plan</h2>
      {investigation.plan === null ? (
        <p>No plan was made: plans are only for real incidents, while the planner is on.</p>
      ) : (
        <PlanSection plan={investigation.plan} decide={decide} />
      )}

      <h2>Models</h2>
      {models.length === 0 ? (
        <p>None listed: the report is older than the model list, or not written yet.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Part</th>
              <th>Model</th>
              <th>Answered</th>
              <th>Missed</th>
            </tr>
          </thead>
          <tbody>
            {models.map((model) => (
              <tr key={`${model.part}-${model.provider}-${model.model}`}>
                <td>{PARTS[model.part]}</td>
                <td>
                  {model.model} ({model.provider})
                </td>
                <td>{model.calls}</td>
                <td>{model.failed}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h2>Fallback notes</h2>
      {notes.length === 0 ? (
        <p>None: every model asked answered each request.</p>
      ) : (
        <ul>
          {notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      )}
    </article>
  );
}
