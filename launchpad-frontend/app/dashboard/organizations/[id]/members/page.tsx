'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { notFound } from 'next/navigation';
import { format } from 'date-fns';
import { LogOut, Pencil, Trash2, UserPlus, X } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Skeleton } from '@/components/ui/skeleton';
import { ORGS_ENABLED } from '@/lib/orgs-enabled';
import { apiErrorMessage, organizationApi } from '@/lib/api/organizations';
import { useOrgStore } from '@/lib/store/org';
import { useAuthStore } from '@/lib/store/auth';
import { currentSessionClaims } from '@/lib/session-claims';
import { assignableRolesFor, canManageMember, ROLE_LABEL, roleCan } from '@/lib/org-matrix';
import { loginUrlWithNext } from '@/lib/post-login';
import type { AssignableRole, OrgInvitation, OrgMember } from '@/types/organization';

const ROLE_HINT: Record<AssignableRole, string> = {
  admin: 'Manage members and infrastructure',
  developer: 'Deploy, roll back, edit env vars, read logs',
  viewer: 'Read-only: status and deploy results',
};

export default function MembersPage() {
  if (!ORGS_ENABLED) notFound();
  return <Members />;
}

function Members() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const clearAuth = useAuthStore((s) => s.clearAuth);
  const orgs = useOrgStore((s) => s.orgs);
  const orgStatus = useOrgStore((s) => s.status);
  const loadOrgs = useOrgStore((s) => s.load);
  const org = useMemo(() => orgs.find((o) => o.id === id) ?? null, [orgs, id]);
  const myAccountId = useMemo(() => currentSessionClaims().accountId, []);

  const [members, setMembers] = useState<OrgMember[] | null>(null);
  const [membersError, setMembersError] = useState<string | null>(null);
  const [invitations, setInvitations] = useState<OrgInvitation[]>([]);

  const [renameOpen, setRenameOpen] = useState(false);
  const [removeTarget, setRemoveTarget] = useState<OrgMember | null>(null);
  const [leaveOpen, setLeaveOpen] = useState(false);
  const [transferOpen, setTransferOpen] = useState(false);

  const role = org?.role ?? null;
  const canManage = role ? roleCan(role, 'members_invite') : false;
  const canTransfer = role === 'owner' && org ? !org.is_personal : false;

  useEffect(() => {
    void loadOrgs();
  }, [loadOrgs]);

  const loadMembers = useCallback(async () => {
    try {
      setMembers(await organizationApi.members(id));
      setMembersError(null);
    } catch (err) {
      setMembers(null);
      setMembersError(apiErrorMessage(err, 'Failed to load members'));
    }
  }, [id]);

  const loadInvitations = useCallback(async () => {
    try {
      setInvitations(await organizationApi.invitations(id));
    } catch (err) {
      toast.error(apiErrorMessage(err, 'Failed to load invitations'), { id: 'org-invitations-load' });
    }
  }, [id]);

  useEffect(() => {
    void loadMembers();
  }, [loadMembers]);

  useEffect(() => {
    if (canManage) void loadInvitations();
  }, [canManage, loadInvitations]);

  if (orgStatus === 'ready' && !org) {
    return (
      <div className="rounded-2xl panel-inset px-8 py-16 text-center">
        <p className="text-base font-display font-medium text-foreground">Organization not found</p>
        <p className="text-sm text-muted-foreground mt-1.5">You are not a member of this organization.</p>
      </div>
    );
  }

  const isSelf = (m: OrgMember) => !!myAccountId && m.account_id === myAccountId;

  return (
    <div className="space-y-10">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="min-w-0">
          <span className="eyebrow">Console / Members</span>
          <h1 className="mt-2 text-2xl font-display font-semibold text-foreground tracking-tight truncate">
            {org?.name ?? <Skeleton className="h-8 w-48" />}
          </h1>
          <p className="text-sm text-muted-foreground mt-1.5">
            {org ? `${org.is_personal ? 'Personal' : 'Team'} organization · you are ${ROLE_LABEL[org.role].toLowerCase()}` : ' '}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {canManage && (
            <Button variant="outline" size="sm" onClick={() => setRenameOpen(true)} className="gap-1.5">
              <Pencil className="w-3.5 h-3.5" /> Rename
            </Button>
          )}
          {role && role !== 'owner' && (
            <Button variant="outline" size="sm" onClick={() => setLeaveOpen(true)} className="gap-1.5 text-destructive hover:text-destructive">
              <LogOut className="w-3.5 h-3.5" /> Leave
            </Button>
          )}
        </div>
      </div>

      <section className="space-y-3">
        <p className="eyebrow">Members</p>
        {members === null && !membersError ? (
          <div className="space-y-2">
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton key={i} className="h-14 w-full rounded-xl" />
            ))}
          </div>
        ) : membersError ? (
          <div role="alert" className="rounded-2xl panel-inset px-6 py-8 text-center">
            <p className="text-sm text-foreground">{membersError}</p>
            <Button variant="outline" size="sm" onClick={loadMembers} className="mt-4">Try again</Button>
          </div>
        ) : (
          <div className="rounded-2xl panel divide-y divide-hairline">
            {members!.map((m) => (
              <MemberRow
                key={m.id}
                member={m}
                self={isSelf(m)}
                roles={role ? assignableRolesFor(role) : []}
                canManage={!!role && canManageMember(role, m.role)}
                onChangeRole={async (next) => {
                  if (!m.account_id) return;
                  try {
                    await organizationApi.changeRole(id, m.account_id, next);
                    toast.success(`${m.display_name} is now ${ROLE_LABEL[next].toLowerCase()}`);
                  } catch (err) {
                    toast.error(apiErrorMessage(err, 'Failed to change role'));
                  }
                  await loadMembers();
                }}
                onRemove={() => setRemoveTarget(m)}
              />
            ))}
            {members!.length === 0 && (
              <p className="px-5 py-8 text-center text-sm text-muted-foreground">No members.</p>
            )}
          </div>
        )}
        {role === 'viewer' && (
          <p className="text-[11px] text-muted-foreground/70 px-1">Emails are visible to owners, admins and developers only.</p>
        )}
      </section>

      {canManage && (
        <InviteSection orgId={id} roles={assignableRolesFor(role!)} invitations={invitations} onChanged={loadInvitations} />
      )}

      {canTransfer && members && (
        <section className="space-y-3">
          <p className="eyebrow">Ownership</p>
          <div className="rounded-2xl panel p-5 flex items-center justify-between gap-4">
            <div>
              <p className="text-sm font-medium text-foreground">Transfer ownership</p>
              <p className="text-xs text-muted-foreground mt-0.5 max-w-md">
                Make another member the owner. You become an admin and must sign in again.
              </p>
            </div>
            <Button variant="outline" size="sm" onClick={() => setTransferOpen(true)} className="shrink-0">
              Transfer…
            </Button>
          </div>
        </section>
      )}

      {org && (
        <RenameDialog
          open={renameOpen}
          onOpenChange={setRenameOpen}
          orgId={id}
          current={org.name}
          onRenamed={() => loadOrgs(true)}
        />
      )}

      <ConfirmDialog
        open={!!removeTarget}
        onOpenChange={(o) => !o && setRemoveTarget(null)}
        title={`Remove ${removeTarget?.display_name ?? 'member'}?`}
        body="They lose access to this organization right away. Their sessions are signed out and any pending invitations to their email are cancelled."
        confirmLabel="Remove"
        onConfirm={async () => {
          if (!removeTarget?.account_id) return;
          await organizationApi.removeMember(id, removeTarget.account_id);
          toast.success('Member removed');
          setRemoveTarget(null);
          await loadMembers();
        }}
      />

      <ConfirmDialog
        open={leaveOpen}
        onOpenChange={setLeaveOpen}
        title={`Leave ${org?.name ?? 'this organization'}?`}
        body="You lose access to everything in it and are signed out on every device. You will need a new invitation to rejoin."
        confirmLabel="Leave"
        onConfirm={async () => {
          if (!myAccountId) throw new Error('Your session does not identify your account. Sign in again.');
          await organizationApi.removeMember(id, myAccountId);
          clearAuth();
          router.push('/login');
          toast.success('You left the organization');
        }}
      />

      {canTransfer && members && org && (
        <TransferDialog
          open={transferOpen}
          onOpenChange={setTransferOpen}
          orgId={id}
          orgName={org.name}
          candidates={members.filter((m) => m.role !== 'owner' && !!m.account_id)}
          onTransferred={() => {
            clearAuth();
            toast.success('Ownership transferred. Sign in again to continue.');
            router.push(loginUrlWithNext(`/dashboard/organizations/${id}/members`));
          }}
        />
      )}
    </div>
  );
}

function RoleBadge({ role }: { role: OrgMember['role'] }) {
  return (
    <span className="font-mono text-[10px] uppercase tracking-[0.12em] px-1.5 py-0.5 rounded-md border border-hairline bg-surface-1 text-muted-foreground">
      {ROLE_LABEL[role]}
    </span>
  );
}

function MemberRow({
  member,
  self,
  canManage,
  roles,
  onChangeRole,
  onRemove,
}: {
  member: OrgMember;
  self: boolean;
  canManage: boolean;
  roles: readonly AssignableRole[];
  onChangeRole: (role: AssignableRole) => Promise<void>;
  onRemove: () => void;
}) {
  const isOwner = member.role === 'owner';
  const editable = canManage && !isOwner && !!member.account_id;
  return (
    <div className="flex items-center gap-4 px-5 py-3.5">
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium text-foreground truncate">
          {member.display_name}
          {self && <span className="ml-2 font-mono text-[10px] text-brand">You</span>}
        </p>
        {member.email && <p className="text-xs text-muted-foreground truncate">{member.email}</p>}
      </div>
      <span className="hidden sm:block text-xs text-muted-foreground font-mono">
        {format(new Date(member.joined_at), 'MMM d, yyyy')}
      </span>
      {editable ? (
        <Select value={member.role} onValueChange={(v) => v && v !== member.role && void onChangeRole(v as AssignableRole)}>
          <SelectTrigger size="sm" aria-label={`Role for ${member.display_name}`} className="w-32">
            <SelectValue>{ROLE_LABEL[member.role]}</SelectValue>
          </SelectTrigger>
          <SelectContent>
            {roles.map((r) => (
              <SelectItem key={r} value={r} className="text-sm py-2">{ROLE_LABEL[r]}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      ) : (
        <span className="w-32 flex justify-start"><RoleBadge role={member.role} /></span>
      )}
      {canManage && !isOwner && !self && member.account_id ? (
        <Button variant="ghost" size="icon-sm" onClick={onRemove} aria-label={`Remove ${member.display_name}`}
          className="text-muted-foreground hover:text-destructive">
          <Trash2 className="w-3.5 h-3.5" />
        </Button>
      ) : (
        <span className="w-7" aria-hidden />
      )}
    </div>
  );
}

function InviteSection({
  orgId,
  roles,
  invitations,
  onChanged,
}: {
  orgId: string;
  roles: readonly AssignableRole[];
  invitations: OrgInvitation[];
  onChanged: () => Promise<void>;
}) {
  const [email, setEmail] = useState('');
  const [role, setRole] = useState<AssignableRole>('developer');
  const [sending, setSending] = useState(false);
  const [revoking, setRevoking] = useState<string | null>(null);

  const send = async (e: React.FormEvent) => {
    e.preventDefault();
    setSending(true);
    try {
      await organizationApi.invite(orgId, email.trim(), role);
      toast.success(`Invitation sent to ${email.trim()}`);
      setEmail('');
      await onChanged();
    } catch (err) {
      toast.error(apiErrorMessage(err, 'Failed to send invitation'));
    } finally {
      setSending(false);
    }
  };

  const revoke = async (inv: OrgInvitation) => {
    setRevoking(inv.id);
    try {
      await organizationApi.revokeInvitation(orgId, inv.id);
      toast.success('Invitation revoked');
      await onChanged();
    } catch (err) {
      toast.error(apiErrorMessage(err, 'Failed to revoke invitation'));
    } finally {
      setRevoking(null);
    }
  };

  return (
    <section className="space-y-3">
      <p className="eyebrow">Invitations</p>
      <form onSubmit={send} className="rounded-2xl panel p-5 grid gap-4 sm:grid-cols-[1fr_200px_auto] sm:items-end">
        <div className="space-y-1.5">
          <Label htmlFor="invite-email" className="eyebrow">Email</Label>
          <Input id="invite-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)}
            required maxLength={254} placeholder="teammate@example.com" />
        </div>
        <div className="space-y-1.5">
          <Label className="eyebrow">Role</Label>
          <Select value={role} onValueChange={(v) => v && setRole(v as AssignableRole)}>
            <SelectTrigger className="w-full h-9" aria-label="Role for the invitation">
              <SelectValue>{ROLE_LABEL[role]}</SelectValue>
            </SelectTrigger>
            <SelectContent>
              {roles.map((r) => (
                <SelectItem key={r} value={r} className="text-sm py-2">{ROLE_LABEL[r]}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <Button type="submit" disabled={sending || !email.trim()} className="gap-1.5">
          <UserPlus className="w-3.5 h-3.5" /> {sending ? 'Sending…' : 'Invite'}
        </Button>
        <p className="text-xs text-muted-foreground sm:col-span-3 -mt-2">{ROLE_HINT[role]}</p>
      </form>

      {invitations.length > 0 && (
        <div className="rounded-2xl panel divide-y divide-hairline">
          {invitations.map((inv) => (
            <div key={inv.id} className="flex items-center gap-4 px-5 py-3">
              <div className="min-w-0 flex-1">
                <p className="text-sm text-foreground truncate">{inv.email}</p>
                <p className="text-xs text-muted-foreground">
                  {ROLE_LABEL[inv.role]} · expires {format(new Date(inv.expires_at), 'MMM d, yyyy')}
                </p>
              </div>
              <Button variant="ghost" size="sm" onClick={() => revoke(inv)} disabled={revoking === inv.id}
                aria-label={`Revoke invitation for ${inv.email}`} className="gap-1.5 text-muted-foreground hover:text-destructive">
                <X className="w-3.5 h-3.5" /> Revoke
              </Button>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function RenameDialog({
  open,
  onOpenChange,
  orgId,
  current,
  onRenamed,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  orgId: string;
  current: string;
  onRenamed: () => Promise<void>;
}) {
  const [name, setName] = useState(current);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (open) setName(current);
  }, [open, current]);

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    try {
      await organizationApi.rename(orgId, name.trim());
      await onRenamed();
      toast.success('Organization renamed');
      onOpenChange(false);
    } catch (err) {
      toast.error(apiErrorMessage(err, 'Failed to rename'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(o) => !saving && onOpenChange(o)}>
      <DialogContent>
        <form onSubmit={save} className="grid gap-4">
          <DialogHeader>
            <DialogTitle className="text-base font-display font-semibold">Rename organization</DialogTitle>
          </DialogHeader>
          <div className="space-y-1.5">
            <Label htmlFor="rename-org" className="eyebrow">Name</Label>
            <Input id="rename-org" value={name} onChange={(e) => setName(e.target.value)} required maxLength={64} autoFocus />
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)} disabled={saving}>Cancel</Button>
            <Button type="submit" disabled={saving || !name.trim() || name.trim() === current}>{saving ? 'Saving…' : 'Save'}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function ConfirmDialog({
  open,
  onOpenChange,
  title,
  body,
  confirmLabel,
  onConfirm,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  body: string;
  confirmLabel: string;
  onConfirm: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);

  const confirm = async () => {
    setBusy(true);
    try {
      await onConfirm();
    } catch (err) {
      toast.error(apiErrorMessage(err, 'Something went wrong'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(o) => !busy && onOpenChange(o)}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle className="text-base font-display font-semibold">{title}</DialogTitle>
          <DialogDescription>{body}</DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={busy}>Cancel</Button>
          <Button variant="destructive" onClick={confirm} disabled={busy}>{busy ? 'Working…' : confirmLabel}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// Two steps: pick the new owner, then confirm. A stale login answers 401 reauth_required,
// which the API client turns into a sign-in that returns to this page (see client.ts).
function TransferDialog({
  open,
  onOpenChange,
  orgId,
  orgName,
  candidates,
  onTransferred,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  orgId: string;
  orgName: string;
  candidates: OrgMember[];
  onTransferred: () => void;
}) {
  const [targetId, setTargetId] = useState('');
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const target = candidates.find((m) => m.account_id === targetId) ?? null;

  useEffect(() => {
    if (!open) {
      setTargetId('');
      setConfirming(false);
    }
  }, [open]);

  const transfer = async () => {
    if (!target?.account_id) return;
    setBusy(true);
    try {
      await organizationApi.transferOwnership(orgId, target.account_id);
      onOpenChange(false);
      onTransferred();
    } catch (err) {
      const res = (err as { response?: { status?: number; data?: { code?: string } } }).response;
      if (res?.status === 401 && res.data?.code === 'reauth_required') return;
      toast.error(apiErrorMessage(err, 'Failed to transfer ownership'));
      setConfirming(false);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(o) => !busy && onOpenChange(o)}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle className="text-base font-display font-semibold">Transfer ownership</DialogTitle>
          <DialogDescription>
            {confirming
              ? `Transfer ${orgName} to ${target?.display_name}?`
              : 'Choose the member who becomes the owner.'}
          </DialogDescription>
        </DialogHeader>

        {!confirming ? (
          <>
            {candidates.length === 0 ? (
              <p className="text-sm text-muted-foreground">Invite someone and wait for them to join first.</p>
            ) : (
              <div className="space-y-1.5">
                <Label className="eyebrow">New owner</Label>
                <Select value={targetId} onValueChange={(v) => v && setTargetId(v)}>
                  <SelectTrigger className="w-full h-9" aria-label="New owner">
                    <SelectValue placeholder="Select a member">
                      {target ? `${target.display_name}${target.email ? ` (${target.email})` : ''}` : undefined}
                    </SelectValue>
                  </SelectTrigger>
                  <SelectContent>
                    {candidates.map((m) => (
                      <SelectItem key={m.id} value={m.account_id!} className="text-sm py-2">
                        {m.display_name}{m.email ? ` (${m.email})` : ''}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            )}
            <DialogFooter>
              <Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
              <Button onClick={() => setConfirming(true)} disabled={!target}>Continue</Button>
            </DialogFooter>
          </>
        ) : (
          <>
            <ul className="list-disc pl-5 text-sm text-muted-foreground space-y-1">
              <li>{target?.display_name} becomes the owner, with sole control of nuke, exit and billing.</li>
              <li>You become an admin and are signed out everywhere.</li>
              <li>You will be asked to sign in again to confirm if your login is not recent.</li>
            </ul>
            <DialogFooter>
              <Button variant="outline" onClick={() => setConfirming(false)} disabled={busy}>Back</Button>
              <Button variant="destructive" onClick={transfer} disabled={busy}>{busy ? 'Transferring…' : 'Transfer ownership'}</Button>
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
