'use client';

import { useState, useEffect, useRef } from 'react';
import { useRouter } from 'next/navigation';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { ArrowLeft, Server, Cpu, HardDrive, Hash, Copy, Check, Terminal, Globe, Search, ChevronDown, ShieldAlert, RefreshCw } from 'lucide-react';
import { infrastructureApi, AwsRegion } from '@/lib/api/infrastructures';
import { InfrastructureCreateResponse } from '@/types/infrastructure';
import { resolveOnboardingScript, getOnboardingMisconfiguration } from '@/lib/onboarding-scripts';
import { toast } from 'sonner';

const API_GATEWAY_URL = process.env.NEXT_PUBLIC_API_GATEWAY_URL || 'http://localhost:8000';

export default function NewInfrastructurePage() {
  const router = useRouter();
  const [loading, setLoading] = useState(false);
  // Track which snippet was last copied so the bootstrap and refresh "Copy"
  // buttons don't share state — clicking one was previously flipping the
  // other's icon to <Check/>.
  const [copiedKey, setCopiedKey] = useState<string | null>(null);
  const [regions, setRegions] = useState<AwsRegion[]>([]);
  const [regionSearch, setRegionSearch] = useState('');
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);
  // Holds the create response — including the single-use onboarding token — once the form succeeds.
  const [createdInfra, setCreatedInfra] = useState<InfrastructureCreateResponse | null>(null);
  // Plaintext script API key, present only after the user clicks "Generate API key".
  // Injected into the refresh-script snippet so update_aws_role.sh runs are attributed.
  const [scriptApiKey, setScriptApiKey] = useState<string | null>(null);
  const [apiKeyLoading, setApiKeyLoading] = useState(false);

  const generateScriptApiKey = async () => {
    setApiKeyLoading(true);
    try {
      const { api_key } = await infrastructureApi.issueScriptApiKey();
      setScriptApiKey(api_key);
      toast.success('API key generated — it is shown only once');
    } catch {
      toast.error('Failed to generate API key');
    } finally {
      setApiKeyLoading(false);
    }
  };

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
      toast.success('Infrastructure created — run the bootstrap script to finish onboarding');
    } catch (err: unknown) {
      const error = err as { response?: { data?: { error?: string } } };
      toast.error(error.response?.data?.error || 'Failed to create infrastructure');
    } finally {
      setLoading(false);
    }
  };

  // Env vars the bootstrap script needs to bind the IAM role to this infra and
  // call the onboarding callback. update_aws_role.sh uses a different set: no
  // onboarding token, but a per-user API key + policy-refresh callback so the
  // platform records who ran the refresh.
  const bootstrapEnv = createdInfra
    ? [
      `export LAUNCHPAD_INFRA_ID=${createdInfra.id}`,
      `export LAUNCHPAD_CALLBACK_URL=${API_GATEWAY_URL}/api/infrastructures/onboarding/callback`,
      `export LAUNCHPAD_ONBOARDING_TOKEN=${createdInfra.onboarding_token}`,
      `export LAUNCHPAD_EXTERNAL_ID=${createdInfra.id}`,
    ]
    : [];

  const updateEnv = createdInfra
    ? [
      `export LAUNCHPAD_INFRA_ID=${createdInfra.id}`,
      `export LAUNCHPAD_EXTERNAL_ID=${createdInfra.id}`,
      `export LAUNCHPAD_CALLBACK_URL=${API_GATEWAY_URL}/api/infrastructures/policy-refresh/callback`,
      scriptApiKey
        ? `export LAUNCHPAD_API_KEY=${scriptApiKey}`
        : `export LAUNCHPAD_API_KEY=<generate-an-api-key-below>`,
    ]
    : [];

  // If onboarding env is missing (e.g. no pinned NEXT_PUBLIC_LAUNCHPAD_SCRIPT_REF
  // in a prod build) we don't want to crash the whole page — the customer still
  // needs to see their onboarding token + account ID. We render a misconfig
  // banner above the script blocks and skip script resolution.
  const onboardingMisconfig = createdInfra ? getOnboardingMisconfiguration() : null;
  let bootstrapScript: ReturnType<typeof resolveOnboardingScript> | null = null;
  let updateScript: ReturnType<typeof resolveOnboardingScript> | null = null;
  if (createdInfra && !onboardingMisconfig) {
    try {
      bootstrapScript = resolveOnboardingScript('create_aws_role.sh', bootstrapEnv);
      updateScript = resolveOnboardingScript('update_aws_role.sh', updateEnv);
    } catch {
      // getOnboardingMisconfiguration should have caught this, but defend
      // against future drift: leave scripts as null; banner already shown.
      bootstrapScript = null;
      updateScript = null;
    }
  }

  // Hold the timeout so a second copy click within 1.5s doesn't fire the
  // first click's reset and prematurely flip the Check icon off.
  const copyResetRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const copy = (text: string, key: string) => {
    if (!text) return;
    navigator.clipboard.writeText(text);
    if (copyResetRef.current) clearTimeout(copyResetRef.current);
    setCopiedKey(key);
    copyResetRef.current = setTimeout(() => {
      setCopiedKey(null);
      copyResetRef.current = null;
    }, 1500);
  };

  if (createdInfra) {
    return (
      <div className="flex justify-center">
        <div className="w-full max-w-2xl space-y-6">
          <button onClick={() => router.back()} className="flex items-center gap-1.5 text-xs text-[#555] hover:text-[#aaa] transition-colors">
            <ArrowLeft className="w-3.5 h-3.5" /> Back
          </button>

          <div>
            <h1 className="text-xl font-semibold text-white tracking-tight">Bootstrap your AWS account</h1>
            <p className="text-xs text-[#555] mt-1">
              Run this command in a shell with AWS credentials for account <span className="font-mono text-[#aaa]">{createdInfra.code}</span>.
              Provisioning starts automatically once the script finishes.
            </p>
          </div>

          <div className="bg-amber-950/30 border border-amber-900/40 rounded-xl p-4 flex items-start gap-3">
            <ShieldAlert className="w-4 h-4 text-amber-400 shrink-0 mt-0.5" />
            <div className="space-y-0.5">
              <p className="text-xs font-medium text-amber-200">This token is shown only once</p>
              <p className="text-[11px] text-amber-200/70">
                Copy the snippet now. If you navigate away you will need to delete and recreate this infrastructure to get a new token.
              </p>
            </div>
          </div>

          {onboardingMisconfig && (
            <div className="bg-amber-950/30 border border-amber-900/40 rounded-xl p-4 flex items-start gap-3">
              <ShieldAlert className="w-4 h-4 text-amber-400 shrink-0 mt-0.5" />
              <div className="space-y-1 flex-1">
                <p className="text-xs font-medium text-amber-200">Onboarding is misconfigured</p>
                <p className="text-[11px] text-amber-200/70">{onboardingMisconfig}</p>
                <p className="text-[11px] text-amber-200/70">
                  Your onboarding token and AWS account ID are still valid — use them with a manually-fetched copy of the bootstrap script.
                </p>
                <div className="mt-2 space-y-1">
                  <div className="flex items-center gap-2">
                    <span className="text-[10px] uppercase tracking-widest font-mono text-amber-200/60 w-28">Account ID</span>
                    <code className="text-[11px] font-mono text-amber-100">{createdInfra.code}</code>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="text-[10px] uppercase tracking-widest font-mono text-amber-200/60 w-28">Infra ID</span>
                    <code className="text-[11px] font-mono text-amber-100">{createdInfra.id}</code>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="text-[10px] uppercase tracking-widest font-mono text-amber-200/60 w-28">Onboarding token</span>
                    <code className="text-[11px] font-mono text-amber-100 break-all">{createdInfra.onboarding_token}</code>
                    <button
                      onClick={() => copy(createdInfra.onboarding_token, 'token')}
                      className="shrink-0 text-amber-300/60 hover:text-amber-200 transition-colors"
                    >
                      {copiedKey === 'token' ? <Check className="w-3.5 h-3.5 text-emerald-500" /> : <Copy className="w-3.5 h-3.5" />}
                    </button>
                  </div>
                </div>
              </div>
            </div>
          )}

          {bootstrapScript && (
            <div className="bg-[#0d0d0d] border border-[#1a1a1a] rounded-xl p-4 space-y-3">
              <div className="flex items-start gap-3">
                <Terminal className="w-4 h-4 text-emerald-400 shrink-0 mt-0.5" />
                <div className="space-y-0.5 flex-1">
                  <p className="text-xs font-medium text-white">{bootstrapScript.label}</p>
                  <p className="text-[11px] text-[#555]">{bootstrapScript.description}</p>
                </div>
                <button onClick={() => copy(bootstrapScript.invocation, 'bootstrap')} className="shrink-0 text-[#444] hover:text-white transition-colors">
                  {copiedKey === 'bootstrap' ? <Check className="w-3.5 h-3.5 text-emerald-500" /> : <Copy className="w-3.5 h-3.5" />}
                </button>
              </div>
              <pre className="bg-[#060606] border border-[#1e1e1e] rounded-lg px-3 py-2 text-[11px] font-mono text-emerald-400 overflow-x-auto whitespace-pre">{bootstrapScript.invocation}</pre>
              {bootstrapScript.locationIsUrl ? (
                <a href={bootstrapScript.location} target="_blank" rel="noopener noreferrer"
                  className="text-[10px] text-[#444] hover:text-violet-400 transition-colors font-mono">
                  View script source →
                </a>
              ) : (
                <p className="text-[10px] text-[#444] font-mono">Local path: {bootstrapScript.location}</p>
              )}
            </div>
          )}

          {/* Refresh-policy script. Shown alongside the bootstrap snippet so customers
              can find it again later when deployments start failing with AccessDenied —
              the canonical recovery path after the IAM policy widens (e.g. codebuild:*
              regression). No onboarding token is needed here, but the snippet carries the
              per-user script API key so the platform records who ran the refresh. */}
          {updateScript && (
            <div className="bg-[#0d0d0d] border border-[#1a1a1a] rounded-xl p-4 space-y-3">
              <div className="flex items-start gap-3">
                <RefreshCw className="w-4 h-4 text-amber-400 shrink-0 mt-0.5" />
                <div className="space-y-0.5 flex-1">
                  <p className="text-xs font-medium text-white">{updateScript.label}</p>
                  <p className="text-[11px] text-[#555]">{updateScript.description}</p>
                </div>
                <button onClick={() => copy(updateScript.invocation, 'update')} className="shrink-0 text-[#444] hover:text-white transition-colors">
                  {copiedKey === 'update' ? <Check className="w-3.5 h-3.5 text-emerald-500" /> : <Copy className="w-3.5 h-3.5" />}
                </button>
              </div>
              <pre className="bg-[#060606] border border-[#1e1e1e] rounded-lg px-3 py-2 text-[11px] font-mono text-amber-300 overflow-x-auto whitespace-pre">{updateScript.invocation}</pre>
              {!scriptApiKey && (
                <div className="flex items-center gap-2">
                  <Button
                    type="button"
                    onClick={generateScriptApiKey}
                    disabled={apiKeyLoading}
                    className="bg-[#1a1a1a] hover:bg-[#262626] h-7 text-[11px] font-medium px-3"
                  >
                    {apiKeyLoading ? 'Generating…' : 'Generate API key'}
                  </Button>
                  <p className="text-[10px] text-[#555]">
                    Fills LAUNCHPAD_API_KEY above so Launchpad records who ran the refresh. Shown only once; generating again revokes old keys.
                  </p>
                </div>
              )}
              {updateScript.locationIsUrl ? (
                <a href={updateScript.location} target="_blank" rel="noopener noreferrer"
                  className="text-[10px] text-[#444] hover:text-violet-400 transition-colors font-mono">
                  View script source →
                </a>
              ) : (
                <p className="text-[10px] text-[#444] font-mono">Local path: {updateScript.location}</p>
              )}
            </div>
          )}

          <div className="flex gap-2">
            <Button
              type="button"
              onClick={() => router.push(`/dashboard/infrastructures/${createdInfra.id}`)}
              className="bg-violet-600 hover:bg-violet-700 h-9 text-sm font-medium px-5"
            >
              Done — go to dashboard
            </Button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex justify-center">
      <div className="w-full max-w-lg space-y-6">
        <button onClick={() => router.back()} className="flex items-center gap-1.5 text-xs text-[#555] hover:text-[#aaa] transition-colors">
          <ArrowLeft className="w-3.5 h-3.5" /> Back
        </button>

        <div>
          <h1 className="text-xl font-semibold text-white tracking-tight">New Infrastructure</h1>
          <p className="text-xs text-[#555] mt-1">Provision an AWS environment for your applications</p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-1">
          <Field icon={<Server className="w-3.5 h-3.5" />} label="Name" hint="e.g. production, staging">
            <Input value={formData.name} onChange={(e) => set('name', e.target.value)}
              placeholder="production" required
              className="bg-transparent border-0 h-9 text-sm text-white placeholder:text-[#333] focus-visible:ring-0 pl-3" />
          </Field>

          <Field icon={<Hash className="w-3.5 h-3.5" />} label="AWS Account ID" hint="12-digit account number">
            <Input value={formData.code} onChange={(e) => set('code', e.target.value)}
              placeholder="123456789012" required maxLength={12}
              className="bg-transparent border-0 h-9 text-sm text-white placeholder:text-[#333] focus-visible:ring-0 pl-3 font-mono" />
          </Field>

          {/* Region dropdown */}
          <Field icon={<Globe className="w-3.5 h-3.5" />} label="AWS Region" hint="Deployment region">
            <div className="relative" ref={dropdownRef}>
              <button
                type="button"
                onClick={() => { setDropdownOpen((o) => !o); setRegionSearch(''); }}
                className="w-full flex items-center justify-between pl-3 pr-2 h-9 text-sm text-white focus:outline-none"
              >
                <span className="font-mono text-sm">{selectedRegionLabel}</span>
                <ChevronDown className={`w-3.5 h-3.5 text-[#444] transition-transform ${dropdownOpen ? 'rotate-180' : ''}`} />
              </button>

              {dropdownOpen && (
                <div className="absolute z-50 left-0 right-0 top-full mt-1 bg-[#0d0d0d] border border-[#1a1a1a] rounded-xl shadow-xl overflow-hidden">
                  {/* Search */}
                  <div className="flex items-center gap-2 px-3 py-2 border-b border-[#1a1a1a]">
                    <Search className="w-3.5 h-3.5 text-[#444] shrink-0" />
                    <input
                      autoFocus
                      value={regionSearch}
                      onChange={(e) => setRegionSearch(e.target.value)}
                      placeholder="Search regions…"
                      className="flex-1 bg-transparent text-xs text-white placeholder:text-[#444] focus:outline-none"
                    />
                  </div>
                  {/* List */}
                  <ul className="max-h-52 overflow-y-auto">
                    {filteredRegions.length === 0 ? (
                      <li className="px-4 py-3 text-xs text-[#444]">No regions found</li>
                    ) : (
                      filteredRegions.map((r) => (
                        <li key={r.value}>
                          <button
                            type="button"
                            onClick={() => { set('aws_region', r.value); setDropdownOpen(false); }}
                            className={`w-full text-left px-4 py-2.5 flex items-center justify-between hover:bg-[#111] transition-colors ${formData.aws_region === r.value ? 'text-violet-400' : 'text-[#aaa]'
                              }`}
                          >
                            <span className="text-xs">{r.label}</span>
                            <span className="text-[10px] font-mono text-[#444]">{r.value}</span>
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
            <Field icon={<Cpu className="w-3.5 h-3.5" />} label="Max CPU" hint="vCPU limit" noBorder>
              <Input type="number" step="0.25" min={0.25} value={formData.max_cpu}
                onChange={(e) => set('max_cpu', parseFloat(e.target.value))} required
                className="bg-transparent border-0 h-9 text-sm text-white focus-visible:ring-0 pl-3 font-mono" />
            </Field>
            <Field icon={<HardDrive className="w-3.5 h-3.5" />} label="Max Memory" hint="GB limit">
              <Input type="number" step="0.5" min={0.5} value={formData.max_memory}
                onChange={(e) => set('max_memory', parseFloat(e.target.value))} required
                className="bg-transparent border-0 h-9 text-sm text-white focus-visible:ring-0 pl-3 font-mono" />
            </Field>
          </div>

          <div className="pt-4 flex gap-2">
            <Button type="submit" disabled={loading}
              className="bg-violet-600 hover:bg-violet-700 h-9 text-sm font-medium px-5">
              {loading ? 'Creating…' : 'Create Infrastructure'}
            </Button>
            <Button type="button" variant="outline" onClick={() => router.back()}
              className="border-[#1e1e1e] bg-transparent hover:bg-[#111] text-[#888] h-9 text-sm">
              Cancel
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}

function Field({ icon, label, hint, children, noBorder }: {
  icon: React.ReactNode; label: string; hint?: string; children: React.ReactNode; noBorder?: boolean;
}) {
  return (
    <div className={`bg-[#0d0d0d] border-[#1a1a1a] px-4 py-3 ${noBorder ? 'border border-r-0' : 'border'} first:rounded-t-xl last:rounded-b-xl`}>
      <div className="flex items-center gap-2 mb-1">
        <span className="text-[#444]">{icon}</span>
        <span className="text-[10px] uppercase tracking-widest font-mono text-[#555]">{label}</span>
        {hint && <span className="text-[10px] text-[#333] ml-auto">{hint}</span>}
      </div>
      {children}
    </div>
  );
}
