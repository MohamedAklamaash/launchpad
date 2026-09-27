'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { ScrollText, RefreshCw, ChevronDown, Lock } from 'lucide-react';
import { applicationApi } from '@/lib/api/applications';
import { RuntimeLogContainer, RuntimeLogEvent } from '@/types/application';

const WINDOW_OPTIONS = [15, 30, 60];
const AUTO_REFRESH_INTERVAL_MS = 10_000;
const AUTO_REFRESH_MAX_DURATION_MS = 10 * 60 * 1000;

interface Props {
  appId: string;
}

interface LogError {
  status: number | null;
  message: string;
}

function describeError(e: unknown): LogError {
  const err = e as { response?: { status?: number; data?: { error?: string }; headers?: Record<string, string> } };
  const status = err.response?.status ?? null;
  const serverMessage = err.response?.data?.error;

  switch (status) {
    case 400:
      return { status, message: serverMessage || 'Invalid request parameters.' };
    case 403:
      return { status, message: 'Only the infrastructure owner can view runtime logs.' };
    case 404:
      return { status, message: 'Application not found.' };
    case 409:
      return { status, message: 'Application is not deployed yet.' };
    case 429: {
      const retryAfter = err.response?.headers?.['retry-after'];
      const seconds = retryAfter ? parseInt(retryAfter, 10) : NaN;
      return {
        status,
        message: Number.isFinite(seconds) ? `Too many requests, try again in ${seconds}s.` : 'Too many requests, try again shortly.',
      };
    }
    case 502:
    case 503:
      return { status, message: 'Log service is temporarily unavailable. Try again shortly.' };
    default:
      return { status, message: serverMessage || 'Failed to load logs.' };
  }
}

export function RuntimeLogsPanel({ appId }: Props) {
  const [container, setContainer] = useState<RuntimeLogContainer>('app');
  const [minutes, setMinutes] = useState(15);
  const [previous, setPrevious] = useState(false);
  const [events, setEvents] = useState<RuntimeLogEvent[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [truncated, setTruncated] = useState(false);
  const [loading, setLoading] = useState(false);
  const [hasLoaded, setHasLoaded] = useState(false);
  const [error, setError] = useState<LogError | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(false);
  const autoRefreshStartRef = useRef<number | null>(null);

  const fetchLogs = useCallback(async (cursor?: string) => {
    setLoading(true);
    setError(null);
    try {
      const data = await applicationApi.logs(appId, cursor
        ? { container, cursor, previous: previous || undefined }
        : { container, minutes, previous: previous || undefined });
      setEvents((prev) => (cursor ? [...prev, ...data.events] : data.events));
      setNextCursor(data.next_cursor);
      setTruncated(data.truncated);
      setHasLoaded(true);
    } catch (e: unknown) {
      setError(describeError(e));
    } finally {
      setLoading(false);
    }
  }, [appId, container, minutes, previous]);

  const resetQuery = () => {
    setEvents([]);
    setNextCursor(null);
    setTruncated(false);
    setError(null);
    setHasLoaded(false);
  };

  const handleContainerChange = (value: RuntimeLogContainer) => {
    if (value === container) return;
    setContainer(value);
    resetQuery();
  };

  const handleMinutesChange = (value: string | null) => {
    if (value === null) return;
    const next = Number(value);
    if (next === minutes) return;
    setMinutes(next);
    resetQuery();
  };

  const handlePreviousChange = (checked: boolean) => {
    setPrevious(checked);
    resetQuery();
  };

  // Auto-refresh: user-opt-in only, capped at once per 10s, stops when the tab
  // is hidden or after 10 minutes. Never faster and never unattended beyond that.
  useEffect(() => {
    if (!autoRefresh) {
      autoRefreshStartRef.current = null;
      return;
    }
    autoRefreshStartRef.current = Date.now();
    const id = setInterval(() => {
      const start = autoRefreshStartRef.current;
      if (start && Date.now() - start >= AUTO_REFRESH_MAX_DURATION_MS) {
        setAutoRefresh(false);
        return;
      }
      fetchLogs();
    }, AUTO_REFRESH_INTERVAL_MS);
    return () => clearInterval(id);
  }, [autoRefresh, fetchLogs]);

  useEffect(() => {
    if (!autoRefresh) return;
    const onVisibility = () => {
      if (document.visibilityState === 'hidden') setAutoRefresh(false);
    };
    document.addEventListener('visibilitychange', onVisibility);
    return () => document.removeEventListener('visibilitychange', onVisibility);
  }, [autoRefresh]);

  // Log content is unredacted customer output — keep it in memory only, never
  // persisted, and drop it as soon as the panel goes away.
  useEffect(() => () => setEvents([]), []);

  const isForbidden = error?.status === 403;

  return (
    <div className="rounded-xl panel p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <ScrollText className="w-3.5 h-3.5 text-muted-foreground" />
          <p className="eyebrow">Runtime Logs</p>
        </div>
        {hasLoaded && !isForbidden && (
          <span className="font-mono text-[10px] text-muted-foreground/60">{events.length} lines</span>
        )}
      </div>

      {isForbidden ? (
        <div className="flex items-center gap-2.5 rounded-lg border border-hairline bg-surface-1 px-3 py-3">
          <Lock className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
          <p className="text-xs text-muted-foreground">{error.message}</p>
        </div>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-2 mb-3">
            <div className="flex items-center rounded-lg border border-hairline p-0.5 gap-0.5">
              {(['app', 'proxy'] as const).map((c) => (
                <button
                  key={c}
                  type="button"
                  onClick={() => handleContainerChange(c)}
                  className={`px-2.5 h-6 rounded-md text-[11px] font-mono uppercase tracking-wide transition-colors ${
                    container === c ? 'bg-surface-3 text-foreground' : 'text-muted-foreground hover:text-foreground'
                  }`}
                >
                  {c}
                </button>
              ))}
            </div>

            <Select value={String(minutes)} onValueChange={handleMinutesChange}>
              <SelectTrigger size="sm" className="h-7 font-mono text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent className="bg-popover border-hairline">
                {WINDOW_OPTIONS.map((m) => (
                  <SelectItem key={m} value={String(m)} className="font-mono text-xs">Last {m}m</SelectItem>
                ))}
              </SelectContent>
            </Select>

            <label className="flex items-center gap-1.5 text-[11px] text-muted-foreground/80 select-none cursor-pointer">
              <input
                type="checkbox"
                checked={previous}
                onChange={(e) => handlePreviousChange(e.target.checked)}
                className="accent-brand"
              />
              Previous instance (EKS)
            </label>

            <div className="ml-auto flex items-center gap-2">
              <label className="flex items-center gap-1.5 text-[11px] text-muted-foreground/80 select-none cursor-pointer">
                <input
                  type="checkbox"
                  checked={autoRefresh}
                  onChange={(e) => setAutoRefresh(e.target.checked)}
                  className="accent-brand"
                />
                Auto-refresh (10s)
              </label>
              <Button
                size="sm"
                variant="outline"
                className="gap-1.5"
                disabled={loading}
                onClick={() => {
                  resetQuery();
                  fetchLogs();
                }}
              >
                <RefreshCw className={`w-3 h-3 ${loading ? 'animate-spin' : ''}`} />
                {hasLoaded ? 'Refresh' : 'Load logs'}
              </Button>
            </div>
          </div>

          {error && (
            <div className="rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 mb-3">
              <p className="text-xs text-destructive/80">{error.message}</p>
            </div>
          )}

          {hasLoaded && !error && events.length === 0 && (
            <p className="text-xs text-muted-foreground">No log events in this window.</p>
          )}

          {events.length > 0 && (
            <>
              <div className="bg-surface-1 border border-hairline rounded-lg p-3 max-h-96 overflow-y-auto font-mono text-[11px] leading-relaxed">
                {events.map((event, idx) => (
                  <div key={`${idx}-${event.timestamp}`} className="whitespace-pre-wrap break-all text-foreground/90">
                    <span className="text-muted-foreground/60">{event.timestamp}</span>{' '}{event.message}
                  </div>
                ))}
              </div>
              {truncated && (
                <p className="text-[10px] text-warning mt-1.5">Output truncated — narrow the time window for full coverage.</p>
              )}
              {nextCursor && (
                <button
                  type="button"
                  onClick={() => fetchLogs(nextCursor)}
                  disabled={loading}
                  className="mt-2 flex items-center gap-1 text-[10px] font-mono uppercase tracking-widest text-muted-foreground/70 hover:text-brand transition-colors disabled:opacity-40"
                >
                  <ChevronDown className="w-3 h-3" /> Load more
                </button>
              )}
            </>
          )}
        </>
      )}
    </div>
  );
}
