'use client';

import { useEffect, useState, useRef, useCallback } from 'react';
import { useRouter, useParams } from 'next/navigation';
import { motion } from 'framer-motion';
import { Button } from '@/components/ui/button';
import { ArrowLeft, ExternalLink, RefreshCw, Moon, Sun, Trash2, Pencil, Eye, EyeOff, Github, Copy, Globe, PackageX } from 'lucide-react';
import { Application } from '@/types/application';
import { applicationApi } from '@/lib/api/applications';
import { useAuthStore } from '@/lib/store/auth';
import { toast } from 'sonner';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import { EditAppSheet } from '@/components/edit-app-sheet';
import { EnvEditor } from '@/components/env-editor';

const POLLING_STATUSES = ['CREATED', 'BUILDING', 'PUSHING_IMAGE', 'DEPLOYING'];

const STATUS: Record<string, { dot: string; label: string }> = {
  ACTIVE: { dot: 'bg-success', label: 'text-success' },
  BUILDING: { dot: 'bg-azure animate-pulse', label: 'text-azure' },
  DEPLOYING: { dot: 'bg-azure animate-pulse', label: 'text-azure' },
  PUSHING_IMAGE: { dot: 'bg-azure animate-pulse', label: 'text-azure' },
  CREATED: { dot: 'bg-warning animate-pulse', label: 'text-warning' },
  SLEEPING: { dot: 'bg-warning', label: 'text-warning' },
  FAILED: { dot: 'bg-destructive', label: 'text-destructive' },
};

const rise = { initial: { opacity: 0, y: 14 }, animate: { opacity: 1, y: 0, transition: { duration: 0.4, ease: [0.16, 1, 0.3, 1] as const } } };

export default function ApplicationDetailPage() {
  const router = useRouter();
  const { id } = useParams<{ id: string }>();
  const [app, setApp] = useState<Application | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [revealedEnvs, setRevealedEnvs] = useState<Set<string>>(new Set());
  const [webhookCreds, setWebhookCreds] = useState<{ webhook_url: string; secret: string; instructions: string } | null>(null);
  const [webhookLoading, setWebhookLoading] = useState(false);
  const [editingEnvs, setEditingEnvs] = useState(false);
  const [envRows, setEnvRows] = useState<[string, string][]>([]);
  const [savingEnvs, setSavingEnvs] = useState(false);
  const intervalRef = useRef<NodeJS.Timeout | null>(null);
  const user = useAuthStore((s) => s.user);
  const canEdit = user?.role === 'super_admin' || user?.role === 'admin';

  const loadApp = useCallback(async () => {
    try {
      const data = await applicationApi.get(id);
      setApp(data);
      return data;
    } catch (e: unknown) {
      const error = e as { response?: { data?: { error?: string } } };
      toast.error(error.response?.data?.error || 'Failed to load application');
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    loadApp();
  }, [id, loadApp]);

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
      toast.success('Webhook secret generated. Save it now — it will not be shown again.');
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

  const action = async (fn: () => Promise<void>, successMsg: string) => {
    setActionLoading(true);
    try {
      await fn();
      toast.success(successMsg);
      loadApp();
    } catch (e: unknown) {
      const error = e as { response?: { data?: { error?: string } } };
      toast.error(error.response?.data?.error || 'Action failed');
    } finally {
      setActionLoading(false);
    }
  };

  if (loading) return (
    <div className="space-y-6">
      <div className="h-4 w-16 rounded panel animate-pulse" />
      <div className="h-8 w-56 rounded-lg panel animate-pulse" />
      <div className="h-16 rounded-xl panel animate-pulse" />
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div className="h-40 rounded-xl panel animate-pulse" />
        <div className="h-40 rounded-xl panel animate-pulse" />
      </div>
    </div>
  );

  if (!app) return (
    <div className="relative overflow-hidden rounded-2xl panel-inset px-8 py-20 text-center">
      <div className="pointer-events-none absolute inset-0 brand-glow opacity-60" />
      <div className="relative">
        <div className="w-14 h-14 rounded-2xl bg-surface-2 border border-hairline-strong flex items-center justify-center mx-auto mb-5">
          <PackageX className="w-6 h-6 text-muted-foreground" />
        </div>
        <p className="text-base font-display font-medium text-foreground mb-1.5">Application not found</p>
        <p className="text-sm text-muted-foreground mb-6 max-w-sm mx-auto">It may have been deleted, or you don&apos;t have access to it.</p>
        <Button variant="outline" size="lg" onClick={() => router.push('/dashboard')} className="gap-1.5">
          <ArrowLeft className="w-4 h-4" /> Back to dashboard
        </Button>
      </div>
    </div>
  );

  const displayStatus = app.is_sleeping ? 'SLEEPING' : app.status;
  const st = STATUS[displayStatus] ?? { dot: 'bg-muted-foreground', label: 'text-muted-foreground' };

  return (
    <motion.div {...rise} className="space-y-8">
      <button onClick={() => router.back()} className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors">
        <ArrowLeft className="w-3.5 h-3.5" /> Back
      </button>

      <div className="flex items-start justify-between gap-4">
        <div>
          <span className="eyebrow">Console / Application</span>
          <div className="mt-2 flex items-center gap-3">
            <h1 className="text-2xl font-display font-semibold text-foreground tracking-tight">{app.name}</h1>
            <span className="flex items-center gap-1.5">
              <span className={`w-1.5 h-1.5 rounded-full ${st.dot}`} />
              <span className={`font-mono text-[10px] uppercase tracking-[0.12em] ${st.label}`}>{displayStatus}</span>
            </span>
          </div>
          {app.description && <p className="text-sm text-muted-foreground mt-1.5">{app.description}</p>}
        </div>
        {canEdit && (
          <Button variant="outline" size="sm" onClick={() => setEditOpen(true)} className="gap-1.5">
            <Pencil className="w-3.5 h-3.5" /> Edit
          </Button>
        )}
      </div>

      {app.deployment_url && (
        <a href={app.deployment_url} target="_blank" rel="noopener noreferrer"
          className="group flex items-center gap-4 rounded-xl border border-azure/30 bg-azure/10 px-4 py-3.5 transition-colors hover:border-azure/50 hover:bg-azure/[0.14] outline-none focus-visible:ring-2 focus-visible:ring-ring/60">
          <span className="w-10 h-10 rounded-lg bg-azure/15 border border-azure/30 flex items-center justify-center shrink-0">
            <Globe className="w-4 h-4 text-azure" />
          </span>
          <div className="min-w-0 flex-1">
            <span className="eyebrow text-azure">Live URL</span>
            <p className="mt-0.5 text-sm font-mono text-azure truncate">{app.deployment_url}</p>
          </div>
          <ExternalLink className="w-4 h-4 text-azure/70 shrink-0 transition-transform group-hover:-translate-y-0.5 group-hover:translate-x-0.5" />
        </a>
      )}

      {app.error_message && (
        <div className="rounded-xl border border-destructive/30 bg-destructive/10 px-4 py-3">
          <p className="eyebrow text-destructive mb-1">Error</p>
          <p className="text-xs text-destructive/80 font-mono break-all">{app.error_message}</p>
        </div>
      )}

      <div className="flex gap-2 flex-wrap">
        {app.status === 'ACTIVE' && !app.is_sleeping && (
          <>
            <Button size="sm" onClick={() => action(() => applicationApi.deploy(id), 'Redeployment queued')} disabled={actionLoading} className="gap-1.5">
              <RefreshCw className="w-3.5 h-3.5" /> Redeploy
            </Button>
            <Button variant="outline" size="sm" className="gap-1.5"
              onClick={() => action(() => applicationApi.sleep(id), 'Application sleeping')} disabled={actionLoading}>
              <Moon className="w-3.5 h-3.5" /> Sleep
            </Button>
          </>
        )}
        {app.is_sleeping && (
          <Button size="sm" onClick={() => action(() => applicationApi.wake(id), 'Application waking up')} disabled={actionLoading} className="gap-1.5">
            <Sun className="w-3.5 h-3.5" /> Wake Up
          </Button>
        )}
        {app.status === 'FAILED' && (
          <Button size="sm" onClick={() => action(() => applicationApi.deploy(id), 'Retry queued')} disabled={actionLoading} className="gap-1.5">
            <RefreshCw className="w-3.5 h-3.5" /> Retry
          </Button>
        )}
        <Button variant="destructive" size="sm" className="gap-1.5 ml-auto"
          onClick={() => setDeleteOpen(true)} disabled={actionLoading}>
          <Trash2 className="w-3.5 h-3.5" /> Delete
        </Button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div className="rounded-xl panel p-4">
          <p className="eyebrow mb-3">Repository</p>
          <div className="space-y-2.5">
            <div className="flex items-start justify-between gap-4">
              <span className="text-xs text-muted-foreground shrink-0">URL</span>
              <a href={app.url} target="_blank" rel="noopener noreferrer"
                className="text-xs text-azure hover:text-azure/80 break-all text-right font-mono transition-colors">{app.url}</a>
            </div>
            {[['Branch', app.branch], ['Dockerfile', app.dockerfile_path], ...(app.build_context ? [['Build Context', app.build_context]] : [])].map(([k, v]) => (
              <div key={k} className="flex items-center justify-between gap-4">
                <span className="text-xs text-muted-foreground">{k}</span>
                <span className="text-xs text-foreground font-mono truncate">{v}</span>
              </div>
            ))}
          </div>
        </div>

        <div className="rounded-xl panel p-4">
          <p className="eyebrow mb-3">Resources</p>
          <div className="space-y-2.5">
            {[['CPU', `${app.cpu} vCPU`], ['Memory', `${app.memory} GB`], ['Port', String(app.port)]].map(([k, v]) => (
              <div key={k} className="flex items-center justify-between">
                <span className="text-xs text-muted-foreground">{k}</span>
                <span className="text-xs text-foreground font-mono">{v}</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="rounded-xl panel p-4">
        <div className="flex items-start justify-between gap-4">
          <div className="flex items-start gap-3">
            <span className="w-9 h-9 rounded-lg bg-surface-3 border border-hairline flex items-center justify-center shrink-0">
              <Github className="w-4 h-4 text-foreground" />
            </span>
            <div>
              <p className="text-sm font-medium text-foreground">Auto-deploy on push</p>
              <p className="text-xs text-muted-foreground mt-0.5">
                Redeploy automatically on every push to{' '}
                <span className="font-mono text-foreground">{app.branch}</span>.
              </p>
            </div>
          </div>
          {canEdit && (
            <Button
              onClick={generateWebhook}
              disabled={webhookLoading}
              variant="outline"
              size="sm"
              className="gap-1.5 shrink-0"
            >
              <RefreshCw className={`w-3 h-3 ${webhookLoading ? 'animate-spin' : ''}`} />
              {webhookCreds ? 'Rotate secret' : 'Generate webhook secret'}
            </Button>
          )}
        </div>
        {webhookCreds && (
          <div className="space-y-3 mt-4">
            <div className="rounded-lg border border-warning/30 bg-warning/10 px-3 py-2">
              <p className="text-[11px] text-warning">This secret is shown only once. Copy it now and store it safely.</p>
            </div>
            <div className="space-y-2.5">
              <div>
                <p className="eyebrow mb-1.5">Webhook URL</p>
                <div className="flex items-center gap-2">
                  <code className="flex-1 text-xs text-foreground bg-surface-1 border border-hairline rounded-lg px-2.5 py-1.5 break-all font-mono">
                    {webhookCreds.webhook_url}
                  </code>
                  <button
                    onClick={() => copyToClipboard(webhookCreds.webhook_url, 'URL')}
                    className="text-muted-foreground hover:text-foreground transition-colors p-1.5 rounded outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
                    title="Copy URL"
                  >
                    <Copy className="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>
              <div>
                <p className="eyebrow mb-1.5">Secret</p>
                <div className="flex items-center gap-2">
                  <code className="flex-1 text-xs text-foreground bg-surface-1 border border-hairline rounded-lg px-2.5 py-1.5 break-all font-mono">
                    {webhookCreds.secret}
                  </code>
                  <button
                    onClick={() => copyToClipboard(webhookCreds.secret, 'Secret')}
                    className="text-muted-foreground hover:text-foreground transition-colors p-1.5 rounded outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
                    title="Copy secret"
                  >
                    <Copy className="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>
              <p className="text-[11px] text-muted-foreground leading-relaxed">{webhookCreds.instructions}</p>
            </div>
          </div>
        )}
      </div>

      {(canEdit || (app.envs && Object.keys(app.envs).length > 0)) && (
        <div className="rounded-xl panel p-4">
          <div className="flex items-center justify-between mb-3">
            <p className="eyebrow">Environment Variables</p>
            <div className="flex items-center gap-3">
              {editingEnvs ? (
                <>
                  <button onClick={() => setEditingEnvs(false)} disabled={savingEnvs}
                    className="text-[10px] font-mono uppercase tracking-widest text-muted-foreground/70 hover:text-foreground transition-colors disabled:opacity-40">
                    Cancel
                  </button>
                  <Button size="sm" onClick={saveEnvs} disabled={savingEnvs} className="h-7">
                    {savingEnvs ? 'Saving…' : 'Save'}
                  </Button>
                </>
              ) : (
                <>
                  {app.envs && Object.keys(app.envs).length > 0 && (
                    <span className="font-mono text-[10px] text-muted-foreground/60">{Object.keys(app.envs).length} vars</span>
                  )}
                  {canEdit && (
                    <button onClick={startEditEnvs}
                      className="flex items-center gap-1 text-[10px] font-mono uppercase tracking-widest text-muted-foreground/70 hover:text-brand transition-colors">
                      <Pencil className="w-3 h-3" /> Edit
                    </button>
                  )}
                </>
              )}
            </div>
          </div>
          {editingEnvs ? (
            <EnvEditor envs={envRows} onChange={setEnvRows} hideTitle />
          ) : app.envs && Object.keys(app.envs).length > 0 ? (
            <div className="space-y-1.5 font-mono max-h-80 overflow-y-auto pr-1">
              {Object.entries(app.envs).map(([key, value]) => {
                const revealed = revealedEnvs.has(key);
                const masked = value.length <= 4
                  ? '*'.repeat(value.length)
                  : `${value.slice(0, Math.ceil(value.length * 0.2))}${'*'.repeat(Math.max(1, value.length - Math.ceil(value.length * 0.4)))}${value.slice(-Math.ceil(value.length * 0.2))}`;
                return (
                  <div key={key} className="flex items-center gap-2 text-xs">
                    <span className="text-brand shrink-0">{key}</span>
                    <span className="text-muted-foreground/60">=</span>
                    <span className="text-foreground break-all flex-1">{revealed ? value : masked}</span>
                    <button
                      onClick={() => setRevealedEnvs(prev => {
                        const next = new Set(prev);
                        if (revealed) next.delete(key);
                        else next.add(key);
                        return next;
                      })}
                      className="shrink-0 text-muted-foreground/60 hover:text-foreground transition-colors ml-1 outline-none focus-visible:ring-2 focus-visible:ring-ring/60 rounded"
                      title={revealed ? 'Hide' : 'Reveal'}
                    >
                      {revealed ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
                    </button>
                  </div>
                );
              })}
            </div>
          ) : (
            <p className="text-xs text-muted-foreground">No environment variables yet. Click Edit to add some.</p>
          )}
        </div>
      )}

      <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle className="text-base font-display font-semibold">Delete Application</DialogTitle>
            <DialogDescription className="text-xs text-muted-foreground">
              Delete <span className="text-foreground font-mono">{app.name}</span>? This cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteOpen(false)}>Cancel</Button>
            <Button variant="destructive" disabled={actionLoading}
              onClick={() => action(async () => {
                await applicationApi.delete(id);
                router.push('/dashboard');
              }, 'Application deleted')}>
              {actionLoading ? 'Deleting…' : 'Delete'}
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
    </motion.div>
  );
}
