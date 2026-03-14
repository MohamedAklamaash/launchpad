// Launchpad logo mark — rocket ascending through an orbit ring
// size prop controls the outer dimension; all internals scale proportionally
interface LogoMarkProps {
  size?: number;
  className?: string;
}

export function LogoMark({ size = 28, className = '' }: LogoMarkProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 28 28"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
    >
      {/* outer hex ring */}
      <path
        d="M14 2L24.39 8V20L14 26L3.61 20V8L14 2Z"
        stroke="url(#lp-ring)"
        strokeWidth="1.5"
        strokeLinejoin="round"
        fill="none"
        opacity="0.6"
      />
      {/* rocket body */}
      <path
        d="M14 7C14 7 10 11.5 10 15.5C10 17.5 11.5 19 14 19C16.5 19 18 17.5 18 15.5C18 11.5 14 7 14 7Z"
        fill="url(#lp-body)"
      />
      {/* rocket fins */}
      <path d="M10 15.5L8 18L10 17.5" stroke="url(#lp-fin)" strokeWidth="1.2" strokeLinecap="round" />
      <path d="M18 15.5L20 18L18 17.5" stroke="url(#lp-fin)" strokeWidth="1.2" strokeLinecap="round" />
      {/* exhaust flame */}
      <path
        d="M12.5 19C12.5 19 13 21.5 14 22C15 21.5 15.5 19 15.5 19"
        stroke="url(#lp-flame)"
        strokeWidth="1.2"
        strokeLinecap="round"
        fill="none"
        opacity="0.85"
      />
      {/* window */}
      <circle cx="14" cy="14" r="1.5" fill="white" opacity="0.9" />

      <defs>
        <linearGradient id="lp-ring" x1="3.61" y1="2" x2="24.39" y2="26" gradientUnits="userSpaceOnUse">
          <stop stopColor="#a78bfa" />
          <stop offset="1" stopColor="#6d28d9" />
        </linearGradient>
        <linearGradient id="lp-body" x1="10" y1="7" x2="18" y2="19" gradientUnits="userSpaceOnUse">
          <stop stopColor="#c4b5fd" />
          <stop offset="1" stopColor="#7c3aed" />
        </linearGradient>
        <linearGradient id="lp-fin" x1="8" y1="15" x2="20" y2="18" gradientUnits="userSpaceOnUse">
          <stop stopColor="#a78bfa" />
          <stop offset="1" stopColor="#5b21b6" />
        </linearGradient>
        <linearGradient id="lp-flame" x1="12.5" y1="19" x2="15.5" y2="22" gradientUnits="userSpaceOnUse">
          <stop stopColor="#fbbf24" />
          <stop offset="1" stopColor="#f97316" />
        </linearGradient>
      </defs>
    </svg>
  );
}
