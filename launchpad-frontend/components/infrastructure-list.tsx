import Link from 'next/link';
import { ChevronRight } from 'lucide-react';
import { StatusBadge } from '@/components/ui/status-badge';
import { Skeleton } from '@/components/ui/skeleton';
import { Infrastructure } from '@/types/infrastructure';

const COMPUTE_LABEL: Record<string, string> = { ecs_fargate: 'ECS Fargate', eks: 'EKS' };

export function InfrastructureList({ items }: { items: Infrastructure[] }) {
  return (
    <ul className="divide-y divide-border overflow-hidden rounded-lg border border-border bg-card">
      {items.map((infra) => (
        <li key={infra.id}>
          <Link
            href={`/dashboard/infrastructures/${infra.id}`}
            className="group flex items-center gap-4 px-4 py-3 outline-none transition-colors hover:bg-muted/40 focus-visible:bg-muted/40 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring/60"
          >
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className="truncate text-sm font-medium text-foreground">{infra.name}</span>
                {infra.is_mock && (
                  <span className="rounded-md border border-border px-1.5 py-0.5 text-xs text-muted-foreground">Mock</span>
                )}
              </div>
              <p className="mt-0.5 break-words font-mono text-xs text-muted-foreground">
                {infra.code} · {COMPUTE_LABEL[infra.compute_type] ?? infra.compute_type} · {infra.max_cpu} vCPU · {infra.max_memory} GB
              </p>
            </div>
            <StatusBadge status={infra.status} />
            <ChevronRight className="size-4 shrink-0 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100" />
          </Link>
        </li>
      ))}
    </ul>
  );
}

export function InfrastructureListSkeleton() {
  return (
    <div className="divide-y divide-border overflow-hidden rounded-lg border border-border bg-card" aria-busy="true" aria-label="Loading infrastructure">
      {Array.from({ length: 4 }).map((_, i) => (
        <div key={i} className="flex items-center gap-4 px-4 py-3">
          <div className="flex-1 space-y-2">
            <Skeleton className="h-4 w-40" />
            <Skeleton className="h-3 w-64 max-w-full" />
          </div>
          <Skeleton className="h-4 w-16" />
        </div>
      ))}
    </div>
  );
}
