'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { Button } from '@/components/ui/button';
import { Plus, Server, Cpu, HardDrive } from 'lucide-react';
import { Infrastructure } from '@/types/infrastructure';
import { infrastructureApi } from '@/lib/api/infrastructures';
import { useAuthStore } from '@/lib/store/auth';
import { toast } from 'sonner';

export default function DashboardPage() {
  const router = useRouter();
  const user = useAuthStore((s) => s.user);
  const [infrastructures, setInfrastructures] = useState<Infrastructure[]>([]);
  const [loading, setLoading] = useState(true);

  const isSuperAdmin = user?.role === 'super_admin';

  useEffect(() => {
    loadInfrastructures();
  }, []);

  const loadInfrastructures = async () => {
    try {
      const data = await infrastructureApi.list();
      setInfrastructures(data);
    } catch (error: any) {
      toast.error(error.response?.data?.error || 'Failed to load infrastructures');
    } finally {
      setLoading(false);
    }
  };

  const getStatusDot = (status: string) => {
    switch (status) {
      case 'ACTIVE': return 'bg-emerald-500';
      case 'PROVISIONING': return 'bg-blue-500 animate-pulse';
      case 'PENDING': return 'bg-amber-500 animate-pulse';
      case 'ERROR': return 'bg-red-500';
      default: return 'bg-[#444]';
    }
  };

  if (loading) return (
    <div className="flex items-center justify-center h-64">
      <div className="w-5 h-5 border-2 border-[#333] border-t-violet-500 rounded-full animate-spin" />
    </div>
  );

  return (
    <div className="space-y-8">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-white tracking-tight">Infrastructure</h1>
          <p className="text-sm text-[#555] mt-0.5">
            {isSuperAdmin ? 'Manage your cloud environments' : `${infrastructures.length} environment${infrastructures.length !== 1 ? 's' : ''} available`}
          </p>
        </div>
        {isSuperAdmin && (
          <Button onClick={() => router.push('/dashboard/infrastructures/new')}
            className="bg-violet-600 hover:bg-violet-700 text-white h-9 text-sm font-medium gap-1.5">
            <Plus className="w-3.5 h-3.5" /> New Infrastructure
          </Button>
        )}
      </div>

      {infrastructures.length === 0 ? (
        <div className="border border-dashed border-[#222] rounded-xl p-16 text-center">
          <div className="w-12 h-12 rounded-xl bg-[#111] border border-[#1e1e1e] flex items-center justify-center mx-auto mb-4">
            <Server className="w-5 h-5 text-[#444]" />
          </div>
          <p className="text-sm font-medium text-[#555] mb-1">No infrastructure yet</p>
          {isSuperAdmin ? (
            <>
              <p className="text-xs text-[#333] mb-5">Create your first environment to start deploying</p>
              <Button onClick={() => router.push('/dashboard/infrastructures/new')}
                className="bg-violet-600 hover:bg-violet-700 text-white h-9 text-sm gap-1.5">
                <Plus className="w-3.5 h-3.5" /> Create Infrastructure
              </Button>
            </>
          ) : (
            <p className="text-xs text-[#333]">You haven't been added to any infrastructure yet.</p>
          )}
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
          {infrastructures.map((infra) => (
            <div key={infra.id}
              className="group bg-[#0d0d0d] border border-[#1a1a1a] rounded-xl p-5 cursor-pointer hover:border-[#2a2a2a] hover:bg-[#111] transition-all"
              onClick={() => router.push(`/dashboard/infrastructures/${infra.id}`)}>
              <div className="flex items-start justify-between mb-4">
                <div className="w-9 h-9 rounded-lg bg-[#141414] border border-[#1e1e1e] flex items-center justify-center">
                  <Server className="w-4 h-4 text-[#555]" />
                </div>
                <div className="flex items-center gap-1.5">
                  <span className={`w-1.5 h-1.5 rounded-full ${getStatusDot(infra.status)}`} />
                  <span className="text-xs text-[#555] font-mono">{infra.status}</span>
                </div>
              </div>
              <h3 className="text-sm font-semibold text-white mb-3 truncate">{infra.name}</h3>
              <div className="flex items-center gap-3 text-xs text-[#444]">
                <span className="flex items-center gap-1"><Cpu className="w-3 h-3" />{infra.max_cpu} vCPU</span>
                <span className="flex items-center gap-1"><HardDrive className="w-3 h-3" />{infra.max_memory} GB</span>
                <span className="ml-auto text-[#333] font-mono">{infra.cloud_provider}</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
