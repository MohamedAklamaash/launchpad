'use client';

import { useEffect, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { motion } from 'framer-motion';
import { Button } from '@/components/ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Plus, Rocket, Cpu, HardDrive, Network, ArrowUpRight } from 'lucide-react';
import { ApplicationSummary } from '@/types/application';
import { Infrastructure } from '@/types/infrastructure';
import { applicationApi } from '@/lib/api/applications';
import { infrastructureApi } from '@/lib/api/infrastructures';
import { toast } from 'sonner';
import { Suspense } from 'react';

const STATUS: Record<string, { dot: string; label: string }> = {
  ACTIVE: { dot: 'bg-success', label: 'text-success' },
  BUILDING: { dot: 'bg-azure animate-pulse', label: 'text-azure' },
  DEPLOYING: { dot: 'bg-azure animate-pulse', label: 'text-azure' },
  PUSHING_IMAGE: { dot: 'bg-azure animate-pulse', label: 'text-azure' },
  CREATED: { dot: 'bg-warning animate-pulse', label: 'text-warning' },
  SLEEPING: { dot: 'bg-warning', label: 'text-warning' },
  FAILED: { dot: 'bg-destructive', label: 'text-destructive' },
};

const rise = { initial: { opacity: 0, y: 14 }, animate: { opacity: 1, y: 0, transition: { duration: 0.4, ease: [0.16, 1, 0.3, 1] as const } } };

const fade = {
  hidden: { opacity: 0, y: 12 },
  show: (i: number) => ({ opacity: 1, y: 0, transition: { delay: i * 0.05, duration: 0.35, ease: [0.16, 1, 0.3, 1] as const } }),
};

export default function ApplicationsPage() {
  return (
    <Suspense>
      <ApplicationsPageInner />
    </Suspense>
  );
}

function ApplicationsPageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [infrastructures, setInfrastructures] = useState<Infrastructure[]>([]);
  const [selectedInfra, setSelectedInfra] = useState<string>(searchParams.get('infra') || '');
  const [apps, setApps] = useState<ApplicationSummary[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    infrastructureApi.list()
      .then((data) => {
        setInfrastructures(data);
        if (!searchParams.get('infra') && data.length > 0) {
          setSelectedInfra(data[0].id);
          router.replace(`/dashboard/applications?infra=${data[0].id}`);
        }
      })
      .catch(() => toast.error('Failed to load infrastructures'));
  }, [router, searchParams]);

  const handleInfraChange = (id: string) => {
    setSelectedInfra(id);
    router.replace(`/dashboard/applications?infra=${id}`);
  };

  useEffect(() => {
    if (!selectedInfra) return;
    let isActive = true;
    const fetchApps = async () => {
      await Promise.resolve();
      if (isActive) setLoading(true);
      try {
        const data = await applicationApi.list(selectedInfra);
        if (isActive) setApps(data);
      } catch (err: unknown) {
        const error = err as { response?: { data?: { error?: string } } };
        if (isActive) toast.error(error.response?.data?.error || 'Failed to load applications');
      } finally {
        if (isActive) setLoading(false);
      }
    };
    fetchApps();
    return () => { isActive = false; };
  }, [selectedInfra]);

  const deployHref = `/dashboard/applications/new${selectedInfra ? `?infra=${selectedInfra}` : ''}`;
  const active = apps.filter((a) => a.status === 'ACTIVE').length;
  const building = apps.filter((a) => ['BUILDING', 'DEPLOYING', 'PUSHING_IMAGE', 'CREATED'].includes(a.status)).length;
  const sleeping = apps.filter((a) => a.status === 'SLEEPING').length;

  return (
    <motion.div {...rise} className="space-y-10">
      <div className="flex items-end justify-between gap-4">
        <div>
          <span className="eyebrow">Console / Applications</span>
          <h1 className="mt-2 text-3xl font-display font-semibold text-foreground tracking-tight">Applications</h1>
          <p className="text-sm text-muted-foreground mt-1.5">Deploy and manage services running on your environments.</p>
        </div>
        <Button size="lg" onClick={() => router.push(deployHref)} className="gap-1.5">
          <Plus className="w-4 h-4" /> Deploy Application
        </Button>
      </div>

      {infrastructures.length > 0 && (
        <div className="max-w-xs space-y-1.5">
          <span className="eyebrow">Environment</span>
          <Select value={selectedInfra} onValueChange={(v) => v && handleInfraChange(v)}>
            <SelectTrigger className="w-full">
              <SelectValue placeholder="Select infrastructure" />
            </SelectTrigger>
            <SelectContent>
              {infrastructures.map((i) => (
                <SelectItem key={i.id} value={i.id}>{i.name}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )}

      {!loading && apps.length > 0 && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-px rounded-xl overflow-hidden border border-hairline bg-hairline">
          {[
            { label: 'Applications', value: apps.length },
            { label: 'Active', value: active },
            { label: 'Building', value: building },
            { label: 'Sleeping', value: sleeping },
          ].map((stat) => (
            <div key={stat.label} className="bg-surface-1 px-5 py-4">
              <p className="eyebrow">{stat.label}</p>
              <p className="mt-1.5 text-2xl font-display font-semibold text-foreground tabular-nums">{stat.value}</p>
            </div>
          ))}
        </div>
      )}

      {loading ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="h-36 rounded-xl panel animate-pulse" />
          ))}
        </div>
      ) : apps.length === 0 ? (
        <div className="relative overflow-hidden rounded-2xl panel-inset px-8 py-20 text-center">
          <div className="pointer-events-none absolute inset-0 brand-glow opacity-60" />
          <div className="relative">
            <div className="w-14 h-14 rounded-2xl bg-surface-2 border border-hairline-strong flex items-center justify-center mx-auto mb-5">
              <Rocket className="w-6 h-6 text-brand" />
            </div>
            <p className="text-base font-display font-medium text-foreground mb-1.5">No applications yet</p>
            <p className="text-sm text-muted-foreground mb-6 max-w-sm mx-auto">Deploy your first service to this environment and ship it straight to AWS.</p>
            <Button size="lg" onClick={() => router.push(deployHref)} className="gap-1.5">
              <Plus className="w-4 h-4" /> Deploy Application
            </Button>
          </div>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {apps.map((app, i) => {
            const st = STATUS[app.status] ?? { dot: 'bg-muted-foreground', label: 'text-muted-foreground' };
            return (
              <motion.button
                key={app.id}
                custom={i}
                variants={fade}
                initial="hidden"
                animate="show"
                onClick={() => router.push(`/dashboard/applications/${app.id}`)}
                className="group text-left relative overflow-hidden rounded-xl panel p-5 transition-colors hover:border-brand/30 hover:bg-surface-2 outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
              >
                <div className="flex items-start justify-between mb-5">
                  <span className="w-10 h-10 rounded-lg bg-surface-3 border border-hairline flex items-center justify-center">
                    <Rocket className="w-4 h-4 text-muted-foreground" />
                  </span>
                  <span className="flex items-center gap-1.5">
                    <span className={`w-1.5 h-1.5 rounded-full ${st.dot}`} />
                    <span className={`font-mono text-[10px] uppercase tracking-[0.12em] ${st.label}`}>{app.status}</span>
                  </span>
                </div>
                <h3 className="text-[15px] font-medium text-foreground mb-4 truncate flex items-center gap-1.5">
                  {app.name}
                  <ArrowUpRight className="w-3.5 h-3.5 text-muted-foreground opacity-0 -translate-x-1 transition-all group-hover:opacity-100 group-hover:translate-x-0" />
                </h3>
                <div className="flex items-center gap-4 text-xs text-muted-foreground font-mono">
                  <span className="flex items-center gap-1.5"><Cpu className="w-3.5 h-3.5" />{app.cpu} vCPU</span>
                  <span className="flex items-center gap-1.5"><HardDrive className="w-3.5 h-3.5" />{app.memory} GB</span>
                  <span className="flex items-center gap-1.5 ml-auto"><Network className="w-3.5 h-3.5" />{app.port}</span>
                </div>
              </motion.button>
            );
          })}
        </div>
      )}
    </motion.div>
  );
}
