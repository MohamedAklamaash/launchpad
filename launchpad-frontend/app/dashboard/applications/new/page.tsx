'use client';

import { Suspense, useState, useEffect } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { ArrowLeft, GitBranch, Globe, Box, Cpu, HardDrive, Hash, FileText, Plus, X } from 'lucide-react';
import { applicationApi } from '@/lib/api/applications';
import { infrastructureApi } from '@/lib/api/infrastructures';
import { Infrastructure } from '@/types/infrastructure';
import { toast } from 'sonner';

const CPU_MEMORY_MAP: Record<number, number[]> = {
  0.25: [0.5, 1, 2],
  0.5: [1, 2, 3, 4],
  1: [2, 3, 4, 5, 6, 7, 8],
  2: [4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16],
  4: [8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30],
};

const inputCls = "bg-transparent border-0 h-9 text-sm text-white placeholder:text-[#333] focus-visible:ring-0 pl-3";
const monoInputCls = inputCls + " font-mono";

function NewApplicationPageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const infraId = searchParams.get('infra');

  const [loading, setLoading] = useState(false);
  const [infrastructures, setInfrastructures] = useState<Infrastructure[]>([]);
  const [envVars, setEnvVars] = useState<Array<{ key: string; value: string }>>([]);
  const [form, setForm] = useState({
    infrastructure_id: infraId || '',
    name: '',
    description: '',
    project_remote_url: '',
    project_branch: 'main',
    dockerfile_path: 'Dockerfile',
    port: 8080,
    alloted_cpu: 0.25,
    alloted_memory: 0.5,
  });

  const set = (k: string, v: string | number) => setForm((p) => ({ ...p, [k]: v }));

  useEffect(() => {
    infrastructureApi.list()
      .then((data) => setInfrastructures(data.filter((i) => i.status === 'ACTIVE')))
      .catch(() => toast.error('Failed to load infrastructures'));
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    try {
      const envs = envVars.reduce((acc, { key, value }) => { if (key) acc[key] = value; return acc; }, {} as Record<string, string>);
      const app = await applicationApi.create({ ...form, envs });
      toast.success('Deployment started');
      router.push(`/dashboard/applications/${app.id}`);
    } catch (err: unknown) {
      const error = err as { response?: { data?: { error?: string } } };
      toast.error(error.response?.data?.error || 'Failed to deploy');
    } finally {
      setLoading(false);
    }
  };

  const updateEnv = (i: number, field: 'key' | 'value', val: string) =>
    setEnvVars((prev) => prev.map((e, idx) => idx === i ? { ...e, [field]: val } : e));

  return (
    <div className="flex justify-center">
      <div className="w-full max-w-xl space-y-6">
        <button onClick={() => router.back()} className="flex items-center gap-1.5 text-xs text-[#555] hover:text-[#aaa] transition-colors">
          <ArrowLeft className="w-3.5 h-3.5" /> Back
        </button>

        <div>
          <h1 className="text-xl font-semibold text-white tracking-tight">Deploy Application</h1>
          <p className="text-xs text-[#555] mt-1">Deploy from a GitHub repository to your infrastructure</p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          {/* Infrastructure + Name */}
          <Section label="General">
            <Row label="Infrastructure" icon={<Box className="w-3.5 h-3.5" />}>
              <Select value={form.infrastructure_id} onValueChange={(v) => v && set('infrastructure_id', v)} required>
                <SelectTrigger className="bg-transparent border-0 h-9 text-sm text-white focus:ring-0 px-0 shadow-none">
                  <SelectValue placeholder="Select infrastructure" />
                </SelectTrigger>
                <SelectContent className="bg-[#0f0f0f] border-[#1a1a1a]">
                  {infrastructures.map((i) => (
                    <SelectItem key={i.id} value={i.id} className="text-sm">{i.name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Row>
            <Row label="App Name" icon={<Hash className="w-3.5 h-3.5" />} hint="lowercase, hyphens only">
              <Input value={form.name} onChange={(e) => set('name', e.target.value.toLowerCase())}
                placeholder="my-app" pattern="[a-z0-9]([-a-z0-9]*[a-z0-9])?" required className={monoInputCls} />
            </Row>
            <Row label="Description" icon={<FileText className="w-3.5 h-3.5" />}>
              <Textarea value={form.description} onChange={(e) => set('description', e.target.value)}
                placeholder="Optional description" rows={2}
                className="bg-transparent border-0 text-sm text-white placeholder:text-[#333] focus-visible:ring-0 px-0 resize-none min-h-0" />
            </Row>
          </Section>

          {/* Repository */}
          <Section label="Repository">
            <Row label="GitHub URL" icon={<Globe className="w-3.5 h-3.5" />}>
              <Input value={form.project_remote_url} onChange={(e) => set('project_remote_url', e.target.value)}
                placeholder="https://github.com/user/repo" required className={monoInputCls} />
            </Row>
            <Row label="Branch" icon={<GitBranch className="w-3.5 h-3.5" />}>
              <Input value={form.project_branch} onChange={(e) => set('project_branch', e.target.value)}
                className={monoInputCls} />
            </Row>
            <Row label="Dockerfile" icon={<FileText className="w-3.5 h-3.5" />}>
              <Input value={form.dockerfile_path} onChange={(e) => set('dockerfile_path', e.target.value)}
                className={monoInputCls} />
            </Row>
          </Section>

          {/* Resources */}
          <Section label="Resources">
            <Row label="Port" icon={<Hash className="w-3.5 h-3.5" />}>
              <Input type="number" value={form.port} onChange={(e) => set('port', parseInt(e.target.value))}
                min={1024} max={65535} className={monoInputCls} />
            </Row>
            <Row label="CPU" icon={<Cpu className="w-3.5 h-3.5" />} wide>
              <Select value={String(form.alloted_cpu)} onValueChange={(v) => {
                if (!v) return;
                const cpu = parseFloat(v);
                set('alloted_cpu', cpu);
                set('alloted_memory', CPU_MEMORY_MAP[cpu][0]);
              }}>
                <SelectTrigger className="bg-[#111] border border-[#1a1a1a] h-9 text-sm text-white focus:ring-0 px-3 shadow-none w-40 rounded-lg">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent className="bg-[#0f0f0f] border-[#1a1a1a]">
                  {Object.keys(CPU_MEMORY_MAP).map((c) => (
                    <SelectItem key={c} value={c} className="text-sm font-mono">{c} vCPU</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Row>
            <Row label="Memory" icon={<HardDrive className="w-3.5 h-3.5" />} wide>
              <Select value={String(form.alloted_memory)} onValueChange={(v) => v && set('alloted_memory', parseFloat(v))}>
                <SelectTrigger className="bg-[#111] border border-[#1a1a1a] h-9 text-sm text-white focus:ring-0 px-3 shadow-none w-40 rounded-lg">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent className="bg-[#0f0f0f] border-[#1a1a1a]">
                  {CPU_MEMORY_MAP[form.alloted_cpu].map((m) => (
                    <SelectItem key={m} value={String(m)} className="text-sm font-mono">{m} GB</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Row>
          </Section>

          {/* Env vars */}
          <Section label="Environment Variables" action={
            <button type="button" onClick={() => setEnvVars([...envVars, { key: '', value: '' }])}
              className="flex items-center gap-1 text-[10px] text-[#555] hover:text-[#aaa] transition-colors font-mono uppercase tracking-widest">
              <Plus className="w-3 h-3" /> Add
            </button>
          }>
            {envVars.length === 0 ? (
              <div className="px-4 py-3 text-xs text-[#333]">No environment variables</div>
            ) : (
              envVars.map((env, i) => (
                <div key={i} className={`flex items-center gap-0 ${i < envVars.length - 1 ? 'border-b border-[#1a1a1a]' : ''}`}>
                  <div className="flex-1 px-4 py-2 border-r border-[#1a1a1a]">
                    <Input placeholder="KEY" value={env.key}
                      onChange={(e) => updateEnv(i, 'key', e.target.value.toUpperCase())}
                      className="bg-transparent border-0 h-8 text-xs text-violet-400 placeholder:text-[#333] focus-visible:ring-0 pl-3 font-mono" />
                  </div>
                  <div className="flex-1 px-4 py-2">
                    <Input placeholder="value" value={env.value}
                      onChange={(e) => updateEnv(i, 'value', e.target.value)}
                      className="bg-transparent border-0 h-8 text-xs text-[#aaa] placeholder:text-[#333] focus-visible:ring-0 pl-3 font-mono" />
                  </div>
                  <button type="button" onClick={() => setEnvVars(envVars.filter((_, idx) => idx !== i))}
                    className="px-3 text-[#333] hover:text-red-400 transition-colors shrink-0">
                    <X className="w-3.5 h-3.5" />
                  </button>
                </div>
              ))
            )}
          </Section>

          <div className="flex gap-2 pt-1">
            <Button type="submit" disabled={loading}
              className="bg-violet-600 hover:bg-violet-700 h-9 text-sm font-medium px-5">
              {loading ? 'Deploying…' : 'Deploy Application'}
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

export default function NewApplicationPage() {
  return (
    <Suspense>
      <NewApplicationPageInner />
    </Suspense>
  );
}

function Section({ label, children, action }: { label: string; children: React.ReactNode; action?: React.ReactNode }) {
  return (
    <div>
      <div className="flex items-center justify-between mb-1.5 px-0.5">
        <span className="text-[10px] uppercase tracking-widest font-mono text-[#555]">{label}</span>
        {action}
      </div>
      <div className="bg-[#0d0d0d] border border-[#1a1a1a] rounded-xl overflow-hidden divide-y divide-[#1a1a1a]">
        {children}
      </div>
    </div>
  );
}

function Row({ label, icon, hint, children, wide }: {
  label: string; icon: React.ReactNode; hint?: string; children: React.ReactNode; wide?: boolean;
}) {
  return (
    <div className="flex items-start gap-3 px-4 py-2.5">
      <div className={`flex items-center gap-2 ${wide ? 'w-40' : 'w-32'} shrink-0 pt-2`}>
        <span className="text-[#444]">{icon}</span>
        <span className="text-xs text-[#555]">{label}</span>
        {hint && <span className="text-[10px] text-[#333] hidden xl:block">{hint}</span>}
      </div>
      <div className="flex-1 min-w-0">{children}</div>
    </div>
  );
}
