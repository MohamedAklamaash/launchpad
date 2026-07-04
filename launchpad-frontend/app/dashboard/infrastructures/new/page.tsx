'use client';

import { useState, useEffect, useRef } from 'react';
import { useRouter } from 'next/navigation';
import { motion } from 'framer-motion';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { ArrowLeft, Server, Cpu, HardDrive, Hash, Copy, Check, Terminal, Globe, Search, ChevronDown, ShieldAlert, Rocket } from 'lucide-react';
import { LaunchSequence, PreflightMeter } from '@/components/launch';
import { infrastructureApi, AwsRegion } from '@/lib/api/infrastructures';
import { InfrastructureCreateResponse } from '@/types/infrastructure';
import { resolveOnboardingScript, getOnboardingMisconfiguration } from '@/lib/onboarding-scripts';
import { toast } from 'sonner';

const API_GATEWAY_URL = process.env.NEXT_PUBLIC_API_GATEWAY_URL || 'http://localhost:8000';

const rise = { initial: { opacity: 0, y: 14 }, animate: { opacity: 1, y: 0, transition: { duration: 0.4, ease: [0.16, 1, 0.3, 1] as const } } };

export default function NewInfrastructurePage() {
  const router = useRouter();
  const [loading, setLoading] = useState(false);
  const [copiedKey, setCopiedKey] = useState<string | null>(null);
  const [regions, setRegions] = useState<AwsRegion[]>([]);
  const [regionSearch, setRegionSearch] = useState('');
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const [createdInfra, setCreatedInfra] = useState<InfrastructureCreateResponse | null>(null);

  const [formData, setFormData] = useState({
    name: '',
    cloud_provider: 'aws' as const,
    max_cpu: 4,
    max_memory: 8,
    code: '',
    aws_region: 'us-east-1',
  });

  useEffect(() => {
    infrastructureApi.listRegions().then(setRegions).catch(() => { });
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

  const preflight = [
    formData.name.trim().length > 0,
    formData.code.trim().length === 12,
    formData.max_cpu > 0,
    formData.max_memory > 0,
  ].filter(Boolean).length;

  const filteredRegions = regions.filter(
    (r) =>
      r.label.toLowerCase().includes(regionSearch.toLowerCase()) ||
      r.value.toLowerCase().includes(regionSearch.toLowerCase())
  );

  const selectedRegionLabel = regions.find((r) => r.value === formData.aws_region)?.label ?? formData.aws_region;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
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

        <div className="rounded-2xl panel p-5">
          <LaunchSequence current="configure" />
        </div>

        <div>
          <span className="eyebrow">Stage 01 / Pre-flight</span>
          <h1 className="mt-1 text-2xl font-display font-semibold text-foreground tracking-tight">Provision an environment</h1>
          <p className="text-sm text-muted-foreground mt-1.5">Stand up a managed AWS environment for your applications.</p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-1">
          <Field icon={<Server className="w-3.5 h-3.5" />} label="Name" hint="e.g. production, staging">
            <Input value={formData.name} onChange={(e) => set('name', e.target.value)}
              placeholder="production" required
              className="bg-transparent border-0 h-9 text-sm placeholder:text-muted-foreground/50 focus-visible:ring-0 pl-6" />
          </Field>

          <Field icon={<Hash className="w-3.5 h-3.5" />} label="AWS Account ID" hint="12-digit account number">
            <Input value={formData.code} onChange={(e) => set('code', e.target.value)}
              placeholder="123456789012" required maxLength={12}
              className="bg-transparent border-0 h-9 text-sm placeholder:text-muted-foreground/50 focus-visible:ring-0 pl-6 font-mono" />
          </Field>

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

          <div className="grid grid-cols-2 gap-0">
            <Field icon={<Cpu className="w-3.5 h-3.5" />} label="Max CPU" hint="vCPU" noBorder>
              <Input type="number" step="0.25" min={0.25} value={formData.max_cpu}
                onChange={(e) => set('max_cpu', parseFloat(e.target.value))} required
                className="bg-transparent border-0 h-9 text-sm focus-visible:ring-0 pl-6 font-mono" />
            </Field>
            <Field icon={<HardDrive className="w-3.5 h-3.5" />} label="Max Memory" hint="GB">
              <Input type="number" step="0.5" min={0.5} value={formData.max_memory}
                onChange={(e) => set('max_memory', parseFloat(e.target.value))} required
                className="bg-transparent border-0 h-9 text-sm focus-visible:ring-0 pl-6 font-mono" />
            </Field>
          </div>

          <div className="pt-4">
            <PreflightMeter ready={preflight} total={4} />
          </div>

          <div className="pt-4 flex gap-2">
            <Button type="submit" size="lg" disabled={loading} className="px-5 gap-1.5">
              <Rocket className="w-4 h-4" /> {loading ? 'Igniting…' : 'Ignite provisioning'}
            </Button>
            <Button type="button" variant="outline" size="lg" onClick={() => router.back()}>
              Cancel
            </Button>
          </div>
        </form>
      </div>
    </motion.div>
  );
}

function Field({ icon, label, hint, children, noBorder }: {
  icon: React.ReactNode; label: string; hint?: string; children: React.ReactNode; noBorder?: boolean;
}) {
  return (
    <div className={`group bg-surface-1 border-hairline px-4 py-2.5 transition-colors focus-within:border-brand/40 focus-within:bg-surface-2 ${noBorder ? 'border border-r-0' : 'border'} first:rounded-t-xl last:rounded-b-xl`}>
      <div className="flex items-center gap-2 mb-0.5">
        <span className="text-muted-foreground/70 group-focus-within:text-brand transition-colors">{icon}</span>
        <span className="eyebrow">{label}</span>
        {hint && <span className="text-[10px] text-muted-foreground/60 ml-auto font-mono">{hint}</span>}
      </div>
      {children}
    </div>
  );
}
