import Link from 'next/link';

/** The public landing: what Triage Copilot does, sign-in, and the demo. It reads nothing. */
export default function Landing() {
  return (
    <main id="main" className="page narrow landing">
      <p className="brand">Triage Copilot</p>
      <h1>Is this alert a real incident?</h1>
      <p className="lede">
        Triage Copilot reads the logs, metrics and recent changes behind an alert. It says whether
        the alert shows a real incident, names the likely cause with its evidence, and proposes a
        fix that a person approves or rejects. Nothing runs without that decision.
      </p>
      <ul className="actions" role="list">
        <li>
          <Link className="button" href="/login">
            Sign in
          </Link>
        </li>
        <li>
          <Link className="button quiet" href="/demo">
            Open the demo
          </Link>
        </li>
      </ul>
    </main>
  );
}
