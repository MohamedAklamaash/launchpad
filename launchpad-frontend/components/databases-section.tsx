'use client';

import { useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Database as DatabaseIcon, Plus, Trash2 } from 'lucide-react';
import { StatusBadge } from '@/components/ui/status-badge';
import { EmptyState } from '@/components/ui/empty-state';
import { Skeleton } from '@/components/ui/skeleton';
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
  postgres: ['17', '16', '15'],
  mysql: ['8.4', '8.0'],
  redis: ['7.1'],
  docdb: ['5.0.0'],
};

const INSTANCE_CLASSES: Record<DatabaseEngine, string[]> = {
  postgres: ['db.t3.micro', 'db.t3.small', 'db.t3.medium', 'db.r6g.large'],
  mysql: ['db.t3.micro', 'db.t3.small', 'db.t3.medium', 'db.r6g.large'],
  redis: ['cache.t3.micro', 'cache.t3.small', 'cache.t3.medium', 'cache.r6g.large'],
  docdb: ['db.t3.medium', 'db.r6g.large'],
};

const IN_FLIGHT: string[] = ['PENDING', 'PROVISIONING', 'DELETING'];

const IN_FLIGHT_HINT: Record<string, string> = {
  PENDING: 'Creating the database. This usually takes 5 to 10 minutes.',
  PROVISIONING: 'Creating the database. This usually takes 5 to 10 minutes.',
  DELETING: 'Deleting. A final snapshot is taken first, which usually takes 5 to 10 minutes.',
};

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

  if (loading) return <Skeleton className="h-24 w-full" />;

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-medium text-foreground">
          Databases <span className="font-normal text-muted-foreground">{databases.length}</span>
        </h2>
        {environmentActive && canManage && (
          <Button size="sm" onClick={() => setCreateOpen(true)}>
            <Plus className="size-3.5" /> New database
          </Button>
        )}
      </div>

      {databases.length === 0 ? (
        <EmptyState
          icon={DatabaseIcon}
          title="No databases yet"
          description={
            environmentActive
              ? 'Launchpad can create PostgreSQL, MySQL, Redis or DocumentDB inside this AWS account.'
              : 'You can create databases once the environment is active.'
          }
          action={
            environmentActive && canManage ? (
              <Button size="sm" onClick={() => setCreateOpen(true)}>
                <Plus className="size-3.5" /> Create a database
              </Button>
            ) : undefined
          }
        />
      ) : (
        <ul className="divide-y divide-border overflow-hidden rounded-lg border border-border bg-card">
          {databases.map((db) => (
            <li key={db.id} className="space-y-1.5 px-4 py-3">
              <div className="flex items-center justify-between gap-3">
                <div className="flex min-w-0 items-center gap-2">
                  <DatabaseIcon className="size-3.5 shrink-0 text-muted-foreground" />
                  <span className="truncate text-sm font-medium text-foreground">{db.name}</span>
                  <span className="font-mono text-xs text-muted-foreground">
                    {db.engine} {db.engine_version} · {db.instance_class}
                    {db.allocated_storage != null && ` · ${db.allocated_storage} GB`}
                  </span>
                </div>
                <div className="flex shrink-0 items-center gap-3">
                  <StatusBadge status={db.status} />
                  {canManage && !IN_FLIGHT.includes(db.status) && db.status !== 'DELETED' && (
                    <button
                      aria-label={`Delete ${db.name}`}
                      onClick={() => { setDeleteTarget(db); setConfirmName(''); }}
                      className="rounded-sm text-muted-foreground transition-colors hover:text-destructive"
                    >
                      <Trash2 className="size-3.5" />
                    </button>
                  )}
                </div>
              </div>
              {db.host && <p className="truncate font-mono text-xs text-muted-foreground">{db.host}:{db.port}</p>}
              {db.secret_arn && (
                <p className="truncate font-mono text-xs text-muted-foreground" title={db.secret_arn}>
                  Secret: {db.secret_arn}
                </p>
              )}
              {IN_FLIGHT.includes(db.status) && (
                <p className="text-xs text-muted-foreground">{IN_FLIGHT_HINT[db.status]}</p>
              )}
              {db.error_message && db.status === 'ERROR' && (
                <p className="text-xs text-destructive">{db.error_message}</p>
              )}
            </li>
          ))}
        </ul>
      )}

      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle className="text-base font-semibold">New database</DialogTitle>
          </DialogHeader>
          <form onSubmit={handleCreate} className="space-y-3">
            <div className="space-y-1.5">
              <Label htmlFor="db-name">Name</Label>
              <Input id="db-name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder="primary-db" pattern="^[a-z][a-z0-9-]{2,30}$" required className="font-mono" />
            </div>
            <div className="space-y-1.5">
              <Label>Engine</Label>
              <Select value={form.engine} onValueChange={(v) => handleEngineChange(v as DatabaseEngine)}>
                <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {ENGINE_OPTIONS.map((o) => (
                    <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label>Version</Label>
                <Select value={form.engine_version} onValueChange={(v) => v && setForm({ ...form, engine_version: v })}>
                  <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {ENGINE_VERSIONS[form.engine].map((v) => (
                      <SelectItem key={v} value={v}>{v}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label>Instance class</Label>
                <Select value={form.instance_class} onValueChange={(v) => v && setForm({ ...form, instance_class: v })}>
                  <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {INSTANCE_CLASSES[form.engine].map((v) => (
                      <SelectItem key={v} value={v} className="font-mono">{v}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
            {form.engine !== 'redis' && (
              <div className="space-y-1.5">
                <Label>Storage (GB)</Label>
                <Input type="number" min={20} max={1000} value={form.allocated_storage ?? 20}
                  onChange={(e) => setForm({ ...form, allocated_storage: Number(e.target.value) })}
                  className="font-mono" required />
              </div>
            )}
            <Button type="submit" disabled={creating} className="w-full mt-1">
              {creating ? 'Creating…' : 'Create database'}
            </Button>
          </form>
        </DialogContent>
      </Dialog>

      <Dialog open={!!deleteTarget} onOpenChange={(o) => { if (!o) setDeleteTarget(null); }}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle className="text-base font-semibold">Delete database</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            Type <span className="font-mono text-foreground">{deleteTarget?.name}</span> to confirm. Launchpad takes a final
            snapshot before it destroys the database.
          </p>
          <Input value={confirmName} onChange={(e) => setConfirmName(e.target.value)}
            placeholder={deleteTarget?.name} className="font-mono" />
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
