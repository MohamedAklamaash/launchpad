'use client';

import { Suspense, useState, useEffect } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { motion } from 'framer-motion';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { ArrowLeft, GitBranch, Globe, Box, Cpu, HardDrive, Hash, FileText, Rocket } from 'lucide-react';
import { EnvEditor } from '@/components/env-editor';
import { LaunchSequence, PreflightMeter } from '@/components/launch';
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

const rise = { initial: { opacity: 0, y: 14 }, animate: { opacity: 1, y: 0, transition: { duration: 0.4, ease: [0.16, 1, 0.3, 1] as const } } };

const inputCls = 'bg-transparent border-0 h-9 text-sm placeholder:text-muted-foreground/50 focus-visible:ring-0 pl-0';
const monoInputCls = inputCls + ' font-mono';
const triggerCls = 'bg-transparent border-0 h-9 text-sm text-foreground focus:ring-0 px-0 shadow-none';

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
    build_context: '',
    port: 8080,
    alloted_cpu: 0.25,
    alloted_memory: 0.5,
  });

  const set = (k: string, v: string | number) => setForm((p) => ({ ...p, [k]: v }));

  const preflight = [
    form.infrastructure_id.trim().length > 0,
    /^[a-z0-9]([-a-z0-9]*[a-z0-9])?$/.test(form.name),
    /^https?:\/\/.+/.test(form.project_remote_url.trim()),
    form.port >= 1024 && form.port <= 65535,
  ].filter(Boolean).length;

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
      toast.success(`Liftoff — ${app.name} is deploying`);
      router.push(`/dashboard/applications/${app.id}`);
    } catch (err: unknown) {
      const error = err as { response?: { data?: { error?: string } } };
      toast.error(error.response?.data?.error || 'Failed to deploy');
    } finally {
      setLoading(false);
    }
  };

  return (
    <motion.div {...rise} className="flex justify-center">
      <div className="w-full max-w-xl space-y-6">
        <button onClick={() => router.back()} className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors">
          <ArrowLeft className="w-3.5 h-3.5" /> Back
        </button>

        <div className="rounded-2xl panel p-5">
          <LaunchSequence current="configure" />
        </div>

        <div>
          <span className="eyebrow">Console / Deploy Application</span>
          <h1 className="mt-2 text-2xl font-display font-semibold text-foreground tracking-tight">Deploy an application</h1>
          <p className="text-sm text-muted-foreground mt-1.5">Deploy from a GitHub repository to your infrastructure.</p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-6">
          <Section label="General">
            <Field icon={<Box className="w-3.5 h-3.5" />} label="Infrastructure">
              <Select value={form.infrastructure_id} onValueChange={(v) => v && set('infrastructure_id', v)} required>
                <SelectTrigger className={triggerCls}>
                  <SelectValue placeholder="Select infrastructure" />
                </SelectTrigger>
                <SelectContent className="bg-popover border-hairline">
                  {infrastructures.map((i) => (
                    <SelectItem key={i.id} value={i.id} className="text-sm">{i.name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
            <Field icon={<Hash className="w-3.5 h-3.5" />} label="App Name" hint="lowercase, hyphens only">
              <Input value={form.name} onChange={(e) => set('name', e.target.value.toLowerCase())}
                placeholder="my-app" pattern="[a-z0-9]([-a-z0-9]*[a-z0-9])?" required className={monoInputCls} />
            </Field>
            <Field icon={<FileText className="w-3.5 h-3.5" />} label="Description">
              <Textarea value={form.description} onChange={(e) => set('description', e.target.value)}
                placeholder="Optional description" rows={2}
                className="bg-transparent border-0 text-sm placeholder:text-muted-foreground/50 focus-visible:ring-0 pl-0 resize-none min-h-0" />
            </Field>
          </Section>

          <Section label="Repository">
            <Field icon={<Globe className="w-3.5 h-3.5" />} label="GitHub URL">
              <Input value={form.project_remote_url} onChange={(e) => set('project_remote_url', e.target.value)}
                placeholder="https://github.com/user/repo" required className={monoInputCls} />
            </Field>
            <Field icon={<GitBranch className="w-3.5 h-3.5" />} label="Branch">
              <Input value={form.project_branch} onChange={(e) => set('project_branch', e.target.value)}
                className={monoInputCls} />
            </Field>
            <Field icon={<FileText className="w-3.5 h-3.5" />} label="Dockerfile">
              <Input value={form.dockerfile_path} onChange={(e) => set('dockerfile_path', e.target.value)}
                className={monoInputCls} />
            </Field>
            <Field icon={<FileText className="w-3.5 h-3.5" />} label="Build Context" hint="monorepo root">
              <Input value={form.build_context} onChange={(e) => set('build_context', e.target.value)}
                placeholder="e.g. identity-services/ (leave blank for auto)" className={monoInputCls} />
            </Field>
          </Section>

          <Section label="Resources">
            <Field icon={<Hash className="w-3.5 h-3.5" />} label="Port">
              <Input type="number" value={form.port} onChange={(e) => set('port', parseInt(e.target.value))}
                min={1024} max={65535} className={monoInputCls} />
            </Field>
            <Field icon={<Cpu className="w-3.5 h-3.5" />} label="CPU">
              <Select value={String(form.alloted_cpu)} onValueChange={(v) => {
                if (!v) return;
                const cpu = parseFloat(v);
                set('alloted_cpu', cpu);
                set('alloted_memory', CPU_MEMORY_MAP[cpu][0]);
              }}>
                <SelectTrigger className={triggerCls + ' font-mono'}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent className="bg-popover border-hairline">
                  {Object.keys(CPU_MEMORY_MAP).map((c) => (
                    <SelectItem key={c} value={c} className="text-sm font-mono">{c} vCPU</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
            <Field icon={<HardDrive className="w-3.5 h-3.5" />} label="Memory">
              <Select value={String(form.alloted_memory)} onValueChange={(v) => v && set('alloted_memory', parseFloat(v))}>
                <SelectTrigger className={triggerCls + ' font-mono'}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent className="bg-popover border-hairline">
                  {CPU_MEMORY_MAP[form.alloted_cpu].map((m) => (
                    <SelectItem key={m} value={String(m)} className="text-sm font-mono">{m} GB</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
          </Section>

          <EnvEditor envs={envVars.map(({ key, value }) => [key, value] as [string, string])} onChange={(rows) => setEnvVars(rows.map(([key, value]) => ({ key, value })))} />

          <PreflightMeter ready={preflight} total={4} />

          <div className="flex gap-2 pt-1">
            <Button type="submit" size="lg" disabled={loading} className="px-5 gap-1.5">
              <Rocket className="w-4 h-4" /> {loading ? 'Launching…' : 'Launch application'}
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

export default function NewApplicationPage() {
  return (
    <Suspense>
      <NewApplicationPageInner />
    </Suspense>
  );
}

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <span className="eyebrow">{label}</span>
      <div className="mt-2">{children}</div>
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
