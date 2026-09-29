'use client';

import { useCallback, useEffect, useState } from 'react';
import { ChevronDown, ChevronRight, CircleDollarSign, RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { infrastructureApi } from '@/lib/api/infrastructures';
import { AppCost, ComputeType, InfrastructureCosts } from '@/types/infrastructure';
import { PolicyRefreshDialog } from '@/components/policy-refresh-dialog';

const MONTH_OPTIONS = [1, 2, 3] as const;
const RETRY_DELAY_MS = 2000;

const fmt = (amount: number, currency: string) =>
  new Intl.NumberFormat('en-US', { style: 'currency', currency }).format(amount);

// Network hiccups and a 503 from Cost Explorer being briefly unavailable are worth one
// quiet retry before showing anything alarming; anything else is a real error.
function isTransient(error: unknown): boolean {
  const status = (error as { response?: { status?: number } })?.response?.status;
  return status === undefined || [502, 503, 504].includes(status);
}

// EKS carries source="estimate" on every figure (no split cost allocation data exists for a
// Kubernetes pod); ECS carries source="actual" everywhere. Labelling reads off the response,
// never off compute_type, so a future mixed-source response still renders correctly.
function SourceBadge({ source }: { source: AppCost['source'] }) {
  if (source === 'actual') return null;
  const label = source === 'estimate' ? 'Estimate' : 'Mock';
  return (
    <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-warning border border-warning/30 bg-warning/10 rounded px-1.5 py-0.5">
      {label}
    </span>
  );
}

function ActivationNote({ tagActivation, computeType }: { tagActivation: InfrastructureCosts['tag_activation']; computeType: ComputeType }) {
  if (computeType === 'eks') {
    return <p className="text-[11px] text-muted-foreground">Estimated from each app&apos;s allotted CPU/memory — EKS costs cannot be read from Cost Explorer directly.</p>;
  }
  if (tagActivation.activated === false) {
    return (
      <p className="text-[11px] text-warning">
        Cost allocation tags could not be activated automatically
        {tagActivation.reason === 'payer_account_required' ? ' — this AWS account is an Organizations member; the payer account must activate them' : ''}.
        Figures reflect infra-level totals only until then.
      </p>
    );
  }
  return null;
}

interface Props {
  infraId: string;
  /** False while the customer hasn't finished the onboarding script yet — the costs
   * endpoint would always 409 infrastructure_not_connected, so skip the request. */
  connected: boolean;
}

export function CostsPanel({ infraId, connected }: Props) {
  const [expanded, setExpanded] = useState(false);
  const [months, setMonths] = useState<1 | 2 | 3>(1);
  const [data, setData] = useState<InfrastructureCosts | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notConnected, setNotConnected] = useState(false);
  const [refreshDialog, setRefreshDialog] = useState<{ open: boolean; deniedActions?: string[] }>({ open: false });

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    setNotConnected(false);
    try {
      setData(await infrastructureApi.getCosts(infraId, months));
    } catch (err: unknown) {
      const e = err as { response?: { status?: number; data?: { error?: string; code?: string; denied_actions?: string[] } } };
      if (e.response?.status === 422 && e.response.data?.code === 'policy_refresh_required') {
        setRefreshDialog({ open: true, deniedActions: e.response.data.denied_actions });
        setError('Launchpad’s IAM role needs the cost permissions from a policy refresh.');
        return;
      }
      if (e.response?.status === 409 && e.response.data?.code === 'infrastructure_not_connected') {
        setNotConnected(true);
        return;
      }
      if (isTransient(err)) {
        await new Promise((r) => setTimeout(r, RETRY_DELAY_MS));
        try {
          setData(await infrastructureApi.getCosts(infraId, months));
          return;
        } catch (retryErr) {
          setError(isTransient(retryErr) ? 'Cost data is temporarily unavailable — usually clears in a moment.' : e.response?.data?.error || 'Failed to load costs');
          return;
        }
      }
      setError(e.response?.data?.error || 'Failed to load costs');
    } finally {
      setLoading(false);
    }
  }, [infraId, months]);

  useEffect(() => {
    if (expanded && connected) load();
  }, [expanded, connected, load]);

  const toggle = () => setExpanded((prev) => !prev);

  return (
    <div className="rounded-xl panel">
      <button
        type="button"
        onClick={toggle}
        aria-expanded={expanded}
        className="w-full flex items-center justify-between px-4 py-3 text-left"
      >
        <span className="flex items-center gap-2 text-sm font-display font-semibold text-foreground">
          <CircleDollarSign className="w-3.5 h-3.5 text-muted-foreground" /> Cost Attribution
        </span>
        {expanded ? <ChevronDown className="w-3.5 h-3.5 text-muted-foreground" /> : <ChevronRight className="w-3.5 h-3.5 text-muted-foreground" />}
      </button>

      {expanded && (
        <div className="border-t border-hairline px-4 py-3 space-y-3">
          {!connected || notConnected ? (
            <p className="text-xs text-muted-foreground">
              Cost data becomes available once your AWS account is connected and provisioning
              finishes. Nothing to attribute yet.
            </p>
          ) : (
            <>
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-1">
                  {MONTH_OPTIONS.map((m) => (
                    <button
                      key={m}
                      onClick={() => setMonths(m)}
                      className={`font-mono text-[10px] uppercase tracking-[0.1em] rounded px-2 py-1 transition-colors ${
                        months === m ? 'bg-foreground text-background' : 'text-muted-foreground hover:text-foreground'
                      }`}
                    >
                      {m}mo
                    </button>
                  ))}
                </div>
                {data?.cached && (
                  <span className="font-mono text-[10px] text-muted-foreground/60">cached (updates daily)</span>
                )}
              </div>

              {loading && !data && (
                <div className="space-y-2">
                  <div className="h-3 w-2/3 rounded panel-inset animate-pulse" />
                  <div className="h-3 w-1/2 rounded panel-inset animate-pulse" />
                  <div className="h-3 w-3/4 rounded panel-inset animate-pulse" />
                </div>
              )}

              {error && !refreshDialog.open && (
                <div className="space-y-2.5">
                  <p className="text-xs text-muted-foreground">{error}</p>
                  <Button variant="outline" size="sm" onClick={load} disabled={loading} className="gap-1.5">
                    <RefreshCw className={`w-3 h-3 ${loading ? 'animate-spin' : ''}`} /> Retry
                  </Button>
                </div>
              )}

              {data && (
                <>
                  <div className="space-y-2">
                    {data.apps.length === 0 && (
                      <p className="text-xs text-muted-foreground">No apps to attribute cost to yet.</p>
                    )}
                    {data.apps.map((app) => (
                      <div key={app.app} className="flex items-center justify-between text-sm">
                        <span className="flex items-center gap-2 min-w-0">
                          <span className="text-foreground truncate">{app.app}</span>
                          <SourceBadge source={app.source} />
                        </span>
                        <span className="font-mono text-foreground shrink-0">{fmt(app.amount_usd, data.currency)}</span>
                      </div>
                    ))}
                    <div className="flex items-center justify-between text-sm pt-2 border-t border-hairline">
                      <span className="flex items-center gap-2 text-muted-foreground">
                        Shared (ALB / NAT / control plane)
                        <SourceBadge source={data.shared.source} />
                      </span>
                      <span className="font-mono text-muted-foreground shrink-0">{fmt(data.shared.amount_usd, data.currency)}</span>
                    </div>
                    {data.shared.note && <p className="text-[11px] text-muted-foreground/70">{data.shared.note}</p>}
                  </div>

                  <ActivationNote tagActivation={data.tag_activation} computeType={data.compute_type} />
                </>
              )}
            </>
          )}
        </div>
      )}

      <PolicyRefreshDialog
        open={refreshDialog.open}
        onOpenChange={(open) => setRefreshDialog({ open })}
        infraId={infraId}
        deniedActions={refreshDialog.deniedActions}
      />
    </div>
  );
}
