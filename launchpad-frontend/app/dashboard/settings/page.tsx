'use client';

import { useEffect, useMemo, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Github, Users, CreditCard, User as UserIcon, Server, Rocket, Trash2 } from 'lucide-react';
import Image from 'next/image';
import { useAuthStore } from '@/lib/store/auth';
import { infrastructureApi } from '@/lib/api/infrastructures';
import { applicationApi } from '@/lib/api/applications';
import { authApi } from '@/lib/api/auth';
import { Infrastructure } from '@/types/infrastructure';
import { InvitedUser } from '@/types/auth';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { toast } from 'sonner';

const ROLE_STYLES: Record<string, string> = {
  super_admin: 'border-brand/30 bg-brand-soft text-brand',
  admin: 'border-azure/30 bg-azure/10 text-azure',
  user: 'border-success/30 bg-success/10 text-success',
  guest: 'border-hairline bg-surface-1 text-muted-foreground',
};
const RANK: Record<string, number> = { super_admin: 3, admin: 2, user: 1, guest: 0 };
const roleLabel = (r: string) => r.replace('_', ' ');

const fade = { initial: { opacity: 0, y: 8 }, animate: { opacity: 1, y: 0 }, exit: { opacity: 0, y: -8 }, transition: { duration: 0.2 } };

type TabKey = 'profile' | 'organization' | 'billing';

interface Member {
  id: string;
  email: string;
  user_name: string;
  role: string;
  infras: string[];
  infraIds: string[];
  pending: boolean;
}

export default function SettingsPage() {
  const user = useAuthStore((s) => s.user);
  const [infras, setInfras] = useState<Infrastructure[]>([]);
  const [invited, setInvited] = useState<InvitedUser[]>([]);
  const [appCount, setAppCount] = useState<number | null>(null);
  const [tab, setTab] = useState<TabKey>('profile');
  const [removeTarget, setRemoveTarget] = useState<Member | null>(null);
  const [removing, setRemoving] = useState(false);
  const canSeeOrg = user?.role === 'super_admin' || user?.role === 'admin';
  const myRank = RANK[user?.role ?? 'guest'] ?? 0;

  useEffect(() => {
    infrastructureApi.list()
      .then(async (list) => {
        setInfras(list);
        const appLists = await Promise.all(list.map((i) => applicationApi.list(i.id).catch(() => [])));
        setAppCount(appLists.reduce((n, a) => n + a.length, 0));
      })
      .catch(() => setAppCount(0));
    if (canSeeOrg) {
      authApi.listInvitedUsers().then(setInvited).catch(() => toast.error('Failed to load members', { id: 'settings-members' }));
    }
  }, [canSeeOrg]);

  const members = useMemo(() => {
    const infraName = new Map(infras.map((i) => [i.id, i.name]));
    return invited
      .filter((u) => u.id !== user?.id)
      .map<Member>((u) => ({
        id: u.id,
        email: u.email,
        user_name: u.user_name,
        role: u.role,
        pending: u.is_authenticated === false,
        infraIds: u.infra_id ?? [],
        infras: (u.infra_id ?? []).map((id) => infraName.get(id) ?? id.slice(0, 8)),
      }));
  }, [invited, infras, user?.id]);

  const pendingCount = members.filter((m) => m.pending).length;

  const doRemove = async () => {
    if (!removeTarget) return;
    setRemoving(true);
    try {
      const owned = new Set(infras.map((i) => i.id));
      const scopedInfraIds = removeTarget.infraIds.filter((id) => owned.has(id));
      if (scopedInfraIds.length === 0) {
        toast.error('You do not manage any organization this member belongs to');
        return;
      }
      await authApi.removeMemberFromOrg(removeTarget.id, scopedInfraIds);
      const results = await Promise.allSettled(
        scopedInfraIds.map((id) => infrastructureApi.removeUser(id, removeTarget.id)),
      );
      const failed = results.filter((r) => r.status === 'rejected').length;
      if (failed > 0) {
        toast.error(`Removed from ${scopedInfraIds.length - failed} of ${scopedInfraIds.length} organizations — retry to clear the rest`);
      } else {
        toast.success(`${removeTarget.user_name} removed from the organization`);
      }
      setInvited(await authApi.listInvitedUsers());
      setRemoveTarget(null);
    } catch (e: unknown) {
      const err = e as { response?: { data?: { message?: string; error?: string } } };
      toast.error(err.response?.data?.message || err.response?.data?.error || 'Failed to remove member');
    } finally {
      setRemoving(false);
    }
  };

  if (!user) return null;

  const tabs: { key: TabKey; label: string; icon: typeof UserIcon; show: boolean }[] = [
    { key: 'profile', label: 'Profile', icon: UserIcon, show: true },
    { key: 'organization', label: 'Organization', icon: Users, show: canSeeOrg },
    { key: 'billing', label: 'Billing', icon: CreditCard, show: true },
  ];

  return (
    <div className="space-y-8">
      <div>
        <span className="eyebrow">Console / Settings</span>
        <h1 className="mt-2 text-2xl font-display font-semibold text-foreground tracking-tight">Settings</h1>
        <p className="text-sm text-muted-foreground mt-1.5">Your profile, organization, and account.</p>
      </div>

      <div className="flex flex-col md:flex-row gap-8">
        <nav className="md:w-52 shrink-0 flex md:flex-col gap-1">
          {tabs.filter((t) => t.show).map(({ key, label, icon: Icon }) => (
            <button
              key={key}
              onClick={() => setTab(key)}
              className={`flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm text-left transition-colors ${
                tab === key ? 'bg-surface-2 text-foreground border border-hairline' : 'text-muted-foreground hover:text-foreground hover:bg-surface-1 border border-transparent'
              }`}
            >
              <Icon className={`w-4 h-4 ${tab === key ? 'text-brand' : ''}`} />
              {label}
              {key === 'organization' && pendingCount > 0 && (
                <span className="ml-auto font-mono text-[10px] px-1.5 py-0.5 rounded border border-warning/30 bg-warning/10 text-warning">{pendingCount}</span>
              )}
            </button>
          ))}
        </nav>

        <div className="flex-1 min-w-0 max-w-2xl">
          <AnimatePresence mode="wait">
            <motion.div key={tab} {...fade}>
              {tab === 'profile' && (
                <section className="space-y-3">
                  <p className="eyebrow">Profile</p>
                  <div className="rounded-2xl panel p-5">
                    <div className="flex items-center gap-4">
                      <span className="w-14 h-14 rounded-full overflow-hidden ring-1 ring-hairline-strong shrink-0 flex items-center justify-center bg-surface-2">
                        {user.profile_url
                          ? <Image src={user.profile_url} alt={user.user_name} width={56} height={56} className="w-14 h-14 object-cover" />
                          : <UserIcon className="w-6 h-6 text-muted-foreground" />}
                      </span>
                      <div className="min-w-0">
                        <p className="text-base font-medium text-foreground truncate">{user.user_name}</p>
                        <p className="text-sm text-muted-foreground truncate">{user.email}</p>
                      </div>
                      <span className={`ml-auto shrink-0 font-mono text-[10px] uppercase tracking-[0.12em] px-2 py-0.5 rounded-md border ${ROLE_STYLES[user.role] ?? ROLE_STYLES.guest}`}>
                        {roleLabel(user.role)}
                      </span>
                    </div>

                    <div className="mt-5 grid grid-cols-3 gap-3">
                      <Stat icon={Server} tint="text-brand" label="Infrastructures" value={infras.length} />
                      <Stat icon={Rocket} tint="text-azure" label="Applications" value={appCount} />
                      {canSeeOrg && <Stat icon={Users} tint="text-success" label="Members" value={members.length + 1} />}
                    </div>

                    <div className="mt-4 rounded-xl panel-inset divide-y divide-hairline">
                      {user.metadata?.github?.username && (
                        <Row label="GitHub">
                          <span className="flex items-center gap-1.5 text-foreground/80"><Github className="w-3.5 h-3.5" />{user.metadata.github.username}</span>
                        </Row>
                      )}
                      <Row label="Member since">
                        <span className="text-foreground/80">{new Date(user.created_at).toLocaleDateString(undefined, { year: 'numeric', month: 'long', day: 'numeric' })}</span>
                      </Row>
                    </div>
                  </div>
                </section>
              )}

              {tab === 'organization' && canSeeOrg && (
                <section className="space-y-3">
                  <div className="flex items-center justify-between">
                    <p className="eyebrow">Organization</p>
                    <span className="font-mono text-[10px] text-muted-foreground/60">
                      {members.length + 1} {members.length === 0 ? 'member' : 'members'}{pendingCount > 0 ? ` · ${pendingCount} pending` : ''}
                    </span>
                  </div>
                  <div className="rounded-2xl panel divide-y divide-hairline">
                    <MemberRow name={user.user_name} email={user.email} role={user.role} profileUrl={user.profile_url} tag="You · Owner" />
                    {members.map((m) => (
                      <MemberRow
                        key={m.id}
                        name={m.user_name}
                        email={m.email}
                        role={m.role}
                        tag={m.infras.join(', ')}
                        pending={m.pending}
                        onRemove={myRank > (RANK[m.role] ?? 0) ? () => setRemoveTarget(m) : undefined}
                      />
                    ))}
                    {members.length === 0 && (
                      <div className="px-5 py-6 text-center">
                        <p className="text-xs text-muted-foreground">No one else has been invited yet. Invite teammates from an infrastructure&apos;s page.</p>
                      </div>
                    )}
                  </div>
                  <p className="text-[11px] text-muted-foreground/70 px-1">
                    <span className="inline-block w-1.5 h-1.5 rounded-full bg-warning align-middle mr-1.5" />
                    Pending means the invite hasn&apos;t been accepted (email not verified) yet.
                  </p>
                </section>
              )}

              {tab === 'billing' && (
                <section className="space-y-3">
                  <p className="eyebrow">Billing</p>
                  <div className="rounded-2xl panel-inset px-5 py-12 text-center">
                    <div className="w-11 h-11 rounded-xl bg-surface-2 border border-hairline flex items-center justify-center mx-auto mb-4">
                      <CreditCard className="w-5 h-5 text-muted-foreground" />
                    </div>
                    <p className="text-sm text-foreground">No invoices yet</p>
                    <p className="text-xs text-muted-foreground mt-1 max-w-sm mx-auto">Plan, payment history, and invoices will appear here once billing is enabled for your account.</p>
                  </div>
                </section>
              )}
            </motion.div>
          </AnimatePresence>
        </div>
      </div>

      <Dialog open={!!removeTarget} onOpenChange={(o) => { if (!o) setRemoveTarget(null); }}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle className="text-base font-display font-semibold">Remove member</DialogTitle>
          </DialogHeader>
          <p className="text-xs text-muted-foreground">
            Remove <span className="text-foreground font-medium">{removeTarget?.user_name}</span> from the organization? They lose access to your infrastructures. Their account is deleted only if this was their last organization.
          </p>
          <div className="flex gap-2 justify-end mt-2">
            <Button variant="outline" onClick={() => setRemoveTarget(null)}>Cancel</Button>
            <Button variant="destructive" onClick={doRemove} disabled={removing} className="gap-1.5">
              <Trash2 className="w-3.5 h-3.5" />{removing ? 'Removing…' : 'Remove'}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function Stat({ icon: Icon, label, value, tint }: { icon: typeof UserIcon; label: string; value: number | null; tint: string }) {
  return (
    <div className="rounded-xl panel-inset p-4">
      <Icon className={`w-4 h-4 ${tint} mb-3`} />
      <p className="eyebrow">{label}</p>
      <p className="mt-1 text-lg font-display font-semibold text-foreground">{value ?? '—'}</p>
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4 px-4 py-2.5">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className="text-xs">{children}</span>
    </div>
  );
}

function MemberRow({ name, email, role, profileUrl, tag, pending, onRemove }: {
  name: string; email: string; role: string; profileUrl?: string; tag?: string; pending?: boolean; onRemove?: () => void;
}) {
  return (
    <div className="flex items-center gap-3 px-5 py-3.5">
      <span className="w-9 h-9 rounded-full overflow-hidden ring-1 ring-hairline shrink-0 flex items-center justify-center bg-surface-2">
        {profileUrl
          ? <Image src={profileUrl} alt={name} width={36} height={36} className="w-9 h-9 object-cover" />
          : <Users className="w-4 h-4 text-muted-foreground" />}
      </span>
      <div className="min-w-0">
        <p className="text-sm font-medium text-foreground truncate">{name}</p>
        <p className="text-xs text-muted-foreground truncate">{email}</p>
      </div>
      <div className="ml-auto flex items-center gap-3 shrink-0">
        {tag && <span className="hidden sm:inline font-mono text-[10px] text-muted-foreground/60 truncate max-w-[140px]">{tag}</span>}
        <span className={`flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-[0.1em] ${pending ? 'text-warning' : 'text-success'}`}>
          <span className={`w-1.5 h-1.5 rounded-full ${pending ? 'bg-warning' : 'bg-success'}`} />
          {pending ? 'Pending' : 'Active'}
        </span>
        <span className={`font-mono text-[10px] uppercase tracking-[0.12em] px-2 py-0.5 rounded-md border ${ROLE_STYLES[role] ?? ROLE_STYLES.guest}`}>
          {roleLabel(role)}
        </span>
        {onRemove && (
          <button onClick={onRemove} title="Remove from organization"
            className="text-muted-foreground/50 hover:text-destructive transition-colors">
            <Trash2 className="w-3.5 h-3.5" />
          </button>
        )}
      </div>
    </div>
  );
}
