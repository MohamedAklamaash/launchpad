"use client";

import { useEffect, useMemo, useState } from "react";
import {
  Github,
  Users,
  CreditCard,
  User as UserIcon,
  Server,
  Rocket,
  Trash2,
  UserPlus,
  Copy,
  Check,
} from "lucide-react";
import Image from "next/image";
import Link from "next/link";
import { ORGS_ENABLED } from "@/lib/orgs-enabled";
import { useOrgRole } from "@/lib/use-org-role";
import { ROLE_LABEL } from "@/lib/org-matrix";
import { useAuthStore } from "@/lib/store/auth";
import { infrastructureApi } from "@/lib/api/infrastructures";
import { applicationApi } from "@/lib/api/applications";
import { authApi } from "@/lib/api/auth";
import { Infrastructure } from "@/types/infrastructure";
import { InvitedUser } from "@/types/auth";
import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/layout/page-header";
import { EmptyState } from "@/components/ui/empty-state";
import { cn } from "@/lib/utils";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { toast } from "sonner";

type InviteRole = "admin" | "user" | "guest";
const ROLE_HINTS: { value: InviteRole; label: string; hint: string }[] = [
  { value: "admin", label: "Admin", hint: "Deploy & manage apps" },
  { value: "user", label: "User", hint: "View only" },
  { value: "guest", label: "Guest", hint: "Limited access" },
];
const emptyInvite = {
  infra_id: "",
  email: "",
  user_name: "",
  password: "",
  role: "user" as InviteRole,
};

const ROLE_STYLES: Record<string, string> = {
  super_admin: "border-brand/30 bg-brand-soft text-brand",
  admin: "border-border bg-muted text-foreground",
  user: "border-success/30 bg-success/10 text-success",
  guest: "border-border bg-surface-1 text-muted-foreground",
};
const RANK: Record<string, number> = {
  super_admin: 3,
  admin: 2,
  user: 1,
  guest: 0,
};
const roleLabel = (r: string) => r.replace("_", " ");

type TabKey = "profile" | "organization" | "billing";

interface Member {
  id: string;
  email: string;
  user_name: string;
  role: string;
  infraRoles: { id: string; name: string; role: string }[];
  infraIds: string[];
  pending: boolean;
}

export default function SettingsPage() {
  const user = useAuthStore((s) => s.user);
  const orgRole = useOrgRole();
  const [infras, setInfras] = useState<Infrastructure[]>([]);
  const [invited, setInvited] = useState<InvitedUser[]>([]);
  const [appCount, setAppCount] = useState<number | null>(null);
  const [tab, setTab] = useState<TabKey>("profile");
  const [removeTarget, setRemoveTarget] = useState<Member | null>(null);
  const [removing, setRemoving] = useState(false);
  const [inviteOpen, setInviteOpen] = useState(false);
  const [inviteForm, setInviteForm] = useState(emptyInvite);
  const [inviting, setInviting] = useState(false);
  const [inviteUrl, setInviteUrl] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const canSeeOrg = user?.role === "super_admin" || user?.role === "admin";
  const canInvite = user?.role === "super_admin";
  const myRank = RANK[user?.role ?? "guest"] ?? 0;

  useEffect(() => {
    infrastructureApi
      .list()
      .then(async (list) => {
        setInfras(list);
        const appLists = await Promise.all(
          list.map((i) => applicationApi.list(i.id).catch(() => [])),
        );
        setAppCount(appLists.reduce((n, a) => n + a.length, 0));
      })
      .catch(() => setAppCount(0));
    if (canSeeOrg && !ORGS_ENABLED) {
      authApi
        .listInvitedUsers()
        .then(setInvited)
        .catch(() =>
          toast.error("Failed to load members", { id: "settings-members" }),
        );
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
        infraRoles: (u.infra_id ?? []).map((id) => ({
          id,
          name: infraName.get(id) ?? id.slice(0, 8),
          role: u.roles?.[id] ?? u.role,
        })),
      }));
  }, [invited, infras, user?.id]);

  const pendingCount = members.filter((m) => m.pending).length;
  const inviteInfra = infras.find((i) => i.id === inviteForm.infra_id);

  const openInvite = () => {
    setInviteUrl(null);
    setInviteForm({ ...emptyInvite, infra_id: infras[0]?.id ?? "" });
    setInviteOpen(true);
  };

  const handleInvite = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!inviteForm.infra_id) return;
    setInviting(true);
    try {
      await authApi.inviteUser(inviteForm);
      setInviteUrl(
        `${window.location.origin}/login?email=${encodeURIComponent(inviteForm.email)}`,
      );
      toast.success(
        `Invited ${inviteForm.user_name} to ${inviteInfra?.name ?? "the infrastructure"}`,
      );
      setInvited(await authApi.listInvitedUsers());
    } catch (err: unknown) {
      const e = err as {
        response?: { data?: { message?: string; error?: string } };
      };
      toast.error(
        e.response?.data?.message ||
          e.response?.data?.error ||
          "Failed to invite",
      );
    } finally {
      setInviting(false);
    }
  };

  const inviteElsewhere = () => {
    setInviteUrl(null);
    setInviteForm((f) => ({
      ...f,
      infra_id: infras.find((i) => i.id !== f.infra_id)?.id ?? f.infra_id,
      role: "user",
    }));
  };

  const copyInviteUrl = async () => {
    if (!inviteUrl) return;
    try {
      await navigator.clipboard.writeText(inviteUrl);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      toast.error("Couldn't copy. Select the link and copy it by hand.");
    }
  };

  const doRemove = async () => {
    if (!removeTarget) return;
    setRemoving(true);
    try {
      const owned = new Set(infras.map((i) => i.id));
      const scopedInfraIds = removeTarget.infraIds.filter((id) =>
        owned.has(id),
      );
      if (scopedInfraIds.length === 0) {
        toast.error(
          "You don't manage any organization this member belongs to.",
        );
        return;
      }
      // Remove infra-side membership first, then clear the auth-side (which may delete the
      // account on the last org) ONLY for the infras that succeeded — so the two systems never
      // diverge and any failure leaves the member visible to retry.
      const results = await Promise.allSettled(
        scopedInfraIds.map((id) =>
          infrastructureApi.removeUser(id, removeTarget.id).then(() => id),
        ),
      );
      const removed = results.flatMap((r) =>
        r.status === "fulfilled" ? [r.value] : [],
      );
      if (removed.length > 0) {
        await authApi.removeMemberFromOrg(removeTarget.id, removed);
      }
      const failed = scopedInfraIds.length - removed.length;
      if (failed > 0) {
        toast.error(
          `Removed from ${removed.length} of ${scopedInfraIds.length} infrastructures. Reopen to retry the rest.`,
        );
      } else {
        toast.success(
          `${removeTarget.user_name} removed from the organization`,
        );
      }
      setInvited(await authApi.listInvitedUsers());
      setRemoveTarget(null);
    } catch (e: unknown) {
      const err = e as {
        response?: { data?: { message?: string; error?: string } };
      };
      toast.error(
        err.response?.data?.message ||
          err.response?.data?.error ||
          "Failed to remove member",
      );
    } finally {
      setRemoving(false);
    }
  };

  if (!user) return null;

  const tabs: {
    key: TabKey;
    label: string;
    icon: typeof UserIcon;
    show: boolean;
  }[] = [
    { key: "profile", label: "Profile", icon: UserIcon, show: true },
    {
      key: "organization",
      label: "Organization",
      icon: Users,
      show: ORGS_ENABLED || canSeeOrg,
    },
    {
      key: "billing",
      label: "Billing",
      icon: CreditCard,
      show: orgRole.allow("billing", true),
    },
  ];

  return (
    <div className="space-y-6">
      <PageHeader title="Settings" description="Your profile, organization and account." />

      <div className="flex flex-col gap-6 md:flex-row md:gap-10">
        <nav aria-label="Settings sections" className="flex shrink-0 gap-1 overflow-x-auto md:w-48 md:flex-col">
          {tabs
            .filter((t) => t.show)
            .map(({ key, label, icon: Icon }) => (
              <button
                key={key}
                onClick={() => setTab(key)}
                aria-current={tab === key ? "page" : undefined}
                className={cn(
                  "flex h-8 items-center gap-2.5 whitespace-nowrap rounded-md px-2.5 text-left text-sm transition-colors",
                  tab === key
                    ? "bg-muted font-medium text-foreground"
                    : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
                )}
              >
                <Icon className="size-4" />
                {label}
                {key === "organization" && pendingCount > 0 && (
                  <span className="ml-auto rounded-md border border-warning/30 bg-warning/10 px-1.5 py-0.5 text-xs text-foreground">
                    {pendingCount}
                  </span>
                )}
              </button>
            ))}
        </nav>

        <div className="flex-1 min-w-0 max-w-2xl">
          <div key={tab} className="reveal">
              {tab === "profile" && (
                <section className="space-y-3">
                  <h2 className="text-sm font-medium text-foreground">Profile</h2>
                  <div className="rounded-lg border border-border bg-card p-5">
                    <div className="flex items-center gap-4">
                      <span className="flex size-14 shrink-0 items-center justify-center overflow-hidden rounded-full border border-border bg-surface-2">
                        {user.profile_url ? (
                          <Image
                            src={user.profile_url}
                            alt=""
                            width={56}
                            height={56}
                            className="w-14 h-14 object-cover"
                          />
                        ) : (
                          <UserIcon className="w-6 h-6 text-muted-foreground" />
                        )}
                      </span>
                      <div className="min-w-0">
                        <p className="text-base font-medium text-foreground truncate">
                          {user.user_name}
                        </p>
                        <p className="text-sm text-muted-foreground truncate">
                          {user.email}
                        </p>
                      </div>
                      <span
                        className={`ml-auto shrink-0 text-xs px-2 py-0.5 rounded-md border ${ROLE_STYLES[user.role] ?? ROLE_STYLES.guest}`}
                      >
                        {roleLabel(user.role)}
                      </span>
                    </div>

                    <div className="mt-5 grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-border bg-border sm:grid-cols-3">
                      <Stat
                        icon={Server}
                        tint="text-muted-foreground"
                        label="Infrastructures"
                        value={infras.length}
                      />
                      <Stat
                        icon={Rocket}
                        tint="text-muted-foreground"
                        label="Applications"
                        value={appCount}
                      />
                      {canSeeOrg && (
                        <Stat
                          icon={Users}
                          tint="text-muted-foreground"
                          label="Members"
                          value={members.length + 1}
                        />
                      )}
                    </div>

                    <div className="mt-4 rounded-lg border border-border bg-surface-1 divide-y divide-border">
                      {user.metadata?.github?.username && (
                        <Row label="GitHub">
                          <span className="flex items-center gap-1.5 text-foreground/80">
                            <Github className="size-3.5" />
                            {user.metadata.github.username}
                          </span>
                        </Row>
                      )}
                      <Row label="Member since">
                        <span className="text-foreground/80">
                          {new Date(user.created_at).toLocaleDateString(
                            undefined,
                            { year: "numeric", month: "long", day: "numeric" },
                          )}
                        </span>
                      </Row>
                    </div>
                  </div>
                </section>
              )}

              {tab === "organization" && ORGS_ENABLED && (
                <section className="space-y-3">
                  <h2 className="text-sm font-medium text-foreground">Organization</h2>
                  <div className="space-y-4 rounded-lg border border-border bg-card p-5">
                    {orgRole.org ? (
                      <>
                        <div className="flex items-start justify-between gap-3">
                          <div className="min-w-0">
                            <p className="text-sm font-medium text-foreground truncate">
                              {orgRole.org.name}
                            </p>
                            <p className="text-xs text-muted-foreground mt-0.5">
                              {orgRole.org.is_personal ? "Personal" : "Team"}{" "}
                              organization
                            </p>
                          </div>
                          <span className="text-xs px-1.5 py-0.5 rounded-md border border-border bg-surface-1 text-muted-foreground">
                            {ROLE_LABEL[orgRole.org.role]}
                          </span>
                        </div>
                        <Link
                          href={`/dashboard/organizations/${orgRole.org.id}/members`}
                          className="inline-flex"
                        >
                          <Button variant="outline" size="sm" className="gap-1.5">
                            <Users className="size-3.5" /> Manage members and name
                          </Button>
                        </Link>
                      </>
                    ) : (
                      <p className="text-sm text-muted-foreground">
                        {orgRole.loading
                          ? "Loading organization…"
                          : "No active organization. Pick one from the switcher in the header."}
                      </p>
                    )}
                  </div>
                </section>
              )}

              {tab === "organization" && canSeeOrg && !ORGS_ENABLED && (
                <section className="space-y-3">
                  <div className="flex items-center justify-between gap-3">
                    <div className="flex items-baseline gap-2.5 min-w-0">
                      <h2 className="text-sm font-medium text-foreground">Organization</h2>
                      <span className="truncate text-xs text-muted-foreground">
                        {members.length + 1}{" "}
                        {members.length === 0 ? "member" : "members"}
                        {pendingCount > 0 ? ` · ${pendingCount} pending` : ""}
                      </span>
                    </div>
                    {canInvite && (
                      <Button
                        size="sm"
                        onClick={openInvite}
                        disabled={infras.length === 0}
                        className="gap-1.5 shrink-0"
                      >
                        <UserPlus className="size-3.5" /> Invite member
                      </Button>
                    )}
                  </div>
                  <div className="rounded-lg border border-border bg-card divide-y divide-border">
                    <MemberRow
                      name={user.user_name}
                      email={user.email}
                      role={user.role}
                      profileUrl={user.profile_url}
                      tag="You · Owner"
                    />
                    {members.map((m) => (
                      <MemberRow
                        key={m.id}
                        name={m.user_name}
                        email={m.email}
                        role={m.role}
                        infraRoles={m.infraRoles}
                        pending={m.pending}
                        onRemove={
                          myRank > (RANK[m.role] ?? 0)
                            ? () => setRemoveTarget(m)
                            : undefined
                        }
                      />
                    ))}
                    {members.length === 0 && (
                      <div className="px-5 py-8 text-center">
                        <p className="text-sm font-medium text-foreground">
                          No teammates yet
                        </p>
                        <p className="text-xs text-muted-foreground mt-1 mb-4 max-w-xs mx-auto">
                          {canInvite
                            ? "Invite people into any of your infrastructures with a role scoped to that infrastructure."
                            : "Only the owner can invite members."}
                        </p>
                        {canInvite && (
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={openInvite}
                            disabled={infras.length === 0}
                            className="gap-1.5"
                          >
                            <UserPlus className="size-3.5" /> Invite member
                          </Button>
                        )}
                      </div>
                    )}
                  </div>
                  <p className="px-1 text-xs text-muted-foreground">
                    <span className="inline-block w-1.5 h-1.5 rounded-full bg-warning align-middle mr-1.5" />
                    Pending means the invite hasn&apos;t been accepted (email
                    not verified) yet.
                  </p>
                </section>
              )}

              {tab === "billing" && (
                <section className="space-y-3">
                  <h2 className="text-sm font-medium text-foreground">Billing</h2>
                  <EmptyState
                    icon={CreditCard}
                    title="No invoices yet"
                    description="Your plan, payment history and invoices will show up here once billing is turned on for your account."
                  />
                </section>
              )}
          </div>
        </div>
      </div>

      <Dialog
        open={!!removeTarget}
        onOpenChange={(o) => {
          if (!o) setRemoveTarget(null);
        }}
      >
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle className="text-base font-semibold">
              Remove member
            </DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            Remove{" "}
            <span className="text-foreground font-medium">
              {removeTarget?.user_name}
            </span>{" "}
            from the organization? They lose access to your infrastructures.
            Their account is deleted only if this was their last organization.
          </p>
          <div className="flex gap-2 justify-end mt-2">
            <Button variant="outline" onClick={() => setRemoveTarget(null)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={doRemove}
              disabled={removing}
              className="gap-1.5"
            >
              <Trash2 className="size-3.5" />
              {removing ? "Removing…" : "Remove"}
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      <Dialog open={inviteOpen} onOpenChange={setInviteOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle className="text-base font-semibold">
              {inviteUrl ? "Invitation ready" : "Invite member"}
            </DialogTitle>
          </DialogHeader>
          {inviteUrl ? (
            <div className="space-y-4">
              <p className="text-xs text-muted-foreground">
                <span className="text-foreground font-medium">
                  {inviteForm.user_name}
                </span>{" "}
                was invited to{" "}
                <span className="text-foreground font-medium">
                  {inviteInfra?.name}
                </span>{" "}
                as{" "}
                <span
                  className={`font-medium ${ROLE_STYLES[inviteForm.role]?.split(" ").pop()}`}
                >
                  {inviteForm.role}
                </span>
                . Share this sign-in link and the temporary password you set.
              </p>
              <div className="rounded-lg border border-border bg-surface-1 p-3 flex items-center gap-3">
                <p className="flex-1 text-xs font-mono text-muted-foreground break-all">
                  {inviteUrl}
                </p>
                <button
                  onClick={copyInviteUrl}
                  className="shrink-0 rounded-sm text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
                  aria-label="Copy link"
                >
                  {copied ? (
                    <Check className="size-4 text-success" />
                  ) : (
                    <Copy className="size-4" />
                  )}
                </button>
              </div>
              <div className="flex gap-2">
                {infras.length > 1 && (
                  <Button
                    variant="outline"
                    onClick={inviteElsewhere}
                    className="flex-1 gap-1.5"
                  >
                    <UserPlus className="size-3.5" /> Add to another
                  </Button>
                )}
                <Button onClick={() => setInviteOpen(false)} className="flex-1">
                  Done
                </Button>
              </div>
            </div>
          ) : (
            <form onSubmit={handleInvite} className="space-y-4">
              <div className="space-y-1.5">
                <Label htmlFor="inv-infra">Infrastructure</Label>
                <Select
                  value={inviteForm.infra_id}
                  onValueChange={(v) => {
                    if (v) setInviteForm({ ...inviteForm, infra_id: v });
                  }}
                >
                  <SelectTrigger id="inv-infra" className="w-full">
                    <SelectValue placeholder="Select an infrastructure">
                      {inviteInfra?.name}
                    </SelectValue>
                  </SelectTrigger>
                  <SelectContent>
                    {infras.map((i) => (
                      <SelectItem
                        key={i.id}
                        value={i.id}
                      >
                        {i.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1.5">
                  <Label htmlFor="inv-email">Email</Label>
                  <Input
                    id="inv-email"
                    type="email"
                    placeholder="user@example.com"
                    value={inviteForm.email}
                    onChange={(e) =>
                      setInviteForm({ ...inviteForm, email: e.target.value })
                    }
                    required
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="inv-user">Username</Label>
                  <Input
                    id="inv-user"
                    type="text"
                    placeholder="johndoe"
                    value={inviteForm.user_name}
                    onChange={(e) =>
                      setInviteForm({
                        ...inviteForm,
                        user_name: e.target.value,
                      })
                    }
                    required
                  />
                </div>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="inv-pass">Temporary password</Label>
                <Input
                  id="inv-pass"
                  type="password"
                  placeholder="At least 6 characters"
                  value={inviteForm.password}
                  onChange={(e) =>
                    setInviteForm({ ...inviteForm, password: e.target.value })
                  }
                  required
                  minLength={6}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="inv-role">Role in this infrastructure</Label>
                <Select
                  value={inviteForm.role}
                  onValueChange={(v) =>
                    setInviteForm({ ...inviteForm, role: v as InviteRole })
                  }
                >
                  <SelectTrigger id="inv-role" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {ROLE_HINTS.map((r) => (
                      <SelectItem
                        key={r.value}
                        value={r.value}
                      >
                        <span className="font-medium">{r.label}</span>
                        <span className="text-muted-foreground">
                          {" "}
                          : {r.hint}
                        </span>
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <Button
                type="submit"
                disabled={inviting || !inviteForm.infra_id}
                className="w-full mt-1"
              >
                {inviting ? "Sending…" : "Send invite"}
              </Button>
            </form>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}

function Stat({
  icon: Icon,
  label,
  value,
  tint,
}: {
  icon: typeof UserIcon;
  label: string;
  value: number | null;
  tint: string;
}) {
  return (
    <div className="bg-card p-4">
      <Icon className={`mb-2 size-4 ${tint}`} />
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="mt-0.5 text-lg font-semibold tabular-nums text-foreground">
        {value ?? "Not available"}
      </p>
    </div>
  );
}

function Row({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-center justify-between gap-4 px-4 py-2.5">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className="text-xs">{children}</span>
    </div>
  );
}

function MemberRow({
  name,
  email,
  role,
  profileUrl,
  tag,
  infraRoles,
  pending,
  onRemove,
}: {
  name: string;
  email: string;
  role: string;
  profileUrl?: string;
  tag?: string;
  infraRoles?: { id: string; name: string; role: string }[];
  pending?: boolean;
  onRemove?: () => void;
}) {
  return (
    <div className="flex items-center gap-3 px-5 py-3.5">
      <span className="flex size-9 shrink-0 items-center justify-center overflow-hidden rounded-full border border-border bg-surface-2">
        {profileUrl ? (
          <Image
            src={profileUrl}
            alt=""
            width={36}
            height={36}
            className="w-9 h-9 object-cover"
          />
        ) : (
          <Users className="size-4 text-muted-foreground" />
        )}
      </span>
      <div className="min-w-0">
        <p className="text-sm font-medium text-foreground truncate">{name}</p>
        <p className="text-xs text-muted-foreground truncate">{email}</p>
      </div>
      <div className="ml-auto flex items-center gap-3 shrink-0">
        {tag && (
          <span className="hidden max-w-[140px] truncate text-xs text-muted-foreground sm:inline">
            {tag}
          </span>
        )}
        <span
          className={`flex items-center gap-1.5 text-xs font-medium ${pending ? "text-warning" : "text-success"}`}
        >
          <span
            className={`w-1.5 h-1.5 rounded-full ${pending ? "bg-warning" : "bg-success"}`}
          />
          {pending ? "Pending" : "Active"}
        </span>
        {infraRoles ? (
          <div className="flex flex-wrap justify-end gap-1.5 max-w-[200px] sm:max-w-[300px]">
            {infraRoles.map((ir) => (
              <span
                key={ir.id}
                className={`flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-xs ${ROLE_STYLES[ir.role] ?? ROLE_STYLES.guest}`}
              >
                <span className="max-w-[90px] truncate text-muted-foreground">
                  {ir.name}
                </span>
                <span className="opacity-40">·</span>
                <span className="capitalize">
                  {roleLabel(ir.role)}
                </span>
              </span>
            ))}
          </div>
        ) : (
          <span
            className={`text-xs px-2 py-0.5 rounded-md border ${ROLE_STYLES[role] ?? ROLE_STYLES.guest}`}
          >
            {roleLabel(role)}
          </span>
        )}
        {onRemove && (
          <button
            onClick={onRemove}
            aria-label={`Remove ${name} from organization`}
            className="rounded-sm text-muted-foreground transition-colors hover:text-destructive focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
          >
            <Trash2 className="size-3.5" />
          </button>
        )}
      </div>
    </div>
  );
}
