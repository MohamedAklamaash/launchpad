'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Plus, Rocket } from 'lucide-react';
import { ApplicationSummary } from '@/types/application';
import { Infrastructure } from '@/types/infrastructure';
import { applicationApi } from '@/lib/api/applications';
import { infrastructureApi } from '@/lib/api/infrastructures';
import { toast } from 'sonner';

const statusColor: Record<string, string> = {
  ACTIVE: 'bg-green-500/10 text-green-500',
  BUILDING: 'bg-blue-500/10 text-blue-500',
  DEPLOYING: 'bg-blue-500/10 text-blue-500',
  PUSHING_IMAGE: 'bg-blue-500/10 text-blue-500',
  SLEEPING: 'bg-yellow-500/10 text-yellow-500',
  FAILED: 'bg-red-500/10 text-red-500',
  CREATED: 'bg-[#262626] text-[#a3a3a3]',
};

export default function ApplicationsPage() {
  const router = useRouter();
  const [infrastructures, setInfrastructures] = useState<Infrastructure[]>([]);
  const [selectedInfra, setSelectedInfra] = useState<string>('');
  const [apps, setApps] = useState<ApplicationSummary[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    infrastructureApi.list()
      .then((data) => {
        setInfrastructures(data);
        if (data.length > 0) setSelectedInfra(data[0].id);
      })
      .catch(() => toast.error('Failed to load infrastructures'));
  }, []);

  useEffect(() => {
    if (!selectedInfra) return;
    setLoading(true);
    applicationApi.list(selectedInfra)
      .then(setApps)
      .catch((e) => toast.error(e.response?.data?.error || 'Failed to load applications'))
      .finally(() => setLoading(false));
  }, [selectedInfra]);

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold">Applications</h1>
          <p className="text-[#a3a3a3] mt-1">Manage your deployed applications</p>
        </div>
        <Button onClick={() => router.push(`/dashboard/applications/new${selectedInfra ? `?infra=${selectedInfra}` : ''}`)}>
          <Plus className="w-4 h-4 mr-2" /> Deploy Application
        </Button>
      </div>

      {infrastructures.length > 0 && (
        <div className="mb-6 max-w-xs">
          <Select value={selectedInfra} onValueChange={(v) => v && setSelectedInfra(v)}>
            <SelectTrigger className="bg-[#141414] border-[#262626]">
              <SelectValue placeholder="Select infrastructure" />
            </SelectTrigger>
            <SelectContent className="bg-[#141414] border-[#262626]">
              {infrastructures.map((i) => (
                <SelectItem key={i.id} value={i.id}>{i.name}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )}

      {loading ? (
        <div className="text-[#a3a3a3]">Loading...</div>
      ) : apps.length === 0 ? (
        <Card className="bg-[#141414] border-[#262626] p-12 text-center">
          <Rocket className="w-12 h-12 text-[#666] mx-auto mb-4" />
          <p className="text-[#a3a3a3] mb-4">No applications deployed yet</p>
          <Button onClick={() => router.push(`/dashboard/applications/new${selectedInfra ? `?infra=${selectedInfra}` : ''}`)}>
            <Plus className="w-4 h-4 mr-2" /> Deploy Application
          </Button>
        </Card>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {apps.map((app) => (
            <Card
              key={app.id}
              className="bg-[#141414] border-[#262626] p-5 cursor-pointer hover:bg-[#1a1a1a] transition-colors"
              onClick={() => router.push(`/dashboard/applications/${app.id}`)}
            >
              <div className="flex items-center justify-between mb-3">
                <h3 className="font-semibold">{app.name}</h3>
              </div>
              <div className="flex items-center gap-4 text-xs text-[#666]">
                <span>{app.cpu} vCPU</span>
                <span>{app.memory} GB</span>
                <span>Port {app.port}</span>
              </div>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
