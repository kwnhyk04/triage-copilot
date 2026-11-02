import Link from 'next/link';

export default function NotFound() {
  return (
    <main id="main" className="page narrow">
      <section className="panel">
        <h1>Not found</h1>
        <p>No page or report has this address.</p>
        <ul className="actions" role="list">
          <li>
            <Link className="button" href="/investigations">
              Live incidents
            </Link>
          </li>
          <li>
            <Link className="button" href="/demo">
              Open the demo
            </Link>
          </li>
        </ul>
      </section>
    </main>
  );
}
