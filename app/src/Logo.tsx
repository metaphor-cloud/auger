/** The auger, drawn.
 *
 * An auger bores past the surface to bring up what is underneath, which is what a
 * review does to a diff. The shank at the top says tool, the flighting tightens as it
 * winds down, and the point is where it bites.
 *
 * It takes its colour from the text around it, so it belongs to whatever theme is on.
 */

export default function Logo({ size = 18 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 64 64"
      role="img"
      aria-label="Auger"
      className="shrink-0"
    >
      <g
        stroke="currentColor"
        fill="none"
        strokeLinecap="round"
        strokeLinejoin="round"
        opacity={0.95}
      >
        <path d="M25 10 L39 10" strokeWidth={5} />
        <path d="M32 10 L32 42" strokeWidth={4.5} />
        <path d="M32 15 C 15 18.5, 15 24.5, 32 28" strokeWidth={5} />
        <path d="M32 23 C 47 26.5, 46 32, 32 35" strokeWidth={4.6} />
        <path d="M32 31 C 20 34, 20.5 38.5, 32 41" strokeWidth={4.2} />
      </g>
      <path d="M32 39.5 L39 48 L32 59 L25 48 Z" fill="currentColor" />
    </svg>
  );
}
