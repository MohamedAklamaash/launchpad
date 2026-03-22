'use client';

import { useState } from 'react';
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Application, ApplicationUpdate } from '@/types/application';
import { applicationApi } from '@/lib/api/applications';
import { toast } from 'sonner';
import { Plus, X } from 'lucide-react';

const CPU_OPTIONS = [0.25, 0.5, 1.0, 2.0, 4.0];
const MEM_RANGES: Record<number, [number, number]> = {
  0.25: [0.5, 2], 0.5: [1, 4], 1: [2, 8], 2: [4, 16], 4: [8, 30],
};

const rowCls = "flex items-center border-b border-[#1a1a1a] last:border-0";
const labelCls = "w-28 shrink-0 text-xs text-[#555] px-4 py-3";
const inputCls = "bg-transparent border-0 h-9 text-sm text-white placeholder:text-[#333] focus-visible:ring-0 pl-3 flex-1";

interface Props {
  app: Application;
  open: boolean;
  onClose: () => void;
  onSaved: (updated: Application) => void;
}

export function EditAppSheet({ app, open, onClose, onSaved }: Props) {
  const [form, setForm] = useState({
    name: app.name,
    description: app.description ?? '',
    project_branch: app.branch,
    dockerfile_path: app.dockerfile_path,
    port: String(app.port),
    alloted_cpu: app.cpu,
    alloted_memory: app.memory,
  });
  const [envs, setEnvs] = useState<[string, string][]>(Object.entries(app.envs ?? {}));
  const [saving, setSaving] = useState(false);

  const set = (k: string, v: string | number) => setForm((p) => ({ ...p, [k]: v }));
  const [minMem, maxMem] = MEM_RANGES[form.alloted_cpu] ?? [0.5, 2];

  const handleSave = async () => {
    setSaving(true);
    try {
      const payload: ApplicationUpdate = {
        name: form.name.trim(),
        description: form.description,
        project_branch: form.project_branch.trim(),
        dockerfile_path: form.dockerfile_path.trim(),
        port: Number(form.port),
        alloted_cpu: form.alloted_cpu,
        alloted_memory: form.alloted_memory,
        envs: Object.fromEntries(envs.filter(([k]) => k.trim())),
      };
      const updated = await applicationApi.update(app.id, payload);
      toast.success('Application updated');
      onSaved(updated);
      onClose();
    } catch (err: unknown) {
      const error = err as { response?: { data?: { error?: string } } };
      toast.error(error.response?.data?.error || 'Update failed');
    } finally {
      setSaving(false);
    }
  };

  const setEnvRow = (i: number, k: string, v: string) =>
    setEnvs((prev) => prev.map((row, idx) => (idx === i ? [k, v] : row)));

  return (
    <Sheet open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <SheetContent className="bg-[#090909] border-[#1a1a1a] w-[480px] min-w-[320px] max-w-[640px] overflow-y-auto flex flex-col resize-x">
        <SheetHeader className="mb-5 shrink-0">
          <SheetTitle className="text-base font-semibold text-white">Edit Application</SheetTitle>
        </SheetHeader>

        <div className="flex-1 space-y-4 overflow-y-auto">
          {/* General */}
          <Group label="General">
            <div className={rowCls}>
              <span className={labelCls}>Name</span>
              <Input value={form.name} onChange={(e) => set('name', e.target.value)}
                className={inputCls + " pr-4"} />
            </div>
            <div className={rowCls}>
              <span className={labelCls}>Description</span>
              <Input value={form.description} onChange={(e) => set('description', e.target.value)}
                placeholder="Optional" className={inputCls + " pr-4"} />
            </div>
          </Group>

          {/* Repository */}
          <Group label="Repository">
            <div className={rowCls}>
              <span className={labelCls}>Branch</span>
              <Input value={form.project_branch} onChange={(e) => set('project_branch', e.target.value)}
                className={inputCls + " pr-4 font-mono text-xs"} />
            </div>
            <div className={rowCls}>
              <span className={labelCls}>Dockerfile</span>
              <Input value={form.dockerfile_path} onChange={(e) => set('dockerfile_path', e.target.value)}
                className={inputCls + " pr-4 font-mono text-xs"} />
            </div>
          </Group>

          {/* Resources */}
          <Group label="Resources">
            <div className={rowCls}>
              <span className={labelCls}>Port</span>
              <Input type="number" value={form.port} onChange={(e) => set('port', e.target.value)}
                min={1024} max={65535} className={inputCls + " pr-4 font-mono text-xs"} />
            </div>
            <div className={rowCls}>
              <span className={labelCls}>CPU</span>
              <div className="flex-1 pr-4">
                <Select value={String(form.alloted_cpu)} onValueChange={(v) => {
                  const cpu = Number(v);
                  set('alloted_cpu', cpu);
                  set('alloted_memory', MEM_RANGES[cpu][0]);
                }}>
                  <SelectTrigger className="bg-transparent border-0 h-9 text-sm text-white focus:ring-0 px-0 shadow-none">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent className="bg-[#0f0f0f] border-[#1a1a1a]">
                    {CPU_OPTIONS.map((c) => (
                      <SelectItem key={c} value={String(c)} className="font-mono text-sm">{c} vCPU</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className={rowCls}>
              <span className={labelCls}>Memory</span>
              <div className="flex-1 pr-4">
                <Input type="number" value={form.alloted_memory}
                  onChange={(e) => set('alloted_memory', Number(e.target.value))}
                  min={minMem} max={maxMem} step={0.5}
                  className={inputCls + " font-mono text-xs"} />
              </div>
              <span className="text-[10px] text-[#333] pr-4 shrink-0">{minMem}–{maxMem} GB</span>
            </div>
          </Group>

          {/* Env vars */}
          <Group label="Environment Variables" action={
            <button onClick={() => setEnvs([...envs, ['', '']])}
              className="flex items-center gap-1 text-[10px] text-[#555] hover:text-[#aaa] transition-colors font-mono uppercase tracking-widest">
              <Plus className="w-3 h-3" /> Add
            </button>
          }>
            {envs.length === 0 ? (
              <div className="px-4 py-3 text-xs text-[#333]">No environment variables</div>
            ) : envs.map(([k, v], i) => (
              <div key={i} className={`flex items-center ${i < envs.length - 1 ? 'border-b border-[#1a1a1a]' : ''}`}>
                <div className="flex-1 border-r border-[#1a1a1a] px-4 py-1.5">
                  <Input placeholder="KEY" value={k} onChange={(e) => setEnvRow(i, e.target.value.toUpperCase(), v)}
                    className="bg-transparent border-0 h-8 text-xs text-violet-400 placeholder:text-[#333] focus-visible:ring-0 pl-3 font-mono" />
                </div>
                <div className="flex-1 px-4 py-1.5">
                  <Input placeholder="value" value={v} onChange={(e) => setEnvRow(i, k, e.target.value)}
                    className="bg-transparent border-0 h-8 text-xs text-[#aaa] placeholder:text-[#333] focus-visible:ring-0 pl-3 font-mono" />
                </div>
                <button onClick={() => setEnvs(envs.filter((_, idx) => idx !== i))}
                  className="px-3 text-[#333] hover:text-red-400 transition-colors shrink-0">
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>
            ))}
          </Group>
        </div>

        <div className="pt-4 shrink-0">
          <Button onClick={handleSave} disabled={saving}
            className="w-full bg-violet-600 hover:bg-violet-700 h-9 text-sm font-medium">
            {saving ? 'Saving…' : 'Save Changes'}
          </Button>
        </div>
      </SheetContent>
    </Sheet>
  );
}

function Group({ label, children, action }: { label: string; children: React.ReactNode; action?: React.ReactNode }) {
  return (
    <div>
      <div className="flex items-center justify-between mb-1.5 px-0.5">
        <span className="text-[10px] uppercase tracking-widest font-mono text-[#555]">{label}</span>
        {action}
      </div>
      <div className="bg-[#0d0d0d] border border-[#1a1a1a] rounded-xl overflow-hidden">
        {children}
      </div>
    </div>
  );
}
