'use client';

import { useCallback, useEffect, useState } from 'react';
import { ChevronDown, ChevronRight, RefreshCw, ScrollText } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { infrastructureApi } from '@/lib/api/infrastructures';
import { InfrastructureStatus, ProvisioningLogs } from '@/types/infrastructure';

const IN_FLIGHT: InfrastructureStatus[] = ['PROVISIONING', 'UPDATING', 'DESTROYING'];
const POLL_MS = 10_000;
const RETRY_DELAY_MS = 2000;

// Network hiccups (gateway restart, LB drain) and 502/503/504 are worth one quiet
// retry before bothering the owner; anything else (403, 404) is not.
function isTransient(error: unknown): boolean {
  const status = (error as { response?: { status?: number } })?.response?.status;
  return status === undefined || [502, 503, 504].includes(status);
}

interface Props {
  infraId: string;
  status: InfrastructureStatus;
  /** False while the customer hasn't finished the onboarding script yet (no AssumeRole,
   * no Terraform run, so there is genuinely nothing to show) — render that explanation
   * instead of attempting a fetch that would just come back empty. */
  connected: boolean;
}

export function ProvisioningLogsPanel({ infraId, status, connected }: Props) {
  const [expanded, setExpanded] = useState(false);
  const [data, setData] = useState<ProvisioningLogs | null>(null);
  const [failed, setFailed] = useState(false);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    if (!connected) return;
    setLoading(true);
    try {
      setData(await infrastructureApi.getLogs(infraId));
      setFailed(false);
    } catch (e) {
      if (isTransient(e)) {
        await new Promise((r) => setTimeout(r, RETRY_DELAY_MS));
        try {
          setData(await infrastructureApi.getLogs(infraId));
          setFailed(false);
          setLoading(false);
          return;
        } catch {
          // Falls through — still failing after the quiet retry.
        }
      }
      setFailed(true);
    } finally {
      setLoading(false);
    }
  }, [infraId, connected]);

  // The page's status can flip to terminal up to 5s before the last log tail is written;
  // keep polling until the logs endpoint itself reports the run finished.
  const live = IN_FLIGHT.includes(status) || (!failed && data !== null && IN_FLIGHT.includes(data.status));

  useEffect(() => {
    if (!expanded || !live || !connected) return;
    const interval = setInterval(load, POLL_MS);
    return () => clearInterval(interval);
  }, [expanded, live, connected, load]);

  const toggle = () => {
    const next = !expanded;
    setExpanded(next);
    if (next && connected) load();
  };

  return (
    <div className="rounded-lg border border-border bg-card">
      <button
        type="button"
        onClick={toggle}
        aria-expanded={expanded}
        className="flex w-full items-center justify-between rounded-lg px-4 py-3 text-left outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
      >
        <span className="flex items-center gap-2 text-sm font-medium text-foreground">
          <ScrollText className="size-3.5 text-muted-foreground" /> Provisioning logs
        </span>
        <span className="flex items-center gap-2">
          {data?.truncated && (
            <span className="rounded-md border border-warning/30 bg-warning/10 px-1.5 py-0.5 text-xs text-foreground">
              Truncated
            </span>
          )}
          {expanded ? <ChevronDown className="size-3.5 text-muted-foreground" /> : <ChevronRight className="size-3.5 text-muted-foreground" />}
        </span>
      </button>

      {expanded && (
        <div className="border-t border-border">
          {!connected ? (
            <div className="px-4 py-4">
              <p className="text-xs text-muted-foreground">
                Waiting for the setup script to run in your AWS account. There is nothing to show until it finishes. Provisioning, and this log, start on their own right after.
              </p>
            </div>
          ) : !data && failed ? (
            <div className="px-4 py-4 space-y-2.5">
              <p className="text-xs text-muted-foreground">Couldn&apos;t reach the logs service. This is usually temporary.</p>
              <Button variant="outline" size="sm" onClick={load} disabled={loading} className="gap-1.5">
                <RefreshCw className={`size-3 ${loading ? 'animate-spin' : ''}`} /> Retry
              </Button>
            </div>
          ) : !data ? (
            <div className="px-4 py-4 space-y-2">
              <Skeleton className="h-3 w-3/4" />
              <Skeleton className="h-3 w-1/2" />
              <Skeleton className="h-3 w-2/3" />
            </div>
          ) : (
            <>
              <div className="flex items-center gap-3 px-4 py-2 text-xs text-muted-foreground font-mono">
                <span>{live ? 'Live, refreshes every 10 seconds' : `Last updated ${new Date(data.updated_at).toLocaleString()}`}</span>
                {data.withheld_lines > 0 && <span>{data.withheld_lines} lines withheld</span>}
                {data.truncated && <span>head clipped to storage cap</span>}
              </div>
              <pre className="max-h-96 overflow-auto px-4 pb-4 text-xs leading-relaxed font-mono text-foreground whitespace-pre-wrap break-words">
                {data.logs || (live ? 'Waiting for Terraform output. The first lines usually appear within a minute.' : 'No output recorded yet.')}
              </pre>
            </>
          )}
        </div>
      )}
    </div>
  );
}
