'use client';

import { useEffect, useState } from 'react';
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Database as DatabaseIcon } from 'lucide-react';
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
      .then((dbs) => {
        setDatabases(dbs.filter((d) => d.status === 'ACTIVE'));
        const liveIds = new Set(dbs.map((d) => d.id));
        setAttachedIds((prev) => prev.filter((id) => liveIds.has(id)));
      })
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
      <SheetContent className="flex w-full flex-col overflow-y-auto sm:w-[480px] sm:max-w-[640px]">
        <SheetHeader className="shrink-0">
          <SheetTitle className="text-base font-semibold text-foreground">Edit application</SheetTitle>
        </SheetHeader>

        <div className="flex-1 space-y-6 overflow-y-auto px-4">
          <Section label="General">
            <Field id="edit-name" label="Name">
              <Input id="edit-name" value={form.name} onChange={(e) => set('name', e.target.value)} />
            </Field>
            <Field id="edit-description" label="Description">
              <Input id="edit-description" value={form.description} onChange={(e) => set('description', e.target.value)}
                placeholder="Optional" />
            </Field>
          </Section>

          <Section label="Repository">
            <Field id="edit-branch" label="Branch">
              <Input id="edit-branch" value={form.project_branch} onChange={(e) => set('project_branch', e.target.value)} className="font-mono" />
            </Field>
            <Field id="edit-dockerfile" label="Dockerfile">
              <Input id="edit-dockerfile" value={form.dockerfile_path} onChange={(e) => set('dockerfile_path', e.target.value)} className="font-mono" />
            </Field>
          </Section>

          <Section label="Resources">
            <Field id="edit-port" label="Port">
              <Input id="edit-port" type="number" value={form.port} onChange={(e) => set('port', e.target.value)}
                min={1024} max={65535} className="font-mono" />
            </Field>
            <Field id="edit-cpu" label="CPU">
              <Select value={String(form.alloted_cpu)} onValueChange={(v) => {
                const cpu = Number(v);
                set('alloted_cpu', cpu);
                set('alloted_memory', MEM_RANGES[cpu][0]);
              }}>
                <SelectTrigger id="edit-cpu" className="w-full font-mono">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {CPU_OPTIONS.map((c) => (
                    <SelectItem key={c} value={String(c)} className="font-mono">{c} vCPU</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
            <Field id="edit-memory" label="Memory" hint={`${minMem} to ${maxMem} GB`}>
              <Input id="edit-memory" type="number" value={form.alloted_memory}
                onChange={(e) => set('alloted_memory', Number(e.target.value))}
                min={minMem} max={maxMem} step={0.5}
                className="font-mono" />
            </Field>
          </Section>

          <Section label="Databases">
            {databases.length === 0 ? (
              <p className="text-xs text-muted-foreground px-1">
                No active managed databases in this infrastructure yet.
              </p>
            ) : (
              <div className="rounded-lg border border-border bg-surface-1 divide-y divide-border">
                {databases.map((db) => (
                  <label key={db.id} className="flex items-center gap-3 px-4 py-2.5 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={attachedIds.includes(db.id)}
                      onChange={() => toggleDatabase(db.id)}
                      className="h-3.5 w-3.5 accent-brand"
                    />
                    <DatabaseIcon className="size-3.5 text-muted-foreground shrink-0" />
                    <span className="text-xs text-foreground/80 flex-1 truncate">{db.name}</span>
                    <span className="text-xs text-muted-foreground font-mono">{db.engine}</span>
                  </label>
                ))}
              </div>
            )}
            <p className="text-xs text-muted-foreground px-1 mt-1.5">
              Attaching doesn’t redeploy. Redeploy the app to inject the new credentials.
            </p>
          </Section>

          <EnvEditor envs={envs} onChange={setEnvs} />
        </div>

        <div className="shrink-0 p-4">
          <Button onClick={handleSave} disabled={saving} size="lg" className="w-full">
            {saving ? 'Saving…' : 'Save changes'}
          </Button>
        </div>
      </SheetContent>
    </Sheet>
  );
}

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <section className="space-y-3">
      <h3 className="text-sm font-medium text-foreground">{label}</h3>
      <div className="space-y-3">{children}</div>
    </section>
  );
}

function Field({ id, label, hint, children }: { id: string; label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-baseline justify-between">
        <Label htmlFor={id}>{label}</Label>
        {hint && <span className="text-xs text-muted-foreground">{hint}</span>}
      </div>
      {children}
    </div>
  );
}
