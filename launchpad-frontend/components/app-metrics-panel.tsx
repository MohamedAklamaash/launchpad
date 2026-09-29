'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { Activity, AlertTriangle, RefreshCw } from 'lucide-react';
import { applicationApi } from '@/lib/api/applications';
import { AppMetricsRange, AppMetricsResponse, MetricPoint, MetricSeriesName } from '@/types/application';
import { ComputeType } from '@/types/infrastructure';
import { PolicyRefreshDialog } from '@/components/policy-refresh-dialog';
import { Button } from '@/components/ui/button';

const RANGES: { value: AppMetricsRange; label: string }[] = [
  { value: '1h', label: '1h' },
  { value: '6h', label: '6h' },
  { value: '24h', label: '24h' },
  { value: '7d', label: '7d' },
];

const AUTO_REFRESH_MS = 30_000;
const CHART_HEIGHT = 190;

// Machine reason codes the metrics API can put in `unavailable` -> a friendly note.
// An unrecognized future code still renders (with the raw code appended) rather than
// silently showing nothing.
const UNAVAILABLE_REASONS: Record<string, string> = {
  eks_container_metrics_not_enabled: "Container CPU/memory metrics aren't enabled for EKS clusters yet.",
};

function unavailableMessage(code: string): string {
  return UNAVAILABLE_REASONS[code] ?? `Metrics unavailable (${code}).`;
}

interface MetricsError {
  kind: 'not_deployed' | 'policy' | 'unavailable' | 'not_found' | 'error';
  message: string;
  /** 409/404/422 are stable outcomes for the current app state — auto-refresh backs off
   *  until the user retries. 503/network hiccups are transient — keep polling through them. */
  pauseAutoRefresh: boolean;
}

function describeError(e: unknown): { error: MetricsError; deniedActions?: string[] } {
  const err = e as {
    response?: { status?: number; data?: { error?: string; code?: string; denied_actions?: string[] } };
  };
  const status = err.response?.status;
  const code = err.response?.data?.code;
  const serverMessage = err.response?.data?.error;

  if (status === 422 && code === 'policy_refresh_required') {
    return {
      error: {
        kind: 'policy',
        message: 'Launchpad’s IAM role needs the metrics permissions from a policy refresh.',
        pauseAutoRefresh: true,
      },
      deniedActions: err.response?.data?.denied_actions,
    };
  }
  if (status === 409) {
    return { error: { kind: 'not_deployed', message: 'Metrics appear after the first deploy.', pauseAutoRefresh: true } };
  }
  if (status === 404) {
    return { error: { kind: 'not_found', message: 'Application not found.', pauseAutoRefresh: true } };
  }
  if (status === 503) {
    return { error: { kind: 'unavailable', message: serverMessage || 'Metrics are temporarily unavailable.', pauseAutoRefresh: false } };
  }
  return { error: { kind: 'error', message: serverMessage || 'Failed to load metrics.', pauseAutoRefresh: false } };
}

// ── chart plumbing ──────────────────────────────────────────────────────────

interface LineSpec {
  key: string;
  name: string;
  color: string;
  points: MetricPoint[];
}

function toChartRows(lines: LineSpec[]): Record<string, number>[] {
  const rows = new Map<number, Record<string, number>>();
  for (const { key, points } of lines) {
    for (const p of points) {
      const t = Date.parse(p.t);
      if (Number.isNaN(t)) continue;
      const row = rows.get(t) ?? { t };
      row[key] = p.v;
      rows.set(t, row);
    }
  }
  return Array.from(rows.values()).sort((a, b) => a.t - b.t);
}

function timeTick(range: AppMetricsRange) {
  return (t: number) => {
    const d = new Date(t);
    return range === '7d'
      ? d.toLocaleDateString([], { month: 'short', day: 'numeric' })
      : d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  };
}

function ChartTooltip({ active, payload, label, unit }: {
  active?: boolean;
  payload?: { dataKey: string; name: string; value: number; color: string }[];
  label?: number;
  unit: string;
}) {
  if (!active || !payload?.length || label === undefined) return null;
  return (
    <div className="rounded-lg border border-hairline bg-popover px-2.5 py-2 shadow-lg">
      <p className="text-[10px] text-muted-foreground mb-1">{new Date(label).toLocaleString()}</p>
      {payload.map((p) => (
        <p key={p.dataKey} className="flex items-center gap-1.5 text-[11px]">
          <span className="w-2 h-2 rounded-full shrink-0" style={{ background: p.color }} />
          <span className="text-muted-foreground">{p.name}</span>
          <span className="font-mono text-foreground ml-auto">
            {typeof p.value === 'number' ? (unit === '%' ? p.value.toFixed(1) : Math.round(p.value)) : p.value}
            {unit}
          </span>
        </p>
      ))}
    </div>
  );
}

function MetricChart({ title, unit, lines, range, yDomain, unavailableReason }: {
  title: string;
  unit: string;
  lines: LineSpec[];
  range: AppMetricsRange;
  yDomain?: [number, number];
  unavailableReason?: string;
}) {
  const data = useMemo(() => toChartRows(lines), [lines]);
  const hasData = data.length > 0;
  const showLegend = lines.length > 1;

  return (
    <div className="rounded-lg border border-hairline bg-surface-1 p-3">
      <p className="text-xs font-medium text-foreground mb-2">{title}</p>
      {unavailableReason ? (
        <div style={{ height: CHART_HEIGHT }} className="flex items-center justify-center px-4 text-center">
          <p className="text-[11px] text-muted-foreground">{unavailableReason}</p>
        </div>
      ) : !hasData ? (
        <div style={{ height: CHART_HEIGHT }} className="flex items-center justify-center">
          <p className="text-[11px] text-muted-foreground">No traffic in this window</p>
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={CHART_HEIGHT}>
          <LineChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: -20 }}>
            <CartesianGrid stroke="var(--hairline)" vertical={false} />
            <XAxis
              dataKey="t"
              type="number"
              scale="time"
              domain={['dataMin', 'dataMax']}
              tickFormatter={timeTick(range)}
              tick={{ fontSize: 10, fill: 'var(--muted-foreground)' }}
              axisLine={{ stroke: 'var(--hairline)' }}
              tickLine={false}
              minTickGap={32}
            />
            <YAxis
              domain={yDomain ?? ['auto', 'auto']}
              tick={{ fontSize: 10, fill: 'var(--muted-foreground)' }}
              axisLine={false}
              tickLine={false}
              width={40}
            />
            <Tooltip content={<ChartTooltip unit={unit} />} />
            {showLegend && (
              <Legend wrapperStyle={{ fontSize: 11 }} iconType="plainline" iconSize={10} />
            )}
            {lines.map((l) => (
              <Line
                key={l.key}
                type="monotone"
                dataKey={l.key}
                name={l.name}
                stroke={l.color}
                strokeWidth={2}
                dot={false}
                isAnimationActive={false}
                connectNulls
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}

// ── stat cards ───────────────────────────────────────────────────────────────

function lastValue(points: MetricPoint[] | undefined): number | null {
  if (!points || points.length === 0) return null;
  return points[points.length - 1].v;
}

function StatCard({ label, value, unavailable }: { label: string; value: string; unavailable?: boolean }) {
  return (
    <div className="rounded-lg border border-hairline bg-surface-1 px-3 py-2.5">
      <p className="text-[10px] uppercase tracking-[0.1em] text-muted-foreground/70 mb-1">{label}</p>
      <p className={`text-lg font-display font-semibold ${unavailable ? 'text-muted-foreground/50' : 'text-foreground'}`}>{value}</p>
    </div>
  );
}

interface Props {
  appId: string;
  infraId: string;
  computeType?: ComputeType;
}

export function AppMetricsPanel({ appId, infraId }: Props) {
  const [range, setRange] = useState<AppMetricsRange>('1h');
  const [data, setData] = useState<AppMetricsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<MetricsError | null>(null);
  const [refreshDialog, setRefreshDialog] = useState<{ open: boolean; deniedActions?: string[] }>({ open: false });
  const pauseAutoRefreshRef = useRef(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await applicationApi.metrics(appId, range);
      setData(resp);
      setError(null);
      pauseAutoRefreshRef.current = false;
    } catch (e: unknown) {
      const { error: err, deniedActions } = describeError(e);
      setError(err);
      pauseAutoRefreshRef.current = err.pauseAutoRefresh;
      if (err.kind === 'policy') {
        setRefreshDialog({ open: true, deniedActions });
      }
    } finally {
      setLoading(false);
    }
  }, [appId, range]);

  useEffect(() => {
    pauseAutoRefreshRef.current = false;
    load();
  }, [load]);

  // Auto-refresh every 30s; pauses while the tab is hidden or after a stable error
  // (not_deployed/not_found/policy_refresh_required) until the user retries manually.
  useEffect(() => {
    const id = setInterval(() => {
      if (document.visibilityState === 'hidden') return;
      if (pauseAutoRefreshRef.current) return;
      load();
    }, AUTO_REFRESH_MS);
    return () => clearInterval(id);
  }, [load]);

  const cpu = data?.series.cpu_percent;
  const mem = data?.series.memory_percent;
  const req = data?.series.request_count;
  const p50 = data?.series.latency_p50_ms;
  const p95 = data?.series.latency_p95_ms;
  const err4xx = data?.series.http_4xx;
  const err5xx = data?.series.http_5xx;
  const healthy = data?.series.healthy_targets;

  const currentCpu = lastValue(cpu);
  const currentMem = lastValue(mem);
  const currentP95 = lastValue(p95);
  const reqPerMin = useMemo(() => {
    const v = lastValue(req);
    if (v === null || !data) return null;
    return (v * 60) / data.period_seconds;
  }, [req, data]);
  const errorRate5xx = useMemo(() => {
    const total = lastValue(req);
    const errs = lastValue(err5xx);
    if (errs === null) return null;
    if (!total) return errs > 0 ? 100 : 0;
    return (errs / total) * 100;
  }, [req, err5xx]);

  const unavailable: Partial<Record<MetricSeriesName, string>> = data?.unavailable ?? {};

  return (
    <div className="rounded-xl panel p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <Activity className="w-3.5 h-3.5 text-muted-foreground" />
          <p className="eyebrow">Metrics</p>
          {data?.cached && <span className="font-mono text-[10px] text-muted-foreground/60">cached</span>}
        </div>
        <div className="flex items-center gap-2">
          <div className="flex items-center rounded-lg border border-hairline p-0.5 gap-0.5">
            {RANGES.map((r) => (
              <button
                key={r.value}
                type="button"
                onClick={() => setRange(r.value)}
                className={`px-2.5 h-6 rounded-md text-[11px] font-mono uppercase tracking-wide transition-colors ${
                  range === r.value ? 'bg-surface-3 text-foreground' : 'text-muted-foreground hover:text-foreground'
                }`}
              >
                {r.label}
              </button>
            ))}
          </div>
          <Button
            size="sm"
            variant="outline"
            className="h-7 gap-1.5"
            disabled={loading}
            onClick={() => {
              pauseAutoRefreshRef.current = false;
              load();
            }}
          >
            <RefreshCw className={`w-3 h-3 ${loading ? 'animate-spin' : ''}`} />
          </Button>
        </div>
      </div>

      {loading && !data && (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-2 mb-3">
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="h-16 rounded-lg panel-inset animate-pulse" />
          ))}
        </div>
      )}

      {error && error.kind !== 'policy' && (
        <div
          className={`flex items-start gap-2.5 rounded-lg px-3 py-3 mb-3 ${
            error.kind === 'not_deployed'
              ? 'border border-hairline bg-surface-1'
              : 'border border-destructive/30 bg-destructive/10'
          }`}
        >
          {error.kind !== 'not_deployed' && <AlertTriangle className="w-3.5 h-3.5 text-destructive shrink-0 mt-0.5" />}
          <div className="flex-1">
            <p className={`text-xs ${error.kind === 'not_deployed' ? 'text-muted-foreground' : 'text-destructive/80'}`}>
              {error.message}
            </p>
          </div>
          {error.kind !== 'not_deployed' && (
            <button
              onClick={() => {
                pauseAutoRefreshRef.current = false;
                load();
              }}
              className="text-[10px] font-mono uppercase tracking-widest text-muted-foreground/70 hover:text-brand transition-colors shrink-0"
            >
              Retry
            </button>
          )}
        </div>
      )}

      {data && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-5 gap-2 mb-3">
            <StatCard label="CPU" value={unavailable.cpu_percent ? '—' : currentCpu !== null ? `${currentCpu.toFixed(1)}%` : '—'} unavailable={!!unavailable.cpu_percent} />
            <StatCard label="Memory" value={unavailable.memory_percent ? '—' : currentMem !== null ? `${currentMem.toFixed(1)}%` : '—'} unavailable={!!unavailable.memory_percent} />
            <StatCard label="Req / min" value={reqPerMin !== null ? reqPerMin.toFixed(1) : '—'} />
            <StatCard label="p95 latency" value={currentP95 !== null ? `${Math.round(currentP95)}ms` : '—'} />
            <StatCard label="5xx rate" value={errorRate5xx !== null ? `${errorRate5xx.toFixed(1)}%` : '—'} />
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
            <MetricChart
              title="CPU & Memory"
              unit="%"
              range={range}
              yDomain={[0, 100]}
              unavailableReason={
                unavailable.cpu_percent || unavailable.memory_percent
                  ? unavailableMessage(unavailable.cpu_percent ?? unavailable.memory_percent!)
                  : undefined
              }
              lines={[
                { key: 'cpu', name: 'CPU %', color: 'var(--chart-1)', points: cpu ?? [] },
                { key: 'mem', name: 'Memory %', color: 'var(--chart-2)', points: mem ?? [] },
              ]}
            />
            <MetricChart
              title="Requests"
              unit=" req"
              range={range}
              unavailableReason={unavailable.request_count ? unavailableMessage(unavailable.request_count) : undefined}
              lines={[{ key: 'req', name: 'Requests', color: 'var(--chart-1)', points: req ?? [] }]}
            />
            <MetricChart
              title="Latency"
              unit="ms"
              range={range}
              unavailableReason={
                unavailable.latency_p50_ms || unavailable.latency_p95_ms
                  ? unavailableMessage(unavailable.latency_p50_ms ?? unavailable.latency_p95_ms!)
                  : undefined
              }
              lines={[
                { key: 'p50', name: 'p50', color: 'var(--chart-2)', points: p50 ?? [] },
                { key: 'p95', name: 'p95', color: 'var(--chart-1)', points: p95 ?? [] },
              ]}
            />
            <MetricChart
              title="4xx / 5xx"
              unit=""
              range={range}
              unavailableReason={
                unavailable.http_4xx || unavailable.http_5xx
                  ? unavailableMessage(unavailable.http_4xx ?? unavailable.http_5xx!)
                  : undefined
              }
              lines={[
                { key: 'e4', name: '4xx', color: 'var(--warning)', points: err4xx ?? [] },
                { key: 'e5', name: '5xx', color: 'var(--destructive)', points: err5xx ?? [] },
              ]}
            />
            <MetricChart
              title="Healthy targets"
              unit=""
              range={range}
              unavailableReason={unavailable.healthy_targets ? unavailableMessage(unavailable.healthy_targets) : undefined}
              lines={[{ key: 'healthy', name: 'Healthy targets', color: 'var(--success)', points: healthy ?? [] }]}
            />
          </div>
        </>
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
