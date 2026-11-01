'use client';

import { useEffect, useState, useCallback } from 'react';
import { useRouter, useParams } from 'next/navigation';
import { Button } from '@/components/ui/button';
import { buttonVariants } from '@/components/ui/button-variants';
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
import Link from 'next/link';
import { ArrowLeft, Plus, ExternalLink, UserPlus, Copy, Check, Settings, Trash2, User, Pencil, RefreshCw, ShieldCheck, Download, Terminal, Flame, CloudCog, AppWindow } from 'lucide-react';
import { StatusBadge } from '@/components/ui/status-badge';
import { EmptyState } from '@/components/ui/empty-state';
import { Skeleton } from '@/components/ui/skeleton';
import { cn } from '@/lib/utils';
import { Infrastructure, InvitedUserSummary } from '@/types/infrastructure';
import { ApplicationSummary, ApplicationStatus } from '@/types/application';
import { infrastructureApi } from '@/lib/api/infrastructures';
import { applicationApi } from '@/lib/api/applications';
import { authApi } from '@/lib/api/auth';
import { useAuthStore } from '@/lib/store/auth';
import { useOrgRole } from '@/lib/use-org-role';
import { toast } from 'sonner';
import { DatabasesSection } from '@/components/databases-section';
import { ProvisioningLogsPanel } from '@/components/provisioning-logs-panel';
import { CostsPanel } from '@/components/costs-panel';
import { PolicyRefreshDialog } from '@/components/policy-refresh-dialog';
import { CfnRefreshDialog } from '@/components/cfn-refresh-dialog';
import { NukeInfrastructureDialog } from '@/components/nuke-infrastructure-dialog';
import { buildBootstrapEnvExports, getOnboardingMisconfiguration, resolveOnboardingScript } from '@/lib/onboarding-scripts';
import { isCfnOnboardingEnabled } from '@/lib/onboarding-cloudformation';

const API_GATEWAY_URL = process.env.NEXT_PUBLIC_API_GATEWAY_URL || 'http://localhost:8000';

const APP_IN_FLIGHT_STATUSES: ApplicationStatus[] = ['CREATED', 'BUILDING', 'PUSHING_IMAGE', 'DEPLOYING'];

const ROLE_COLORS: Record<string, string> = {
  super_admin: 'text-brand',
  admin: 'text-foreground',
  user: 'text-success',
  guest: 'text-muted-foreground',
};

function Banner({ tone = 'info', children }: { tone?: 'info' | 'error' | 'warning'; children: React.ReactNode }) {
  return (
    <div
      className={cn(
        'rounded-lg border px-4 py-3 text-sm',
        tone === 'info' && 'border-border bg-surface-1 text-foreground',
        tone === 'warning' && 'border-warning/30 bg-warning/10 text-foreground',
        tone === 'error' && 'border-destructive/30 bg-destructive/10 text-foreground',
      )}
    >
      {children}
    </div>
  );
}

function Dot({ tone, pulse }: { tone: 'brand' | 'warning'; pulse?: boolean }) {
  return <span className={cn('mt-1.5 size-1.5 shrink-0 rounded-full', tone === 'brand' ? 'bg-brand' : 'bg-warning', pulse && 'animate-pulse')} />;
}

function SettingsGroup({ title, tone, children }: { title: string; tone?: 'danger'; children: React.ReactNode }) {
  return (
    <section className="space-y-2">
      <h3 className={cn('px-1 text-xs font-medium', tone === 'danger' ? 'text-destructive' : 'text-muted-foreground')}>{title}</h3>
      {children}
    </section>
  );
}

function SettingsRow({ title, description, action, tone, highlight }: { title: string; description: React.ReactNode; action: React.ReactNode; tone?: 'danger'; highlight?: boolean }) {
  return (
    <div
      className={cn(
        'flex items-center justify-between gap-4 rounded-lg border px-4 py-3',
        tone === 'danger' ? 'border-destructive/30' : 'border-border bg-surface-1',
        highlight && 'border-warning/50',
      )}
    >
      <div className="min-w-0">
        <p className="text-sm font-medium text-foreground">{title}</p>
        <p className="mt-0.5 text-xs text-muted-foreground">{description}</p>
      </div>
      <div className="shrink-0">{action}</div>
    </div>
  );
}

// A blob-response request (evidence pack, exit export) gets its error body back as a
// Blob too, so the usual `err.response?.data?.error` read is undefined — read the blob's
// text and parse it to recover the server's actual message (e.g. a 429's retry-later text).
async function extractBlobErrorMessage(error: unknown, fallback = 'Failed to download evidence pack'): Promise<string> {
  const data = (error as { response?: { data?: unknown } })?.response?.data;
  if (data instanceof Blob) {
    try {
      const parsed = JSON.parse(await data.text()) as { error?: string };
      if (parsed.error) return parsed.error;
    } catch {
      // Not JSON (or empty) — fall through to the generic message below.
    }
  }
  return fallback;
}

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
  const [nukeOpen, setNukeOpen] = useState(false);
  const [reprovisioning, setReprovisioning] = useState(false);
  const [refreshPolicyOpen, setRefreshPolicyOpen] = useState(false);
  const [cfnRefreshOpen, setCfnRefreshOpen] = useState(false);
  const [verifyingCfn, setVerifyingCfn] = useState(false);
  const [provisioningError, setProvisioningError] = useState<string | null>(null);
  const [downloadingEvidence, setDownloadingEvidence] = useState(false);
  const [reissuingToken, setReissuingToken] = useState(false);
  const [setupCommand, setSetupCommand] = useState<{ invocation: string; misconfig: string | null } | null>(null);
  const [setupCommandCopied, setSetupCommandCopied] = useState(false);
  const [exitExportOpen, setExitExportOpen] = useState(false);
  const [downloadingExitExport, setDownloadingExitExport] = useState(false);
  const [completeExitOpen, setCompleteExitOpen] = useState(false);
  const [completingExit, setCompletingExit] = useState(false);

  // Invite dialog
  const [inviteOpen, setInviteOpen] = useState(false);
  const [inviteForm, setInviteForm] = useState({ email: '', password: '', user_name: '', role: 'user' as 'admin' | 'user' | 'guest' });
  const [inviting, setInviting] = useState(false);
  const [inviteUrl, setInviteUrl] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const orgRole = useOrgRole();
  const isSuperAdmin = user?.role === 'super_admin';
  const canDeploy = orgRole.allow('deploy', isSuperAdmin || user?.role === 'admin');
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

  // Owner-only controls (nuke, exit, delete, setup command, evidence pack, rename,
  // provisioning logs). With orgs on the matrix decides; with them off, the original check.
  const legacyIsOwner = isSuperAdmin && infra?.user_id === user?.id;
  const isOwner = orgRole.allow('manage_infrastructure', legacyIsOwner);
  const canSeeCosts = orgRole.allow('costs', legacyIsOwner);
  const canManageDatabases = orgRole.allow('databases', legacyIsOwner);

  useEffect(() => {
    if (!isOwner || infra?.status !== 'ERROR') return;
    let cancelled = false;
    infrastructureApi.getLogs(id)
      .then((logs) => { if (!cancelled) setProvisioningError(logs.error_message); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [id, isOwner, infra?.status]);

  // Three distinct states, and they must not be collapsed. "Never recorded" is not the
  // same as "behind": accounts onboarded before policy versioning report no version, and
  // showing them an out-of-date warning would be a false alarm on a policy they already
  // hold. `undefined` (rather than null) means the backend predates this field, so fall
  // back to the passive copy instead of rendering "Policy vundefined". Compared against
  // required_policy_version, not current_policy_version: the latter is the global
  // latest and can be ahead of what this infra's compute_type has ever needed, e.g. an
  // EKS-only bump doesn't make an ECS account stale.
  const policyState = ((): { kind: 'unknown' | 'unrecorded' | 'stale' | 'current'; title: string; detail: string } => {
    const applied = infra?.policy_version;
    const required = infra?.required_policy_version;
    if (applied === undefined || required === undefined || required === null) {
      return {
        kind: 'unknown',
        title: 'Refresh IAM Policy',
        detail: 'Re-apply the latest deployment policy if actions start failing with AccessDenied.',
      };
    }
    if (applied === null) {
      return {
        kind: 'unrecorded',
        title: 'Refresh IAM Policy',
        detail: `Launchpad hasn't recorded which policy version this account holds. Run the refresh once to record it (required: v${required}).`,
      };
    }
    if (applied < required) {
      return {
        kind: 'stale',
        title: 'IAM policy out of date',
        detail: `Your account has policy v${applied}; Launchpad now requires v${required}. Re-run the script before your next deploy.`,
      };
    }
    return {
      kind: 'current',
      title: 'Refresh IAM Policy',
      detail: `Policy v${applied} is current. Re-apply it if actions start failing with AccessDenied.`,
    };
  })();

  // Apps table has its own in-flight statuses (build/deploy/wake) distinct from the
  // infra's own PENDING/PROVISIONING — either one in flight should keep this page live.
  const appsInFlight = apps.some((a) => APP_IN_FLIGHT_STATUSES.includes(a.status));

  useEffect(() => {
    loadData();
    const interval = setInterval(() => {
      if (infra?.status === 'PENDING' || infra?.status === 'PROVISIONING' || appsInFlight) loadData();
    }, 5000);
    return () => clearInterval(interval);
  }, [id, infra?.status, appsInFlight, loadData]);

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
      toast.success(wasActive ? 'Destroy queued. Launchpad is tearing down your AWS resources.' : 'Infrastructure deleted');
      router.push('/dashboard/infrastructures');
    } catch (error: unknown) {
      const err = error as { response?: { data?: { error?: string } } };
      toast.error(err.response?.data?.error || 'Failed to delete infrastructure');
    } finally {
      setDeleting(false);
      setDeleteOpen(false);
    }
  };

  const handleNuked = () => {
    toast.success('Infrastructure nuked. Everything is removed.');
    router.push('/dashboard/infrastructures');
  };

  const handleDownloadEvidencePack = async () => {
    setDownloadingEvidence(true);
    try {
      const blob = await infrastructureApi.downloadEvidencePack(id);
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `evidence-pack-${id}.zip`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      // Revoking immediately can race the browser's download handoff in some
      // browsers; defer it a tick past that.
      setTimeout(() => window.URL.revokeObjectURL(url), 1000);
    } catch (error: unknown) {
      toast.error(await extractBlobErrorMessage(error));
    } finally {
      setDownloadingEvidence(false);
    }
  };

  const handleDownloadExitExport = async () => {
    setDownloadingExitExport(true);
    try {
      const blob = await infrastructureApi.downloadExitExport(id);
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `exit-export-${id}.zip`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => window.URL.revokeObjectURL(url), 1000);
      setExitExportOpen(false);
    } catch (error: unknown) {
      // A reauth_required 401 already triggers a redirect to /login via the axios
      // interceptor; this toast is a best-effort message in the moment before that lands.
      toast.error(await extractBlobErrorMessage(error, 'Failed to download exit export'));
    } finally {
      setDownloadingExitExport(false);
    }
  };

  const handleCompleteExit = async () => {
    setCompletingExit(true);
    try {
      const { status: httpStatus } = await infrastructureApi.completeExit(id);
      if (httpStatus === 202) {
        // Not final: the teardown request was published but not yet confirmed, and
        // exited_at is deliberately left unset server-side. Keep the dialog open so the
        // owner can retry rather than implying the exit already completed.
        toast.info('DNS teardown is still running. Retry to finish the exit.');
        return;
      }
      toast.success('Exit complete. Platform DNS records are removed.');
      setCompleteExitOpen(false);
      await loadData();
    } catch (error: unknown) {
      const err = error as { response?: { status?: number; data?: { error?: string } } };
      if (err.response?.status === 409) {
        toast.error('This infrastructure has already exited');
        setCompleteExitOpen(false);
      } else {
        toast.error(err.response?.data?.error || 'Failed to complete exit');
      }
    } finally {
      setCompletingExit(false);
    }
  };

  // The onboarding token is shown exactly once, at create time — the detail page never
  // has it. Mint a fresh one (rejected once onboarded) so an owner who lost or never
  // copied it can still get a working setup command without deleting/recreating the infra.
  const handleShowSetupCommand = async () => {
    setReissuingToken(true);
    try {
      const reissued = await infrastructureApi.reissueOnboardingToken(id);
      const misconfig = getOnboardingMisconfiguration();
      if (misconfig) {
        setSetupCommand({ invocation: '', misconfig });
      } else {
        const env = buildBootstrapEnvExports(
          reissued,
          reissued.onboarding_token,
          `${API_GATEWAY_URL}/api/infrastructures/onboarding/callback`,
        );
        setSetupCommand({ invocation: resolveOnboardingScript('bootstrap', env).invocation, misconfig: null });
      }
    } catch (error: unknown) {
      const err = error as { response?: { data?: { error?: string } } };
      toast.error(err.response?.data?.error || 'Failed to prepare setup command');
    } finally {
      setReissuingToken(false);
    }
  };

  // GUI-path equivalent of the script callback: claims the onboarding token, AssumeRoles
  // into the customer's account, and confirms the CFN quick-create stack actually ran.
  // Nothing persists "this infra chose CFN" before this succeeds, so the script's
  // "Show setup command" button stays visible alongside this one — see
  // plan/F8-cloudformation-onboarding.md §4.
  const handleCfnVerify = async () => {
    setVerifyingCfn(true);
    try {
      await infrastructureApi.cfnVerify(id);
      toast.success('Connection verified. Provisioning is queued.');
      await loadData();
    } catch (error: unknown) {
      const err = error as { response?: { status?: number; data?: { error?: string; retry_after_seconds?: number } } };
      const status = err.response?.status;
      const message = err.response?.data?.error;
      if (status === 400) {
        toast.error(message || 'LaunchpadDeploymentRole is not tagged as CloudFormation-managed yet. Launch the stack first.');
      } else if (status === 403) {
        const retrySeconds = err.response?.data?.retry_after_seconds;
        toast.error(message || (retrySeconds ? `AssumeRole failed. Try again in ${retrySeconds}s.` : 'AssumeRole failed.'));
      } else if (status === 404) {
        toast.error(message || 'CloudFormation onboarding is not available on this deployment.');
      } else if (status === 429) {
        toast.error(message || 'Too many requests for this operation. Try again later.');
      } else {
        toast.error(message || 'Failed to verify the connection');
      }
    } finally {
      setVerifyingCfn(false);
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
    <div className="space-y-6" aria-busy="true" aria-label="Loading infrastructure">
      <Skeleton className="h-4 w-16" />
      <div className="flex items-start justify-between">
        <div className="space-y-2.5">
          <Skeleton className="h-6 w-56" />
          <Skeleton className="h-4 w-72 max-w-full" />
        </div>
        <Skeleton className="h-8 w-28" />
      </div>
      <Skeleton className="h-20 w-full" />
      <Skeleton className="h-40 w-full" />
    </div>
  );

  if (!infra) return (
    <EmptyState
      icon={AppWindow}
      title="Infrastructure not found"
      description="It may have been deleted, or you may not have access to it."
      action={<Link href="/dashboard/infrastructures" className={buttonVariants({ variant: 'outline' })}>Back to infrastructure</Link>}
    />
  );

  const details = [
    { label: 'AWS account', value: infra.code, mono: true },
    { label: 'Region', value: infra.metadata?.aws_region ?? 'Not set', mono: true },
    { label: 'Compute', value: infra.compute_type === 'eks' ? 'Kubernetes (EKS)' : 'ECS Fargate' },
    { label: 'Max CPU', value: `${infra.max_cpu} vCPU`, mono: true },
    { label: 'Max memory', value: `${infra.max_memory} GB`, mono: true },
  ];

  return (
    <div className="reveal space-y-6">
      <button onClick={() => router.back()} className="flex items-center gap-1.5 rounded-md text-xs text-muted-foreground transition-colors hover:text-foreground">
        <ArrowLeft className="size-3.5" /> Back
      </button>

      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <h1 className="truncate text-xl font-semibold tracking-tight text-foreground">{infra.name}</h1>
            <StatusBadge status={infra.status} />
            {infra.is_mock && (
              <span className="rounded-md border border-border px-1.5 py-0.5 text-xs text-muted-foreground">Mock</span>
            )}
          </div>
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          {orgRole.allow('manage_infrastructure', isSuperAdmin) && (infra.status === 'ERROR' || infra.status === 'DESTROYED' || infra.status === 'PENDING') && (
            <Button variant="outline" size="sm" onClick={handleReprovision} disabled={reprovisioning}>
              <RefreshCw className={cn('size-3.5', reprovisioning && 'animate-spin')} /> Reprovision
            </Button>
          )}
          {isSuperAdmin && !orgRole.enabled && (
            <Button variant="outline" size="sm" onClick={() => setInviteOpen(true)}>
              <UserPlus className="size-3.5" /> Invite
            </Button>
          )}
          <Button variant="outline" size="sm" onClick={() => setSettingsOpen(true)}>
            <Settings className="size-3.5" /> Settings
          </Button>
        </div>
      </div>

      {infra.is_mock && (
        <Banner>
          This is a mock infrastructure for development. No real AWS resources exist, and the load balancer link goes nowhere.
        </Banner>
      )}

      {infra.status === 'PENDING' && !infra.is_cloud_authenticated && (
        <Banner>
          <div className="flex items-start gap-3">
            <Dot tone="brand" pulse />
            <div className="space-y-2.5">
              <p>
                Waiting for the setup script to run in AWS account <span className="font-mono">{infra.code}</span>.
                Provisioning starts on its own once it finishes.
              </p>
              {isOwner && (
                <div className="flex flex-wrap items-center gap-2">
                  <Button variant="outline" size="sm" onClick={handleShowSetupCommand} disabled={reissuingToken}>
                    <Terminal className="size-3.5" /> {reissuingToken ? 'Preparing…' : 'Show setup command'}
                  </Button>
                  {isCfnOnboardingEnabled() && !infra.is_mock && (
                    <Button variant="outline" size="sm" onClick={handleCfnVerify} disabled={verifyingCfn}>
                      <CloudCog className="size-3.5" /> {verifyingCfn ? 'Verifying…' : 'Verify connection'}
                    </Button>
                  )}
                </div>
              )}
            </div>
          </div>
        </Banner>
      )}
      {infra.status === 'PENDING' && infra.is_cloud_authenticated && (
        <Banner>
          <div className="flex items-start gap-3">
            <Dot tone="brand" pulse />
            <p>Provisioning is queued and will start shortly.</p>
          </div>
        </Banner>
      )}
      {infra.status === 'PROVISIONING' && (
        <Banner>
          <div className="flex items-start gap-3">
            <Dot tone="brand" pulse />
            <p>
              Terraform is building your AWS infrastructure.
              {!infra.is_mock && ' This usually takes 8 to 15 minutes. The provisioning logs below show live progress.'}
            </p>
          </div>
        </Banner>
      )}
      {infra.status === 'ERROR' && (
        <Banner tone="error">
          <p>Provisioning failed. Use <span className="font-medium">Reprovision</span> to retry.</p>
          {provisioningError && (
            <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap break-words font-mono text-xs text-muted-foreground">{provisioningError}</pre>
          )}
        </Banner>
      )}
      {infra.status === 'DESTROYING' && (
        <Banner tone="warning">
          <div className="flex items-start gap-3">
            <Dot tone="warning" pulse />
            <p>Terraform is destroying the AWS resources for this infrastructure.</p>
          </div>
        </Banner>
      )}

      <dl className="grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-border bg-border max-md:[&>div:last-child:nth-child(odd)]:col-span-2 md:grid-cols-5">
        {details.map(({ label, value, mono }) => (
          <div key={label} className="bg-card px-4 py-3">
            <dt className="text-xs text-muted-foreground">{label}</dt>
            <dd className={cn('mt-1 truncate text-sm text-foreground', mono && 'font-mono')}>{value}</dd>
          </div>
        ))}
      </dl>

      {infra.environment?.alb_dns && (
        <div className="flex items-center justify-between gap-3 rounded-lg border border-border bg-surface-1 px-4 py-3">
          <span className="text-xs text-muted-foreground">Load balancer</span>
          {infra.is_mock ? (
            <span className="flex cursor-not-allowed select-none items-center gap-2 break-all font-mono text-xs text-muted-foreground">
              {infra.environment.alb_dns} <span className="text-xs">(placeholder)</span>
            </span>
          ) : (
            <a href={`http://${infra.environment.alb_dns}`} target="_blank" rel="noopener noreferrer"
              className="flex items-center gap-1.5 break-all rounded-sm font-mono text-xs text-brand transition-colors hover:underline">
              {infra.environment.alb_dns} <ExternalLink className="size-3 shrink-0" />
            </a>
          )}
        </div>
      )}

      <section className="space-y-3" aria-labelledby="apps-title">
        <div className="flex items-center justify-between">
          <h2 id="apps-title" className="text-sm font-medium text-foreground">
            Applications <span className="font-normal text-muted-foreground">{apps.length}</span>
          </h2>
          {infra.status === 'ACTIVE' && canDeploy && apps.length > 0 && (
            <Button size="sm" onClick={() => router.push(`/dashboard/applications/new?infra=${id}`)}>
              <Plus className="size-3.5" /> Deploy
            </Button>
          )}
        </div>
        {apps.length === 0 ? (
          <EmptyState
            icon={AppWindow}
            title="No applications yet"
            description={
              infra.status === 'ACTIVE'
                ? canDeploy
                  ? 'Connect a GitHub repository and Launchpad builds and deploys it here.'
                  : 'Applications deployed here will show up in this list.'
                : 'You can deploy once provisioning has finished.'
            }
            action={
              infra.status === 'ACTIVE' && canDeploy ? (
                <Button size="sm" onClick={() => router.push(`/dashboard/applications/new?infra=${id}`)}>
                  <Plus className="size-3.5" /> Deploy an application
                </Button>
              ) : undefined
            }
          />
        ) : (
          <ul className="divide-y divide-border overflow-hidden rounded-lg border border-border bg-card">
            {apps.map((app) => (
              <li key={app.id}>
                <Link
                  href={`/dashboard/applications/${app.id}`}
                  className="flex items-center gap-4 px-4 py-3 outline-none transition-colors hover:bg-muted/40 focus-visible:bg-muted/40 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring/60"
                >
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium text-foreground">{app.name}</p>
                    <p className="mt-0.5 font-mono text-xs text-muted-foreground">
                      {app.cpu} vCPU · {app.memory} GB · :{app.port}
                    </p>
                  </div>
                  <StatusBadge status={app.status} />
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>

      <DatabasesSection infraId={id} environmentActive={infra.status === 'ACTIVE'} canManage={canManageDatabases} />

      {canSeeCosts && <CostsPanel infraId={id} connected={infra.is_cloud_authenticated} />}

      {isOwner && <ProvisioningLogsPanel infraId={id} status={infra.status} connected={infra.is_cloud_authenticated} />}

      <Sheet open={settingsOpen} onOpenChange={setSettingsOpen}>
        <SheetContent className="w-full overflow-y-auto sm:w-[480px] sm:max-w-[640px]">
          <SheetHeader className="mb-2">
            <SheetTitle className="text-base font-semibold">Settings</SheetTitle>
          </SheetHeader>

          <div className="space-y-6 px-4 pb-6">
            <SettingsGroup title="Details">
              <div className="divide-y divide-border rounded-lg border border-border bg-surface-1">
                {[
                  { label: 'ID', value: infra.id, mono: true },
                  { label: 'Provider', value: infra.cloud_provider },
                  { label: 'Region', value: infra.metadata?.aws_region ?? 'Not set', mono: true },
                  { label: 'Status', value: infra.status },
                  { label: 'Max CPU', value: `${infra.max_cpu} vCPU` },
                  { label: 'Max memory', value: `${infra.max_memory} GB` },
                  { label: 'Cloud access', value: infra.is_cloud_authenticated ? 'Connected' : 'Not connected' },
                  { label: 'Created', value: new Date(infra.created_at).toLocaleDateString() },
                ].map(({ label, value, mono }) => (
                  <div key={label} className="flex items-center justify-between gap-4 px-4 py-2.5">
                    <span className="text-xs text-muted-foreground">{label}</span>
                    <span className={cn('max-w-[240px] truncate text-xs text-foreground', mono && 'font-mono')}>{value}</span>
                  </div>
                ))}
                <div className="flex items-center justify-between gap-4 px-4 py-2.5">
                  <span className="text-xs text-muted-foreground">Name</span>
                  {isOwner && editingName ? (
                    <div className="flex items-center gap-2">
                      <Input value={infraName} onChange={(e) => setInfraName(e.target.value)}
                        aria-label="Infrastructure name"
                        onKeyDown={(e) => { if (e.key === 'Enter') handleRenameInfra(); if (e.key === 'Escape') setEditingName(false); }}
                        className="h-7 w-36 text-xs" autoFocus />
                      <button onClick={handleRenameInfra} disabled={savingName}
                        className="rounded-sm text-xs font-medium text-brand transition-colors hover:underline disabled:opacity-40">
                        {savingName ? 'Saving…' : 'Save'}
                      </button>
                    </div>
                  ) : (
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-foreground">{infra.name}</span>
                      {isOwner && (
                        <button aria-label="Rename" onClick={() => { setInfraName(infra.name); setEditingName(true); }}
                          className="rounded-sm text-muted-foreground transition-colors hover:text-foreground">
                          <Pencil className="size-3" />
                        </button>
                      )}
                    </div>
                  )}
                </div>
              </div>
            </SettingsGroup>

            {isOwner && infra.is_cloud_authenticated && (() => {
              // Branch between the script and CFN refresh flows by how this account was
              // onboarded. `onboarding_method` is undefined until the backend serializer
              // exposes it (see Infrastructure['onboarding_method']'s doc comment) — treat
              // that the same as the model's own 'script' default.
              const isCfnManaged = isCfnOnboardingEnabled() && infra.onboarding_method === 'cloudformation';
              return (
                <SettingsGroup title="AWS permissions">
                  <SettingsRow
                    highlight={policyState.kind === 'stale'}
                    title={policyState.title}
                    description={policyState.detail}
                    action={
                      <Button
                        variant={policyState.kind === 'stale' ? 'default' : 'outline'}
                        size="sm"
                        onClick={() => (isCfnManaged ? setCfnRefreshOpen(true) : setRefreshPolicyOpen(true))}
                      >
                        <ShieldCheck className="size-3.5" /> Refresh
                      </Button>
                    }
                  />
                </SettingsGroup>
              );
            })()}

            {isOwner && (
              <SettingsGroup title="Compliance">
                <SettingsRow
                  title="Evidence pack"
                  description="What Launchpad can do in your account, what it actually has, and where they differ."
                  action={
                    <Button variant="outline" size="sm" onClick={handleDownloadEvidencePack} disabled={downloadingEvidence}>
                      <Download className="size-3.5" /> {downloadingEvidence ? 'Preparing…' : 'Download'}
                    </Button>
                  }
                />
              </SettingsGroup>
            )}

            {isOwner && !infra.exited_at && (
              <SettingsGroup title="Continuity">
                <SettingsRow
                  title="Exit export"
                  description="A read-only handover archive: inventory, revocation steps, Terraform and CI seed."
                  action={
                    <Button variant="outline" size="sm" onClick={() => setExitExportOpen(true)}>
                      <Download className="size-3.5" /> Export
                    </Button>
                  }
                />
              </SettingsGroup>
            )}

            {infra.exited_at && (
              <SettingsGroup title="Continuity">
                <SettingsRow
                  title="Exited"
                  description={<>This infrastructure exited on {new Date(infra.exited_at).toLocaleString()}. Platform hostnames no longer resolve.</>}
                  action={null}
                />
              </SettingsGroup>
            )}

            <SettingsGroup title={`Users (${infra.invited_users?.length ?? 0})`}>
              {!infra.invited_users || infra.invited_users.length === 0 ? (
                <p className="px-1 text-xs text-muted-foreground">Nobody has been invited yet.</p>
              ) : (
                <ul className="divide-y divide-border rounded-lg border border-border bg-surface-1">
                  {infra.invited_users.map((u) => (
                    <li key={u.id} className="flex items-center justify-between gap-3 px-4 py-2.5">
                      <div className="flex min-w-0 items-center gap-3">
                        <span className="flex size-7 shrink-0 items-center justify-center rounded-full border border-border bg-surface-3">
                          <User className="size-3 text-muted-foreground" />
                        </span>
                        <div className="min-w-0">
                          <p className="truncate text-sm font-medium text-foreground">{u.user_name}</p>
                          <p className="truncate text-xs text-muted-foreground">{u.email}</p>
                        </div>
                      </div>
                      <div className="flex shrink-0 items-center gap-3">
                        <span className={cn('text-xs', ROLE_COLORS[u.role] ?? 'text-muted-foreground')}>{u.role}</span>
                        {isOwner && (
                          <button
                            aria-label={`Remove ${u.user_name}`}
                            onClick={() => handleRemoveUser(u)}
                            disabled={removingUserId === u.id}
                            className="rounded-sm text-muted-foreground transition-colors hover:text-destructive disabled:opacity-40"
                          >
                            <Trash2 className="size-3.5" />
                          </button>
                        )}
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </SettingsGroup>

            {isOwner && (
              <SettingsGroup title="Danger zone" tone="danger">
                {!infra.exited_at && (
                  <SettingsRow
                    tone="danger"
                    title="Complete exit"
                    description="Removes the platform DNS records for this infrastructure. Your AWS resources are untouched."
                    action={
                      <Button variant="outline" size="sm" onClick={() => setCompleteExitOpen(true)}
                        className="border-destructive/40 text-destructive hover:bg-destructive/10 hover:text-destructive">
                        Complete exit
                      </Button>
                    }
                  />
                )}
                <SettingsRow
                  tone="danger"
                  title="Delete infrastructure"
                  description="Tears down all AWS resources Launchpad created. This can’t be undone."
                  action={
                    <Button variant="outline" size="sm" onClick={() => setDeleteOpen(true)}
                      className="border-destructive/40 text-destructive hover:bg-destructive/10 hover:text-destructive">
                      <Trash2 className="size-3.5" /> Delete
                    </Button>
                  }
                />
                <SettingsRow
                  tone="danger"
                  title="Nuke infrastructure"
                  description="Destroys everything: apps, databases (no snapshot), logs and all AWS resources. It does not refuse when apps or databases exist."
                  action={
                    <Button variant="outline" size="sm" onClick={() => setNukeOpen(true)}
                      className="border-destructive/40 text-destructive hover:bg-destructive/10 hover:text-destructive">
                      <Flame className="size-3.5" /> Nuke
                    </Button>
                  }
                />
              </SettingsGroup>
            )}
          </div>
        </SheetContent>
      </Sheet>

      <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle className="text-base font-semibold">Delete infrastructure</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            Delete <span className="font-mono text-foreground">{infra.name}</span>? Terraform will destroy every AWS resource it created in your account. This can’t be undone.
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

      <NukeInfrastructureDialog
        open={nukeOpen}
        onOpenChange={setNukeOpen}
        infraId={id}
        infraName={infra.name}
        onNuked={handleNuked}
      />

      <Dialog open={!!setupCommand} onOpenChange={(o) => { if (!o) { setSetupCommand(null); setSetupCommandCopied(false); } }}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle className="text-base font-semibold">Setup command</DialogTitle>
          </DialogHeader>
          {setupCommand?.misconfig ? (
            <p className="text-xs text-destructive">{setupCommand.misconfig}</p>
          ) : (
            <div className="space-y-3">
              <Banner tone="warning">
                <p className="text-xs">
                  This creates a new one-time token, and any earlier token for this infrastructure stops working. Run the command in a shell with AWS credentials for account <span className="font-mono">{infra.code}</span>.
                </p>
              </Banner>
              <div className="relative">
                <pre className="overflow-x-auto whitespace-pre rounded-md border border-border bg-surface-1 px-3 py-2.5 pr-9 font-mono text-xs text-foreground">{setupCommand?.invocation}</pre>
                <button
                  onClick={() => {
                    navigator.clipboard.writeText(setupCommand?.invocation ?? '').then(
                      () => {
                        setSetupCommandCopied(true);
                        setTimeout(() => setSetupCommandCopied(false), 2000);
                      },
                      () => toast.error('Copy failed. Copy it by hand.'),
                    );
                  }}
                  aria-label="Copy command"
                  className="absolute right-2 top-2 rounded-sm text-muted-foreground transition-colors hover:text-foreground"
                >
                  {setupCommandCopied ? <Check className="size-3.5 text-success" /> : <Copy className="size-3.5" />}
                </button>
              </div>
            </div>
          )}
          <div className="flex gap-2 justify-end mt-2">
            <Button variant="outline" onClick={() => { setSetupCommand(null); setSetupCommandCopied(false); }}>Done</Button>
          </div>
        </DialogContent>
      </Dialog>

      <Dialog open={exitExportOpen} onOpenChange={setExitExportOpen}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle className="text-base font-semibold">Download exit export</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            Downloads a handover archive for <span className="font-mono text-foreground">{infra.name}</span>: inventory and ARNs,
            revocation instructions, a Terraform bundle, redacted task definitions or Kubernetes manifests, the CI buildspec,
            and a list of GitHub webhooks to remove. Every environment value is redacted, and nothing is changed in your AWS account.
          </p>
          <div className="flex gap-2 justify-end mt-2">
            <Button variant="outline" onClick={() => setExitExportOpen(false)}>
              Cancel
            </Button>
            <Button onClick={handleDownloadExitExport} disabled={downloadingExitExport}>
              {downloadingExitExport ? 'Preparing…' : 'Download'}
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      <Dialog open={completeExitOpen} onOpenChange={setCompleteExitOpen}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle className="text-base font-semibold">Complete exit</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            This removes the platform DNS records for <span className="font-mono text-foreground">{infra.name}</span> and marks it exited.
            Your AWS resources keep running; only Launchpad&apos;s own DNS zone changes, so platform hostnames stop resolving.
            This can’t be undone. Download the exit export first if you haven’t already.
          </p>
          <div className="flex gap-2 justify-end mt-2">
            <Button variant="outline" onClick={() => setCompleteExitOpen(false)}>
              Cancel
            </Button>
            <Button variant="destructive" onClick={handleCompleteExit} disabled={completingExit}>
              {completingExit ? 'Completing…' : 'Complete exit'}
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      <Dialog open={inviteOpen} onOpenChange={(o) => { if (!o) closeInviteDialog(); else setInviteOpen(true); }}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle className="text-base font-semibold">{inviteUrl ? 'Invitation ready' : 'Invite user'}</DialogTitle>
          </DialogHeader>
          {inviteUrl ? (
            <div className="space-y-4">
              <p className="text-xs text-muted-foreground">
                Share this link with <span className="text-foreground font-mono">{inviteForm.email}</span>.
              </p>
              <div className="flex items-center gap-3 rounded-md border border-border bg-surface-1 p-3">
                <p className="flex-1 break-all font-mono text-xs text-muted-foreground">{inviteUrl}</p>
                <button aria-label="Copy link" onClick={copyUrl} className="shrink-0 rounded-sm text-muted-foreground transition-colors hover:text-foreground">
                  {copied ? <Check className="size-4 text-success" /> : <Copy className="size-4" />}
                </button>
              </div>
              <Button onClick={closeInviteDialog} variant="outline" className="w-full">Done</Button>
            </div>
          ) : (
            <form onSubmit={handleInvite} className="space-y-3">
              {[
                { label: 'Email', type: 'email', placeholder: 'user@example.com', key: 'email' },
                { label: 'Username', type: 'text', placeholder: 'johndoe', key: 'user_name' },
                { label: 'Temporary password', type: 'password', placeholder: 'At least 6 characters', key: 'password' },
              ].map(({ label, type, placeholder, key }) => (
                <div key={key} className="space-y-1.5">
                  <Label htmlFor={`invite-${key}`}>{label}</Label>
                  <Input id={`invite-${key}`} type={type} placeholder={placeholder}
                    value={inviteForm[key as keyof typeof inviteForm]}
                    onChange={(e) => setInviteForm({ ...inviteForm, [key]: e.target.value })}
                    required minLength={key === 'password' ? 6 : undefined}
                    />
                </div>
              ))}
              <div className="space-y-1.5">
                <Label htmlFor="invite-role">Role</Label>
                <Select value={inviteForm.role} onValueChange={(v) => setInviteForm({ ...inviteForm, role: v as 'admin' | 'user' | 'guest' })}>
                  <SelectTrigger id="invite-role" className="w-full"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="admin">Admin: can deploy apps</SelectItem>
                    <SelectItem value="user">User: view only</SelectItem>
                    <SelectItem value="guest">Guest: limited access</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <Button type="submit" disabled={inviting} className="w-full mt-1">
                {inviting ? 'Inviting…' : 'Send invite'}
              </Button>
            </form>
          )}
        </DialogContent>
      </Dialog>

      <PolicyRefreshDialog open={refreshPolicyOpen} onOpenChange={setRefreshPolicyOpen} infraId={id} />
      <CfnRefreshDialog open={cfnRefreshOpen} onOpenChange={setCfnRefreshOpen} infraId={id} />
    </div>
  );
}
