'use client';

import { useEffect, useState, useRef, useCallback } from 'react';
import { useRouter, useParams } from 'next/navigation';
import { Button } from '@/components/ui/button';
import { ArrowLeft, ExternalLink, RefreshCw, Moon, Sun, Trash2, Pencil, Eye, EyeOff } from 'lucide-react';
import { Application } from '@/types/application';
import { applicationApi } from '@/lib/api/applications';
import { useAuthStore } from '@/lib/store/auth';
import { toast } from 'sonner';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import { EditAppSheet } from '@/components/edit-app-sheet';

const POLLING_STATUSES = ['CREATED', 'BUILDING', 'PUSHING_IMAGE', 'DEPLOYING'];

export default function ApplicationDetailPage() {
  const router = useRouter();
  const { id } = useParams<{ id: string }>();
  const [app, setApp] = useState<Application | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [revealedEnvs, setRevealedEnvs] = useState<Set<string>>(new Set());
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
    if (POLLING_STATUSES.includes(app.status)) {
      intervalRef.current = setInterval(loadApp, 3000);
    } else {
      if (intervalRef.current) clearInterval(intervalRef.current);
    }
    return () => { if (intervalRef.current) clearInterval(intervalRef.current); };
  }, [app, app?.status, loadApp]);

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
    <div className="flex items-center justify-center h-64">
      <div className="w-5 h-5 border-2 border-[#333] border-t-violet-500 rounded-full animate-spin" />
    </div>
  );
  if (!app) return <div className="text-[#555] text-sm">Application not found</div>;

  const statusDot: Record<string, string> = {
    ACTIVE: 'bg-emerald-500',
    BUILDING: 'bg-blue-500 animate-pulse',
    DEPLOYING: 'bg-blue-500 animate-pulse',
    PUSHING_IMAGE: 'bg-blue-500 animate-pulse',
    SLEEPING: 'bg-amber-500',
    FAILED: 'bg-red-500',
    CREATED: 'bg-[#444]',
  };

  return (
    <div className="space-y-6">
      <button onClick={() => router.back()} className="flex items-center gap-1.5 text-xs text-[#666] hover:text-white transition-colors cursor-pointer">
        <ArrowLeft className="w-3.5 h-3.5" /> Back
      </button>

      {/* Header */}
      <div className="flex items-start justify-between">
        <div className="space-y-1.5">
          <div className="flex items-center gap-2.5">
            <h1 className="text-xl font-semibold text-white tracking-tight">{app.name}</h1>
            <div className="flex items-center gap-1.5">
              <span className={`w-1.5 h-1.5 rounded-full ${statusDot[app.status] || 'bg-[#444]'}`} />
              <span className="text-xs text-[#888] font-mono">{app.is_sleeping ? 'SLEEPING' : app.status}</span>
            </div>
          </div>
          {app.description && <p className="text-xs text-[#888]">{app.description}</p>}
        </div>
        {canEdit && (
          <Button variant="outline" onClick={() => setEditOpen(true)}
            className="border-[#1e1e1e] bg-transparent hover:bg-[#111] text-[#aaa] hover:text-white h-8 text-xs gap-1.5 cursor-pointer">
            <Pencil className="w-3.5 h-3.5" /> Edit
          </Button>
        )}
      </div>

      {app.deployment_url && (
        <div className="bg-[#0d0d0d] border border-[#1a1a1a] rounded-xl px-4 py-3 flex items-center justify-between">
          <span className="text-xs text-[#999]">Deployment URL</span>
          <a href={app.deployment_url} target="_blank" rel="noopener noreferrer"
            className="text-xs text-violet-400 hover:text-violet-300 flex items-center gap-1.5 font-mono transition-colors">
            {app.deployment_url} <ExternalLink className="w-3 h-3" />
          </a>
        </div>
      )}

      {app.error_message && (
        <div className="bg-red-500/5 border border-red-500/20 rounded-xl px-4 py-3">
          <p className="text-xs font-medium text-red-400 mb-1">Error</p>
          <p className="text-xs text-red-400/70 font-mono">{app.error_message}</p>
        </div>
      )}

      {/* Actions */}
      <div className="flex gap-2 flex-wrap">
        {app.status === 'ACTIVE' && !app.is_sleeping && (
          <>
            <Button onClick={() => action(() => applicationApi.deploy(id), 'Redeployment queued')} disabled={actionLoading}
              className="bg-violet-600 hover:bg-violet-700 h-8 text-xs gap-1.5 cursor-pointer">
              <RefreshCw className="w-3.5 h-3.5" /> Redeploy
            </Button>
            <Button variant="outline" className="border-[#1e1e1e] bg-transparent hover:bg-[#111] text-[#aaa] hover:text-white h-8 text-xs gap-1.5 cursor-pointer"
              onClick={() => action(() => applicationApi.sleep(id), 'Application sleeping')} disabled={actionLoading}>
              <Moon className="w-3.5 h-3.5" /> Sleep
            </Button>
          </>
        )}
        {app.is_sleeping && (
          <Button onClick={() => action(() => applicationApi.wake(id), 'Application waking up')} disabled={actionLoading}
            className="bg-violet-600 hover:bg-violet-700 h-8 text-xs gap-1.5 cursor-pointer">
            <Sun className="w-3.5 h-3.5" /> Wake Up
          </Button>
        )}
        {app.status === 'FAILED' && (
          <Button onClick={() => action(() => applicationApi.deploy(id), 'Retry queued')} disabled={actionLoading}
            className="bg-violet-600 hover:bg-violet-700 h-8 text-xs gap-1.5 cursor-pointer">
            <RefreshCw className="w-3.5 h-3.5" /> Retry
          </Button>
        )}
        <Button variant="outline" className="border-red-500/20 text-red-400 hover:bg-red-500/10 hover:text-red-400 h-8 text-xs gap-1.5 ml-auto cursor-pointer"
          onClick={() => setDeleteOpen(true)} disabled={actionLoading}>
          <Trash2 className="w-3.5 h-3.5" /> Delete
        </Button>
      </div>

      {/* Details grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div className="bg-[#0d0d0d] border border-[#1a1a1a] rounded-xl p-4">
          <p className="text-[10px] uppercase tracking-widest text-[#666] font-mono mb-3">Repository</p>
          <div className="space-y-2">
            <div className="flex items-start justify-between gap-4">
              <span className="text-xs text-[#888] shrink-0">URL</span>
              <a href={app.url} target="_blank" rel="noopener noreferrer"
                className="text-xs text-violet-400 hover:text-violet-300 break-all text-right transition-colors">{app.url}</a>
            </div>
            {[['Branch', app.branch], ['Dockerfile', app.dockerfile_path]].map(([k, v]) => (
              <div key={k} className="flex items-center justify-between">
                <span className="text-xs text-[#888]">{k}</span>
                <span className="text-xs text-[#ddd] font-mono">{v}</span>
              </div>
            ))}
          </div>
        </div>

        <div className="bg-[#0d0d0d] border border-[#1a1a1a] rounded-xl p-4">
          <p className="text-[10px] uppercase tracking-widest text-[#666] font-mono mb-3">Resources</p>
          <div className="space-y-2">
            {[['CPU', `${app.cpu} vCPU`], ['Memory', `${app.memory} GB`], ['Port', String(app.port)]].map(([k, v]) => (
              <div key={k} className="flex items-center justify-between">
                <span className="text-xs text-[#888]">{k}</span>
                <span className="text-xs text-[#ddd] font-mono">{v}</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      {app.envs && Object.keys(app.envs).length > 0 && (
        <div className="bg-[#0d0d0d] border border-[#1a1a1a] rounded-xl p-4">
          <p className="text-[10px] uppercase tracking-widest text-[#666] font-mono mb-3">Environment Variables</p>
          <div className="space-y-1.5 font-mono">
            {Object.entries(app.envs).map(([key, value]) => {
              const revealed = revealedEnvs.has(key);
              const masked = value.length <= 4
                ? '*'.repeat(value.length)
                : `${value.slice(0, Math.ceil(value.length * 0.2))}${'*'.repeat(Math.max(1, value.length - Math.ceil(value.length * 0.4)))}${value.slice(-Math.ceil(value.length * 0.2))}`;
              return (
                <div key={key} className="flex items-center gap-2 text-xs">
                  <span className="text-violet-400 shrink-0">{key}</span>
                  <span className="text-[#666]">=</span>
                  <span className="text-[#ccc] break-all flex-1">{revealed ? value : masked}</span>
                  <button
                    onClick={() => setRevealedEnvs(prev => {
                      const next = new Set(prev);
                      if (revealed) next.delete(key);
                      else next.add(key);
                      return next;
                    })}
                    className="shrink-0 text-[#444] hover:text-[#aaa] transition-colors ml-1"
                    title={revealed ? 'Hide' : 'Reveal'}
                  >
                    {revealed ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
                  </button>
                </div>
              );
            })}
          </div>
        </div>
      )}

      <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <DialogContent className="bg-[#090909] border-[#1a1a1a] shadow-2xl shadow-black/60">
          <DialogHeader>
            <DialogTitle className="text-base font-semibold">Delete Application</DialogTitle>
            <DialogDescription className="text-xs text-[#555]">
              Delete <span className="text-[#aaa] font-mono">{app.name}</span>? This cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteOpen(false)}
              className="border-[#1e1e1e] bg-transparent hover:bg-[#111] text-[#888] h-9 text-sm">Cancel</Button>
            <Button className="bg-red-500/90 hover:bg-red-500 h-9 text-sm" disabled={actionLoading}
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
          onSaved={(updated) => setApp(updated)}
        />
      )}
    </div>
  );
}
