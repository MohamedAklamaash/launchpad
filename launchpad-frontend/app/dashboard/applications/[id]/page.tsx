'use client';

import { useEffect, useState, useRef, useCallback } from 'react';
import { useRouter, useParams } from 'next/navigation';
import Link from 'next/link';
import { Button } from '@/components/ui/button';
import { buttonVariants } from '@/components/ui/button-variants';
import { StatusBadge } from '@/components/ui/status-badge';
import { EmptyState } from '@/components/ui/empty-state';
import { Skeleton } from '@/components/ui/skeleton';
import { cn } from '@/lib/utils';
import { ArrowLeft, ExternalLink, RefreshCw, Moon, Sun, Trash2, Pencil, Eye, EyeOff, Github, Copy, PackageX, Pin, PlayCircle, AlertTriangle } from 'lucide-react';
import { Application } from '@/types/application';
import { Infrastructure } from '@/types/infrastructure';
import { applicationApi } from '@/lib/api/applications';
import { infrastructureApi } from '@/lib/api/infrastructures';
import { useAuthStore } from '@/lib/store/auth';
import { useOrgRole } from '@/lib/use-org-role';
import { toast } from 'sonner';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import { EditAppSheet } from '@/components/edit-app-sheet';
import { EnvEditor } from '@/components/env-editor';
import { DeploymentHistory } from '@/components/deployment-history';
import { AppMetricsPanel } from '@/components/app-metrics-panel';
import { AppLiveLogs } from '@/components/app-live-logs';
import { BuildLogs } from '@/components/build-logs';
import { CustomDomainsPanel } from '@/components/custom-domains-panel';

const POLLING_STATUSES = ['CREATED', 'BUILDING', 'PUSHING_IMAGE', 'DEPLOYING'];
// Narrower than POLLING_STATUSES: CREATED has no build yet (queued on a budget slot) and
// DEPLOYING means the build already finished — only these two states have a build worth
// tailing live (F7 ticket 8).
const IN_PROGRESS_BUILD_STATUSES = ['BUILDING', 'PUSHING_IMAGE'];

const IN_FLIGHT_HINT: Record<string, string> = {
  CREATED: 'Queued, waiting for a build slot…',
  BUILDING: 'Building your image, usually a couple of minutes…',
  PUSHING_IMAGE: 'Pushing the built image to your registry…',
  DEPLOYING: 'Rolling out, waiting for tasks to pass health checks…',
};

const FOCUS_ICON = 'rounded-sm text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60';

function Panel({ title, aside, children }: { title: string; aside?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="rounded-lg border border-border bg-card p-4">
      <div className="mb-3 flex items-center justify-between gap-3">
        <h2 className="text-sm font-medium text-foreground">{title}</h2>
        {aside}
      </div>
      {children}
    </section>
  );
}

function UrlRow({ label, url }: { label: string; url: string }) {
  return (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      className="group flex items-center justify-between gap-4 rounded-lg border border-border bg-surface-1 px-4 py-3 outline-none transition-colors hover:bg-muted/40 focus-visible:ring-2 focus-visible:ring-ring/60"
    >
      <div className="min-w-0">
        <p className="text-xs text-muted-foreground">{label}</p>
        <p className="mt-0.5 truncate font-mono text-sm text-brand">{url}</p>
      </div>
      <ExternalLink className="size-4 shrink-0 text-muted-foreground transition-colors group-hover:text-foreground" />
    </a>
  );
}

export default function ApplicationDetailPage() {
  const router = useRouter();
  const { id } = useParams<{ id: string }>();
  const [app, setApp] = useState<Application | null>(null);
  const [infra, setInfra] = useState<Infrastructure | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(false);
  // Distinguishes which action is in flight so the button that was actually clicked can
  // say "Sleeping…"/"Waking…" instead of every button in the row claiming the same state.
  const [pendingAction, setPendingAction] = useState<'deploy' | 'sleep' | 'wake' | 'retry' | 'delete' | null>(null);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [revealedEnvs, setRevealedEnvs] = useState<Set<string>>(new Set());
  const [webhookCreds, setWebhookCreds] = useState<{ webhook_url: string; secret: string; instructions: string } | null>(null);
  const [webhookLoading, setWebhookLoading] = useState(false);
  const [editingEnvs, setEditingEnvs] = useState(false);
  const [envRows, setEnvRows] = useState<[string, string][]>([]);
  const [savingEnvs, setSavingEnvs] = useState(false);
  const [hasRollbackAccess, setHasRollbackAccess] = useState(false);
  const intervalRef = useRef<NodeJS.Timeout | null>(null);
  const user = useAuthStore((s) => s.user);
  const orgRole = useOrgRole();
  const legacyCanEdit = user?.role === 'super_admin' || user?.role === 'admin';
  // With orgs on, each control follows the org-role matrix; with them off these all
  // collapse to the checks this page always had.
  const canEdit = orgRole.allow('deploy', legacyCanEdit);
  const canEditEnvs = orgRole.allow('env_vars', legacyCanEdit);
  const canRollbackRole = orgRole.allow('rollback', legacyCanEdit);
  const canDeployNow = orgRole.allow('deploy', true);
  const canDeleteApp = orgRole.allow('delete_application', true);
  // Runtime logs are gated on ownership of THIS application's infrastructure, not the
  // global JWT role — an infra owner isn't necessarily super_admin platform-wide, and a
  // platform super_admin viewing someone else's infra isn't its owner. The backend
  // enforces this regardless; this only decides whether to show the panel at all.
  const legacyIsOwner = !!infra && !!user && infra.user_id === user.id;
  const canSeeLogs = orgRole.allow('logs_metrics', legacyIsOwner);
  const canManageDomains = orgRole.allow('custom_domains', legacyIsOwner);

  const loadApp = useCallback(async () => {
    try {
      const data = await applicationApi.get(id);
      setApp(data);
      return data;
    } catch (e: unknown) {
      const error = e as { response?: { data?: { error?: string } } };
      // Fixed id: a failing 3s poll (e.g. gateway restart) must replace the same toast
      // rather than stack a new one every tick.
      toast.error(error.response?.data?.error || 'Failed to load application', { id: 'app-load' });
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    loadApp();
  }, [id, loadApp]);

  useEffect(() => {
    if (!app?.infrastructure_id) return;
    infrastructureApi.get(app.infrastructure_id).then(setInfra).catch(() => setInfra(null));
  }, [app?.infrastructure_id]);

  useEffect(() => {
    if (!app) return;
    if (POLLING_STATUSES.includes(app.status) && !app.is_sleeping) {
      intervalRef.current = setInterval(loadApp, 3000);
    } else {
      if (intervalRef.current) clearInterval(intervalRef.current);
    }
    return () => { if (intervalRef.current) clearInterval(intervalRef.current); };
  }, [app, app?.status, app?.is_sleeping, loadApp]);

  const generateWebhook = async () => {
    setWebhookLoading(true);
    try {
      const creds = await applicationApi.rotateWebhookSecret(id);
      setWebhookCreds(creds);
      toast.success('Webhook secret generated. Save it now, it will not be shown again.');
    } catch (e: unknown) {
      const error = e as { response?: { data?: { error?: string } } };
      toast.error(error.response?.data?.error || 'Failed to generate webhook secret');
    } finally {
      setWebhookLoading(false);
    }
  };

  const startEditEnvs = () => {
    setEnvRows(Object.entries(app?.envs ?? {}));
    setEditingEnvs(true);
  };

  const saveEnvs = async () => {
    setSavingEnvs(true);
    try {
      const envs = Object.fromEntries(envRows.filter(([k]) => k.trim()));
      await applicationApi.update(id, { envs });
      setApp((prev) => (prev ? { ...prev, envs } : prev));
      toast.success('Environment variables updated');
      setEditingEnvs(false);
    } catch (e: unknown) {
      const error = e as { response?: { data?: { error?: string } } };
      toast.error(error.response?.data?.error || 'Failed to update variables');
    } finally {
      setSavingEnvs(false);
    }
  };

  const copyToClipboard = (value: string, label: string) => {
    navigator.clipboard.writeText(value).then(
      () => toast.success(`${label} copied`),
      () => toast.error(`Failed to copy ${label}`),
    );
  };

  const action = async (fn: () => Promise<void>, successMsg: string, key: typeof pendingAction = null) => {
    setActionLoading(true);
    setPendingAction(key);
    try {
      await fn();
      toast.success(successMsg);
      loadApp();
    } catch (e: unknown) {
      const error = e as { response?: { data?: { error?: string } } };
      toast.error(error.response?.data?.error || 'Action failed');
    } finally {
      setActionLoading(false);
      setPendingAction(null);
    }
  };

  if (loading) return (
    <div className="space-y-6" aria-busy="true" aria-label="Loading application">
      <Skeleton className="h-4 w-16" />
      <Skeleton className="h-7 w-56" />
      <Skeleton className="h-14 w-full" />
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <Skeleton className="h-36 w-full" />
        <Skeleton className="h-36 w-full" />
      </div>
    </div>
  );

  if (!app) return (
    <EmptyState
      icon={PackageX}
      title="Application not found"
      description="It may have been deleted, or you may not have access to it."
      action={<Link href="/dashboard/applications" className={buttonVariants({ variant: 'outline' })}>Back to applications</Link>}
    />
  );

  const displayStatus = app.is_sleeping ? 'SLEEPING' : app.status;

  return (
    <div className="reveal space-y-6">
      <button onClick={() => router.back()} className="flex items-center gap-1.5 rounded-md text-xs text-muted-foreground transition-colors hover:text-foreground">
        <ArrowLeft className="size-3.5" /> Back
      </button>

      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <h1 className="truncate text-xl font-semibold tracking-tight text-foreground">{app.name}</h1>
            <StatusBadge status={displayStatus} />
            {app.auto_deploy_paused && (
              <span className="inline-flex items-center gap-1 rounded-md border border-warning/30 bg-warning/10 px-1.5 py-0.5 text-xs text-foreground">
                <Pin className="size-3 text-warning" />
                Pinned
              </span>
            )}
          </div>
          {app.description && <p className="mt-1.5 text-sm text-muted-foreground">{app.description}</p>}
        </div>
        {canEdit && !app.infrastructure_exited && (
          <Button variant="outline" size="sm" onClick={() => setEditOpen(true)}>
            <Pencil className="size-3.5" /> Edit
          </Button>
        )}
      </div>

      {app.infrastructure_exited && (
        <div className="flex items-start gap-3 rounded-lg border border-warning/30 bg-warning/10 px-4 py-3">
          <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warning" />
          <div>
            <p className="text-sm font-medium text-foreground">Infrastructure exited</p>
            <p className="mt-0.5 text-xs text-muted-foreground">
              This application&apos;s infrastructure has completed the exit flow. Deploys, rollbacks,
              config changes and auto-deploy on push are disabled. Runtime logs still work.
            </p>
          </div>
        </div>
      )}

      {app.host_url && <UrlRow label="Host URL" url={app.host_url} />}
      {app.deployment_url && <UrlRow label={app.host_url ? 'Path URL' : 'Live URL'} url={app.deployment_url} />}

      {POLLING_STATUSES.includes(app.status) && !app.is_sleeping && (
        <div role="status" className="flex items-center gap-3 rounded-lg border border-border bg-surface-1 px-4 py-3">
          <span className="size-1.5 shrink-0 animate-pulse rounded-full bg-brand" />
          <p className="text-sm text-foreground">{IN_FLIGHT_HINT[app.status]}</p>
        </div>
      )}

      {canSeeLogs && IN_PROGRESS_BUILD_STATUSES.includes(app.status) && (
        <BuildLogs appId={id} />
      )}

      {app.is_sleeping && (
        <div className="flex items-center gap-3 rounded-lg border border-border bg-surface-1 px-4 py-3">
          <Moon className="size-3.5 shrink-0 text-muted-foreground" />
          <p className="text-sm text-foreground">Scaled to zero. Use Wake up to bring it back.</p>
        </div>
      )}

      {app.error_message && (
        <div className="rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3">
          <p className="mb-1 text-sm font-medium text-foreground">{app.status === 'FAILED' ? 'Last deploy failed' : 'Error'}</p>
          <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words font-mono text-xs text-muted-foreground">{app.error_message}</pre>
        </div>
      )}

      <div className="flex flex-wrap gap-2">
        {canDeployNow && app.status === 'ACTIVE' && !app.is_sleeping && (
          <>
            {!app.infrastructure_exited && (
              <Button size="sm" onClick={() => action(() => applicationApi.deploy(id), 'Redeployment queued.', 'deploy')} disabled={actionLoading}>
                <RefreshCw className={`size-3.5 ${pendingAction === 'deploy' ? 'animate-spin' : ''}`} /> {pendingAction === 'deploy' ? 'Queuing…' : 'Redeploy'}
              </Button>
            )}
            {!app.infrastructure_exited && (
              <Button variant="outline" size="sm"
                onClick={() => action(() => applicationApi.sleep(id), 'Application sleeping', 'sleep')} disabled={actionLoading}>
                <Moon className="size-3.5" /> {pendingAction === 'sleep' ? 'Sleeping…' : 'Sleep'}
              </Button>
            )}
          </>
        )}
        {canDeployNow && app.is_sleeping && !app.infrastructure_exited && (
          <Button size="sm" onClick={() => action(() => applicationApi.wake(id), 'Application waking up', 'wake')} disabled={actionLoading}>
            <Sun className="size-3.5" /> {pendingAction === 'wake' ? 'Waking…' : 'Wake up'}
          </Button>
        )}
        {canDeployNow && app.status === 'FAILED' && !app.infrastructure_exited && (
          <Button size="sm" onClick={() => action(() => applicationApi.deploy(id), 'Retry queued', 'retry')} disabled={actionLoading}>
            <RefreshCw className={`size-3.5 ${pendingAction === 'retry' ? 'animate-spin' : ''}`} /> {pendingAction === 'retry' ? 'Queuing…' : 'Retry'}
          </Button>
        )}
        {app.auto_deploy_paused && canRollbackRole && hasRollbackAccess && !app.infrastructure_exited && (
          <Button variant="outline" size="sm"
            onClick={() => action(() => applicationApi.resumeAutoDeploy(id), 'Auto-deploy resumed')} disabled={actionLoading}>
            <PlayCircle className="size-3.5" /> Resume auto-deploy
          </Button>
        )}
        {canDeleteApp && (
          <Button variant="destructive" size="sm" className="ml-auto"
            onClick={() => setDeleteOpen(true)} disabled={actionLoading}>
            <Trash2 className="size-3.5" /> Delete
          </Button>
        )}
      </div>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <Panel title="Repository">
          <dl className="space-y-2.5">
            <div className="flex items-start justify-between gap-4">
              <dt className="shrink-0 text-xs text-muted-foreground">URL</dt>
              <dd className="text-right">
                <a href={app.url} target="_blank" rel="noopener noreferrer"
                  className="break-all rounded-sm font-mono text-xs text-brand hover:underline">{app.url}</a>
              </dd>
            </div>
            {[['Branch', app.branch], ['Dockerfile', app.dockerfile_path], ...(app.build_context ? [['Build context', app.build_context]] : [])].map(([k, v]) => (
              <div key={k} className="flex items-center justify-between gap-4">
                <dt className="text-xs text-muted-foreground">{k}</dt>
                <dd className="truncate font-mono text-xs text-foreground">{v}</dd>
              </div>
            ))}
          </dl>
        </Panel>

        <Panel title="Resources">
          <dl className="space-y-2.5">
            {[['CPU', `${app.cpu} vCPU`], ['Memory', `${app.memory} GB`], ['Port', String(app.port)]].map(([k, v]) => (
              <div key={k} className="flex items-center justify-between">
                <dt className="text-xs text-muted-foreground">{k}</dt>
                <dd className="font-mono text-xs text-foreground">{v}</dd>
              </div>
            ))}
          </dl>
        </Panel>
      </div>

      <section className="rounded-lg border border-border bg-card p-4">
        <div className="flex items-start justify-between gap-4">
          <div className="flex items-start gap-3">
            <Github className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
            <div>
              <h2 className="text-sm font-medium text-foreground">Auto-deploy on push</h2>
              <p className="mt-0.5 text-xs text-muted-foreground">
                Redeploy on every push to{' '}
                <span className="font-mono text-foreground">{app.branch}</span>.
              </p>
            </div>
          </div>
          {canEdit && (
            <Button onClick={generateWebhook} disabled={webhookLoading} variant="outline" size="sm" className="shrink-0">
              <RefreshCw className={cn('size-3', webhookLoading && 'animate-spin')} />
              {webhookCreds ? 'Rotate secret' : 'Generate webhook secret'}
            </Button>
          )}
        </div>
        {webhookCreds && (
          <div className="mt-4 space-y-3">
            <div className="rounded-md border border-warning/30 bg-warning/10 px-3 py-2">
              <p className="text-xs text-foreground">This secret is shown once. Copy it now and store it somewhere safe.</p>
            </div>
            <div className="space-y-2.5">
              <div>
                <p className="mb-1.5 text-xs font-medium text-foreground/90">Webhook URL</p>
                <div className="flex items-center gap-2">
                  <code className="flex-1 break-all rounded-md border border-border bg-surface-1 px-2.5 py-1.5 font-mono text-xs text-foreground">
                    {webhookCreds.webhook_url}
                  </code>
                  <button
                    type="button"
                    onClick={() => copyToClipboard(webhookCreds.webhook_url, 'URL')}
                    className={cn('p-1.5', FOCUS_ICON)}
                    aria-label="Copy webhook URL"
                  >
                    <Copy className="size-3.5" />
                  </button>
                </div>
              </div>
              <div>
                <p className="mb-1.5 text-xs font-medium text-foreground/90">Secret</p>
                <div className="flex items-center gap-2">
                  <code className="flex-1 break-all rounded-md border border-border bg-surface-1 px-2.5 py-1.5 font-mono text-xs text-foreground">
                    {webhookCreds.secret}
                  </code>
                  <button
                    type="button"
                    onClick={() => copyToClipboard(webhookCreds.secret, 'Secret')}
                    className={cn('p-1.5', FOCUS_ICON)}
                    aria-label="Copy webhook secret"
                  >
                    <Copy className="size-3.5" />
                  </button>
                </div>
              </div>
              <p className="text-xs leading-relaxed text-muted-foreground">{webhookCreds.instructions}</p>
            </div>
          </div>
        )}
      </section>

      {(canEditEnvs || (app.envs && Object.keys(app.envs).length > 0)) && (
        <Panel
          title="Environment variables"
          aside={
            <div className="flex items-center gap-3">
              {editingEnvs ? (
                <>
                  <button onClick={() => setEditingEnvs(false)} disabled={savingEnvs}
                    className="rounded-sm text-xs text-muted-foreground transition-colors hover:text-foreground disabled:opacity-40">
                    Cancel
                  </button>
                  <Button size="sm" onClick={saveEnvs} disabled={savingEnvs}>
                    {savingEnvs ? 'Saving…' : 'Save'}
                  </Button>
                </>
              ) : (
                <>
                  {app.envs && Object.keys(app.envs).length > 0 && (
                    <span className="text-xs text-muted-foreground">{Object.keys(app.envs).length} variables</span>
                  )}
                  {canEditEnvs && !app.infrastructure_exited && (
                    <button onClick={startEditEnvs}
                      className="flex items-center gap-1 rounded-sm text-xs text-muted-foreground transition-colors hover:text-foreground">
                      <Pencil className="size-3" /> Edit
                    </button>
                  )}
                </>
              )}
            </div>
          }
        >
          {editingEnvs ? (
            <EnvEditor envs={envRows} onChange={setEnvRows} hideTitle />
          ) : app.envs && Object.keys(app.envs).length > 0 ? (
            <div className="max-h-80 space-y-1.5 overflow-y-auto pr-1 font-mono">
              {Object.entries(app.envs).map(([key, value]) => {
                const revealed = revealedEnvs.has(key);
                const masked = value.length <= 4
                  ? '*'.repeat(value.length)
                  : `${value.slice(0, Math.ceil(value.length * 0.2))}${'*'.repeat(Math.max(1, value.length - Math.ceil(value.length * 0.4)))}${value.slice(-Math.ceil(value.length * 0.2))}`;
                return (
                  <div key={key} className="flex items-center gap-2 text-xs">
                    <span className="shrink-0 text-brand">{key}</span>
                    <span className="text-muted-foreground">=</span>
                    <span className="flex-1 break-all text-foreground">{revealed ? value : masked}</span>
                    <button
                      type="button"
                      onClick={() => setRevealedEnvs(prev => {
                        const next = new Set(prev);
                        if (revealed) next.delete(key);
                        else next.add(key);
                        return next;
                      })}
                      className={cn('ml-1 shrink-0', FOCUS_ICON)}
                      aria-label={`${revealed ? 'Hide' : 'Reveal'} ${key}`}
                    >
                      {revealed ? <EyeOff className="size-3.5" /> : <Eye className="size-3.5" />}
                    </button>
                  </div>
                );
              })}
            </div>
          ) : (
            <p className="text-xs text-muted-foreground">No environment variables yet. Use Edit to add some.</p>
          )}
        </Panel>
      )}

      <DeploymentHistory
        appId={id}
        appStatus={app.status}
        canRollback={!!canRollbackRole && !app.infrastructure_exited}
        onRolledBack={() => loadApp()}
        onAccessChange={setHasRollbackAccess}
      />

      {canSeeLogs && infra && (
        <div className="space-y-3">
          <h2 className="text-sm font-medium text-foreground">Observability</h2>
          <AppMetricsPanel appId={id} infraId={infra.id} computeType={infra.compute_type} />
          <AppLiveLogs appId={id} infraId={infra.id} computeType={infra.compute_type} />
        </div>
      )}

      {canManageDomains && infra && <CustomDomainsPanel appId={id} infraId={infra.id} />}

      <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle className="text-base font-semibold">Delete application</DialogTitle>
            <DialogDescription>
              Delete <span className="text-foreground font-mono">{app.name}</span>? This can’t be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteOpen(false)}>Cancel</Button>
            <Button variant="destructive" disabled={actionLoading}
              onClick={() => action(async () => {
                await applicationApi.delete(id);
                router.push('/dashboard');
              }, 'Application deleted', 'delete')}>
              {pendingAction === 'delete' ? 'Deleting…' : 'Delete'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {editOpen && (
        <EditAppSheet
          app={app}
          open={editOpen}
          onClose={() => setEditOpen(false)}
          onSaved={() => loadApp()}
        />
      )}
    </div>
  );
}
