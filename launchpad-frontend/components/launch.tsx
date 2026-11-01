import { Check } from 'lucide-react';
import { cn } from '@/lib/utils';

export type LaunchStage = 'configure' | 'connect' | 'provision' | 'live';

const STAGES: { key: LaunchStage; label: string }[] = [
  { key: 'configure', label: 'Configure' },
  { key: 'connect', label: 'Connect' },
  { key: 'provision', label: 'Provision' },
  { key: 'live', label: 'Live' },
];

export function LaunchSequence({ current }: { current: LaunchStage }) {
  const idx = STAGES.findIndex((s) => s.key === current);
  return (
    <ol className="flex items-center" aria-label="Progress">
      {STAGES.map((s, i) => {
        const state = i < idx ? 'done' : i === idx ? 'active' : 'todo';
        return (
          <li
            key={s.key}
            aria-current={state === 'active' ? 'step' : undefined}
            className={cn('flex items-center', i < STAGES.length - 1 && 'flex-1')}
          >
            <span className="flex items-center gap-2">
              <span
                className={cn(
                  'flex size-5 shrink-0 items-center justify-center rounded-full border text-[11px] font-medium tabular-nums',
                  state === 'done' && 'border-brand bg-brand text-background',
                  state === 'active' && 'border-brand text-brand',
                  state === 'todo' && 'border-border text-muted-foreground',
                )}
              >
                {state === 'done' ? <Check className="size-3" /> : i + 1}
              </span>
              <span className={cn('hidden text-xs sm:block', state === 'todo' ? 'text-muted-foreground' : 'text-foreground')}>
                {s.label}
              </span>
            </span>
            {i < STAGES.length - 1 && <span className={cn('mx-3 h-px flex-1', i < idx ? 'bg-brand' : 'bg-border')} />}
          </li>
        );
      })}
    </ol>
  );
}
