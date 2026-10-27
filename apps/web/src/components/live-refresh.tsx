'use client';

import { useRouter } from 'next/navigation';
import { useEffect, useState } from 'react';

/**
 * Renders the page again on the server every `seconds` while the tab is visible, until paused.
 * The server stops rendering it once nothing on the page can change, which ends the polling.
 */
export function LiveRefresh({ seconds, status }: { seconds: number; status: string }) {
  const router = useRouter();
  const [paused, setPaused] = useState(false);
  useEffect(() => {
    if (paused) return undefined;
    const timer = setInterval(() => {
      if (document.visibilityState === 'visible') router.refresh();
    }, seconds * 1000);
    return () => {
      clearInterval(timer);
    };
  }, [paused, router, seconds]);
  return (
    <div className="live" data-every={seconds}>
      <p role="status">{status}</p>
      <button
        type="button"
        className="button quiet"
        onClick={() => {
          setPaused(!paused);
        }}
      >
        {paused ? 'Resume updates' : 'Pause updates'}
      </button>
    </div>
  );
}
