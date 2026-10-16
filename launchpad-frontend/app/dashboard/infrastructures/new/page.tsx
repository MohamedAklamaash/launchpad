'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import { useRouter } from 'next/navigation';
import { motion, AnimatePresence } from 'framer-motion';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import { ArrowLeft, ArrowRight, Server, Cpu, HardDrive, Hash, Copy, Check, Terminal, Globe, Search, ChevronDown, ShieldAlert, Rocket, Layers, CloudCog, ExternalLink, Loader2 } from 'lucide-react';
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

const rise = { initial: { opacity: 0, y: 14 }, animate: { opacity: 1, y: 0, transition: { duration: 0.4, ease: [0.16, 1, 0.3, 1] as const } } };

const STEPS = [
  { title: 'Identity', sub: 'Pre-flight', blurb: 'Name the environment and link your AWS account.' },
  { title: 'Region', sub: 'Placement', blurb: 'Choose where this environment is deployed.' },
  { title: 'Resources', sub: 'Compute', blurb: 'Pick the compute target and set the CPU and memory ceilings.' },
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
  const [dir, setDir] = useState(1);

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
  const readyCount = Object.values(checks).filter(Boolean).length;
  const totalChecks = Object.keys(checks).length;
  const isLast = step === STEPS.length - 1;

  useEffect(() => {
    if (step > gate) setStep(gate);
  }, [gate, step]);

  const go = (i: number) => { setDir(i > step ? 1 : -1); setStep(i); };
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
      toast.success('Liftoff — infrastructure created. Run the bootstrap script to finish onboarding.');
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
      () => toast.error('Copy failed — copy it manually'),
    );
  };

  if (createdInfra) {
    return (
      <motion.div {...rise} className="flex justify-center">
        <div className="w-full max-w-2xl space-y-6">
          <button onClick={() => router.back()} className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors">
            <ArrowLeft className="w-3.5 h-3.5" /> Back
          </button>

          <div className="rounded-2xl panel p-5">
            <LaunchSequence current="ignition" />
          </div>

          <div className="flex items-start gap-3">
            <span className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-brand-soft border border-brand/30">
              <Rocket className="w-4.5 h-4.5 text-brand" />
            </span>
            <div>
              <span className="eyebrow">Stage 02 / Ignition</span>
              <h1 className="mt-1 text-2xl font-display font-semibold text-foreground tracking-tight">Connect your AWS account</h1>
              <p className="text-sm text-muted-foreground mt-1.5">
                {cfnUiEnabled ? (
                  <>Finish onboarding account <span className="font-mono text-foreground">{createdInfra.code}</span> with the shell script or a CloudFormation stack. Provisioning starts automatically once either one finishes.</>
                ) : (
                  <>Run this command in a shell with AWS credentials for account <span className="font-mono text-foreground">{createdInfra.code}</span>. Provisioning starts automatically once the script finishes.</>
                )}
              </p>
            </div>
          </div>

          {cfnUiEnabled ? (
            <Tabs defaultValue="script" className="w-full">
              <TabsList className="w-full">
                <TabsTrigger value="script" className="flex-1">Script</TabsTrigger>
                <span title={cfnAccountState === 'script_sibling' ? 'This AWS account already uses the shell-script onboarding method — use the Script tab instead.' : undefined} className="flex-1">
                  <TabsTrigger value="cloudformation" disabled={cfnAccountState === 'script_sibling'} className="w-full">
                    CloudFormation
                  </TabsTrigger>
                </span>
              </TabsList>

              {cfnAccountState === 'script_sibling' && (
                <p className="text-[11px] text-muted-foreground px-1 pt-2">
                  This AWS account already uses the shell-script onboarding method — an account picks one method
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
                  siblingsLoading={siblingsLoading}
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
            <Button size="lg" onClick={() => router.push(`/dashboard/infrastructures/${createdInfra.id}`)} className="px-5 gap-1.5">
              <Rocket className="w-4 h-4" /> Track provisioning
            </Button>
          </div>
        </div>
      </motion.div>
    );
  }

  return (
    <motion.div {...rise} className="flex justify-center">
      <div className="w-full max-w-lg space-y-6">
        <button onClick={() => router.back()} className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors">
          <ArrowLeft className="w-3.5 h-3.5" /> Back
        </button>

        <div>
          <span className="eyebrow">Stage 01 / Pre-flight</span>
          <h1 className="mt-1 text-2xl font-display font-semibold text-foreground tracking-tight">Provision an environment</h1>
          <p className="text-sm text-muted-foreground mt-1.5">Stand up a managed AWS environment for your applications.</p>
        </div>

        <div className="rounded-2xl panel p-5">
          <Stepper current={step} gate={gate} onSelect={go} />
        </div>

        <form onSubmit={onSubmit} className="space-y-6">
          <AnimatePresence mode="wait" custom={dir}>
            <motion.div
              key={step}
              custom={dir}
              initial={{ opacity: 0, x: dir * 26 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: dir * -26 }}
              transition={{ duration: 0.28, ease: [0.16, 1, 0.3, 1] }}
              className="space-y-4"
            >
              <div>
                <h2 className="text-sm font-display font-semibold text-foreground">{STEPS[step].title}</h2>
                <p className="text-xs text-muted-foreground mt-0.5">{STEPS[step].blurb}</p>
              </div>

              {step === 0 && (
                <div>
                  <Field icon={<Server className="w-3.5 h-3.5" />} label="Name" hint="e.g. production, staging">
                    <Input value={formData.name} onChange={(e) => set('name', e.target.value)}
                      placeholder="production" autoFocus
                      className="bg-transparent border-0 h-9 text-sm placeholder:text-muted-foreground/50 focus-visible:ring-0 pl-6" />
                  </Field>
                  <Field icon={<Hash className="w-3.5 h-3.5" />} label="AWS Account ID" hint="12-digit account number">
                    <Input value={formData.code} onChange={(e) => set('code', e.target.value)}
                      placeholder="123456789012" maxLength={12}
                      className="bg-transparent border-0 h-9 text-sm placeholder:text-muted-foreground/50 focus-visible:ring-0 pl-6 font-mono" />
                  </Field>
                </div>
              )}

              {step === 1 && (
                <Field icon={<Globe className="w-3.5 h-3.5" />} label="AWS Region" hint="Deployment region">
                  <div className="relative" ref={dropdownRef}>
                    <button
                      type="button"
                      onClick={() => { setDropdownOpen((o) => !o); setRegionSearch(''); }}
                      className="w-full flex items-center justify-between h-9 pl-6 text-sm text-foreground focus:outline-none"
                    >
                      <span className="font-mono text-sm">{selectedRegionLabel}</span>
                      <ChevronDown className={`w-3.5 h-3.5 text-muted-foreground transition-transform ${dropdownOpen ? 'rotate-180' : ''}`} />
                    </button>

                    {dropdownOpen && (
                      <div className="absolute z-50 left-0 right-0 top-full mt-2 bg-popover border border-hairline-strong rounded-xl shadow-xl shadow-black/40 overflow-hidden">
                        <div className="flex items-center gap-2 px-3 py-2.5 border-b border-hairline">
                          <Search className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
                          <input
                            autoFocus
                            value={regionSearch}
                            onChange={(e) => setRegionSearch(e.target.value)}
                            placeholder="Search regions…"
                            className="flex-1 bg-transparent text-xs text-foreground placeholder:text-muted-foreground focus:outline-none"
                          />
                        </div>
                        <ul className="max-h-52 overflow-y-auto py-1">
                          {filteredRegions.length === 0 ? (
                            <li className="px-4 py-3 text-xs text-muted-foreground">No regions found</li>
                          ) : (
                            filteredRegions.map((r) => (
                              <li key={r.value}>
                                <button
                                  type="button"
                                  onClick={() => { set('aws_region', r.value); setDropdownOpen(false); }}
                                  className={`w-full text-left px-4 py-2 flex items-center justify-between transition-colors hover:bg-surface-3 ${formData.aws_region === r.value ? 'text-brand' : 'text-foreground'}`}
                                >
                                  <span className="text-xs">{r.label}</span>
                                  <span className="text-[10px] font-mono text-muted-foreground">{r.value}</span>
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
                <div>
                  <Field icon={<Layers className="w-3.5 h-3.5" />} label="Compute Target" hint="immutable after create">
                    <div className="flex gap-2 py-1 pl-6">
                      {COMPUTE_OPTIONS.map((o) => {
                        const available = enabledComputeTypes.includes(o.value);
                        const selected = formData.compute_type === o.value;
                        return (
                          <button
                            key={o.value}
                            type="button"
                            disabled={!available}
                            title={available ? undefined : `${o.label} is not enabled on this deployment`}
                            onClick={() => set('compute_type', o.value)}
                            className={`flex-1 rounded-lg border px-3 py-2 text-left transition-colors ${selected ? 'border-brand/60 bg-brand-soft' : 'border-hairline bg-surface-1'} ${available ? 'hover:bg-surface-2' : 'opacity-50 cursor-not-allowed'}`}
                          >
                            <p className={`text-xs font-medium ${selected ? 'text-brand' : 'text-foreground'}`}>{o.label}</p>
                            <p className="text-[10px] text-muted-foreground mt-0.5">
                              {available ? o.blurb : 'Not enabled on this deployment'}
                            </p>
                          </button>
                        );
                      })}
                    </div>
                  </Field>
                  <Field icon={<Cpu className="w-3.5 h-3.5" />} label="Max CPU" hint="vCPU">
                    <Input type="number" step="0.25" min={0.25} value={Number.isNaN(formData.max_cpu) ? '' : formData.max_cpu}
                      onChange={(e) => set('max_cpu', e.target.value === '' ? NaN : parseFloat(e.target.value))}
                      className="bg-transparent border-0 h-9 text-sm focus-visible:ring-0 pl-6 font-mono" />
                  </Field>
                  <Field icon={<HardDrive className="w-3.5 h-3.5" />} label="Max Memory" hint="GB">
                    <Input type="number" step="0.5" min={0.5} value={Number.isNaN(formData.max_memory) ? '' : formData.max_memory}
                      onChange={(e) => set('max_memory', e.target.value === '' ? NaN : parseFloat(e.target.value))}
                      className="bg-transparent border-0 h-9 text-sm focus-visible:ring-0 pl-6 font-mono" />
                  </Field>
                </div>
              )}
            </motion.div>
          </AnimatePresence>

          <div className="flex items-center gap-3 pt-1">
            <Button type="button" variant="outline" size="lg" onClick={back} className="gap-1.5">
              <ArrowLeft className="w-3.5 h-3.5" /> {step === 0 ? 'Cancel' : 'Back'}
            </Button>
            <span className="font-mono text-[10px] uppercase tracking-[0.16em] text-muted-foreground/70">
              Stage {String(step + 1).padStart(2, '0')} / {String(STEPS.length).padStart(2, '0')}
              <span className="ml-2 text-muted-foreground/50">{readyCount}/{totalChecks} ready</span>
            </span>
            <div className="ml-auto">
              {isLast ? (
                <Button type="submit" size="lg" disabled={loading || readyCount < totalChecks} className="px-5 gap-1.5">
                  <Rocket className="w-4 h-4" /> {loading ? 'Igniting…' : 'Ignite provisioning'}
                </Button>
              ) : (
                <Button type="submit" size="lg" disabled={!stepValid[step]} className="px-5 gap-1.5">
                  Next <ArrowRight className="w-4 h-4" />
                </Button>
              )}
            </div>
          </div>
        </form>
      </div>
    </motion.div>
  );
}

function Stepper({ current, gate, onSelect }: { current: number; gate: number; onSelect: (i: number) => void }) {
  return (
    <div className="flex items-center">
      {STEPS.map((s, i) => {
        const state = i < current ? 'done' : i === current ? 'active' : 'todo';
        const reachable = i <= gate;
        return (
          <div key={s.title} className={`flex items-center ${i < STEPS.length - 1 ? 'flex-1' : ''}`}>
            <button
              type="button"
              onClick={() => reachable && onSelect(i)}
              disabled={!reachable}
              className="flex items-center gap-2.5 text-left outline-none disabled:cursor-not-allowed focus-visible:opacity-100"
            >
              <span
                className={`relative flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border text-[10px] font-mono transition-colors ${
                  state === 'done'
                    ? 'border-brand/40 bg-brand-soft text-brand'
                    : state === 'active'
                      ? 'border-brand bg-brand text-primary-foreground'
                      : 'border-hairline bg-surface-1 text-muted-foreground'
                }`}
              >
                {state === 'done' ? <Check className="w-3.5 h-3.5" /> : String(i + 1).padStart(2, '0')}
                {state === 'active' && <span className="absolute inset-0 rounded-lg bg-brand/40 animate-ping" />}
              </span>
              <div className="hidden sm:block leading-tight">
                <p className={`text-xs font-medium ${state === 'todo' ? 'text-muted-foreground' : 'text-foreground'}`}>{s.title}</p>
                <p className="font-mono text-[9px] uppercase tracking-[0.16em] text-muted-foreground/70">{s.sub}</p>
              </div>
            </button>
            {i < STEPS.length - 1 && <span className={`mx-3 h-px flex-1 ${i < current ? 'bg-brand/40' : 'bg-hairline'}`} />}
          </div>
        );
      })}
    </div>
  );
}

function Field({ icon, label, hint, children }: {
  icon: React.ReactNode; label: string; hint?: string; children: React.ReactNode;
}) {
  return (
    <div className="group bg-surface-1 border border-hairline px-4 py-2.5 transition-colors focus-within:border-brand/40 focus-within:bg-surface-2 first:rounded-t-xl last:rounded-b-xl">
      <div className="flex items-center gap-2 mb-0.5">
        <span className="text-muted-foreground/70 group-focus-within:text-brand transition-colors">{icon}</span>
        <span className="eyebrow">{label}</span>
        {hint && <span className="text-[10px] text-muted-foreground/60 ml-auto font-mono">{hint}</span>}
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
      <div className="rounded-xl border border-warning/30 bg-warning/10 p-4 flex items-start gap-3">
        <ShieldAlert className="w-4 h-4 text-warning shrink-0 mt-0.5" />
        <div className="space-y-0.5">
          <p className="text-sm font-medium text-warning">This token is shown only once</p>
          <p className="text-xs text-warning/80">
            Copy the snippet now. If you navigate away you will need to delete and recreate this infrastructure to get a new token.
          </p>
        </div>
      </div>

      {onboardingMisconfig && (
        <div className="rounded-xl border border-warning/30 bg-warning/10 p-4 flex items-start gap-3">
          <ShieldAlert className="w-4 h-4 text-warning shrink-0 mt-0.5" />
          <div className="space-y-1 flex-1">
            <p className="text-sm font-medium text-warning">Onboarding is misconfigured</p>
            <p className="text-xs text-warning/80">{onboardingMisconfig}</p>
            <p className="text-xs text-warning/80">
              Your onboarding token and AWS account ID are still valid — use them with a manually-fetched copy of the bootstrap script.
            </p>
            <div className="mt-2 space-y-1.5">
              {[
                { k: 'Account ID', v: createdInfra.code, copyable: false, key: '' },
                { k: 'Infra ID', v: createdInfra.id, copyable: false, key: '' },
                { k: 'Onboarding token', v: createdInfra.onboarding_token, copyable: true, key: 'token' },
              ].map((row) => (
                <div key={row.k} className="flex items-center gap-2">
                  <span className="eyebrow w-28 shrink-0">{row.k}</span>
                  <code className="text-xs font-mono text-warning break-all">{row.v}</code>
                  {row.copyable && (
                    <button onClick={() => copy(row.v, row.key)} className="shrink-0 text-warning/70 hover:text-warning transition-colors">
                      {copiedKey === row.key ? <Check className="w-3.5 h-3.5 text-success" /> : <Copy className="w-3.5 h-3.5" />}
                    </button>
                  )}
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {bootstrapScript && (
        <div className="rounded-xl panel p-4 space-y-3">
          <div className="flex items-start gap-3">
            <Terminal className="w-4 h-4 text-success shrink-0 mt-0.5" />
            <div className="space-y-0.5 flex-1 min-w-0">
              <p className="text-sm font-medium text-foreground">{bootstrapScript.label}</p>
              <p className="text-xs text-muted-foreground">{bootstrapScript.description}</p>
            </div>
            <button onClick={() => copy(bootstrapScript!.invocation, 'bootstrap')} className="shrink-0 text-muted-foreground hover:text-foreground transition-colors">
              {copiedKey === 'bootstrap' ? <Check className="w-3.5 h-3.5 text-success" /> : <Copy className="w-3.5 h-3.5" />}
            </button>
          </div>
          <pre className="bg-surface-3 border border-hairline rounded-lg px-3 py-2.5 text-[11px] font-mono text-success overflow-x-auto whitespace-pre">{bootstrapScript.invocation}</pre>
          {bootstrapScript.locationIsUrl ? (
            <a href={bootstrapScript.location} target="_blank" rel="noopener noreferrer" className="inline-block text-[10px] text-muted-foreground hover:text-brand transition-colors font-mono">
              View script source →
            </a>
          ) : (
            <p className="text-[10px] text-muted-foreground font-mono">Local path: {bootstrapScript.location}</p>
          )}
        </div>
      )}
    </>
  );
}

// F8 ticket 5: the CloudFormation sibling to ScriptOnboardingPanel. Covers the three
// account states plan/F8-cloudformation-onboarding.md §5/§5a describe — 'script_sibling'
// is handled by the caller disabling this whole tab, so this component only ever renders
// for 'fresh' or 'cfn_sibling'.
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
      <div className="flex items-center gap-2.5 py-6 justify-center text-muted-foreground">
        <Loader2 className="w-4 h-4 animate-spin" />
        <p className="text-xs">Checking your AWS account&apos;s onboarding status…</p>
      </div>
    );
  }

  if (siblingsError) {
    return (
      <div className="rounded-xl border border-destructive/30 bg-destructive/10 p-4 space-y-2.5">
        <p className="text-xs text-destructive">Couldn&apos;t check this AWS account&apos;s onboarding status.</p>
        <Button variant="outline" size="sm" onClick={onRetry} className="gap-1.5">
          <Loader2 className="w-3.5 h-3.5" /> Retry
        </Button>
      </div>
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
        <div className="rounded-xl border border-warning/30 bg-warning/10 p-4 flex items-start gap-3">
          <ShieldAlert className="w-4 h-4 text-warning shrink-0 mt-0.5" />
          <div className="space-y-1">
            <p className="text-sm font-medium text-warning">CloudFormation onboarding is misconfigured</p>
            <p className="text-xs text-warning/80">{misconfig}</p>
          </div>
        </div>
      ) : accountState === 'cfn_sibling' ? (
        <div className="space-y-3">
          <div className="rounded-xl border border-azure/20 bg-azure/5 p-4 flex items-start gap-3">
            <CloudCog className="w-4 h-4 text-azure shrink-0 mt-0.5" />
            <p className="text-xs text-azure">
              AWS account <span className="font-mono">{createdInfra.code}</span> already has a CloudFormation-managed
              {' '}<span className="font-mono">{CFN_ROLE_NAME}</span> stack. Update it in place instead of creating a new one
              — open the stack&apos;s <span className="font-medium">Update stack</span> flow, choose
              {' '}<span className="font-medium">Replace current template</span>, and paste the values below.
            </p>
          </div>
          <div className="rounded-xl panel p-4 space-y-2.5">
            <div className="flex items-center gap-2">
              <span className="eyebrow w-44 shrink-0">Template (recommended)</span>
              <span className="text-xs text-foreground">{templateLabel}</span>
            </div>
            {templateUrl && (
              <div className="flex items-center gap-2">
                <span className="eyebrow w-44 shrink-0">Template URL</span>
                <code className="text-xs font-mono text-foreground break-all flex-1">{templateUrl}</code>
                <button onClick={() => copy(templateUrl, 'cfn-template-url')} className="shrink-0 text-muted-foreground hover:text-foreground transition-colors">
                  {copiedKey === 'cfn-template-url' ? <Check className="w-3.5 h-3.5 text-success" /> : <Copy className="w-3.5 h-3.5" />}
                </button>
              </div>
            )}
            {paramRows.map((row) => (
              <div key={row.key} className="flex items-center gap-2">
                <span className="eyebrow w-44 shrink-0">{row.k}</span>
                <code className="text-xs font-mono text-foreground break-all flex-1">{row.v}</code>
                <button onClick={() => copy(row.v, row.key)} className="shrink-0 text-muted-foreground hover:text-foreground transition-colors">
                  {copiedKey === row.key ? <Check className="w-3.5 h-3.5 text-success" /> : <Copy className="w-3.5 h-3.5" />}
                </button>
              </div>
            ))}
          </div>
          {cfnStackName ? (
            <a href={buildStackInfoUrl(cfnStackName)} target="_blank" rel="noopener noreferrer">
              <Button variant="outline" className="w-full gap-1.5">
                <ExternalLink className="w-3.5 h-3.5" /> Open stack in AWS console
              </Button>
            </a>
          ) : (
            <p className="text-[11px] text-muted-foreground">
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
          <div className="rounded-xl panel p-4 space-y-2">
            <div className="flex items-start gap-3">
              <CloudCog className="w-4 h-4 text-success shrink-0 mt-0.5" />
              <div className="space-y-0.5 flex-1 min-w-0">
                <p className="text-sm font-medium text-foreground">Launch stack ({templateLabel})</p>
                <p className="text-xs text-muted-foreground">
                  Opens the CloudFormation console pre-filled with this infrastructure&apos;s parameters. Review
                  and create the stack there — nothing happens on Launchpad&apos;s side until you verify the
                  connection.
                </p>
              </div>
            </div>
          </div>
          <a href={quickCreate.url} target="_blank" rel="noopener noreferrer">
            <Button className="w-full gap-1.5">
              <ExternalLink className="w-3.5 h-3.5" /> Launch stack in AWS console
            </Button>
          </a>
          <p className="text-xs text-muted-foreground">
            After the stack reaches <span className="font-mono">CREATE_COMPLETE</span>, click{' '}
            <span className="font-medium">Verify connection</span> on this infrastructure&apos;s detail page to
            finish onboarding.
          </p>
        </div>
      ) : (
        <div className="rounded-xl panel p-4 space-y-2.5">
          <p className="text-xs text-muted-foreground">
            The parameter list is too large for a quick-create link — specify the template manually in the AWS
            console and paste these parameters:
          </p>
          {templateUrl && (
            <div className="flex items-center gap-2">
              <span className="eyebrow w-44 shrink-0">Template URL</span>
              <code className="text-xs font-mono text-foreground break-all flex-1">{templateUrl}</code>
              <button onClick={() => copy(templateUrl, 'cfn-template-url')} className="shrink-0 text-muted-foreground hover:text-foreground transition-colors">
                {copiedKey === 'cfn-template-url' ? <Check className="w-3.5 h-3.5 text-success" /> : <Copy className="w-3.5 h-3.5" />}
              </button>
            </div>
          )}
          {paramRows.map((row) => (
            <div key={row.key} className="flex items-center gap-2">
              <span className="eyebrow w-44 shrink-0">{row.k}</span>
              <code className="text-xs font-mono text-foreground break-all flex-1">{row.v}</code>
              <button onClick={() => copy(row.v, row.key)} className="shrink-0 text-muted-foreground hover:text-foreground transition-colors">
                {copiedKey === row.key ? <Check className="w-3.5 h-3.5 text-success" /> : <Copy className="w-3.5 h-3.5" />}
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
