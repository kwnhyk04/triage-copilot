import { useId } from 'react';

// The Triage Copilot mark, redrawn from the logo's PNG: a pulse line with one flagged peak. The
// line takes the text colour; the peak above the baseline, cut level as in the PNG, is --brand.
// app/icon.svg and docs/brand/ draw the same path.
const PULSE = 'M4 26.5H13L19 38.5L29.5 9.5L36.5 26.5H44';

export function LogoMark() {
  const peak = useId();
  return (
    <svg
      className="logo-mark"
      width="28"
      height="28"
      viewBox="0 0 48 48"
      fill="none"
      strokeWidth="4.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      <clipPath id={peak}>
        <rect width="48" height="23" />
      </clipPath>
      <path d={PULSE} stroke="currentColor" />
      <path d={PULSE} stroke="var(--brand)" clipPath={`url(#${peak})`} />
    </svg>
  );
}
