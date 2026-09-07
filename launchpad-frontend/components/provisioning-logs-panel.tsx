'use client';

import { useCallback, useEffect, useState } from 'react';
import { ChevronDown, ChevronRight, ScrollText } from 'lucide-react';
import { infrastructureApi } from '@/lib/api/infrastructures';
import { InfrastructureStatus, ProvisioningLogs } from '@/types/infrastructure';

const IN_FLIGHT: InfrastructureStatus[] = ['PROVISIONING', 'UPDATING', 'DESTROYING'];
const POLL_MS = 10_000;

interface Props {
  infraId: string;
  status: InfrastructureStatus;
}

export function ProvisioningLogsPanel({ infraId, status }: Props) {
  const [expanded, setExpanded] = useState(false);
  const [data, setData] = useState<ProvisioningLogs | null>(null);
  const [failed, setFailed] = useState(false);

  const load = useCallback(async () => {
    try {
      setData(await infrastructureApi.getLogs(infraId));
      setFailed(false);
    } catch {
      setFailed(true);
    }
  }, [infraId]);

  // The page's status can flip to terminal up to 5s before the last log tail is written;
  // keep polling until the logs endpoint itself reports the run finished.
  const live = IN_FLIGHT.includes(status) || (!failed && data !== null && IN_FLIGHT.includes(data.status));

  useEffect(() => {
    if (!expanded || !live) return;
    const interval = setInterval(load, POLL_MS);
    return () => clearInterval(interval);
  }, [expanded, live, load]);

  const toggle = () => {
    const next = !expanded;
    setExpanded(next);
    if (next) load();
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
          {!data && failed && <p className="px-4 py-3 text-xs text-destructive">Failed to load provisioning logs.</p>}
          {!data && !failed && <div className="h-16 animate-pulse" />}
          {data && (
            <>
              <div className="flex items-center gap-3 px-4 py-2 text-[11px] text-muted-foreground font-mono">
                <span>{live ? 'Live — refreshes every 10s' : `Last updated ${new Date(data.updated_at).toLocaleString()}`}</span>
                {data.withheld_lines > 0 && <span>{data.withheld_lines} lines withheld</span>}
                {data.truncated && <span>head clipped to storage cap</span>}
              </div>
              <pre className="max-h-96 overflow-auto px-4 pb-4 text-[11px] leading-relaxed font-mono text-foreground whitespace-pre-wrap break-words">
                {data.logs || 'No output recorded yet.'}
              </pre>
            </>
          )}
        </div>
      )}
    </div>
  );
}
