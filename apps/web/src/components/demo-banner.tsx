/** The public demo's note under the header: saved reports, readable without signing in. */
export function DemoBanner() {
  return (
    <div className="demo-banner" role="region" aria-label="About the demo">
      <p>
        <strong>Demo.</strong> These are saved reports, open without signing in. The eval runs are
        recorded runs on simulated incidents. The three live incidents are illustrative samples
        written by hand, not real incidents. The plan buttons do nothing here.
      </p>
    </div>
  );
}
