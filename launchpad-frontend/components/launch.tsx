'use client';

import { Check } from 'lucide-react';
import { motion } from 'framer-motion';

export type LaunchStage = 'configure' | 'ignition' | 'provision' | 'orbit';

const STAGES: { key: LaunchStage; label: string; sub: string }[] = [
  { key: 'configure', label: 'Configure', sub: 'Pre-flight' },
  { key: 'ignition', label: 'Ignition', sub: 'Bootstrap' },
  { key: 'provision', label: 'Provision', sub: 'Building' },
  { key: 'orbit', label: 'Live', sub: 'In orbit' },
];

export function LaunchSequence({ current }: { current: LaunchStage }) {
  const idx = STAGES.findIndex((s) => s.key === current);
  return (
    <div className="flex items-center">
      {STAGES.map((s, i) => {
        const state = i < idx ? 'done' : i === idx ? 'active' : 'todo';
        return (
          <div key={s.key} className={`flex items-center ${i < STAGES.length - 1 ? 'flex-1' : ''}`}>
            <div className="flex items-center gap-2.5">
              <span
                className={`relative flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border text-[10px] font-mono ${
                  state === 'done'
                    ? 'border-brand/40 bg-brand-soft text-brand'
                    : state === 'active'
                      ? 'border-brand bg-brand text-primary-foreground'
                      : 'border-hairline bg-surface-1 text-muted-foreground'
                }`}
              >
                {state === 'done' ? <Check className="w-3.5 h-3.5" /> : String(i + 1).padStart(2, '0')}
                {state === 'active' && <span className="absolute inset-0 rounded-lg bg-brand/40 animate-ping" />}
              </span>
              <div className="hidden sm:block leading-tight">
                <p className={`text-xs font-medium ${state === 'todo' ? 'text-muted-foreground' : 'text-foreground'}`}>{s.label}</p>
                <p className="font-mono text-[9px] uppercase tracking-[0.16em] text-muted-foreground/70">{s.sub}</p>
              </div>
            </div>
            {i < STAGES.length - 1 && <span className={`mx-3 h-px flex-1 ${i < idx ? 'bg-brand/40' : 'bg-hairline'}`} />}
          </div>
        );
      })}
    </div>
  );
}

export function PreflightMeter({ ready, total, label }: { ready: number; total: number; label?: string }) {
  const pct = total ? Math.min(100, Math.round((ready / total) * 100)) : 0;
  const go = total > 0 && ready >= total;
  return (
    <div className="rounded-xl panel-inset px-4 py-3">
      <div className="mb-2 flex items-center justify-between">
        <span className="eyebrow">{go ? 'All systems go' : label ?? 'Pre-flight check'}</span>
        <span className={`font-mono text-xs ${go ? 'text-success' : 'text-muted-foreground'}`}>{ready}/{total} ready</span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-surface-3">
        <motion.div
          className={`h-full rounded-full ${go ? 'bg-success' : 'bg-brand'}`}
          initial={false}
          animate={{ width: `${pct}%` }}
          transition={{ type: 'spring', stiffness: 200, damping: 26 }}
        />
      </div>
    </div>
  );
}
