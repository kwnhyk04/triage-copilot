import type { Investigation } from '../lib/investigations.ts';
import { cause, fallbackNotes, PARTS, verdictLabel } from '../lib/report.ts';

/** One investigation, read-only: verdict, cause and evidence, the models asked and their misses. */
export function Report({ investigation }: { investigation: Investigation }) {
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
