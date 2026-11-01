interface LogoMarkProps {
  size?: number;
  className?: string;
}

export function LogoMark({ size = 24, className = '' }: LogoMarkProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
      className={className}
    >
      <rect width="24" height="24" rx="6" fill="var(--primary)" />
      <path d="M12 5.5 17.5 14h-11L12 5.5Z" fill="white" />
      <rect x="6.5" y="16.5" width="11" height="1.75" rx="0.875" fill="white" fillOpacity="0.7" />
    </svg>
  );
}
