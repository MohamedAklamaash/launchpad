'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { Plus, Server } from 'lucide-react';
import { buttonVariants } from '@/components/ui/button-variants';
import { EmptyState } from '@/components/ui/empty-state';
import { Skeleton } from '@/components/ui/skeleton';
import { PageHeader } from '@/components/layout/page-header';
import { InfrastructureList, InfrastructureListSkeleton } from '@/components/infrastructure-list';
import { Infrastructure } from '@/types/infrastructure';
import { Application } from '@/types/application';
import { infrastructureApi } from '@/lib/api/infrastructures';
import { applicationApi } from '@/lib/api/applications';
import { useAuthStore } from '@/lib/store/auth';
import { useOrgRole } from '@/lib/use-org-role';
import { toast } from 'sonner';

function Stat({ label, value, unit }: { label: string; value: number | null; unit?: string }) {
  return (
    <div className="bg-card px-4 py-3">
      <p className="text-xs text-muted-foreground">{label}</p>
      {value === null ? (
        <Skeleton className="mt-1.5 h-6 w-10" />
      ) : (
        <p className="mt-1 text-xl font-semibold tabular-nums tracking-tight text-foreground">
          {value}
          {unit && <span className="ml-1 text-xs font-normal text-muted-foreground">{unit}</span>}
        </p>
      )}
    </div>
  );
}

export default function DashboardPage() {
  const user = useAuthStore((s) => s.user);
  const [infrastructures, setInfrastructures] = useState<Infrastructure[]>([]);
  const [apps, setApps] = useState<Application[] | null>(null);
  const [loading, setLoading] = useState(true);

  const orgRole = useOrgRole();
  const isSuperAdmin = orgRole.allow('create_infrastructure', user?.role === 'super_admin');

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
  const liveApps = apps?.filter((a) => a.status === 'ACTIVE').length ?? null;
  const totalCpu = infrastructures.reduce((s, i) => s + Number(i.max_cpu || 0), 0);
  const firstName = user?.user_name?.split(' ')[0];
  const count = infrastructures.length;

  const addAccount = (
    <Link href="/dashboard/infrastructures/new" className={buttonVariants()}>
      <Plus className="size-4" />
      Add AWS account
    </Link>
  );

  return (
    <div>
      <PageHeader
        title={firstName ? `Welcome back, ${firstName}` : 'Overview'}
        description={
          isSuperAdmin
            ? 'Your AWS accounts and what is running in them.'
            : count > 0
              ? `You can work in ${count} AWS account${count === 1 ? '' : 's'}.`
              : undefined
        }
        actions={isSuperAdmin && !loading && count > 0 ? addAccount : undefined}
      />

      {loading ? (
        <InfrastructureListSkeleton />
      ) : count === 0 ? (
        isSuperAdmin ? (
          <EmptyState
            icon={Server}
            title="Connect your first AWS account"
            description="Launchpad builds the network, load balancer and cluster inside your account. You run one script there to grant access, and provisioning starts on its own."
            action={addAccount}
          />
        ) : (
          <EmptyState
            icon={Server}
            title="No AWS accounts shared with you yet"
            description="An owner or admin needs to give you access. Once they do, the account shows up here."
          />
        )
      ) : (
        <div className="space-y-8">
          <div className="grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-border bg-border lg:grid-cols-4">
            <Stat label="AWS accounts" value={count} />
            <Stat label="Active" value={activeEnv} />
            <Stat label="Running apps" value={liveApps} />
            <Stat label="Compute limit" value={totalCpu} unit="vCPU" />
          </div>
          <section aria-labelledby="envs-title">
            <h2 id="envs-title" className="mb-3 text-sm font-medium text-foreground">
              Infrastructure
            </h2>
            <InfrastructureList items={infrastructures} />
          </section>
        </div>
      )}
    </div>
  );
}
