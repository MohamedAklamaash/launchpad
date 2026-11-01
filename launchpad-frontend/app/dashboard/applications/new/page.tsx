'use client';

import { Suspense, useState, useEffect } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Label } from '@/components/ui/label';
import { PageHeader } from '@/components/layout/page-header';
import { cn } from '@/lib/utils';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { ArrowLeft, ArrowRight, Check } from 'lucide-react';
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

const EKS_CPU_MIN = 0.25;
const EKS_CPU_MAX = 16;
const EKS_MEMORY_MIN = 0.5;
const EKS_MEMORY_MAX = 64;

const STEPS = [
  { title: 'General', blurb: 'Name your app and pick the AWS account it runs in.' },
  { title: 'Repository', blurb: 'Point Launchpad at your GitHub repository and say how to build it.' },
  { title: 'Resources', blurb: 'Size the container the app runs in.' },
  { title: 'Environment', blurb: 'Add environment variables, review, and deploy.' },
] as const;

function NewApplicationPageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const infraId = searchParams.get('infra');

  const [loading, setLoading] = useState(false);
  const [infrastructures, setInfrastructures] = useState<Infrastructure[]>([]);
  const [envVars, setEnvVars] = useState<Array<{ key: string; value: string }>>([]);
  const [step, setStep] = useState(0);
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

  const selectedInfra = infrastructures.find((i) => i.id === form.infrastructure_id);
  const isEks = selectedInfra?.compute_type === 'eks';

  const checks = {
    infra: form.infrastructure_id.trim().length > 0,
    name: /^[a-z0-9]([-a-z0-9]*[a-z0-9])?$/.test(form.name),
    url: /^https?:\/\/.+/.test(form.project_remote_url.trim()),
    port: form.port >= 1024 && form.port <= 65535,
    cpu: isEks
      ? form.alloted_cpu >= EKS_CPU_MIN && form.alloted_cpu <= EKS_CPU_MAX
      : form.alloted_cpu in CPU_MEMORY_MAP,
    memory: isEks
      ? form.alloted_memory >= EKS_MEMORY_MIN && form.alloted_memory <= EKS_MEMORY_MAX
      : (CPU_MEMORY_MAP[form.alloted_cpu] ?? []).includes(form.alloted_memory),
  };
  const stepValid = [checks.infra && checks.name, checks.url, checks.port && checks.cpu && checks.memory, true];
  const firstInvalid = stepValid.findIndex((v) => !v);
  const gate = firstInvalid === -1 ? STEPS.length - 1 : firstInvalid;

  const infraName = selectedInfra?.name;
  const isLast = step === STEPS.length - 1;

  useEffect(() => {
    if (step > gate) setStep(gate);
  }, [gate, step]);

  useEffect(() => {
    infrastructureApi.list()
      // H2: an exited infrastructure refuses application-create with a 409 — never offer
      // it as a target here.
      .then((data) => {
        const eligible = data.filter((i) => i.status === 'ACTIVE' && !i.exited_at);
        setInfrastructures(eligible);
        // A pre-filled ?infra= query param (deep link) may point at one filtered out above
        // (exited since the link was shared) — clear it so the form can't be submitted
        // against a target the picker never showed as an option.
        if (infraId && !eligible.some((i) => i.id === infraId)) {
          set('infrastructure_id', '');
        }
      })
      .catch(() => toast.error('Failed to load infrastructures', { id: 'app-new-infra-load' }));
    // eslint-disable-next-line react-hooks/exhaustive-deps -- run once on mount only, matching every other effect in this file; infraId is read from the initial URL and does not need to re-trigger this fetch.
  }, []);

  const selectInfra = (id: string) => {
    set('infrastructure_id', id);
    const infra = infrastructures.find((i) => i.id === id);
    if (infra?.compute_type === 'eks') return;
    const ladder = CPU_MEMORY_MAP[form.alloted_cpu];
    if (!ladder) {
      set('alloted_cpu', 0.25);
      set('alloted_memory', 0.5);
    } else if (!ladder.includes(form.alloted_memory)) {
      set('alloted_memory', ladder[0]);
    }
  };

  const go = (i: number) => setStep(i);
  const next = () => { if (!isLast && stepValid[step]) go(step + 1); };
  const back = () => { if (step > 0) go(step - 1); else router.back(); };

  const launch = async () => {
    setLoading(true);
    try {
      const envs = envVars.reduce((acc, { key, value }) => { if (key) acc[key] = value; return acc; }, {} as Record<string, string>);
      const app = await applicationApi.create({ ...form, envs });
      toast.success(`${app.name} is deploying.`);
      router.push(`/dashboard/applications/${app.id}`);
    } catch (err: unknown) {
      const error = err as { response?: { data?: { error?: string } } };
      toast.error(error.response?.data?.error || 'Could not deploy the application.');
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
    <div className="reveal mx-auto w-full max-w-xl space-y-6">
        <button onClick={() => router.back()} className="flex items-center gap-1.5 rounded-md text-xs text-muted-foreground transition-colors hover:text-foreground">
          <ArrowLeft className="size-3.5" /> Back
        </button>

        <PageHeader title="Deploy an application" description="Build and deploy from a GitHub repository." />

        <Stepper current={step} gate={gate} onSelect={go} />

        <form onSubmit={onSubmit} className="space-y-6">
            <div key={step} className="reveal space-y-4 rounded-lg border border-border bg-card p-5">
              <div>
                <h2 className="text-sm font-medium text-foreground">{STEPS[step].title}</h2>
                <p className="text-xs text-muted-foreground mt-0.5">{STEPS[step].blurb}</p>
              </div>

              {step === 0 && (
                <div className="space-y-4">
                  <Field id="app-infrastructure" label="Infrastructure">
                    <Select value={form.infrastructure_id} onValueChange={(v) => v && selectInfra(v)} required>
                      <SelectTrigger id="app-infrastructure" className="w-full"><SelectValue placeholder="Select infrastructure">{infraName}</SelectValue></SelectTrigger>
                      <SelectContent>
                        {infrastructures.map((i) => (
                          <SelectItem key={i.id} value={i.id}>{i.name}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </Field>
                  <Field id="app-app-name" label="App Name" hint="lowercase, hyphens only">
                    <Input id="app-app-name" value={form.name} onChange={(e) => set('name', e.target.value.toLowerCase())}
                      placeholder="my-app" pattern="[a-z0-9]([a-z0-9-]*[a-z0-9])?" required className="font-mono" />
                  </Field>
                  <Field id="app-description" label="Description">
                    <Textarea id="app-description" value={form.description} onChange={(e) => set('description', e.target.value)}
                      placeholder="Optional description" rows={2}
                      className="resize-none" />
                  </Field>
                </div>
              )}

              {step === 1 && (
                <div className="space-y-4">
                  <Field id="app-github-url" label="GitHub URL">
                    <Input id="app-github-url" value={form.project_remote_url} onChange={(e) => set('project_remote_url', e.target.value)}
                      placeholder="https://github.com/user/repo" required className="font-mono" />
                  </Field>
                  <Field id="app-branch" label="Branch">
                    <Input id="app-branch" value={form.project_branch} onChange={(e) => set('project_branch', e.target.value)} className="font-mono" />
                  </Field>
                  <Field id="app-dockerfile" label="Dockerfile">
                    <Input id="app-dockerfile" value={form.dockerfile_path} onChange={(e) => set('dockerfile_path', e.target.value)} className="font-mono" />
                  </Field>
                  <Field id="app-build-context" label="Build Context" hint="monorepo root">
                    <Input id="app-build-context" value={form.build_context} onChange={(e) => set('build_context', e.target.value)}
                      placeholder="e.g. identity-services/ (leave blank for auto)" className="font-mono" />
                  </Field>
                </div>
              )}

              {step === 2 && (
                <div className="space-y-4">
                  <Field id="app-port" label="Port">
                    <Input id="app-port" type="number" value={Number.isNaN(form.port) ? '' : form.port}
                      onChange={(e) => set('port', e.target.value === '' ? NaN : parseInt(e.target.value, 10))}
                      min={1024} max={65535} className="font-mono" />
                  </Field>
                  {isEks ? (
                    <>
                      <Field id="app-cpu" label="CPU" hint={`${EKS_CPU_MIN} - ${EKS_CPU_MAX} vCPU`}>
                        <Input id="app-cpu" type="number" step="0.25" min={EKS_CPU_MIN} max={EKS_CPU_MAX}
                          value={Number.isNaN(form.alloted_cpu) ? '' : form.alloted_cpu}
                          onChange={(e) => set('alloted_cpu', e.target.value === '' ? NaN : parseFloat(e.target.value))}
                          className="font-mono" />
                      </Field>
                      <Field id="app-memory" label="Memory" hint={`${EKS_MEMORY_MIN} - ${EKS_MEMORY_MAX} GB`}>
                        <Input id="app-memory" type="number" step="0.5" min={EKS_MEMORY_MIN} max={EKS_MEMORY_MAX}
                          value={Number.isNaN(form.alloted_memory) ? '' : form.alloted_memory}
                          onChange={(e) => set('alloted_memory', e.target.value === '' ? NaN : parseFloat(e.target.value))}
                          className="font-mono" />
                      </Field>
                    </>
                  ) : (
                    <>
                      <Field id="app-cpu" label="CPU">
                        <Select value={String(form.alloted_cpu)} onValueChange={(v) => {
                          if (!v) return;
                          const cpu = parseFloat(v);
                          set('alloted_cpu', cpu);
                          set('alloted_memory', CPU_MEMORY_MAP[cpu][0]);
                        }}>
                          <SelectTrigger id="app-cpu" className="w-full font-mono"><SelectValue /></SelectTrigger>
                          <SelectContent>
                            {Object.keys(CPU_MEMORY_MAP).map((c) => (
                              <SelectItem key={c} value={c} className="font-mono">{c} vCPU</SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      </Field>
                      <Field id="app-memory" label="Memory">
                        <Select value={String(form.alloted_memory)} onValueChange={(v) => v && set('alloted_memory', parseFloat(v))}>
                          <SelectTrigger id="app-memory" className="w-full font-mono"><SelectValue /></SelectTrigger>
                          <SelectContent>
                            {(CPU_MEMORY_MAP[form.alloted_cpu] ?? []).map((m) => (
                              <SelectItem key={m} value={String(m)} className="font-mono">{m} GB</SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      </Field>
                    </>
                  )}
                </div>
              )}

              {step === 3 && (
                <div className="space-y-4">
                  <EnvEditor envs={envVars.map(({ key, value }) => [key, value] as [string, string])} onChange={(rows) => setEnvVars(rows.map(([key, value]) => ({ key, value })))} />
                  <div className="rounded-lg border border-border bg-surface-1 divide-y divide-border">
                    {[
                      { label: 'App', value: form.name || 'Not set' },
                      { label: 'Infrastructure', value: infraName ?? 'Not set' },
                      { label: 'Repository', value: form.project_remote_url || 'Not set' },
                      { label: 'Branch', value: form.project_branch },
                      { label: 'Compute', value: `${form.alloted_cpu} vCPU · ${form.alloted_memory} GB · :${form.port}` },
                    ].map(({ label, value }) => (
                      <div key={label} className="flex items-center justify-between gap-4 px-4 py-2.5">
                        <span className="shrink-0 text-xs text-muted-foreground">{label}</span>
                        <span className="truncate font-mono text-xs text-foreground">{value}</span>
                      </div>
                    ))}
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
                <Button type="submit" size="lg" disabled={loading}>
                  {loading ? 'Deploying…' : 'Deploy application'}
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

export default function NewApplicationPage() {
  return (
    <Suspense>
      <NewApplicationPageInner />
    </Suspense>
  );
}

function Stepper({ current, gate, onSelect }: { current: number; gate: number; onSelect: (i: number) => void }) {
  return (
    <ol className="flex items-center" aria-label="Steps">
      {STEPS.map((st, i) => {
        const state = i < current ? 'done' : i === current ? 'active' : 'todo';
        const reachable = i <= gate;
        return (
          <li key={st.title} aria-current={state === 'active' ? 'step' : undefined} className={cn('flex items-center', i < STEPS.length - 1 && 'flex-1')}>
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
              <span className={cn('hidden text-xs sm:block', state === 'todo' ? 'text-muted-foreground' : 'text-foreground')}>{st.title}</span>
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
