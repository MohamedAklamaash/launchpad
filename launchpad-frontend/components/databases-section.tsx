'use client';

import { useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Database as DatabaseIcon, Plus, Trash2 } from 'lucide-react';
import { toast } from 'sonner';
import { databaseApi } from '@/lib/api/databases';
import { DatabaseCreate, DatabaseEngine, ManagedDatabase } from '@/types/database';
import { PolicyRefreshDialog } from '@/components/policy-refresh-dialog';

// Mirrors infrastructure-service's core/settings.py allowlists — keep in sync.
const ENGINE_OPTIONS: { value: DatabaseEngine; label: string }[] = [
  { value: 'postgres', label: 'PostgreSQL' },
  { value: 'mysql', label: 'MySQL' },
  { value: 'redis', label: 'Redis' },
  { value: 'docdb', label: 'DocumentDB' },
];

const ENGINE_VERSIONS: Record<DatabaseEngine, string[]> = {
  postgres: ['15.10', '16.6', '17.2'],
  mysql: ['8.0.39'],
  redis: ['7.1'],
  docdb: ['5.0.0'],
};

const INSTANCE_CLASSES: Record<DatabaseEngine, string[]> = {
  postgres: ['db.t3.micro', 'db.t3.small', 'db.t3.medium', 'db.r6g.large'],
  mysql: ['db.t3.micro', 'db.t3.small', 'db.t3.medium', 'db.r6g.large'],
  redis: ['cache.t3.micro', 'cache.t3.small', 'cache.t3.medium', 'cache.r6g.large'],
  docdb: ['db.t3.medium', 'db.r6g.large'],
};

const STATUS: Record<string, { dot: string; label: string }> = {
  ACTIVE: { dot: 'bg-success', label: 'text-success' },
  PENDING: { dot: 'bg-warning animate-pulse', label: 'text-warning' },
  PROVISIONING: { dot: 'bg-azure animate-pulse', label: 'text-azure' },
  DELETING: { dot: 'bg-warning animate-pulse', label: 'text-warning' },
  ERROR: { dot: 'bg-destructive', label: 'text-destructive' },
};
const statusOf = (s: string) => STATUS[s] ?? { dot: 'bg-muted-foreground', label: 'text-muted-foreground' };

const IN_FLIGHT: string[] = ['PENDING', 'PROVISIONING', 'DELETING'];

interface Props {
  infraId: string;
  environmentActive: boolean;
  canManage: boolean;
}

export function DatabasesSection({ infraId, environmentActive, canManage }: Props) {
  const [databases, setDatabases] = useState<ManagedDatabase[]>([]);
  const [loading, setLoading] = useState(true);
  const [createOpen, setCreateOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState<DatabaseCreate>({
    name: '', engine: 'postgres', engine_version: ENGINE_VERSIONS.postgres[0],
    instance_class: INSTANCE_CLASSES.postgres[0], allocated_storage: 20,
  });
  const [deleteTarget, setDeleteTarget] = useState<ManagedDatabase | null>(null);
  const [confirmName, setConfirmName] = useState('');
  const [deleting, setDeleting] = useState(false);
  const [refreshDialog, setRefreshDialog] = useState<{ open: boolean; deniedActions?: string[] }>({ open: false });

  const load = async () => {
    try {
      const data = await databaseApi.list(infraId);
      setDatabases(data);
    } catch {
      // Silent — the section stays showing whatever it last had rather than flashing an error
      // banner on every poll tick.
    } finally {
      setLoading(false);
    }
  };

  // Assigned in an effect, not during render: React treats a ref write during render as a
  // bug (the component can miss the update), and newer eslint-plugin-react-hooks errors on
  // it. The poll below only reads .current every 5s, so updating it after paint is fine.
  const hasInFlight = useRef(false);
  useEffect(() => {
    hasInFlight.current = databases.some((d) => IN_FLIGHT.includes(d.status));
  }, [databases]);

  useEffect(() => {
    load();
    const interval = setInterval(() => {
      if (hasInFlight.current) load();
    }, 5000);
    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [infraId]);

  const handleEngineChange = (engine: DatabaseEngine) => {
    setForm({
      name: form.name, engine,
      engine_version: ENGINE_VERSIONS[engine][0],
      instance_class: INSTANCE_CLASSES[engine][0],
      allocated_storage: engine === 'redis' ? undefined : 20,
    });
  };

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    setCreating(true);
    try {
      await databaseApi.create(infraId, form);
      toast.success('Database queued for provisioning');
      setCreateOpen(false);
      setForm({ name: '', engine: 'postgres', engine_version: ENGINE_VERSIONS.postgres[0], instance_class: INSTANCE_CLASSES.postgres[0], allocated_storage: 20 });
      await load();
    } catch (error: unknown) {
      const err = error as { response?: { status?: number; data?: { error?: string; code?: string; denied_actions?: string[] } } };
      if (err.response?.status === 422 && err.response.data?.code === 'policy_refresh_required') {
        setCreateOpen(false);
        setRefreshDialog({ open: true, deniedActions: err.response.data.denied_actions });
        return;
      }
      toast.error(err.response?.data?.error || 'Failed to create database');
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await databaseApi.delete(infraId, deleteTarget.id, confirmName);
      toast.success('Database deletion queued');
      setDeleteTarget(null);
      setConfirmName('');
      await load();
    } catch (error: unknown) {
      const err = error as { response?: { data?: { error?: string } } };
      toast.error(err.response?.data?.error || 'Failed to delete database');
    } finally {
      setDeleting(false);
    }
  };

  if (loading) return <div className="h-24 rounded-xl panel animate-pulse" />;

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-display font-semibold text-foreground">
          Databases <span className="text-muted-foreground/60 font-normal font-mono">({databases.length})</span>
        </h2>
        {environmentActive && canManage && (
          <Button size="sm" onClick={() => setCreateOpen(true)} className="gap-1.5">
            <Plus className="w-3.5 h-3.5" /> New Database
          </Button>
        )}
      </div>

      {databases.length === 0 ? (
        <div className="rounded-xl border border-dashed border-hairline-strong p-10 text-center">
          <p className="text-xs text-muted-foreground mb-3">
            {environmentActive ? 'No managed databases yet' : 'Databases can be created once the environment is ACTIVE'}
          </p>
          {environmentActive && canManage && (
            <Button size="sm" onClick={() => setCreateOpen(true)} className="gap-1.5">
              <Plus className="w-3.5 h-3.5" /> Create First Database
            </Button>
          )}
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {databases.map((db) => {
            const st = statusOf(db.status);
            return (
              <div key={db.id} className="rounded-xl panel p-4 space-y-2.5">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2 min-w-0">
                    <DatabaseIcon className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
                    <span className="text-sm font-medium text-foreground truncate">{db.name}</span>
                  </div>
                  <div className="flex items-center gap-2 shrink-0 ml-2">
                    <span className={`w-1.5 h-1.5 rounded-full ${st.dot}`} />
                    <span className={`font-mono text-[10px] uppercase tracking-[0.12em] ${st.label}`}>{db.status}</span>
                    {canManage && !IN_FLIGHT.includes(db.status) && db.status !== 'DELETED' && (
                      <button onClick={() => { setDeleteTarget(db); setConfirmName(''); }}
                        className="text-muted-foreground/50 hover:text-destructive transition-colors">
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    )}
                  </div>
                </div>
                <div className="flex items-center gap-3 text-xs text-muted-foreground font-mono">
                  <span className="uppercase">{db.engine}</span>
                  <span>{db.engine_version}</span>
                  <span>{db.instance_class}</span>
                  {db.allocated_storage != null && <span>{db.allocated_storage}GB</span>}
                </div>
                {db.host && (
                  <p className="text-[11px] text-muted-foreground font-mono truncate">{db.host}:{db.port}</p>
                )}
                {db.secret_arn && (
                  <p className="text-[10px] text-muted-foreground/70 font-mono truncate" title={db.secret_arn}>
                    Secret: {db.secret_arn}
                  </p>
                )}
                {db.error_message && db.status === 'ERROR' && (
                  <p className="text-[11px] text-destructive">{db.error_message}</p>
                )}
              </div>
            );
          })}
        </div>
      )}

      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle className="text-base font-display font-semibold">New Database</DialogTitle>
          </DialogHeader>
          <form onSubmit={handleCreate} className="space-y-3">
            <div className="space-y-1.5">
              <Label className="eyebrow">Name</Label>
              <Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder="primary-db" pattern="^[a-z][a-z0-9-]{2,30}$" required className="h-9 text-sm font-mono" />
            </div>
            <div className="space-y-1.5">
              <Label className="eyebrow">Engine</Label>
              <Select value={form.engine} onValueChange={(v) => handleEngineChange(v as DatabaseEngine)}>
                <SelectTrigger className="h-9 text-sm w-full"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {ENGINE_OPTIONS.map((o) => (
                    <SelectItem key={o.value} value={o.value} className="text-sm py-2.5">{o.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label className="eyebrow">Version</Label>
                <Select value={form.engine_version} onValueChange={(v) => v && setForm({ ...form, engine_version: v })}>
                  <SelectTrigger className="h-9 text-sm w-full"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {ENGINE_VERSIONS[form.engine].map((v) => (
                      <SelectItem key={v} value={v} className="text-sm py-2.5">{v}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label className="eyebrow">Instance Class</Label>
                <Select value={form.instance_class} onValueChange={(v) => v && setForm({ ...form, instance_class: v })}>
                  <SelectTrigger className="h-9 text-sm w-full"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {INSTANCE_CLASSES[form.engine].map((v) => (
                      <SelectItem key={v} value={v} className="text-sm py-2.5 font-mono">{v}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
            {form.engine !== 'redis' && (
              <div className="space-y-1.5">
                <Label className="eyebrow">Storage (GB)</Label>
                <Input type="number" min={20} max={1000} value={form.allocated_storage ?? 20}
                  onChange={(e) => setForm({ ...form, allocated_storage: Number(e.target.value) })}
                  className="h-9 text-sm font-mono" required />
              </div>
            )}
            <Button type="submit" disabled={creating} className="w-full mt-1">
              {creating ? 'Creating…' : 'Create Database'}
            </Button>
          </form>
        </DialogContent>
      </Dialog>

      <Dialog open={!!deleteTarget} onOpenChange={(o) => { if (!o) setDeleteTarget(null); }}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle className="text-base font-display font-semibold">Delete Database</DialogTitle>
          </DialogHeader>
          <p className="text-xs text-muted-foreground">
            Type <span className="text-foreground font-mono">{deleteTarget?.name}</span> to confirm. A final
            snapshot is taken before the underlying resource is destroyed.
          </p>
          <Input value={confirmName} onChange={(e) => setConfirmName(e.target.value)}
            placeholder={deleteTarget?.name} className="h-9 text-sm font-mono" />
          <div className="flex gap-2 justify-end mt-2">
            <Button variant="outline" onClick={() => setDeleteTarget(null)}>Cancel</Button>
            <Button variant="destructive" onClick={handleDelete} disabled={deleting || confirmName !== deleteTarget?.name}>
              {deleting ? 'Deleting…' : 'Delete'}
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      <PolicyRefreshDialog
        open={refreshDialog.open}
        onOpenChange={(open) => setRefreshDialog({ open })}
        infraId={infraId}
        deniedActions={refreshDialog.deniedActions}
      />
    </div>
  );
}
