'use client';

import { useEffect, useState, useCallback } from 'react';
import { useRouter, useParams } from 'next/navigation';
import { motion } from 'framer-motion';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet';
import { ArrowLeft, Plus, Server, Cpu, HardDrive, ExternalLink, UserPlus, Copy, Check, Settings, Trash2, User, Pencil, RefreshCw, ShieldCheck } from 'lucide-react';
import { Infrastructure, InvitedUserSummary } from '@/types/infrastructure';
import { ApplicationSummary } from '@/types/application';
import { infrastructureApi } from '@/lib/api/infrastructures';
import { applicationApi } from '@/lib/api/applications';
import { authApi } from '@/lib/api/auth';
import { useAuthStore } from '@/lib/store/auth';
import { toast } from 'sonner';
import { DatabasesSection } from '@/components/databases-section';
import { PolicyRefreshDialog } from '@/components/policy-refresh-dialog';

const ROLE_COLORS: Record<string, string> = {
  super_admin: 'text-brand',
  admin: 'text-azure',
  user: 'text-success',
  guest: 'text-muted-foreground',
};

const STATUS: Record<string, { dot: string; badge: string; label: string }> = {
  ACTIVE: { dot: 'bg-success', badge: 'border-success/30 bg-success/10 text-success', label: 'text-success' },
  PROVISIONING: { dot: 'bg-azure animate-pulse', badge: 'border-azure/30 bg-azure/10 text-azure', label: 'text-azure' },
  DEPLOYING: { dot: 'bg-azure animate-pulse', badge: 'border-azure/30 bg-azure/10 text-azure', label: 'text-azure' },
  BUILDING: { dot: 'bg-azure animate-pulse', badge: 'border-azure/30 bg-azure/10 text-azure', label: 'text-azure' },
  PENDING: { dot: 'bg-warning animate-pulse', badge: 'border-warning/30 bg-warning/10 text-warning', label: 'text-warning' },
  ERROR: { dot: 'bg-destructive', badge: 'border-destructive/30 bg-destructive/10 text-destructive', label: 'text-destructive' },
  FAILED: { dot: 'bg-destructive', badge: 'border-destructive/30 bg-destructive/10 text-destructive', label: 'text-destructive' },
  DESTROYING: { dot: 'bg-warning animate-pulse', badge: 'border-warning/30 bg-warning/10 text-warning', label: 'text-warning' },
  DESTROYED: { dot: 'bg-muted-foreground', badge: 'border-hairline bg-surface-1 text-muted-foreground', label: 'text-muted-foreground' },
};

const statusOf = (status: string) =>
  STATUS[status] ?? { dot: 'bg-muted-foreground', badge: 'border-hairline bg-surface-1 text-muted-foreground', label: 'text-muted-foreground' };

const rise = { initial: { opacity: 0, y: 14 }, animate: { opacity: 1, y: 0, transition: { duration: 0.4, ease: [0.16, 1, 0.3, 1] as const } } };

export default function InfrastructureDetailPage() {
  const router = useRouter();
  const params = useParams();
  const id = params.id as string;
  const user = useAuthStore((s) => s.user);

  const [infra, setInfra] = useState<Infrastructure | null>(null);
  const [apps, setApps] = useState<ApplicationSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [removingUserId, setRemovingUserId] = useState<string | null>(null);
  const [editingName, setEditingName] = useState(false);
  const [infraName, setInfraName] = useState('');
  const [savingName, setSavingName] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [reprovisioning, setReprovisioning] = useState(false);
  const [refreshPolicyOpen, setRefreshPolicyOpen] = useState(false);

  // Invite dialog
  const [inviteOpen, setInviteOpen] = useState(false);
  const [inviteForm, setInviteForm] = useState({ email: '', password: '', user_name: '', role: 'user' as 'admin' | 'user' | 'guest' });
  const [inviting, setInviting] = useState(false);
  const [inviteUrl, setInviteUrl] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const isSuperAdmin = user?.role === 'super_admin';
  const canDeploy = isSuperAdmin || user?.role === 'admin';
  const loadData = useCallback(async () => {
    try {
      const [infraData, appsData] = await Promise.all([
        infrastructureApi.get(id),
        applicationApi.list(id),
      ]);
      setInfra(infraData);
      setApps(appsData);
    } catch (error: unknown) {
      const err = error as { response?: { data?: { error?: string } } };
      toast.error(err.response?.data?.error || 'Failed to load data', { id: 'infra-detail-load' });
    } finally {
      setLoading(false);
    }
  }, [id]);

  const isOwner = isSuperAdmin && infra?.user_id === user?.id;

  useEffect(() => {
    loadData();
    const interval = setInterval(() => {
      if (infra?.status === 'PENDING' || infra?.status === 'PROVISIONING') loadData();
    }, 5000);
    return () => clearInterval(interval);
  }, [id, infra?.status, loadData]);

  const handleRenameInfra = async () => {
    if (!infraName.trim() || infraName === infra?.name) { setEditingName(false); return; }
    setSavingName(true);
    try {
      await infrastructureApi.updateConfig(id, { name: infraName.trim() });
      toast.success('Infrastructure renamed');
      await loadData();
      setEditingName(false);
    } catch (error: unknown) {
      const err = error as { response?: { data?: { error?: string } } };
      toast.error(err.response?.data?.error || 'Failed to rename');
    } finally {
      setSavingName(false);
    }
  };

  const handleRemoveUser = async (targetUser: InvitedUserSummary) => {
    setRemovingUserId(targetUser.id);
    try {
      await infrastructureApi.removeUser(id, targetUser.id);
      toast.success(`${targetUser.user_name} removed`);
      await loadData();
    } catch (error: unknown) {
      const err = error as { response?: { data?: { error?: string } } };
      toast.error(err.response?.data?.error || 'Failed to remove user');
    } finally {
      setRemovingUserId(null);
    }
  };

  const handleInvite = async (e: React.FormEvent) => {
    e.preventDefault();
    setInviting(true);
    try {
      await authApi.inviteUser({ ...inviteForm, infra_id: id });
      const url = `${window.location.origin}/login?email=${encodeURIComponent(inviteForm.email)}`;
      setInviteUrl(url);
      toast.success('User invited successfully');
      await loadData();
    } catch (error: unknown) {
      const err = error as { response?: { data?: { error?: string } } };
      toast.error(err.response?.data?.error || 'Failed to invite user');
    } finally {
      setInviting(false);
    }
  };

  const copyUrl = () => {
    if (!inviteUrl) return;
    navigator.clipboard.writeText(inviteUrl);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const closeInviteDialog = () => {
    setInviteOpen(false);
    setInviteUrl(null);
    setInviteForm({ email: '', password: '', user_name: '', role: 'user' });
  };

  const handleDeleteInfra = async () => {
    setDeleting(true);
    try {
      await infrastructureApi.delete(id);
      const wasActive = infra?.status === 'ACTIVE';
      toast.success(wasActive ? 'Destroy queued — We are tearing down your AWS resources' : 'Infrastructure deleted');
      router.push('/dashboard/infrastructures');
    } catch (error: unknown) {
      const err = error as { response?: { data?: { error?: string } } };
      toast.error(err.response?.data?.error || 'Failed to delete infrastructure');
    } finally {
      setDeleting(false);
      setDeleteOpen(false);
    }
  };

  const handleReprovision = async () => {
    setReprovisioning(true);
    try {
      await infrastructureApi.reprovision(id);
      toast.success('Re-provisioning queued');
      await loadData();
    } catch (error: unknown) {
      const err = error as { response?: { data?: { error?: string } } };
      toast.error(err.response?.data?.error || 'Failed to queue re-provisioning');
    } finally {
      setReprovisioning(false);
    }
  };

  if (loading) return (
    <div className="space-y-6">
      <div className="h-3 w-16 rounded panel animate-pulse" />
      <div className="flex items-start justify-between">
        <div className="space-y-2.5">
          <div className="h-3 w-40 rounded panel animate-pulse" />
          <div className="h-7 w-56 rounded panel animate-pulse" />
        </div>
        <div className="h-8 w-40 rounded-lg panel animate-pulse" />
      </div>
      <div className="grid grid-cols-3 gap-3">
        {Array.from({ length: 3 }).map((_, i) => (
          <div key={i} className="h-24 rounded-xl panel animate-pulse" />
        ))}
      </div>
      <div className="h-40 rounded-xl panel animate-pulse" />
    </div>
  );

  if (!infra) return (
    <div className="rounded-2xl panel-inset px-8 py-20 text-center">
      <p className="text-sm text-muted-foreground">Infrastructure not found.</p>
    </div>
  );

  const st = statusOf(infra.status);

  return (
    <motion.div {...rise} className="space-y-6">
      <button onClick={() => router.back()} className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors">
        <ArrowLeft className="w-3.5 h-3.5" /> Back
      </button>

      <div className="flex items-start justify-between gap-4">
        <div className="space-y-1.5">
          <span className="eyebrow">Console / Infrastructure</span>
          <div className="flex items-center gap-2.5">
            <h1 className="text-xl font-display font-semibold text-foreground tracking-tight">{infra.name}</h1>
            <span className={`font-mono text-[10px] uppercase tracking-[0.12em] px-2 py-0.5 rounded-md border ${st.badge}`}>
              {infra.status}
            </span>
            <span className="font-mono text-[10px] uppercase tracking-[0.12em] px-2 py-0.5 rounded-md border border-hairline bg-surface-1 text-muted-foreground">
              {infra.compute_type === 'eks' ? 'Kubernetes' : 'ECS Fargate'}
            </span>
            {infra.is_mock && (
              <span className="font-mono text-[10px] uppercase tracking-[0.12em] px-2 py-0.5 rounded-md border border-brand/30 bg-brand-soft text-brand">
                Mock / Dev
              </span>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2">
          {isSuperAdmin && (infra.status === 'ERROR' || infra.status === 'DESTROYED' || infra.status === 'PENDING') && (
            <Button variant="outline" size="sm" onClick={handleReprovision} disabled={reprovisioning} className="gap-1.5 text-brand hover:text-brand">
              <RefreshCw className={`w-3.5 h-3.5 ${reprovisioning ? 'animate-spin' : ''}`} /> Reprovision
            </Button>
          )}
          {isSuperAdmin && (
            <Button variant="outline" size="sm" onClick={() => setInviteOpen(true)} className="gap-1.5">
              <UserPlus className="w-3.5 h-3.5" /> Invite
            </Button>
          )}
          <Button variant="outline" size="sm" onClick={() => setSettingsOpen(true)} className="gap-1.5">
            <Settings className="w-3.5 h-3.5" /> Settings
          </Button>
        </div>
      </div>

      {infra.is_mock && (
        <div className="rounded-xl border border-brand/30 bg-brand-soft px-4 py-3 flex items-center gap-3">
          <div className="w-1.5 h-1.5 rounded-full bg-brand shrink-0" />
          <p className="text-xs text-brand">
            This is a MOCK / DEV infrastructure. No real AWS resources exist and the load balancer link is a dead placeholder.
          </p>
        </div>
      )}

      {(infra.status === 'PROVISIONING' || infra.status === 'PENDING') && (
        <div className="rounded-xl border border-azure/20 bg-azure/5 px-4 py-3 flex items-center gap-3">
          <div className="w-1.5 h-1.5 rounded-full bg-azure animate-pulse shrink-0" />
          <p className="text-xs text-azure">
            {infra.status === 'PROVISIONING' ? 'Terraform is provisioning your AWS infrastructure…' : 'Provisioning queued — waiting to start…'}
          </p>
        </div>
      )}
      {infra.status === 'ERROR' && (
        <div className="rounded-xl border border-destructive/20 bg-destructive/5 px-4 py-3 flex items-center justify-between">
          <p className="text-xs text-destructive">Provisioning failed. Click <span className="font-medium">Reprovision</span> to retry.</p>
        </div>
      )}
      {infra.status === 'DESTROYING' && (
        <div className="rounded-xl border border-warning/20 bg-warning/5 px-4 py-3 flex items-center gap-3">
          <div className="w-1.5 h-1.5 rounded-full bg-warning animate-pulse shrink-0" />
          <p className="text-xs text-warning">Terraform is destroying your AWS infrastructure…</p>
        </div>
      )}

      <div className="grid grid-cols-3 gap-3">
        {[
          { icon: Server, label: 'Provider', value: infra.cloud_provider, mono: false, tint: 'text-brand' },
          { icon: Cpu, label: 'Max CPU', value: `${infra.max_cpu} vCPU`, mono: true, tint: 'text-azure' },
          { icon: HardDrive, label: 'Max Memory', value: `${infra.max_memory} GB`, mono: true, tint: 'text-success' },
        ].map(({ icon: Icon, label, value, mono, tint }) => (
          <div key={label} className="rounded-xl panel p-4">
            <Icon className={`w-4 h-4 ${tint} mb-3`} />
            <p className="eyebrow">{label}</p>
            <p className={`mt-1 text-sm font-semibold text-foreground ${mono ? 'font-mono' : ''}`}>{value}</p>
          </div>
        ))}
      </div>

      {infra.environment?.alb_dns && (
        <div className="rounded-xl panel-inset px-4 py-3 flex items-center justify-between gap-3">
          <span className="eyebrow">Load Balancer</span>
          {infra.is_mock ? (
            <span className="text-xs text-muted-foreground/70 flex items-center gap-2 font-mono cursor-not-allowed select-none">
              {infra.environment.alb_dns} <span className="text-[10px] uppercase tracking-widest text-muted-foreground/60">dead link</span>
            </span>
          ) : (
            <a href={`http://${infra.environment.alb_dns}`} target="_blank" rel="noopener noreferrer"
              className="text-xs text-azure hover:text-azure/80 flex items-center gap-1.5 font-mono transition-colors">
              {infra.environment.alb_dns} <ExternalLink className="w-3 h-3" />
            </a>
          )}
        </div>
      )}

      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-display font-semibold text-foreground">
            Applications <span className="text-muted-foreground/60 font-normal font-mono">({apps.length})</span>
          </h2>
          {infra.status === 'ACTIVE' && canDeploy && (
            <Button size="sm" onClick={() => router.push(`/dashboard/applications/new?infra=${id}`)} className="gap-1.5">
              <Plus className="w-3.5 h-3.5" /> Deploy
            </Button>
          )}
        </div>
        {apps.length === 0 ? (
          <div className="rounded-xl border border-dashed border-hairline-strong p-10 text-center">
            <p className="text-xs text-muted-foreground mb-3">No applications deployed yet</p>
            {infra.status === 'ACTIVE' && canDeploy && (
              <Button size="sm" onClick={() => router.push(`/dashboard/applications/new?infra=${id}`)} className="gap-1.5">
                <Plus className="w-3.5 h-3.5" /> Deploy First Application
              </Button>
            )}
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {apps.map((app) => {
              const ast = statusOf(app.status);
              return (
                <div key={app.id}
                  className="group rounded-xl panel p-4 cursor-pointer transition-colors hover:border-brand/30 hover:bg-surface-2"
                  onClick={() => router.push(`/dashboard/applications/${app.id}`)}>
                  <div className="flex items-center justify-between mb-3">
                    <span className="text-sm font-medium text-foreground truncate">{app.name}</span>
                    <div className="flex items-center gap-1.5 shrink-0 ml-2">
                      <span className={`w-1.5 h-1.5 rounded-full ${ast.dot}`} />
                      <span className={`font-mono text-[10px] uppercase tracking-[0.12em] ${ast.label}`}>{app.status}</span>
                    </div>
                  </div>
                  <div className="flex items-center gap-3 text-xs text-muted-foreground font-mono">
                    <span>{app.cpu} vCPU</span>
                    <span>{app.memory} GB</span>
                    <span>:{app.port}</span>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      <DatabasesSection infraId={id} environmentActive={infra.status === 'ACTIVE'} canManage={isOwner} />

      <Sheet open={settingsOpen} onOpenChange={setSettingsOpen}>
        <SheetContent className="w-[480px] min-w-[320px] max-w-[640px] overflow-y-auto resize-x">
          <SheetHeader className="mb-6">
            <SheetTitle className="text-base font-display font-semibold">Settings</SheetTitle>
          </SheetHeader>

          <div className="space-y-6">
            <div className="space-y-2">
              <p className="eyebrow px-1">Details</p>
              <div className="rounded-xl panel-inset divide-y divide-hairline">
                {[
                  { label: 'ID', value: infra.id, mono: true },
                  { label: 'Provider', value: infra.cloud_provider },
                  { label: 'Region', value: infra.metadata?.aws_region ?? '—', mono: true },
                  { label: 'Status', value: infra.status },
                  { label: 'Max CPU', value: `${infra.max_cpu} vCPU` },
                  { label: 'Max Memory', value: `${infra.max_memory} GB` },
                  { label: 'Cloud Auth', value: infra.is_cloud_authenticated ? 'Authenticated' : 'Not authenticated' },
                  { label: 'Created', value: new Date(infra.created_at).toLocaleDateString() },
                ].map(({ label, value, mono }) => (
                  <div key={label} className="flex items-center justify-between px-4 py-2.5">
                    <span className="text-xs text-muted-foreground">{label}</span>
                    <span className={`text-xs text-foreground/80 ${mono ? 'font-mono' : ''} max-w-[200px] truncate`}>{value}</span>
                  </div>
                ))}
                <div className="flex items-center justify-between px-4 py-2.5">
                  <span className="text-xs text-muted-foreground">Name</span>
                  {isOwner && editingName ? (
                    <div className="flex items-center gap-2">
                      <Input value={infraName} onChange={(e) => setInfraName(e.target.value)}
                        onKeyDown={(e) => { if (e.key === 'Enter') handleRenameInfra(); if (e.key === 'Escape') setEditingName(false); }}
                        className="h-7 text-xs w-36 font-mono" autoFocus />
                      <button onClick={handleRenameInfra} disabled={savingName}
                        className="text-xs text-brand hover:text-brand/80 disabled:opacity-40 transition-colors">
                        {savingName ? '…' : 'Save'}
                      </button>
                    </div>
                  ) : (
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-foreground/80">{infra.name}</span>
                      {isOwner && (
                        <button onClick={() => { setInfraName(infra.name); setEditingName(true); }}
                          className="text-muted-foreground/50 hover:text-foreground transition-colors">
                          <Pencil className="w-3 h-3" />
                        </button>
                      )}
                    </div>
                  )}
                </div>
              </div>
            </div>

            {isOwner && infra.is_cloud_authenticated && (
              <div className="space-y-2">
                <p className="eyebrow px-1">AWS Permissions</p>
                <div className="rounded-xl panel-inset px-4 py-3 flex items-center justify-between gap-3">
                  <div>
                    <p className="text-xs font-medium text-foreground">Refresh IAM Policy</p>
                    <p className="text-[11px] text-muted-foreground mt-0.5">
                      Re-apply the latest deployment policy if actions start failing with AccessDenied.
                    </p>
                  </div>
                  <Button variant="outline" size="sm" onClick={() => setRefreshPolicyOpen(true)} className="gap-1.5 shrink-0">
                    <ShieldCheck className="w-3.5 h-3.5" /> Refresh
                  </Button>
                </div>
              </div>
            )}

            <div className="space-y-2">
              <p className="eyebrow px-1">Users ({infra.invited_users?.length ?? 0})</p>
              {!infra.invited_users || infra.invited_users.length === 0 ? (
                <p className="text-xs text-muted-foreground px-1">No invited users yet.</p>
              ) : (
                <div className="space-y-1.5">
                  {infra.invited_users.map((u) => (
                    <div key={u.id} className="rounded-xl panel-inset px-4 py-3 flex items-center justify-between">
                      <div className="flex items-center gap-3">
                        <div className="w-7 h-7 rounded-full bg-surface-3 border border-hairline flex items-center justify-center">
                          <User className="w-3 h-3 text-muted-foreground" />
                        </div>
                        <div>
                          <p className="text-xs font-medium text-foreground">{u.user_name}</p>
                          <p className="text-[11px] text-muted-foreground">{u.email}</p>
                        </div>
                      </div>
                      <div className="flex items-center gap-3">
                        <span className={`text-[10px] font-mono uppercase tracking-[0.12em] ${ROLE_COLORS[u.role] ?? 'text-muted-foreground'}`}>{u.role}</span>
                        {isOwner && (
                          <button
                            onClick={() => handleRemoveUser(u)}
                            disabled={removingUserId === u.id}
                            className="text-muted-foreground/50 hover:text-destructive transition-colors disabled:opacity-40"
                          >
                            <Trash2 className="w-3.5 h-3.5" />
                          </button>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {isOwner && (
              <div className="space-y-2">
                <p className="eyebrow px-1 text-destructive/70">Danger Zone</p>
                <div className="rounded-xl border border-destructive/30 bg-destructive/5 px-4 py-3 flex items-center justify-between">
                  <div>
                    <p className="text-xs font-medium text-foreground">Delete Infrastructure</p>
                    <p className="text-[11px] text-muted-foreground mt-0.5">Tears down all AWS resources. Cannot be undone.</p>
                  </div>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setDeleteOpen(true)}
                    className="border-destructive/30 text-destructive hover:bg-destructive/10 hover:text-destructive gap-1.5 shrink-0 ml-4"
                  >
                    <Trash2 className="w-3.5 h-3.5" /> Delete
                  </Button>
                </div>
              </div>
            )}
          </div>
        </SheetContent>
      </Sheet>

      <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle className="text-base font-display font-semibold">Delete Infrastructure</DialogTitle>
          </DialogHeader>
          <p className="text-xs text-muted-foreground">
            Delete <span className="text-foreground font-mono">{infra.name}</span>? This will trigger Terraform destroy and remove all AWS resources it provisioned in your account. This cannot be undone.
          </p>
          <div className="flex gap-2 justify-end mt-2">
            <Button variant="outline" onClick={() => setDeleteOpen(false)}>
              Cancel
            </Button>
            <Button variant="destructive" onClick={handleDeleteInfra} disabled={deleting}>
              {deleting ? 'Deleting…' : 'Delete'}
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      <Dialog open={inviteOpen} onOpenChange={(o) => { if (!o) closeInviteDialog(); else setInviteOpen(true); }}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle className="text-base font-display font-semibold">{inviteUrl ? 'Invitation Ready' : 'Invite User'}</DialogTitle>
          </DialogHeader>
          {inviteUrl ? (
            <div className="space-y-4">
              <p className="text-xs text-muted-foreground">
                Share this link with <span className="text-foreground font-mono">{inviteForm.email}</span>.
              </p>
              <div className="rounded-xl panel-inset p-3 flex items-center gap-3">
                <p className="flex-1 text-xs font-mono text-muted-foreground break-all">{inviteUrl}</p>
                <button onClick={copyUrl} className="shrink-0 text-muted-foreground hover:text-foreground transition-colors">
                  {copied ? <Check className="w-4 h-4 text-success" /> : <Copy className="w-4 h-4" />}
                </button>
              </div>
              <Button onClick={closeInviteDialog} variant="outline" className="w-full">Done</Button>
            </div>
          ) : (
            <form onSubmit={handleInvite} className="space-y-3">
              {[
                { label: 'Email', type: 'email', placeholder: 'user@example.com', key: 'email' },
                { label: 'Username', type: 'text', placeholder: 'johndoe', key: 'user_name' },
                { label: 'Temporary Password', type: 'password', placeholder: 'Min 6 characters', key: 'password' },
              ].map(({ label, type, placeholder, key }) => (
                <div key={key} className="space-y-1.5">
                  <Label className="eyebrow">{label}</Label>
                  <Input type={type} placeholder={placeholder}
                    value={inviteForm[key as keyof typeof inviteForm]}
                    onChange={(e) => setInviteForm({ ...inviteForm, [key]: e.target.value })}
                    required minLength={key === 'password' ? 6 : undefined}
                    className="h-9 text-sm" />
                </div>
              ))}
              <div className="space-y-1.5">
                <Label className="eyebrow">Role</Label>
                <Select value={inviteForm.role} onValueChange={(v) => setInviteForm({ ...inviteForm, role: v as 'admin' | 'user' | 'guest' })}>
                  <SelectTrigger className="h-9 text-sm w-full"><SelectValue /></SelectTrigger>
                  <SelectContent className="w-[320px]">
                    <SelectItem value="admin" className="text-sm py-2.5">Admin — can deploy apps</SelectItem>
                    <SelectItem value="user" className="text-sm py-2.5">User — view only</SelectItem>
                    <SelectItem value="guest" className="text-sm py-2.5">Guest — limited access</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <Button type="submit" disabled={inviting} className="w-full mt-1">
                {inviting ? 'Inviting…' : 'Send Invite'}
              </Button>
            </form>
          )}
        </DialogContent>
      </Dialog>

      <PolicyRefreshDialog open={refreshPolicyOpen} onOpenChange={setRefreshPolicyOpen} infraId={id} />
    </motion.div>
  );
}
