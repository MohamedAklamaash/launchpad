'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import { useRouter } from 'next/navigation';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { PageHeader } from '@/components/layout/page-header';
import { cn } from '@/lib/utils';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import { ArrowLeft, ArrowRight, Copy, Check, Terminal, Search, ChevronDown, ShieldAlert, CloudCog, ExternalLink, Loader2 } from 'lucide-react';
import { LaunchSequence } from '@/components/launch';
import { infrastructureApi, AwsRegion } from '@/lib/api/infrastructures';
import { ComputeType, Infrastructure, InfrastructureCreateResponse } from '@/types/infrastructure';
import { resolveOnboardingScript, getOnboardingMisconfiguration, buildBootstrapEnvExports } from '@/lib/onboarding-scripts';
import {
  isCfnOnboardingEnabled,
  getCfnOnboardingMisconfiguration,
  buildQuickCreateLink,
  buildStackInfoUrl,
  resolveCfnTemplateUrl,
  widestComputeType,
  CFN_ROLE_NAME,
  QuickCreateLink,
} from '@/lib/onboarding-cloudformation';
import { toast } from 'sonner';

const API_GATEWAY_URL = process.env.NEXT_PUBLIC_API_GATEWAY_URL || 'http://localhost:8000';

const STEPS = [
  { title: 'Account', blurb: 'Name this environment and enter the 12-digit ID of the AWS account it will live in.' },
  { title: 'Region', blurb: 'Pick the region Launchpad deploys into.' },
  { title: 'Compute', blurb: 'Choose ECS Fargate or Kubernetes, then set CPU and memory ceilings. The compute target can’t be changed later.' },
] as const;

const COMPUTE_OPTIONS: { value: ComputeType; label: string; blurb: string }[] = [
  { value: 'ecs_fargate', label: 'ECS Fargate', blurb: 'Serverless containers on ECS' },
  { value: 'eks', label: 'Kubernetes', blurb: 'EKS Auto Mode cluster' },
];

export default function NewInfrastructurePage() {
  const router = useRouter();
  const [loading, setLoading] = useState(false);
  const [copiedKey, setCopiedKey] = useState<string | null>(null);
  const [regions, setRegions] = useState<AwsRegion[]>([]);
  // Fails closed: EKS stays unavailable until the server says it is enabled, so a failed
  // capabilities call can never surface a target that create would reject with a 400.
  const [enabledComputeTypes, setEnabledComputeTypes] = useState<ComputeType[]>(['ecs_fargate']);
  const [regionSearch, setRegionSearch] = useState('');
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const [createdInfra, setCreatedInfra] = useState<InfrastructureCreateResponse | null>(null);
  const [step, setStep] = useState(0);

  const [formData, setFormData] = useState({
    name: '',
    cloud_provider: 'aws' as const,
    max_cpu: 4,
    max_memory: 8,
    code: '',
    compute_type: 'ecs_fargate' as ComputeType,
    aws_region: 'us-east-1',
  });

  useEffect(() => {
    infrastructureApi.listRegions().then(setRegions).catch(() => { });
    infrastructureApi
      .listCapabilities()
      .then((caps) =>
        setEnabledComputeTypes(caps.compute_types.filter((c) => c.enabled).map((c) => c.value)),
      )
      .catch(() => { });
  }, []);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setDropdownOpen(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  const set = (k: string, v: string | number) => setFormData((p) => ({ ...p, [k]: v }));

  const checks = {
    name: formData.name.trim().length > 0,
    code: formData.code.trim().length === 12,
    compute: enabledComputeTypes.includes(formData.compute_type),
    cpu: formData.max_cpu > 0,
    memory: formData.max_memory > 0,
  };
  const stepValid = [checks.name && checks.code, true, checks.compute && checks.cpu && checks.memory];
  const firstInvalid = stepValid.findIndex((v) => !v);
  const gate = firstInvalid === -1 ? STEPS.length - 1 : firstInvalid;
  const allReady = Object.values(checks).every(Boolean);
  const isLast = step === STEPS.length - 1;

  useEffect(() => {
    if (step > gate) setStep(gate);
  }, [gate, step]);

  const go = (i: number) => setStep(i);
  const next = () => { if (!isLast && stepValid[step]) go(step + 1); };
  const back = () => { if (step > 0) go(step - 1); else router.back(); };

  const filteredRegions = regions.filter(
    (r) =>
      r.label.toLowerCase().includes(regionSearch.toLowerCase()) ||
      r.value.toLowerCase().includes(regionSearch.toLowerCase())
  );

  const selectedRegionLabel = regions.find((r) => r.value === formData.aws_region)?.label ?? formData.aws_region;

  const create = async () => {
    setLoading(true);
    try {
      const { aws_region, ...rest } = formData;
      const infra = await infrastructureApi.create({ ...rest, metadata: { aws_region } });
      setCreatedInfra(infra);
      toast.success('AWS account added. Run the script to finish onboarding.');
    } catch (err: unknown) {
      const error = err as { response?: { data?: { error?: string } } };
      toast.error(error.response?.data?.error || 'Failed to create infrastructure');
    } finally {
      setLoading(false);
    }
  };

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (isLast) create();
    else next();
  };

  const bootstrapEnv = createdInfra
    ? buildBootstrapEnvExports(
      createdInfra,
      createdInfra.onboarding_token,
      `${API_GATEWAY_URL}/api/infrastructures/onboarding/callback`,
    )
    : [];

  const onboardingMisconfig = createdInfra ? getOnboardingMisconfiguration() : null;
  let bootstrapScript: ReturnType<typeof resolveOnboardingScript> | null = null;
  if (createdInfra && !onboardingMisconfig) {
    try {
      bootstrapScript = resolveOnboardingScript('bootstrap', bootstrapEnv);
    } catch {
      bootstrapScript = null;
    }
  }

  // CFN onboarding is unavailable for mock (dev-mode) infras — the backend's
  // CfnOnboardingMockUnsupported 400 matches this. Hide the whole tab rather than let
  // a customer pick it and hit an error immediately.
  const cfnUiEnabled = isCfnOnboardingEnabled() && !!createdInfra && !createdInfra.is_mock;

  const [accountSiblings, setAccountSiblings] = useState<Infrastructure[] | null>(null);
  const [siblingsLoading, setSiblingsLoading] = useState(false);
  const [siblingsError, setSiblingsError] = useState(false);

  // Which AWS account states this target account is in: fresh (no authenticated infra
  // yet — this is the only lookup that can see), already has a CFN-managed sibling
  // (second infra joining an existing stack, plan §5's customer-driven copy/paste flow),
  // or already has a script-managed sibling (CFN tab disabled — plan §2's exclusivity
  // rule). Scoped to infras this user can see via `list()`; a sibling owned by a
  // different user sharing the same AWS account is invisible to this check — see the
  // PR description for why that's a real gap, not a corner we cut for convenience.
  const loadSiblings = useCallback(async () => {
    if (!createdInfra) return;
    setSiblingsLoading(true);
    setSiblingsError(false);
    try {
      const all = await infrastructureApi.list();
      setAccountSiblings(
        all.filter((i) => i.id !== createdInfra.id && i.code === createdInfra.code && i.is_cloud_authenticated),
      );
    } catch {
      setSiblingsError(true);
    } finally {
      setSiblingsLoading(false);
    }
  }, [createdInfra]);

  useEffect(() => {
    if (cfnUiEnabled) loadSiblings();
  }, [cfnUiEnabled, loadSiblings]);

  // `onboarding_method` is undefined until the backend serializer exposes it (see
  // Infrastructure['onboarding_method']'s doc comment) — every sibling falls back to
  // 'script', the model's own default, which is also the conservative read: without
  // that field no sibling can ever actually be recognized as CFN-managed yet.
  const cfnSiblings = (accountSiblings ?? []).filter((s) => s.onboarding_method === 'cloudformation');
  const scriptSiblings = (accountSiblings ?? []).filter((s) => (s.onboarding_method ?? 'script') === 'script');
  const cfnAccountState: 'fresh' | 'cfn_sibling' | 'script_sibling' =
    cfnSiblings.length > 0 ? 'cfn_sibling' : scriptSiblings.length > 0 ? 'script_sibling' : 'fresh';
  // `accountSiblings` is null before the list() call resolves — until then
  // `cfnAccountState` defaults to 'fresh', which must never be treated as "confirmed
  // fresh" (it would briefly show a quick-create link for what turns out to be a
  // script-managed account). The tab stays disabled until this is true.
  const siblingsResolved = accountSiblings !== null || siblingsError;

  // Never just this infra's own compute_type — run every sibling (if any) through the
  // same widest-compute-type computation the backend uses, so a narrower account never
  // gets recommended a template that would downgrade a wider sibling. See plan §5a.
  const recommendedComputeType: ComputeType = createdInfra
    ? widestComputeType([createdInfra.compute_type, ...cfnSiblings.map((s) => s.compute_type)])
    : 'ecs_fargate';
  const mergedExternalIds = createdInfra ? [createdInfra.id, ...cfnSiblings.map((s) => s.id)] : [];
  const cfnStackName = cfnSiblings.find((s) => s.cfn_stack_name)?.cfn_stack_name ?? null;

  const cfnMisconfig = getCfnOnboardingMisconfiguration();
  const quickCreate =
    createdInfra && !cfnMisconfig
      ? buildQuickCreateLink({
        computeType: recommendedComputeType,
        externalIds: mergedExternalIds,
        platformAccountId: createdInfra.platform_account_id,
        platformUser: createdInfra.platform_user,
      })
      : null;

  const copyResetRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const copy = (text: string, key: string) => {
    if (!text) return;
    navigator.clipboard.writeText(text).then(
      () => {
        if (copyResetRef.current) clearTimeout(copyResetRef.current);
        setCopiedKey(key);
        copyResetRef.current = setTimeout(() => {
          setCopiedKey(null);
          copyResetRef.current = null;
        }, 1500);
      },
      () => toast.error('Copy failed. Copy it by hand.'),
    );
  };

  if (createdInfra) {
    return (
      <div className="reveal mx-auto w-full max-w-2xl space-y-6">
        <button onClick={() => router.back()} className="flex items-center gap-1.5 rounded-md text-xs text-muted-foreground transition-colors hover:text-foreground">
          <ArrowLeft className="size-3.5" /> Back
        </button>

        <LaunchSequence current="connect" />

        <PageHeader
          title="Connect your AWS account"
          description={
            cfnUiEnabled ? (
              <>Finish onboarding account <span className="font-mono text-foreground">{createdInfra.code}</span> with the shell script or a CloudFormation stack. Provisioning starts on its own once either one finishes.</>
            ) : (
              <>Run this command in a shell with AWS credentials for account <span className="font-mono text-foreground">{createdInfra.code}</span>. Provisioning starts on its own once the script finishes.</>
            )
          }
        />

          {cfnUiEnabled ? (
            <Tabs defaultValue="script" className="w-full">
              <TabsList className="w-full">
                <TabsTrigger value="script" className="flex-1">Script</TabsTrigger>
                <span title={!siblingsResolved ? 'Checking this AWS account’s onboarding status…' : cfnAccountState === 'script_sibling' ? 'This AWS account already uses the shell-script onboarding method, use the Script tab instead.' : undefined} className="flex-1">
                  <TabsTrigger value="cloudformation" disabled={!siblingsResolved || cfnAccountState === 'script_sibling'} className="w-full">
                    CloudFormation
                  </TabsTrigger>
                </span>
              </TabsList>

              {siblingsResolved && cfnAccountState === 'script_sibling' && (
                <p className="px-1 pt-2 text-xs text-muted-foreground">
                  This AWS account already uses the shell-script onboarding method, an account picks one method
                  for life. Use the Script tab above instead.
                </p>
              )}

              <TabsContent value="script" className="space-y-4 pt-4">
                <ScriptOnboardingPanel
                  createdInfra={createdInfra}
                  onboardingMisconfig={onboardingMisconfig}
                  bootstrapScript={bootstrapScript}
                  copiedKey={copiedKey}
                  copy={copy}
                />
              </TabsContent>

              <TabsContent value="cloudformation" className="space-y-4 pt-4">
                <CloudFormationOnboardingPanel
                  createdInfra={createdInfra}
                  accountState={cfnAccountState}
                  siblingsLoading={siblingsLoading || !siblingsResolved}
                  siblingsError={siblingsError}
                  onRetry={loadSiblings}
                  misconfig={cfnMisconfig}
                  quickCreate={quickCreate}
                  recommendedComputeType={recommendedComputeType}
                  mergedExternalIds={mergedExternalIds}
                  cfnStackName={cfnStackName}
                  copiedKey={copiedKey}
                  copy={copy}
                />
              </TabsContent>
            </Tabs>
          ) : (
            <ScriptOnboardingPanel
              createdInfra={createdInfra}
              onboardingMisconfig={onboardingMisconfig}
              bootstrapScript={bootstrapScript}
              copiedKey={copiedKey}
              copy={copy}
            />
          )}

          <div className="flex gap-2 pt-1">
            <Button size="lg" onClick={() => router.push(`/dashboard/infrastructures/${createdInfra.id}`)}>
              Track provisioning
              <ArrowRight className="size-4" />
            </Button>
          </div>
      </div>
    );
  }

  return (
    <div className="reveal mx-auto w-full max-w-lg space-y-6">
      <button onClick={() => router.back()} className="flex items-center gap-1.5 rounded-md text-xs text-muted-foreground transition-colors hover:text-foreground">
        <ArrowLeft className="size-3.5" /> Back
      </button>

      <PageHeader
        title="Add an AWS account"
        description="Launchpad provisions a VPC, load balancer, registry and cluster inside it."
      />

      <Stepper current={step} gate={gate} onSelect={go} />

      <form onSubmit={onSubmit} className="space-y-6">
        <div key={step} className="reveal space-y-4 rounded-lg border border-border bg-card p-5">
          <div>
            <h2 className="text-sm font-medium text-foreground">{STEPS[step].title}</h2>
            <p className="mt-0.5 text-xs text-muted-foreground">{STEPS[step].blurb}</p>
          </div>

          {step === 0 && (
            <div className="space-y-4">
              <Field id="infra-name" label="Name" hint="For example production or staging">
                <Input id="infra-name" value={formData.name} onChange={(e) => set('name', e.target.value)}
                  placeholder="production" autoFocus />
              </Field>
              <Field id="infra-code" label="AWS account ID" hint="12 digits">
                <Input id="infra-code" value={formData.code} onChange={(e) => set('code', e.target.value)}
                  placeholder="123456789012" maxLength={12} inputMode="numeric" className="font-mono" />
              </Field>
            </div>
          )}

          {step === 1 && (
            <Field id="infra-region" label="Region">
              <div className="relative" ref={dropdownRef}>
                <button
                  id="infra-region"
                  type="button"
                  aria-haspopup="listbox"
                  aria-expanded={dropdownOpen}
                  onClick={() => { setDropdownOpen((o) => !o); setRegionSearch(''); }}
                  className="flex h-8 w-full items-center justify-between rounded-md border border-input px-2.5 text-sm text-foreground outline-none transition-colors focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/40"
                >
                  <span className="font-mono text-sm">{selectedRegionLabel}</span>
                  <ChevronDown className={cn('size-3.5 text-muted-foreground transition-transform', dropdownOpen && 'rotate-180')} />
                </button>

                {dropdownOpen && (
                  <div className="absolute left-0 right-0 top-full z-50 mt-1.5 overflow-hidden rounded-md bg-popover shadow-popover">
                    <div className="flex items-center gap-2 border-b border-border px-3 py-2">
                      <Search className="size-3.5 shrink-0 text-muted-foreground" />
                      <input
                        autoFocus
                        aria-label="Search regions"
                        value={regionSearch}
                        onChange={(e) => setRegionSearch(e.target.value)}
                        placeholder="Search regions"
                        className="flex-1 bg-transparent text-sm text-foreground outline-none placeholder:text-muted-foreground"
                      />
                    </div>
                    <ul role="listbox" aria-label="Regions" className="max-h-52 overflow-y-auto p-1">
                      {filteredRegions.length === 0 ? (
                        <li className="px-3 py-2 text-xs text-muted-foreground">No regions found</li>
                      ) : (
                        filteredRegions.map((r) => (
                          <li key={r.value} role="option" aria-selected={formData.aws_region === r.value}>
                            <button
                              type="button"
                              onClick={() => { set('aws_region', r.value); setDropdownOpen(false); }}
                              className={cn(
                                'flex w-full items-center justify-between rounded-sm px-2 py-1.5 text-left transition-colors hover:bg-accent focus-visible:bg-accent focus-visible:outline-none',
                                formData.aws_region === r.value ? 'text-brand' : 'text-foreground',
                              )}
                            >
                              <span className="text-sm">{r.label}</span>
                              <span className="font-mono text-xs text-muted-foreground">{r.value}</span>
                            </button>
                          </li>
                        ))
                      )}
                    </ul>
                  </div>
                )}
              </div>
            </Field>
          )}

          {step === 2 && (
            <div className="space-y-4">
              <div role="group" aria-labelledby="compute-label" className="space-y-1.5">
                <div className="flex items-baseline justify-between">
                  <span id="compute-label" className="text-xs font-medium text-foreground/90">Compute target</span>
                  <span className="text-xs text-muted-foreground">Can’t be changed later</span>
                </div>
                <div className="grid gap-2 sm:grid-cols-2">
                  {COMPUTE_OPTIONS.map((o) => {
                    const available = enabledComputeTypes.includes(o.value);
                    const selected = formData.compute_type === o.value;
                    return (
                      <button
                        key={o.value}
                        type="button"
                        disabled={!available}
                        aria-pressed={selected}
                        title={available ? undefined : `${o.label} is not enabled on this deployment`}
                        onClick={() => set('compute_type', o.value)}
                        className={cn(
                          'rounded-md border px-3 py-2.5 text-left outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring/60',
                          selected ? 'border-brand bg-brand-soft' : 'border-border hover:bg-muted/50',
                          !available && 'cursor-not-allowed opacity-50 hover:bg-transparent',
                        )}
                      >
                        <p className={cn('text-sm font-medium', selected ? 'text-brand' : 'text-foreground')}>{o.label}</p>
                        <p className="mt-0.5 text-xs text-muted-foreground">
                          {available ? o.blurb : 'Not enabled on this deployment'}
                        </p>
                      </button>
                    );
                  })}
                </div>
              </div>
              <div className="grid gap-4 sm:grid-cols-2">
                <Field id="infra-cpu" label="Max CPU" hint="vCPU">
                  <Input id="infra-cpu" type="number" step="0.25" min={0.25} value={Number.isNaN(formData.max_cpu) ? '' : formData.max_cpu}
                    onChange={(e) => set('max_cpu', e.target.value === '' ? NaN : parseFloat(e.target.value))}
                    className="font-mono" />
                </Field>
                <Field id="infra-mem" label="Max memory" hint="GB">
                  <Input id="infra-mem" type="number" step="0.5" min={0.5} value={Number.isNaN(formData.max_memory) ? '' : formData.max_memory}
                    onChange={(e) => set('max_memory', e.target.value === '' ? NaN : parseFloat(e.target.value))}
                    className="font-mono" />
                </Field>
              </div>
            </div>
          )}
        </div>

        <div className="flex items-center gap-3">
          <Button type="button" variant="outline" size="lg" onClick={back}>
            <ArrowLeft className="size-3.5" /> {step === 0 ? 'Cancel' : 'Back'}
          </Button>
          <span className="text-xs text-muted-foreground">Step {step + 1} of {STEPS.length}</span>
          <div className="ml-auto">
            {isLast ? (
              <Button type="submit" size="lg" disabled={loading || !allReady}>
                {loading ? 'Creating…' : 'Create account'}
              </Button>
            ) : (
              <Button type="submit" size="lg" disabled={!stepValid[step]}>
                Next <ArrowRight className="size-4" />
              </Button>
            )}
          </div>
        </div>
      </form>
    </div>
  );
}

function Stepper({ current, gate, onSelect }: { current: number; gate: number; onSelect: (i: number) => void }) {
  return (
    <ol className="flex items-center" aria-label="Steps">
      {STEPS.map((s, i) => {
        const state = i < current ? 'done' : i === current ? 'active' : 'todo';
        const reachable = i <= gate;
        return (
          <li key={s.title} aria-current={state === 'active' ? 'step' : undefined} className={cn('flex items-center', i < STEPS.length - 1 && 'flex-1')}>
            <button
              type="button"
              onClick={() => reachable && onSelect(i)}
              disabled={!reachable}
              className="flex items-center gap-2 rounded-md text-left outline-none focus-visible:ring-2 focus-visible:ring-ring/60 disabled:cursor-not-allowed"
            >
              <span
                className={cn(
                  'flex size-5 shrink-0 items-center justify-center rounded-full border text-[11px] font-medium tabular-nums',
                  state === 'done' && 'border-brand bg-brand text-background',
                  state === 'active' && 'border-brand text-brand',
                  state === 'todo' && 'border-border text-muted-foreground',
                )}
              >
                {state === 'done' ? <Check className="size-3" /> : i + 1}
              </span>
              <span className={cn('text-xs', state === 'todo' ? 'text-muted-foreground' : 'text-foreground')}>{s.title}</span>
            </button>
            {i < STEPS.length - 1 && <span className={cn('mx-3 h-px flex-1', i < current ? 'bg-brand' : 'bg-border')} />}
          </li>
        );
      })}
    </ol>
  );
}

function Field({ id, label, hint, children }: { id: string; label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-baseline justify-between">
        <Label htmlFor={id}>{label}</Label>
        {hint && <span className="text-xs text-muted-foreground">{hint}</span>}
      </div>
      {children}
    </div>
  );
}

// The existing (pre-F8) bootstrap-script content, unchanged, just extracted so it can
// render either standalone (flag off) or inside the Script tab (flag on).
function ScriptOnboardingPanel({ createdInfra, onboardingMisconfig, bootstrapScript, copiedKey, copy }: {
  createdInfra: InfrastructureCreateResponse;
  onboardingMisconfig: string | null;
  bootstrapScript: ReturnType<typeof resolveOnboardingScript> | null;
  copiedKey: string | null;
  copy: (text: string, key: string) => void;
}) {
  return (
    <>
      <div className="flex items-start gap-3 rounded-lg border border-warning/30 bg-warning/10 p-4">
        <ShieldAlert className="mt-0.5 size-4 shrink-0 text-warning" />
        <div className="space-y-0.5">
          <p className="text-sm font-medium text-foreground">This token is shown once</p>
          <p className="text-xs text-muted-foreground">
            Copy the command now. If you leave this page, you will have to delete this account and add it again to get a new token.
          </p>
        </div>
      </div>

      {onboardingMisconfig && (
        <div className="flex items-start gap-3 rounded-lg border border-warning/30 bg-warning/10 p-4">
          <ShieldAlert className="mt-0.5 size-4 shrink-0 text-warning" />
          <div className="space-y-1 flex-1">
            <p className="text-sm font-medium text-foreground">Onboarding is misconfigured</p>
            <p className="text-xs text-muted-foreground">{onboardingMisconfig}</p>
            <p className="text-xs text-muted-foreground">
              Your onboarding token and AWS account ID are still valid. Use them with a copy of the bootstrap script you fetch yourself.
            </p>
            <div className="mt-2 space-y-1.5">
              {[
                { k: 'Account ID', v: createdInfra.code, copyable: false, key: '' },
                { k: 'Infra ID', v: createdInfra.id, copyable: false, key: '' },
                { k: 'Onboarding token', v: createdInfra.onboarding_token, copyable: true, key: 'token' },
              ].map((row) => (
                <div key={row.k} className="flex items-center gap-2">
                  <span className="eyebrow w-28 shrink-0">{row.k}</span>
                  <code className="break-all font-mono text-xs text-foreground">{row.v}</code>
                  {row.copyable && (
                    <button onClick={() => copy(row.v, row.key)} className="shrink-0 rounded-sm text-muted-foreground transition-colors hover:text-foreground">
                      {copiedKey === row.key ? <Check className="size-3.5 text-success" /> : <Copy className="size-3.5" />}
                    </button>
                  )}
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {bootstrapScript && (
        <div className="space-y-3 rounded-lg border border-border bg-card p-4">
          <div className="flex items-start gap-3">
            <Terminal className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
            <div className="space-y-0.5 flex-1 min-w-0">
              <p className="text-sm font-medium text-foreground">{bootstrapScript.label}</p>
              <p className="text-xs text-muted-foreground">{bootstrapScript.description}</p>
            </div>
            <button aria-label="Copy command" onClick={() => copy(bootstrapScript!.invocation, 'bootstrap')} className="shrink-0 rounded-sm text-muted-foreground transition-colors hover:text-foreground">
              {copiedKey === 'bootstrap' ? <Check className="size-3.5 text-success" /> : <Copy className="size-3.5" />}
            </button>
          </div>
          <pre className="overflow-x-auto whitespace-pre rounded-md border border-border bg-surface-1 px-3 py-2.5 font-mono text-xs text-foreground">{bootstrapScript.invocation}</pre>
          {bootstrapScript.locationIsUrl ? (
            <a href={bootstrapScript.location} target="_blank" rel="noopener noreferrer" className="inline-block text-xs text-muted-foreground transition-colors hover:text-foreground">
              View script source
            </a>
          ) : (
            <p className="font-mono text-xs text-muted-foreground">Local path: {bootstrapScript.location}</p>
          )}
        </div>
      )}
    </>
  );
}

// F8 ticket 5: the CloudFormation sibling to ScriptOnboardingPanel. Covers the three
// account states plan/F8-cloudformation-onboarding.md §5/§5a describe. The caller
// disables the whole tab for 'script_sibling', but `Tabs` is uncontrolled — a disabled
// trigger doesn't retroactively move the customer off an already-active panel, and the
// state can flip to 'script_sibling' *after* the tab was opened (the fresh-until-resolved
// default while `list()` is in flight, or a Retry after an error). The explicit guard
// below is the actual backstop; the disabled trigger is just the common-case UX.
function CloudFormationOnboardingPanel({
  createdInfra, accountState, siblingsLoading, siblingsError, onRetry, misconfig,
  quickCreate, recommendedComputeType, mergedExternalIds, cfnStackName, copiedKey, copy,
}: {
  createdInfra: InfrastructureCreateResponse;
  accountState: 'fresh' | 'cfn_sibling' | 'script_sibling';
  siblingsLoading: boolean;
  siblingsError: boolean;
  onRetry: () => void;
  misconfig: string | null;
  quickCreate: QuickCreateLink | null;
  recommendedComputeType: ComputeType;
  mergedExternalIds: string[];
  cfnStackName: string | null;
  copiedKey: string | null;
  copy: (text: string, key: string) => void;
}) {
  if (siblingsLoading) {
    return (
      <div className="flex items-center justify-center gap-2.5 py-6 text-muted-foreground" role="status">
        <Loader2 className="size-4 animate-spin" />
        <p className="text-xs">Checking your AWS account&apos;s onboarding status…</p>
      </div>
    );
  }

  if (siblingsError) {
    return (
      <div className="space-y-2.5 rounded-lg border border-destructive/30 bg-destructive/10 p-4">
        <p className="text-xs text-destructive">Couldn&apos;t check this AWS account&apos;s onboarding status.</p>
        <Button variant="outline" size="sm" onClick={onRetry} className="gap-1.5">
          <Loader2 className="size-3.5" /> Retry
        </Button>
      </div>
    );
  }

  if (accountState === 'script_sibling') {
    return (
      <p className="text-xs text-muted-foreground">
        This AWS account already uses the shell-script onboarding method, an account picks one method for life.
        Use the Script tab instead.
      </p>
    );
  }

  const templateUrl = resolveCfnTemplateUrl(recommendedComputeType);
  const templateLabel = recommendedComputeType === 'eks' ? 'Kubernetes (ecs_fargate + eks)' : 'ECS Fargate';

  const paramRows: { k: string; v: string; key: string }[] = [
    { k: 'ExternalIds', v: mergedExternalIds.join(','), key: 'cfn-external-ids' },
    { k: 'LaunchpadPlatformAccountId', v: createdInfra.platform_account_id, key: 'cfn-platform-account' },
    { k: 'LaunchpadPlatformUser', v: createdInfra.platform_user, key: 'cfn-platform-user' },
  ];

  return (
    <div className="space-y-4">
      {misconfig ? (
        <div className="flex items-start gap-3 rounded-lg border border-warning/30 bg-warning/10 p-4">
          <ShieldAlert className="mt-0.5 size-4 shrink-0 text-warning" />
          <div className="space-y-1">
            <p className="text-sm font-medium text-warning">CloudFormation onboarding is misconfigured</p>
            <p className="text-xs text-warning/80">{misconfig}</p>
          </div>
        </div>
      ) : accountState === 'cfn_sibling' ? (
        <div className="space-y-3">
          <div className="flex items-start gap-3 rounded-lg border border-border bg-surface-1 p-4">
            <CloudCog className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
            <p className="text-xs text-muted-foreground">
              AWS account <span className="font-mono">{createdInfra.code}</span> already has a CloudFormation-managed
              {' '}<span className="font-mono">{CFN_ROLE_NAME}</span> stack. Update it in place instead of creating a new one
             , open the stack&apos;s <span className="font-medium">Update stack</span> flow, choose
              {' '}<span className="font-medium">Replace current template</span>, and paste the values below.
            </p>
          </div>
          <div className="space-y-2.5 rounded-lg border border-border bg-card p-4">
            <div className="flex items-center gap-2">
              <span className="eyebrow w-44 shrink-0">Template (recommended)</span>
              <span className="text-xs text-foreground">{templateLabel}</span>
            </div>
            {templateUrl && (
              <div className="flex items-center gap-2">
                <span className="eyebrow w-44 shrink-0">Template URL</span>
                <code className="text-xs font-mono text-foreground break-all flex-1">{templateUrl}</code>
                <button onClick={() => copy(templateUrl, 'cfn-template-url')} className="shrink-0 text-muted-foreground hover:text-foreground transition-colors">
                  {copiedKey === 'cfn-template-url' ? <Check className="size-3.5 text-success" /> : <Copy className="size-3.5" />}
                </button>
              </div>
            )}
            {paramRows.map((row) => (
              <div key={row.key} className="flex items-center gap-2">
                <span className="eyebrow w-44 shrink-0">{row.k}</span>
                <code className="text-xs font-mono text-foreground break-all flex-1">{row.v}</code>
                <button onClick={() => copy(row.v, row.key)} className="shrink-0 text-muted-foreground hover:text-foreground transition-colors">
                  {copiedKey === row.key ? <Check className="size-3.5 text-success" /> : <Copy className="size-3.5" />}
                </button>
              </div>
            ))}
          </div>
          {cfnStackName ? (
            <a href={buildStackInfoUrl(cfnStackName)} target="_blank" rel="noopener noreferrer">
              <Button variant="outline" className="w-full gap-1.5">
                <ExternalLink className="size-3.5" /> Open stack in AWS console
              </Button>
            </a>
          ) : (
            <p className="text-xs text-muted-foreground">
              Open CloudFormation in the AWS console (region us-east-1) and find the stack backing{' '}
              <span className="font-mono">{CFN_ROLE_NAME}</span> to apply this update.
            </p>
          )}
          <p className="text-xs text-muted-foreground">
            Once the update completes, click <span className="font-medium">Verify connection</span> on this
            infrastructure&apos;s detail page to finish onboarding.
          </p>
        </div>
      ) : quickCreate ? (
        <div className="space-y-3">
          <div className="space-y-2 rounded-lg border border-border bg-card p-4">
            <div className="flex items-start gap-3">
              <CloudCog className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
              <div className="space-y-0.5 flex-1 min-w-0">
                <p className="text-sm font-medium text-foreground">Launch stack ({templateLabel})</p>
                <p className="text-xs text-muted-foreground">
                  Opens the CloudFormation console pre-filled with this infrastructure&apos;s parameters. Review
                  and create the stack there, nothing happens on Launchpad&apos;s side until you verify the
                  connection.
                </p>
              </div>
            </div>
          </div>
          <a href={quickCreate.url} target="_blank" rel="noopener noreferrer">
            <Button className="w-full gap-1.5">
              <ExternalLink className="size-3.5" /> Launch stack in AWS console
            </Button>
          </a>
          <p className="text-xs text-muted-foreground">
            After the stack reaches <span className="font-mono">CREATE_COMPLETE</span>, click{' '}
            <span className="font-medium">Verify connection</span> on this infrastructure&apos;s detail page to
            finish onboarding.
          </p>
        </div>
      ) : (
        <div className="space-y-2.5 rounded-lg border border-border bg-card p-4">
          <p className="text-xs text-muted-foreground">
            The parameter list is too large for a quick-create link, specify the template manually in the AWS
            console and paste these parameters:
          </p>
          {templateUrl && (
            <div className="flex items-center gap-2">
              <span className="eyebrow w-44 shrink-0">Template URL</span>
              <code className="text-xs font-mono text-foreground break-all flex-1">{templateUrl}</code>
              <button onClick={() => copy(templateUrl, 'cfn-template-url')} className="shrink-0 text-muted-foreground hover:text-foreground transition-colors">
                {copiedKey === 'cfn-template-url' ? <Check className="size-3.5 text-success" /> : <Copy className="size-3.5" />}
              </button>
            </div>
          )}
          {paramRows.map((row) => (
            <div key={row.key} className="flex items-center gap-2">
              <span className="eyebrow w-44 shrink-0">{row.k}</span>
              <code className="text-xs font-mono text-foreground break-all flex-1">{row.v}</code>
              <button onClick={() => copy(row.v, row.key)} className="shrink-0 text-muted-foreground hover:text-foreground transition-colors">
                {copiedKey === row.key ? <Check className="size-3.5 text-success" /> : <Copy className="size-3.5" />}
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
