// Line icons on a 24-unit grid, drawn for TC: the three theme choices and the phone menu.
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
};
export type IconName = keyof typeof ICONS;

export function Icon({ name, size = 20 }: { name: IconName; size?: number }) {
  return (
    <svg
      className="icon"
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
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
