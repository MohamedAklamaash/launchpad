'use client';

import { useCallback, useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Check, Copy, Globe, Loader2, Plus, ShieldCheck, ShieldX, Trash2 } from 'lucide-react';
import { customDomainApi } from '@/lib/api/custom-domains';
import { CustomDomain, DnsRecord } from '@/types/custom-domain';
import { toast } from 'sonner';

interface Props {
  appId: string;
  infraId: string;
}

function errorMessage(e: unknown, fallback: string): string {
  const err = e as { response?: { status?: number; data?: { error?: string } } };
  return err.response?.data?.error || fallback;
}

const STATUS_BADGE: Record<CustomDomain['status'], { variant: 'default' | 'secondary' | 'destructive'; label: string }> = {
  PENDING: { variant: 'secondary', label: 'Pending' },
  VALIDATED: { variant: 'default', label: 'Validated' },
  DISABLED: { variant: 'destructive', label: 'Disabled' },
};

function CopyField({ record, warn }: { record: DnsRecord; warn?: string }) {
  const [copied, setCopied] = useState(false);

  const copy = () => {
    if (!record.value) return;
    navigator.clipboard.writeText(record.value).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };

  return (
    <div className="rounded-lg border border-hairline bg-surface-3 px-3 py-2 space-y-1">
      <div className="flex items-center justify-between gap-2">
        <span className="eyebrow text-[10px]">{record.type} · {record.name}</span>
        {record.value && (
          <button onClick={copy} className="shrink-0 text-muted-foreground hover:text-foreground transition-colors">
            {copied ? <Check className="w-3.5 h-3.5 text-success" /> : <Copy className="w-3.5 h-3.5" />}
          </button>
        )}
      </div>
      <code className="block text-[11px] font-mono text-foreground break-all">
        {record.value || 'Still generating — refresh in a moment'}
      </code>
      {warn && <p className="text-[10px] text-warning">{warn}</p>}
    </div>
  );
}

export function CustomDomainsPanel({ appId, infraId }: Props) {
  const [domains, setDomains] = useState<CustomDomain[]>([]);
  const [loading, setLoading] = useState(true);
  const [hostname, setHostname] = useState('');
  const [claiming, setClaiming] = useState(false);
  const [verifyingId, setVerifyingId] = useState<string | null>(null);
  const [removingId, setRemovingId] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await customDomainApi.list(infraId);
      setDomains(data);
    } catch (e: unknown) {
      toast.error(errorMessage(e, 'Failed to load custom domains'));
    } finally {
      setLoading(false);
    }
  }, [infraId]);

  useEffect(() => {
    load();
  }, [load]);

  const handleClaim = async () => {
    const trimmed = hostname.trim();
    if (!trimmed) return;
    setClaiming(true);
    try {
      const domain = await customDomainApi.claim(infraId, appId, trimmed);
      setDomains((prev) => [domain, ...prev]);
      setHostname('');
      toast.success('Domain claimed — save the ownership token below, it will not be shown again.');
    } catch (e: unknown) {
      toast.error(errorMessage(e, 'Failed to claim domain'));
    } finally {
      setClaiming(false);
    }
  };

  const handleVerify = async (domainId: string) => {
    setVerifyingId(domainId);
    try {
      const updated = await customDomainApi.verify(infraId, domainId);
      setDomains((prev) => prev.map((d) => (d.id === domainId ? updated : d)));
      toast.success(`${updated.hostname} is now live`);
    } catch (e: unknown) {
      toast.error(errorMessage(e, 'Verification failed — check the DNS records below and try again'));
    } finally {
      setVerifyingId(null);
    }
  };

  const handleRemove = async (domainId: string) => {
    setRemovingId(domainId);
    try {
      await customDomainApi.remove(infraId, domainId);
      await load();
    } catch (e: unknown) {
      toast.error(errorMessage(e, 'Failed to remove domain'));
    } finally {
      setRemovingId(null);
    }
  };

  return (
    <div className="panel rounded-2xl p-5 space-y-4">
      <div className="flex items-center gap-2">
        <Globe className="w-4 h-4 text-muted-foreground" />
        <h3 className="text-sm font-display font-semibold text-foreground">Custom Domains</h3>
      </div>

      <div className="flex items-center gap-2">
        <Input
          value={hostname}
          onChange={(e) => setHostname(e.target.value)}
          placeholder="app.example.com"
          className="text-sm"
          onKeyDown={(e) => { if (e.key === 'Enter') handleClaim(); }}
        />
        <Button onClick={handleClaim} disabled={claiming || !hostname.trim()} className="gap-1.5 shrink-0">
          {claiming ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Plus className="w-3.5 h-3.5" />}
          Claim
        </Button>
      </div>

      {loading ? (
        <p className="text-xs text-muted-foreground">Loading…</p>
      ) : domains.length === 0 ? (
        <p className="text-xs text-muted-foreground">
          No custom domains yet. Claim one above to point your own hostname at this app.
        </p>
      ) : (
        <div className="space-y-3">
          {domains.map((domain) => {
            const badge = STATUS_BADGE[domain.status];
            return (
              <div key={domain.id} className="rounded-xl border border-hairline p-4 space-y-3">
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="text-sm font-medium text-foreground truncate">{domain.hostname}</span>
                    <Badge variant={badge.variant}>{badge.label}</Badge>
                  </div>
                  {domain.status !== 'DISABLED' && (
                    <Button
                      variant="outline" size="sm" className="gap-1.5 shrink-0"
                      onClick={() => handleRemove(domain.id)} disabled={removingId === domain.id}
                    >
                      {removingId === domain.id ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />}
                      Remove
                    </Button>
                  )}
                </div>

                {domain.status === 'PENDING' && (
                  <div className="space-y-2">
                    <p className="text-xs text-muted-foreground">
                      Add these records in your own DNS provider, then verify. Expires{' '}
                      {new Date(domain.expires_at).toLocaleString()} if not verified.
                    </p>
                    <CopyField
                      record={domain.ownership_txt}
                      warn={domain.ownership_txt.value ? 'Shown once — save it now.' : 'Already claimed once; delete and re-claim to see it again.'}
                    />
                    <CopyField record={domain.cname_to_edge} />
                    {domain.acm_validation ? (
                      <CopyField record={domain.acm_validation} />
                    ) : (
                      <p className="text-[11px] text-muted-foreground">
                        Certificate validation record is still generating — refresh shortly.
                      </p>
                    )}
                    <Button
                      size="sm" className="gap-1.5" onClick={() => handleVerify(domain.id)}
                      disabled={verifyingId === domain.id}
                    >
                      {verifyingId === domain.id ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <ShieldCheck className="w-3.5 h-3.5" />}
                      Verify
                    </Button>
                  </div>
                )}

                {domain.status === 'VALIDATED' && (
                  <p className="text-xs text-success flex items-center gap-1.5">
                    <ShieldCheck className="w-3.5 h-3.5" />
                    Live at https://{domain.hostname}
                    {domain.last_verified_at && ` · last verified ${new Date(domain.last_verified_at).toLocaleString()}`}
                  </p>
                )}

                {domain.status === 'DISABLED' && (
                  <p className="text-xs text-muted-foreground flex items-center gap-1.5">
                    <ShieldX className="w-3.5 h-3.5" />
                    Disabled — ownership could no longer be verified, or it was removed.
                  </p>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
