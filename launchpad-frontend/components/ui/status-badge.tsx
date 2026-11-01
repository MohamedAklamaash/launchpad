import { cn } from '@/lib/utils';

type Tone = 'success' | 'brand' | 'warning' | 'destructive' | 'muted';

const TONE: Record<string, { tone: Tone; pulse?: boolean; label: string }> = {
  ACTIVE: { tone: 'success', label: 'Active' },
  UPDATING: { tone: 'brand', pulse: true, label: 'Updating' },
  PROVISIONING: { tone: 'brand', pulse: true, label: 'Provisioning' },
  PENDING: { tone: 'warning', pulse: true, label: 'Pending' },
  ERROR: { tone: 'destructive', label: 'Error' },
  DESTROYING: { tone: 'warning', pulse: true, label: 'Destroying' },
  DESTROYED: { tone: 'muted', label: 'Destroyed' },
  CREATED: { tone: 'warning', pulse: true, label: 'Created' },
  BUILDING: { tone: 'brand', pulse: true, label: 'Building' },
  PUSHING_IMAGE: { tone: 'brand', pulse: true, label: 'Pushing image' },
  DEPLOYING: { tone: 'brand', pulse: true, label: 'Deploying' },
  SLEEPING: { tone: 'muted', label: 'Sleeping' },
  FAILED: { tone: 'destructive', label: 'Failed' },
  DELETING: { tone: 'warning', pulse: true, label: 'Deleting' },
  DELETED: { tone: 'muted', label: 'Deleted' },
};

const DOT: Record<Tone, string> = {
  success: 'bg-success',
  brand: 'bg-brand',
  warning: 'bg-warning',
  destructive: 'bg-destructive',
  muted: 'bg-muted-foreground',
};

const TEXT: Record<Tone, string> = {
  success: 'text-success',
  brand: 'text-brand',
  warning: 'text-warning',
  destructive: 'text-destructive',
  muted: 'text-muted-foreground',
};

export function StatusBadge({ status, className }: { status: string; className?: string }) {
  const s = TONE[status] ?? { tone: 'muted' as Tone, label: status };
  return (
    <span className={cn('inline-flex items-center gap-1.5 text-xs font-medium', TEXT[s.tone], className)}>
      <span className={cn('size-1.5 rounded-full', DOT[s.tone], s.pulse && 'animate-pulse')} />
      {s.label}
    </span>
  );
}
