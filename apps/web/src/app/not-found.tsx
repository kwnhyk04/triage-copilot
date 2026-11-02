import Link from 'next/link';

import { SiteHeader } from '../components/header.tsx';

export default function NotFound() {
  return (
    <>
      <SiteHeader />
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
    </>
  );
}
