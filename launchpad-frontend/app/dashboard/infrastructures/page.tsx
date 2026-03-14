'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Plus, Server, Cpu, HardDrive } from 'lucide-react';
import { Infrastructure } from '@/types/infrastructure';
import { infrastructureApi } from '@/lib/api/infrastructures';
import { toast } from 'sonner';

const statusColor: Record<string, string> = {
  ACTIVE: 'bg-green-500/10 text-green-500',
  PROVISIONING: 'bg-blue-500/10 text-blue-500',
  PENDING: 'bg-yellow-500/10 text-yellow-500',
  ERROR: 'bg-red-500/10 text-red-500',
  DESTROYING: 'bg-orange-500/10 text-orange-500',
  DESTROYED: 'bg-[#262626] text-[#a3a3a3]',
};

export default function InfrastructuresPage() {
  const router = useRouter();
  const [infrastructures, setInfrastructures] = useState<Infrastructure[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    infrastructureApi.list()
      .then(setInfrastructures)
      .catch((e) => toast.error(e.response?.data?.error || 'Failed to load'))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="text-[#a3a3a3]">Loading...</div>;

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold">Infrastructure</h1>
          <p className="text-[#a3a3a3] mt-1">Manage your cloud environments</p>
        </div>
        <Button onClick={() => router.push('/dashboard/infrastructures/new')}>
          <Plus className="w-4 h-4 mr-2" /> Create Infrastructure
        </Button>
      </div>

      {infrastructures.length === 0 ? (
        <Card className="bg-[#141414] border-[#262626] p-12 text-center">
          <Server className="w-12 h-12 text-[#666] mx-auto mb-4" />
          <p className="text-[#a3a3a3] mb-4">No infrastructure yet</p>
          <Button onClick={() => router.push('/dashboard/infrastructures/new')}>
            <Plus className="w-4 h-4 mr-2" /> Create Infrastructure
          </Button>
        </Card>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {infrastructures.map((infra) => (
            <Card
              key={infra.id}
              className="bg-[#141414] border-[#262626] p-6 cursor-pointer hover:bg-[#1a1a1a] transition-colors"
              onClick={() => router.push(`/dashboard/infrastructures/${infra.id}`)}
            >
              <div className="flex items-center justify-between mb-4">
                <h3 className="text-lg font-semibold">{infra.name}</h3>
                <Badge className={statusColor[infra.status] || 'bg-[#262626] text-[#a3a3a3]'}>
                  {infra.status}
                </Badge>
              </div>
              <div className="space-y-2 text-sm text-[#a3a3a3]">
                <div className="flex items-center gap-2">
                  <Server className="w-4 h-4" /><span>{infra.cloud_provider.toUpperCase()}</span>
                </div>
                <div className="flex items-center gap-2">
                  <Cpu className="w-4 h-4" /><span>{infra.max_cpu} vCPU</span>
                </div>
                <div className="flex items-center gap-2">
                  <HardDrive className="w-4 h-4" /><span>{infra.max_memory} GB RAM</span>
                </div>
              </div>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
