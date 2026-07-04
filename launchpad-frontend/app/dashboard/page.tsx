'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { motion } from 'framer-motion';
import { Button } from '@/components/ui/button';
import { Plus, Server, Cpu, HardDrive, ArrowUpRight, Rocket, Radio, Gauge, Trophy } from 'lucide-react';
import { Infrastructure } from '@/types/infrastructure';
import { Application } from '@/types/application';
import { infrastructureApi } from '@/lib/api/infrastructures';
import { applicationApi } from '@/lib/api/applications';
import { useAuthStore } from '@/lib/store/auth';
import { toast } from 'sonner';

const STATUS: Record<string, { dot: string; label: string }> = {
  ACTIVE: { dot: 'bg-success', label: 'text-success' },
  PROVISIONING: { dot: 'bg-azure animate-pulse', label: 'text-azure' },
  PENDING: { dot: 'bg-warning animate-pulse', label: 'text-warning' },
  ERROR: { dot: 'bg-destructive', label: 'text-destructive' },
};

const fade = {
  hidden: { opacity: 0, y: 12 },
  show: (i: number) => ({ opacity: 1, y: 0, transition: { delay: i * 0.05, duration: 0.35, ease: [0.16, 1, 0.3, 1] as const } }),
};

type Rank = { at: number; name: string };

const MILESTONES = {
  environments: [
    { at: 1, name: 'First Contact' },
    { at: 3, name: 'Fleet Pilot' },
    { at: 6, name: 'Fleet Commander' },
    { at: 12, name: 'Fleet Admiral' },
  ] as Rank[],
  launched: [
    { at: 1, name: 'First Launch' },
    { at: 5, name: 'Operator' },
    { at: 15, name: 'Flight Director' },
    { at: 40, name: 'Mission Commander' },
  ] as Rank[],
};

function CountUp({ value }: { value: number }) {
  const [n, setN] = useState(0);
  useEffect(() => {
    const reduce = typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const dur = reduce ? 0 : 900;
    let raf = 0;
    const start = performance.now();
    const tick = (t: number) => {
      const p = dur === 0 ? 1 : Math.min(1, (t - start) / dur);
      const eased = 1 - Math.pow(1 - p, 3);
      setN(Math.round(value * eased));
      if (p < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [value]);
  return <>{n}</>;
}

function Milestone({ icon, label, value, unit, ranks, tone }: {
  icon: React.ReactNode; label: string; value: number; unit?: string; ranks?: Rank[]; tone: 'brand' | 'success' | 'azure' | 'warning';
}) {
  const toneText = { brand: 'text-brand', success: 'text-success', azure: 'text-azure', warning: 'text-warning' }[tone];
  const toneBar = { brand: 'bg-brand', success: 'bg-success', azure: 'bg-azure', warning: 'bg-warning' }[tone];

  let rankLine = null;
  let pct = 100;
  if (ranks) {
    const earned = [...ranks].reverse().find((r) => value >= r.at);
    const next = ranks.find((r) => value < r.at);
    const prevAt = earned?.at ?? 0;
    pct = next ? Math.min(100, Math.round(((value - prevAt) / (next.at - prevAt)) * 100)) : 100;
    rankLine = (
      <div className="mt-3 space-y-1.5">
        <div className="flex items-center justify-between">
          <span className="flex items-center gap-1 text-[11px] font-medium text-foreground">
            <Trophy className={`w-3 h-3 ${toneText}`} />
            {earned?.name ?? 'Recruit'}
          </span>
          <span className="font-mono text-[10px] text-muted-foreground">
            {next ? `${next.at - value} to ${next.name}` : 'Max rank'}
          </span>
        </div>
        <div className="h-1 overflow-hidden rounded-full bg-surface-3">
          <motion.div className={`h-full rounded-full ${toneBar}`} initial={{ width: 0 }} animate={{ width: `${pct}%` }} transition={{ duration: 0.9, ease: [0.16, 1, 0.3, 1] }} />
        </div>
      </div>
    );
  }

  return (
    <div className="rounded-xl panel p-4">
      <div className="flex items-center justify-between">
        <span className="eyebrow">{label}</span>
        <span className={`flex h-7 w-7 items-center justify-center rounded-lg bg-surface-3 ${toneText}`}>{icon}</span>
      </div>
      <p className="mt-2 text-3xl font-display font-semibold text-foreground tabular-nums leading-none">
        <CountUp value={value} />{unit && <span className="ml-1 text-base font-medium text-muted-foreground">{unit}</span>}
      </p>
      {rankLine}
    </div>
  );
}

export default function DashboardPage() {
  const router = useRouter();
  const user = useAuthStore((s) => s.user);
  const [infrastructures, setInfrastructures] = useState<Infrastructure[]>([]);
  const [apps, setApps] = useState<Application[]>([]);
  const [loading, setLoading] = useState(true);

  const isSuperAdmin = user?.role === 'super_admin';

  useEffect(() => {
    const load = async () => {
      try {
        const data = await infrastructureApi.list();
        setInfrastructures(data);
        setLoading(false);
        const appLists = await Promise.all(data.map((i) => applicationApi.list(i.id).catch(() => [] as Application[])));
        setApps(appLists.flat());
      } catch (error: unknown) {
        const err = error as { response?: { data?: { error?: string } } };
        toast.error(err.response?.data?.error || 'Failed to load infrastructures');
        setLoading(false);
      }
    };
    load();
  }, []);

  const activeEnv = infrastructures.filter((i) => i.status === 'ACTIVE').length;
  const liveApps = apps.filter((a) => a.status === 'ACTIVE').length;
  const totalCpu = infrastructures.reduce((s, i) => s + Number(i.max_cpu || 0), 0);

  return (
    <div className="space-y-10">
      <div className="flex items-end justify-between gap-4">
        <div>
          <span className="eyebrow">Console / Overview</span>
          <h1 className="mt-2 text-3xl font-display font-semibold text-foreground tracking-tight">
            {user?.user_name ? `Welcome back, ${user.user_name.split(' ')[0]}` : 'Overview'}
          </h1>
          <p className="text-sm text-muted-foreground mt-1.5">
            {isSuperAdmin ? 'Manage your cloud environments and deployments.' : `${infrastructures.length} environment${infrastructures.length !== 1 ? 's' : ''} available to you.`}
          </p>
        </div>
        {isSuperAdmin && (
          <Button size="lg" onClick={() => router.push('/dashboard/infrastructures/new')} className="gap-1.5">
            <Plus className="w-4 h-4" /> New Infrastructure
          </Button>
        )}
      </div>

      {!loading && infrastructures.length > 0 && (
        <div>
          <div className="mb-3 flex items-center gap-2">
            <Trophy className="w-3.5 h-3.5 text-brand" />
            <span className="eyebrow">Mission milestones</span>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
            <Milestone tone="brand" icon={<Server className="w-4 h-4" />} label="Environments" value={infrastructures.length} ranks={MILESTONES.environments} />
            <Milestone tone="azure" icon={<Rocket className="w-4 h-4" />} label="Apps launched" value={apps.length} ranks={MILESTONES.launched} />
            <Milestone tone="success" icon={<Radio className="w-4 h-4" />} label="In orbit" value={liveApps + activeEnv} unit="live" />
            <Milestone tone="warning" icon={<Gauge className="w-4 h-4" />} label="Compute" value={totalCpu} unit="vCPU" />
          </div>
        </div>
      )}

      {loading ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="h-40 rounded-xl panel animate-pulse" />
          ))}
        </div>
      ) : infrastructures.length === 0 ? (
        <div className="relative overflow-hidden rounded-2xl panel-inset px-8 py-20 text-center">
          <div className="pointer-events-none absolute inset-0 brand-glow opacity-60" />
          <div className="relative">
            <div className="w-14 h-14 rounded-2xl bg-surface-2 border border-hairline-strong flex items-center justify-center mx-auto mb-5">
              <Server className="w-6 h-6 text-brand" />
            </div>
            <p className="text-base font-display font-medium text-foreground mb-1.5">No infrastructure yet</p>
            {isSuperAdmin ? (
              <>
                <p className="text-sm text-muted-foreground mb-6 max-w-sm mx-auto">Provision your first cloud environment to start shipping applications to AWS.</p>
                <Button size="lg" onClick={() => router.push('/dashboard/infrastructures/new')} className="gap-1.5">
                  <Plus className="w-4 h-4" /> Create Infrastructure
                </Button>
              </>
            ) : (
              <p className="text-sm text-muted-foreground">You haven&apos;t been added to any infrastructure yet.</p>
            )}
          </div>
        </div>
      ) : (
        <div>
          <div className="mb-3 flex items-center gap-2">
            <Server className="w-3.5 h-3.5 text-muted-foreground" />
            <span className="eyebrow">Environments</span>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {infrastructures.map((infra, i) => {
              const st = STATUS[infra.status] ?? { dot: 'bg-muted-foreground', label: 'text-muted-foreground' };
              return (
                <motion.button
                  key={infra.id}
                  custom={i}
                  variants={fade}
                  initial="hidden"
                  animate="show"
                  onClick={() => router.push(`/dashboard/infrastructures/${infra.id}`)}
                  className="group text-left relative overflow-hidden rounded-xl panel p-5 transition-colors hover:border-brand/30 hover:bg-surface-2 outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
                >
                  <div className="flex items-start justify-between mb-5">
                    <span className="w-10 h-10 rounded-lg bg-surface-3 border border-hairline flex items-center justify-center">
                      <Server className="w-4.5 h-4.5 text-muted-foreground" />
                    </span>
                    <span className="flex items-center gap-1.5">
                      <span className={`w-1.5 h-1.5 rounded-full ${st.dot}`} />
                      <span className={`font-mono text-[10px] uppercase tracking-[0.12em] ${st.label}`}>{infra.status}</span>
                    </span>
                  </div>
                  <h3 className="text-[15px] font-medium text-foreground mb-4 truncate flex items-center gap-1.5">
                    {infra.name}
                    <ArrowUpRight className="w-3.5 h-3.5 text-muted-foreground opacity-0 -translate-x-1 transition-all group-hover:opacity-100 group-hover:translate-x-0" />
                  </h3>
                  <div className="flex items-center gap-4 text-xs text-muted-foreground font-mono">
                    <span className="flex items-center gap-1.5"><Cpu className="w-3.5 h-3.5" />{infra.max_cpu} vCPU</span>
                    <span className="flex items-center gap-1.5"><HardDrive className="w-3.5 h-3.5" />{infra.max_memory} GB</span>
                    <span className="ml-auto uppercase tracking-wide text-muted-foreground/70">{infra.cloud_provider}</span>
                  </div>
                </motion.button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
