'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { Plus, Server } from 'lucide-react';
import { buttonVariants } from '@/components/ui/button-variants';
import { EmptyState } from '@/components/ui/empty-state';
import { PageHeader } from '@/components/layout/page-header';
import { InfrastructureList, InfrastructureListSkeleton } from '@/components/infrastructure-list';
import { Infrastructure } from '@/types/infrastructure';
import { infrastructureApi } from '@/lib/api/infrastructures';
import { useOrgRole } from '@/lib/use-org-role';
import { toast } from 'sonner';

export default function InfrastructuresPage() {
  const [infrastructures, setInfrastructures] = useState<Infrastructure[]>([]);
  const [loading, setLoading] = useState(true);
  const canCreate = useOrgRole().allow('create_infrastructure', true);

  useEffect(() => {
    infrastructureApi.list()
      .then(setInfrastructures)
      .catch((e) => toast.error(e.response?.data?.error || 'Failed to load', { id: 'infra-list-load' }))
      .finally(() => setLoading(false));
  }, []);

  const addAccount = (
    <Link href="/dashboard/infrastructures/new" className={buttonVariants()}>
      <Plus className="size-4" />
      Add AWS account
    </Link>
  );

  return (
    <div>
      <PageHeader
        title="Infrastructure"
        description="The AWS accounts Launchpad deploys into."
        actions={canCreate && !loading && infrastructures.length > 0 ? addAccount : undefined}
      />

      {loading ? (
        <InfrastructureListSkeleton />
      ) : infrastructures.length === 0 ? (
        canCreate ? (
          <EmptyState
            icon={Server}
            title="No AWS accounts yet"
            description="Add one, run the onboarding script in it, and Launchpad takes it from there."
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
        <InfrastructureList items={infrastructures} />
      )}
    </div>
  );
}
