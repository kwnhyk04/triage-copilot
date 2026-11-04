// Line icons on a 24-unit grid, drawn for TC: the three theme choices, the phone menu, the pager's
// chevrons, and one per report section.
const ICONS = {
  system: [
    'M4 4h16a1 1 0 0 1 1 1v10a1 1 0 0 1 -1 1h-16a1 1 0 0 1 -1 -1v-10a1 1 0 0 1 1 -1z',
    'M8 20h8',
    'M12 16v4',
  ],
  light: [
    'M12 8a4 4 0 1 0 0 8a4 4 0 1 0 0 -8z',
    'M12 2v2',
    'M12 20v2',
    'M2 12h2',
    'M20 12h2',
    'M4.9 4.9l1.4 1.4',
    'M17.7 17.7l1.4 1.4',
    'M4.9 19.1l1.4 -1.4',
    'M17.7 6.3l1.4 -1.4',
  ],
  dark: ['M20 14.5a8 8 0 1 1 -10.5 -10.5a7.5 7.5 0 0 0 10.5 10.5z'],
  menu: ['M4 6h16', 'M4 12h16', 'M4 18h16'],
  previous: ['M15 6l-6 6l6 6'],
  next: ['M9 6l6 6l-6 6'],
  down: ['M6 9l6 6l6 -6'],
  // The verdict: a target, its centre marked.
  verdict: [
    'M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0 -18z',
    'M12 8a4 4 0 1 0 0 8a4 4 0 1 0 0 -8z',
    'M12 12h.01',
  ],
  // The plan: two ticked lines.
  plan: ['M4 7l2 2l3 -3', 'M12 8h8', 'M4 15l2 2l3 -3', 'M12 16h8'],
  // Proposed actions: a bolt.
  actions: ['M13 3l-8 11h7l-1 7l8 -11h-7z'],
  // Evidence: a lens.
  evidence: ['M10.5 4a6.5 6.5 0 1 0 0 13a6.5 6.5 0 1 0 0 -13z', 'M15.5 15.5l4.5 4.5'],
  // Steps: a line through two stops.
  steps: [
    'M12 3v3',
    'M12 10v4',
    'M12 18v3',
    'M12 6a2 2 0 1 0 0 4a2 2 0 1 0 0 -4z',
    'M12 14a2 2 0 1 0 0 4a2 2 0 1 0 0 -4z',
  ],
  // The models: a chip.
  models: [
    'M7 7h10v10h-10z',
    'M10 10h4v4h-4z',
    'M10 3v4',
    'M14 3v4',
    'M10 17v4',
    'M14 17v4',
    'M3 10h4',
    'M3 14h4',
    'M17 10h4',
    'M17 14h4',
  ],
  // Missing evidence: a ring with a mark.
  missing: ['M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0 -18z', 'M12 8v4', 'M12 16h.01'],
};
export type IconName = keyof typeof ICONS;

export function Icon({
  name,
  size = 20,
  stroke = 1.75,
}: {
  name: IconName;
  size?: number;
  stroke?: number;
}) {
  return (
    <svg
      className="icon"
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth={stroke}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {ICONS[name].map((d) => (
        <path key={d} d={d} />
      ))}
    </svg>
  );
}
