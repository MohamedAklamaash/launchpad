'use client';

import { useEffect, useState } from 'react';
import { useRouter, useParams } from 'next/navigation';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
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
import { ArrowLeft, Plus, Server, Cpu, HardDrive, ExternalLink, UserPlus, Copy, Check, Settings, Trash2, User, Pencil } from 'lucide-react';
import { Infrastructure, InvitedUserSummary } from '@/types/infrastructure';
import { ApplicationSummary } from '@/types/application';
import { infrastructureApi } from '@/lib/api/infrastructures';
import { applicationApi } from '@/lib/api/applications';
import { authApi } from '@/lib/api/auth';
import { useAuthStore } from '@/lib/store/auth';
import { toast } from 'sonner';

const ROLE_COLORS: Record<string, string> = {
  super_admin: 'text-purple-400',
  admin: 'text-blue-400',
  user: 'text-green-400',
  guest: 'text-[#666]',
};

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

  // Invite dialog
  const [inviteOpen, setInviteOpen] = useState(false);
  const [inviteForm, setInviteForm] = useState({ email: '', password: '', user_name: '', role: 'user' as 'admin' | 'user' | 'guest' });
  const [inviting, setInviting] = useState(false);
  const [inviteUrl, setInviteUrl] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const isSuperAdmin = user?.role === 'super_admin';
  const canDeploy = isSuperAdmin || user?.role === 'admin';
  const isOwner = isSuperAdmin && infra?.user_id === user?.id;

  useEffect(() => {
    loadData();
    const interval = setInterval(() => {
      if (infra?.status === 'PENDING' || infra?.status === 'PROVISIONING') loadData();
    }, 5000);
    return () => clearInterval(interval);
  }, [id, infra?.status]);

  const loadData = async () => {
    try {
      const [infraData, appsData] = await Promise.all([
        infrastructureApi.get(id),
        applicationApi.list(id),
      ]);
      setInfra(infraData);
      setApps(appsData);
    } catch (error: any) {
      toast.error(error.response?.data?.error || 'Failed to load data');
    } finally {
      setLoading(false);
    }
  };

  const handleRenameInfra = async () => {
    if (!infraName.trim() || infraName === infra?.name) { setEditingName(false); return; }
    setSavingName(true);
    try {
      await infrastructureApi.updateConfig(id, { name: infraName.trim() });
      toast.success('Infrastructure renamed');
      await loadData();
      setEditingName(false);
    } catch (err: any) {
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
    } catch (err: any) {
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
    } catch (err: any) {
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

  const getStatusDot = (status: string) => {
    switch (status) {
      case 'ACTIVE': return 'bg-emerald-500';
      case 'PROVISIONING': return 'bg-blue-500 animate-pulse';
      case 'PENDING': return 'bg-amber-500 animate-pulse';
      case 'ERROR': return 'bg-red-500';
      case 'BUILDING': return 'bg-blue-500 animate-pulse';
      case 'DEPLOYING': return 'bg-blue-500 animate-pulse';
      case 'FAILED': return 'bg-red-500';
      default: return 'bg-[#444]';
    }
  };

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'ACTIVE': return 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20';
      case 'PROVISIONING': return 'bg-blue-500/10 text-blue-400 border-blue-500/20';
      case 'PENDING': return 'bg-amber-500/10 text-amber-400 border-amber-500/20';
      case 'ERROR': return 'bg-red-500/10 text-red-400 border-red-500/20';
      case 'BUILDING': return 'bg-blue-500/10 text-blue-400 border-blue-500/20';
      case 'DEPLOYING': return 'bg-blue-500/10 text-blue-400 border-blue-500/20';
      case 'FAILED': return 'bg-red-500/10 text-red-400 border-red-500/20';
      default: return 'bg-[#1a1a1a] text-[#666] border-[#222]';
    }
  };

  if (loading) return (
    <div className="flex items-center justify-center h-64">
      <div className="w-5 h-5 border-2 border-[#333] border-t-violet-500 rounded-full animate-spin" />
    </div>
  );
  if (!infra) return <div className="text-[#555] text-sm">Infrastructure not found</div>;

  return (
    <div className="space-y-6">
      <button onClick={() => router.back()} className="flex items-center gap-1.5 text-xs text-[#666] hover:text-white transition-colors cursor-pointer">
        <ArrowLeft className="w-3.5 h-3.5" /> Back
      </button>

      {/* Header */}
      <div className="flex items-start justify-between">
        <div className="space-y-1.5">
          <div className="flex items-center gap-2.5">
            <h1 className="text-xl font-semibold text-white tracking-tight">{infra.name}</h1>
            <span className={`text-xs font-mono px-2 py-0.5 rounded-full border ${getStatusColor(infra.status)}`}>
              {infra.status}
            </span>
          </div>
          <p className="text-xs text-[#444] font-mono">{infra.cloud_provider} · {infra.id.slice(0, 8)}…</p>
        </div>
        <div className="flex items-center gap-2">
          {isSuperAdmin && (
            <Button variant="outline" onClick={() => setInviteOpen(true)}
              className="border-[#1e1e1e] bg-transparent hover:bg-[#111] text-[#888] hover:text-white h-8 text-xs gap-1.5">
              <UserPlus className="w-3.5 h-3.5" /> Invite
            </Button>
          )}
          <Button variant="outline" onClick={() => setSettingsOpen(true)}
            className="border-[#1e1e1e] bg-transparent hover:bg-[#111] text-[#888] hover:text-white h-8 text-xs gap-1.5">
            <Settings className="w-3.5 h-3.5" /> Settings
          </Button>
        </div>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-3 gap-3">
        {[
          { icon: Server, label: 'Provider', value: infra.cloud_provider, color: 'text-violet-400' },
          { icon: Cpu, label: 'Max CPU', value: `${infra.max_cpu} vCPU`, color: 'text-blue-400' },
          { icon: HardDrive, label: 'Max Memory', value: `${infra.max_memory} GB`, color: 'text-emerald-400' },
        ].map(({ icon: Icon, label, value, color }) => (
          <div key={label} className="bg-[#0d0d0d] border border-[#1a1a1a] rounded-xl p-4">
            <Icon className={`w-4 h-4 ${color} mb-3`} />
            <p className="text-xs text-[#666] mb-0.5">{label}</p>
            <p className="text-sm font-semibold text-white">{value}</p>
          </div>
        ))}
      </div>

      {infra.environment?.alb_dns && (
        <div className="bg-[#0d0d0d] border border-[#1a1a1a] rounded-xl px-4 py-3 flex items-center justify-between">
          <span className="text-xs text-[#666]">Load Balancer</span>
          <a href={`http://${infra.environment.alb_dns}`} target="_blank" rel="noopener noreferrer"
            className="text-xs text-violet-400 hover:text-violet-300 flex items-center gap-1.5 font-mono transition-colors">
            {infra.environment.alb_dns} <ExternalLink className="w-3 h-3" />
          </a>
        </div>
      )}

      {/* Applications */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-white">Applications <span className="text-[#444] font-normal">({apps.length})</span></h2>
          {infra.status === 'ACTIVE' && canDeploy && (
            <Button onClick={() => router.push(`/dashboard/applications/new?infra=${id}`)}
              className="bg-violet-600 hover:bg-violet-700 text-white h-8 text-xs gap-1.5">
              <Plus className="w-3.5 h-3.5" /> Deploy
            </Button>
          )}
        </div>
        {apps.length === 0 ? (
          <div className="border border-dashed border-[#1a1a1a] rounded-xl p-10 text-center">
            <p className="text-xs text-[#444] mb-3">No applications deployed yet</p>
            {infra.status === 'ACTIVE' && canDeploy && (
              <Button onClick={() => router.push(`/dashboard/applications/new?infra=${id}`)}
                className="bg-violet-600 hover:bg-violet-700 text-white h-8 text-xs gap-1.5">
                <Plus className="w-3.5 h-3.5" /> Deploy First Application
              </Button>
            )}
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {apps.map((app) => (
              <div key={app.id}
                className="bg-[#0d0d0d] border border-[#1a1a1a] rounded-xl p-4 cursor-pointer hover:border-[#2a2a2a] hover:bg-[#111] transition-all"
                onClick={() => router.push(`/dashboard/applications/${app.id}`)}>
                <div className="flex items-center justify-between mb-3">
                  <span className="text-sm font-medium text-white truncate">{app.name}</span>
                  <div className="flex items-center gap-1.5 shrink-0 ml-2">
                    <span className={`w-1.5 h-1.5 rounded-full ${getStatusDot(app.status)}`} />
                    <span className="text-xs text-[#444] font-mono">{app.status}</span>
                  </div>
                </div>
                <div className="flex items-center gap-3 text-xs text-[#444]">
                  <span>{app.cpu} vCPU</span>
                  <span>{app.memory} GB</span>
                  <span>:{app.port}</span>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Settings Sheet */}
      <Sheet open={settingsOpen} onOpenChange={setSettingsOpen}>
        <SheetContent className="bg-[#090909] border-[#1a1a1a] w-[480px] min-w-[320px] max-w-[640px] overflow-y-auto resize-x">
          <SheetHeader className="mb-6">
            <SheetTitle className="text-white text-base font-semibold">Settings</SheetTitle>
          </SheetHeader>

          <div className="space-y-6">
            {/* Details */}
            <div className="space-y-2">
              <p className="text-[10px] uppercase tracking-widest text-[#555] font-mono px-1">Details</p>
              <div className="bg-[#0d0d0d] border border-[#1a1a1a] rounded-xl divide-y divide-[#1a1a1a]">
                {[
                  { label: 'ID', value: infra.id, mono: true },
                  { label: 'Provider', value: infra.cloud_provider },
                  { label: 'Status', value: infra.status },
                  { label: 'Max CPU', value: `${infra.max_cpu} vCPU` },
                  { label: 'Max Memory', value: `${infra.max_memory} GB` },
                  { label: 'Cloud Auth', value: infra.is_cloud_authenticated ? 'Authenticated' : 'Not authenticated' },
                  { label: 'Created', value: new Date(infra.created_at).toLocaleDateString() },
                ].map(({ label, value, mono }) => (
                  <div key={label} className="flex items-center justify-between px-4 py-2.5">
                    <span className="text-xs text-[#444]">{label}</span>
                    <span className={`text-xs text-[#aaa] ${mono ? 'font-mono' : ''} max-w-[200px] truncate`}>{value}</span>
                  </div>
                ))}
                {/* Editable name */}
                <div className="flex items-center justify-between px-4 py-2.5">
                  <span className="text-xs text-[#444]">Name</span>
                  {isOwner && editingName ? (
                    <div className="flex items-center gap-2">
                      <Input value={infraName} onChange={(e) => setInfraName(e.target.value)}
                        onKeyDown={(e) => { if (e.key === 'Enter') handleRenameInfra(); if (e.key === 'Escape') setEditingName(false); }}
                        className="bg-[#050505] border-[#222] h-7 text-xs w-36 font-mono focus:border-violet-500" autoFocus />
                      <button onClick={handleRenameInfra} disabled={savingName}
                        className="text-xs text-violet-400 hover:text-violet-300 disabled:opacity-40 transition-colors">
                        {savingName ? '…' : 'Save'}
                      </button>
                    </div>
                  ) : (
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-[#aaa]">{infra.name}</span>
                      {isOwner && (
                        <button onClick={() => { setInfraName(infra.name); setEditingName(true); }}
                          className="text-[#333] hover:text-[#888] transition-colors">
                          <Pencil className="w-3 h-3" />
                        </button>
                      )}
                    </div>
                  )}
                </div>
              </div>
            </div>

            {/* Invited Users */}
            <div className="space-y-2">
              <p className="text-[10px] uppercase tracking-widest text-[#555] font-mono px-1">
                Users ({infra.invited_users?.length ?? 0})
              </p>
              {!infra.invited_users || infra.invited_users.length === 0 ? (
                <p className="text-xs text-[#555] px-1">No invited users yet.</p>
              ) : (
                <div className="space-y-1.5">
                  {infra.invited_users.map((u) => (
                    <div key={u.id} className="bg-[#0d0d0d] border border-[#1a1a1a] rounded-xl px-4 py-3 flex items-center justify-between">
                      <div className="flex items-center gap-3">
                        <div className="w-7 h-7 rounded-full bg-[#141414] border border-[#1e1e1e] flex items-center justify-center">
                          <User className="w-3 h-3 text-[#444]" />
                        </div>
                        <div>
                          <p className="text-xs font-medium text-white">{u.user_name}</p>
                          <p className="text-[11px] text-[#444]">{u.email}</p>
                        </div>
                      </div>
                      <div className="flex items-center gap-3">
                        <span className={`text-[10px] font-mono ${ROLE_COLORS[u.role] ?? 'text-[#444]'}`}>{u.role}</span>
                        {isOwner && (
                          <button
                            onClick={() => handleRemoveUser(u)}
                            disabled={removingUserId === u.id}
                            className="text-[#333] hover:text-red-400 transition-colors disabled:opacity-40"
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
          </div>
        </SheetContent>
      </Sheet>

      {/* Invite User Dialog */}
      <Dialog open={inviteOpen} onOpenChange={(o) => { if (!o) closeInviteDialog(); else setInviteOpen(true); }}>
        <DialogContent className="bg-[#090909] border-[#1a1a1a] max-w-md shadow-2xl shadow-black/60">
          <DialogHeader>
            <DialogTitle className="text-base font-semibold">{inviteUrl ? 'Invitation Ready' : 'Invite User'}</DialogTitle>
          </DialogHeader>
          {inviteUrl ? (
            <div className="space-y-4">
              <p className="text-xs text-[#555]">
                Share this link with <span className="text-[#aaa] font-mono">{inviteForm.email}</span>.
              </p>
              <div className="bg-[#050505] border border-[#1a1a1a] rounded-xl p-3 flex items-center gap-3">
                <p className="flex-1 text-xs font-mono text-[#777] break-all">{inviteUrl}</p>
                <button onClick={copyUrl} className="shrink-0 text-[#444] hover:text-white transition-colors">
                  {copied ? <Check className="w-4 h-4 text-emerald-500" /> : <Copy className="w-4 h-4" />}
                </button>
              </div>
              <Button onClick={closeInviteDialog} variant="outline" className="w-full border-[#1e1e1e] bg-transparent hover:bg-[#111] text-[#888] h-9 text-sm">Done</Button>
            </div>
          ) : (
            <form onSubmit={handleInvite} className="space-y-3">
              {[
                { label: 'Email', type: 'email', placeholder: 'user@example.com', key: 'email' },
                { label: 'Username', type: 'text', placeholder: 'johndoe', key: 'user_name' },
                { label: 'Temporary Password', type: 'password', placeholder: 'Min 6 characters', key: 'password' },
              ].map(({ label, type, placeholder, key }) => (
                <div key={key} className="space-y-1.5">
                  <Label className="text-[#444] text-[10px] uppercase tracking-widest font-mono">{label}</Label>
                  <Input type={type} placeholder={placeholder}
                    value={(inviteForm as any)[key]}
                    onChange={(e) => setInviteForm({ ...inviteForm, [key]: e.target.value })}
                    required minLength={key === 'password' ? 6 : undefined}
                    className="bg-[#050505] border-[#1a1a1a] focus:border-violet-500 h-9 text-sm" />
                </div>
              ))}
              <div className="space-y-1.5">
                <Label className="text-[#444] text-[10px] uppercase tracking-widest font-mono">Role</Label>
                <Select value={inviteForm.role} onValueChange={(v) => setInviteForm({ ...inviteForm, role: v as 'admin' | 'user' | 'guest' })}>
                  <SelectTrigger className="bg-[#050505] border-[#1a1a1a] h-9 text-sm w-full"><SelectValue /></SelectTrigger>
                  <SelectContent className="bg-[#0d0d0d] border-[#1a1a1a] w-[320px]">
                    <SelectItem value="admin" className="text-sm py-2.5">Admin — can deploy apps</SelectItem>
                    <SelectItem value="user" className="text-sm py-2.5">User — view only</SelectItem>
                    <SelectItem value="guest" className="text-sm py-2.5">Guest — limited access</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <Button type="submit" disabled={inviting} className="w-full bg-violet-600 hover:bg-violet-700 h-9 text-sm font-medium mt-1">
                {inviting ? 'Inviting…' : 'Send Invite'}
              </Button>
            </form>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
