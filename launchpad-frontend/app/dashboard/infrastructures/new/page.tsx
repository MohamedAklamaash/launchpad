'use client';

import { useState, useEffect, useRef } from 'react';
import { useRouter } from 'next/navigation';
import { motion, AnimatePresence } from 'framer-motion';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { ArrowLeft, ArrowRight, Server, Cpu, HardDrive, Hash, Copy, Check, Terminal, Globe, Search, ChevronDown, ShieldAlert, Rocket, Layers } from 'lucide-react';
import { LaunchSequence } from '@/components/launch';
import { infrastructureApi, AwsRegion } from '@/lib/api/infrastructures';
import { ComputeType, InfrastructureCreateResponse } from '@/types/infrastructure';
import { resolveOnboardingScript, getOnboardingMisconfiguration } from '@/lib/onboarding-scripts';
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

  const mockEnv = createdInfra?.is_mock
    ? [
      `export LAUNCHPAD_MOCK=1`,
      `export LAUNCHPAD_ACCOUNT_ID=${createdInfra.code}`,
    ]
    : [];

  const bootstrapEnv = createdInfra
    ? [
      `export LAUNCHPAD_INFRA_ID=${createdInfra.id}`,
      `export LAUNCHPAD_CALLBACK_URL=${API_GATEWAY_URL}/api/infrastructures/onboarding/callback`,
      `export LAUNCHPAD_ONBOARDING_TOKEN=${createdInfra.onboarding_token}`,
      `export LAUNCHPAD_EXTERNAL_ID=${createdInfra.id}`,
      `export LAUNCHPAD_COMPUTE_TYPE=${createdInfra.compute_type}`,
      ...mockEnv,
    ]
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
              <h1 className="mt-1 text-2xl font-display font-semibold text-foreground tracking-tight">Bootstrap your AWS account</h1>
              <p className="text-sm text-muted-foreground mt-1.5">
                Run this command in a shell with AWS credentials for account <span className="font-mono text-foreground">{createdInfra.code}</span>.
                Provisioning starts automatically once the script finishes.
              </p>
            </div>
          </div>

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
