'use client';

import { useRef } from 'react';
import { Input } from '@/components/ui/input';
import { Plus, X, Upload, ClipboardPaste } from 'lucide-react';

type EnvRow = [string, string];

interface Props {
  envs: EnvRow[];
  onChange: (envs: EnvRow[]) => void;
  hideTitle?: boolean;
}

function parseEnvText(text: string): EnvRow[] {
  const rows: EnvRow[] = [];
  for (const raw of text.split('\n')) {
    const line = raw.trim();
    if (!line || line.startsWith('#')) continue;
    const eq = line.indexOf('=');
    if (eq === -1) continue;
    const key = line.slice(0, eq).trim();
    let val = line.slice(eq + 1).trim();
    if ((val.startsWith('"') && val.endsWith('"')) || (val.startsWith("'") && val.endsWith("'")))
      val = val.slice(1, -1);
    if (key) rows.push([key, val]);
  }
  return rows;
}

export function EnvEditor({ envs, onChange, hideTitle }: Props) {
  const fileRef = useRef<HTMLInputElement>(null);

  const setRow = (i: number, k: string, v: string) =>
    onChange(envs.map((row, idx) => (idx === i ? [k, v] : row)));

  const handlePaste = (e: React.ClipboardEvent) => {
    const text = e.clipboardData.getData('text');
    // Only bulk-parse if it looks like a .env file (has KEY=VALUE lines)
    if (!text.includes('=')) return;
    const parsed = parseEnvText(text);
    if (parsed.length < 2) return;
    e.preventDefault();
    // Merge: keep existing rows that aren't overridden, append new ones
    const map = new Map(envs.filter(([k]) => k));
    for (const [k, v] of parsed) map.set(k, v);
    onChange(Array.from(map.entries()));
  };

  const handleFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => {
      const parsed = parseEnvText(ev.target?.result as string);
      const map = new Map(envs.filter(([k]) => k));
      for (const [k, v] of parsed) map.set(k, v);
      onChange(Array.from(map.entries()));
    };
    reader.readAsText(file);
    e.target.value = '';
  };

  const actionCls = 'flex items-center gap-1 text-[10px] text-muted-foreground/70 hover:text-brand transition-colors font-mono uppercase tracking-widest';

  return (
    <div>
      <div className={`flex items-center ${hideTitle ? 'justify-end' : 'justify-between'} mb-2 px-0.5`}>
        {!hideTitle && <span className="eyebrow">Environment Variables</span>}
        <div className="flex items-center gap-3">
          <button type="button" onClick={() => fileRef.current?.click()} className={actionCls}>
            <Upload className="w-3 h-3" /> .env file
          </button>
          <button type="button" onClick={() => {
            navigator.clipboard.readText().then(text => {
              const parsed = parseEnvText(text);
              if (!parsed.length) return;
              const map = new Map(envs.filter(([k]) => k));
              for (const [k, v] of parsed) map.set(k, v);
              onChange(Array.from(map.entries()));
            }).catch(() => {});
          }} className={actionCls}>
            <ClipboardPaste className="w-3 h-3" /> Paste
          </button>
          <button type="button" onClick={() => onChange([...envs, ['', '']])} className={actionCls}>
            <Plus className="w-3 h-3" /> Add
          </button>
        </div>
      </div>

      <input ref={fileRef} type="file" accept=".env,.txt,text/plain" className="hidden" onChange={handleFile} />

      <div className="bg-surface-1 border border-hairline rounded-xl overflow-hidden divide-y divide-hairline">
        {envs.length === 0 ? (
          <div
            onPaste={handlePaste}
            className="px-4 py-6 text-xs text-muted-foreground/70 text-center outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
            tabIndex={0}
          >
            Paste a <span className="font-mono text-muted-foreground">.env</span> file or click Add
          </div>
        ) : (
          envs.map(([k, v], i) => (
            <div key={i} className="group flex items-center transition-colors focus-within:bg-surface-2">
              <div className="flex-1 border-r border-hairline px-4 py-1.5">
                <Input
                  placeholder="KEY"
                  value={k}
                  onChange={(e) => setRow(i, e.target.value.toUpperCase(), v)}
                  onPaste={handlePaste}
                  className="bg-transparent border-0 h-8 text-xs text-brand placeholder:text-muted-foreground/50 focus-visible:ring-0 pl-0 font-mono"
                />
              </div>
              <div className="flex-1 px-4 py-1.5">
                <Input
                  placeholder="value"
                  value={v}
                  onChange={(e) => setRow(i, k, e.target.value)}
                  onPaste={handlePaste}
                  className="bg-transparent border-0 h-8 text-xs text-foreground placeholder:text-muted-foreground/50 focus-visible:ring-0 pl-0 font-mono"
                />
              </div>
              <button type="button" onClick={() => onChange(envs.filter((_, idx) => idx !== i))}
                className="px-3 self-stretch flex items-center text-muted-foreground/50 hover:text-destructive transition-colors shrink-0">
                <X className="w-3.5 h-3.5" />
              </button>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
