'use client';

import { useEffect, useState } from 'react';
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Hash, FileText, GitBranch, Cpu, HardDrive, Database as DatabaseIcon } from 'lucide-react';
import { Application, ApplicationUpdate } from '@/types/application';
import { ManagedDatabase } from '@/types/database';
import { applicationApi } from '@/lib/api/applications';
import { databaseApi } from '@/lib/api/databases';
import { toast } from 'sonner';
import { EnvEditor } from '@/components/env-editor';

const CPU_OPTIONS = [0.25, 0.5, 1.0, 2.0, 4.0];
const MEM_RANGES: Record<number, [number, number]> = {
  0.25: [0.5, 2], 0.5: [1, 4], 1: [2, 8], 2: [4, 16], 4: [8, 30],
};

const inputCls = 'bg-transparent border-0 h-9 text-sm placeholder:text-muted-foreground/50 focus-visible:ring-0 pl-6';
const monoInputCls = inputCls + ' font-mono';
const triggerCls = 'bg-transparent border-0 h-9 text-sm text-foreground focus:ring-0 pl-6 pr-2 shadow-none font-mono';

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
  const [databases, setDatabases] = useState<ManagedDatabase[]>([]);
  const [attachedIds, setAttachedIds] = useState<string[]>(app.attached_database_ids ?? []);

  useEffect(() => {
    databaseApi.list(app.infrastructure_id)
      .then((dbs) => setDatabases(dbs.filter((d) => d.status === 'ACTIVE')))
      .catch(() => setDatabases([]));
  }, [app.infrastructure_id]);

  const toggleDatabase = (id: string) => {
    setAttachedIds((prev) => prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]);
  };

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
        attached_database_ids: attachedIds,
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

  return (
    <Sheet open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <SheetContent className="bg-surface-2 border-hairline w-[480px] min-w-[320px] max-w-[640px] overflow-y-auto flex flex-col resize-x">
        <SheetHeader className="mb-5 shrink-0">
          <span className="eyebrow">Console / Edit Application</span>
          <SheetTitle className="mt-2 text-lg font-display font-semibold text-foreground tracking-tight">Edit application</SheetTitle>
        </SheetHeader>

        <div className="flex-1 space-y-6 overflow-y-auto">
          <Section label="General">
            <Field icon={<Hash className="w-3.5 h-3.5" />} label="Name">
              <Input value={form.name} onChange={(e) => set('name', e.target.value)} className={inputCls} />
            </Field>
            <Field icon={<FileText className="w-3.5 h-3.5" />} label="Description">
              <Input value={form.description} onChange={(e) => set('description', e.target.value)}
                placeholder="Optional" className={inputCls} />
            </Field>
          </Section>

          <Section label="Repository">
            <Field icon={<GitBranch className="w-3.5 h-3.5" />} label="Branch">
              <Input value={form.project_branch} onChange={(e) => set('project_branch', e.target.value)} className={monoInputCls} />
            </Field>
            <Field icon={<FileText className="w-3.5 h-3.5" />} label="Dockerfile">
              <Input value={form.dockerfile_path} onChange={(e) => set('dockerfile_path', e.target.value)} className={monoInputCls} />
            </Field>
          </Section>

          <Section label="Resources">
            <Field icon={<Hash className="w-3.5 h-3.5" />} label="Port">
              <Input type="number" value={form.port} onChange={(e) => set('port', e.target.value)}
                min={1024} max={65535} className={monoInputCls} />
            </Field>
            <Field icon={<Cpu className="w-3.5 h-3.5" />} label="CPU">
              <Select value={String(form.alloted_cpu)} onValueChange={(v) => {
                const cpu = Number(v);
                set('alloted_cpu', cpu);
                set('alloted_memory', MEM_RANGES[cpu][0]);
              }}>
                <SelectTrigger className={triggerCls}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent className="bg-popover border-hairline">
                  {CPU_OPTIONS.map((c) => (
                    <SelectItem key={c} value={String(c)} className="font-mono text-sm">{c} vCPU</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
            <Field icon={<HardDrive className="w-3.5 h-3.5" />} label="Memory" hint={`${minMem}–${maxMem} GB`}>
              <Input type="number" value={form.alloted_memory}
                onChange={(e) => set('alloted_memory', Number(e.target.value))}
                min={minMem} max={maxMem} step={0.5}
                className={monoInputCls} />
            </Field>
          </Section>

          <Section label="Databases">
            {databases.length === 0 ? (
              <p className="text-xs text-muted-foreground px-1">
                No active managed databases in this infrastructure yet.
              </p>
            ) : (
              <div className="rounded-xl panel-inset divide-y divide-hairline">
                {databases.map((db) => (
                  <label key={db.id} className="flex items-center gap-3 px-4 py-2.5 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={attachedIds.includes(db.id)}
                      onChange={() => toggleDatabase(db.id)}
                      className="h-3.5 w-3.5 accent-brand"
                    />
                    <DatabaseIcon className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
                    <span className="text-xs text-foreground/80 flex-1 truncate">{db.name}</span>
                    <span className="text-[10px] uppercase tracking-[0.12em] text-muted-foreground/60 font-mono">{db.engine}</span>
                  </label>
                ))}
              </div>
            )}
            <p className="text-[11px] text-muted-foreground px-1 mt-1.5">
              Attaching does not redeploy — redeploy the app to inject the new credentials.
            </p>
          </Section>

          <EnvEditor envs={envs} onChange={setEnvs} />
        </div>

        <div className="pt-4 shrink-0">
          <Button onClick={handleSave} disabled={saving} size="lg" className="w-full">
            {saving ? 'Saving…' : 'Save Changes'}
          </Button>
        </div>
      </SheetContent>
    </Sheet>
  );
}

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <span className="eyebrow">{label}</span>
      <div className="mt-2">{children}</div>
    </div>
  );
}

function Field({ icon, label, hint, children }: {
  icon: React.ReactNode; label: string; hint?: string; children: React.ReactNode;
}) {
  return (
    <div className="group bg-surface-1 border border-hairline px-4 py-2.5 transition-colors focus-within:border-brand/40 focus-within:bg-surface-2 first:rounded-t-xl last:rounded-b-xl">
      <div className="flex items-center gap-2 mb-0.5">
        <span className="text-muted-foreground/70 group-focus-within:text-brand transition-colors">{icon}</span>
        <span className="eyebrow">{label}</span>
        {hint && <span className="text-[10px] text-muted-foreground/60 ml-auto font-mono">{hint}</span>}
      </div>
      {children}
    </div>
  );
}
