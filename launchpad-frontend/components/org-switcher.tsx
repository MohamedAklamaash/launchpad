'use client';

import { useState } from 'react';
import { Building2, Check, ChevronsUpDown, Plus } from 'lucide-react';
import { toast } from 'sonner';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { apiErrorMessage, goToDashboard, organizationApi, switchToOrg } from '@/lib/api/organizations';
import { useOrgStore } from '@/lib/store/org';
import { useOrgRole } from '@/lib/use-org-role';
import { ROLE_LABEL } from '@/lib/org-matrix';

export function OrgSwitcher() {
  const { org: active, loading } = useOrgRole();
  const orgs = useOrgStore((s) => s.orgs);
  const status = useOrgStore((s) => s.status);
  const load = useOrgStore((s) => s.load);
  const [switchingId, setSwitchingId] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [name, setName] = useState('');
  const [creating, setCreating] = useState(false);

  const handleSwitch = async (id: string) => {
    if (id === active?.id || switchingId) return;
    setSwitchingId(id);
    try {
      await switchToOrg(id);
      goToDashboard();
    } catch (err) {
      toast.error(apiErrorMessage(err, 'Could not switch organization'));
      setSwitchingId(null);
    }
  };

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    setCreating(true);
    try {
      const created = await organizationApi.create(name.trim());
      await load(true);
      toast.success(`Created ${created.name}`);
      setCreateOpen(false);
      await handleSwitch(created.id);
    } catch (err) {
      toast.error(apiErrorMessage(err, 'Could not create organization'));
    } finally {
      setCreating(false);
    }
  };

  const label = active?.name ?? (loading ? 'Loading…' : status === 'error' ? 'Organizations unavailable' : 'No organization');

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger
          aria-label="Switch organization"
          className="flex h-9 max-w-[240px] items-center gap-2 rounded-lg border border-hairline-strong bg-surface-1 px-3 text-sm text-foreground outline-none transition-colors hover:border-brand/40 focus-visible:ring-2 focus-visible:ring-ring/60"
        >
          <Building2 className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          <span className="truncate">{label}</span>
          <ChevronsUpDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="min-w-[260px] bg-popover border-hairline-strong shadow-xl shadow-black/40">
          <p className="eyebrow px-3 py-2">Organizations</p>
          {orgs.length === 0 && (
            <p className="px-3 py-2 text-xs text-muted-foreground">
              {status === 'error' ? 'Could not load your organizations.' : 'You are not in any organization yet.'}
            </p>
          )}
          {orgs.map((o) => (
            <DropdownMenuItem
              key={o.id}
              onClick={() => handleSwitch(o.id)}
              disabled={switchingId !== null}
              className="mx-1 cursor-pointer rounded-md"
            >
              <span className="flex min-w-0 flex-1 flex-col">
                <span className="truncate text-sm">{o.name}</span>
                <span className="text-[11px] text-muted-foreground">
                  {o.is_personal ? 'Personal' : 'Team'} · {ROLE_LABEL[o.role]}
                </span>
              </span>
              {o.id === active?.id && <Check className="ml-2 h-3.5 w-3.5 text-brand" />}
            </DropdownMenuItem>
          ))}
          <DropdownMenuSeparator className="bg-hairline" />
          <DropdownMenuItem onClick={() => { setName(''); setCreateOpen(true); }} className="mx-1 mb-1 cursor-pointer rounded-md">
            <Plus className="mr-2 h-3.5 w-3.5" />
            Create team org
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <Dialog open={createOpen} onOpenChange={(o) => !creating && setCreateOpen(o)}>
        <DialogContent>
          <form onSubmit={handleCreate} className="grid gap-4">
            <DialogHeader>
              <DialogTitle className="text-base font-display font-semibold">Create team org</DialogTitle>
            </DialogHeader>
            <div className="space-y-1.5">
              <Label htmlFor="org-name" className="eyebrow">Name</Label>
              <Input id="org-name" value={name} onChange={(e) => setName(e.target.value)} required maxLength={64}
                autoFocus placeholder="Acme Platform" />
              <p className="text-xs text-muted-foreground">You become its owner and can invite teammates afterwards.</p>
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setCreateOpen(false)} disabled={creating}>Cancel</Button>
              <Button type="submit" disabled={creating || !name.trim()}>{creating ? 'Creating…' : 'Create'}</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </>
  );
}
