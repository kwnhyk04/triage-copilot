import Link from 'next/link';

export default function NotFound() {
  return (
    <main id="main" className="page narrow">
      <p className="brand">
        <Link href="/">Triage Copilot</Link>
      </p>
      <h1>Not found</h1>
      <p>No page or report has this address.</p>
      <ul className="actions" role="list">
        <li>
          <Link className="button quiet" href="/investigations">
            Live incidents
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
