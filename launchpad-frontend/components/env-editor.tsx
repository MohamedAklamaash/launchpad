'use client';

import { useRef } from 'react';
import { Input } from '@/components/ui/input';
import { Plus, X, Upload, ClipboardPaste } from 'lucide-react';

type EnvRow = [string, string];

interface Props {
  envs: EnvRow[];
  onChange: (envs: EnvRow[]) => void;
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

export function EnvEditor({ envs, onChange }: Props) {
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

  return (
    <div>
      <div className="flex items-center justify-between mb-1.5 px-0.5">
        <span className="text-[10px] uppercase tracking-widest font-mono text-[#555]">Environment Variables</span>
        <div className="flex items-center gap-3">
          <button type="button" onClick={() => fileRef.current?.click()}
            className="flex items-center gap-1 text-[10px] text-[#555] hover:text-[#aaa] transition-colors font-mono uppercase tracking-widest">
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
          }}
            className="flex items-center gap-1 text-[10px] text-[#555] hover:text-[#aaa] transition-colors font-mono uppercase tracking-widest">
            <ClipboardPaste className="w-3 h-3" /> Paste
          </button>
          <button type="button" onClick={() => onChange([...envs, ['', '']])}
            className="flex items-center gap-1 text-[10px] text-[#555] hover:text-[#aaa] transition-colors font-mono uppercase tracking-widest">
            <Plus className="w-3 h-3" /> Add
          </button>
        </div>
      </div>

      <input ref={fileRef} type="file" accept=".env,.txt,text/plain" className="hidden" onChange={handleFile} />

      <div className="bg-[#0d0d0d] border border-[#1a1a1a] rounded-xl overflow-hidden divide-y divide-[#1a1a1a]">
        {envs.length === 0 ? (
          <div
            onPaste={handlePaste}
            className="px-4 py-6 text-xs text-[#333] text-center outline-none"
            tabIndex={0}
          >
            Paste a <span className="font-mono text-[#444]">.env</span> file or click Add
          </div>
        ) : (
          envs.map(([k, v], i) => (
            <div key={i} className="flex items-center">
              <div className="flex-1 border-r border-[#1a1a1a] px-4 py-1.5">
                <Input
                  placeholder="KEY"
                  value={k}
                  onChange={(e) => setRow(i, e.target.value.toUpperCase(), v)}
                  onPaste={handlePaste}
                  className="bg-transparent border-0 h-8 text-xs text-violet-400 placeholder:text-[#333] focus-visible:ring-0 pl-3 font-mono"
                />
              </div>
              <div className="flex-1 px-4 py-1.5">
                <Input
                  placeholder="value"
                  value={v}
                  onChange={(e) => setRow(i, k, e.target.value)}
                  onPaste={handlePaste}
                  className="bg-transparent border-0 h-8 text-xs text-[#aaa] placeholder:text-[#333] focus-visible:ring-0 pl-3 font-mono"
                />
              </div>
              <button type="button" onClick={() => onChange(envs.filter((_, idx) => idx !== i))}
                className="px-3 text-[#333] hover:text-red-400 transition-colors shrink-0">
                <X className="w-3.5 h-3.5" />
              </button>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
