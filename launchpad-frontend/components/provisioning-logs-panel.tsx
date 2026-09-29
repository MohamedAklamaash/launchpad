'use client';

import { useCallback, useEffect, useState } from 'react';
import { ChevronDown, ChevronRight, RefreshCw, ScrollText } from 'lucide-react';
import { Button } from '@/components/ui/button';
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
    <div className="rounded-xl panel">
      <button
        type="button"
        onClick={toggle}
        aria-expanded={expanded}
        className="w-full flex items-center justify-between px-4 py-3 text-left"
      >
        <span className="flex items-center gap-2 text-sm font-display font-semibold text-foreground">
          <ScrollText className="w-3.5 h-3.5 text-muted-foreground" /> Provisioning Logs
        </span>
        <span className="flex items-center gap-2">
          {data?.truncated && (
            <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-warning border border-warning/30 bg-warning/10 rounded px-1.5 py-0.5">
              truncated
            </span>
          )}
          {expanded ? <ChevronDown className="w-3.5 h-3.5 text-muted-foreground" /> : <ChevronRight className="w-3.5 h-3.5 text-muted-foreground" />}
        </span>
      </button>

      {expanded && (
        <div className="border-t border-hairline">
          {!connected ? (
            <div className="px-4 py-4">
              <p className="text-xs text-muted-foreground">
                Waiting for you to run the setup script in your AWS account. There is nothing to
                show here until that finishes — provisioning (and this log) starts automatically
                right after.
              </p>
            </div>
          ) : !data && failed ? (
            <div className="px-4 py-4 space-y-2.5">
              <p className="text-xs text-muted-foreground">Couldn&apos;t reach the logs service. This is usually temporary.</p>
              <Button variant="outline" size="sm" onClick={load} disabled={loading} className="gap-1.5">
                <RefreshCw className={`w-3 h-3 ${loading ? 'animate-spin' : ''}`} /> Retry
              </Button>
            </div>
          ) : !data ? (
            <div className="px-4 py-4 space-y-2">
              <div className="h-3 w-3/4 rounded panel-inset animate-pulse" />
              <div className="h-3 w-1/2 rounded panel-inset animate-pulse" />
              <div className="h-3 w-2/3 rounded panel-inset animate-pulse" />
            </div>
          ) : (
            <>
              <div className="flex items-center gap-3 px-4 py-2 text-[11px] text-muted-foreground font-mono">
                <span>{live ? 'Live — refreshes every 10s' : `Last updated ${new Date(data.updated_at).toLocaleString()}`}</span>
                {data.withheld_lines > 0 && <span>{data.withheld_lines} lines withheld</span>}
                {data.truncated && <span>head clipped to storage cap</span>}
              </div>
              <pre className="max-h-96 overflow-auto px-4 pb-4 text-[11px] leading-relaxed font-mono text-foreground whitespace-pre-wrap break-words">
                {data.logs || (live ? 'Waiting for Terraform output — first lines usually appear within a minute.' : 'No output recorded yet.')}
              </pre>
            </>
          )}
        </div>
      )}
    </div>
  );
}
