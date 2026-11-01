'use client';

import { useEffect, useRef, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import Link from 'next/link';
import { buttonVariants } from '@/components/ui/button-variants';
import { Label } from '@/components/ui/label';
import { PageHeader } from '@/components/layout/page-header';
import { EmptyState } from '@/components/ui/empty-state';
import { StatusBadge } from '@/components/ui/status-badge';
import { Skeleton } from '@/components/ui/skeleton';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Plus, Rocket, ChevronRight } from 'lucide-react';
import { ApplicationSummary } from '@/types/application';
import { Infrastructure } from '@/types/infrastructure';
import { applicationApi } from '@/lib/api/applications';
import { useOrgRole } from '@/lib/use-org-role';
import { infrastructureApi } from '@/lib/api/infrastructures';
import { toast } from 'sonner';
import { Suspense } from 'react';

const IN_FLIGHT_STATUSES = ['CREATED', 'BUILDING', 'PUSHING_IMAGE', 'DEPLOYING'];

export default function ApplicationsPage() {
  return (
    <Suspense>
      <ApplicationsPageInner />
    </Suspense>
  );
}

function ApplicationsPageInner() {
  const canDeploy = useOrgRole().allow('deploy', true);
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

  // Assigned in an effect, not during render (see databases-section.tsx for the same
  // pattern): the poll below only reads .current, so updating it after paint is fine and
  // keeps setState out of the render path.
  const hasInFlight = useRef(false);
  useEffect(() => {
    hasInFlight.current = apps.some((a) => IN_FLIGHT_STATUSES.includes(a.status));
  }, [apps]);

  useEffect(() => {
    if (!selectedInfra) return;
    let isActive = true;
    const fetchApps = async (showLoading: boolean) => {
      if (showLoading) await Promise.resolve();
      if (isActive && showLoading) setLoading(true);
      try {
        const data = await applicationApi.list(selectedInfra);
        if (isActive) setApps(data);
      } catch (err: unknown) {
        // Silent on background poll ticks — only the initial load surfaces an error toast,
        // otherwise a transient gateway blip during polling would spam the user every 5s.
        if (isActive && showLoading) {
          const error = err as { response?: { data?: { error?: string } } };
          toast.error(error.response?.data?.error || 'Failed to load applications');
        }
      } finally {
        if (isActive && showLoading) setLoading(false);
      }
    };
    fetchApps(true);
    const interval = setInterval(() => {
      if (hasInFlight.current) fetchApps(false);
    }, 5000);
    return () => { isActive = false; clearInterval(interval); };
  }, [selectedInfra]);

  const deployHref = `/dashboard/applications/new${selectedInfra ? `?infra=${selectedInfra}` : ''}`;
  const active = apps.filter((a) => a.status === 'ACTIVE').length;
  const building = apps.filter((a) => ['BUILDING', 'DEPLOYING', 'PUSHING_IMAGE', 'CREATED'].includes(a.status)).length;
  const sleeping = apps.filter((a) => a.status === 'SLEEPING').length;

  const deployButton = (
    <Link href={deployHref} className={buttonVariants()}>
      <Plus className="size-4" />
      Deploy application
    </Link>
  );

  return (
    <div className="space-y-6">
      <PageHeader
        title="Applications"
        description="Services running on your AWS accounts."
        actions={canDeploy ? deployButton : undefined}
      />

      {infrastructures.length > 0 && (
        <div className="max-w-xs space-y-1.5">
          <Label htmlFor="app-infra">Infrastructure</Label>
          <Select value={selectedInfra} onValueChange={(v) => v && handleInfraChange(v)}>
            <SelectTrigger id="app-infra" className="w-full">
              <SelectValue placeholder="Select infrastructure">
                {infrastructures.find((i) => i.id === selectedInfra)?.name}
              </SelectValue>
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
        <div className="grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-border bg-border sm:grid-cols-4">
          {[
            { label: 'Applications', value: apps.length },
            { label: 'Active', value: active },
            { label: 'Building', value: building },
            { label: 'Sleeping', value: sleeping },
          ].map((stat) => (
            <div key={stat.label} className="bg-card px-4 py-3">
              <p className="text-xs text-muted-foreground">{stat.label}</p>
              <p className="mt-1 text-xl font-semibold tabular-nums tracking-tight text-foreground">{stat.value}</p>
            </div>
          ))}
        </div>
      )}

      {loading ? (
        <div className="divide-y divide-border overflow-hidden rounded-lg border border-border bg-card" aria-busy="true" aria-label="Loading applications">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="flex items-center gap-4 px-4 py-3">
              <div className="flex-1 space-y-2">
                <Skeleton className="h-4 w-36" />
                <Skeleton className="h-3 w-56 max-w-full" />
              </div>
              <Skeleton className="h-4 w-16" />
            </div>
          ))}
        </div>
      ) : apps.length === 0 ? (
        <EmptyState
          icon={Rocket}
          title={infrastructures.length === 0 ? 'No infrastructure yet' : 'No applications yet'}
          description={
            infrastructures.length === 0
              ? 'Add an AWS account first. Applications deploy into it.'
              : canDeploy
                ? 'Connect a GitHub repository and Launchpad builds and deploys it to this account.'
                : 'Applications deployed to this account will show up here.'
          }
          action={canDeploy && infrastructures.length > 0 ? deployButton : undefined}
        />
      ) : (
        <ul className="divide-y divide-border overflow-hidden rounded-lg border border-border bg-card">
          {apps.map((app) => (
            <li key={app.id}>
              <Link
                href={`/dashboard/applications/${app.id}`}
                className="group flex items-center gap-4 px-4 py-3 outline-none transition-colors hover:bg-muted/40 focus-visible:bg-muted/40 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring/60"
              >
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium text-foreground">{app.name}</p>
                  <p className="mt-0.5 font-mono text-xs text-muted-foreground">
                    {app.cpu} vCPU · {app.memory} GB · :{app.port}
                  </p>
                </div>
                <StatusBadge status={app.status} />
                <ChevronRight className="size-4 shrink-0 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100" />
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
