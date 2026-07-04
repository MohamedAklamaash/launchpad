'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { motion } from 'framer-motion';
import { Button } from '@/components/ui/button';
import { Plus, Server, Cpu, HardDrive, ArrowUpRight } from 'lucide-react';
import { Infrastructure } from '@/types/infrastructure';
import { infrastructureApi } from '@/lib/api/infrastructures';
import { toast } from 'sonner';

const STATUS: Record<string, { dot: string; label: string }> = {
  ACTIVE: { dot: 'bg-success', label: 'text-success' },
  PROVISIONING: { dot: 'bg-azure animate-pulse', label: 'text-azure' },
  PENDING: { dot: 'bg-warning animate-pulse', label: 'text-warning' },
  ERROR: { dot: 'bg-destructive', label: 'text-destructive' },
  DESTROYING: { dot: 'bg-warning animate-pulse', label: 'text-warning' },
  DESTROYED: { dot: 'bg-muted-foreground', label: 'text-muted-foreground' },
};

const fade = {
  hidden: { opacity: 0, y: 12 },
  show: (i: number) => ({ opacity: 1, y: 0, transition: { delay: i * 0.05, duration: 0.35, ease: [0.16, 1, 0.3, 1] as const } }),
};

export default function InfrastructuresPage() {
  const router = useRouter();
  const [infrastructures, setInfrastructures] = useState<Infrastructure[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    infrastructureApi.list()
      .then(setInfrastructures)
      .catch((e) => toast.error(e.response?.data?.error || 'Failed to load', { id: 'infra-list-load' }))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="space-y-10">
      <div className="flex items-end justify-between gap-4">
        <div>
          <span className="eyebrow">Console / Infrastructure</span>
          <h1 className="mt-2 text-2xl font-display font-semibold text-foreground tracking-tight">Infrastructure</h1>
          <p className="text-sm text-muted-foreground mt-1.5">Manage your cloud environments and deployments.</p>
        </div>
        <Button size="lg" onClick={() => router.push('/dashboard/infrastructures/new')} className="gap-1.5">
          <Plus className="w-4 h-4" /> Create Infrastructure
        </Button>
      </div>

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
            <p className="text-sm text-muted-foreground mb-6 max-w-sm mx-auto">Provision your first cloud environment to start shipping applications to AWS.</p>
            <Button size="lg" onClick={() => router.push('/dashboard/infrastructures/new')} className="gap-1.5">
              <Plus className="w-4 h-4" /> Create Infrastructure
            </Button>
          </div>
        </div>
      ) : (
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
                  <span className="flex items-center gap-2">
                    {infra.is_mock && (
                      <span className="font-mono text-[10px] uppercase tracking-[0.12em] px-1.5 py-0.5 rounded-md border border-brand/30 bg-brand-soft text-brand">
                        Mock
                      </span>
                    )}
                    <span className="flex items-center gap-1.5">
                      <span className={`w-1.5 h-1.5 rounded-full ${st.dot}`} />
                      <span className={`font-mono text-[10px] uppercase tracking-[0.12em] ${st.label}`}>{infra.status}</span>
                    </span>
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
      )}
    </div>
  );
}
