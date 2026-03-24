'use client';

import { useState, useEffect, useRef } from 'react';
import { useRouter } from 'next/navigation';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { ArrowLeft, Server, Cpu, HardDrive, Hash, Copy, Check, Terminal, Globe, Search, ChevronDown } from 'lucide-react';
import { infrastructureApi, AwsRegion } from '@/lib/api/infrastructures';
import { toast } from 'sonner';

const SCRIPT_URL = 'https://raw.githubusercontent.com/MohamedAklamaash/launchpad/main/app_scripts/create_aws_role.sh';
const SCRIPT_CMD = `curl -sSL ${SCRIPT_URL} | bash`;

export default function NewInfrastructurePage() {
  const router = useRouter();
  const [loading, setLoading] = useState(false);
  const [copied, setCopied] = useState(false);
  const [regions, setRegions] = useState<AwsRegion[]>([]);
  const [regionSearch, setRegionSearch] = useState('');
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

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
      toast.success('Infrastructure created — provisioning started');
      router.push(`/dashboard/infrastructures/${infra.id}`);
    } catch (err: unknown) {
      const error = err as { response?: { data?: { error?: string } } };
      toast.error(error.response?.data?.error || 'Failed to create infrastructure');
    } finally {
      setLoading(false);
    }
  };

  const copyScript = () => {
    navigator.clipboard.writeText(SCRIPT_CMD);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

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

        {/* AWS Role Script Banner */}
        <div className="bg-[#0d0d0d] border border-[#1a1a1a] rounded-xl p-4 space-y-3">
          <div className="flex items-start gap-3">
            <Terminal className="w-4 h-4 text-amber-400 shrink-0 mt-0.5" />
            <div className="space-y-0.5">
              <p className="text-xs font-medium text-white">AWS IAM Role Setup</p>
              <p className="text-[11px] text-[#555]">
                Run this script to create the required IAM role for Launchpad deployments.{' '}
                <span className="text-[#444]">Do this once when new to the app — skip if already done.</span>
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2 bg-[#060606] border border-[#1e1e1e] rounded-lg px-3 py-2">
            <code className="flex-1 text-[11px] font-mono text-emerald-400 truncate">{SCRIPT_CMD}</code>
            <button onClick={copyScript} className="shrink-0 text-[#444] hover:text-white transition-colors ml-1">
              {copied ? <Check className="w-3.5 h-3.5 text-emerald-500" /> : <Copy className="w-3.5 h-3.5" />}
            </button>
          </div>
          <a href="https://github.com/MohamedAklamaash/launchpad/blob/main/app_scripts/create_aws_role.sh"
            target="_blank" rel="noopener noreferrer"
            className="text-[10px] text-[#444] hover:text-violet-400 transition-colors font-mono">
            View script on GitHub →
          </a>
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
