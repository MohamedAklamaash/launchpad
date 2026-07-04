'use client';

import { Suspense, useState, useEffect } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { motion, AnimatePresence } from 'framer-motion';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { ArrowLeft, ArrowRight, GitBranch, Globe, Box, Cpu, HardDrive, Hash, FileText, Rocket, Check } from 'lucide-react';
import { EnvEditor } from '@/components/env-editor';
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

const STEPS = [
  { title: 'General', sub: 'Identity', blurb: 'Name your app and pick where it runs.', icon: Box },
  { title: 'Repository', sub: 'Source', blurb: 'Point us at your GitHub source and how to build it.', icon: GitBranch },
  { title: 'Resources', sub: 'Compute', blurb: 'Size the container it runs in.', icon: Cpu },
  { title: 'Environment', sub: 'Lift-off', blurb: 'Add variables, review, and launch.', icon: Rocket },
] as const;

const rise = { initial: { opacity: 0, y: 14 }, animate: { opacity: 1, y: 0, transition: { duration: 0.4, ease: [0.16, 1, 0.3, 1] as const } } };

const inputCls = 'bg-transparent border-0 h-9 text-sm placeholder:text-muted-foreground/50 focus-visible:ring-0 pl-6';
const monoInputCls = inputCls + ' font-mono';
const triggerCls = 'bg-transparent border-0 h-9 text-sm text-foreground focus:ring-0 pl-6 pr-2 shadow-none';

function NewApplicationPageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const infraId = searchParams.get('infra');

  const [loading, setLoading] = useState(false);
  const [infrastructures, setInfrastructures] = useState<Infrastructure[]>([]);
  const [envVars, setEnvVars] = useState<Array<{ key: string; value: string }>>([]);
  const [step, setStep] = useState(0);
  const [dir, setDir] = useState(1);
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

  const checks = {
    infra: form.infrastructure_id.trim().length > 0,
    name: /^[a-z0-9]([-a-z0-9]*[a-z0-9])?$/.test(form.name),
    url: /^https?:\/\/.+/.test(form.project_remote_url.trim()),
    port: form.port >= 1024 && form.port <= 65535,
  };
  const stepValid = [checks.infra && checks.name, checks.url, checks.port, true];
  const firstInvalid = stepValid.findIndex((v) => !v);
  const gate = firstInvalid === -1 ? STEPS.length - 1 : firstInvalid;
  const readyCount = Object.values(checks).filter(Boolean).length;

  const infraName = infrastructures.find((i) => i.id === form.infrastructure_id)?.name;
  const isLast = step === STEPS.length - 1;

  useEffect(() => {
    if (step > gate) setStep(gate);
  }, [gate, step]);

  useEffect(() => {
    infrastructureApi.list()
      .then((data) => setInfrastructures(data.filter((i) => i.status === 'ACTIVE')))
      .catch(() => toast.error('Failed to load infrastructures', { id: 'app-new-infra-load' }));
  }, []);

  const go = (i: number) => { setDir(i > step ? 1 : -1); setStep(i); };
  const next = () => { if (!isLast && stepValid[step]) go(step + 1); };
  const back = () => { if (step > 0) go(step - 1); else router.back(); };

  const launch = async () => {
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

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (isLast) launch();
    else next();
  };

  return (
    <motion.div {...rise} className="flex justify-center">
      <div className="w-full max-w-xl space-y-6">
        <button onClick={() => router.back()} className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors">
          <ArrowLeft className="w-3.5 h-3.5" /> Back
        </button>

        <div>
          <span className="eyebrow">Console / Deploy Application</span>
          <h1 className="mt-2 text-2xl font-display font-semibold text-foreground tracking-tight">Deploy an application</h1>
          <p className="text-sm text-muted-foreground mt-1.5">Deploy from a GitHub repository to your infrastructure.</p>
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
                  <Field icon={<Box className="w-3.5 h-3.5" />} label="Infrastructure">
                    <Select value={form.infrastructure_id} onValueChange={(v) => v && set('infrastructure_id', v)} required>
                      <SelectTrigger className={triggerCls}><SelectValue placeholder="Select infrastructure" /></SelectTrigger>
                      <SelectContent className="bg-popover border-hairline">
                        {infrastructures.map((i) => (
                          <SelectItem key={i.id} value={i.id} className="text-sm">{i.name}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </Field>
                  <Field icon={<Hash className="w-3.5 h-3.5" />} label="App Name" hint="lowercase, hyphens only">
                    <Input value={form.name} onChange={(e) => set('name', e.target.value.toLowerCase())}
                      placeholder="my-app" pattern="[a-z0-9]([a-z0-9-]*[a-z0-9])?" required className={monoInputCls} />
                  </Field>
                  <Field icon={<FileText className="w-3.5 h-3.5" />} label="Description">
                    <Textarea value={form.description} onChange={(e) => set('description', e.target.value)}
                      placeholder="Optional description" rows={2}
                      className="bg-transparent border-0 text-sm placeholder:text-muted-foreground/50 focus-visible:ring-0 pl-6 resize-none min-h-0" />
                  </Field>
                </div>
              )}

              {step === 1 && (
                <div>
                  <Field icon={<Globe className="w-3.5 h-3.5" />} label="GitHub URL">
                    <Input value={form.project_remote_url} onChange={(e) => set('project_remote_url', e.target.value)}
                      placeholder="https://github.com/user/repo" required className={monoInputCls} />
                  </Field>
                  <Field icon={<GitBranch className="w-3.5 h-3.5" />} label="Branch">
                    <Input value={form.project_branch} onChange={(e) => set('project_branch', e.target.value)} className={monoInputCls} />
                  </Field>
                  <Field icon={<FileText className="w-3.5 h-3.5" />} label="Dockerfile">
                    <Input value={form.dockerfile_path} onChange={(e) => set('dockerfile_path', e.target.value)} className={monoInputCls} />
                  </Field>
                  <Field icon={<FileText className="w-3.5 h-3.5" />} label="Build Context" hint="monorepo root">
                    <Input value={form.build_context} onChange={(e) => set('build_context', e.target.value)}
                      placeholder="e.g. identity-services/ (leave blank for auto)" className={monoInputCls} />
                  </Field>
                </div>
              )}

              {step === 2 && (
                <div>
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
                      <SelectTrigger className={triggerCls + ' font-mono'}><SelectValue /></SelectTrigger>
                      <SelectContent className="bg-popover border-hairline">
                        {Object.keys(CPU_MEMORY_MAP).map((c) => (
                          <SelectItem key={c} value={c} className="text-sm font-mono">{c} vCPU</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </Field>
                  <Field icon={<HardDrive className="w-3.5 h-3.5" />} label="Memory">
                    <Select value={String(form.alloted_memory)} onValueChange={(v) => v && set('alloted_memory', parseFloat(v))}>
                      <SelectTrigger className={triggerCls + ' font-mono'}><SelectValue /></SelectTrigger>
                      <SelectContent className="bg-popover border-hairline">
                        {CPU_MEMORY_MAP[form.alloted_cpu].map((m) => (
                          <SelectItem key={m} value={String(m)} className="text-sm font-mono">{m} GB</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </Field>
                </div>
              )}

              {step === 3 && (
                <div className="space-y-4">
                  <EnvEditor envs={envVars.map(({ key, value }) => [key, value] as [string, string])} onChange={(rows) => setEnvVars(rows.map(([key, value]) => ({ key, value })))} />
                  <div className="rounded-xl panel-inset divide-y divide-hairline">
                    {[
                      { label: 'App', value: form.name || '—' },
                      { label: 'Infrastructure', value: infraName ?? '—' },
                      { label: 'Repository', value: form.project_remote_url || '—' },
                      { label: 'Branch', value: form.project_branch },
                      { label: 'Compute', value: `${form.alloted_cpu} vCPU · ${form.alloted_memory} GB · :${form.port}` },
                    ].map(({ label, value }) => (
                      <div key={label} className="flex items-center justify-between gap-4 px-4 py-2.5">
                        <span className="eyebrow shrink-0">{label}</span>
                        <span className="text-xs text-foreground/80 font-mono truncate">{value}</span>
                      </div>
                    ))}
                  </div>
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
              <span className="ml-2 text-muted-foreground/50">{readyCount}/4 ready</span>
            </span>
            <div className="ml-auto">
              {isLast ? (
                <Button type="submit" size="lg" disabled={loading} className="px-5 gap-1.5">
                  <Rocket className="w-4 h-4" /> {loading ? 'Launching…' : 'Launch application'}
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

export default function NewApplicationPage() {
  return (
    <Suspense>
      <NewApplicationPageInner />
    </Suspense>
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
